"""자격증명발급번호 중복 방지 - DB 계층.

번호가 저장되는 5곳(예정자, 회원, 양도양수대장, 폐업현황, 자격증명발급대장)을 한 번에 확인한다.
비교 규칙(26-085 == 26-85 등)과 '본인/연결 레코드는 중복이 아님' 판단은
``certificate_number_rules``(순수 함수)에 있고, 이 모듈은 DB 조회와 동시성 잠금만 담당한다.

- 이 모듈은 어떤 데이터도 수정/삭제하지 않는다 (조회 + 트랜잭션 잠금만).
- 후보 행은 번호의 숫자열(LIKE)로 먼저 좁히고, 필요한 컬럼만 읽는다 (ORM 객체 전체 로딩 없음).
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import models
from app.services import certificate_number_rules as rules

logger = logging.getLogger(__name__)

# source 이름 -> (모델, 번호 컬럼, 이름 컬럼, 추가로 읽을 컬럼)
_SPECS = {
    "candidates": (
        models.Candidate, "certificate_number", "name",
        ("vehicle_number", "region", "management_number", "member_id"),
    ),
    "license_holders": (
        models.LicenseHolder, "certificate_number", "name",
        ("vehicle_number", "region", "management_number", "candidate_id"),
    ),
    "transfer_ledger": (
        models.TransferLedger, "certificate_number", "transferee",
        ("vehicle_number", "region", "management_number", "member_id", "transferee_member_id"),
    ),
    "closures": (
        models.Closure, "certificate_number", "name",
        ("vehicle_number", "region", "management_number", "member_id", "transferee_member_id"),
    ),
    "certificate_ledger": (
        ledger_models.CertificateIssuanceLedger, "document_number", "name",
        ("vehicle_number", "region", "candidate_id", "member_id"),
    ),
}


class CertificateNumberDuplicate(HTTPException):
    """이미 등록된 자격증명발급번호. 409로 응답하고 기존 등록 대상 정보를 문구에 담는다."""

    def __init__(self, cert: str, hits: list):
        super().__init__(status_code=409, detail=rules.duplicate_message(cert, hits))
        self.cert = cert
        self.hits = hits


def _hit_from_row(source: str, row, number_col: str, name_col: str, extra: Iterable[str]) -> dict:
    hit = {
        "source": source,
        "id": row.id,
        "number": getattr(row, number_col, None),
        "name": getattr(row, name_col, None) or "",
    }
    for col in extra:
        hit[col] = getattr(row, col, None)
    return hit


def _scan_source(db: Session, source: str, cert: Optional[str], digits: str) -> list:
    """한 테이블에서 같은 번호를 쓰는 행을 찾는다 (cert가 None이면 번호가 있는 모든 행).

    삭제(soft-delete)된 행은 제외한다. 필요한 컬럼만 읽는다.
    """
    model, number_col, name_col, extra = _SPECS[source]
    number_attr = getattr(model, number_col)
    columns = [model.id, number_attr, getattr(model, name_col)] + [getattr(model, c) for c in extra]
    q = db.query(*columns).filter(number_attr.isnot(None), number_attr != "")
    if hasattr(model, "deleted_at"):
        q = q.filter(model.deleted_at.is_(None))
    if digits:
        # 앞자리 0(0085)·괄호 주석 등 어떤 표기에도 순번 숫자열은 부분 문자열로 들어 있다.
        q = q.filter(number_attr.contains(digits))

    hits = []
    for row in q.all():
        row_cert = rules.canonical_number(getattr(row, number_col, None))
        if not row_cert:
            continue
        if cert is not None and row_cert != cert:
            continue
        hit = _hit_from_row(source, row, number_col, name_col, extra)
        hit["canonical"] = row_cert
        hits.append(hit)
    return hits


def scan_usages(db: Session, cert: str) -> list:
    """5개 저장소에서 이 번호(표준형)를 쓰는 모든 행."""
    digits = rules.sequence_digits(cert)
    hits = []
    for source in _SPECS:
        hits.extend(_scan_source(db, source, cert, digits))
    return hits


def build_context(
    db: Session,
    *,
    candidate_id: Optional[int] = None,
    member_id: Optional[int] = None,
    transfer_id: Optional[int] = None,
    closure_id: Optional[int] = None,
    ledger_id: Optional[int] = None,
    extra_member_ids: Iterable[Optional[int]] = (),
    name: str = "",
    vehicle_number: str = "",
) -> rules.SubjectContext:
    """저장 대상 본인과 (id로) 연결된 레코드 집합을 만든다. 한 단계 연결까지만 따라간다."""
    ctx = rules.SubjectContext(name=(name or "").strip(), vehicle_number=(vehicle_number or "").strip())
    if candidate_id:
        ctx.candidate_ids.add(int(candidate_id))
    if member_id:
        ctx.member_ids.add(int(member_id))
    if transfer_id:
        ctx.transfer_ids.add(int(transfer_id))
    if closure_id:
        ctx.closure_ids.add(int(closure_id))
    for mid in extra_member_ids or ():
        if mid:
            ctx.member_ids.add(int(mid))

    L = ledger_models.CertificateIssuanceLedger
    if ledger_id:
        ctx.ledger_ids.add(int(ledger_id))
        row = db.query(L.candidate_id, L.member_id, L.name, L.vehicle_number).filter(L.id == int(ledger_id)).first()
        if row:
            if row.candidate_id:
                ctx.candidate_ids.add(row.candidate_id)
            if row.member_id:
                ctx.member_ids.add(row.member_id)
            ctx.name = ctx.name or (row.name or "").strip()
            ctx.vehicle_number = ctx.vehicle_number or (row.vehicle_number or "").strip()

    # 양도양수대장으로 이어진 양도자/양수자: 번호가 양도양수 과정에서 그대로 이어질 수 있다.
    T = models.TransferLedger
    conds = []
    if ctx.member_ids:
        conds += [T.member_id.in_(ctx.member_ids), T.transferee_member_id.in_(ctx.member_ids),
                  T.transferor_member_id.in_(ctx.member_ids)]
    if ctx.transfer_ids:
        conds.append(T.id.in_(ctx.transfer_ids))
    if conds:
        for tid, mid, transferee_mid, transferor_mid in db.query(
            T.id, T.member_id, T.transferee_member_id, T.transferor_member_id
        ).filter(T.deleted_at.is_(None), or_(*conds)).all():
            ctx.transfer_ids.add(tid)
            for linked in (mid, transferee_mid, transferor_mid):
                if linked:
                    ctx.member_ids.add(linked)

    C, M = models.Candidate, models.LicenseHolder
    if ctx.candidate_ids:
        for (linked,) in db.query(C.member_id).filter(C.id.in_(ctx.candidate_ids), C.member_id.isnot(None)).all():
            ctx.member_ids.add(linked)
        for (linked,) in db.query(M.id).filter(M.candidate_id.in_(ctx.candidate_ids)).all():
            ctx.member_ids.add(linked)
    if ctx.member_ids:
        for (linked,) in db.query(C.id).filter(C.member_id.in_(ctx.member_ids)).all():
            ctx.candidate_ids.add(linked)
        for (linked,) in db.query(M.candidate_id).filter(M.id.in_(ctx.member_ids), M.candidate_id.isnot(None)).all():
            ctx.candidate_ids.add(linked)
    return ctx


def find_conflicts(db: Session, value, ctx: rules.SubjectContext) -> list:
    cert = rules.canonical_number(value)
    if not cert:
        return []
    return rules.conflicts(scan_usages(db, cert), ctx)


def lock_number(db: Session, cert: str) -> None:
    """같은 번호를 동시에 저장하려는 요청을 직렬화한다 (PostgreSQL 트랜잭션 잠금).

    잠금은 호출한 트랜잭션이 commit/rollback될 때 자동으로 풀린다. 그래서 '확인 -> 저장'이
    같은 트랜잭션 안에서 끝나야 한다: 호출 뒤 저장 전에 db.commit()/rollback()을 하지 말 것.
    SQLite(테스트)에서는 동시 쓰기가 DB 수준에서 직렬화되므로 아무것도 하지 않는다.
    """
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"certnum:{cert}"})


def guard_certificate_number(db: Session, value, **subject) -> str:
    """저장 직전 호출: 번호를 잠그고 중복이면 409(CertificateNumberDuplicate)를 던진다.

    - 번호 형식이 아닌 값(과거 메모성 문자열, 빈 값)은 검사하지 않고 빈 문자열을 반환한다.
    - ``subject``: build_context 인자 (candidate_id, member_id, transfer_id, ledger_id,
      extra_member_ids, name, vehicle_number ...). 저장 대상 본인은 중복으로 보지 않는다.
    - 가드 자체의 예기치 않은 오류로 정상 저장까지 막지 않도록, 중복 판정이 아닌 오류는
      로그를 남기고 통과시킨다 (기존 동작과 동일한 수준으로 후퇴).
    """
    cert = rules.canonical_number(value)
    if not cert:
        return ""
    try:
        lock_number(db, cert)
        ctx = build_context(db, **subject)
        hits = find_conflicts(db, cert, ctx)
    except HTTPException:
        raise
    except Exception:
        logger.exception("자격증명발급번호 중복검사 오류(검사 생략): %s", cert)
        try:
            db.rollback()
        except Exception:
            pass
        return cert
    if hits:
        raise CertificateNumberDuplicate(cert, hits)
    return cert


def check_number(db: Session, value, **subject) -> dict:
    """화면의 즉시 중복확인용 (잠금 없음, 읽기 전용)."""
    cert = rules.canonical_number(value)
    raw = str(value or "").strip()
    if not cert:
        return {"valid_format": False, "normalized": "", "duplicate": False, "message": "", "matches": [],
                "input": raw}
    ctx = build_context(db, **subject)
    hits = find_conflicts(db, cert, ctx)
    return {
        "valid_format": True,
        "normalized": cert,
        "input": raw,
        "duplicate": bool(hits),
        "message": rules.duplicate_message(cert, hits),
        "matches": [
            {
                "source": h["source"],
                "source_label": rules.SOURCE_LABELS.get(h["source"], h["source"]),
                "id": h["id"],
                "name": h.get("name") or "",
                "vehicle_number": h.get("vehicle_number") or "",
                "region": h.get("region") or "",
                "management_number": h.get("management_number") or "",
                "description": rules.describe_hit(h),
            }
            for h in hits
        ],
    }


def find_existing_duplicates(db: Session) -> list:
    """이미 저장된 데이터에서 서로 다른 대상이 같은 번호를 쓰는 경우를 찾는다 (조회 전용).

    삭제/수정/번호 변경은 하지 않는다. 연결(id)이 없고 성명+차량번호도 다른 행끼리만
    '다른 대상'으로 센다. 사람이 확인하기 위한 참고 목록이다.
    """
    by_cert: dict = {}
    for source in _SPECS:
        for hit in _scan_source(db, source, None, ""):
            by_cert.setdefault(hit["canonical"], []).append(hit)

    report = []
    for cert, hits in by_cert.items():
        if len(hits) < 2:
            continue
        groups = rules.group_subjects(hits)
        if len(groups) < 2:
            continue
        report.append({
            "number": cert,
            "subjects": len(groups),
            "records": [
                {
                    "source": h["source"],
                    "source_label": rules.SOURCE_LABELS.get(h["source"], h["source"]),
                    "id": h["id"],
                    "stored_value": h.get("number") or "",
                    "name": h.get("name") or "",
                    "vehicle_number": h.get("vehicle_number") or "",
                    "region": h.get("region") or "",
                    "management_number": h.get("management_number") or "",
                    "subject_group": gi + 1,
                }
                for gi, group in enumerate(groups)
                for h in group
            ],
        })
    report.sort(key=lambda r: rules.number_key(r["number"]) or (0, 0))
    return report
