from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import extract
import io, pandas as pd

from app.database import get_db
from app.auth import get_current_user
from app import models, crud

router = APIRouter()


def _ym_filter(q, model, year, month):
    return q.filter(extract("year", model.created_at) == year,
                    extract("month", model.created_at) == month,
                    model.deleted_at.is_(None))


@router.get("/monthly")
async def monthly(year: int = Query(...), month: int = Query(...),
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    lh = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.status == "active")
    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    alloc = db.query(models.AllocationCount).filter(
        models.AllocationCount.year == year, models.AllocationCount.month == month
    ).first()
    return {
        "year": year, "month": month,
        "member_stats": {
            "total": lh.count(),
            "joined": lh.filter(models.LicenseHolder.membership_status == "가입").count(),
            "individual": lh.filter(models.LicenseHolder.category == "개인").count(),
            "delivery": lh.filter(models.LicenseHolder.category == "택배").count(),
        },
        "monthly_counts": {
            "new_members": _ym_filter(db.query(models.LicenseHolder).filter(
                models.LicenseHolder.registration_type == "신규"),
                models.LicenseHolder, year, month).count(),
            "transfers": _ym_filter(db.query(models.TransferLedger), models.TransferLedger, year, month).count(),
            "closures": _ym_filter(db.query(models.Closure), models.Closure, year, month).count(),
        },
        "manual_entry": {
            "document_number": entry.document_number if entry else "",
            "execution_date": entry.execution_date if entry else "",
            "memo": entry.memo if entry else "",
            "custom_data": entry.custom_data if entry else {},
        },
        "allocation": {
            "association_join": alloc.association_join if alloc else 0,
            "transfer_in": alloc.transfer_in if alloc else 0,
            "other_region": alloc.other_region if alloc else 0,
            "closed": alloc.closed if alloc else 0,
            "withdrawn": alloc.withdrawn if alloc else 0,
            "delivery_new": alloc.delivery_new if alloc else 0,
            "mgmt_fee_closed": alloc.mgmt_fee_closed if alloc else 0,
            "over_70": alloc.over_70 if alloc else 0,
            "base_count": alloc.base_count if alloc else 0,
            "total_count": alloc.total_count if alloc else 0,
            "delivery_mgmt": alloc.delivery_mgmt if alloc else 0,
        } if alloc else None,
    }


@router.post("/monthly/save")
async def save_entry(year: int = Query(...), month: int = Query(...),
                      data: dict = None,
                      db: Session = Depends(get_db), _=Depends(get_current_user)):
    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    from datetime import datetime
    if entry:
        entry.document_number = data.get("document_number", "")
        entry.execution_date = data.get("execution_date", "")
        entry.memo = data.get("memo", "")
        entry.custom_data = data.get("custom_data", {})
        entry.updated_at = datetime.utcnow()
    else:
        entry = models.MonthlyReportEntry(year=year, month=month,
            document_number=data.get("document_number", ""),
            execution_date=data.get("execution_date", ""),
            memo=data.get("memo", ""), custom_data=data.get("custom_data", {}))
        db.add(entry)
    db.commit()
    return {"ok": True}


@router.get("/monthly/export")
async def export(year: int = Query(...), month: int = Query(...),
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        def sheet(data, name):
            pd.DataFrame(data or [{}]).to_excel(w, sheet_name=name, index=False)
        sheet([{"관리번호": r.management_number, "지역": r.region, "차량번호": r.vehicle_number,
                "성명": r.name, "등록구분": r.registration_type}
               for r in _ym_filter(db.query(models.LicenseHolder).filter(
                   models.LicenseHolder.registration_type == "신규"),
                   models.LicenseHolder, year, month).all()], "신규등록")
        sheet([{"지역": r.region, "차량번호": r.vehicle_number, "양도자": r.transferor,
                "양수자": r.transferee, "인가일자": r.approval_date}
               for r in _ym_filter(db.query(models.TransferLedger), models.TransferLedger, year, month).all()], "양도양수")
        sheet([{"관리번호": r.management_number, "지역": r.region, "차량번호": r.vehicle_number,
                "성명": r.name, "구분": r.closure_type}
               for r in _ym_filter(db.query(models.Closure), models.Closure, year, month).all()], "폐지")
    out.seek(0)
    return StreamingResponse(out,
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": f"attachment; filename=report_{year}_{month:02d}.xlsx"})
