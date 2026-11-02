"""명시된 업무 유형·변경사유를 해석하는 읽기전용 자동판정.

문자열 차이만으로 주소 이전/상호 변경을 확정하지 않는다. 기록에 남은 업무 사유,
이동 경로, 원본 주소 열을 근거로 사용하고 근거가 없으면 검증 작업 묶음으로 보낸다.
"""
import re
from dataclasses import dataclass, field
from app.services.change_judgement import (
    NORMAL, FIRST, NOTATION, ERROR_FIX, REVIEW, EXCLUDED,
    classify_for_type, pick_report_date, _k, is_placeholder_plate,
)


@dataclass
class EvidenceDecision:
    judgement: str
    report_decision: str
    reason: str
    rule: str
    queue: str = "AUTO"
    evidence: dict = field(default_factory=dict)


def _get(row, key, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def _hold(reason, rule, queue="HUMAN_EXCEPTION", evidence=None):
    return EvidenceDecision(REVIEW, "hold", reason, rule, queue, evidence or {})


def _auto(j, reason, rule, evidence=None):
    return EvidenceDecision(j, "include" if j == NORMAL else "exclude", reason, rule, "AUTO", evidence or {})


_REGIONS = set("춘천 원주 강릉 동해 태백 속초 삼척 홍천 횡성 영월 평창 정선 철원 화천 양구 인제 고성 양양 서울 부산 대구 인천 광주 대전 울산 세종 경기 강원 충북 충남 전북 전남 경북 경남 제주 청주 전주 천안 수원 성남 용인".split())
_ALIASES = {"강원특별자치도": "강원", "전북특별자치도": "전북", "충청북도": "충북", "충청남도": "충남",
            "경상북도": "경북", "경상남도": "경남", "전라북도": "전북", "전라남도": "전남", "제주특별자치도": "제주"}


def _region(s):
    s = str(s or "").strip()
    if s in _ALIASES:
        return _ALIASES[s]
    s = re.sub(r"(?:특별시|광역시|특별자치시|시|군|도)$", "", s)
    return s if s in _REGIONS else None


def transfer_route(ct, before, after, memo):
    m = re.search(re.escape(ct) + r"\s*([가-힣]+)\s*(?:->|→|⇒)\s*([가-힣]+)", memo or "")
    if m:
        src, dst = _region(m[1]), _region(m[2])
    else:
        m = re.fullmatch(re.escape(ct) + r"\s*([가-힣]+)", str(before or "").strip())
        src, dst = (_region(m[1]), _region(after)) if m else (None, None)
    return (src, dst) if src and dst and src != dst else None


def address_layout_proof(before, after, raw):
    """주소/상세주소의 원본 열 이름과 값이 모두 맞을 때만 정보보완을 인정한다.
    Unnamed 열이나 단순 호수 차이만으로는 주소 이동을 부정하지 않는다.
    """
    if not isinstance(raw, dict):
        return None
    keys = {_k(k).casefold(): v for k, v in raw.items()}
    if any(k in keys for k in ("변경전", "변경후", "이전주소", "신주소", "변경전주소", "변경후주소",
                               "before_value", "after_value", "beforevalue", "aftervalue", "old_address", "new_address")):
        return None
    for address in ("주소", "주소1", "기본주소", "도로명주소"):
        for detail in ("상세주소", "주소2", "상세", "상세주소2"):
            if address in keys and detail in keys and _k(keys[address]) == _k(before) and _k(keys[detail]) == _k(after):
                return {"address_column": address, "detail_column": detail}
    return None


def hyphen_split_proof(before, after, raw):
    """원본 단일 셀(`변 경 내 용`)이 변경전-변경후를 하이픈으로 이은 값과 정확히 같으면,
    한 개의 주소가 업로드 때 첫 하이픈에서 둘로 쪼개진 것이다(과거 주소가 아님).
    복원 값 일치 + 오른쪽이 완전한 주소가 아닌 경우 또는 일치만으로도 이전 주소가 없는 단일 셀임을 증명한다.
    """
    if not isinstance(raw, dict):
        return None
    cell = raw.get("변 경 내 용")
    if cell is None or not _k(before) or not _k(after):
        return None
    # 오른쪽은 번지·동·호수처럼 숫자로 시작하는 상세값이어야 한다(독립된 주소 두 개를 잇는 형태 배제).
    if not re.match(r"\s*\d", str(after)):
        return None
    if _k(cell) == _k(str(before) + "-" + str(after)):
        return {"raw_cell": "변 경 내 용", "reconstructed": True}
    return None


def _phone_set(s):
    numbers = re.findall(r"(?<!\d)(?:0\d{1,2})[- ]?\d{3,4}[- ]?\d{4}(?!\d)", str(s or ""))
    return {re.sub(r"\D", "", x) for x in numbers}


def classify_record(row):
    ct, before, after, memo = (_get(row, k, "") for k in ("change_type", "before_value", "after_value", "memo"))
    memo = str(memo or "")
    j, reason = classify_for_type(ct, before, after)
    if j not in (REVIEW, NORMAL):
        return _auto(j, reason, "BASE_EXCLUSION")
    date, date_source = pick_report_date(_get(row, "change_date"), _get(row, "receipt_date"))
    if not date:
        return _hold("유효한 변경일·접수일 없음: 원본/DB 일자 묶음 검증", "DATE_MISSING", "DB_DATE")
    if not _get(row, "name") or not _get(row, "vehicle_number"):
        return _hold("업무 대상 식별정보 부족: DB 연결 검증", "IDENTITY_MISSING", "DB_IDENTITY")
    ev = {"recorded_type": ct, "basis_date_source": date_source}
    raw = _get(row, "raw_data", {}) or {}
    if isinstance(raw, dict):
        extras = [str(raw.get(k) or "") for k in ("변경사유", "처리사유", "변경내용", "변경사항", "비고")]
        memo = " / ".join([s for s in [memo, *[s for s in extras if s and s != memo]] if s])
        reason_fields = {"주소지변경": ("address", "official_address"), "주소변경": ("address", "official_address"),
                         "상호변경": ("company_name",), "전속계약 업체변경": ("affiliated_company",),
                         "번호변경": ("vehicle_number",), "차량번호변경": ("vehicle_number",)}
        choices = raw.get("change_reasons") or {}
        selected = {choices[k] for k in reason_fields.get(ct, ()) if isinstance(choices.get(k), str)} if isinstance(choices, dict) else set()
        selected.discard(None)
        if len(selected) == 1:
            selected_reason = next(iter(selected))
            if selected_reason == "correction":
                return _auto(ERROR_FIX, "DB 원본에 저장된 사용자 입력 오류정정 선택", "STORED_USER_CORRECTION", ev)
            if selected_reason == "supplement":
                return _auto(FIRST, "DB 원본에 저장된 사용자 정보보완 선택", "STORED_USER_SUPPLEMENT", ev)
            if selected_reason == "actual" and _k(before) and _k(after) and _k(before) != _k(after):
                return _auto(NORMAL, "DB 원본에 저장된 사용자 실제 변경 선택", "STORED_USER_ACTUAL", ev)
    if re.search(r"오타|오류정정|입력오류|공문\s*잘못|잘못\s*옴|표기정정", memo):
        return _auto(ERROR_FIX, "명시된 오류·정정 사유에 따른 실적 제외", "EXPLICIT_CORRECTION", {**ev, "memo": memo})
    if re.search(r"신고취소|접수취소|변경취소|반려", memo):
        return _hold("취소·반려 기록은 원 처리 사건과 DB로 연결", "CANCELLED_EVENT", "DB_EVENT", ev)
    if ct in ("등록이관", "이전전출"):
        route = transfer_route(ct, before, after, memo)
        if route:
            return _auto(NORMAL, "명시된 업무 유형과 출발·도착 경로 확인(주소변경·폐업과 별도)", "RECORDED_TRANSFER_ROUTE", {**ev, "route": list(route)})
        return _hold("유형은 유지하며 이동 경로를 DB 원본 사유와 묶음 검증", "TRANSFER_ROUTE_MISSING", "DB_EVENT", ev)
    if ct in ("주소지변경", "주소변경"):
        proof = address_layout_proof(before, after, raw)
        if proof:
            return _auto(FIRST, "원본의 기본주소/상세주소 열이 각각 연결됨: 최초주소등록·정보보완", "ADDRESS_SOURCE_COLUMNS", {**ev, **proof})
        split = hyphen_split_proof(before, after, raw)
        if split:
            return _auto(FIRST, "원본 단일 주소 셀이 하이픈에서 분리 저장됨(변경전+'-'+변경후=원본 셀): 과거 주소 없음, 최초주소등록·정보보완",
                         "ADDRESS_HYPHEN_SPLIT_PROOF", {**ev, **split})
        if re.search(r"(?:실제\s*주소\s*이전|거주지\s*이전|주소\s*이전\s*(?:신고|완료)|주사무소\s*변경)", memo):
            if str(before or "").strip() and str(after or "").strip() and _k(before) != _k(after):
                return _auto(NORMAL, "기록에 명시된 주소 이전·주사무소 변경 사유 확인", "EXPLICIT_ADDRESS_MOVE", {**ev, "memo": memo})
        # 상세 호수만 바뀐 경우 실제 호수 이전일 수도 있어 자동 제외하지 않는다.
        full_after = bool(re.search(r"(?:로|길|번지)\s*\d|(?:읍|면|동|리)\s*\d", str(after or "")))
        if not full_after and str(after or "").strip():
            return _hold("상세 호수·주소 열 분리 후보: 원본 주소 열로 일괄 검증, 호수 차이만으로 제외 금지", "ADDRESS_LAYOUT_BATCH", "DB_ADDRESS_COLUMNS", ev)
        return _hold("완전한 주소 차이만으로 이전 확정 불가: 이전 사유·원본 변경전후 열 확인", "ADDRESS_MOVE_EVIDENCE", "DB_ADDRESS_REASON", ev)
    if ct == "구조변경":
        if _k(memo) == "구조변경" and _k(before) and _k(after) and _k(before) != _k(after):
            return _auto(NORMAL, "구조변경 사유와 전후 구조 확인(대폐차 제외)", "RECORDED_STRUCTURE", {**ev, "memo": memo})
        return _hold("구조변경의 원 처리내용 확인", "STRUCTURE_INCOMPLETE", "DB_EVENT", ev)
    if ct == "상호변경":
        person = _k(_get(row, "name"))
        if person and (_k(before) == person or _k(before).startswith(person + "(")):
            return _hold("기존 상호가 성명과 같아 최초 상호 입력 여부 검증", "COMPANY_WAS_PERSON_NAME", "DB_COMPANY", ev)
        if _k(memo) == "상호변경" and _k(before) and _k(after) and _k(before) != _k(after):
            return _auto(NORMAL, "상호변경 업무 사유 및 기존·새 상호 기록 확인", "RECORDED_COMPANY_CHANGE", {**ev, "memo": memo})
        return _hold("회원 수정 기록만으로 실제 상호 변경을 확정하지 않음", "COMPANY_REASON_MISSING", "DB_COMPANY", ev)
    if ct == "전속계약 업체변경":
        if "전속계약업체변경" in _k(memo) or "전속계약업체변경" in _k(after):
            return _auto(NORMAL, "전속계약 업체변경 업무 사유 확인(단순 회원 필드 차이와 구분)", "RECORDED_CONTRACT_CHANGE", {**ev, "memo": memo})
        return _hold("소속업체 수정 사유를 DB 원본·업무 기록으로 묶음 검증", "CONTRACT_REASON_MISSING", "DB_COMPANY", ev)
    if ct in ("차량번호변경", "번호변경"):
        plate = lambda s: bool(re.fullmatch(r"(?:[가-힣]{2})?\d{2,3}[가-힣]\d{4}", re.sub(r"\s|호$", "", str(s or ""))))
        if is_placeholder_plate(before) and plate(after):
            return _auto(FIRST, "빈값·임시번호에서 완성 번호 최초등록(대폐차 제외)", "FIRST_SHORT_PLATE", ev)
        if re.search(r"(?:번호판\s*분실|차량번호\s*변경\s*\(분실\))", memo) and plate(before) and plate(after):
            return _auto(NORMAL, "번호판 분실에 따른 실제 번호변경 사유 기록(대폐차 제외)", "PLATE_LOSS_REASON", {**ev, "memo": memo})
        return _hold("번호변경 사유 필요: 차종 변경과 분리", "PLATE_REASON_MISSING", "DB_PLATE_REASON", ev)
    if ct in ("대폐차", "차종변경") and j == NORMAL:
        return _auto(NORMAL, "차종 입력값의 실질 변경 확인(연식 포함)", "VEHICLE_TYPE_CHANGE", ev)
    if ct == "연락처변경":
        old, new = _phone_set(before), _phone_set(after)
        if old and old == new:
            return _auto(NOTATION, "전화번호 숫자 집합 동일: 표기·공백 정리", "PHONE_NOTATION", ev)
        if len(old) == len(new) == 1 and old != new:
            return _auto(NORMAL, "연락처변경 기록의 유효한 단일 전화번호 교체", "PHONE_VALUE_CHANGE", ev)
        return _hold("복수 연락처 정리는 숫자 집합·수정사유로 일괄 검증", "PHONE_MULTI_VALUE", "DB_CONTACT", ev)
    if ct == "성명변경" and re.fullmatch(r"[가-힣]{2,5}", _k(before)) and re.fullmatch(r"[가-힣]{2,5}", _k(after)):
        if re.search(r"성명변경|이름변경", _k(memo)) or _k(after) == _k(_get(row, "name")):
            return _auto(NORMAL, "성명변경 유형과 전후 성명·기록 대상 확인", "RECORDED_NAME_CHANGE", ev)
    if ct == "대표자변경" and "대표자변경" in _k(memo) and _k(before) and _k(after) and _k(before) != _k(after):
        return _auto(NORMAL, "대표자변경 업무 사유 및 전후 대표자 기록 확인", "RECORDED_REPRESENTATIVE", ev)
    if ct == "업종변경" and _k(memo) in ("업종변경", "변경허가"):
        return _auto(NORMAL, "업종변경·변경허가 업무 기록 확인(회원 업종 원문은 수정하지 않음)", "RECORDED_BUSINESS_TYPE", ev)
    if ct == "상속" and "상속" in memo:
        return _auto(NORMAL, "상속 업무 사유 확인(주소변경·폐업과 별도)", "RECORDED_INHERITANCE", ev)
    if ct == "운행정지" and "운행정지명령등록" in _k(memo):
        return _auto(NORMAL, "운행정지명령 등록 사유 확인", "RECORDED_SUSPENSION", ev)
    if ct == "영업소변경" and _k(after) == "본점배치" and "영업소" in _k(memo):
        return _auto(NORMAL, "영업소 본점배치 업무 사유 확인", "RECORDED_OFFICE", ev)
    if ct == "말소/폐차":
        if "번호변경말소" in _k(memo):
            return _auto(EXCLUDED, "번호변경말소는 실제 사업 폐업 실적에서 제외", "PLATE_ONLY_DEREGISTRATION", ev)
        if re.search(r"허가취소|허가실효|감차말소|폐차|운전면허.*취소", _k(memo)):
            return _auto(NORMAL, "말소·폐차 사유 확인: 별도 관련 업무로만 집계, 사업폐업 자동 합산 금지", "RECORDED_DEREGISTRATION", {**ev, "business_closure_inclusion": False})
        return _hold("일반 말소 기록은 폐업 대장과 ID·일자·번호로 일괄 대조", "CLOSURE_LEDGER_BATCH", "DB_CLOSURE", ev)
    return _hold(reason, "OTHER_EVIDENCE_MISSING", "DB_EVENT", ev)
