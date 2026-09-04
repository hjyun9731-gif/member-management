"""자격증명 발급대장 상태 연결 서비스."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import models


WAITING = "인가대기"
APPROVED = "인가완료"
ISSUED = "발급완료"


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


def _approve_entry(
    db: Session,
    entry: ledger_models.CertificateIssuanceLedger,
    member_id: int | None,
    approval_date: str | None,
    operator: str,
    memo: str,
) -> bool:
    if entry.status == ISSUED:
        return False

    changed = False
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
    return changed


def mark_candidate_approved(
    db: Session,
    candidate_id: int,
    member_id: int | None,
    approval_date: str | None,
    user,
) -> int:
    """예정자 등록 성공 직후 연결된 대장을 인가완료로 바꾼다.

    이 함수에서 오류가 나더라도 기존 예정자 등록 결과를 되돌리지 않도록 호출부가
    예외를 분리한다. 목록 조회 시 reconcile_registered_candidates가 한 번 더 보정한다.
    """
    actor = operator_name(user)
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
            "예정자 목록에서 신규 등록되어 자동 변경",
        ):
            changed += 1
    if changed:
        db.commit()
    return changed


def reconcile_registered_candidates(db: Session) -> int:
    """연결 훅 누락/일시 오류를 목록 조회 시 자동 보정하는 안전망."""
    waiting = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.status == WAITING,
            ledger_models.CertificateIssuanceLedger.candidate_id.isnot(None),
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    )
    if not waiting:
        return 0

    ids = [entry.candidate_id for entry in waiting]
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
    for entry in waiting:
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
            "등록 상태 자동 점검으로 인가완료 반영",
        ):
            changed += 1
    if changed:
        db.commit()
    return changed
