"""문자발송 관리 API.

핵심 원칙 (요청사항 그대로):
- 별도의 회원 명단을 복제해서 관리하지 않는다. 대상자는 매 요청마다
  license_holders(전체면허자현황)를 실시간으로 조회해서 추출한다.
- 문자 발송 이력 / 예약정보 / 템플릿만 별도 테이블(sms_jobs / sms_recipients /
  sms_templates)로 관리한다. SmsRecipient는 "그 순간 무엇을 보냈는지"에 대한
  발송 기록이지, 회원 명단의 대체본이 아니다.
- React 등 브라우저에서 발송닷컴 API를 직접 호출하지 않는다. 반드시
  프론트 → 이 라우터(FastAPI) → balsong_client.py → 발송닷컴 순서로만 통신한다.
- 수신자가 여러 명이어도 balsong_client.send_message() 한 번(Destination 배열)으로
  묶어서 보낸다. 수신자별로 반복 호출하지 않는다 (문서의 호출 제한 규정).
"""
import re
import io
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import and_
from openpyxl import Workbook

from app.database import get_db, SessionLocal
from app.auth import get_current_user, require_admin
from app import models
from app.excel_utils import normalize_fuel
from app.routers.dashboard import calc_age_from_resident, classify_vt, classify_fuel
from app.services.balsong_client import balsong

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sms", tags=["문자발송"])

# 템플릿 변수 -> LicenseHolder 필드 매핑 (확장 가능한 구조 - 필요 시 여기에만 추가하면 됨)
VARIABLE_FIELD_MAP = {
    "성명": "name",
    "차량번호": "vehicle_number",
    "지역": "region",
    "차종": "vehicle_type",
    "유종": "fuel_type",
    "회원구분": "category",
    "관리번호": "management_number",
    "소속업체": "affiliated_company",
    "가입여부": "membership_status",
}

# 문자발송 화면 전용 분류 (기존 회원등록/엑셀에서 쓰는 vehicle_type/fuel_type 원본값과는 별개.
# dashboard.py의 classify_vt/classify_fuel(이미 검증된 분류 로직)을 그대로 재사용해서
# 사용자가 요청한 8개/6개 버킷으로 다시 묶는다.)
SMS_VEHICLE_CATS = ["일반카고", "내장탑차", "냉동·냉장차", "윙바디", "밴형", "픽업형", "특장차", "기타"]
SMS_FUEL_CATS = ["경유", "휘발유", "LPG", "전기", "수소", "기타"]

_VT_TO_SMS_CAT = {
    "카고": "일반카고",
    "탑차/내장탑": "내장탑차",
    "냉동탑/냉장탑": "냉동·냉장차",
    "윙바디": "윙바디",
    "밴/특수밴": "밴형",
    "픽업/덮개": "픽업형",
    "사다리/고소": "특장차",
    "렉카/구난": "특장차",
    "기타특수": "특장차",
    "미분류": "기타",
}

_HYDROGEN_KW = ["수소", "hydrogen", "fcev", "수소전기"]


def sms_classify_vehicle(vehicle_type: str, fuel_type: str = "") -> str:
    return _VT_TO_SMS_CAT.get(classify_vt(vehicle_type, fuel_type), "기타")


def sms_classify_fuel(fuel_type: str, vehicle_type: str = "") -> str:
    text = f"{fuel_type or ''} {vehicle_type or ''}".lower()
    if any(k in text for k in _HYDROGEN_KW):
        return "수소"
    result = classify_fuel(fuel_type, vehicle_type)
    if result in ("경유", "휘발유", "LPG", "전기"):
        return result
    return "기타"  # 하이브리드/CNG/미분류 등은 사용자가 요청한 6개 버킷 밖이라 기타로 묶음


_PHONE_RE = re.compile(r"^01[016789]\d{7,8}$")


def _valid_phone(mobile: str) -> bool:
    if not mobile:
        return False
    digits = re.sub(r"\D", "", mobile)
    return bool(_PHONE_RE.match(digits))


def _clean_phone(mobile: str) -> str:
    return re.sub(r"\D", "", mobile or "")


# ────────────────────────────────────────────────────────────
# 1. 대상자 실시간 조회 (license_holders 원본, 별도 명단 없음)
# ────────────────────────────────────────────────────────────

def _csv_list(v: Optional[str]) -> List[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _build_target_query(db: Session, *, region=None, category=None,
                         membership_status=None, search=None):
    """region/category/membership_status는 license_holders의 실제 컬럼값이라 SQL IN으로
    거른다. vehicle_type(차량형태)/fuel_type(유종)은 분류(가공)된 값이라 SQL로 거를 수
    없어서, 여기서는 폐업 제외 등 공통 조건만 걸고 나머지는 파이썬에서 분류 후 거른다."""
    q = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status != "closed",  # 폐업자 제외
    )
    if region:
        q = q.filter(models.LicenseHolder.region.in_(region))
    if category:
        q = q.filter(models.LicenseHolder.category.in_(category))
    if membership_status:
        q = q.filter(models.LicenseHolder.membership_status.in_(membership_status))
    if search:
        like = f"%{search}%"
        q = q.filter((models.LicenseHolder.name.ilike(like)) |
                      (models.LicenseHolder.vehicle_number.ilike(like)))
    return q


def _apply_all_filters(rows, *, vehicle_type_cats=None, fuel_type_cats=None,
                        age_min=None, age_max=None, valid_phone_only=True):
    """차량형태/유종(분류값), 연령, 유효 전화번호 - SQL로 거르지 못하는 조건들을
    여기서 한 번에 적용한다. 같은 항목 내 여러 값은 OR, 서로 다른 항목끼리는 AND."""
    vt_set = set(vehicle_type_cats or [])
    fuel_set = set(fuel_type_cats or [])
    result = []
    for m in rows:
        if valid_phone_only and not _valid_phone(m.mobile):
            continue
        if vt_set and sms_classify_vehicle(m.vehicle_type, m.fuel_type) not in vt_set:
            continue
        if fuel_set and sms_classify_fuel(m.fuel_type, m.vehicle_type) not in fuel_set:
            continue
        if age_min is not None or age_max is not None:
            age = calc_age_from_resident(m.resident_number)
            if age is None:
                continue
            if age_min is not None and age < age_min:
                continue
            if age_max is not None and age > age_max:
                continue
        result.append(m)
    return result


def _fmt_target(m) -> dict:
    return {
        "id": m.id,
        "management_number": m.management_number or "",
        "name": m.name or "",
        "mobile": m.mobile or "",
        "region": m.region or "",
        "vehicle_number": m.vehicle_number or "",
        "vehicle_type": sms_classify_vehicle(m.vehicle_type, m.fuel_type),
        "fuel_type": sms_classify_fuel(m.fuel_type, m.vehicle_type),
        "age": calc_age_from_resident(m.resident_number),
        "category": m.category or "",
        "membership_status": m.membership_status or "",
    }


@router.get("/targets")
async def get_targets(
    region: Optional[str] = Query(None, description="콤마로 구분된 복수값 (예: 춘천시,속초시)"),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    fuel_type: Optional[str] = Query(None),
    age_min: Optional[str] = Query(None),
    age_max: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """조건에 맞는 대상자를 license_holders에서 실시간으로 조회한다.
    각 항목(region/category/membership_status/vehicle_type/fuel_type)은 콤마로 여러
    값을 받을 수 있고, 같은 항목 내에서는 OR, 서로 다른 항목끼리는 AND로 적용한다.
    전화번호(mobile)가 없거나 형식이 유효하지 않은 사람은 자동 제외한다."""
    age_min_i = int(age_min) if age_min not in (None, "") else None
    age_max_i = int(age_max) if age_max not in (None, "") else None
    q = _build_target_query(db, region=_csv_list(region), category=_csv_list(category),
                             membership_status=_csv_list(membership_status), search=search)
    all_rows = q.all()
    filtered = _apply_all_filters(all_rows, vehicle_type_cats=_csv_list(vehicle_type),
                                   fuel_type_cats=_csv_list(fuel_type),
                                   age_min=age_min_i, age_max=age_max_i)
    total = len(filtered)
    excluded_no_phone = len([m for m in all_rows if not _valid_phone(m.mobile)])
    start = (page - 1) * limit
    page_rows = filtered[start:start + limit]
    return {
        "total": total,
        "excluded_no_phone": excluded_no_phone,
        "page": page,
        "limit": limit,
        "items": [_fmt_target(m) for m in page_rows],
    }


@router.get("/targets/ids")
async def get_target_ids(
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    fuel_type: Optional[str] = Query(None),
    age_min: Optional[str] = Query(None),
    age_max: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """조건에 맞는 전체 대상자의 id 목록만 반환 - 화면의 "전체 선택"용
    (페이지에 보이는 것만이 아니라 조건 전체를 선택할 때 사용)."""
    age_min_i = int(age_min) if age_min not in (None, "") else None
    age_max_i = int(age_max) if age_max not in (None, "") else None
    q = _build_target_query(db, region=_csv_list(region), category=_csv_list(category),
                             membership_status=_csv_list(membership_status), search=search)
    filtered = _apply_all_filters(q.all(), vehicle_type_cats=_csv_list(vehicle_type),
                                   fuel_type_cats=_csv_list(fuel_type),
                                   age_min=age_min_i, age_max=age_max_i)
    return {"total": len(filtered), "ids": [m.id for m in filtered]}


# ────────────────────────────────────────────────────────────
# 1-1. 명단추출 (엑셀 다운로드) - 조회만 하며 회원 DB를 수정하지 않는다.
#      발송닷컴 API는 절대 호출하지 않는다.
# ────────────────────────────────────────────────────────────

_EXPORT_HEADERS = ["번호", "이름", "휴대폰번호", "지역", "회원구분", "가입여부", "차량번호", "차량형태", "유종"]


def _build_targets_xlsx(rows: List) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "명단"
    ws.append(_EXPORT_HEADERS)
    for idx, m in enumerate(rows, start=1):
        # 휴대폰번호는 반드시 문자열로 기록 (엑셀에서 앞자리 0 소실 방지)
        mobile = str(m.mobile or "")
        ws.append([
            idx, m.name or "", mobile, m.region or "", m.category or "",
            m.membership_status or "", m.vehicle_number or "",
            sms_classify_vehicle(m.vehicle_type, m.fuel_type),
            sms_classify_fuel(m.fuel_type, m.vehicle_type),
        ])
        ws.cell(row=idx + 1, column=3).number_format = "@"
    widths = [6, 10, 14, 10, 8, 8, 12, 10, 8]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _xlsx_response(content: bytes) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=list_extract.xlsx"},
    )


@router.get("/export/all")
async def export_all_targets(
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    vehicle_type: Optional[str] = Query(None),
    fuel_type: Optional[str] = Query(None),
    age_min: Optional[str] = Query(None),
    age_max: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """현재 검색조건에 해당하는 전체 대상자를 XLSX로 추출한다 (페이지 제한 없음).
    조회 전용이며 회원 DB는 전혀 수정하지 않는다. 발송닷컴 API는 호출하지 않는다."""
    age_min_i = int(age_min) if age_min not in (None, "") else None
    age_max_i = int(age_max) if age_max not in (None, "") else None
    q = _build_target_query(db, region=_csv_list(region), category=_csv_list(category),
                             membership_status=_csv_list(membership_status), search=search)
    filtered = _apply_all_filters(q.all(), vehicle_type_cats=_csv_list(vehicle_type),
                                   fuel_type_cats=_csv_list(fuel_type),
                                   age_min=age_min_i, age_max=age_max_i)
    return _xlsx_response(_build_targets_xlsx(filtered))


@router.get("/export/selected")
async def export_selected_targets(
    ids: str = Query(..., description="콤마로 구분된 id 목록"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """체크한 대상자만 XLSX로 추출한다. 조회 전용이며 회원 DB는 전혀 수정하지 않는다.
    발송닷컴 API는 호출하지 않는다."""
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not id_list:
        raise HTTPException(400, "선택된 대상자가 없습니다.")
    members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.id.in_(id_list),
        models.LicenseHolder.deleted_at.is_(None),
    ).all()
    order = {mid: i for i, mid in enumerate(id_list)}
    members.sort(key=lambda m: order.get(m.id, 10 ** 9))
    return _xlsx_response(_build_targets_xlsx(members))


@router.get("/filter-options")
async def filter_options(current_user: models.User = Depends(get_current_user)):
    """문자발송 화면의 차량형태/유종 선택지 (프론트에도 상수로 있지만, 서버 기준을
    단일 소스로 유지하기 위해 API로도 제공)."""
    return {"vehicle_cats": SMS_VEHICLE_CATS, "fuel_cats": SMS_FUEL_CATS}


# ────────────────────────────────────────────────────────────
# 2. 문자 템플릿
# ────────────────────────────────────────────────────────────

class TemplateIn(BaseModel):
    name: str
    category: Optional[str] = None
    subject: Optional[str] = None
    content: str
    service: Optional[str] = "SMS"


def _fmt_template(t) -> dict:
    return {
        "id": t.id, "name": t.name or "", "category": t.category or "",
        "subject": t.subject or "", "content": t.content or "",
        "service": t.service or "SMS",
        "updated_at": str(t.updated_at)[:16] if t.updated_at else "",
    }


@router.get("/templates")
async def list_templates(db: Session = Depends(get_db),
                          current_user: models.User = Depends(get_current_user)):
    rows = db.query(models.SmsTemplate).filter(
        models.SmsTemplate.deleted_at.is_(None)
    ).order_by(models.SmsTemplate.updated_at.desc()).all()
    return {"items": [_fmt_template(t) for t in rows]}


@router.post("/templates")
async def create_template(data: TemplateIn, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    t = models.SmsTemplate(**data.model_dump(), created_by=getattr(current_user, "username", None))
    db.add(t); db.commit(); db.refresh(t)
    return _fmt_template(t)


@router.put("/templates/{tid}")
async def update_template(tid: int, data: TemplateIn, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    t = db.query(models.SmsTemplate).filter(models.SmsTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
    for k, v in data.model_dump().items():
        setattr(t, k, v)
    db.commit(); db.refresh(t)
    return _fmt_template(t)


@router.post("/templates/{tid}/copy")
async def copy_template(tid: int, db: Session = Depends(get_db),
                         current_user: models.User = Depends(get_current_user)):
    t = db.query(models.SmsTemplate).filter(models.SmsTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
    new_t = models.SmsTemplate(name=f"{t.name} (복사본)", category=t.category,
                                subject=t.subject, content=t.content, service=t.service,
                                created_by=getattr(current_user, "username", None))
    db.add(new_t); db.commit(); db.refresh(new_t)
    return _fmt_template(new_t)


@router.delete("/templates/{tid}")
async def delete_template(tid: int, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    t = db.query(models.SmsTemplate).filter(models.SmsTemplate.id == tid).first()
    if not t:
        raise HTTPException(404, "템플릿을 찾을 수 없습니다.")
    t.deleted_at = datetime.utcnow()
    db.commit()
    return {"message": "삭제되었습니다."}


# ────────────────────────────────────────────────────────────
# 3. 발송 (테스트 / 즉시 / 예약)
# ────────────────────────────────────────────────────────────

def _replace_datas_for(m) -> list:
    out = []
    for var_name, field in VARIABLE_FIELD_MAP.items():
        val = getattr(m, field, "") or ""
        if field == "fuel_type":
            val = normalize_fuel(val)
        out.append({"Key": f"#{{{var_name}}}", "Value": str(val)})
    return out


def _apply_replace(text: str, m) -> str:
    if not text:
        return text
    result = text
    for var_name, field in VARIABLE_FIELD_MAP.items():
        val = getattr(m, field, "") or ""
        if field == "fuel_type":
            val = normalize_fuel(val)
        result = result.replace(f"#{{{var_name}}}", str(val))
    return result


def _guess_service(main_text: str) -> str:
    # 문서에 자동판별 규정은 없으므로, 프론트에서 명시적으로 선택한 값을 우선 사용한다.
    # 값이 없을 때만 바이트 길이로 기본값을 추정 (SMS 90byte 초과 시 LMS 권장 - 업계 통상 기준일 뿐,
    # 발송닷컴 문서에 명시된 규정이 아니므로 어디까지나 프론트 기본 선택값에 대한 보조 추정치임).
    byte_len = len((main_text or "").encode("euc-kr", errors="ignore"))
    return "LMS" if byte_len > 90 else "SMS"


class SendRequest(BaseModel):
    filters: dict = {}
    recipient_ids: List[int] = []
    service: Optional[str] = None            # SMS/LMS - 미지정시 자동 추정
    callback: str                             # 발신번호 (필수)
    subject: Optional[str] = None
    main_text: str
    template_id: Optional[int] = None
    send_mode: str = "즉시"                    # 즉시 / 예약
    scheduled_at: Optional[str] = None        # "YYYY-MM-DD HH:MM" (예약인 경우 필수)
    is_test: bool = False
    test_phones: List[str] = []               # 테스트 발송 시 사용할 번호(소수)


async def _do_immediate_send(db: Session, job: models.SmsJob, recipients: List[models.SmsRecipient],
                              destinations: list):
    """실제 발송닷컴 호출 - 배치 1회. 결과를 job/recipients에 반영."""
    resp = await balsong.send_message(
        service=job.service, callback=job.callback, main_text=job.main_text,
        destinations=destinations, subject=job.subject,
    )
    ok = resp.get("Result") == "OK" and resp.get("Code") in (0, "0")
    job.sent_at = datetime.utcnow()
    if ok:
        job.status = "완료"
        job.job_no = str(resp.get("Job_No")) if resp.get("Job_No") is not None else None
        job.cash_after = resp.get("Cash")
        job.success_count = job.total_count
        job.fail_count = 0
    else:
        job.status = "실패"
        job.error_message = _clean_result_message(resp)
        job.fail_count = job.total_count
        job.success_count = 0
    for r in recipients:
        r.status = "성공" if ok else "실패"
        if not ok:
            r.status_detail = job.error_message
    db.commit()
    return ok, resp


_TAG_RE = re.compile(r"<[^>]+>")


def _clean_result_message(resp: dict) -> str:
    """발송닷컴 응답에서 사용자에게 보여줘도 안전한 오류 메시지만 추출한다.
    HTML/SVG 등 마크업이 섞여 들어오면 태그를 제거하고, 그래도 비어있으면
    일반적인 문구로 대체한다 (UserID/UserPW 등 비밀정보는 애초에 응답에 없음)."""
    raw = resp.get("Message") or resp.get("Code") or ""
    text = _TAG_RE.sub(" ", str(raw))
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > 200:
        return "발송닷컴 발송에 실패했습니다. 관리자에게 문의해 주세요."
    return text


@router.post("/send")
async def send_sms(data: SendRequest, db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    if data.send_mode not in ("즉시", "예약"):
        raise HTTPException(400, "send_mode는 '즉시' 또는 '예약'이어야 합니다.")
    if data.send_mode == "예약" and not data.scheduled_at:
        raise HTTPException(400, "예약발송은 scheduled_at이 필요합니다.")

    service = data.service or _guess_service(data.main_text)
    if service not in ("SMS", "LMS", "MMS"):
        raise HTTPException(400, "service는 SMS/LMS/MMS 중 하나여야 합니다.")

    # ── 테스트 발송: 선택한 대상자 대신 지정된 소수 번호로만 즉시 발송 ──
    if data.is_test:
        phones = [p for p in (re.sub(r"\D", "", p) for p in data.test_phones) if _PHONE_RE.match(p)]
        if not phones:
            raise HTTPException(400, "유효한 테스트 번호가 없습니다.")
        job = models.SmsJob(
            filters=data.filters, template_id=data.template_id, service=service,
            callback=data.callback, subject=data.subject, main_text=data.main_text,
            send_mode="즉시", is_test=True, status="발송중",
            total_count=len(phones), created_by=getattr(current_user, "username", None),
        )
        db.add(job); db.commit(); db.refresh(job)
        recipients = []
        for p in phones:
            r = models.SmsRecipient(sms_job_id=job.id, license_holder_id=None,
                                     name="(테스트)", phone=p, msg_text=data.main_text)
            db.add(r); recipients.append(r)
        db.commit()
        destinations = [{"Phone": p, "Name": "테스트"} for p in phones]
        ok, resp = await _do_immediate_send(db, job, recipients, destinations)
        if not ok:
            # 발송닷컴 호출 실패를 우리 API의 성공(200)으로 반환하지 않는다.
            # job/recipients는 이미 "실패"로 커밋됐으니 이력에서 확인 가능.
            raise HTTPException(
                status_code=502,
                detail=f"{_clean_result_message(resp)} (job_id={job.id})",
            )
        return {"success": True, "message": "테스트 문자가 정상 발송되었습니다.",
                "ok": True, "job_id": job.id, "job_no": job.job_no}

    # ── 실제 발송: 선택된 recipient_ids를 license_holders에서 그대로 재조회 ──
    if not data.recipient_ids:
        raise HTTPException(400, "발송 대상자가 없습니다.")
    members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.id.in_(data.recipient_ids),
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status != "closed",
    ).all()
    members = [m for m in members if _valid_phone(m.mobile)]
    if not members:
        raise HTTPException(400, "유효한 휴대폰 번호를 가진 대상자가 없습니다.")

    job = models.SmsJob(
        filters=data.filters, template_id=data.template_id, service=service,
        callback=data.callback, subject=data.subject, main_text=data.main_text,
        send_mode=data.send_mode, scheduled_at=data.scheduled_at, is_test=False,
        status=("예약대기" if data.send_mode == "예약" else "발송중"),
        total_count=len(members), created_by=getattr(current_user, "username", None),
    )
    db.add(job); db.commit(); db.refresh(job)

    recipients = []
    for m in members:
        msg_text = _apply_replace(data.main_text, m)
        r = models.SmsRecipient(sms_job_id=job.id, license_holder_id=m.id,
                                 name=m.name or "", phone=_clean_phone(m.mobile),
                                 region=m.region or "", msg_text=msg_text)
        db.add(r); recipients.append(r)
    db.commit()

    if data.send_mode == "예약":
        # 발송닷컴 자체 Send_Date가 아니라, 우리 쪽에서 예약시각까지 보관했다가
        # 그 시각에 즉시발송으로 호출한다. 이렇게 해야 예약 취소/수정이 실제로 가능하다
        # (문서에 별도의 예약취소 API가 없으므로, 발송닷컴에 먼저 넘겨버리면 취소할 수 없다).
        return {"success": True, "message": "예약 발송이 등록되었습니다.",
                "ok": True, "job_id": job.id, "status": "예약대기", "scheduled_at": data.scheduled_at}

    destinations = []
    for m in members:
        dest = {"Phone": _clean_phone(m.mobile), "Name": m.name or "",
                "Replace_Datas": _replace_datas_for(m)}
        destinations.append(dest)
    ok, resp = await _do_immediate_send(db, job, recipients, destinations)
    if not ok:
        raise HTTPException(
            status_code=502,
            detail=f"{_clean_result_message(resp)} (job_id={job.id})",
        )
    return {"success": True, "message": f"발송 완료 ({len(members)}명)",
            "ok": True, "job_id": job.id, "job_no": job.job_no, "total": len(members)}


# ────────────────────────────────────────────────────────────
# 4. 예약문자 관리
# ────────────────────────────────────────────────────────────

def _fmt_job(j) -> dict:
    return {
        "id": j.id, "service": j.service, "callback": j.callback or "",
        "subject": j.subject or "", "main_text": j.main_text or "",
        "send_mode": j.send_mode, "scheduled_at": j.scheduled_at or "",
        "status": j.status, "total_count": j.total_count,
        "success_count": j.success_count, "fail_count": j.fail_count,
        "job_no": j.job_no or "", "is_test": bool(j.is_test),
        "filters": j.filters or {},
        "created_at": str(j.created_at)[:16] if j.created_at else "",
        "sent_at": str(j.sent_at)[:16] if j.sent_at else "",
    }


@router.get("/reserved")
async def list_reserved(db: Session = Depends(get_db),
                         current_user: models.User = Depends(get_current_user)):
    rows = db.query(models.SmsJob).filter(
        models.SmsJob.send_mode == "예약",
        models.SmsJob.status.in_(["예약대기", "발송중", "완료", "실패", "취소"]),
    ).order_by(models.SmsJob.scheduled_at.asc()).all()
    return {"items": [_fmt_job(j) for j in rows]}


class ReservedUpdate(BaseModel):
    main_text: Optional[str] = None
    subject: Optional[str] = None
    scheduled_at: Optional[str] = None


@router.put("/reserved/{job_id}")
async def update_reserved(job_id: int, data: ReservedUpdate, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    job = db.query(models.SmsJob).filter(models.SmsJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "예약을 찾을 수 없습니다.")
    if job.status != "예약대기":
        raise HTTPException(400, "이미 발송되었거나 취소된 예약은 수정할 수 없습니다.")
    if data.main_text is not None:
        job.main_text = data.main_text
        recipients = db.query(models.SmsRecipient).filter(
            models.SmsRecipient.sms_job_id == job.id).all()
        members_by_id = {m.id: m for m in db.query(models.LicenseHolder).filter(
            models.LicenseHolder.id.in_([r.license_holder_id for r in recipients if r.license_holder_id])
        ).all()}
        for r in recipients:
            m = members_by_id.get(r.license_holder_id)
            r.msg_text = _apply_replace(data.main_text, m) if m else data.main_text
    if data.subject is not None:
        job.subject = data.subject
    if data.scheduled_at is not None:
        job.scheduled_at = data.scheduled_at
    db.commit()
    return {"ok": True}


@router.post("/reserved/{job_id}/cancel")
async def cancel_reserved(job_id: int, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    job = db.query(models.SmsJob).filter(models.SmsJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "예약을 찾을 수 없습니다.")
    if job.status != "예약대기":
        raise HTTPException(400, "이미 발송되었거나 취소된 예약입니다.")
    job.status = "취소"
    job.cancelled_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


# ────────────────────────────────────────────────────────────
# 5. 발송 이력
# ────────────────────────────────────────────────────────────

@router.get("/history")
async def list_history(page: int = Query(1, ge=1), limit: int = Query(30, ge=1, le=100),
                        db: Session = Depends(get_db),
                        current_user: models.User = Depends(get_current_user)):
    q = db.query(models.SmsJob).filter(models.SmsJob.send_mode == "즉시").order_by(
        models.SmsJob.created_at.desc())
    # 예약이었던 건도 발송이 완료/실패되면 이력에 함께 보여준다
    q_all = db.query(models.SmsJob).filter(
        models.SmsJob.status.in_(["완료", "실패"])
    ).order_by(models.SmsJob.created_at.desc())
    total = q_all.count()
    rows = q_all.offset((page - 1) * limit).limit(limit).all()
    return {"total": total, "items": [_fmt_job(j) for j in rows]}


@router.get("/history/{job_id}")
async def history_detail(job_id: int, db: Session = Depends(get_db),
                          current_user: models.User = Depends(get_current_user)):
    job = db.query(models.SmsJob).filter(models.SmsJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "발송 이력을 찾을 수 없습니다.")
    recipients = db.query(models.SmsRecipient).filter(
        models.SmsRecipient.sms_job_id == job_id).all()
    return {
        "job": _fmt_job(job),
        "recipients": [{
            "id": r.id, "license_holder_id": r.license_holder_id, "name": r.name or "",
            "phone": r.phone or "", "region": r.region or "", "status": r.status,
            "status_detail": r.status_detail or "", "done_date": r.done_date or "",
        } for r in recipients],
    }


@router.post("/history/{job_id}/refresh")
async def refresh_history(job_id: int, db: Session = Depends(get_db),
                           current_user: models.User = Depends(get_current_user)):
    """발송닷컴 Report_Detail로 수신자별 성공/실패 상태를 최신화한다."""
    job = db.query(models.SmsJob).filter(models.SmsJob.id == job_id).first()
    if not job:
        raise HTTPException(404, "발송 이력을 찾을 수 없습니다.")
    if not job.job_no:
        return {"ok": False, "message": "Job_No가 없어 조회할 수 없습니다."}
    resp = await balsong.get_report_detail(job_no=job.job_no, service=job.service)
    if resp.get("Result") != "OK":
        return {"ok": False, "raw": resp}
    detail_list = resp.get("List") or resp.get("Data") or resp.get("Items") or []
    by_phone = {}
    for d in detail_list if isinstance(detail_list, list) else []:
        phone = re.sub(r"\D", "", str(d.get("Phone", "")))
        if phone:
            by_phone[phone] = d
    recipients = db.query(models.SmsRecipient).filter(
        models.SmsRecipient.sms_job_id == job_id).all()
    success = fail = 0
    for r in recipients:
        d = by_phone.get(r.phone)
        if not d:
            continue
        status_raw = str(d.get("Status", ""))
        r.status_detail = d.get("Status_Detail") or ""
        r.done_date = d.get("Done_Date") or ""
        if "성공" in status_raw or status_raw.upper() in ("OK", "SUCCESS"):
            r.status = "성공"; success += 1
        else:
            r.status = "실패"; fail += 1
    if success or fail:
        job.success_count = success
        job.fail_count = fail
    db.commit()
    return {"ok": True, "raw": resp}


@router.get("/history/member/{license_holder_id}")
async def member_history(license_holder_id: int, db: Session = Depends(get_db),
                          current_user: models.User = Depends(get_current_user)):
    """회원 상세화면에서 사용 - 해당 회원에게 보낸 문자 이력."""
    recipients = db.query(models.SmsRecipient).filter(
        models.SmsRecipient.license_holder_id == license_holder_id
    ).order_by(models.SmsRecipient.created_at.desc()).all()
    job_ids = list({r.sms_job_id for r in recipients})
    jobs = {j.id: j for j in db.query(models.SmsJob).filter(models.SmsJob.id.in_(job_ids)).all()}
    items = []
    for r in recipients:
        j = jobs.get(r.sms_job_id)
        items.append({
            "sent_at": str(j.sent_at)[:16] if j and j.sent_at else (str(r.created_at)[:16] if r.created_at else ""),
            "content": r.msg_text or (j.main_text if j else ""),
            "template_id": j.template_id if j else None,
            "status": r.status, "status_detail": r.status_detail or "",
        })
    return {"items": items}


@router.get("/connection-test")
async def connection_test(current_user: models.User = Depends(get_current_user)):
    return await balsong.test_connection()


@router.get("/network-diag")
async def network_diag(current_user: models.User = Depends(require_admin)):
    """balsong.com에 대한 저수준 네트워크 점검 (DNS/TCP/TLS/무인증 GET).
    실제 발송 계정정보나 발송 데이터는 전혀 사용하지 않는다.
    운영(Railway) 환경에서 호출해야 그 네트워크 기준의 결과를 볼 수 있다."""
    return await balsong.network_diagnostics()


class AdminTestSendRequest(BaseModel):
    service: str = "SMS"
    callback: str
    subject: Optional[str] = None
    main_text: str
    phone: str


@router.post("/admin-test-send")
async def admin_test_send(data: AdminTestSendRequest,
                           current_user: models.User = Depends(require_admin)):
    """관리자 전용 - 실제 수신자 1명에게만 발송닷컴으로 SMS/LMS 1건을 보내고,
    발송닷컴의 원본 Result/Code/Message/Job_No를 그대로(가공 없이) 반환한다.
    balsong_client의 send_message()를 그대로 사용 - 별도의 발송 로직을 만들지 않는다.
    타임아웃/오류로 응답을 못 받으면 절대 성공(ok=True)으로 표시하지 않는다.
    회원 발송이력(SmsJob/SmsRecipient)에는 남기지 않는 순수 연결 테스트용 endpoint."""
    phone = re.sub(r"\D", "", data.phone or "")
    if not _PHONE_RE.match(phone):
        raise HTTPException(400, "유효한 휴대폰 번호가 아닙니다.")
    if data.service not in ("SMS", "LMS", "MMS"):
        raise HTTPException(400, "service는 SMS/LMS/MMS 중 하나여야 합니다.")
    if not data.callback:
        raise HTTPException(400, "발신번호(Callback)를 입력하세요.")
    if not data.main_text.strip():
        raise HTTPException(400, "문자 내용을 입력하세요.")

    resp = await balsong.send_message(
        service=data.service, callback=data.callback, main_text=data.main_text,
        destinations=[{"Phone": phone, "Name": "테스트"}], subject=data.subject,
    )
    ok = resp.get("Result") == "OK" and resp.get("Code") in (0, "0")
    payload = {
        "ok": ok,
        "result": resp.get("Result"),
        "code": resp.get("Code"),
        "message": resp.get("Message") or "",
        "job_no": resp.get("Job_No"),
    }
    if not ok:
        return JSONResponse(status_code=502, content=payload)
    return payload


# ────────────────────────────────────────────────────────────
# 6. 예약발송 스케줄러 (발송닷컴 자체 Send_Date 대신, 우리 쪽에서 시각까지 들고 있다가
#    직접 즉시발송을 호출 - 예약취소/수정을 실제로 가능하게 하기 위함)
# ────────────────────────────────────────────────────────────

async def run_scheduled_sms_loop():
    while True:
        try:
            await _process_due_reserved_jobs()
        except Exception as e:
            logger.warning(f"예약문자 스케줄러 오류 (무시하고 계속): {e}")
        await asyncio.sleep(30)


async def _process_due_reserved_jobs():
    db = SessionLocal()
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        due_jobs = db.query(models.SmsJob).filter(
            models.SmsJob.send_mode == "예약",
            models.SmsJob.status == "예약대기",
            models.SmsJob.scheduled_at <= now_str,
        ).all()
        for job in due_jobs:
            recipients = db.query(models.SmsRecipient).filter(
                models.SmsRecipient.sms_job_id == job.id).all()
            if not recipients:
                job.status = "실패"; job.error_message = "수신자 없음"; db.commit()
                continue
            members = db.query(models.LicenseHolder).filter(
                models.LicenseHolder.id.in_([r.license_holder_id for r in recipients if r.license_holder_id])
            ).all()
            members_by_id = {m.id: m for m in members}
            destinations = []
            for r in recipients:
                m = members_by_id.get(r.license_holder_id)
                if not m or not _valid_phone(m.mobile):
                    continue
                destinations.append({"Phone": _clean_phone(m.mobile), "Name": r.name or "",
                                      "Replace_Datas": _replace_datas_for(m)})
            job.status = "발송중"; db.commit()
            if not destinations:
                job.status = "실패"; job.error_message = "유효한 수신자가 없습니다."; db.commit()
                continue
            await _do_immediate_send(db, job, recipients, destinations)
    finally:
        db.close()
