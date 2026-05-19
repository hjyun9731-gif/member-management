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


@router.get("/check-closed-new-members")
async def check_closed_new_members(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """신규등록대장에서 폐업 처리로 숨겨진 회원 현황 확인"""
    closed = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
        models.LicenseHolder.status == "closed",
    ).all()

    return {
        "숨겨진_신규회원수": len(closed),
        "목록": [
            {
                "id": m.id,
                "management_number": m.management_number,
                "name": m.name,
                "vehicle_number": m.vehicle_number,
                "approval_date": m.approval_date,
                "status": m.status,
            }
            for m in closed
        ]
    }


@router.get("/dashboard-verify")
async def dashboard_verify(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """대시보드 집계 실제 검증"""
    from datetime import datetime
    from app.routers.dashboard import _ext_year

    cur_year = datetime.now().year

    # 1. 신규 집계: 관리번호 신26-* 전체 vs 인가일자 2026년
    shin26_total = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
    ).count()

    shin26_active = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
        models.LicenseHolder.status == "active",
    ).count()

    shin26_closed = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
        models.LicenseHolder.status == "closed",
    ).count()

    # 관리번호 기준 연도별 집계 (신YY-* → YY년도, 접수일자/인가일자/status 무관)
    import re as _re
    shin_all = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
    ).all()

    by_mgmt_year = {}
    bad_format = []
    for m in shin_all:
        mgmt = (m.management_number or "").strip()
        m2 = _re.match(r'^신(\d{2})[-]', mgmt)
        if m2:
            yy = int(m2.group(1))
            cur_yy = datetime.now().year % 100
            y = 2000 + yy if yy <= cur_yy else 1900 + yy
            by_mgmt_year[y] = by_mgmt_year.get(y, 0) + 1
        else:
            bad_format.append({"id": m.id, "management_number": mgmt, "status": m.status})

    # 2. 폐업현황 집계
    total_closures = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)
    ).count()

    # 3. 전체 회원
    total_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    ).count()

    return {
        "신26_관리번호_전체": shin26_total,
        "신26_active": shin26_active,
        "신26_closed(신규등록대장에서_숨겨진수)": shin26_closed,
        "관리번호기준_연도별_신규수": dict(sorted(by_mgmt_year.items(), reverse=True)),
        "관리번호_형식_불일치": bad_format[:10],
        "폐업현황_전체": total_closures,
        "현재_active_회원수": total_members,
    }
