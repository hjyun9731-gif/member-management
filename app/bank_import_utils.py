"""통장 거래내역(농협 인터넷뱅킹 등) 파싱 보조 함수 — DB/FastAPI에 의존하지 않는 순수 함수.

배경(2026-10-09 분석):
  기존 payer 열 후보에 '거래내용/적요/내용'이 들어 있었다. 농협 거래내역에서 '거래내용'은
  입금 수단(예: 스마트뱅킹, 타행이체)이고, 실제 입금자명은 '거래기록사항'에 있다.
  그래서 거래수단·은행명이 입금자명으로 들어가 회원 매칭이 틀어졌다.
"""
from __future__ import annotations

import re
from datetime import datetime, time as dtime

# 입금자명으로 "직접" 읽을 수 있는 열 (우선순위 순)
PAYER_PRIMARY_ALIASES = [
    "입금자명", "입금자", "보낸분", "보낸사람", "송금인", "의뢰인", "예금주",
    "거래기록사항", "기재내용", "받는분통장표시", "성명", "구매자명", "고객명", "회원명",
]
# 위 열이 하나도 없을 때만 쓰는 약한 후보(값이 거래수단/은행명이면 버린다)
PAYER_FALLBACK_ALIASES = ["거래내용", "적요", "내용"]

# 이전 버전의 열 후보 — 이미 반영된 거래의 중복키(legacy fingerprint) 재현용. 수정하지 말 것.
LEGACY_PAYER_ALIASES = ["입금자명", "입금자", "예금주", "성명", "구매자명", "고객명", "회원명", "거래내용", "적요", "내용"]
LEGACY_MEMO_ALIASES = ["메모", "비고", "적요", "거래내용", "내용", "상품명", "결제수단"]

MEMO_ALIASES = ["메모", "이체메모", "비고", "적요", "거래내용", "내용", "상품명", "결제수단"]
AMOUNT_ALIASES = ["입금액", "입금금액", "입금금액원", "입금", "결제금액", "승인금액", "결제액", "거래금액", "금액"]
TIME_ALIASES = ["거래시간", "거래시각", "시간", "입금시간"]
BALANCE_ALIASES = ["거래후잔액", "거래후잔액원", "잔액", "거래후금액"]
BRANCH_ALIASES = ["거래점", "취급점", "거래지점"]

_BANKS = {
    "농협", "nh", "nh농협", "농협은행", "축협", "국민", "kb", "kb국민", "신한", "우리", "하나", "외환", "기업", "ibk",
    "sc", "sc제일", "제일", "씨티", "한국씨티", "카카오뱅크", "카카오", "케이뱅크", "토스", "토스뱅크", "새마을",
    "새마을금고", "우체국", "신협", "수협", "산림조합", "저축은행", "부산", "대구", "경남", "광주", "전북", "제주",
    "산업", "수출입", "금고", "상호금융",
}
_CHANNEL = {
    "인터넷", "인터넷뱅킹", "모바일", "모바일뱅킹", "스마트폰", "스마트뱅킹", "nh스마트뱅킹", "nh스마트폰뱅킹",
    "올원뱅크", "nh올원뱅크", "오픈뱅킹", "타행이체", "타행", "당행", "당행이체", "계좌이체", "이체", "자동이체",
    "펌뱅킹", "cms", "현금", "atm", "cd", "창구", "무통장", "무통장입금", "입금", "폰뱅킹", "텔레뱅킹", "체크카드",
    "지로", "전자금융", "대량이체", "급여", "환급", "이자", "결산이자", "수수료", "타점권", "당좌", "대체",
}


def norm_text(v) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(v or "")).lower()


def _is_bank_or_channel_token(tok: str) -> bool:
    n = norm_text(tok)
    if not n:
        return True
    if n in _CHANNEL or n in _BANKS:
        return True
    if n.endswith("은행") and (n[:-2] in _BANKS or len(n) >= 4 and n[:-2] in {"농협", "국민", "신한", "우리", "하나", "기업", "제일", "씨티", "카카오", "케이", "산업", "수협"}):
        return True
    if n.endswith("뱅크") and n[:-2] in _BANKS:
        return True
    if n.isdigit() and len(n) >= 6:  # 계좌번호/승인번호 조각
        return True
    return False


def clean_payer(value) -> str:
    """입금자 칸 값에서 은행명·거래수단·계좌번호를 걷어내고 실제 이름 부분만 남긴다.

    - '김철수' -> '김철수'
    - '신한 김철수' / '김철수/농협' -> '김철수'
    - '타행이체', 'NH스마트뱅킹', '농협은행' -> '' (입금자 확인 불가)
    """
    s = str(value or "").strip()
    if not s:
        return ""
    toks = [t for t in re.split(r"[\s/|,;]+", s) if t]
    kept = [t for t in toks if not _is_bank_or_channel_token(t)]
    return " ".join(kept).strip()


def looks_like_channel_only(value) -> bool:
    s = str(value or "").strip()
    return bool(s) and clean_payer(s) == ""


def parse_time_text(*values) -> str:
    """거래 시각을 'HH:MM:SS'로. 날짜 셀(datetime)이나 '08:59:43', '085943' 모두 허용. 없으면 ''."""
    for v in values:
        if v is None:
            continue
        if isinstance(v, datetime):
            if (v.hour, v.minute, v.second) != (0, 0, 0):
                return v.strftime("%H:%M:%S")
            continue
        if isinstance(v, dtime):
            return v.strftime("%H:%M:%S")
        s = str(v).strip()
        m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3) or '00'}"
        m = re.fullmatch(r"(\d{2})(\d{2})(\d{2})", s)
        if m:
            return f"{m.group(1)}:{m.group(2)}:{m.group(3)}"
    return ""


def balance_int(v) -> int | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("원", "")
    if not s or s.lower() == "nan":
        return None
    try:
        return int(round(float(s)))
    except Exception:
        return None


# ───────────────────────── 입금자 텍스트 해석 v2 (실제 농협 거래내역 기준) ─────────────────────────
# 실제 '거래기록사항' 유형(2026-10 농협 파일 검증):
#   이름만(57%) / 이름+숫자4자리 / 이름+차량번호 / 차량번호+이름 / 이름(비고) /
#   카드·가맹점 정산형(예: 'KB' + 숫자9, '우' + 숫자9) / 결제대행 영문(예: 'ciderpay')
PLATE_RE = re.compile(r"(\d{2,3})\s*([가-힣])\s*(\d{4})")
_NOTE_WORDS = ("사업소", "지점", "지부", "센터", "상사", "운수", "택배", "물류", "주식회사", "(주)", "㈜", "협회")

# 자동 확정(매칭) 허용 종류. 그 외는 사람이 확인해야 한다.
AUTO_ELIGIBLE_KINDS = {"person", "person_last4", "plate"}


def parse_payer(raw) -> dict:
    """거래기록사항 → {name, plate, last4, extra, kind}.

    kind:
      person        '홍길동'
      person_last4  '홍길동1234' (이름 뒤 숫자 4자리 = 차량/전화 뒤 4자리 추정)
      plate         차량번호가 들어 있음('홍길동12배3456' / '12배3456홍길동')
      name_with_note 이름 + 괄호/비고('홍길동(평창사업소)') — 후보만, 자동 확정 금지
      account_like  카드/계좌번호형('KB123456789') — 회원 입금자로 보지 않음
      service_name  결제대행 영문명('ciderpay')
      empty / unknown
    """
    s = str(raw or "").replace("\u3000", " ").strip()
    info = {"raw": s, "name": "", "plate": "", "last4": "", "extra": [], "kind": "empty"}
    if not s:
        return info
    # 카드/계좌번호형: 한 토큰에 숫자 8자리 이상 + 문자 3자 이하
    for tok in re.split(r"\s+", s):
        digits = len(re.findall(r"\d", tok))
        if digits >= 8 and len(re.sub(r"\d", "", tok)) <= 3:
            info["kind"] = "account_like"
            return info
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 _.\-]*", s):
        info["kind"] = "service_name"
        return info
    work = s
    m = PLATE_RE.search(work)
    if m:
        info["plate"] = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        work = (work[:m.start()] + " " + work[m.end():]).strip()
    # 괄호 안 내용은 별도(비고/대리 입금자 후보)
    paren = re.findall(r"\(([^)]*)\)?", work)
    outside = re.sub(r"\([^)]*\)?", " ", work)
    names = [t for t in re.findall(r"[가-힣]{2,4}", outside) if not _is_bank_or_channel_token(t)]
    extra = [t for p in paren for t in re.findall(r"[가-힣]{2,4}", p) if not _is_bank_or_channel_token(t)]
    info["extra"] = extra
    if names:
        info["name"] = names[0]
    elif extra and not paren:
        info["name"] = extra[0]
    tail = re.fullmatch(r"\s*[가-힣]{2,4}\s*(\d{4})\s*", outside)
    if tail:
        info["last4"] = tail.group(1)
    has_note = bool(paren) or any(w in s for w in _NOTE_WORDS) or len(set(names)) > 1
    if not info["name"] and not info["plate"]:
        info["kind"] = "unknown"
    elif has_note:
        info["kind"] = "name_with_note"
    elif info["plate"]:
        info["kind"] = "plate"
    elif info["last4"]:
        info["kind"] = "person_last4"
    else:
        info["kind"] = "person"
    return info
