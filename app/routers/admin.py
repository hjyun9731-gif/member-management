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
async def db_status(db: Session = Depends(get_db), _=Depends(require_admin)):
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


@router.post("/backfill-transfer-mgmt")
async def backfill_transfer_management_numbers(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """기존 TransferLedger에 management_number가 없는 행을 seq_number+data_year로 채움
    형식: 양YY-NN (예: 양26-28, 양00-15)
    """
    import re
    rows = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.management_number.is_(None) |
        (models.TransferLedger.management_number == '')
    ).all()

    updated = skipped = 0
    for r in rows:
        seq = str(r.seq_number or '').strip()
        try:
            seq_int = int(float(seq)) if seq else 0
        except (ValueError, TypeError):
            seq_int = 0

        if seq_int <= 0:
            skipped += 1
            continue

        # data_year 또는 receipt_date/approval_date에서 연도 추출
        year = r.data_year if hasattr(r, 'data_year') and r.data_year else None
        if not year:
            for date_field in [r.receipt_date, r.approval_date]:
                if date_field:
                    m = re.search(r'(\d{2,4})[.\-/]', str(date_field))
                    if m:
                        y = int(m.group(1))
                        year = (2000 + y) if y <= 99 and y >= 0 else y
                        break

        if not year:
            skipped += 1
            continue

        yy = str(year % 100).zfill(2)
        r.management_number = f"양{yy}-{seq_int}"
        updated += 1

    db.commit()
    return {
        "updated": updated,
        "skipped": skipped,
        "total": len(rows),
        "message": f"management_number 채우기 완료: {updated}건 업데이트"
    }


@router.post("/fix-transfer-dates")
async def fix_transfer_dates(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """기존 TransferLedger 데이터의 날짜 보정:
    raw_data의 원본 엑셀 값(접수일자, 인가일자)을 읽어서
    receipt_date / approval_date로 정확히 저장.
    처리일자(process_date) 개념 제거.
    """
    import re

    def _clean(v):
        if not v: return None
        s = str(v).strip().rstrip('.')
        return s if s and s.lower() not in ('nan','none','nat','') else None

    rows = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)
    ).all()

    fixed = skipped = 0
    for r in rows:
        if not isinstance(r.raw_data, dict):
            skipped += 1
            continue

        rd = r.raw_data
        # 접수일자: raw_data에서 정규화된 키 탐색
        receipt = _clean(rd.get('접수일자') or rd.get('접수\n일자') or rd.get('접수 일자'))
        approval = _clean(rd.get('인가일자') or rd.get('인가\n일자') or rd.get('인가 일자'))

        changed = False
        if receipt and r.receipt_date != receipt:
            r.receipt_date = receipt
            changed = True
        if approval and r.approval_date != approval:
            r.approval_date = approval
            changed = True
        # process_date는 clear (양도양수에 처리일자 없음)
        if r.process_date:
            r.process_date = None
            changed = True

        if changed:
            fixed += 1

    db.commit()
    return {
        "fixed": fixed,
        "skipped": skipped,
        "total": len(rows),
        "message": f"날짜 보정 완료: {fixed}건 수정 (접수일자→receipt_date, 인가일자→approval_date)"
    }


@router.delete("/upload/{history_id}")
async def delete_upload(
    history_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """특정 업로드 이력의 데이터만 삭제 (다른 업로드 데이터 유지)."""
    from datetime import datetime, timezone
    hist = db.query(models.UploadHistory).filter(
        models.UploadHistory.id == history_id
    ).first()
    if not hist:
        raise HTTPException(status_code=404, detail="업로드 이력을 찾을 수 없습니다.")

    deleted = {}
    for model, label in [
        (models.LicenseHolder, "면허자현황"),
        (models.TransferLedger, "양도양수대장"),
        (models.Closure, "폐지현황"),
        (models.ChangeHistory, "변경이력대장"),
    ]:
        if not hasattr(model, 'upload_id'):
            continue
        rows = db.query(model).filter(
            model.upload_id == history_id,
            model.deleted_at.is_(None),
        ).all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.deleted_at = now
        deleted[label] = len(rows)

    # 이력도 삭제 처리
    db.delete(hist)
    db.commit()

    total = sum(deleted.values())
    return {
        "deleted_total": total,
        "deleted_by_type": deleted,
        "message": f"업로드 이력 #{history_id} 삭제 완료: 총 {total}건",
    }
