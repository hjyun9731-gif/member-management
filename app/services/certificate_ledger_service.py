"""자격증명 발급대장 상태 연결 서비스.

실제 업무 순서:
서류 접수 -> 예정자 입력(이때 기존 예정자 화면에서 자격증명발급번호 부여 가능)
-> 자격증명 생성/발급 -> 이후 시청 인가 공문이 오면 예정자 등록 및 인가일자 반영.

기존 회원/예정자 등록 로직과 기존 자격증명발급번호 원장은 건드리지 않고,
새 발급대장만 연결한다.
"""

from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import models


WAITING = "인가대기"
APPROVED = "인가완료"
ISSUED = "발급완료"


def ensure_ledger_schema(db: Session) -> None:
    """발급대장 전용 테이블만 없을 때 안전하게 생성한다.

    기존 DB 테이블은 수정/삭제하지 않는다. Railway 운영 DB에서 테이블 누락으로
    조회 API가 500이 되는 상황을 막기 위한 최소 안전장치다.
    """
    bind = db.get_bind()
    ledger_models.CertificateIssuanceLedger.__table__.create(bind=bind, checkfirst=True)
    ledger_models.CertificateIssuanceHistory.__table__.create(bind=bind, checkfirst=True)

    # 예전 구현은 document_number를 NOT NULL로 만들었던 버전이 있었다.
    # 실제 업무는 예정자 저장 뒤 발급번호를 부여할 수 있으므로 번호 없는 인가대기 행도 허용해야 한다.
    if bind is not None and bind.dialect.name == "postgresql":
        columns = inspect(bind).get_columns("certificate_issuance_ledger")
        doc_col = next((col for col in columns if col.get("name") == "document_number"), None)
        if doc_col and not doc_col.get("nullable", True):
            db.execute(text(
                "ALTER TABLE certificate_issuance_ledger "
                "ALTER COLUMN document_number DROP NOT NULL"
            ))
            db.commit()


def operator_name(user) -> str:
    if user is None:
        return "시스템"
    full_name = (getattr(user, "full_name", None) or "").strip()
    username = (getattr(user, "username", None) or "").strip()
    return full_name or username or str(user) or "시스템"


def _norm_vehicle(value: str) -> str:
    import re
    return re.sub(r"[\s-]+", "", str(value or "")).strip()


def _same_candidate_member(candidate, member) -> bool:
    if not candidate or not member:
        return False
    category = (getattr(member, "category", "") or "").strip()
    if category and category not in {"개인", "택배"}:
        return False
    if getattr(candidate, "member_id", None) == getattr(member, "id", None):
        return True
    if getattr(member, "candidate_id", None) == getattr(candidate, "id", None):
        return True
    cv, mv = _norm_vehicle(getattr(candidate, "vehicle_number", "")), _norm_vehicle(getattr(member, "vehicle_number", ""))
    cn, mn = (getattr(candidate, "name", "") or "").strip(), (getattr(member, "name", "") or "").strip()
    return bool(cv and mv and cv == mv and (not cn or not mn or cn == mn))


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


def _member_for_candidate(db: Session, candidate: models.Candidate):
    if not candidate.member_id:
        return None
    return (
        db.query(models.LicenseHolder)
        .filter(models.LicenseHolder.id == candidate.member_id)
        .first()
    )


def ensure_candidate_ledger(db: Session, candidate: models.Candidate, user=None):
    """예정자와 연결된 발급대장 1건을 보장한다.

    예정자 저장 시 바로 대장을 만든다. 예정자 화면에서 이미 부여한
    자격증명발급번호(candidate.certificate_number)가 있으면 그대로 대장에 연결한다.
    발급대장에서 새 번호를 채번하지 않는다.
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

        # 번호의 원본은 예정자/기존 발급번호 이력이다. 발급완료 전까지는 예정자 번호를 따라간다.
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

    member = _member_for_candidate(db, candidate) if candidate.is_registered else None
    initial_status = APPROVED if candidate.is_registered else WAITING
    now = datetime.now(timezone.utc)
    entry = ledger_models.CertificateIssuanceLedger(
        candidate_id=candidate.id,
        member_id=candidate.member_id,
        region=candidate.region or "",
        vehicle_number=candidate.vehicle_number or "",
        name=candidate.name or "",
        qualification_number="",
        document_number=existing_number,
        approval_date=(getattr(member, "approval_date", None) or "") if member else "",
        certificate_issue_date=(getattr(candidate, "certificate_issue_date", None) or ""),
        status=initial_status,
        latest_operator=actor,
        created_by=actor,
        approved_at=now if initial_status == APPROVED else None,
    )
    db.add(entry)
    db.flush()
    add_history(
        db,
        entry.id,
        "생성",
        None,
        initial_status,
        actor,
        "예정자 저장과 연결되어 자동 생성"
        if initial_status == WAITING
        else "기존 등록완료 예정자와 연결되어 자동 생성",
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
    """인가정보를 반영한다.

    자격증명이 먼저 발급되어 이미 '발급완료'인 경우에는 그 상태를 되돌리지 않고
    member_id/인가일자만 추가한다. 즉 실제 업무 순서(발급 후 인가)도 보존한다.
    """
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
        # 발급이 인가보다 먼저 끝난 정상 케이스. 발급완료 상태는 그대로 유지한다.
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
    """예정자 목록에서 등록이 실제 성공한 직후 인가정보를 연결한다."""
    ensure_ledger_schema(db)
    actor = operator_name(user)

    candidate = (
        db.query(models.Candidate)
        .filter(
            models.Candidate.id == candidate_id,
            models.Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if not candidate:
        return 0

    ensure_candidate_ledger(db, candidate, user)

    entries = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id == candidate_id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    )
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
    """예정자와 실제 개인/택배 회원을 대조해 인가상태를 보정한다.

    기존 is_registered/member_id 연결이 정상인 경우뿐 아니라, 과거 자료처럼 연결키가 빠져 있어도
    같은 성명+차량번호의 개인/택배 회원이 실제 존재하면 이미 인가허가 완료된 것으로 본다.
    회원/예정자 데이터 자체는 삭제하거나 덮어쓰지 않고 발급대장의 인가상태만 보정한다.
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
    candidates = (db.query(models.Candidate)
                  .filter(models.Candidate.id.in_(ids), models.Candidate.deleted_at.is_(None))
                  .all())
    by_id = {c.id: c for c in candidates}
    if not by_id:
        return 0

    member_ids = {c.member_id for c in candidates if c.member_id}
    candidate_ids = {c.id for c in candidates}
    names = {(c.name or "").strip() for c in candidates if (c.name or "").strip()}
    vehicles = {(c.vehicle_number or "").strip() for c in candidates if (c.vehicle_number or "").strip()}

    from sqlalchemy import or_
    clauses = []
    if member_ids:
        clauses.append(models.LicenseHolder.id.in_(member_ids))
    if candidate_ids:
        clauses.append(models.LicenseHolder.candidate_id.in_(candidate_ids))
    if names:
        clauses.append(models.LicenseHolder.name.in_(names))
    if vehicles:
        clauses.append(models.LicenseHolder.vehicle_number.in_(vehicles))
    members = (db.query(models.LicenseHolder)
               .filter(models.LicenseHolder.deleted_at.is_(None), or_(*clauses))
               .all()) if clauses else []
    by_member_id = {m.id: m for m in members}
    by_candidate_id = {m.candidate_id: m for m in members if getattr(m, "candidate_id", None)}
    by_identity = {}
    for m in members:
        key = ((m.name or "").strip(), _norm_vehicle(m.vehicle_number))
        if key[0] and key[1] and key not in by_identity:
            by_identity[key] = m

    changed = 0
    for entry in entries:
        candidate = by_id.get(entry.candidate_id)
        if not candidate:
            continue
        member = None
        if candidate.member_id:
            member = by_member_id.get(candidate.member_id)
        if not member:
            member = by_candidate_id.get(candidate.id)
        if not member:
            member = by_identity.get(((candidate.name or "").strip(), _norm_vehicle(candidate.vehicle_number)))
            if member and not _same_candidate_member(candidate, member):
                member = None
        if not member:
            continue
        # 실제 개인/택배 회원에 존재하면 approval_date가 비어 있어도 인가완료로 본다.
        approval_date = getattr(member, "approval_date", None) or entry.approval_date or None
        if _approve_entry(
            db, entry, member.id, approval_date, "시스템 자동연동",
            "예정자/신규회원과 실제 개인·택배 회원 대조로 인가완료 확인",
        ):
            changed += 1
    if changed:
        db.commit()
    return changed
