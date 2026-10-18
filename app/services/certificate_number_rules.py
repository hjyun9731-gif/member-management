"""자격증명발급번호 비교 규칙 - DB/ORM에 의존하지 않는 순수 함수 모음.

원칙
- 같은 번호의 판단 기준은 하나다: ``YY-N`` 표준형 (``26-085`` == ``26-85`` == ``2026-0085``
  == ``26 – 85`` == ``26-85(운전자명)``).
- 이 모듈은 SQLAlchemy/FastAPI를 import하지 않는다. 그래서 의존성이 없는 환경에서도
  규칙만 단독으로 테스트할 수 있다 (tests/test_certificate_number_rules.py).
- ``canonical_number``는 app.crud.normalize_certificate_number와 같은 규칙의 사본이다.
  두 구현이 어긋나지 않는지는 위 테스트가 crud.py 원본 소스를 읽어 직접 비교한다.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

CERT_HYPHENS = ("‐", "‑", "‒", "–", "—", "−", "﹣", "－")

# 발급번호만 먼저 예약(채번)되고 아직 대상자가 입력되지 않은 발급대장 행의 표시명
UNASSIGNED_NAME = "(대상미입력)"

SOURCE_LABELS = {
    "candidates": "예정자",
    "license_holders": "회원",
    "transfer_ledger": "양도양수대장",
    "closures": "폐업현황",
    "certificate_ledger": "자격증명발급대장",
}


def canonical_number(value) -> str:
    """자격증명번호를 ``YY-N`` 표준형으로 만든다. 번호 형식이 아니면 빈 문자열."""
    if value is None:
        return ""
    v = unicodedata.normalize("NFKC", str(value)).strip()
    # '26-385(강동규)'처럼 괄호 주석이 붙은 표기는 주석을 떼고 순수 번호만 비교한다.
    v = re.sub(r"\([^)]*\)\s*$", "", v).strip()
    for ch in CERT_HYPHENS:
        v = v.replace(ch, "-")
    v = re.sub(r"\s*-\s*", "-", v)
    v = re.sub(r"\s+", "", v)
    m = re.fullmatch(r"(\d{2}|\d{4})-(\d{1,6})", v)
    if not m:
        return ""
    yy = m.group(1)[-2:]
    try:
        num = str(int(m.group(2)))
    except Exception:
        return ""
    return f"{yy}-{num}"


def number_key(value) -> Optional[tuple]:
    """정렬/비교용 (연도, 순번) 정수 키. 번호 형식이 아니면 None."""
    cert = canonical_number(value)
    if not cert:
        return None
    yy, no = cert.split("-", 1)
    return int(yy), int(no)


def sequence_digits(value) -> str:
    """DB에서 후보 행을 좁히는 LIKE 조건용 숫자열 (예: 26-085 -> '85').

    앞자리 0 표기(0085)와 괄호 주석 등 어떤 저장 형태에도 이 숫자열은 부분 문자열로 들어 있다.
    """
    key = number_key(value)
    return str(key[1]) if key else ""


def norm_vehicle(value) -> str:
    """차량번호 비교용: 공백/하이픈 표기 차이는 같은 차량으로 본다."""
    return re.sub(r"[\s-]+", "", str(value or "")).strip()


def same_subject(name_a, vehicle_a, name_b, vehicle_b) -> bool:
    """성명과 차량번호가 모두 비어 있지 않고 모두 같을 때만 같은 대상으로 본다."""
    na, nb = str(name_a or "").strip(), str(name_b or "").strip()
    va, vb = norm_vehicle(vehicle_a), norm_vehicle(vehicle_b)
    return bool(na and nb and va and vb and na == nb and va == vb)


def is_unassigned_reservation(hit_name, hit_vehicle, subject_name, subject_vehicle) -> bool:
    """대상자가 연결되지 않은 발급대장 예약 행이 이 저장 대상의 예약인지 판단한다.

    '발급번호 부여' 버튼은 성명+차량번호가 입력된 상태에서만 번호를 채번하고 이 값을 예약 행에
    남긴다. 그 뒤 저장 직전에 성명/차량번호 중 한쪽을 고쳐 쓰는 일이 있으므로,
    예약 행은 이름이 없거나, 이름이 같거나, 차량번호가 같으면 같은 대상의 예약으로 본다.
    """
    hn = str(hit_name or "").strip()
    if not hn or hn == UNASSIGNED_NAME:
        return True
    sn = str(subject_name or "").strip()
    if sn and hn == sn:
        return True
    hv, sv = norm_vehicle(hit_vehicle), norm_vehicle(subject_vehicle)
    return bool(hv and sv and hv == sv)


@dataclass
class SubjectContext:
    """지금 저장하려는 대상과 '같은 대상'으로 취급할 레코드들.

    같은 사람의 번호는 예정자 -> 회원 전환, 양도양수대장, 폐업현황, 발급대장에 그대로 복사되어
    여러 곳에 존재한다. 이 연결고리(id)로 이어진 레코드는 중복이 아니다.
    """
    candidate_ids: set = field(default_factory=set)
    member_ids: set = field(default_factory=set)
    transfer_ids: set = field(default_factory=set)
    closure_ids: set = field(default_factory=set)
    ledger_ids: set = field(default_factory=set)
    name: str = ""
    vehicle_number: str = ""


def is_exempt(hit: dict, ctx: SubjectContext) -> bool:
    """찾은 사용처(hit)가 저장 대상 본인(또는 본인과 연결된 레코드)이면 True."""
    src, rid = hit.get("source"), hit.get("id")
    member_refs = [hit.get(k) for k in ("member_id", "transferee_member_id")]

    if src == "candidates":
        if rid in ctx.candidate_ids:
            return True
        if hit.get("member_id") and hit["member_id"] in ctx.member_ids:
            return True
    elif src == "license_holders":
        if rid in ctx.member_ids:
            return True
        if hit.get("candidate_id") and hit["candidate_id"] in ctx.candidate_ids:
            return True
    elif src == "transfer_ledger":
        if rid in ctx.transfer_ids:
            return True
        if any(m and m in ctx.member_ids for m in member_refs):
            return True
    elif src == "closures":
        if rid in ctx.closure_ids:
            return True
        if any(m and m in ctx.member_ids for m in member_refs):
            return True
    elif src == "certificate_ledger":
        if rid in ctx.ledger_ids:
            return True
        cid, mid = hit.get("candidate_id"), hit.get("member_id")
        if cid and cid in ctx.candidate_ids:
            return True
        if mid and mid in ctx.member_ids:
            return True
        if not cid and not mid and is_unassigned_reservation(
            hit.get("name"), hit.get("vehicle_number"), ctx.name, ctx.vehicle_number
        ):
            return True

    # 연결고리가 없어도 성명+차량번호가 모두 같으면 같은 대상의 다른 기록으로 본다
    # (기존 certificate_number_reservation._same_subject 와 같은 기준).
    return same_subject(hit.get("name"), hit.get("vehicle_number"), ctx.name, ctx.vehicle_number)


def conflicts(hits: list, ctx: SubjectContext) -> list:
    """사용처 목록에서 저장 대상 본인을 뺀, 진짜 중복만 반환한다."""
    return [h for h in hits if not is_exempt(h, ctx)]


def describe_hit(hit: dict) -> str:
    """사람이 읽는 한 줄 설명. 예: '예정자 홍길동 / 강원80자1234 / 춘천시'."""
    label = SOURCE_LABELS.get(hit.get("source"), str(hit.get("source") or ""))
    parts = [str(hit.get("name") or "").strip(), str(hit.get("vehicle_number") or "").strip(),
             str(hit.get("region") or "").strip()]
    body = " / ".join(p for p in parts if p)
    return f"{label} {body}".strip() if body else label


DUPLICATE_MESSAGE = "이미 등록된 자격증명발급번호입니다."


def duplicate_message(cert: str, hits: list) -> str:
    """경고 문구 + 기존 등록 대상 정보."""
    if not hits:
        return ""
    extra = f" 외 {len(hits) - 1}건" if len(hits) > 1 else ""
    return f"{DUPLICATE_MESSAGE} ({cert} → {describe_hit(hits[0])}{extra})"


def identity_keys(hit: dict) -> list:
    """같은 사람의 기록끼리 묶기 위한 연결 키 (기존 중복 보고서용)."""
    src, rid = hit.get("source"), hit.get("id")
    keys = []
    if src == "candidates":
        keys.append(("c", rid))
        if hit.get("member_id"):
            keys.append(("m", hit["member_id"]))
    elif src == "license_holders":
        keys.append(("m", rid))
        if hit.get("candidate_id"):
            keys.append(("c", hit["candidate_id"]))
    elif src == "transfer_ledger":
        keys.append(("t", rid))
        for k in ("member_id", "transferee_member_id"):
            if hit.get(k):
                keys.append(("m", hit[k]))
    elif src == "closures":
        keys.append(("x", rid))
        for k in ("member_id", "transferee_member_id"):
            if hit.get(k):
                keys.append(("m", hit[k]))
    elif src == "certificate_ledger":
        keys.append(("l", rid))
        if hit.get("candidate_id"):
            keys.append(("c", hit["candidate_id"]))
        if hit.get("member_id"):
            keys.append(("m", hit["member_id"]))
    return keys


def group_subjects(hits: list) -> list:
    """같은 번호를 쓰는 기록들을 '같은 대상' 단위로 묶는다 (연결 id 또는 성명+차량번호 일치).

    반환: 대상별 hit 목록의 리스트. 길이가 2 이상이면 서로 다른 대상이 같은 번호를 쓰는 것이다.
    """
    n = len(hits)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    seen = {}
    for i, hit in enumerate(hits):
        for key in identity_keys(hit):
            if key in seen:
                union(i, seen[key])
            else:
                seen[key] = i
    for i in range(n):
        for j in range(i + 1, n):
            if same_subject(hits[i].get("name"), hits[i].get("vehicle_number"),
                            hits[j].get("name"), hits[j].get("vehicle_number")):
                union(i, j)

    groups: dict = {}
    for i, hit in enumerate(hits):
        groups.setdefault(find(i), []).append(hit)
    return list(groups.values())
