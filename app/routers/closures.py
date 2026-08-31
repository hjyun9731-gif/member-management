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

SEARCH = ["name", "vehicle_number", "management_number", "region", "reason", "company_name", "memo"]

# 폐업현황 상세정보 보강 시 회원정보로 채워넣을 필드 목록
# (기존 이전자료 폐업현황에 값이 비어있어도 회원정보에서 조회되게 함)
_FALLBACK_FIELDS = [
    'approval_date', 'certificate_issue_date', 'certificate_number',
    'driver_license_number', 'structure_change', 'vehicle_type', 'fuel_type',
    'membership_date', 'membership_status', 'resident_number', 'phone', 'mobile',
    'address', 'official_address', 'affiliated_company', 'agent_name', 'agent_mobile',
]


def _norm_vn(v) -> str:
    """차량번호 비교용 정규화: 공백 제거 + 끝의 '호' 제거 + 소문자화.
    엑셀 원본 시트마다 '11가 1111' vs '11가1111', '1234호' vs '1234'처럼
    표기가 달라 DB 완전일치(in_)로는 매칭이 누락되는 경우가 있어 정규화 후 비교한다.
    (app/routers/admin.py의 debug-closure-match 진단 도구와 동일한 정규화 규칙)
    """
    import re as _re
    v = str(v or '').strip()
    v = _re.sub(r'\s+', '', v)
    v = _re.sub(r'호$', '', v)
    return v.lower()


def _build_member_lookup(db, closures_list):
    """폐업현황 상세정보 보강용 회원 조회 캐시.
    우선순위: member_id 직접 매칭 > 주민등록번호 완전일치 > 차량번호(정규화 후) 일치.
    전체 회원 테이블을 한 번만 읽어 정규화된 인덱스를 만든다
    (테이블 규모가 작아 요청당 1회 조회로 충분히 빠름).
    """
    ids = {c.member_id for c in closures_list if getattr(c, 'member_id', None)}
    need_lookup = any(
        not getattr(c, 'member_id', None) and
        ((c.vehicle_number or '').strip() or (getattr(c, 'resident_number', '') or '').strip())
        for c in closures_list
    )
    by_id, by_vehicle, by_resident = {}, {}, {}
    if ids:
        for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.id.in_(ids)).all():
            by_id[m.id] = m
    if need_lookup:
        for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all():
            vn = _norm_vn(m.vehicle_number)
            if vn:
                by_vehicle.setdefault(vn, []).append(m)
            rn = (m.resident_number or '').strip()
            if rn:
                by_resident.setdefault(rn, []).append(m)
    return by_id, by_vehicle, by_resident


def _find_linked_member(c, by_id, by_vehicle, by_resident):
    mid = getattr(c, 'member_id', None)
    if mid and mid in by_id:
        return by_id[mid]

    rn = (getattr(c, 'resident_number', '') or '').strip()
    if rn and rn in by_resident:
        candidates = by_resident[rn]
        if len(candidates) == 1:
            return candidates[0]
        name = (c.name or '').strip()
        for m in candidates:
            if name and (m.name or '').strip() == name:
                return m

    vn = _norm_vn(c.vehicle_number)
    if vn and vn in by_vehicle:
        candidates = by_vehicle[vn]
        name = (c.name or '').strip()
        if name:
            for m in candidates:
                if (m.name or '').strip() == name:
                    return m
        if len(candidates) == 1:
            return candidates[0]
    return None




def _norm_name_for_link(v) -> str:
    """신규 폐업현황을 현재 회원과 연결할 때 쓰는 보수적 성명키.
    공백/기호만 제거하고 차량번호와 함께 일치할 때만 자동연결한다.
    """
    import re as _re
    return _re.sub(r"[^0-9A-Za-z가-힣]", "", str(v or "")).lower()


def _safe_find_active_member_for_new_closure(db, data):
    """신규 폐업/양도/이관 입력을 현재 활성회원과 안전하게 연결한다.

    우선순위:
    1) payload member_id
    2) 주민등록번호 정확일치(활성회원 유일)
    3) 차량번호 정규화 + 성명 정규화 정확일치(활성회원 유일)

    차량번호만 같은 경우에는 양도/차주변경 오연결 위험 때문에 자동연결하지 않는다.
    """
    active_q = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        ((models.LicenseHolder.status.is_(None)) | (models.LicenseHolder.status == "active")),
    )

    raw_mid = data.get("member_id")
    if raw_mid not in (None, ""):
        try:
            mid = int(raw_mid)
        except Exception:
            mid = None
        if mid is not None:
            m = active_q.filter(models.LicenseHolder.id == mid).first()
            if m is not None:
                return m, "member_id 정확일치"

    rn = str(data.get("resident_number") or "").strip()
    if rn:
        candidates = active_q.filter(models.LicenseHolder.resident_number == rn).all()
        if len(candidates) == 1:
            return candidates[0], "주민등록번호 정확일치"

    vn = _norm_vn(data.get("vehicle_number"))
    nn = _norm_name_for_link(data.get("name"))
    region = str(data.get("region") or "").strip()
    if vn and nn:
        # DB 표현이 '강원80배 1234호' / '80배1234'처럼 달라질 수 있어
        # 전체 활성회원 중 정규화된 차량번호+성명이 동시에 맞는 경우만 연결한다.
        candidates = []
        for m in active_q.all():
            if _norm_vn(m.vehicle_number) != vn:
                continue
            if _norm_name_for_link(m.name) != nn:
                continue
            if region and (m.region or "").strip() and (m.region or "").strip() != region:
                continue
            candidates.append(m)
        if len(candidates) == 1:
            return candidates[0], "차량번호+성명 정확일치"

    return None, "회원 안전매칭 실패"


def _copy_new_closure_payload_fields(closure, data):
    """close_member_no_commit로 생성된 현재회원 스냅샷에 사용자가 입력한 추가값을 보존."""
    preserve = (
        "data_type", "company_name", "vehicle_type", "fuel_type", "structure_change",
        "phone", "mobile", "address", "official_address", "membership_status",
        "membership_date", "certificate_issue_date", "certificate_number",
        "driver_license_number", "resident_number", "affiliated_company",
        "agent_name", "agent_mobile", "approval_date", "memo",
    )
    for field in preserve:
        if not hasattr(closure, field):
            continue
        value = data.get(field)
        if value not in (None, ""):
            setattr(closure, field, value)


def _fmt(c, member=None):
    ct = c.closure_type or ""
    if ct == '폐지':
        ct = '폐업'
    result = {
        "id": c.id,
        "management_number": c.management_number or "",
        "closure_type": ct,
        "data_type": c.data_type or "신규자료",
        "region": c.region or "",
        "vehicle_number": c.vehicle_number or "",
        "name": c.name or "",
        "company_name": c.company_name or "",
        "vehicle_type": getattr(c, 'vehicle_type', '') or "",
        "fuel_type":    getattr(c, 'fuel_type', '') or "",
        "structure_change": getattr(c, 'structure_change', '') or "",
        "phone":        getattr(c, 'phone', '') or "",
        "mobile":       getattr(c, 'mobile', '') or "",
        "address":      getattr(c, 'address', '') or "",
        "official_address": getattr(c, 'official_address', '') or "",
        "membership_status": getattr(c, 'membership_status', '') or "",
        "membership_date": getattr(c, 'membership_date', '') or "",
        "certificate_issue_date": getattr(c, 'certificate_issue_date', '') or "",
        "certificate_number": getattr(c, 'certificate_number', '') or "",
        "driver_license_number": getattr(c, 'driver_license_number', '') or "",
        "resident_number": getattr(c, 'resident_number', '') or "",
        "affiliated_company": getattr(c, 'affiliated_company', '') or "",
        "agent_name":   getattr(c, 'agent_name', '') or "",
        "agent_mobile": getattr(c, 'agent_mobile', '') or "",
        "closure_date": c.closure_date or "",
        "receipt_date": getattr(c, 'receipt_date', '') or "",       # 접수일자 (공문 접수일)
        "approval_date": c.approval_date or "",
        "reason": c.reason or "",
        "transferee": getattr(c, 'transferee', '') or "",        # 양수인 (양도 시)
        "transfer_region": getattr(c, 'transfer_region', '') or "",  # 이관지역 / 양도지역
        "memo": c.memo or "",
        "member_id": getattr(c, 'member_id', None),
        "raw_data": c.raw_data or {},
        "created_at": str(c.created_at)[:10] if c.created_at else "",
    }
    # 기존/신규 자료 표시 통일: 폐업현황 자체 필드가 비어있으면 연결된 회원정보로 보강
    # (신규 자료는 close_member 처리 시 이미 회원정보가 복사되어 저장되므로 보강이 필요없고,
    #  구자료(이전자료)만 실제로 보강됨 — 저장된 값은 그대로 두고 조회 시에만 채운다)
    if member:
        for f in _FALLBACK_FIELDS:
            if not result.get(f):
                v = getattr(member, f, None)
                if v:
                    result[f] = v
    return result


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
    by_id, by_vehicle, by_resident = _build_member_lookup(db, items)
    return {"items": [_fmt(i, _find_linked_member(i, by_id, by_vehicle, by_resident)) for i in items], "total": total,
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
    by_id, by_vehicle, by_resident = _build_member_lookup(db, items)
    content = records_to_excel(
        [_fmt(i, _find_linked_member(i, by_id, by_vehicle, by_resident)) for i in items], exclude=["id"])
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
    by_id, by_vehicle, by_resident = _build_member_lookup(db, [c])
    return _fmt(c, _find_linked_member(c, by_id, by_vehicle, by_resident))


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

    # 업무관리시스템 > 인허가/변경 > 폐업현황에서 '신규자료'를 등록한 경우,
    # 차량번호+성명(또는 주민번호/member_id)이 현재 활성회원과 안전하게 일치하면
    # 별도의 이중입력 없이 같은 트랜잭션에서 회원을 폐업/양도/이관 처리한다.
    # 이렇게 연결되면 수납·미수금은 기존 잔액/입금이력을 보존한 채 즉시 폐업미수로 이동하고
    # 처리월 이후 자동부과도 중단된다. 이전자료와 애매한 매칭은 절대 자동처리하지 않는다.
    ct = data.get("closure_type") or "폐업"
    data_type = data.get("data_type") or "신규자료"
    close_types = {"폐업", "양도", "이관", "사망", "말소"}
    if data_type != "이전자료" and ct in close_types:
        member, match_reason = _safe_find_active_member_for_new_closure(db, data)
        if member is not None:
            try:
                closure = crud.close_member_no_commit(
                    db, member.id, ct, data.get("closure_date") or "", mgmt or "",
                    data.get("reason") or "",
                    transferee=data.get("transferee") or "",
                    transfer_region=data.get("transfer_region") or "",
                    receipt_date=data.get("receipt_date") or "",
                )
                _copy_new_closure_payload_fields(closure, data)
                db.commit()
                db.refresh(closure)
                result = _fmt(closure, member)
                result["receivables_synced"] = True
                result["member_link_reason"] = match_reason
                return result
            except Exception:
                db.rollback()
                raise

    # 안전하게 현재 회원을 특정할 수 없는 경우 폐업현황 기록 자체는 보존하되,
    # 현재회원/미수금에 억지로 연결하지 않는다.
    created = crud.create_item(db, models.Closure, data)
    result = _fmt(created)
    result["receivables_synced"] = False
    result["member_link_reason"] = "회원 안전매칭 실패 또는 이전자료"
    return result


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
