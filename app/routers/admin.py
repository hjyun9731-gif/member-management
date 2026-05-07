"""
관리자 전용: 테이블 초기화 엔드포인트
- 잘못 업로드된 데이터 전체 삭제
- admin 권한만 사용 가능
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db
from app.auth import require_admin
from app import models

router = APIRouter()

TABLE_MAP = {
    "license_holders":  models.LicenseHolder,
    "transfer_ledger":  models.TransferLedger,
    "closures":         models.Closure,
    "change_history":   models.ChangeHistory,
    "candidates":       models.Candidate,
    "upload_histories": models.UploadHistory,
}


@router.delete("/reset/{table_name}")
async def reset_table(
    table_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """특정 테이블의 모든 데이터를 soft-delete로 초기화"""
    model = TABLE_MAP.get(table_name)
    if not model:
        raise HTTPException(400, f"알 수 없는 테이블: {table_name}. 가능: {list(TABLE_MAP.keys())}")

    now = datetime.now(timezone.utc)
    if hasattr(model, "deleted_at"):
        updated = db.query(model).filter(model.deleted_at.is_(None)).update(
            {"deleted_at": now}, synchronize_session=False
        )
    else:
        # UploadHistory는 deleted_at 없으므로 실제 삭제
        updated = db.query(model).delete(synchronize_session=False)

    db.commit()
    return {"ok": True, "table": table_name, "deleted_count": updated}


@router.delete("/reset-all")
async def reset_all(
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """모든 업로드 데이터 초기화 (사용자/설정 제외)"""
    now = datetime.now(timezone.utc)
    counts = {}
    for name, model in TABLE_MAP.items():
        if hasattr(model, "deleted_at"):
            cnt = db.query(model).filter(model.deleted_at.is_(None)).update(
                {"deleted_at": now}, synchronize_session=False
            )
        else:
            cnt = db.query(model).delete(synchronize_session=False)
        counts[name] = cnt
    db.commit()
    return {"ok": True, "deleted": counts}


@router.get("/db-status")
async def db_status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Railway DB 실제 상태 확인용"""
    from sqlalchemy import text, func

    # TransferLedger 상태
    tl_total = db.query(models.TransferLedger).filter(models.TransferLedger.deleted_at.is_(None)).count()
    tl_has_mgmt = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.management_number.isnot(None),
        models.TransferLedger.management_number != ''
    ).count()
    tl_has_receipt = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.receipt_date.isnot(None),
        models.TransferLedger.receipt_date != ''
    ).count()

    # 샘플 10건
    samples = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)
    ).limit(10).all()

    return {
        "transfer_ledger": {
            "total": tl_total,
            "has_management_number": tl_has_mgmt,
            "no_management_number": tl_total - tl_has_mgmt,
            "has_receipt_date": tl_has_receipt,
            "samples": [
                {
                    "id": r.id,
                    "management_number": r.management_number,
                    "receipt_date": r.receipt_date,
                    "process_date": r.process_date,
                    "approval_date": r.approval_date,
                    "transferee": r.transferee,
                }
                for r in samples
            ]
        },
        "license_holders": {
            "total": db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).count(),
            "individual": db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.category=='개인').count(),
            "delivery": db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.category=='택배').count(),
        }
    }
