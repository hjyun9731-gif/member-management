from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import Optional
import io
import re
import datetime

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, normalize_fuel, normalize_membership_status
from app.services.certificate_ledger_service import ensure_member_ledger

router = APIRouter()

SEARCH = ["name", "vehicle_number", "phone", "mobile", "management_number",
          "certificate_number", "address", "affiliated_company", "resident_number"]


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
        "membership_status": normalize_membership_status(m.membership_date or ""),
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
        "status": m.status or "active",
        "created_at": str(m.created_at)[:10] if m.created_at else "",
        # ── 택배 전용 ──────────────────────────────────
        "reapproval_date": getattr(m, "reapproval_date", None) or "",
        "official_address": getattr(m, "official_address", None) or "",
        # ── 개인 전용 ──────────────────────────────────
        "agent_name": getattr(m, "agent_name", None) or "",
        "agent_resident_number": getattr(m, "agent_resident_number", None) or "",
        "agent_mobile": getattr(m, "agent_mobile", None) or "",
        "agent_address": getattr(m, "agent_address", None) or "",
        "structure_change": getattr(m, "structure_change", None) or "",
        "pinned": bool(getattr(m, "pinned", False)),
    }


def _fmt_detail(m, transfer=None, transfer_out=None):
    d = _fmt(m)
    d["raw_data"] = _clean_raw(m.raw_data)
    # 양수 정보: 이 회원이 누구에게서 양수받았는지 (transfer_ledger와 연결된 경우)
    if transfer:
        d["transfer_info"] = {
            "id":                    transfer.id,
            "management_number":     transfer.management_number or "",
            "transferor":            transfer.transferor or "",       # 양도인
            "transferee":            transfer.transferee or "",       # 양수자
            "receipt_date":          transfer.receipt_date or "",     # 접수일자
            "approval_date":         transfer.approval_date or "",    # 인가일자
            "membership_date":       transfer.membership_date or "",  # 가입일자
            "certificate_issue_date": transfer.certificate_issue_date or "",
            "certificate_number":    transfer.certificate_number or "",
            "region":                transfer.region or "",
            "vehicle_number":        transfer.vehicle_number or "",
            "address":               transfer.address or "",
            "phone":                 transfer.phone or "",
            "mobile":                transfer.mobile or "",
            "memo":                  transfer.memo or "",
            "transferor_member_id":  getattr(transfer, 'transferor_member_id', None),
        }
    else:
        d["transfer_info"] = None
    # 양도 정보: 이 회원이 누구에게 양도했는지 (도내 양도양수로 폐업 처리된 경우)
    if transfer_out:
        d["transfer_out_info"] = {
            "id":                    transfer_out.id,
            "management_number":     transfer_out.management_number or "",
            "transferee":            transfer_out.transferee or "",
            "receipt_date":          transfer_out.receipt_date or "",
            "approval_date":         transfer_out.approval_date or "",
            "memo":                  transfer_out.memo or "",
            "transferee_member_id":  getattr(transfer_out, 'transferee_member_id', None),
        }
    else:
        d["transfer_out_info"] = None
    return d


@router.get("/next-new-number")
async def next_new_number(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return {"next_number": crud.get_next_new_member_number(db)}


@router.get("/next-transfer-number")
async def next_transfer_number(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return {"next_number": crud.get_next_transfer_member_number(db)}


@router.get("/last-management-number")
async def last_management_number(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """현재까지 마지막으로 발급된 관리번호 확인 (신규/양도양수 각각)."""
    return {
        "new": crud.get_last_issued_management_number(db, "new"),
        "transfer": crud.get_last_issued_management_number(db, "transfer"),
    }


@router.post("/issue-management-number")
async def issue_management_number(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    """회원정보 입력 없이 관리번호만 먼저 발급.
    body: {"type": "new"|"transfer", "category": "개인"|"택배" (선택)}
    이름/차량번호가 빈 placeholder 회원(status=pending)을 생성해 번호를 예약하고,
    그 레코드의 id를 반환한다. 이후 이 id로 회원 수정(PUT)을 하면 회원정보가
    해당 관리번호에 연결된다 (수정 시 자동으로 status가 active로 전환됨).
    """
    mgmt_type = data.get("type")
    if mgmt_type not in ("new", "transfer"):
        raise HTTPException(400, "type은 'new' 또는 'transfer'여야 합니다.")
    category = data.get("category") or None
    if category not in (None, "개인", "택배"):
        raise HTTPException(400, "category는 '개인' 또는 '택배'여야 합니다.")
    m = crud.issue_management_number_only(db, mgmt_type, category)
    return {"id": m.id, "management_number": m.management_number, "status": m.status}


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
    # status=all이면 status 필터 제거 (신규등록대장: 폐업자도 포함)
    if status == "all":
        status = None
    filters = {"region": region, "category": category,
               "membership_status": membership_status, "status": status,
               "registration_type": registration_type,
               "management_number_prefix": mgmt_prefix}
    nonempty = [] if status == "pending" else ["vehicle_number", "name"]

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
    # 양수 정보 조회 (이 회원이 양수자인 경우): transfer_ledger_id 우선, 없으면 member_id/transferee_member_id로 역조회
    # 주의: 동일 회원이 같은 거래의 양도자이기도 한 경우(데이터 오류 방지용 self-guard)는 양수 정보로 취급하지 않음
    transfer = None
    if m.transfer_ledger_id:
        cand = crud.get_by_id(db, models.TransferLedger, m.transfer_ledger_id)
        if cand and cand.transferor_member_id != mid:
            transfer = cand
    if not transfer:
        transfer = db.query(models.TransferLedger).filter(
            models.TransferLedger.deleted_at.is_(None),
            models.TransferLedger.transferor_member_id != mid,
            or_(
                models.TransferLedger.transferee_member_id == mid,
                # 하위호환: transferee_member_id가 없는 구자료는 레거시 member_id로만 판정
                and_(models.TransferLedger.transferee_member_id.is_(None),
                     models.TransferLedger.member_id == mid),
            ),
        ).first()
    # 양도 정보 조회 (이 회원이 양도자인 경우 - 도내 양도양수로 폐업된 경우)
    # self-guard: 동일 회원이 양수자로도 잡혀있는 레코드는 양도 정보로 취급하지 않음
    transfer_out = db.query(models.TransferLedger).filter(
        models.TransferLedger.transferor_member_id == mid,
        or_(models.TransferLedger.transferee_member_id.is_(None),
            models.TransferLedger.transferee_member_id != mid),
        models.TransferLedger.deleted_at.is_(None),
    ).first()
    result = _fmt_detail(m, transfer, transfer_out)
    # 관리번호가 '양YY-N' 형식인데 양도양수대장에 동일 관리번호 기록이 없는 경우 감지
    # (예정자→회원 등록 후 나중에 관리번호만 양YY-N으로 바뀌어 대장 기록이 누락된 경우)
    result["transfer_ledger_missing"] = False
    mgmt = (m.management_number or "").strip()
    if mgmt and re.match(r'^양\s*\d{2}\s*-', mgmt):
        ledger_exists = db.query(models.TransferLedger).filter(
            models.TransferLedger.deleted_at.is_(None),
            models.TransferLedger.management_number == mgmt,
        ).first()
        if not ledger_exists:
            result["transfer_ledger_missing"] = True
    return result


@router.post("")
async def create_member(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    data.setdefault("category", crud.detect_category(data.get("vehicle_number", "")))
    data.setdefault("status", "active")
    # 지역 정규화
    if data.get("region"):
        from app.excel_utils import _normalize_region
        data["region"] = _normalize_region(data["region"])
    mgmt = (data.get("management_number") or "").strip()
    if mgmt and crud.check_mgmt_dup(db, models.LicenseHolder, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    return _fmt(crud.create_item(db, models.LicenseHolder, data))


_AUTO_CHANGE_FIELDS = {
    "address":            "주소지변경",
    "official_address":   "주소지변경",
    "company_name":       "상호변경",
    "affiliated_company": "전속계약 업체변경",
    "vehicle_number":     "차량번호변경",
    "mobile":             "연락처변경",
    "phone":              "연락처변경",
    # 아래는 변경등록대장 기록 안 함 (내부 수정로그만)
    # "vehicle_type", "fuel_type" → 차종 변경은 UI에서 선택
    # "memo", "name", "region" → 내부관리
}

# 내부 수정로그만 남기는 필드 (변경등록대장 기록 안 함)
_INTERNAL_LOG_ONLY_FIELDS = {
    "memo", "name", "region", "membership_status", "membership_date",
    "approval_date", "certificate_issue_date", "certificate_number",
    "driver_license_number", "resident_number", "business_number",
    "reapproval_date", "agent_name", "agent_resident_number", "agent_mobile",
    "agent_address",
    "category",
}

# 차종/구조 변경은 별도 처리 (auto_change_type 파라미터로 수신)
_STRUCT_FIELDS = {"vehicle_type", "fuel_type", "structure_change"}

def _normalize_for_compare(field: str, v: str) -> str:
    """공백/하이픈/호 정규화 - 형식만 다른 경우 변경 안 함"""
    import re
    v = str(v or "").strip()
    if field in ("mobile", "phone"):
        return re.sub(r"[-\s]", "", v)  # 010-1234-5678 == 01012345678
    if field == "vehicle_number":
        return re.sub(r"[\s호]", "", v)  # 강원81자 1234호 == 강원81자1234
    return v

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
    "agent_name", "agent_resident_number", "agent_mobile", "agent_address",
    "structure_change",
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

    new_mgmt = data.get("management_number")
    if new_mgmt is not None and str(new_mgmt).strip() and str(new_mgmt).strip() != (m.management_number or ""):
        if crud.check_mgmt_dup(db, models.LicenseHolder, str(new_mgmt).strip(), exclude_id=mid):
            raise HTTPException(400, f"관리번호 {new_mgmt}가 이미 존재합니다.")

    old_cert_before = (m.certificate_number or "").strip()
    new_cert = data.get("certificate_number")
    new_cert_clean = ""
    if new_cert is not None:
        raw_cert = str(new_cert).strip()
        normalized_cert = crud.normalize_certificate_number(raw_cert) if raw_cert else ""
        # 번호처럼 인식되는 값은 DB에도 표준형(YY-N)으로 저장한다.
        # 메모성 과거값은 기존 호환성을 위해 원문을 유지한다.
        if normalized_cert:
            data["certificate_number"] = normalized_cert
            new_cert_clean = normalized_cert
        else:
            new_cert_clean = raw_cert
    if new_cert is not None and new_cert_clean and new_cert_clean != (crud.normalize_certificate_number(old_cert_before) or old_cert_before):
        usage = crud._scan_certificate_number_usage(db, new_cert_clean)
        if usage:
            tname, lid, uname, uvehicle = usage
            same_target = (tname == "license_holders" and lid == mid)
            if not same_target:
                raise HTTPException(
                    400,
                    f"자격증명발급번호 {new_cert_clean}는 이미 {tname}에서 사용 중입니다"
                    f"(대상: {uname or lid}). 다른 번호를 입력하거나 기존 자료를 먼저 확인하세요."
                )

    # 허용 필드만 필터링
    filtered_data = {k: v for k, v in data.items() if k in _ALLOWED_UPDATE_FIELDS}

    # 가입상태의 유일한 기준은 가입일자. 화면이 membership_status를 함께 보내더라도
    # "가입일자 있음 + 미가입" 모순값이 저장되지 않도록 가입일자를 항상 우선한다.
    if 'membership_date' in filtered_data:
        filtered_data['membership_status'] = normalize_membership_status(
            filtered_data.get('membership_date') or ''
        )
    logger.info(f"PUT /api/members/{mid} 저장 필드: {list(filtered_data.keys())}")

    # 새로 추가된 컬럼이 실제 DB에 없을 경우 안전하게 제거
    _new_cols = {"reapproval_date", "official_address", "agent_name",
                 "agent_resident_number", "agent_mobile", "agent_address", "structure_change"}
    for col in list(_new_cols):
        if col in filtered_data and not hasattr(m, col):
            filtered_data.pop(col)
            logger.warning(f"컬럼 {col} 없어서 제거됨")

    # 변경 전 값 스냅샷 (자동기록 + 내부기록 대상 모두)
    all_track_fields = set(_AUTO_CHANGE_FIELDS) | _INTERNAL_LOG_ONLY_FIELDS | _STRUCT_FIELDS
    before_snap = {f: getattr(m, f, "") or "" for f in all_track_fields}

    # 기본 필드 업데이트
    for k, v in filtered_data.items():
        try:
            setattr(m, k, v)
        except Exception as ex:
            logger.warning(f"setattr {k}={v} 실패: {ex}")

    m.updated_at = datetime.datetime.now(datetime.timezone.utc)

    # 변경된 필드 분류
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    changes_by_type: dict = {}
    internal_logs = []

    for field in all_track_fields:
        old_val = (before_snap.get(field) or "").strip()
        new_val = str(filtered_data.get(field, old_val) or "").strip()

        # 정규화 비교: 형식만 다른 경우 무시
        if _normalize_for_compare(field, old_val) == _normalize_for_compare(field, new_val):
            continue
        if not new_val:
            continue

        # 원본이 미기재(공백)였다가 이번에 값이 채워지는 경우 - 변경등록대장에는 기록하지 않음
        # (실제 값이 바뀐 '변경'이 아니라 누락된 정보를 처음 채우는 것이므로).
        # 단, 내부 수정로그(MemberEditLog)에는 그대로 남긴다.
        is_blank_fill = not old_val

        # 내부 수정로그 항상 기록
        internal_logs.append((field, old_val, new_val, is_blank_fill))

        if field in _STRUCT_FIELDS:
            # 차종/구조 변경: auto_change_type 파라미터로만 기록
            auto_ct = data.get("auto_change_type")  # 'structureChange'/'vehicleTypeCorrection'/'vehicleReplacement'
            if auto_ct and not is_blank_fill:
                ct_map = {"structureChange": "구조변경", "vehicleTypeCorrection": "차종정정",
                          "vehicleReplacement": "대폐차"}
                change_type = ct_map.get(auto_ct, "구조변경")
                if change_type not in changes_by_type:
                    changes_by_type[change_type] = []
                changes_by_type[change_type].append((field, old_val, new_val))
            # auto_change_type 없거나 미기재→채움인 경우 변경등록대장 기록 안 함

        elif field in _INTERNAL_LOG_ONLY_FIELDS:
            pass  # 내부 수정로그만

        elif field in _AUTO_CHANGE_FIELDS:
            if not is_blank_fill:
                change_type = _AUTO_CHANGE_FIELDS[field]
                if change_type not in changes_by_type:
                    changes_by_type[change_type] = []
                changes_by_type[change_type].append((field, old_val, new_val))
            # else: 미기재→채움은 내부 수정로그만 남기고 변경등록대장 기록 제외

    # 내부 수정로그 저장
    for field, old_val, new_val, is_blank_fill in internal_logs:
        recorded = (field in _AUTO_CHANGE_FIELDS or (
            field in _STRUCT_FIELDS and bool(data.get("auto_change_type")))) and not is_blank_fill
        try:
            log = models.MemberEditLog(
                member_id=mid, vehicle_number=getattr(m, "vehicle_number", "") or "",
                name=getattr(m, "name", "") or "", field_name=field,
                old_value=old_val, new_value=new_val,
                record_to_change_history=recorded,
                change_type=_AUTO_CHANGE_FIELDS.get(field, ""),
                created_by=getattr(current_user, "username", ""),
            )
            db.add(log)
        except Exception as ex:
            logger.warning(f"내부 수정로그 저장 실패: {ex}")

    # 변경등록대장 자동기록
    for change_type, field_changes in changes_by_type.items():
        try:
            if len(field_changes) == 1:
                _, old_val, new_val = field_changes[0]
                bv, av = old_val or "", new_val
            else:
                bv = " / ".join(ov for _, ov, _ in field_changes if ov)
                av = " / ".join(nv for _, _, nv in field_changes)
            ch = models.ChangeHistory(
                change_type=change_type,
                region=getattr(m, "region", "") or "",
                vehicle_number=getattr(m, "vehicle_number", "") or "",
                name=getattr(m, "name", "") or "",
                before_value=bv, after_value=av,
                change_date=today,
                memo="회원정보 수정 자동기록",
                member_id=mid,
                raw_data={"source": "member_auto_log"},
            )
            db.add(ch)
        except Exception as ex:
            logger.warning(f"변경이력 저장 실패: {ex}")

    # transfer_ledger 동기화 (양도양수에서 온 회원인 경우)
    _TRANSFER_SYNC_FIELDS = {
        "region", "vehicle_number", "name", "address", "phone", "mobile",
        "approval_date", "membership_date", "certificate_issue_date",
        "certificate_number", "driver_license_number", "vehicle_type",
        "fuel_type", "affiliated_company", "memo",
    }
    if m.transfer_ledger_id:
        try:
            tl = db.query(models.TransferLedger).filter(
                models.TransferLedger.id == m.transfer_ledger_id
            ).first()
            if tl:
                # transferor (양도인): transfer_data에서 직접 전달한 경우
                if "transferor" in data:
                    tl.transferor = data["transferor"]
                # receipt_date: transfer_data에서 직접 전달한 경우
                if "receipt_date" in data:
                    tl.receipt_date = data["receipt_date"]
                # 공통 필드 동기화
                for tf in _TRANSFER_SYNC_FIELDS:
                    if tf in filtered_data:
                        setattr(tl, tf, filtered_data[tf])
                logger.info(f"transfer_ledger {tl.id} 동기화 완료")
        except Exception as ex:
            logger.warning(f"transfer_ledger 동기화 실패: {ex}")

    # 관리번호만 먼저 발급된 placeholder(status='pending')였다가 이번 저장에서
    # 차량번호/성명이 채워지면 정식 회원으로 전환
    if getattr(m, "status", None) == "pending":
        final_vehicle = filtered_data.get("vehicle_number", m.vehicle_number) or ""
        final_name = filtered_data.get("name", m.name) or ""
        if str(final_vehicle).strip() and str(final_name).strip():
            m.status = "active"

    try:
        db.commit()
        db.refresh(m)
        logger.info(f"PUT /api/members/{mid} 저장 성공")
    except Exception as e:
        db.rollback()
        logger.error(f"PUT /api/members/{mid} DB 저장 실패: {e}")
        raise HTTPException(500, f"DB 저장 오류: {str(e)}")

    # 자격증명발급번호가 이번 저장에서 채워지거나 바뀐 경우, 발급이력(certificate_number_logs)에도
    # 실제 대상자로 연결 - 이전에는 이 경로(회원 직접수정)로 입력하면 발급이력이 갱신되지 않아
    # 발급이력 화면에는 계속 대상자 미연결(발급/미사용)로 남는 문제가 있었음.
    if "certificate_number" in filtered_data:
        cert_val = (m.certificate_number or "").strip()
        if cert_val and crud._is_valid_certificate_number_format(cert_val):
            try:
                crud.resync_certificate_number_change(
                    db, old_cert_before, cert_val, "license_holders", m.id,
                    m.name or "", m.vehicle_number or ""
                )
                # 과거 인가가 먼저 끝난 개인/택배회원도 회원 수정에서 번호를 넣는 즉시
                # 자격증명발급대장에 생성/연결한다. (박영민 26-370 같은 케이스)
                ensure_member_ledger(db, m, current_user)
            except Exception as ex:
                logger.warning(f"자격증명번호/발급대장 재동기화 실패: {ex}")

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
    transferee: Optional[str] = ""
    transfer_region: Optional[str] = ""
    receipt_date: Optional[str] = ""     # 접수일자 (공문 접수일)


@router.post("/{mid}/close")
async def close_member(mid: int, body: CloseBody,
                        db: Session = Depends(get_db), _=Depends(get_current_user)):
    from app.excel_utils import normalize_closure_type
    ct = normalize_closure_type(body.closure_type)
    mgmt = body.management_number or crud.get_next_closure_number(db, ct)
    if crud.check_mgmt_dup(db, models.Closure, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    closure = crud.close_member(db, mid, ct, body.closure_date, mgmt, body.reason,
                                 transferee=body.transferee,
                                 transfer_region=body.transfer_region,
                                 receipt_date=body.receipt_date)
    return {"ok": True, "closure_id": closure.id, "management_number": mgmt}


@router.post("/{mid}/create-missing-transfer-ledger")
async def create_missing_transfer_ledger(mid: int, db: Session = Depends(get_db),
                                          _=Depends(get_current_user)):
    """관리번호가 양YY-N인데 양도양수대장에 동일 관리번호 기록이 없는 경우,
    현재 회원 정보를 양수자로 하여 누락된 대장 기록을 생성한다.
    (실제 생성/중복방지 로직은 crud.create_missing_transfer_ledger_for_member 공용 함수 사용)
    """
    m = crud.get_by_id(db, models.LicenseHolder, mid)
    if not m:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")

    try:
        ledger, created = crud.create_missing_transfer_ledger_for_member(db, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"양도양수대장 생성 실패: {e}")

    return {"ok": True, "created": created, "transfer_ledger_id": ledger.id,
            "message": "양도양수대장 기록을 생성했습니다." if created else "이미 동일 관리번호의 양도양수대장 기록이 존재합니다."}


@router.patch("/{mid}/pin")
async def toggle_member_pin(mid: int, body: dict,
                             db: Session = Depends(get_db), _=Depends(get_current_user)):
    """목록 고정(핀) 토글 - 비고(memo)와 무관한 별도 표시용 플래그."""
    m = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == mid).first()
    if not m:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    m.pinned = bool(body.get("pinned"))
    db.commit()
    return {"ok": True, "id": m.id, "pinned": m.pinned}
