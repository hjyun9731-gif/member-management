"""관리자 전용, 백업 확인 전에는 모두 미리보기 전용인 정정 기능."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.auth import require_admin
from app.services import safe_corrections as sc

router = APIRouter(prefix="/api/admin/data-corrections", tags=["관리자 정정 검증"])


class TransferCorrectionBody(BaseModel):
    member_id: int = Field(..., ge=1)
    desired_number: str
    expected_old: str
    reason: str = Field(..., min_length=10, max_length=200)


class CertificateRelinkBody(BaseModel):
    ledger_id: int = Field(..., ge=1)
    target_type: str
    target_id: int = Field(..., ge=1)
    reason: str = Field(..., min_length=10, max_length=200)


def _apply(func):
    try:
        return func()
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/status")
def status(_=Depends(require_admin)):
    return {"writes_enabled": sc.writes_enabled(), "requires_backup_verification": True}


@router.get("/transfer/preview")
def transfer_preview(member_id: int = Query(..., ge=1), desired_number: str = Query(..., min_length=1),
                     db: Session = Depends(get_db), _=Depends(require_admin)):
    return sc.preview_transfer_correction(db, member_id, desired_number)


@router.post("/transfer/apply")
def transfer_apply(body: TransferCorrectionBody, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return _apply(lambda: sc.apply_transfer_correction(db, body.member_id, body.desired_number,
                                                       body.expected_old, body.reason,
                                                       (admin.full_name or admin.username or '관리자')))


@router.get("/certificate/preview")
def certificate_preview(ledger_id: int = Query(..., ge=1), target_type: str = Query(...),
                        target_id: int = Query(..., ge=1), db: Session = Depends(get_db), _=Depends(require_admin)):
    return sc.preview_certificate_relink(db, ledger_id, target_type, target_id)


@router.get("/certificate/candidates")
def certificate_candidates(limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                           db: Session = Depends(get_db), _=Depends(require_admin)):
    return sc.certificate_relink_candidates(db, limit, offset)


@router.post("/certificate/apply")
def certificate_apply(body: CertificateRelinkBody, db: Session = Depends(get_db), admin=Depends(require_admin)):
    return _apply(lambda: sc.apply_certificate_relink(db, body.ledger_id, body.target_type,
                                                     body.target_id, body.reason,
                                                     (admin.full_name or admin.username or '관리자')))
