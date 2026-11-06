"""데이터 정합성 점검(읽기 전용).

원칙
- 이 모듈은 SELECT 만 수행한다. INSERT/UPDATE/DELETE/DDL 및 commit 을 호출하지 않는다.
- 같은 이름만으로 동일인을 판정하지 않는다. 가족/대리인이 번호를 공유하거나, 같은 차량이
  과거 양도자와 현재 양수자에게 나타나는 것은 정상 업무이력일 수 있어 '확인 필요'로만 분류한다.
- 주민등록번호 등 민감정보는 결과에 마스킹해서 담는다.
"""
from __future__ import annotations

import re
from datetime import date
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app import models
from app.services import contact_parser as cp
from app.services.certificate_number_rules import canonical_number
from app.certificate_ledger_models import CertificateIssuanceLedger

CONFIRMED = "오류 확정"
REVIEW = "확인 필요"

CHECKS: Dict[str, str] = {
    "DQ-01": "복수 연락처",
    "DQ-02": "연락처 중복",
    "DQ-03": "연락처 형식",
    "DQ-04": "회원 중복 가능성",
    "DQ-05": "관리번호 충돌",
    "DQ-06": "관리번호 형식",
    "DQ-07": "등록유형 불일치",
    "DQ-08": "양도양수 연결",
    "DQ-09": "자격증명 연결",
    "DQ-10": "가입정보",
    "DQ-11": "날짜 형식",
    "DQ-12": "폐업·양도 상태",
    "DQ-13": "대리인",
    "DQ-14": "등록예정자 연결",
    "DQ-15": "고아 레코드",
}

_BATCH = 2000


def mask_rrn(value) -> str:
    d = re.sub(r"\D", "", str(value or ""))
    if len(d) >= 7:
        return f"{d[:6]}-{d[6]}******"
    return "(마스킹)" if d else ""


def _batched(q) -> Iterable:
    """대량 테이블을 한꺼번에 메모리에 올리지 않도록 yield_per 로 읽는다."""
    return q.yield_per(_BATCH)


def _f(code, severity, table, rec, field, current, message, recommendation, **extra) -> dict:
    d = {
        "code": code, "type": CHECKS.get(code, code), "severity": severity,
        "table": table, "record_id": rec.get("id"), "name": rec.get("name") or "",
        "management_number": rec.get("management_number") or "",
        "vehicle_number": rec.get("vehicle_number") or "",
        "category": rec.get("category") or "", "region": rec.get("region") or "",
        "status": rec.get("status") or "",
        "field": field, "current_value": current, "message": message,
        "recommendation": recommendation,
    }
    d.update(extra)
    return d


def _row(**kw) -> dict:
    return kw


# ───────────────────────── DQ-01/02/03 연락처 ─────────────────────────
_CONTACT_SOURCES = [
    ("license_holders", models.LicenseHolder, ["phone", "mobile", "agent_mobile"]),
    ("candidates", models.Candidate, ["phone", "mobile"]),
    ("transfer_ledger", models.TransferLedger, ["phone", "mobile", "agent_mobile"]),
    ("closures", models.Closure, ["phone", "mobile", "agent_mobile"]),
]


def check_contacts(db: Session) -> List[dict]:
    out: List[dict] = []
    for tname, model, cols in _CONTACT_SOURCES:
        fields = [getattr(model, c) for c in cols if hasattr(model, c)]
        names = [c for c in cols if hasattr(model, c)]
        base = [model.id, model.name if hasattr(model, "name") else model.transferee if hasattr(model, "transferee") else model.id,
                model.vehicle_number, model.region]
        mgmt = model.management_number if hasattr(model, "management_number") else None
        cat = model.category if hasattr(model, "category") else None
        q = db.query(*base, *( [mgmt] if mgmt is not None else []), *([cat] if cat is not None else []), *fields)
        q = q.filter(model.deleted_at.is_(None))
        for row in _batched(q):
            vals = list(row)
            rec = {"id": vals[0], "name": vals[1], "vehicle_number": vals[2], "region": vals[3]}
            i = 4
            if mgmt is not None:
                rec["management_number"] = vals[i]; i += 1
            if cat is not None:
                rec["category"] = vals[i]; i += 1
            data = dict(zip(names, vals[i:]))
            parsed = {k: cp.parse_contact(v) for k, v in data.items()}
            for fname, p in parsed.items():
                raw = data[fname]
                if p.status == cp.MULTIPLE:
                    out.append(_f("DQ-01", CONFIRMED, tname, rec, fname, raw,
                                  f"한 칸에 서로 다른 번호 {len(p.numbers)}개가 함께 저장됨",
                                  "번호를 각각 분리 입력(원본 보존 후 수동 확인)",
                                  parsed_numbers=[n.formatted for n in p.numbers]))
                elif p.status == cp.REPEATED:
                    out.append(_f("DQ-01", CONFIRMED, tname, rec, fname, raw,
                                  "같은 번호가 한 칸에 반복 입력됨", "중복 표기 정리(수동 확인)",
                                  parsed_numbers=[n.formatted for n in p.numbers]))
                elif p.status == cp.INVALID:
                    out.append(_f("DQ-03", REVIEW, tname, rec, fname, raw,
                                  "번호로 해석할 수 없거나 번호 외 문구/잘못된 자릿수 포함", "원문 확인 후 수동 정정",
                                  residual=p.residual))
            # DQ-02: 같은 레코드의 phone/mobile 에 같은 번호 (확정 아님 → 중복 가능성)
            if "phone" in data and "mobile" in data:
                common = cp.same_number_across(data["phone"], data["mobile"])
                if common:
                    out.append(_f("DQ-02", REVIEW, tname, rec, "phone/mobile", f"{data['phone']} | {data['mobile']}",
                                  "전화번호와 핸드폰 칸에 같은 번호가 있음(중복 가능성)", "한 칸만 남길지 담당자가 판단",
                                  parsed_numbers=[cp.format_phone(d) for d in common]))
    return out


# ───────────────────────── 관리번호 ─────────────────────────
_MGMT_OK = re.compile(r"^(신|양)\d{2}-\d+$")


def normalize_mgmt(value) -> str:
    """관리번호 비교용 정규화(비교에만 사용, 저장값을 바꾸지 않는다)."""
    s = str(value or "").strip()
    s = re.sub(r"[\s.·,;]+$", "", s)       # 끝의 마침표/공백/구분자
    s = re.sub(r"\s+", "", s)
    return s


def _members(db: Session):
    return db.query(models.LicenseHolder.id, models.LicenseHolder.name, models.LicenseHolder.management_number,
                    models.LicenseHolder.vehicle_number, models.LicenseHolder.category, models.LicenseHolder.region,
                    models.LicenseHolder.status, models.LicenseHolder.registration_type,
                    models.LicenseHolder.candidate_id, models.LicenseHolder.transfer_ledger_id,
                    models.LicenseHolder.closure_id, models.LicenseHolder.certificate_number,
                    models.LicenseHolder.resident_number, models.LicenseHolder.membership_status,
                    models.LicenseHolder.membership_date, models.LicenseHolder.approval_date,
                    models.LicenseHolder.agent_name).filter(models.LicenseHolder.deleted_at.is_(None))


_MCOLS = ["id", "name", "management_number", "vehicle_number", "category", "region", "status",
          "registration_type", "candidate_id", "transfer_ledger_id", "closure_id", "certificate_number",
          "resident_number", "membership_status", "membership_date", "approval_date", "agent_name"]


def _member_dicts(db: Session) -> List[dict]:
    return [dict(zip(_MCOLS, r)) for r in _batched(_members(db))]


def check_mgmt_numbers(db: Session, members: List[dict]) -> List[dict]:
    out: List[dict] = []
    by_norm: Dict[str, List[dict]] = defaultdict(list)
    for m in members:
        mn = m["management_number"]
        if not mn:
            continue
        by_norm[normalize_mgmt(mn)].append(m)
        # DQ-06 형식
        if mn != mn.strip() or re.search(r"[.·,;\s]$", mn):
            out.append(_f("DQ-06", CONFIRMED, "license_holders", m, "management_number", mn,
                          "관리번호 끝/앞에 공백·마침표 등 불필요한 문자가 있음", "정규화 시 다른 회원과 충돌하는지 확인 후 수동 정정"))
        elif mn[:1] in ("신", "양") and not _MGMT_OK.match(mn):
            out.append(_f("DQ-06", REVIEW, "license_holders", m, "management_number", mn,
                          "신/양 관리번호 표준형(신YY-N, 양YY-N)이 아님", "원본 확인"))
    # DQ-05 정규화 번호 기준 충돌
    for norm, items in by_norm.items():
        if len(items) < 2:
            continue
        actives = [i for i in items if (i["status"] or "active") == "active"]
        exact = len({i["management_number"] for i in items}) == 1
        sev = CONFIRMED if len(actives) >= 2 and exact else REVIEW
        why = ("서로 다른 활성 회원이 같은 관리번호를 사용 중" if sev == CONFIRMED else
               "같은 번호(정규화 기준)를 둘 이상의 회원 기록이 점유 — 폐업/표기차이 여부 확인 필요")
        ids = [i["id"] for i in items]
        for i in items:
            out.append(_f("DQ-05", sev, "license_holders", i, "management_number", i["management_number"], why,
                          "관리번호 진단 화면에서 점유 기록과 연결 ID 확인 후 관리자 승인으로 정정", conflict_ids=ids))
    return out


def diagnose_mgmt_number(db: Session, number: str) -> dict:
    """관리번호 한 건의 점유 현황(읽기 전용). 삭제 여부와 폐업 상태를 구분해서 보여준다."""
    norm = normalize_mgmt(number)
    variants = {number, norm, norm + ".", norm + " "}
    L, C, T, Z = models.LicenseHolder, models.Candidate, models.TransferLedger, models.Closure

    def status_of(r):
        return "삭제됨" if r.deleted_at else (getattr(r, "status", None) or "활성")

    holders = []
    for r in db.query(L).filter(L.management_number.in_(variants)).all():
        holders.append({"table": "license_holders", "id": r.id, "management_number": r.management_number,
                        "name": r.name, "vehicle_number": r.vehicle_number, "status": status_of(r),
                        "deleted": bool(r.deleted_at), "registration_type": r.registration_type,
                        "approval_date": r.approval_date, "certificate_number": r.certificate_number,
                        "candidate_id": r.candidate_id, "transfer_ledger_id": r.transfer_ledger_id,
                        "closure_id": r.closure_id,
                        "blocks_new_registration": r.deleted_at is None})
    for r in db.query(C).filter(C.management_number.in_(variants)).all():
        holders.append({"table": "candidates", "id": r.id, "management_number": r.management_number,
                        "name": r.name, "vehicle_number": r.vehicle_number,
                        "status": "삭제됨" if r.deleted_at else ("등록완료" if r.is_registered else "예정"),
                        "deleted": bool(r.deleted_at), "member_id": r.member_id,
                        "certificate_number": r.certificate_number,
                        "blocks_new_registration": r.deleted_at is None})
    for r in db.query(T).filter(T.management_number.in_(variants)).all():
        holders.append({"table": "transfer_ledger", "id": r.id, "management_number": r.management_number,
                        "name": r.transferee, "vehicle_number": r.vehicle_number, "status": status_of(r),
                        "deleted": bool(r.deleted_at), "transferor": r.transferor,
                        "transferor_member_id": r.transferor_member_id,
                        "transferee_member_id": r.transferee_member_id, "member_id": r.member_id,
                        "process_date": r.process_date,
                        "blocks_new_registration": False})
    for r in db.query(Z).filter(Z.original_management_number.in_(variants)).all():
        holders.append({"table": "closures", "id": r.id, "management_number": r.original_management_number,
                        "name": r.name, "vehicle_number": r.vehicle_number, "status": status_of(r),
                        "deleted": bool(r.deleted_at), "closure_type": r.closure_type,
                        "transferee_member_id": r.transferee_member_id,
                        "transfer_ledger_id": r.transfer_ledger_id, "member_id": r.member_id,
                        "blocks_new_registration": False})
    blockers = [h for h in holders if h.get("blocks_new_registration")]
    causes = []
    for h in blockers:
        if h["table"] == "license_holders" and h.get("status") == "closed":
            causes.append(f"회원 #{h['id']} 가 폐업(closed) 상태이지만 삭제(deleted_at)되지 않아 번호를 계속 점유 "
                          f"— '폐업'과 '삭제'는 다른 상태이므로 자동 재사용 불가")
        elif h["table"] == "license_holders":
            causes.append(f"활성 회원 #{h['id']} 가 번호를 사용 중")
        elif h["table"] == "candidates":
            causes.append(f"예정자 #{h['id']} 가 번호를 점유 중(회원과 별도 테이블)")
    return {
        "number": number, "normalized": norm,
        "normalized_differs": norm != number,
        "records": holders,
        "blocking_count": len(blockers),
        "causes": causes,
        "note": "읽기 전용 진단입니다. 이 화면은 어떤 기록도 수정/삭제하지 않습니다.",
    }


# ───────────────────────── DQ-04/07/08/12/14/15 연결·중복 ─────────────────────────
def check_links(db: Session, members: List[dict]) -> List[dict]:
    out: List[dict] = []
    L, C, T, Z = models.LicenseHolder, models.Candidate, models.TransferLedger, models.Closure
    mem_all = {r[0]: r[1] for r in db.query(L.id, L.deleted_at)}          # id -> deleted_at
    cand_all = {r[0]: r for r in db.query(C.id, C.deleted_at, C.is_registered, C.member_id)}
    tl_all = {r[0]: r[1] for r in db.query(T.id, T.deleted_at)}
    cl_all = {r[0]: r[1] for r in db.query(Z.id, Z.deleted_at)}

    def mstate(i):
        if i is None:
            return None
        if i not in mem_all:
            return "없음"
        return "삭제됨" if mem_all[i] is not None else "정상"

    mem_by_id = {m["id"]: m for m in members}
    # 회원 → 예정자/대장/폐업 참조
    for m in members:
        if m["candidate_id"] and m["candidate_id"] not in cand_all:
            out.append(_f("DQ-15", CONFIRMED, "license_holders", m, "candidate_id", m["candidate_id"],
                          "존재하지 않는 예정자를 참조", "연결 해제 여부 수동 확인"))
        elif m["candidate_id"] and cand_all[m["candidate_id"]][1] is not None:
            out.append(_f("DQ-15", REVIEW, "license_holders", m, "candidate_id", m["candidate_id"],
                          "삭제 처리된 예정자를 참조", "예정자 복구 또는 연결 정정 검토"))
        if m["transfer_ledger_id"] and m["transfer_ledger_id"] not in tl_all:
            out.append(_f("DQ-08", CONFIRMED, "license_holders", m, "transfer_ledger_id", m["transfer_ledger_id"],
                          "존재하지 않는 양도양수대장을 참조", "연결 정정"))
        elif m["transfer_ledger_id"] and tl_all[m["transfer_ledger_id"]] is not None:
            out.append(_f("DQ-08", REVIEW, "license_holders", m, "transfer_ledger_id", m["transfer_ledger_id"],
                          "삭제 처리된 양도양수대장을 참조", "활성 대장으로 재연결 필요 여부 검토"))
        if m["closure_id"] and m["closure_id"] not in cl_all:
            out.append(_f("DQ-15", CONFIRMED, "license_holders", m, "closure_id", m["closure_id"],
                          "존재하지 않는 폐업현황을 참조", "연결 정정"))
        # DQ-07 등록유형 vs 관리번호 접두어
        mn, rt = (m["management_number"] or ""), (m["registration_type"] or "")
        if rt == "양도양수" and mn.startswith("신"):
            out.append(_f("DQ-07", REVIEW, "license_holders", m, "registration_type", f"{rt} / {mn}",
                          "양도양수로 등록됐지만 관리번호가 '신'으로 시작", "양도양수대장과 대조 후 정정 여부 판단"))
        elif rt == "신규" and mn.startswith("양"):
            out.append(_f("DQ-07", REVIEW, "license_holders", m, "registration_type", f"{rt} / {mn}",
                          "신규로 등록됐지만 관리번호가 '양'으로 시작", "예정자/양도양수대장 대조 후 정정 여부 판단"))
        if rt == "양도양수" and not m["transfer_ledger_id"]:
            out.append(_f("DQ-08", REVIEW, "license_holders", m, "transfer_ledger_id", None,
                          "양도양수 등록 회원인데 양도양수대장 연결이 없음", "대장 기록 존재 여부 확인"))
        # DQ-12 상태 vs 폐업 연결
        if m["status"] == "closed" and not m["closure_id"]:
            out.append(_f("DQ-12", REVIEW, "license_holders", m, "closure_id", None,
                          "폐업 상태 회원인데 폐업현황 연결이 없음", "폐업현황 대조"))
        if m["status"] == "active" and m["closure_id"] and cl_all.get(m["closure_id"], 1) is None:
            out.append(_f("DQ-12", REVIEW, "license_holders", m, "closure_id", m["closure_id"],
                          "활성 상태 회원인데 활성 폐업현황과 연결되어 있음", "상태 또는 폐업기록 확인"))

    # 양도양수대장
    for r in db.query(T.id, T.management_number, T.transferee, T.transferor, T.vehicle_number, T.region,
                      T.member_id, T.transferor_member_id, T.transferee_member_id).filter(T.deleted_at.is_(None)):
        rec = {"id": r[0], "management_number": r[1], "name": r[2], "vehicle_number": r[4], "region": r[5]}
        for fname, val in (("member_id", r[6]), ("transferor_member_id", r[7]), ("transferee_member_id", r[8])):
            st = mstate(val)
            if st == "없음":
                out.append(_f("DQ-15", CONFIRMED, "transfer_ledger", rec, fname, val,
                              "존재하지 않는 회원을 참조", "연결 정정"))
            elif st == "삭제됨":
                out.append(_f("DQ-08", REVIEW, "transfer_ledger", rec, fname, val,
                              "삭제 처리된 회원을 참조", "활성 회원으로 재연결 필요 여부 검토"))
        if r[6] and r[8] and r[6] != r[8]:
            out.append(_f("DQ-08", REVIEW, "transfer_ledger", rec, "member_id/transferee_member_id",
                          f"{r[6]} / {r[8]}", "하위호환용 member_id 와 양수자 회원 ID 가 서로 다름", "어느 쪽이 맞는지 확인"))
        if r[8] and r[8] in mem_by_id and mem_by_id[r[8]]["transfer_ledger_id"] not in (None, r[0]):
            out.append(_f("DQ-08", REVIEW, "transfer_ledger", rec, "transferee_member_id", r[8],
                          f"양수자 회원 #{r[8]} 이 다른 양도양수대장(#{mem_by_id[r[8]]['transfer_ledger_id']})을 가리킴",
                          "대장 연결 확인"))
    # 폐업현황
    for r in db.query(Z.id, Z.name, Z.vehicle_number, Z.region, Z.member_id, Z.transferee_member_id,
                      Z.transfer_ledger_id).filter(Z.deleted_at.is_(None)):
        rec = {"id": r[0], "name": r[1], "vehicle_number": r[2], "region": r[3]}
        for fname, val in (("member_id", r[4]), ("transferee_member_id", r[5])):
            if mstate(val) == "없음":
                out.append(_f("DQ-15", CONFIRMED, "closures", rec, fname, val, "존재하지 않는 회원을 참조", "연결 정정"))
        if r[6] and r[6] not in tl_all:
            out.append(_f("DQ-15", CONFIRMED, "closures", rec, "transfer_ledger_id", r[6],
                          "존재하지 않는 양도양수대장을 참조", "연결 정정"))
    # DQ-14 예정자
    for cid, st in cand_all.items():
        _, deleted, is_reg, member_id = st
        if deleted is not None:
            continue
        if is_reg and not member_id:
            out.append(_f("DQ-14", CONFIRMED, "candidates", {"id": cid}, "member_id", None,
                          "등록완료 상태인데 연결된 회원 ID가 없음", "회원과 대조 후 연결"))
        elif is_reg and mstate(member_id) != "정상":
            out.append(_f("DQ-14", CONFIRMED, "candidates", {"id": cid}, "member_id", member_id,
                          f"등록완료 예정자의 회원 연결이 {mstate(member_id)}", "회원과 대조 후 연결 정정"))
        elif not is_reg and member_id and mstate(member_id) == "정상":
            out.append(_f("DQ-14", REVIEW, "candidates", {"id": cid}, "is_registered", False,
                          "회원 ID는 있는데 등록완료 상태가 아님", "상태 확인"))

    # DQ-04: 이름만으로는 판정하지 않는다. 같은 주민등록번호 또는 같은 차량번호의 '활성' 회원이 2명 이상일 때만 확인 필요
    by_rrn: Dict[str, List[dict]] = defaultdict(list)
    by_veh: Dict[str, List[dict]] = defaultdict(list)
    for m in members:
        if (m["status"] or "active") != "active":
            continue
        d = re.sub(r"\D", "", m["resident_number"] or "")
        if len(d) == 13:
            by_rrn[d].append(m)
        v = re.sub(r"[\s-]+", "", m["vehicle_number"] or "")
        if v:
            by_veh[v].append(m)
    for key, items in by_rrn.items():
        if len(items) > 1:
            for m in items:
                out.append(_f("DQ-04", REVIEW, "license_holders", m, "resident_number", mask_rrn(key),
                              "같은 주민등록번호의 활성 회원이 2명 이상", "신원·차량·인가정보·연결 이력 대조 후 관리자가 남길 회원 선택",
                              duplicate_ids=[i["id"] for i in items]))
    for key, items in by_veh.items():
        if len(items) > 1:
            for m in items:
                out.append(_f("DQ-04", REVIEW, "license_holders", m, "vehicle_number", m["vehicle_number"],
                              "같은 차량번호의 활성 회원이 2명 이상 (양도양수 후 정상 이력일 수도 있음)",
                              "양도양수대장·폐업현황과 대조", duplicate_ids=[i["id"] for i in items]))
    return out


# ───────────────────────── DQ-09 자격증명 ─────────────────────────
def check_certificates(db: Session, members: List[dict]) -> List[dict]:
    out: List[dict] = []
    L, C = models.LicenseHolder, models.Candidate
    logs: Dict[str, List] = defaultdict(list)
    for r in _batched(db.query(models.CertificateNumberLog.id, models.CertificateNumberLog.certificate_number,
                               models.CertificateNumberLog.status, models.CertificateNumberLog.linked_table,
                               models.CertificateNumberLog.linked_id)):
        key = canonical_number(r[1])
        if key:
            logs[key].append(r)
    # 고유 문자열에 대한 UNIQUE 제약이 있어도 '26-085'와 '26-85'는 함께 존재할 수 있음.
    # 이 경우 마지막 로그로 덮어쓰면 취소 여부를 조회 순서에 따라 잘못 확정할 위험이 있다.
    for key, group in logs.items():
        if len(group) > 1:
            for r in group:
                out.append(_f("DQ-09", REVIEW, "certificate_number_logs",
                              {"id": r[0]}, "certificate_number", r[1],
                              f"표준화 번호({key})가 같은 발급번호 이력 {len(group)}건이 존재",
                              "취소·사용 상태와 실사용자를 확인(자동 재연결 금지)",
                              duplicate_ids=[v[0] for v in group]))
    ledger_by_num: Dict[str, List] = defaultdict(list)
    Lg = CertificateIssuanceLedger
    ledger_rows = db.query(Lg.id, Lg.document_number, Lg.name, Lg.vehicle_number, Lg.region, Lg.member_id,
                           Lg.candidate_id, Lg.status).filter(Lg.deleted_at.is_(None)).all()
    mem_state = {r[0]: r[1] for r in db.query(L.id, L.deleted_at)}
    cand_member = {r[0]: (r[1], r[2]) for r in db.query(C.id, C.member_id, C.deleted_at)}
    for r in ledger_rows:
        key = canonical_number(r[1])
        if key:
            ledger_by_num[key].append(r)
        rec = {"id": r[0], "name": r[2], "vehicle_number": r[3], "region": r[4]}
        if r[2] == "(대상미입력)":
            continue    # 번호만 예약된 행은 정상
        if not r[5] and not r[6]:
            out.append(_f("DQ-09", REVIEW, "certificate_issuance_ledger", rec, "member_id/candidate_id", None,
                          "발급대장 행이 회원·예정자 어디에도 연결되어 있지 않음", "재연결 후보 확인(자동 연결은 유일 후보 + 신원정보 일치 시에만)"))
        if r[5] and r[5] not in mem_state:
            out.append(_f("DQ-15", CONFIRMED, "certificate_issuance_ledger", rec, "member_id", r[5],
                          "존재하지 않는 회원을 참조", "재연결"))
        elif r[5] and mem_state.get(r[5]) is not None:
            out.append(_f("DQ-09", REVIEW, "certificate_issuance_ledger", rec, "member_id", r[5],
                          "삭제 처리된 회원을 참조", "활성 회원으로 재연결 필요 여부 검토"))
        if r[6] and r[6] not in cand_member:
            out.append(_f("DQ-15", CONFIRMED, "certificate_issuance_ledger", rec, "candidate_id", r[6],
                          "존재하지 않는 예정자를 참조", "발급이력과 예정자 대조 후 연결 정정"))
        elif r[6] and cand_member[r[6]][1] is not None:
            out.append(_f("DQ-09", REVIEW, "certificate_issuance_ledger", rec, "candidate_id", r[6],
                          "삭제된 예정자와 연결됨", "과거 발급 이력 여부 확인"))
        if r[6] and r[6] in cand_member:
            cm, cdel = cand_member[r[6]]
            if cm and r[5] and cm != r[5]:
                out.append(_f("DQ-09", CONFIRMED, "certificate_issuance_ledger", rec, "member_id vs candidate.member_id",
                              f"{r[5]} / {cm}", "발급대장의 회원 ID와 예정자가 가리키는 회원 ID가 서로 다름", "어느 쪽이 맞는지 확인 후 정정"))
    for key, rows in ledger_by_num.items():
        if len(rows) > 1:
            for r in rows:
                out.append(_f("DQ-09", CONFIRMED, "certificate_issuance_ledger",
                              {"id": r[0], "name": r[2], "vehicle_number": r[3], "region": r[4]},
                              "document_number", r[1], f"같은 표준번호({key})를 가진 발급대장 행이 {len(rows)}건",
                              "중복 행 확인", duplicate_ids=[x[0] for x in rows]))
    # 회원의 자격증명번호 ↔ 발급이력/대장
    owner_by_num: Dict[str, List[dict]] = defaultdict(list)
    for m in members:
        key = canonical_number(m["certificate_number"])
        if not key:
            continue
        owner_by_num[key].append(m)
        number_logs = logs.get(key, [])
        if not number_logs:
            out.append(_f("DQ-09", REVIEW, "license_holders", m, "certificate_number", m["certificate_number"],
                          "회원에 자격증명번호가 있으나 발급번호 이력(certificate_number_logs)에 없음",
                          "이력 보충 여부 확인"))
        elif len(number_logs) == 1 and number_logs[0][2] == "cancelled":
            out.append(_f("DQ-09", CONFIRMED, "license_holders", m, "certificate_number", m["certificate_number"],
                          "취소된 발급번호를 회원이 사용 중", "번호 확인"))
        elif len(number_logs) > 1:
            out.append(_f("DQ-09", REVIEW, "license_holders", m, "certificate_number", m["certificate_number"],
                          "동일 표준번호의 발급이력이 여러 개여서 상태가 불확실함",
                          "발급번호 이력 및 실제 사용 여부 확인(자동 재연결 금지)"))
        if key not in ledger_by_num:
            out.append(_f("DQ-09", REVIEW, "license_holders", m, "certificate_number", m["certificate_number"],
                          "회원에 자격증명번호가 있으나 발급대장(certificate_issuance_ledger)에 없음",
                          "재연결/대장 보충 필요 여부 확인(자동 생성 금지)"))
        else:
            rows = ledger_by_num[key]
            if all(r[5] != m["id"] for r in rows):
                linked = [r[5] for r in rows if r[5]]
                out.append(_f("DQ-09", CONFIRMED if linked else REVIEW, "license_holders", m, "certificate_number",
                              m["certificate_number"],
                              ("발급대장에는 이 번호가 있으나 다른 회원(#%s)에게 연결됨" % linked[0]) if linked
                              else "발급대장에 이 번호가 있으나 이 회원과 연결되지 않음",
                              "신원정보 대조 후 재연결"))
    for key, items in owner_by_num.items():
        actives = [i for i in items if (i["status"] or "active") == "active"]
        if len(actives) > 1:
            for m in actives:
                out.append(_f("DQ-09", CONFIRMED, "license_holders", m, "certificate_number", m["certificate_number"],
                              f"같은 자격증명번호({key})를 활성 회원 {len(actives)}명이 사용", "번호 소유관계 확인",
                              duplicate_ids=[i["id"] for i in actives]))
    return out


# ───────────────────────── DQ-10/11/13 ─────────────────────────
_DATE_OK = re.compile(r"^\d{4}[-./]\s?\d{1,2}[-./]\s?\d{1,2}\.?$|^\d{8}$|^\d{2}[-./]\d{1,2}[-./]\d{1,2}\.?$")


def _date_ok(v: str) -> bool:
    s = str(v or "").strip()
    if not s:
        return True
    # 과거 자료에는 '13. 6.11.' 같이 구분자 뒤 공백이 들어간 정상 일자가 많다.
    # 공백만 제거한 사본으로 파싱하며, 저장된 원본 표기는 변경하지 않는다.
    compact = re.sub(r"\s+", "", s)
    if not _DATE_OK.match(compact):
        return False
    digits = re.findall(r"\d+", compact)
    if len(digits) == 1 and len(digits[0]) == 8:
        y, mo, d = int(digits[0][:4]), int(digits[0][4:6]), int(digits[0][6:])
    else:
        y, mo, d = int(digits[0]), int(digits[1]), int(digits[2])
        if y < 100:
            y += 2000
    if not 1950 <= y <= 2100:
        return False
    try:
        date(y, mo, d)  # 2월 30일·윤년 오류 등 실제 달력에 없는 날짜 차단
        return True
    except ValueError:
        return False


def check_member_fields(db: Session, members: List[dict]) -> List[dict]:
    out: List[dict] = []
    A = models.MemberAgent
    current_by_member: Dict[int, int] = defaultdict(int)
    # 임의 SQL 예외를 잡아 무시하면 PostgreSQL에서는 트랜잭션이 aborted 상태로 남는다.
    # 선택적 테이블이 없는지만 메타데이터로 확인하고, 그 밖의 DB 오류는 호출자에게 전달한다.
    if inspect(db.get_bind()).has_table(A.__tablename__):
        for mid, _ in db.query(A.member_id, A.id).filter(A.deleted_at.is_(None), A.status == "현재"):
            current_by_member[mid] += 1
    for m in members:
        has_date = bool(str(m["membership_date"] or "").strip())
        ms = (m["membership_status"] or "").strip()
        if ms == "가입" and not has_date:
            out.append(_f("DQ-10", REVIEW, "license_holders", m, "membership_status", "가입 / 가입일자 없음",
                          "가입으로 표시됐으나 가입일자가 비어 있음(업무규칙: 가입일자 없으면 미가입)", "원본 확인"))
        elif ms == "미가입" and has_date:
            out.append(_f("DQ-10", REVIEW, "license_holders", m, "membership_status", f"미가입 / {m['membership_date']}",
                          "미가입으로 표시됐으나 가입일자가 있음(업무규칙: 날짜가 있으면 가입)", "원본 확인"))
        for fname in ("membership_date", "approval_date"):
            if not _date_ok(m[fname]):
                out.append(_f("DQ-11", REVIEW, "license_holders", m, fname, m[fname],
                              "날짜로 해석할 수 없는 값(형식 차이만으로는 오류 확정하지 않음)", "원본 확인"))
        if m["agent_name"] and (m["category"] or "") != "택배" and current_by_member.get(m["id"], 0) == 0:
            out.append(_f("DQ-13", REVIEW, "license_holders", m, "agent_name", m["agent_name"],
                          "회원 정보에는 대리인이 있으나 현재 대리인 이력(member_agents)이 없음", "대리인 이력 확인"))
        if current_by_member.get(m["id"], 0) > 1:
            out.append(_f("DQ-13", CONFIRMED, "license_holders", m, "member_agents", current_by_member[m["id"]],
                          "'현재' 상태 대리인 이력이 2건 이상", "대리인 이력 정리"))
    return out


# ───────────────────────── 실행 ─────────────────────────
def run_scan(db: Session, codes: Optional[List[str]] = None) -> dict:
    """전체 점검(읽기 전용). 반환: summary + findings 전체 목록."""
    members = _member_dicts(db)
    findings: List[dict] = []
    findings += check_contacts(db)
    findings += check_mgmt_numbers(db, members)
    findings += check_links(db, members)
    findings += check_certificates(db, members)
    findings += check_member_fields(db, members)
    if codes:
        findings = [f for f in findings if f["code"] in codes]
    by_code: Dict[str, Dict[str, int]] = {c: {CONFIRMED: 0, REVIEW: 0} for c in CHECKS}
    for f in findings:
        by_code[f["code"]][f["severity"]] += 1
    flagged_members = {f["record_id"] for f in findings if f["table"] == "license_holders"}
    summary = {
        "members_checked": len(members),
        "flagged_members": len(flagged_members),
        "normal_members": len(members) - len(flagged_members),
        "confirmed": sum(1 for f in findings if f["severity"] == CONFIRMED),
        "review": sum(1 for f in findings if f["severity"] == REVIEW),
        "auto_fixable": 0,   # 정정 기능은 운영 환경에서 비활성(승인 전). 자동 정리 가능 건수는 0으로 고정 표시
        "conflicts": sum(1 for f in findings if f["code"] in ("DQ-05", "DQ-04", "DQ-08", "DQ-09") and f["severity"] == CONFIRMED),
        "by_code": by_code,
    }
    return {"summary": summary, "findings": findings}
