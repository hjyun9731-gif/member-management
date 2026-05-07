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
from app.excel_utils import records_to_excel, normalize_fuel

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
        "fuel_type": normalize_fuel(m.fuel_type or ""),
        "business_number": m.business_number or "",
        "affiliated_company": m.affiliated_company or "",
        "resident_number": m.resident_number or "",
        "company_name": m.company_name or "",
        "memo": m.memo or "",
        "registration_type": m.registration_type or "",
        "created_at": str(m.created_at)[:10] if m.created_at else "",
        # ── 택배 전용 ──────────────────────────────────
        "reapproval_date": getattr(m, "reapproval_date", None) or "",
        "official_address": getattr(m, "official_address", None) or "",
        # ── 개인 전용 ──────────────────────────────────
        "agent_name": getattr(m, "agent_name", None) or "",
        "agent_resident_number": getattr(m, "agent_resident_number", None) or "",
        "agent_mobile": getattr(m, "agent_mobile", None) or "",
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


@router.get("")
async def list_members(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    registration_type: Optional[str] = Query(None),
    mgmt_prefix: Optional[str] = Query(None),
    status: Optional[str] = Query("active"),
    member_sort: Optional[str] = Query("default"),  # default / approval_desc / approval_asc / join_desc / join_asc
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    filters = {"region": region, "category": category,
               "membership_status": membership_status, "status": status,
               "registration_type": registration_type,
               "management_number_prefix": mgmt_prefix}
    nonempty = ["vehicle_number", "name"]

    # 이전 버전 호환: desc/asc → default (날짜 정렬은 approval_desc/approval_asc 사용)
    if member_sort in ("desc", "asc", None, ""):
        member_sort = "default"

    if member_sort in ("approval_desc", "approval_asc"):
        # 인가일자 기준 날짜 정렬
        sort_dir = "desc" if member_sort == "approval_desc" else "asc"
        items, total = crud.get_sorted_page(
            db, models.LicenseHolder, date_field="approval_date", sort_dir=sort_dir,
            page=page, limit=limit,
            search=search, search_fields=SEARCH, filters=filters,
            nonempty_any=nonempty,
        )
    elif member_sort in ("join_desc", "join_asc"):
        # 가입일자 기준 날짜 정렬
        sort_dir = "desc" if member_sort == "join_desc" else "asc"
        items, total = crud.get_sorted_page(
            db, models.LicenseHolder, date_field="membership_date", sort_dir=sort_dir,
            page=page, limit=limit,
            search=search, search_fields=SEARCH, filters=filters,
            nonempty_any=nonempty,
        )
    elif member_sort in ("mgmt_desc", "mgmt_asc"):
        # 관리번호 기준 자연정렬 (연도+번호 숫자 비교, 개인/택배 구분 없이)
        sort_dir = "desc" if member_sort == "mgmt_desc" else "asc"
        items, total = crud.get_sorted_page_mgmt(
            db, models.LicenseHolder, sort_dir=sort_dir,
            page=page, limit=limit,
            search=search, search_fields=SEARCH, filters=filters,
            nonempty_any=nonempty,
        )
    else:
        # 기본: 지역(가나다) + 차량번호(자연정렬)
        items, total = crud.get_region_vehicle_page(
            db, models.LicenseHolder, page=page, limit=limit,
            search=search, search_fields=SEARCH, filters=filters,
            nonempty_any=nonempty,
        )

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

# 수정 시 저장 허용할 모든 필드 목록 (화이트리스트)
_ALLOWED_UPDATE_FIELDS = {
    "management_number", "region", "vehicle_number", "name", "company_name",
    "address", "phone", "mobile", "membership_status", "membership_date",
    "approval_date", "certificate_issue_date", "certificate_number",
    "driver_license_number", "vehicle_type", "fuel_type", "business_number",
    "affiliated_company", "resident_number", "memo", "category",
    # 택배 전용
    "reapproval_date", "official_address",
    # 개인 전용
    "agent_name", "agent_resident_number", "agent_mobile",
}


@router.put("/{mid}")
async def update_member(mid: int, data: dict, db: Session = Depends(get_db),
                         current_user=Depends(get_current_user)):
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"PUT /api/members/{mid} 요청: {list(data.keys())}")

    m = crud.get_by_id(db, models.LicenseHolder, mid)
    if not m:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    if data.get("region"):
        from app.excel_utils import _normalize_region
        data["region"] = _normalize_region(data["region"])

    # 허용 필드만 필터링
    filtered_data = {k: v for k, v in data.items() if k in _ALLOWED_UPDATE_FIELDS}

    # 가입일자(membership_date) 변경 시 membership_status 재판정
    # 단, membership_status를 직접 명시한 경우는 그 값 우선
    if 'membership_date' in filtered_data and 'membership_status' not in filtered_data:
        from app.excel_utils import normalize_membership_status
        filtered_data['membership_status'] = normalize_membership_status(
            filtered_data.get('membership_date') or ''
        )
    logger.info(f"PUT /api/members/{mid} 저장 필드: {list(filtered_data.keys())}")

    # 새로 추가된 컬럼이 실제 DB에 없을 경우 안전하게 제거
    _new_cols = {"reapproval_date", "official_address", "agent_name",
                 "agent_resident_number", "agent_mobile"}
    for col in list(_new_cols):
        if col in filtered_data and not hasattr(m, col):
            filtered_data.pop(col)
            logger.warning(f"컬럼 {col} 없어서 제거됨")

    # 변경 전 값 스냅샷
    before_snap = {f: getattr(m, f, "") or "" for f in _AUTO_CHANGE_FIELDS}

    # 기본 필드 업데이트
    for k, v in filtered_data.items():
        try:
            setattr(m, k, v)
        except Exception as ex:
            logger.warning(f"setattr {k}={v} 실패: {ex}")

    m.updated_at = datetime.datetime.now(datetime.timezone.utc)

    # 변경된 필드 자동 기록
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for field, change_type in _AUTO_CHANGE_FIELDS.items():
        old_val = before_snap[field]
        new_val = filtered_data.get(field, old_val)
        if old_val != new_val and new_val:
            try:
                ch = models.ChangeHistory(
                    change_type=change_type,
                    region=getattr(m, "region", "") or "",
                    vehicle_number=getattr(m, "vehicle_number", "") or "",
                    name=getattr(m, "name", "") or "",
                    before_value=old_val,
                    after_value=str(new_val),
                    change_date=today,
                    memo="회원정보 수정 자동기록",
                    member_id=mid,
                )
                db.add(ch)
            except Exception as ex:
                logger.warning(f"변경이력 저장 실패: {ex}")

    try:
        db.commit()
        db.refresh(m)
        logger.info(f"PUT /api/members/{mid} 저장 성공")
    except Exception as e:
        db.rollback()
        logger.error(f"PUT /api/members/{mid} DB 저장 실패: {e}")
        raise HTTPException(500, f"DB 저장 오류: {str(e)}")

    return _fmt(m)


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
    from app.excel_utils import normalize_closure_type
    ct = normalize_closure_type(body.closure_type)
    mgmt = body.management_number or crud.get_next_closure_number(db, ct)
    if crud.check_mgmt_dup(db, models.Closure, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    closure = crud.close_member(db, mid, ct, body.closure_date,
                                  mgmt, body.reason)
    return {"ok": True, "closure_id": closure.id, "management_number": mgmt}
