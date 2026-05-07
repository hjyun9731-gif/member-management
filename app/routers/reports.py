from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io, re, pandas as pd
from datetime import datetime

from app.database import get_db
from app.auth import get_current_user
from app import models, crud

router = APIRouter()


def _extract_year_month(date_str: str):
    """날짜 문자열에서 (year, month) 추출. 실패시 (None, None)"""
    if not date_str:
        return None, None
    s = str(date_str).strip()
    # 4자리 연도
    m = re.search(r'(19[0-9]{2}|20[0-9]{2})\s*[\.\-/]\s*(\d{1,2})', s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 2자리 연도
    m = re.match(r'^(\d{2})\s*[\.\-/]\s*(\d{1,2})', s)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy if yy <= 30 else 1900 + yy
        return year, int(m.group(2))
    return None, None


def _in_month(date_str: str, year: int, month: int) -> bool:
    y, mo = _extract_year_month(date_str)
    return y == year and mo == month


@router.get("/monthly")
async def monthly(year: int = Query(...), month: int = Query(...),
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    """월례보고서: 해당 연/월 기준으로 가입/미가입/신규/양도/폐업 집계"""
    all_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active"
    ).all()

    # 해당 월 가입자: membership_date(가입일자)가 해당 월인 사람
    month_joined = sum(1 for m in all_members
                       if _in_month(m.membership_date or '', year, month))
    # 해당 월 미가입자: membership_status가 미가입이고 approval_date(인가일자)가 해당 월인 사람
    month_not_joined = sum(1 for m in all_members
                           if m.membership_status != '가입' and _in_month(m.approval_date or '', year, month))

    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    alloc = db.query(models.AllocationCount).filter(
        models.AllocationCount.year == year, models.AllocationCount.month == month
    ).first()

    # 해당 월 신규/양도/폐업
    month_new = sum(1 for m in all_members
                    if m.registration_type == '신규' and _in_month(m.approval_date or '', year, month))
    # 양도: process_date 기준
    month_transfers = sum(1 for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)).all()
        if _in_month(t.process_date or '', year, month))
    # 폐업: closure_date 기준
    month_closures = sum(1 for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()
        if _in_month(c.closure_date or '', year, month))

    return {
        "year": year, "month": month,
        "member_stats": {
            "total": len(all_members),
            "joined": sum(1 for m in all_members if m.membership_status == "가입"),
            "individual": sum(1 for m in all_members if m.category == "개인"),
            "delivery": sum(1 for m in all_members if m.category == "택배"),
            "month_joined": month_joined,
            "month_not_joined": month_not_joined,
        },
        "monthly_counts": {
            "new_members": month_new,
            "transfers": month_transfers,
            "closures": month_closures,
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
    all_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.status == "active").all()
    new_members = [m for m in all_members
                   if m.registration_type == "신규" and _in_month(m.approval_date or '', year, month)]
    transfer_list = [t for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)).all()
        if _in_month(t.process_date or '', year, month)]
    closure_list = [c for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()
        if _in_month(c.closure_date or '', year, month)]

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        def sheet(data, name):
            pd.DataFrame(data or [{}]).to_excel(w, sheet_name=name, index=False)
        sheet([{"관리번호": r.management_number, "지역": r.region, "차량번호": r.vehicle_number,
                "성명": r.name, "등록구분": r.registration_type} for r in new_members], "신규등록")
        sheet([{"지역": r.region, "차량번호": r.vehicle_number, "양도자": r.transferor,
                "양수자": r.transferee, "처리일자": r.process_date} for r in transfer_list], "양도양수")
        sheet([{"관리번호": r.management_number, "지역": r.region, "차량번호": r.vehicle_number,
                "성명": r.name, "구분": "폐업"} for r in closure_list], "폐업")
    out.seek(0)
    return StreamingResponse(out,
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": f"attachment; filename=report_{year}_{month:02d}.xlsx"})
