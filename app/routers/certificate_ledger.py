"""자격증명 발급대장 API.

실제 업무 순서에 맞춘 연결:
1) 자격증명 발급 신청서류 접수
2) 기존 예정자 입력 화면에서 예정자 입력 + 자격증명발급번호 부여
3) 그 예정자의 기존 발급번호로 자격증명 생성/출력
4) 이후 시청 인가 공문이 오면 예정자 목록에서 등록 + 인가일자 입력

중요: 이 라우터는 자격증명발급번호를 새로 채번하지 않는다.
번호 원본은 기존 candidates.certificate_number + certificate_number_logs이다.
"""

from datetime import date, datetime, timezone
from math import ceil
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import crud, models
from app.auth import get_current_user
from app.database import get_db
from app.services.certificate_ledger_service import (
    APPROVED,
    ISSUED,
    WAITING,
    add_history,
    ensure_candidate_ledger,
    ensure_ledger_schema,
    operator_name,
    reconcile_registered_candidates,
)


router = APIRouter(prefix="/api/certificate-ledger", tags=["자격증명 발급대장"])
VALID_STATUSES = {WAITING, APPROVED, ISSUED}


class CreateLedgerBody(BaseModel):
    candidate_id: int
    qualification_number: Optional[str] = ""
    document_number: Optional[str] = ""  # 하위호환용. 실제 원본은 예정자 certificate_number.


class IssueLedgerBody(BaseModel):
    issue_date: Optional[str] = ""
    qualification_number: Optional[str] = ""
    document_number: Optional[str] = ""


def _dt(value):
    return value.isoformat() if value else None


def _item(row):
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "member_id": row.member_id,
        "region": row.region or "",
        "vehicle_number": row.vehicle_number or "",
        "name": row.name or "",
        "qualification_number": row.qualification_number or "",
        "document_number": row.document_number or "",
        "approval_date": row.approval_date or "",
        "certificate_issue_date": row.certificate_issue_date or "",
        "status": row.status,
        "latest_operator": row.latest_operator or "",
        "created_by": row.created_by or "",
        "approved_at": _dt(row.approved_at),
        "issued_at": _dt(row.issued_at),
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _get_row(db: Session, ledger_id: int):
    ensure_ledger_schema(db)
    row = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.id == ledger_id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(404, "발급대장 기록을 찾을 수 없습니다.")
    return row


def _same_number_subject(log: models.CertificateNumberLog, row) -> bool:
    if not log or log.status != "used":
        return True
    if row.member_id and log.linked_table == "license_holders" and log.linked_id == row.member_id:
        return True
    if row.candidate_id and log.linked_table == "candidates" and log.linked_id == row.candidate_id:
        return True
    return bool(
        (log.target_name or "").strip() == (row.name or "").strip()
        and (log.vehicle_number or "").strip() == (row.vehicle_number or "").strip()
        and (row.name or "").strip()
        and (row.vehicle_number or "").strip()
    )


def _candidate_for_row(db: Session, row):
    if not row.candidate_id:
        return None
    return (
        db.query(models.Candidate)
        .filter(
            models.Candidate.id == row.candidate_id,
            models.Candidate.deleted_at.is_(None),
        )
        .first()
    )


def _sync_existing_number_from_candidate(db: Session, row, actor: str = ""):
    """예정자에 이미 부여된 발급번호를 발급대장에 연결한다.

    여기서는 절대 get_next_certificate_number()를 호출하지 않는다.
    번호가 없으면 예정자 입력 화면에서 먼저 '발급번호 부여'를 하도록 안내한다.
    """
    candidate = _candidate_for_row(db, row)
    if not candidate:
        raise HTTPException(400, "연결된 예정자 정보를 찾을 수 없습니다.")

    number = (candidate.certificate_number or "").strip()
    if not number:
        raise HTTPException(
            400,
            "자격증명발급번호가 없습니다. 예정자 입력 화면에서 '발급번호 부여'를 먼저 하세요.",
        )

    current = (row.document_number or "").strip()
    if current and current != number and row.status == ISSUED:
        raise HTTPException(
            400,
            f"이미 발급완료된 대장의 증명서 No.({current})와 예정자 발급번호({number})가 다릅니다. 임의 변경하지 마세요.",
        )

    # 같은 번호가 다른 발급대장에 연결된 경우 차단.
    duplicate = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.document_number == number,
            ledger_models.CertificateIssuanceLedger.id != row.id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )
    if duplicate:
        raise HTTPException(400, f"자격증명발급번호 {number}가 이미 다른 발급대장에 연결되어 있습니다.")

    if row.document_number != number:
        row.document_number = number
        row.latest_operator = actor or row.latest_operator

    # 기존 번호이력 원장을 그대로 확인한다. 누락된 과거 수기값만 기존 sync 함수를 사용해 복구한다.
    log = (
        db.query(models.CertificateNumberLog)
        .filter(models.CertificateNumberLog.certificate_number == number)
        .first()
    )
    if not log:
        crud.sync_certificate_number_usage(
            db,
            number,
            "candidates",
            candidate.id,
            candidate.name or "",
            candidate.vehicle_number or "",
        )
        log = (
            db.query(models.CertificateNumberLog)
            .filter(models.CertificateNumberLog.certificate_number == number)
            .first()
        )

    if not log:
        raise HTTPException(400, f"자격증명발급번호 {number}의 발급 이력을 확인할 수 없습니다.")
    if log.status == "cancelled":
        raise HTTPException(400, f"자격증명발급번호 {number}는 취소된 번호입니다.")
    if log.status == "used" and not _same_number_subject(log, row):
        raise HTTPException(400, f"자격증명발급번호 {number}는 다른 대상자에게 이미 사용 중입니다.")

    # 예정자 레코드에 번호가 실제 저장되어 있으므로 기존 원장 기준으로는 사용중이 맞다.
    # issued 상태로 남아 있다면 기존 사용연결 규칙과 동일하게 현재 예정자에 연결한다.
    if log.status == "issued":
        log.status = "used"
        log.linked_table = "candidates"
        log.linked_id = candidate.id
        log.target_name = candidate.name or ""
        log.vehicle_number = candidate.vehicle_number or ""
        if actor and not log.issued_by:
            log.issued_by = actor

    db.commit()
    db.refresh(row)
    return row


def _sync_after_issue(db: Session, row, actor: str) -> None:
    """발급완료 시 발급일과 기존 발급번호 연결만 갱신한다."""
    candidate = _candidate_for_row(db, row)
    if candidate:
        candidate.certificate_issue_date = row.certificate_issue_date or candidate.certificate_issue_date
        # 번호는 예정자에서 먼저 부여된 값이 원본이므로 서로 다르면 덮어쓰지 않고 오류 처리한다.
        if (candidate.certificate_number or "").strip() != (row.document_number or "").strip():
            raise HTTPException(400, "예정자의 자격증명발급번호와 발급대장 번호가 일치하지 않습니다.")

    if row.member_id:
        member = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == row.member_id).first()
        if member:
            member.certificate_issue_date = row.certificate_issue_date or member.certificate_issue_date
            if not (member.certificate_number or "").strip():
                member.certificate_number = row.document_number or ""

    number = (row.document_number or "").strip()
    log = (
        db.query(models.CertificateNumberLog)
        .filter(models.CertificateNumberLog.certificate_number == number)
        .first()
    )
    if not log:
        raise HTTPException(400, f"자격증명발급번호 {number}의 발급 이력이 없습니다.")
    if log.status == "cancelled":
        raise HTTPException(400, f"자격증명발급번호 {number}는 취소된 번호입니다.")
    if log.status == "used" and not _same_number_subject(log, row):
        raise HTTPException(400, f"자격증명발급번호 {number}는 다른 대상자에게 이미 사용 중입니다.")

    log.status = "used"
    log.linked_table = "license_holders" if row.member_id else "candidates"
    log.linked_id = row.member_id or row.candidate_id
    log.target_name = row.name or ""
    log.vehicle_number = row.vehicle_number or ""
    if not log.issued_by:
        log.issued_by = actor


@router.get("/candidates")
async def candidate_choices(
    search: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    ensure_ledger_schema(db)
    q = db.query(models.Candidate).filter(models.Candidate.deleted_at.is_(None))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                models.Candidate.name.ilike(pattern),
                models.Candidate.vehicle_number.ilike(pattern),
                models.Candidate.certificate_number.ilike(pattern),
                models.Candidate.region.ilike(pattern),
            )
        )
    rows = q.order_by(models.Candidate.id.desc()).limit(limit).all()

    for candidate in rows:
        try:
            ensure_candidate_ledger(db, candidate, user)
        except Exception:
            db.rollback()

    ledger_rows = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id.isnot(None),
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    )
    by_candidate = {r.candidate_id: r for r in ledger_rows}

    return {
        "items": [
            {
                "id": row.id,
                "region": row.region or "",
                "vehicle_number": row.vehicle_number or "",
                "name": row.name or "",
                "document_number": (row.certificate_number or "").strip(),
                "is_registered": bool(row.is_registered),
                "member_id": row.member_id,
                "already_connected": row.id in by_candidate,
                "ledger_id": by_candidate[row.id].id if row.id in by_candidate else None,
                "ledger_status": by_candidate[row.id].status if row.id in by_candidate else None,
            }
            for row in rows
        ]
    }


@router.get("/stats")
async def ledger_stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    ensure_ledger_schema(db)
    reconcile_registered_candidates(db)
    base = db.query(ledger_models.CertificateIssuanceLedger).filter(
        ledger_models.CertificateIssuanceLedger.deleted_at.is_(None)
    )
    counts = {
        status: base.filter(ledger_models.CertificateIssuanceLedger.status == status).count()
        for status in VALID_STATUSES
    }
    return {"total": sum(counts.values()), "counts": counts}


@router.get("")
async def list_ledger(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    ensure_ledger_schema(db)
    reconcile_registered_candidates(db)
    q = db.query(ledger_models.CertificateIssuanceLedger).filter(
        ledger_models.CertificateIssuanceLedger.deleted_at.is_(None)
    )
    if status:
        if status not in VALID_STATUSES:
            raise HTTPException(400, "처리상태 값이 올바르지 않습니다.")
        q = q.filter(ledger_models.CertificateIssuanceLedger.status == status)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                ledger_models.CertificateIssuanceLedger.name.ilike(pattern),
                ledger_models.CertificateIssuanceLedger.vehicle_number.ilike(pattern),
                ledger_models.CertificateIssuanceLedger.document_number.ilike(pattern),
                ledger_models.CertificateIssuanceLedger.qualification_number.ilike(pattern),
                ledger_models.CertificateIssuanceLedger.region.ilike(pattern),
                ledger_models.CertificateIssuanceLedger.latest_operator.ilike(pattern),
            )
        )
    total = q.count()
    rows = (
        q.order_by(ledger_models.CertificateIssuanceLedger.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "items": [_item(row) for row in rows],
        "total": total,
        "page": page,
        "pages": max(1, ceil(total / limit)),
        "limit": limit,
    }


@router.post("")
async def create_ledger(
    body: CreateLedgerBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    ensure_ledger_schema(db)
    candidate = (
        db.query(models.Candidate)
        .filter(
            models.Candidate.id == body.candidate_id,
            models.Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if not candidate:
        raise HTTPException(404, "예정자를 찾을 수 없습니다.")

    row = ensure_candidate_ledger(db, candidate, user)
    if not row:
        raise HTTPException(500, "자격증명 발급대장을 생성하지 못했습니다.")

    if body.qualification_number and not row.qualification_number:
        row.qualification_number = body.qualification_number.strip()
        row.latest_operator = operator_name(user)
        db.commit()
        db.refresh(row)

    # body.document_number로 새 번호를 만들거나 덮어쓰지 않는다.
    # 예정자에 이미 부여된 번호가 있으면 그것만 연결한다.
    if (candidate.certificate_number or "").strip():
        row = _sync_existing_number_from_candidate(db, row, operator_name(user))
    return _item(row)


@router.post("/{ledger_id}/prepare")
async def prepare_issue(
    ledger_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """하위호환 엔드포인트.

    과거 버전처럼 새 번호를 예약하지 않고, 예정자 입력 단계에서 이미 부여된 번호만 확인/연결한다.
    """
    row = _get_row(db, ledger_id)
    row = _sync_existing_number_from_candidate(db, row, operator_name(user))
    return _item(row)


@router.get("/{ledger_id}/history")
async def ledger_history(
    ledger_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    _get_row(db, ledger_id)
    rows = (
        db.query(ledger_models.CertificateIssuanceHistory)
        .filter(ledger_models.CertificateIssuanceHistory.ledger_id == ledger_id)
        .order_by(ledger_models.CertificateIssuanceHistory.id.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "operator": row.operator or "",
                "memo": row.memo or "",
                "created_at": _dt(row.created_at),
            }
            for row in rows
        ]
    }


@router.post("/{ledger_id}/issue")
async def complete_issue(
    ledger_id: int,
    body: IssueLedgerBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _get_row(db, ledger_id)
    if row.status == ISSUED:
        return _item(row)

    # 실제 업무상 자격증명은 시청 인가 전에 만들 수 있으므로 인가대기/인가완료 모두 허용한다.
    if row.status not in (WAITING, APPROVED):
        raise HTTPException(400, "현재 상태에서는 발급완료 처리할 수 없습니다.")

    actor = operator_name(user)
    row = _sync_existing_number_from_candidate(db, row, actor)

    qualification_number = (body.qualification_number or row.qualification_number or "").strip()
    issue_date = (body.issue_date or "").strip() or date.today().isoformat()
    requested_number = (body.document_number or "").strip()
    document_number = (row.document_number or "").strip()

    if not qualification_number:
        raise HTTPException(400, "자격증번호를 입력하세요.")
    if not document_number:
        raise HTTPException(400, "자격증명발급번호가 없습니다. 예정자 입력 화면에서 먼저 발급번호를 부여하세요.")
    if requested_number and requested_number != document_number:
        raise HTTPException(400, "증명서 No.는 예정자에게 이미 부여된 자격증명발급번호와 같아야 합니다.")

    previous = row.status
    row.qualification_number = qualification_number
    row.certificate_issue_date = issue_date
    row.status = ISSUED
    row.issued_at = datetime.now(timezone.utc)
    row.latest_operator = actor

    try:
        _sync_after_issue(db, row, actor)
        add_history(
            db,
            row.id,
            "발급완료",
            previous,
            ISSUED,
            actor,
            f"기존 발급번호 {document_number}로 자격증명 발급 / 발급일 {issue_date}",
        )
        db.commit()
        db.refresh(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"자격증명 발급완료 저장 중 오류가 발생했습니다: {exc}") from exc
    return _item(row)


@router.get("/{ledger_id}")
async def get_ledger(
    ledger_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _get_row(db, ledger_id)
    # 예정자에 나중에 번호를 부여한 경우 조회 시 대장에 즉시 반영한다.
    candidate = _candidate_for_row(db, row)
    if candidate and (candidate.certificate_number or "").strip() and row.status != ISSUED:
        row = _sync_existing_number_from_candidate(db, row, operator_name(user))
    return _item(row)
