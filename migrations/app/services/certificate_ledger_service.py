"""자격증명 발급대장 연결 서비스.

실제 업무 순서
1) 자격증명 발급 신청서류 접수
2) 예정자 입력
3) 기존 자격증명발급번호 발급/확인
4) 자격증명 생성·출력
5) 이후 시청 인가 공문이 오면 예정자 등록/인가일자 반영

주의:
- 기존 자격증명발급번호 이력은 번호 원장이고, 발급대장은 새 업무 건만 관리한다.
- 과거 예정자/등록회원 전체를 발급대장으로 역수입하지 않는다.
"""

from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import models


WAITING = "인가대기"
APPROVED = "인가완료"
ISSUED = "발급완료"

_SCHEMA_READY = set()


def _cleanup_buggy_backfill(db: Session) -> int:
    """이전 버그 버전이 후보 검색만으로 생성한 과거 등록완료 행만 제거한다.

    삭제 대상은 생성 이력 메모가 정확히
    '기존 등록완료 예정자와 연결되어 자동 생성'이고 실제 발급완료 기록이 없는 행뿐이다.
    기존 CertificateNumberLog(자격증명발급번호 발급 이력)는 절대 건드리지 않는다.
    """
    bad_ids = [
        row_id for (row_id,) in (
            db.query(ledger_models.CertificateIssuanceLedger.id)
            .join(
                ledger_models.CertificateIssuanceHistory,
                ledger_models.CertificateIssuanceHistory.ledger_id
                == ledger_models.CertificateIssuanceLedger.id,
            )
            .filter(
                ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
                ledger_models.CertificateIssuanceLedger.issued_at.is_(None),
                ledger_models.CertificateIssuanceHistory.event_type == "생성",
                ledger_models.CertificateIssuanceHistory.memo
                == "기존 등록완료 예정자와 연결되어 자동 생성",
            )
            .distinct()
            .all()
        )
    ]
    if not bad_ids:
        return 0

    db.query(ledger_models.CertificateIssuanceHistory).filter(
        ledger_models.CertificateIssuanceHistory.ledger_id.in_(bad_ids)
    ).delete(synchronize_session=False)
    db.query(ledger_models.CertificateIssuanceLedger).filter(
        ledger_models.CertificateIssuanceLedger.id.in_(bad_ids)
    ).delete(synchronize_session=False)
    db.commit()
    return len(bad_ids)


def ensure_ledger_schema(db: Session) -> None:
    """발급대장 전용 테이블만 안전하게 보장한다."""
    bind = db.get_bind()
    key = str(getattr(bind, "url", "default"))
    if key in _SCHEMA_READY:
        return

    ledger_models.CertificateIssuanceLedger.__table__.create(bind=bind, checkfirst=True)
    ledger_models.CertificateIssuanceHistory.__table__.create(bind=bind, checkfirst=True)

    # 예정자 저장 직후에는 아직 발급번호가 없을 수 있으므로 NULL 허용.
    if bind is not None and bind.dialect.name == "postgresql":
        columns = inspect(bind).get_columns("certificate_issuance_ledger")
        doc_col = next((col for col in columns if col.get("name") == "document_number"), None)
        if doc_col and not doc_col.get("nullable", True):
            db.execute(text(
                "ALTER TABLE certificate_issuance_ledger "
                "ALTER COLUMN document_number DROP NOT NULL"
            ))
            db.commit()

    _cleanup_buggy_backfill(db)
    _SCHEMA_READY.add(key)


def operator_name(user) -> str:
    if user is None:
        return "시스템"
    full_name = (getattr(user, "full_name", None) or "").strip()
    username = (getattr(user, "username", None) or "").strip()
    return full_name or username or str(user) or "시스템"


def add_history(
    db: Session,
    ledger_id: int,
    event_type: str,
    from_status: str | None,
    to_status: str,
    operator: str,
    memo: str = "",
) -> None:
    db.add(
        ledger_models.CertificateIssuanceHistory(
            ledger_id=ledger_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            operator=operator,
            memo=memo,
        )
    )


def ensure_candidate_ledger(db: Session, candidate: models.Candidate, user=None):
    """실제 예정자 업무 건과 연결된 발급대장 1건을 보장한다.

    중요: 등록완료된 과거 후보를 조회했다는 이유만으로 새 대장을 만들지 않는다.
    새 행 생성은 예정자 저장 단계에서만 일어나는 것을 전제로 한다.
    """
    if candidate is None or getattr(candidate, "deleted_at", None) is not None:
        return None

    ensure_ledger_schema(db)
    actor = operator_name(user)
    existing_number = (getattr(candidate, "certificate_number", None) or "").strip() or None

    entry = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id == candidate.id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )

    if entry:
        changed = False
        for field, value in (
            ("region", candidate.region or ""),
            ("vehicle_number", candidate.vehicle_number or ""),
            ("name", candidate.name or ""),
        ):
            if getattr(entry, field) != value:
                setattr(entry, field, value)
                changed = True

        # 번호는 기존 예정자/발급번호 원장이 원본이다. 발급완료 전까지만 따라간다.
        if existing_number and entry.status != ISSUED and entry.document_number != existing_number:
            duplicate = (
                db.query(ledger_models.CertificateIssuanceLedger.id)
                .filter(
                    ledger_models.CertificateIssuanceLedger.document_number == existing_number,
                    ledger_models.CertificateIssuanceLedger.id != entry.id,
                    ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
                )
                .first()
            )
            if not duplicate:
                entry.document_number = existing_number
                changed = True

        if candidate.member_id and entry.member_id != candidate.member_id:
            entry.member_id = candidate.member_id
            changed = True

        if changed:
            entry.latest_operator = actor
            db.commit()
            db.refresh(entry)
        return entry

    # 과거 등록완료 자료는 신규 발급대장으로 역수입하지 않는다.
    if bool(getattr(candidate, "is_registered", False)):
        return None

    entry = ledger_models.CertificateIssuanceLedger(
        candidate_id=candidate.id,
        member_id=None,
        region=candidate.region or "",
        vehicle_number=candidate.vehicle_number or "",
        name=candidate.name or "",
        qualification_number="",
        document_number=existing_number,
        approval_date="",
        certificate_issue_date="",
        status=WAITING,
        latest_operator=actor,
        created_by=actor,
    )
    db.add(entry)
    db.flush()
    add_history(
        db,
        entry.id,
        "생성",
        None,
        WAITING,
        actor,
        "예정자 신규 입력과 연결되어 자격증명 발급대장 생성",
    )
    db.commit()
    db.refresh(entry)
    return entry


def _approve_entry(
    db: Session,
    entry: ledger_models.CertificateIssuanceLedger,
    member_id: int | None,
    approval_date: str | None,
    operator: str,
    memo: str,
) -> bool:
    changed = False
    had_approval = bool((entry.approval_date or "").strip())

    if member_id and entry.member_id != member_id:
        entry.member_id = member_id
        changed = True
    if approval_date and entry.approval_date != approval_date:
        entry.approval_date = approval_date
        changed = True

    if entry.status == WAITING:
        previous = entry.status
        entry.status = APPROVED
        entry.approved_at = datetime.now(timezone.utc)
        entry.latest_operator = operator
        add_history(db, entry.id, "인가완료", previous, APPROVED, operator, memo)
        changed = True
    elif entry.status == ISSUED and changed and not had_approval:
        # 자격증명이 먼저 발급된 정상 순서. 발급완료 상태는 그대로 둔다.
        entry.approved_at = datetime.now(timezone.utc)
        entry.latest_operator = operator
        add_history(
            db,
            entry.id,
            "인가완료",
            ISSUED,
            ISSUED,
            operator,
            f"{memo} / 자격증명은 이미 발급완료되어 상태 유지",
        )
    elif changed:
        entry.latest_operator = operator

    return changed


def mark_candidate_approved(
    db: Session,
    candidate_id: int,
    member_id: int | None,
    approval_date: str | None,
    user,
) -> int:
    """기존에 발급대장 행이 있는 예정자가 실제 등록된 경우에만 인가정보 반영."""
    ensure_ledger_schema(db)
    actor = operator_name(user)

    entries = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id == candidate_id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    )
    if not entries:
        # 과거 회원을 신규 발급대장으로 자동 생성하지 않는다.
        return 0

    changed = 0
    for entry in entries:
        if _approve_entry(
            db,
            entry,
            member_id,
            approval_date,
            actor,
            "예정자 목록에서 등록 + 인가일자 입력 + 회원등록 성공 후 자동 반영",
        ):
            changed += 1
    if changed:
        db.commit()
    return changed


def reconcile_registered_candidates(db: Session) -> int:
    """이미 존재하는 발급대장 행만 회원등록 상태와 동기화한다.

    새 행을 만들지 않는다.
    """
    ensure_ledger_schema(db)
    entries = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id.isnot(None),
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    )
    if not entries:
        return 0

    ids = [entry.candidate_id for entry in entries]
    candidates = (
        db.query(models.Candidate)
        .filter(
            models.Candidate.id.in_(ids),
            models.Candidate.is_registered.is_(True),
            models.Candidate.deleted_at.is_(None),
        )
        .all()
    )
    by_id = {candidate.id: candidate for candidate in candidates}
    if not by_id:
        return 0

    member_ids = [candidate.member_id for candidate in candidates if candidate.member_id]
    members = (
        db.query(models.LicenseHolder)
        .filter(models.LicenseHolder.id.in_(member_ids))
        .all()
        if member_ids
        else []
    )
    by_member_id = {member.id: member for member in members}

    changed = 0
    for entry in entries:
        candidate = by_id.get(entry.candidate_id)
        if not candidate:
            continue
        member = by_member_id.get(candidate.member_id)
        approval_date = getattr(member, "approval_date", None) if member else None
        if _approve_entry(
            db,
            entry,
            candidate.member_id,
            approval_date,
            "시스템 자동연동",
            "기존 발급대장 건의 등록 상태 확인으로 인가정보 반영",
        ):
            changed += 1
    if changed:
        db.commit()
    return changed
