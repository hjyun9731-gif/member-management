from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, parse_date_sort, normalize_closure_type

router = APIRouter()

SEARCH = ["name", "vehicle_number", "management_number", "region", "reason", "company_name"]


def _fmt(c):
    ct = c.closure_type or ""
    if ct == '폐지':
        ct = '폐업'
    return {
        "id": c.id,
        "management_number": c.management_number or "",
        "closure_type": ct,
        "data_type": c.data_type or "신규자료",
        "region": c.region or "",
        "vehicle_number": c.vehicle_number or "",
        "name": c.name or "",
        "company_name": c.company_name or "",
        "closure_date": c.closure_date or "",
        "approval_date": c.approval_date or "",
        "reason": c.reason or "",
        "transferee": getattr(c, 'transferee', '') or "",        # 양수인 (양도 시)
        "transfer_region": getattr(c, 'transfer_region', '') or "",  # 이관지역 / 양도지역
        "memo": c.memo or "",
        "member_id": getattr(c, 'member_id', None),
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
    # '폐업' 필터 시 DB에 '폐지'로 저장된 데이터도 포함 (or_ 방식)
    from sqlalchemy import or_
    base_q = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None))
    if region:
        base_q = base_q.filter(models.Closure.region == region)
    if closure_type:
        if closure_type == '폐업':
            base_q = base_q.filter(or_(models.Closure.closure_type == '폐업', models.Closure.closure_type == '폐지'))
        else:
            base_q = base_q.filter(models.Closure.closure_type == closure_type)
    if data_type:
        base_q = base_q.filter(models.Closure.data_type == data_type)
    if search:
        from sqlalchemy import or_ as _or
        conds = [getattr(models.Closure, f).ilike(f"%{search}%") for f in SEARCH if hasattr(models.Closure, f)]
        if conds:
            base_q = base_q.filter(_or(*conds))
    # nonempty filter
    from sqlalchemy import and_
    base_q = base_q.filter(or_(
        and_(models.Closure.vehicle_number.isnot(None), models.Closure.vehicle_number != ''),
        and_(models.Closure.name.isnot(None), models.Closure.name != ''),
    ))

    date_order_v = date_order or "desc"
    # 날짜 기준 정렬 (기본) or 관리번호 기준 정렬
    if date_order_v in ("mgmt_desc", "mgmt_asc"):
        sort_dir = "desc" if date_order_v == "mgmt_desc" else "asc"
        from app import crud as _crud
        all_items_raw = base_q.with_entities(models.Closure.id, models.Closure.management_number, models.Closure.closure_date).all()
        from app.excel_utils import mgmt_sort_key, parse_date_sort
        reverse = sort_dir == "desc"
        all_items_raw.sort(key=lambda r: mgmt_sort_key(r[1] or ''), reverse=reverse)
    else:
        from app.excel_utils import parse_date_sort
        all_items_raw = base_q.with_entities(models.Closure.id, models.Closure.closure_date).all()
        reverse = date_order_v == "desc"
        all_items_raw.sort(key=lambda r: parse_date_sort(r[1] or ""), reverse=reverse)
    total = len(all_items_raw)
    page_ids = [r[0] for r in all_items_raw[(page - 1) * limit: page * limit]]
    if page_ids:
        items = db.query(models.Closure).filter(models.Closure.id.in_(page_ids)).all()
        items_by_id = {i.id: i for i in items}
        items = [items_by_id[pid] for pid in page_ids if pid in items_by_id]
    else:
        items = []
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
    # 폐지 → 폐업 통일
    if data.get("closure_type"):
        data["closure_type"] = normalize_closure_type(data["closure_type"])
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
    # 폐지 → 폐업 통일
    if data.get("closure_type"):
        data["closure_type"] = normalize_closure_type(data["closure_type"])
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
