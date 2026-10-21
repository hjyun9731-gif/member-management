"""화물운송종사자격증 번호 정규화/검증.

형식: 0-00-000000 (숫자 1자리-2자리-6자리, 총 9자리). 선택 입력이며 문자열로 저장한다.
- 빈 값 → "" (미입력)
- 숫자 9자리 → "0-00-000000" 로 하이픈 정규화 (앞자리 0 유지)
- 그 외(9자리 아님/9자리 초과) → ValueError

주의: 이 번호는 '운송종사자 개인'에 귀속되므로 자격증명발급번호(certificate_number),
운전면허번호(driver_license_number)와는 별개의 필드(freight_qualification_number)다.
"""
import re

FIELD = "freight_qualification_number"
_NON_DIGIT = re.compile(r"\D")


def format_fqn(digits: str) -> str:
    return f"{digits[0]}-{digits[1:3]}-{digits[3:]}"


def normalize_fqn(value) -> str:
    """입력값을 '0-00-000000'으로 정규화. 빈 값이면 ''. 형식 불일치면 ValueError."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    digits = _NON_DIGIT.sub("", s)
    if not digits:
        return ""
    if len(digits) != 9:
        raise ValueError("화물운송종사자격증 번호는 숫자 9자리(0-00-000000) 형식이어야 합니다.")
    return format_fqn(digits)


def normalize_in_dict(data: dict, key: str = FIELD) -> dict:
    """dict에 해당 키가 있을 때만 정규화(키가 없으면 건드리지 않음 → 기존 값 보존)."""
    if isinstance(data, dict) and key in data:
        data[key] = normalize_fqn(data.get(key))
    return data


def normalize_in_dict_or_400(data: dict, key: str = FIELD) -> dict:
    """라우터용: 형식 오류면 HTTP 400. 키가 없으면 아무것도 하지 않는다."""
    from fastapi import HTTPException
    try:
        return normalize_in_dict(data, key)
    except ValueError as e:
        raise HTTPException(400, str(e))
