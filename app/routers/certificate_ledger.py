"""자격증명 발급대장 전용 API.

기존 API와 테이블은 수정하지 않는다. 모든 경로는 /api/certificate-ledger 아래에만 있다.
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
    operator_name,
    reconcile_registered_candidates,
)


router = APIRouter(prefix="/api/certificate-ledger", tags=["자격증명 발급대장"])
VALID_STATUSES = {WAITING, APPROVED, ISSUED}


class CreateLedgerBody(BaseModel):
    candidate_id: int
    qualification_number: Optional[str] = ""
    document_number: Optional[str] = ""


class IssueLedgerBody(BaseModel):
    issue_date: Optional[str] = ""


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


@router.get("/candidates")
async def candidate_choices(
    search: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
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
    rows = q.order_by(models.Candidate.is_registered.asc(), models.Candidate.id.desc()).limit(limit).all()
    connected_ids = {
        candidate_id
        for (candidate_id,) in db.query(ledger_models.CertificateIssuanceLedger.candidate_id)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id.isnot(None),
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    }
    return {
        "items": [
            {
                "id": row.id,
                "region": row.region or "",
                "vehicle_number": row.vehicle_number or "",
                "name": row.name or "",
                "document_number": row.certificate_number or "",
                "is_registered": bool(row.is_registered),
                "member_id": row.member_id,
                "already_connected": row.id in connected_ids,
            }
            for row in rows
        ]
    }


@router.get("/stats")
async def ledger_stats(
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    reconcile_registered_candidates(db)
    base = db.query(ledger_models.CertificateIssuanceLedger).filter(
        ledger_models.CertificateIssuanceLedger.deleted_at.is_(None)
    )
    counts = {status: base.filter(ledger_models.CertificateIssuanceLedger.status == status).count() for status in VALID_STATUSES}
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
    duplicate = (
        db.query(ledger_models.CertificateIssuanceLedger.id)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id == candidate.id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )
    if duplicate:
        raise HTTPException(400, "이 예정자는 이미 자격증명 발급대장에 연결되어 있습니다.")

    actor = operator_name(user)
    document_number = (body.document_number or "").strip() or (candidate.certificate_number or "").strip()
    generated = False
    if not document_number:
        try:
            document_number = crud.get_next_certificate_number(db, issued_by=actor)
            generated = True
        except ValueError as exc:
            raise HTTPException(500, str(exc)) from exc

    if db.query(ledger_models.CertificateIssuanceLedger.id).filter(
        ledger_models.CertificateIssuanceLedger.document_number == document_number,
        ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
    ).first():
        raise HTTPException(400, f"증명서 No. {document_number}가 이미 발급대장에 있습니다.")

    member = None
    if candidate.is_registered and candidate.member_id:
        member = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == candidate.member_id).first()
    initial_status = APPROVED if candidate.is_registered else WAITING
    now = datetime.now(timezone.utc)
    row = ledger_models.CertificateIssuanceLedger(
        candidate_id=candidate.id,
        member_id=candidate.member_id,
        region=candidate.region or "",
        vehicle_number=candidate.vehicle_number or "",
        name=candidate.name or "",
        qualification_number=(body.qualification_number or "").strip(),
        document_number=document_number,
        approval_date=(getattr(member, "approval_date", None) or "") if member else "",
        status=initial_status,
        latest_operator=actor,
        created_by=actor,
        approved_at=now if initial_status == APPROVED else None,
    )
    try:
        db.add(row)
        db.flush()
        add_history(
            db,
            row.id,
            "생성",
            None,
            initial_status,
            actor,
            "자격증명 발급대장 생성" + ("(증명서 No. 자동 채번)" if generated else ""),
        )
        if generated:
            log = db.query(models.CertificateNumberLog).filter(
                models.CertificateNumberLog.certificate_number == document_number
            ).first()
            if log:
                log.status = "used"
                log.linked_table = "certificate_issuance_ledger"
                log.linked_id = row.id
                log.target_name = row.name
                log.vehicle_number = row.vehicle_number
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(400, "같은 예정자 또는 증명서 No.가 이미 등록되어 있습니다.") from exc
    return _item(row)


@router.get("/{ledger_id}/history")
async def ledger_history(
    ledger_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    if not db.query(ledger_models.CertificateIssuanceLedger.id).filter(
        ledger_models.CertificateIssuanceLedger.id == ledger_id,
        ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
    ).first():
        raise HTTPException(404, "발급대장 기록을 찾을 수 없습니다.")
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
    reconcile_registered_candidates(db)
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
    if row.status == ISSUED:
        return _item(row)
    if row.status != APPROVED:
        raise HTTPException(400, "인가완료 상태에서만 발급완료 처리할 수 있습니다.")
    if not (row.qualification_number or "").strip():
        raise HTTPException(400, "자격증번호가 없어 발급할 수 없습니다.")

    actor = operator_name(user)
    previous = row.status
    row.status = ISSUED
    row.certificate_issue_date = (body.issue_date or "").strip() or date.today().isoformat()
    row.issued_at = datetime.now(timezone.utc)
    row.latest_operator = actor
    add_history(
        db,
        row.id,
        "발급완료",
        previous,
        ISSUED,
        actor,
        f"발급일 {row.certificate_issue_date}",
    )
    db.commit()
    db.refresh(row)
    return _item(row)


@router.get("/{ledger_id}")
async def get_ledger(
    ledger_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    reconcile_registered_candidates(db)
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
    return _item(row)
