from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io
import re
import datetime

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, parse_date_sort

_FUEL_VALID = {'전기', '경유', 'LPG', '휘발유', '기타'}
_FUEL_BAD_RE = re.compile(r'^[\d\.\,]|포터|봉고|트럭|탑차|냉동|사다리|픽업|렉스턴', re.I)

def _normalize_fuel(fuel: str) -> str:
    if not fuel:
        return ""
    f = str(fuel).strip()
    if not f or f in ('.', '-', 'nan', 'None'):
        return ""
    if _FUEL_BAD_RE.search(f):
        return ""
    fl = f.lower().replace(' ', '')
    if '전기' in fl: return '전기'
    if '경유' in fl or '디젤' in fl: return '경유'
    if 'lpg' in fl or 'lp가스' in fl or '엘피지' in fl or '가스' in fl: return 'LPG'
    if '휘발유' in fl or '가솔린' in fl: return '휘발유'
    if '하이브리드' in fl: return '기타'
    return f

router = APIRouter()

SEARCH = ["name", "vehicle_number", "phone", "mobile", "management_number",
          "certificate_number", "address", "affiliated_company"]


_HIDDEN_FIELDS = {'허가번호', 'permit_number', 'status', 'active', '등록구분', 'registration_type'}
_UNNAMED_RE = re.compile(r'^Unnamed\s*[:.]?\s*\d+', re.I)


def _clean_raw(raw_data: dict) -> dict:
    if not raw_data:
        return {}
    return {k: v for k, v in raw_data.items()
            if k not in _HIDDEN_FIELDS and not _UNNAMED_RE.match(str(k))}


def _fmt(m):
    return {
        "id": m.id,
        "management_number": m.management_number or "",
        "region": m.region or "",
        "vehicle_number": m.vehicle_number or "",
        "name": m.name or "",
        "category": m.category or "",
        "address": m.address or "",
        "phone": m.phone or "",
        "mobile": m.mobile or "",
        "membership_status": m.membership_status or "",
        "membership_date": m.membership_date or "",
        "approval_date": m.approval_date or "",
        "certificate_issue_date": m.certificate_issue_date or "",
        "certificate_number": m.certificate_number or "",
        "driver_license_number": m.driver_license_number or "",
        "vehicle_type": m.vehicle_type or "",
        "fuel_type": _normalize_fuel(m.fuel_type or ""),
        "business_number": m.business_number or "",
        "affiliated_company": m.affiliated_company or "",
        "resident_number": m.resident_number or "",
        "company_name": m.company_name or "",
        "memo": m.memo or "",
        "registration_type": m.registration_type or "",
        "created_at": str(m.created_at)[:10] if m.created_at else "",
    }


def _fmt_detail(m):
    d = _fmt(m)
    d["raw_data"] = _clean_raw(m.raw_data)
    return d


@router.get("/next-new-number")
async def next_new_number(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return {"next_number": crud.get_next_new_member_number(db)}


@router.get("/next-transfer-number")
async def next_transfer_number(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return {"next_number": crud.get_next_transfer_member_number(db)}


def _nat_key(s: str):
    """차량번호 자연 정렬 키: 숫자 부분을 정수로 비교"""
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', s or '')]


@router.get("")
async def list_members(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    registration_type: Optional[str] = Query(None),
    mgmt_prefix: Optional[str] = Query(None),
    status: Optional[str] = Query("active"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    filters = {"region": region, "category": category,
               "membership_status": membership_status, "status": status,
               "registration_type": registration_type,
               "management_number_prefix": mgmt_prefix}
    # 전체 매칭 레코드 (지역+차량번호 자연정렬 필요)
    all_items, _ = crud.get_list(
        db, models.LicenseHolder, skip=0, limit=99999,
        search=search, search_fields=SEARCH, filters=filters,
    )
    # 빈 행 제거
    all_items = [i for i in all_items
                 if (i.vehicle_number and i.vehicle_number.strip()) or (i.name and i.name.strip())]
    # 기본 정렬: 1차 지역(가나다) → 2차 차량번호(자연 정렬)
    all_items.sort(key=lambda m: (m.region or 'zzz', _nat_key(m.vehicle_number or '')))
    total = len(all_items)
    start = (page - 1) * limit
    items = all_items[start:start + limit]
    pages = max(1, (total + limit - 1) // limit)
    return {"items": [_fmt(i) for i in items], "total": total,
            "page": page, "pages": pages, "limit": limit}


@router.get("/export/excel")
async def export_excel(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    mgmt_prefix: Optional[str] = Query(None),
    status: Optional[str] = Query("active"),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    filters = {"region": region, "category": category,
               "membership_status": membership_status, "status": status,
               "management_number_prefix": mgmt_prefix}
    items, _ = crud.get_list(db, models.LicenseHolder, skip=0, limit=9999,
                              search=search, search_fields=SEARCH, filters=filters)
    content = records_to_excel([_fmt(i) for i in items],
                                exclude=["id", "status", "registration_type"])
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=members.xlsx"},
    )


@router.get("/{mid}")
async def get_member(mid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    m = crud.get_by_id(db, models.LicenseHolder, mid)
    if not m:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    return _fmt_detail(m)


@router.post("")
async def create_member(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    data.setdefault("category", crud.detect_category(data.get("vehicle_number", "")))
    data.setdefault("status", "active")
    # 지역 정규화
    if data.get("region"):
        from app.excel_utils import _normalize_region
        data["region"] = _normalize_region(data["region"])
    return _fmt(crud.create_item(db, models.LicenseHolder, data))


_AUTO_CHANGE_FIELDS = {
    "address":           "주소지변경",
    "name":              "성명변경",
    "affiliated_company":"전속계약 업체변경",
    "company_name":      "상호변경",
    "vehicle_number":    "번호변경",
    "region":            "등록이관",
}


@router.put("/{mid}")
async def update_member(mid: int, data: dict, db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    m = crud.get_by_id(db, models.LicenseHolder, mid)
    if not m:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    if data.get("region"):
        from app.excel_utils import _normalize_region
        data["region"] = _normalize_region(data["region"])

    # 변경 전 값 스냅샷
    before_snap = {f: getattr(m, f, "") or "" for f in _AUTO_CHANGE_FIELDS}

    updated = crud.update_item(db, m, data)

    # 변경된 필드 자동 기록
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for field, change_type in _AUTO_CHANGE_FIELDS.items():
        old_val = before_snap[field]
        new_val = data.get(field, old_val)
        if old_val != new_val and new_val:
            ch = models.ChangeHistory(
                change_type=change_type,
                region=updated.region or "",
                vehicle_number=updated.vehicle_number or "",
                name=updated.name or "",
                before_value=old_val,
                after_value=str(new_val),
                change_date=today,
                memo=f"회원정보 수정 자동기록",
                member_id=mid,
            )
            db.add(ch)
    db.commit()

    return _fmt(updated)


@router.delete("/{mid}")
async def delete_member(mid: int, db: Session = Depends(get_db),
                         _=Depends(require_admin)):
    m = crud.get_by_id(db, models.LicenseHolder, mid)
    if not m:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    crud.soft_delete(db, m)
    return {"ok": True}


from pydantic import BaseModel


class CloseBody(BaseModel):
    closure_type: str
    closure_date: str
    management_number: Optional[str] = None
    reason: Optional[str] = ""


@router.post("/{mid}/close")
async def close_member(mid: int, body: CloseBody,
                        db: Session = Depends(get_db), _=Depends(get_current_user)):
    mgmt = body.management_number or crud.get_next_closure_number(db, body.closure_type)
    if crud.check_mgmt_dup(db, models.Closure, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    closure = crud.close_member(db, mid, body.closure_type, body.closure_date,
                                  mgmt, body.reason)
    return {"ok": True, "closure_id": closure.id, "management_number": mgmt}
