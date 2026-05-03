from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud

router = APIRouter()

SEARCH = ["name", "vehicle_number", "phone", "mobile", "certificate_number", "region"]


@router.get("")
async def list_candidates(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    filters = {"region": region, "is_registered": False}
    items, total = crud.get_list(db, models.Candidate, skip=(page-1)*limit, limit=limit,
                                  search=search, search_fields=SEARCH, filters=filters)
    return {"items": [_fmt(i) for i in items], "total": total,
            "page": page, "pages": max(1, (total+limit-1)//limit), "limit": limit}


def _fmt(c):
    return {
        "id": c.id, "region": c.region, "vehicle_number": c.vehicle_number, "name": c.name,
        "resident_number": c.resident_number, "address": c.address, "phone": c.phone, "mobile": c.mobile,
        "certificate_issue_date": c.certificate_issue_date, "certificate_number": c.certificate_number,
        "driver_license_number": c.driver_license_number, "vehicle_type": c.vehicle_type,
        "fuel_type": c.fuel_type, "business_number": c.business_number,
        "affiliated_company": c.affiliated_company, "memo": c.memo,
        "is_registered": c.is_registered, "member_id": c.member_id,
        "created_at": str(c.created_at)[:16] if c.created_at else None,
    }


@router.get("/{cid}")
async def get_candidate(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    item = crud.get_by_id(db, models.Candidate, cid)
    if not item:
        raise HTTPException(404, "예정자를 찾을 수 없습니다.")
    return _fmt(item)


@router.post("")
async def create_candidate(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _fmt(crud.create_item(db, models.Candidate, data))


@router.put("/{cid}")
async def update_candidate(cid: int, data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    item = crud.get_by_id(db, models.Candidate, cid)
    if not item:
        raise HTTPException(404, "예정자를 찾을 수 없습니다.")
    return _fmt(crud.update_item(db, item, data))


@router.delete("/{cid}")
async def delete_candidate(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    item = crud.get_by_id(db, models.Candidate, cid)
    if not item:
        raise HTTPException(404, "예정자를 찾을 수 없습니다.")
    crud.soft_delete(db, item)
    return {"ok": True}


class RegisterBody(BaseModel):
    approval_date: str
    management_number: Optional[str] = None


@router.post("/{cid}/register")
async def register_as_member(cid: int, body: RegisterBody,
                              db: Session = Depends(get_db), _=Depends(get_current_user)):
    mgmt = body.management_number or crud.get_next_new_member_number(db)
    if crud.check_mgmt_dup(db, models.LicenseHolder, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    member = crud.register_candidate_as_member(db, cid, body.approval_date, mgmt)
    return {"ok": True, "management_number": mgmt, "member_id": member.id,
            "category": member.category}
