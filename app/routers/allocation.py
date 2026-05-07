from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io, pandas as pd

from app.database import get_db
from app.auth import get_current_user
from app import models

router = APIRouter()

FIELDS = ["association_join","transfer_in","other_region","closed","withdrawn",
          "delivery_new","mgmt_fee_closed","over_70","base_count","total_count","delivery_mgmt"]

LABELS = {"association_join":"협회가입","transfer_in":"양도","other_region":"타도",
          "closed":"폐지","withdrawn":"탈퇴","delivery_new":"택배신규",
          "mgmt_fee_closed":"관리비폐지","over_70":"70세","base_count":"협회기본대수",
          "total_count":"총부과대수","delivery_mgmt":"택배관리"}


def _fmt(a):
    return {"id": a.id, "year": a.year, "month": a.month,
            **{f: getattr(a, f, 0) for f in FIELDS}, "memo": a.memo,
            "updated_at": str(a.updated_at)[:16] if a.updated_at else None}


@router.get("")
async def get_allocation(year: int = Query(...), month: int = Query(...),
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    row = db.query(models.AllocationCount).filter(
        models.AllocationCount.year == year,
        models.AllocationCount.month == month
    ).first()
    if not row:
        return {"year": year, "month": month, **{f: 0 for f in FIELDS}, "memo": ""}
    return _fmt(row)


@router.get("/list")
async def list_allocations(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows = db.query(models.AllocationCount).order_by(
        models.AllocationCount.year.desc(), models.AllocationCount.month.desc()
    ).all()
    return [_fmt(r) for r in rows]


@router.post("")
async def save_allocation(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    year, month = int(data.get("year", 0)), int(data.get("month", 0))
    row = db.query(models.AllocationCount).filter(
        models.AllocationCount.year == year,
        models.AllocationCount.month == month
    ).first()
    if row:
        for f in FIELDS:
            setattr(row, f, int(data.get(f, 0)))
        row.memo = data.get("memo", "")
        from datetime import datetime
        row.updated_at = datetime.datetime.now(datetime.timezone.utc)
    else:
        row = models.AllocationCount(
            year=year, month=month,
            **{f: int(data.get(f, 0)) for f in FIELDS},
            memo=data.get("memo", "")
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return _fmt(row)


@router.get("/export/excel")
async def export(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows = db.query(models.AllocationCount).order_by(
        models.AllocationCount.year, models.AllocationCount.month
    ).all()
    records = [{"연도": r.year, "월": r.month,
                **{LABELS[f]: getattr(r, f, 0) for f in FIELDS},
                "메모": r.memo} for r in rows]
    df = pd.DataFrame(records)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df.to_excel(w, index=False)
    out.seek(0)
    return StreamingResponse(out,
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": "attachment; filename=allocation.xlsx"})
