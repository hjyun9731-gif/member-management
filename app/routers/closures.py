from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, parse_date_sort

router = APIRouter()

SEARCH = ["name", "vehicle_number", "management_number", "region", "reason", "company_name"]


def _fmt(c):
    return {
        "id": c.id,
        "management_number": c.management_number or "",
        "closure_type": c.closure_type or "",
        "data_type": c.data_type or "신규자료",
        "region": c.region or "",
        "vehicle_number": c.vehicle_number or "",
        "name": c.name or "",
        "company_name": c.company_name or "",
        "closure_date": c.closure_date or "",
        "approval_date": c.approval_date or "",
        "reason": c.reason or "",
        "memo": c.memo or "",
        "created_at": str(c.created_at)[:10] if c.created_at else "",
    }


@router.get("")
async def list_closures(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    closure_type: Optional[str] = Query(None),
    data_type: Optional[str] = Query(None),
    date_order: Optional[str] = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    filters = {"region": region, "closure_type": closure_type, "data_type": data_type}
    items, total = crud.get_sorted_page(
        db, models.Closure,
        date_field="closure_date", sort_dir=date_order or "desc",
        page=page, limit=limit,
        search=search, search_fields=SEARCH, filters=filters,
        nonempty_any=["vehicle_number", "name"],
    )
    pages = max(1, (total + limit - 1) // limit)
    return {"items": [_fmt(i) for i in items], "total": total,
            "page": page, "pages": pages, "limit": limit}


@router.get("/next-number/{closure_type}")
async def next_number(closure_type: str, db: Session = Depends(get_db),
                       _=Depends(get_current_user)):
    return {"next_number": crud.get_next_closure_number(db, closure_type)}


@router.get("/export/excel")
async def export_excel(
    region: Optional[str] = Query(None),
    closure_type: Optional[str] = Query(None),
    data_type: Optional[str] = Query(None),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    filters = {"region": region, "closure_type": closure_type, "data_type": data_type}
    items, _ = crud.get_list(db, models.Closure, skip=0, limit=9999, filters=filters)
    content = records_to_excel([_fmt(i) for i in items], exclude=["id"])
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=closures.xlsx"},
    )


@router.get("/{cid}")
async def get_closure(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = crud.get_by_id(db, models.Closure, cid)
    if not c:
        raise HTTPException(404)
    return _fmt(c)


@router.post("")
async def create_closure(data: dict, db: Session = Depends(get_db),
                          _=Depends(get_current_user)):
    if not data.get("management_number") and data.get("closure_type"):
        data["management_number"] = crud.get_next_closure_number(db, data["closure_type"])
    mgmt = data.get("management_number")
    if mgmt and crud.check_mgmt_dup(db, models.Closure, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    return _fmt(crud.create_item(db, models.Closure, data))


@router.put("/{cid}")
async def update_closure(cid: int, data: dict, db: Session = Depends(get_db),
                          _=Depends(get_current_user)):
    c = crud.get_by_id(db, models.Closure, cid)
    if not c:
        raise HTTPException(404)
    new_mgmt = data.get("management_number")
    if new_mgmt and new_mgmt != c.management_number:
        if crud.check_mgmt_dup(db, models.Closure, new_mgmt, exclude_id=cid):
            raise HTTPException(400, f"관리번호 {new_mgmt}가 이미 존재합니다.")
    return _fmt(crud.update_item(db, c, data))


@router.delete("/{cid}")
async def delete_closure(cid: int, db: Session = Depends(get_db),
                          _=Depends(require_admin)):
    c = crud.get_by_id(db, models.Closure, cid)
    if not c:
        raise HTTPException(404)
    crud.soft_delete(db, c)
    return {"ok": True}
