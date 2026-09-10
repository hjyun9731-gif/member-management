from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import io

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, parse_date_sort, normalize_closure_type, is_association_member

router = APIRouter()


SEARCH = ["name", "vehicle_number", "management_number", "region", "reason", "company_name", "memo"]

_CANCEL_TYPE = "폐업취소"


def _is_cancelled_void(c) -> bool:
    return (getattr(c, "closure_type", "") or "").strip() == _CANCEL_TYPE


def _visible_closure_filter():
    """정상 폐업현황과 폐업취소 결번행만 화면/엑셀에 노출. 일반 삭제행은 숨김."""
    from sqlalchemy import or_, and_
    return or_(
        models.Closure.deleted_at.is_(None),
        and_(models.Closure.deleted_at.isnot(None), models.Closure.closure_type == _CANCEL_TYPE),
    )


def _append_memo(existing: str, note: str) -> str:
    base = (existing or "").rstrip()
    return f"{base}\n{note}" if base else note

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
    # 이미 폐업취소 처리되어 본문 필드가 비워진 과거 행은
    # raw_data.cancelled_member_id를 통해 복원된 원회원과 다시 연결해 표시한다.
    for c in closures_list:
        if _is_cancelled_void(c):
            raw = getattr(c, "raw_data", None) or {}
            if isinstance(raw, dict):
                cancelled_member_id = raw.get("cancelled_member_id")
                if cancelled_member_id:
                    try:
                        ids.add(int(cancelled_member_id))
                    except Exception:
                        pass

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

    # 기존 버전에서 폐업취소하며 c.member_id를 비워버린 결번행 호환.
    if _is_cancelled_void(c):
        raw = getattr(c, "raw_data", None) or {}
        if isinstance(raw, dict):
            cancelled_member_id = raw.get("cancelled_member_id")
            try:
                cancelled_member_id = int(cancelled_member_id) if cancelled_member_id else None
            except Exception:
                cancelled_member_id = None
            if cancelled_member_id and cancelled_member_id in by_id:
                return by_id[cancelled_member_id]

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
        "cancelled_void": _is_cancelled_void(c),
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

        # 폐업취소 행은 '누구를 취소했는지' 반드시 보여야 한다.
        # 새 취소 건은 폐업 당시 스냅샷을 그대로 보존하고,
        # 구버전에서 이미 비워진 취소행만 복원된 원회원 정보로 빈 값을 보강한다.
        if _is_cancelled_void(c):
            for f in ("region", "vehicle_number", "name", "company_name"):
                if not result.get(f):
                    v = getattr(member, f, None)
                    if v:
                        result[f] = v

    # 구버전에서 폐업취소 처리 시 closure_date까지 비워진 행은
    # raw_data.cancelled_at의 날짜를 처리일자로 표시한다.
    if _is_cancelled_void(c) and not (result.get("closure_date") or "").strip():
        raw = result.get("raw_data") or {}
        if isinstance(raw, dict):
            cancelled_at = str(raw.get("cancelled_at") or "")
            if cancelled_at:
                result["closure_date"] = cancelled_at[:10]

    # 구버전 취소행은 원래 폐업사유가 이미 지워졌으므로 빈칸 대신 상태를 명확히 표시.
    if _is_cancelled_void(c) and not (result.get("reason") or "").strip():
        result["reason"] = "폐업취소"

    # 폐업현황 목록 표시용 읽기 전용 보강값.
    # 기존 회원 관리번호는 폐업관리번호와 별개이므로 연결된 회원마스터에서만 가져온다.
    result["previous_management_number"] = (getattr(member, "management_number", "") or "") if member else ""

    # 가입여부는 "가입일자 칸이 비어있지 않다"로 판정하면 안 된다.
    # 원본에는 x / 개별에 등록 / 개별에서 대폐차 / 대폐차&등록 같은
    # 업무메모가 membership_date 칸에 들어간 과거 데이터가 실제로 존재한다.
    # 공통 판정 함수는 실제 날짜(또는 O/ㅇ/○)만 가입으로 인정하고, 그 외 텍스트는 미가입으로 본다.
    mem_date = (result.get("membership_date") or "").strip()
    result["membership_status"] = "가입" if is_association_member(mem_date) else "미가입"
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
    base_q = db.query(models.Closure).filter(_visible_closure_filter())
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
        models.Closure.closure_type == _CANCEL_TYPE,
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
        all_items_raw = base_q.with_entities(
            models.Closure.id,
            models.Closure.closure_date,
            models.Closure.closure_type,
            models.Closure.raw_data,
        ).all()
        reverse = date_order_v == "desc"

        def _closure_sort_date(row):
            date_v = row[1] or ""
            if (row[2] or "").strip() == _CANCEL_TYPE and not str(date_v).strip():
                raw = row[3] or {}
                if isinstance(raw, dict):
                    cancelled_at = str(raw.get("cancelled_at") or "")
                    if cancelled_at:
                        date_v = cancelled_at[:10]
            return parse_date_sort(date_v or "")

        all_items_raw.sort(key=_closure_sort_date, reverse=reverse)
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
    from sqlalchemy import or_
    q = db.query(models.Closure).filter(_visible_closure_filter())
    if region:
        q = q.filter(models.Closure.region == region)
    if closure_type:
        if closure_type == '폐업':
            q = q.filter(or_(models.Closure.closure_type == '폐업', models.Closure.closure_type == '폐지'))
        else:
            q = q.filter(models.Closure.closure_type == closure_type)
    if data_type:
        q = q.filter(models.Closure.data_type == data_type)
    items = q.order_by(models.Closure.id.asc()).all()
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


@router.post("/{cid}/cancel")
async def cancel_closure(cid: int, db: Session = Depends(get_db),
                         _=Depends(require_admin)):
    """잘못 처리한 '폐업'을 취소하고 원래 회원을 복원한다.

    - 원회원은 기존 관리번호/정보 그대로 active 상태로 복원
    - 사용된 폐업관리번호는 결번으로 영구 보존
    - 폐업현황의 취소행에는 누가 취소되었는지 확인할 수 있도록
      폐업 당시 인적/차량정보를 삭제하지 않고 그대로 보존
    - 집계에서는 제외하기 위해 deleted_at은 유지
    """
    c = db.query(models.Closure).filter(models.Closure.id == cid).first()
    if not c:
        raise HTTPException(404, "폐업 기록을 찾을 수 없습니다.")
    if _is_cancelled_void(c):
        raise HTTPException(400, "이미 폐업취소 처리된 관리번호입니다.")

    ct = (c.closure_type or "").strip()
    if ct not in ("폐업", "폐지"):
        raise HTTPException(400, "폐업취소는 폐업 건에만 사용할 수 있습니다. 양도/이관은 별도 정정이 필요합니다.")

    member = None
    if getattr(c, "member_id", None):
        member = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.id == c.member_id,
            models.LicenseHolder.deleted_at.is_(None),
        ).first()

    # 구자료 호환: 직접 member_id가 없을 때 저장된 원래 관리번호가
    # 단 하나의 회원과 정확히 일치할 때만 자동 복원한다.
    if member is None:
        original_mgmt = (getattr(c, "original_management_number", "") or "").strip()
        if original_mgmt:
            matches = db.query(models.LicenseHolder).filter(
                models.LicenseHolder.management_number == original_mgmt,
                models.LicenseHolder.deleted_at.is_(None),
            ).all()
            if len(matches) == 1:
                member = matches[0]
            elif len(matches) > 1:
                raise HTTPException(400, "원래 관리번호에 연결된 회원이 2명 이상이라 자동 복원할 수 없습니다.")

    if member is None:
        raise HTTPException(400, "원래 회원과 직접 연결된 폐업건이 아니어서 자동 복원할 수 없습니다.")

    current_closure_id = getattr(member, "closure_id", None)
    if current_closure_id not in (None, c.id):
        raise HTTPException(400, "회원이 다른 폐업기록과 연결되어 있어 자동 복원을 중단했습니다.")
    if (member.status or "active") == "active" and current_closure_id is None:
        raise HTTPException(400, "이미 활성 회원 상태입니다. 중복 폐업취소를 중단했습니다.")

    void_no = (c.management_number or "").strip()
    restored_mgmt = (member.management_number or "").strip()
    now_kr = datetime.now(ZoneInfo("Asia/Seoul"))
    cancel_date = now_kr.strftime("%Y-%m-%d")
    note = (
        f"폐업취소({cancel_date}): 잘못 처리된 폐업을 취소하여 기존 회원정보로 복원함. "
        f"폐업관리번호 {void_no or '(번호없음)'}는 결번 처리하며 재사용하지 않음."
    )

    try:
        # 회원은 기존 행 그대로 복원한다. 기존 회원 관리번호도 그대로 유지.
        member.status = "active"
        member.closure_id = None
        member.memo = _append_memo(getattr(member, "memo", "") or "", note)

        # 폐업행은 '폐업취소' 이력으로 남긴다.
        # 누가 취소되었는지 확인해야 하므로 이름/차량/지역/사유 등 기존 스냅샷은 지우지 않는다.
        audit_raw = dict(c.raw_data or {}) if isinstance(c.raw_data, dict) else {}
        audit_raw.update({
            "cancelled_at": now_kr.isoformat(timespec="seconds"),
            "cancelled_member_id": member.id,
            "restored_management_number": restored_mgmt,
            "voided_closure_management_number": void_no,
        })

        c.closure_type = _CANCEL_TYPE
        c.member_id = member.id  # 감사용 원회원 연결은 유지
        c.memo = _append_memo(c.memo or "", note)
        c.raw_data = audit_raw

        if hasattr(c, "original_mgmt_match_status"):
            c.original_mgmt_match_status = "cancelled"

        # 기존 통계/집계에서 폐업 건으로 잡히지 않게 제외하되,
        # _visible_closure_filter()가 폐업취소 행은 화면에 계속 보여준다.
        c.deleted_at = datetime.now(timezone.utc)

        db.commit()
        return {
            "ok": True,
            "restored_member_id": member.id,
            "restored_management_number": restored_mgmt,
            "voided_closure_management_number": void_no,
            "memo": note,
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"폐업취소 처리 중 오류가 발생했습니다: {exc}")


@router.delete("/{cid}")
async def delete_closure(cid: int, db: Session = Depends(get_db),
                          _=Depends(require_admin)):
    c = crud.get_by_id(db, models.Closure, cid)
    if not c:
        raise HTTPException(404)
    crud.soft_delete(db, c)
    return {"ok": True}
