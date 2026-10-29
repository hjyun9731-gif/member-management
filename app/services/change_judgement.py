"""변경이력 판정 / 집계 기준일 / 개인정보 보호 (순수 함수 모음).

판정 유형: 정상변경 · 최초등록 · 표기정정 · 오류정정 · 확인필요
- 정상변경만 월례보고서·월별 통계의 '실제 변경실적'에 합산한다.
- 이 모듈은 DB를 읽거나 쓰지 않는다 (판정·표시용 계산만).
"""
import datetime as _dt
import re

NORMAL, FIRST, NOTATION, ERROR_FIX, REVIEW = "정상변경", "최초등록", "표기정정", "오류정정", "확인필요"
JUDGEMENTS = (NORMAL, FIRST, NOTATION, ERROR_FIX, REVIEW)

# 집계 구분(월례보고서·통계 공통)
B_COMPANY, B_VEHICLE, B_ADDRESS, B_PLATE = "상호변경", "차량변경(대폐차)", "주소변경", "차량번호변경"
B_REP, B_CERT, B_OTHER = "대표자변경", "자격증재교부", "기타"
BUCKET_ORDER = [B_COMPANY, B_VEHICLE, B_ADDRESS, B_PLATE, B_REP, B_CERT, B_OTHER]

_BUCKET_BY_TYPE = {
    "상호변경": B_COMPANY, "전속계약 업체변경": B_COMPANY,          # 소속업체 변경 = 상호변경 실적
    "대폐차": B_VEHICLE, "차종변경": B_VEHICLE, "구조변경": B_VEHICLE,  # 구조변경: 기존 집계 기준 유지
    "주소지변경": B_ADDRESS,
    "차량번호변경": B_PLATE, "번호변경": B_PLATE, "최초번호등록": B_PLATE,
    "대표자변경": B_REP, "자격증재교부": B_CERT, "자격재교부": B_CERT,
}


def bucket_of(change_type: str) -> str:
    return _BUCKET_BY_TYPE.get((change_type or "").strip(), B_OTHER)


# ───────── 개인정보(주민등록번호 형식) 보호 ─────────
_RRN_ANY = re.compile(r"\d{6}\s*-\s*\d{7}")
MASK_TEXT = "(표시 제한)"


def has_rrn(s) -> bool:
    return bool(_RRN_ANY.search(str(s or "")))


def mask_rrn(s):
    """주민등록번호 형식(######-#######)이 들어 있으면 값을 가린다. 원본 DB는 건드리지 않는다."""
    if s is None:
        return s
    t = str(s)
    return MASK_TEXT if _RRN_ANY.search(t) else t


# ───────── 날짜 ─────────
def parse_ymd(s):
    """유효한 날짜면 (y, m, d), 아니면 None. 허용: 2018.01.03 / 2018.01.03. / 2016 09.21 / 26.05.22 / 2026-05-07"""
    t = str(s or "").strip()
    if not t or has_rrn(t):
        return None
    m = re.fullmatch(r"(\d{4})\s*[.\-/ ]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*\.?", t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*\.?", t)
        if not m:
            return None
        yy, mo, d = (int(x) for x in m.groups())
        y = 2000 + yy if yy <= 30 else 1900 + yy
    try:
        _dt.date(y, mo, d)
    except ValueError:
        return None
    return (y, mo, d) if 1990 <= y <= 2100 else None


def pick_report_date(change_date, receipt_date):
    """집계 기준일: 유효한 변경일자 → 유효한 접수일자 → 없음. 반환 ((y,m,d)|None, 'change_date'|'receipt_date'|None)"""
    ymd = parse_ymd(change_date)
    if ymd:
        return ymd, "change_date"
    ymd = parse_ymd(receipt_date)
    if ymd:
        return ymd, "receipt_date"
    return None, None


# ───────── 판정 ─────────
_WS = re.compile(r"\s+")


def _k(s) -> str:
    return _WS.sub("", str(s or ""))


def _plate_key(s) -> str:
    return re.sub(r"[\s]|호$", "", str(s or ""))


_FULL_PLATE = re.compile(r"^강원\d{2}[가-힣]\d{4}$")
_PLATE_SHAPE = re.compile(r"^[가-힣]{2}\d{2}[가-힣]\d{4}$")   # 지역명 오타(강운 등)도 번호 형태로는 인정
_PLACEHOLDER = re.compile(r"^(-|택배|강원[가-힣]?)?$")


def is_placeholder_plate(s) -> bool:
    return bool(_PLACEHOLDER.fullmatch(_plate_key(s)))


def is_full_plate(s) -> bool:
    return bool(_FULL_PLATE.fullmatch(_plate_key(s)))


def _is_plate_shape(s) -> bool:
    return bool(_PLATE_SHAPE.fullmatch(_plate_key(s)))


def classify_plate(before, after):
    """차량번호 변경 판정 → (판정, 사유)"""
    nb, na = _plate_key(before), _plate_key(after)
    if nb == na:
        return NOTATION, "공백·'호' 표기 차이"
    if not na:
        return REVIEW, "변경 후 차량번호가 비어 있음"
    if is_placeholder_plate(nb):
        if is_full_plate(na):
            return FIRST, "임시값/빈칸에서 최초 차량번호 확정 등록"
        return REVIEW, "임시값 → 완전한 차량번호가 아님"
    if _is_plate_shape(nb) and _is_plate_shape(na):
        # 지역명 한 글자 오타 정정(강운→강원): 나머지(숫자·용도문자)가 같을 때만
        if nb[2:] == na[2:] and sum(a != b for a, b in zip(nb[:2], na[:2])) == 1:
            return ERROR_FIX, "지역명 오타 정정(번호 동일)"
        if len(nb) == len(na) and sum(a != b for a, b in zip(nb, na)) == 1:
            return REVIEW, "한 글자 차이: 오타인지 실제 번호변경인지 불확실"
        return NORMAL, "차량번호 변경"
    return REVIEW, "차량번호 형식이 달라 자동 판정 불가"


def classify_text(before, after):
    """주소·소속업체·차종 등 일반 텍스트 변경 판정 → (판정, 사유)"""
    kb, ka = _k(before).lower(), _k(after).lower()
    if kb == ka:
        return NOTATION, "공백·표기 차이만 있음"
    if kb in ("", "-"):
        return FIRST, "이전 값 없음(-)에서 최초 입력"
    return NORMAL, "내용 변경"


def legacy_address_split_suspect(before, after) -> bool:
    """기존 업로드 이력 점검용: 이후 값이 호수·동 같은 상세 값만 있고 이전 값이 도로명 주소인 경우
    (업로드 때 주소가 두 칸으로 나뉘어 저장된 것으로 의심). 신규 수정 판정에는 쓰지 않는다."""
    a, b = str(after or "").strip(), str(before or "")
    return bool(re.fullmatch(r"[\d\-,호동층가-힣]{0,8}", a)) and not re.search(r"(로|길|읍|면|리|번지)", a) \
        and bool(re.search(r"(로|길|읍|면|리)", b))


def classify_for_type(change_type, before, after):
    if (change_type or "") in ("차량번호변경", "번호변경", "최초번호등록"):
        return classify_plate(before, after)
    return classify_text(before, after)


# ───────── 저장된 이력 한 건의 '집계상 상태' ─────────
class RowView:
    __slots__ = ("judgement", "reason", "ymd", "date_source", "legacy", "bucket")


def view_row(c) -> RowView:
    """저장된 ChangeHistory 한 건을 월례보고서/통계 공통 기준으로 해석한다 (DB 수정 없음).
    - 판정값이 저장돼 있으면 그대로, 없으면(기존 이력) 정상변경으로 보되 legacy=True 로 표시.
    - 유효한 날짜(변경일자→접수일자)가 하나도 없으면 확인필요(월 귀속 불가)."""
    v = RowView()
    stored = getattr(c, "judgement", None)
    v.legacy = not stored
    v.judgement = stored if stored in JUDGEMENTS else NORMAL
    v.reason = getattr(c, "judge_reason", None) or ""
    v.ymd, v.date_source = pick_report_date(getattr(c, "change_date", ""), getattr(c, "receipt_date", ""))
    if v.ymd is None and v.judgement == NORMAL:
        v.judgement, v.reason = REVIEW, "변경일자·접수일자 모두 유효하지 않아 월 귀속 불가"
    v.bucket = bucket_of(getattr(c, "change_type", ""))
    return v
