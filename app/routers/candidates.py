from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.services.certificate_ledger_service import (
    ensure_candidate_ledger, ensure_ledger_schema, mark_candidate_approved,
)

router = APIRouter()

SEARCH = ["name", "vehicle_number", "phone", "mobile", "certificate_number", "region"]


@router.post("/issue-certificate-number")
async def issue_certificate_number(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """자격증명발급번호 자동 채번 (YY-N). 예정자/도내양도 등록 화면 공통 사용."""
    try:
        return {"certificate_number": crud.get_next_certificate_number(db, issued_by=user.username)}
    except ValueError as e:
        raise HTTPException(500, str(e))


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
        "affiliated_company": c.affiliated_company,
        "membership_date": getattr(c, 'membership_date', '') or "",   # 가입일자
        "memo": c.memo,
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
async def create_candidate(data: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = crud.create_item(db, models.Candidate, data)
    if item.certificate_number:
        crud.sync_certificate_number_usage(db, item.certificate_number, "candidates", item.id,
                                            item.name or "", item.vehicle_number or "")
    # 예정자 저장 직후 자격증명 발급대장도 인가대기 상태로 1건 연결한다.
    # 기존 예정자 저장 성공 자체는 이 부가연동 실패 때문에 취소하지 않는다.
    try:
        ensure_ledger_schema(db)
        ensure_candidate_ledger(db, item, user)
    except Exception:
        db.rollback()
    return _fmt(item)


@router.put("/{cid}")
async def update_candidate(cid: int, data: dict, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = crud.get_by_id(db, models.Candidate, cid)
    if not item:
        raise HTTPException(404, "예정자를 찾을 수 없습니다.")
    item = crud.update_item(db, item, data)
    if item.certificate_number:
        crud.sync_certificate_number_usage(db, item.certificate_number, "candidates", item.id,
                                            item.name or "", item.vehicle_number or "")
    # 예정자에 자격증명발급번호를 나중에 부여/수정한 경우 발급대장에도 같은 번호를 반영한다.
    try:
        ensure_ledger_schema(db)
        ensure_candidate_ledger(db, item, user)
    except Exception:
        db.rollback()
    return _fmt(item)


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
    membership_date: Optional[str] = ""   # 가입일자 (없으면 미가입)


@router.post("/{cid}/register")
async def register_as_member(cid: int, body: RegisterBody,
                              db: Session = Depends(get_db), user=Depends(get_current_user)):
    mgmt = body.management_number or crud.get_next_new_member_number(db)
    if crud.check_mgmt_dup(db, models.LicenseHolder, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    try:
        member = crud.register_candidate_as_member(
            db, cid, body.approval_date, mgmt,
            membership_date=body.membership_date or ""
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"예정자 등록 처리 중 오류가 발생했습니다: {e}")
    # 기존 회원등록이 성공한 뒤에만 발급대장에 인가일자/회원연결을 반영한다.
    # 자격증명이 이미 먼저 발급완료된 경우에는 발급완료 상태를 유지한다.
    try:
        ensure_ledger_schema(db)
        mark_candidate_approved(db, cid, member.id, body.approval_date, user)
    except Exception:
        db.rollback()
    return {"ok": True, "management_number": mgmt, "member_id": member.id,
            "category": member.category,
            "transfer_ledger_created": bool(member.transfer_ledger_id)}
