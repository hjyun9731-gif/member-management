"""연락처(전화/휴대전화) 파서 - DB/FastAPI에 의존하지 않는 순수 함수 모음.

목적
- 한 칸에 여러 번호가 섞여 저장된 값(예: ``010-3428-2714 010-6740-2714``)을 분리해 보여준다.
- **원본은 절대 변경하지 않는다.** 이 모듈은 값을 읽어 해석만 하고, 해석이 불명확하면
  ``needs_review`` 로 분류해 원문을 그대로 남긴다.
- 어떤 번호가 '대표' 연락처인지는 판단하지 않는다(순서만 보존).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

# 휴대전화: 010/011/016/017/018/019 + 3~4자리 + 4자리
_MOBILE = r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}"
# 유선: 02 또는 03x~06x + 3~4자리 + 4자리
_LANDLINE = r"(?:02|0[3-6]\d)[-.\s]?\d{3,4}[-.\s]?\d{4}"
_PHONE_RE = re.compile(rf"(?<!\d)(?:{_MOBILE}|{_LANDLINE})(?!\d)")
_SEPARATORS = re.compile(r"[\s,;/|·•()\[\]\-.~]+")

# 판정 상태
EMPTY = "empty"                  # 값 없음
OK = "ok"                        # 번호 1개, 형식 정상
MULTIPLE = "multiple"            # 서로 다른 번호 2개 이상 (DQ-01 A)
REPEATED = "repeated"            # 같은 번호가 반복 입력 (DQ-01 B)
INVALID = "invalid"              # 번호로 해석 불가/자릿수 오류/부가문구 포함 (DQ-03 D)


@dataclass
class PhoneNumber:
    digits: str
    formatted: str
    kind: str  # mobile / landline


@dataclass
class ParsedContact:
    raw: str
    numbers: List[PhoneNumber] = field(default_factory=list)   # 서로 다른 번호만, 입력 순서 유지
    status: str = EMPTY
    repeated: bool = False       # 동일 번호 반복 여부
    residual: str = ""           # 번호 이외에 남은 문구 (있으면 확인 필요)
    needs_review: bool = False

    @property
    def digits_list(self) -> List[str]:
        return [n.digits for n in self.numbers]


def digits_only(value) -> str:
    return re.sub(r"\D", "", unicodedata.normalize("NFKC", str(value or "")))


def format_phone(digits: str) -> str:
    d = digits
    if len(d) == 11:
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"
    if len(d) == 10:
        if d.startswith("02"):
            return f"{d[:2]}-{d[2:6]}-{d[6:]}"
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    if len(d) == 9 and d.startswith("02"):
        return f"{d[:2]}-{d[2:5]}-{d[5:]}"
    return d


def parse_contact(value) -> ParsedContact:
    """연락처 칸 한 개를 해석한다. 값을 바꾸지 않고 해석 결과만 돌려준다."""
    raw = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", raw)
    if not text.strip():
        return ParsedContact(raw=raw, status=EMPTY)

    found = []
    for m in _PHONE_RE.finditer(text):
        d = digits_only(m.group(0))
        kind = "mobile" if d.startswith("01") else "landline"
        found.append((m.span(), PhoneNumber(d, format_phone(d), kind)))

    # 번호로 인식된 구간을 지운 뒤 남는 문구 확인 (구분자는 무시)
    residual_text = text
    for (s, e), _ in sorted(found, key=lambda x: -x[0][0]):
        residual_text = residual_text[:s] + " " + residual_text[e:]
    residual = _SEPARATORS.sub(" ", residual_text).strip()

    seen, unique = set(), []
    for _, num in found:
        if num.digits not in seen:
            seen.add(num.digits)
            unique.append(num)
    repeated = len(found) > len(unique)

    if not found:
        return ParsedContact(raw=raw, status=INVALID, residual=residual or text.strip(), needs_review=True)

    if residual:
        status, review = INVALID, True      # 번호 외 문구 포함 -> 자동 해석 금지, 원문 유지
    elif len(unique) >= 2:
        status, review = MULTIPLE, False
    elif repeated:
        status, review = REPEATED, False
    else:
        status, review = OK, False
    return ParsedContact(raw=raw, numbers=unique, status=status, repeated=repeated,
                         residual=residual, needs_review=review)


def split_numbers(value) -> List[str]:
    """화면 표시용: 서로 다른 번호를 하이픈 형식으로 나눠 돌려준다 (불명확하면 빈 리스트)."""
    p = parse_contact(value)
    if p.status in (MULTIPLE, REPEATED, OK) and not p.residual:
        return [n.formatted for n in p.numbers]
    return []


def same_number_across(*values) -> List[str]:
    """여러 칸(예: phone, mobile)에 공통으로 들어 있는 번호(digits)를 돌려준다."""
    sets = [set(parse_contact(v).digits_list) for v in values if str(v or "").strip()]
    if len(sets) < 2:
        return []
    common = set.intersection(*sets)
    return sorted(common)
