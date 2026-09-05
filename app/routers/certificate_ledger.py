"""자격증명 발급대장 API.

실제 업무 순서에 맞춘 연결:
1) 자격증명 발급 신청서류 접수
2) 기존 예정자 입력 화면에서 예정자 입력 + 자격증명발급번호 부여
3) 그 예정자의 기존 발급번호로 자격증명 생성/출력
4) 이후 시청 인가 공문이 오면 예정자 목록에서 등록 + 인가일자 입력

중요: 이 라우터는 자격증명발급번호를 새로 채번하지 않는다.
번호 원본은 기존 candidates.certificate_number + certificate_number_logs이다.
"""

from datetime import date, datetime, timezone
from math import ceil
from time import monotonic
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Integer, and_, cast, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import crud, models
from app.auth import get_current_user
from app.database import get_db
from app.services.certificate_ledger_service import (
    APPROVED,
    ISSUED,
    WAITING,
    add_history,
    ensure_candidate_ledger,
    ensure_member_ledger,
    ensure_ledger_schema,
    operator_name,
    reconcile_registered_candidates,
)


router = APIRouter(prefix="/api/certificate-ledger", tags=["자격증명 발급대장"])
VALID_STATUSES = {WAITING, APPROVED, ISSUED}
# 2026-09-04 기준 26-370까지는 이미 실제 자격증명이 만들어져 있던 기존 이력.
LEGACY_COMPLETED_THROUGH = {26: 370}
_STATS_CACHE = {"at": 0.0, "value": None}
_STATS_CACHE_TTL = 30.0
_MEMBER_LEDGER_SYNC = {"at": 0.0}
_MEMBER_LEDGER_SYNC_TTL = 300.0

def _invalidate_stats_cache():
    _STATS_CACHE["at"] = 0.0
    _STATS_CACHE["value"] = None



def _ensure_existing_member_ledgers_cached(db: Session, *, force: bool = False) -> int:
    """기존 개인/택배회원의 최신 자격증명번호 누락만 빠르게 보강한다.

    전체 회원에 대해 발급대장을 하나씩 대조하지 않는다. 자격증명번호가 실제로
    들어있는 회원의 ``id + 번호`` 두 컬럼만 한 번 읽고, 이미 대장에 있는 번호와
    비교한 뒤 *누락된 최신 번호*만 ``ensure_member_ledger``로 연결한다.

    따라서 박영민 26-370처럼 패치 전에 회원정보에 이미 저장돼 있던 번호도
    발급대장에 자동으로 살아나지만, 380/381처럼 번호만 발급하고 실제 회원에게
    사용하지 않은 번호는 대장에 만들지 않는다.
    """
    now = monotonic()
    if not force and (now - float(_MEMBER_LEDGER_SYNC.get("at") or 0)) < _MEMBER_LEDGER_SYNC_TTL:
        return 0
    _MEMBER_LEDGER_SYNC["at"] = now

    # 1) 실제 개인/택배회원 중 자격증명번호가 있는 행의 최소 컬럼만 조회.
    #    회원 전체 객체 + 관계를 순회하던 예전 방식보다 훨씬 가볍다.
    member_refs = (
        db.query(models.LicenseHolder.id, models.LicenseHolder.certificate_number)
        .filter(
            models.LicenseHolder.deleted_at.is_(None),
            models.LicenseHolder.certificate_number.isnot(None),
            models.LicenseHolder.certificate_number != "",
            models.LicenseHolder.category.in_(["개인", "택배"]),
        )
        .all()
    )
    if not member_refs:
        return 0

    normalized_members = []
    for member_id, raw_number in member_refs:
        cert = crud.normalize_certificate_number(raw_number)
        parts = _number_parts(cert) if cert else None
        if parts:
            normalized_members.append((parts[0], parts[1], int(member_id), cert))
    if not normalized_members:
        return 0

    # 2) 현재 발급대장 번호들을 한 번만 읽어 set으로 만든다.
    existing_rows = (
        db.query(ledger_models.CertificateIssuanceLedger.document_number)
        .filter(ledger_models.CertificateIssuanceLedger.deleted_at.is_(None))
        .all()
    )
    existing_keys = set()
    for (raw_number,) in existing_rows:
        parts = _number_parts(raw_number)
        if parts:
            existing_keys.add(parts)

    # 3) 누락된 번호 중 최신 번호부터 최대 10건만 보강.
    #    보통 정상 운영에서는 0~1건이며, 박영민 26-370 같은 기존 누락을 살리는 안전망이다.
    missing = [row for row in normalized_members if (row[0], row[1]) not in existing_keys]
    if not missing:
        return 0
    missing.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    missing = missing[:10]

    ids = [row[2] for row in missing]
    members = (
        db.query(models.LicenseHolder)
        .filter(models.LicenseHolder.id.in_(ids), models.LicenseHolder.deleted_at.is_(None))
        .all()
    )
    by_id = {m.id: m for m in members}

    changed = 0
    for _, _, member_id, _ in missing:
        member = by_id.get(member_id)
        if not member:
            continue
        try:
            ensure_member_ledger(db, member, None)
            changed += 1
        except Exception:
            db.rollback()

    if changed:
        _invalidate_stats_cache()
    return changed

def _number_variants(value: str):
    cert = crud.normalize_certificate_number(value)
    if not cert or "-" not in cert:
        return [cert] if cert else []
    yy, no = cert.split("-", 1)
    try:
        n = int(no)
    except Exception:
        return [cert]
    return list(dict.fromkeys([f"{yy}-{n}", f"{yy}-{n:02d}", f"{yy}-{n:03d}", f"{yy}-{n:04d}"]))


def _number_sort_key(row):
    parts = _number_parts(row.document_number)
    if parts:
        return (parts[0], parts[1], row.id or 0)
    return (-1, -1, row.id or 0)


class CreateLedgerBody(BaseModel):
    candidate_id: int
    qualification_number: Optional[str] = ""
    document_number: Optional[str] = ""  # 하위호환용. 실제 원본은 예정자 certificate_number.


class IssueLedgerBody(BaseModel):
    issue_date: Optional[str] = ""
    qualification_number: Optional[str] = ""
    document_number: Optional[str] = ""


class UpdateLedgerBody(BaseModel):
    """발급대장 자체 수정 전용 body.

    예정자/회원의 이름·주민등록번호·차량번호·자격증명번호 등은 여기서 건드리지 않는다.
    발급대장에서 실제로 관리하는 최소 항목(발급일자, 비고)만 받는다.
    """
    certificate_issue_date: Optional[str] = None
    remark: Optional[str] = None


def _dt(value):
    return value.isoformat() if value else None


def _number_parts(value: str):
    cert = crud.normalize_certificate_number(value)
    if not cert:
        return None
    try:
        yy, no = cert.split("-", 1)
        return int(yy), int(no)
    except Exception:
        return None


def _legacy_completed(value: str) -> bool:
    parts = _number_parts(value)
    if not parts:
        return False
    yy, no = parts
    return no <= LEGACY_COMPLETED_THROUGH.get(yy, -1)


def _norm_vehicle(value: str) -> str:
    """차량번호 비교용: 공백/하이픈 표기 차이는 같은 차량으로 본다."""
    import re
    return re.sub(r"[\s-]+", "", str(value or "")).strip()


def _norm_rrn(value: str) -> str:
    """주민등록번호 비교용: 숫자만 남긴다."""
    import re
    return re.sub(r"\D", "", str(value or ""))


def _same_person_vehicle(candidate, member) -> bool:
    """동명이인 오연결을 막기 위해 이름만으로는 동일인으로 보지 않는다.

    주민등록번호가 둘 다 있으면 그것으로, 없으면 차량번호+이름이 모두 일치할 때만 동일인으로 본다.
    """
    if not candidate or not member:
        return False
    c_rrn = _norm_rrn(getattr(candidate, "resident_number", ""))
    m_rrn = _norm_rrn(getattr(member, "resident_number", ""))
    if c_rrn and m_rrn:
        return c_rrn == m_rrn

    c_vehicle = _norm_vehicle(getattr(candidate, "vehicle_number", ""))
    m_vehicle = _norm_vehicle(getattr(member, "vehicle_number", ""))
    if not c_vehicle or not m_vehicle or c_vehicle != m_vehicle:
        return False
    c_name = (getattr(candidate, "name", "") or "").strip()
    m_name = (getattr(member, "name", "") or "").strip()
    return bool(c_name and m_name and c_name == m_name)


def _member_means_approved(candidate, member) -> bool:
    """예정자/신규회원이 실제 개인회원 또는 택배회원에 등록돼 있으면 인가완료로 판단."""
    if not member:
        return False
    category = (getattr(member, "category", "") or "").strip()
    if category and category not in {"개인", "택배"}:
        return False
    if candidate is None:
        return False
    if getattr(candidate, "member_id", None) == getattr(member, "id", None):
        return True
    if getattr(member, "candidate_id", None) == getattr(candidate, "id", None):
        return True
    return _same_person_vehicle(candidate, member)


def _actual_details(db: Session, row):
    """발급대장에 복사된 값만 믿지 않고 현재 회원/예정자/양도양수 실제 값을 우선 조회한다.

    특히 과거 행의 시청 인가는 실제 ``approval_date``가 있으면 인가완료로 판단한다.
    """
    member = None
    candidate = None
    transfer = None

    if row.member_id:
        member = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.id == row.member_id,
            models.LicenseHolder.deleted_at.is_(None),
        ).first()

    if row.candidate_id:
        candidate = db.query(models.Candidate).filter(
            models.Candidate.id == row.candidate_id,
            models.Candidate.deleted_at.is_(None),
        ).first()
        if not candidate and not member:
            # 예정자가 회원 전환 후 삭제(soft-delete)되었더라도, 이미 연결돼 있던
            # member_id만 읽어와 인가완료 상태/회원 연결이 끊기지 않게 한다.
            # 삭제된 예정자를 되살리거나 예정자 API로 노출하지는 않는다.
            deleted_candidate = db.query(models.Candidate).filter(
                models.Candidate.id == row.candidate_id,
            ).first()
            if deleted_candidate and getattr(deleted_candidate, "member_id", None):
                member = db.query(models.LicenseHolder).filter(
                    models.LicenseHolder.id == deleted_candidate.member_id,
                    models.LicenseHolder.deleted_at.is_(None),
                ).first()
        if not member and candidate:
            mid = getattr(candidate, "member_id", None)
            if mid:
                member = db.query(models.LicenseHolder).filter(
                    models.LicenseHolder.id == mid,
                    models.LicenseHolder.deleted_at.is_(None),
                ).first()
            if not member:
                member = db.query(models.LicenseHolder).filter(
                    models.LicenseHolder.candidate_id == candidate.id,
                    models.LicenseHolder.deleted_at.is_(None),
                ).first()
            if not member and (candidate.vehicle_number or "").strip():
                # 과거 자료는 candidate_id/member_id 연결이 없어도 실제 개인/택배 회원에 이미 들어가 있을 수 있다.
                # 같은 차량번호 + 같은 성명이면 인가된 회원으로 연결해 표시한다.
                candidates = db.query(models.LicenseHolder).filter(
                    models.LicenseHolder.deleted_at.is_(None),
                    models.LicenseHolder.name == (candidate.name or ""),
                ).all()
                member = next((m for m in candidates if _same_person_vehicle(candidate, m)), None)

    number = crud.normalize_certificate_number(row.document_number)
    if number and not member:
        usage = crud._scan_certificate_number_usage(db, number)
        if usage:
            tname, lid, _, _ = usage
            if tname == "license_holders":
                member = db.query(models.LicenseHolder).filter(
                    models.LicenseHolder.id == lid, models.LicenseHolder.deleted_at.is_(None)
                ).first()
            elif tname == "candidates" and not candidate:
                candidate = db.query(models.Candidate).filter(
                    models.Candidate.id == lid, models.Candidate.deleted_at.is_(None)
                ).first()
                if candidate and getattr(candidate, "member_id", None):
                    member = db.query(models.LicenseHolder).filter(
                        models.LicenseHolder.id == candidate.member_id, models.LicenseHolder.deleted_at.is_(None)
                    ).first()
            elif tname == "transfer_ledger":
                transfer = db.query(models.TransferLedger).filter(
                    models.TransferLedger.id == lid, models.TransferLedger.deleted_at.is_(None)
                ).first()
                if transfer:
                    mid = getattr(transfer, "transferee_member_id", None) or getattr(transfer, "member_id", None)
                    if mid:
                        member = db.query(models.LicenseHolder).filter(
                            models.LicenseHolder.id == mid, models.LicenseHolder.deleted_at.is_(None)
                        ).first()

    src = member or candidate or transfer
    approval_date = (getattr(member, "approval_date", "") if member else "") or \
                    (getattr(transfer, "approval_date", "") if transfer else "") or \
                    (row.approval_date or "")
    issue_date = (row.certificate_issue_date or "") or \
                 (getattr(src, "certificate_issue_date", "") if src else "")
    name = (getattr(src, "name", "") if src else "") or \
           (getattr(src, "transferee", "") if src else "") or row.name or ""
    vehicle = (getattr(src, "vehicle_number", "") if src else "") or row.vehicle_number or ""
    region = (getattr(src, "region", "") if src else "") or row.region or ""

    log = None
    if number:
        log = crud.get_certificate_number_log(db, number)

    return {
        "member": member, "candidate": candidate, "transfer": transfer, "log": log,
        "approval_date": approval_date or "", "issue_date": issue_date or "",
        "name": name, "vehicle_number": vehicle, "region": region,
    }


def _item(db: Session, row):
    actual = _actual_details(db, row)
    approval_date = (actual["approval_date"] or "").strip()
    approval_status = "인가완료" if (approval_date or _member_means_approved(actual.get("candidate"), actual.get("member"))) else "인가대기"

    log = actual.get("log")
    if log and log.status == "cancelled":
        issuance_status = "취소"
    elif row.status == ISSUED or row.issued_at or _legacy_completed(row.document_number):
        issuance_status = "발급완료"
    else:
        issuance_status = "생성대기"

    latest_operator = row.latest_operator or (getattr(log, "issued_by", "") if log else "") or ""
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "member_id": getattr(actual.get("member"), "id", None) or row.member_id,
        "region": actual["region"],
        "vehicle_number": actual["vehicle_number"],
        "name": actual["name"],
        "qualification_number": row.qualification_number or "",
        "document_number": crud.normalize_certificate_number(row.document_number) or (row.document_number or ""),
        "approval_date": approval_date,
        "certificate_issue_date": actual["issue_date"],
        "status": row.status,
        "approval_status": approval_status,
        "issuance_status": issuance_status,
        "latest_operator": latest_operator,
        "created_by": row.created_by or "",
        "remark": row.remark or "",
        "approved_at": _dt(row.approved_at),
        "issued_at": _dt(row.issued_at),
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }



def _bulk_context(db: Session, rows):
    """목록/통계용 관련 자료를 몇 번의 IN 조회로 미리 읽어 N+1 쿼리를 없앤다."""
    rows = list(rows or [])
    candidate_ids = {r.candidate_id for r in rows if r.candidate_id}
    direct_member_ids = {r.member_id for r in rows if r.member_id}
    numbers = {crud.normalize_certificate_number(r.document_number) for r in rows if r.document_number}
    numbers.discard(None)
    numbers.discard("")

    candidates = {}
    if candidate_ids:
        candidates = {r.id: r for r in db.query(models.Candidate).filter(
            models.Candidate.id.in_(candidate_ids), models.Candidate.deleted_at.is_(None)
        ).all()}

    logs = {}
    if numbers:
        parts = {_number_parts(n) for n in numbers}
        parts.discard(None)
        clauses = [and_(models.CertificateNumberLog.year == yy, models.CertificateNumberLog.number == no) for yy, no in parts]
        log_rows = db.query(models.CertificateNumberLog).filter(or_(*clauses)).all() if clauses else []
        priority = {"used": 3, "cancelled": 2, "issued": 1}
        for log_row in log_rows:
            key = crud.normalize_certificate_number(log_row.certificate_number) or (f"{log_row.year}-{log_row.number}" if log_row.year is not None and log_row.number is not None else "")
            if not key:
                continue
            prev = logs.get(key)
            if prev is None or priority.get(log_row.status or "", 0) > priority.get(prev.status or "", 0):
                logs[key] = log_row

    # 번호이력에 직접 연결된 과거 자료도 한 번에 가져온다.
    transfer_ids = {r.linked_id for r in logs.values() if r.linked_table == "transfer_ledger" and r.linked_id}
    log_member_ids = {r.linked_id for r in logs.values() if r.linked_table == "license_holders" and r.linked_id}
    log_candidate_ids = {r.linked_id for r in logs.values() if r.linked_table == "candidates" and r.linked_id}
    missing_candidate_ids = log_candidate_ids.difference(candidates.keys())
    if missing_candidate_ids:
        for r in db.query(models.Candidate).filter(
            models.Candidate.id.in_(missing_candidate_ids), models.Candidate.deleted_at.is_(None)
        ).all():
            candidates[r.id] = r

    transfers = {}
    if transfer_ids:
        transfers = {r.id: r for r in db.query(models.TransferLedger).filter(
            models.TransferLedger.id.in_(transfer_ids), models.TransferLedger.deleted_at.is_(None)
        ).all()}

    member_ids = set(direct_member_ids) | set(log_member_ids)
    member_ids.update(getattr(c, "member_id", None) for c in candidates.values())
    for t in transfers.values():
        member_ids.add(getattr(t, "transferee_member_id", None) or getattr(t, "member_id", None))
    member_ids.discard(None)

    # 예정자가 회원 전환 후 삭제(soft-delete)된 경우에도, 이미 연결돼 있던 member_id는
    # 잃지 않아야 인가완료 상태가 계속 정상 표시된다. 삭제된 예정자를 되살리지 않고
    # member_id만 조회 용도로 읽어온다.
    missing_from_active = candidate_ids.difference(candidates.keys())
    deleted_candidate_member = {}
    if missing_from_active:
        for cid, mid in db.query(models.Candidate.id, models.Candidate.member_id).filter(
            models.Candidate.id.in_(missing_from_active),
            models.Candidate.member_id.isnot(None),
        ).all():
            member_ids.add(mid)
            deleted_candidate_member[cid] = mid

    members = {}
    member_q = db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None))
    clauses = []
    if member_ids:
        clauses.append(models.LicenseHolder.id.in_(member_ids))
    if candidate_ids or log_candidate_ids:
        clauses.append(models.LicenseHolder.candidate_id.in_(candidate_ids | log_candidate_ids))
    cand_names = {(c.name or "").strip() for c in candidates.values() if (c.name or "").strip()}
    cand_vehicles = {(c.vehicle_number or "").strip() for c in candidates.values() if (c.vehicle_number or "").strip()}
    if cand_names:
        clauses.append(models.LicenseHolder.name.in_(cand_names))
    if cand_vehicles:
        clauses.append(models.LicenseHolder.vehicle_number.in_(cand_vehicles))
    if clauses:
        members = {r.id: r for r in member_q.filter(or_(*clauses)).all()}
    members_by_candidate = {r.candidate_id: r for r in members.values() if getattr(r, "candidate_id", None)}
    members_by_identity = {}
    for m in members.values():
        key = ((m.name or "").strip(), _norm_vehicle(m.vehicle_number))
        if key[0] and key[1] and key not in members_by_identity:
            members_by_identity[key] = m

    return {
        "candidates": candidates,
        "members": members,
        "members_by_candidate": members_by_candidate,
        "members_by_identity": members_by_identity,
        "deleted_candidate_member": deleted_candidate_member,
        "transfers": transfers,
        "logs": logs,
    }


def _item_bulk(db: Session, row, ctx):
    """_item과 같은 화면 값을 만들되 행마다 별도 SELECT를 반복하지 않는다."""
    members = ctx["members"]
    candidates = ctx["candidates"]
    transfers = ctx["transfers"]
    logs = ctx["logs"]

    member = members.get(row.member_id) if row.member_id else None
    candidate = candidates.get(row.candidate_id) if row.candidate_id else None
    if not member and not candidate and row.candidate_id:
        # 예정자가 회원 전환 후 삭제(soft-delete)됐지만 member_id 연결은 살아있는 경우.
        member = members.get(ctx.get("deleted_candidate_member", {}).get(row.candidate_id))
    if not member and candidate:
        member = members.get(getattr(candidate, "member_id", None)) or ctx["members_by_candidate"].get(candidate.id)
        if not member:
            member = ctx.get("members_by_identity", {}).get(((candidate.name or "").strip(), _norm_vehicle(candidate.vehicle_number)))
            if member and not _same_person_vehicle(candidate, member):
                member = None

    number = crud.normalize_certificate_number(row.document_number)
    log = logs.get(number) if number else None
    transfer = None
    if log and not member:
        if log.linked_table == "license_holders":
            member = members.get(log.linked_id)
        elif log.linked_table == "candidates" and not candidate:
            candidate = candidates.get(log.linked_id)
            if candidate:
                member = members.get(getattr(candidate, "member_id", None)) or ctx["members_by_candidate"].get(candidate.id)
        elif log.linked_table == "transfer_ledger":
            transfer = transfers.get(log.linked_id)
            if transfer:
                member = members.get(getattr(transfer, "transferee_member_id", None) or getattr(transfer, "member_id", None))

    # 아주 오래된 예외 자료만 기존 정밀 조회로 보완한다.
    if number and not any((member, candidate, transfer, log)):
        return _item(db, row)

    src = member or candidate or transfer
    approval_date = (getattr(member, "approval_date", "") if member else "") or \
                    (getattr(transfer, "approval_date", "") if transfer else "") or \
                    (row.approval_date or "")
    issue_date = (row.certificate_issue_date or "") or (getattr(src, "certificate_issue_date", "") if src else "")
    name = (getattr(src, "name", "") if src else "") or (getattr(src, "transferee", "") if src else "") or row.name or ""
    vehicle = (getattr(src, "vehicle_number", "") if src else "") or row.vehicle_number or ""
    region = (getattr(src, "region", "") if src else "") or row.region or ""

    approval_date = (approval_date or "").strip()
    approval_status = "인가완료" if (approval_date or _member_means_approved(candidate, member)) else "인가대기"
    if log and log.status == "cancelled":
        issuance_status = "취소"
    elif row.status == ISSUED or row.issued_at or _legacy_completed(row.document_number):
        issuance_status = "발급완료"
    else:
        issuance_status = "생성대기"

    latest_operator = row.latest_operator or (getattr(log, "issued_by", "") if log else "") or ""
    return {
        "id": row.id,
        "candidate_id": row.candidate_id,
        "member_id": getattr(member, "id", None) or row.member_id,
        "region": region,
        "vehicle_number": vehicle,
        "name": name,
        "qualification_number": row.qualification_number or "",
        "document_number": number or (row.document_number or ""),
        "approval_date": approval_date,
        "certificate_issue_date": issue_date,
        "status": row.status,
        "approval_status": approval_status,
        "issuance_status": issuance_status,
        "latest_operator": latest_operator,
        "created_by": row.created_by or "",
        "remark": row.remark or "",
        "approved_at": _dt(row.approved_at),
        "issued_at": _dt(row.issued_at),
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _items_bulk(db: Session, rows):
    rows = list(rows or [])
    if not rows:
        return []
    ctx = _bulk_context(db, rows)
    return [_item_bulk(db, row, ctx) for row in rows]


def _get_row(db: Session, ledger_id: int):
    ensure_ledger_schema(db)
    row = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.id == ledger_id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(404, "발급대장 기록을 찾을 수 없습니다.")
    return row


def _same_number_subject(log: models.CertificateNumberLog, row) -> bool:
    if not log or log.status != "used":
        return True
    if row.member_id and log.linked_table == "license_holders" and log.linked_id == row.member_id:
        return True
    if row.candidate_id and log.linked_table == "candidates" and log.linked_id == row.candidate_id:
        return True
    return bool(
        (log.target_name or "").strip() == (row.name or "").strip()
        and (log.vehicle_number or "").strip() == (row.vehicle_number or "").strip()
        and (row.name or "").strip()
        and (row.vehicle_number or "").strip()
    )


def _candidate_for_row(db: Session, row):
    if not row.candidate_id:
        return None
    return (
        db.query(models.Candidate)
        .filter(
            models.Candidate.id == row.candidate_id,
            models.Candidate.deleted_at.is_(None),
        )
        .first()
    )


def _sync_existing_number_from_candidate(db: Session, row, actor: str = ""):
    """예정자에 이미 부여된 발급번호를 발급대장에 연결한다.

    여기서는 절대 get_next_certificate_number()를 호출하지 않는다.
    번호가 없으면 예정자 입력 화면에서 먼저 '발급번호 부여'를 하도록 안내한다.
    """
    candidate = _candidate_for_row(db, row)
    if not candidate:
        raise HTTPException(400, "연결된 예정자 정보를 찾을 수 없습니다.")

    number = crud.normalize_certificate_number(candidate.certificate_number) or (candidate.certificate_number or "").strip()
    if not number:
        raise HTTPException(
            400,
            "자격증명발급번호가 없습니다. 예정자 입력 화면에서 '발급번호 부여'를 먼저 하세요.",
        )

    current = (row.document_number or "").strip()
    if current and current != number and row.status == ISSUED:
        raise HTTPException(
            400,
            f"이미 발급완료된 대장의 자격증명번호({current})와 예정자 발급번호({number})가 다릅니다. 임의 변경하지 마세요.",
        )

    # 같은 번호가 다른 발급대장에 연결된 경우 차단.
    duplicate = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.document_number == number,
            ledger_models.CertificateIssuanceLedger.id != row.id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )
    if duplicate:
        raise HTTPException(400, f"자격증명발급번호 {number}가 이미 다른 발급대장에 연결되어 있습니다.")

    if row.document_number != number:
        row.document_number = number
        row.latest_operator = actor or row.latest_operator

    # 기존 번호이력 원장을 그대로 확인한다. 누락된 과거 수기값만 기존 sync 함수를 사용해 복구한다.
    log = crud.get_certificate_number_log(db, number)
    if not log:
        crud.sync_certificate_number_usage(
            db,
            number,
            "candidates",
            candidate.id,
            candidate.name or "",
            candidate.vehicle_number or "",
        )
        log = crud.get_certificate_number_log(db, number)

    if not log:
        raise HTTPException(400, f"자격증명발급번호 {number}의 발급 이력을 확인할 수 없습니다.")
    if log.status == "cancelled":
        raise HTTPException(400, f"자격증명발급번호 {number}는 취소된 번호입니다.")
    if log.status == "used" and not _same_number_subject(log, row):
        raise HTTPException(400, f"자격증명발급번호 {number}는 다른 대상자에게 이미 사용 중입니다.")

    # 예정자 레코드에 번호가 실제 저장되어 있으므로 기존 원장 기준으로는 사용중이 맞다.
    # issued 상태로 남아 있다면 기존 사용연결 규칙과 동일하게 현재 예정자에 연결한다.
    if log.status == "issued":
        log.status = "used"
        log.linked_table = "candidates"
        log.linked_id = candidate.id
        log.target_name = candidate.name or ""
        log.vehicle_number = candidate.vehicle_number or ""
        if actor and not log.issued_by:
            log.issued_by = actor

    db.commit()
    db.refresh(row)
    return row


def _sync_after_issue(db: Session, row, actor: str) -> None:
    """발급완료 시 발급일과 기존 발급번호 연결만 갱신한다."""
    candidate = _candidate_for_row(db, row)
    if candidate:
        candidate.certificate_issue_date = row.certificate_issue_date or candidate.certificate_issue_date
        # 번호는 예정자에서 먼저 부여된 값이 원본이므로 서로 다르면 덮어쓰지 않고 오류 처리한다.
        if (candidate.certificate_number or "").strip() != (row.document_number or "").strip():
            raise HTTPException(400, "예정자의 자격증명발급번호와 발급대장 번호가 일치하지 않습니다.")

    if row.member_id:
        member = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == row.member_id).first()
        if member:
            member.certificate_issue_date = row.certificate_issue_date or member.certificate_issue_date
            if not (member.certificate_number or "").strip():
                member.certificate_number = row.document_number or ""

    number = (row.document_number or "").strip()
    log = crud.get_certificate_number_log(db, number)
    if not log:
        raise HTTPException(400, f"자격증명발급번호 {number}의 발급 이력이 없습니다.")
    if log.status == "cancelled":
        raise HTTPException(400, f"자격증명발급번호 {number}는 취소된 번호입니다.")
    if log.status == "used" and not _same_number_subject(log, row):
        raise HTTPException(400, f"자격증명발급번호 {number}는 다른 대상자에게 이미 사용 중입니다.")

    log.status = "used"
    log.linked_table = "license_holders" if row.member_id else "candidates"
    log.linked_id = row.member_id or row.candidate_id
    log.target_name = row.name or ""
    log.vehicle_number = row.vehicle_number or ""
    if not log.issued_by:
        log.issued_by = actor


@router.get("/candidates")
async def candidate_choices(
    search: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """예정자 검색은 조회만 한다.

    과거 버전처럼 검색 결과를 발급대장에 자동 생성하지 않는다.
    신규 발급대장 행은 예정자 신규 저장 훅에서만 생성된다.
    """
    ensure_ledger_schema(db)
    q = db.query(models.Candidate).filter(
        models.Candidate.deleted_at.is_(None),
        models.Candidate.is_registered.is_(False),
    )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                models.Candidate.name.ilike(pattern),
                models.Candidate.vehicle_number.ilike(pattern),
                models.Candidate.certificate_number.ilike(pattern),
                models.Candidate.region.ilike(pattern),
            )
        )
    rows = q.order_by(models.Candidate.id.desc()).limit(limit).all()

    ledger_rows = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id.isnot(None),
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .all()
    )
    by_candidate = {r.candidate_id: r for r in ledger_rows}

    return {
        "items": [
            {
                "id": row.id,
                "region": row.region or "",
                "vehicle_number": row.vehicle_number or "",
                "name": row.name or "",
                "document_number": (row.certificate_number or "").strip(),
                "is_registered": bool(row.is_registered),
                "member_id": row.member_id,
                "already_connected": row.id in by_candidate,
                "ledger_id": by_candidate[row.id].id if row.id in by_candidate else None,
                "ledger_status": by_candidate[row.id].status if row.id in by_candidate else None,
            }
            for row in rows
        ]
    }


@router.get("/stats")
async def ledger_stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    # 같은 통계를 화면 전환 때마다 재계산하지 않는다. 30초 캐시로 체감 속도를 높인다.
    now = monotonic()
    cached = _STATS_CACHE.get("value")
    if cached is not None and (now - float(_STATS_CACHE.get("at") or 0)) < _STATS_CACHE_TTL:
        return cached
    ensure_ledger_schema(db)
    rows = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(ledger_models.CertificateIssuanceLedger.deleted_at.is_(None))
        .all()
    )
    items = _items_bulk(db, rows)
    issued = sum(1 for item in items if item["issuance_status"] == "발급완료")
    cancelled = sum(1 for item in items if item["issuance_status"] == "취소")
    approved = sum(1 for item in items if item["approval_status"] == "인가완료")

    yy = date.today().year % 100
    counter = db.query(models.CertificateNumberCounter).filter(
        models.CertificateNumberCounter.year == yy
    ).first()
    last_number = int(counter.last_number or 0) if counter else 0
    if not last_number:
        max_log = db.query(models.CertificateNumberLog.number).filter(
            models.CertificateNumberLog.year == yy
        ).order_by(models.CertificateNumberLog.number.desc()).first()
        last_number = int(max_log[0] or 0) if max_log else 0

    result = {
        "total": last_number,
        "ledger_total": len(items),
        "last_certificate_number": f"{yy}-{last_number}" if last_number else "-",
        "counts": {
            "생성대기": max(0, len(items) - issued - cancelled),
            "발급완료": issued,
            "취소": cancelled,
            "인가대기": len(items) - approved,
            "인가완료": approved,
        },
    }
    _STATS_CACHE["value"] = result
    _STATS_CACHE["at"] = monotonic()
    return result


@router.get("")
async def list_ledger(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    # 기본 화면은 DB에서 먼저 50개만 잘라 가져와 즉시 표시한다.
    # 검색/상태 필터가 있을 때만 전체 대상에서 필터링하되 N+1 쿼리는 사용하지 않는다.
    ensure_ledger_schema(db)
    _ensure_existing_member_ledgers_cached(db)
    base = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(ledger_models.CertificateIssuanceLedger.deleted_at.is_(None))
    )

    # 발급번호 숫자 기준 최신순. 26-370이 26-369보다 항상 먼저 보인다.
    # 26-085/26-85도 같은 숫자 85로 정렬된다.
    rows = base.all()
    rows.sort(key=_number_sort_key, reverse=True)
    items = _items_bulk(db, rows)

    if status:
        valid = {"인가대기", "인가완료"}
        if status not in valid:
            raise HTTPException(400, "처리상태 값이 올바르지 않습니다.")
        items = [x for x in items if x["approval_status"] == status]

    if search and search.strip():
        needle = search.strip().lower()
        def matched(item):
            fields = (
                item.get("name"), item.get("vehicle_number"), item.get("document_number"),
                item.get("region"), item.get("latest_operator"), item.get("approval_date"),
            )
            return any(needle in str(v or "").lower() for v in fields)
        items = [x for x in items if matched(x)]

    total = len(items)
    start_i = (page - 1) * limit
    page_items = items[start_i:start_i + limit]
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "pages": max(1, ceil(total / limit)),
        "limit": limit,
    }


@router.post("/refresh")
async def refresh_ledger(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """관리자가 필요할 때만 발급대장/자격증명번호 원장을 전체 대조한다.

    일반 목록·통계 조회에서는 실행하지 않아 화면 전환이 느려지지 않도록 한다.
    """
    ensure_ledger_schema(db)
    _invalidate_stats_cache()
    changed_members = _ensure_existing_member_ledgers_cached(db, force=True)
    changed_candidates = reconcile_registered_candidates(db)
    changed_numbers = 0
    try:
        result = crud.reconcile_certificate_number_logs(db)
        if isinstance(result, dict):
            changed_numbers = int(result.get("changed") or result.get("updated") or 0)
        elif isinstance(result, (int, float)):
            changed_numbers = int(result)
    except Exception:
        db.rollback()
    return {
        "ok": True,
        "member_ledger_changes": changed_members,
        "candidate_changes": changed_candidates,
        "number_log_changes": changed_numbers,
    }


@router.post("")
async def create_ledger(
    body: CreateLedgerBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    ensure_ledger_schema(db)
    _invalidate_stats_cache()
    candidate = (
        db.query(models.Candidate)
        .filter(
            models.Candidate.id == body.candidate_id,
            models.Candidate.deleted_at.is_(None),
        )
        .first()
    )
    if not candidate:
        raise HTTPException(404, "예정자를 찾을 수 없습니다.")

    existing = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(
            ledger_models.CertificateIssuanceLedger.candidate_id == candidate.id,
            ledger_models.CertificateIssuanceLedger.deleted_at.is_(None),
        )
        .first()
    )
    if existing:
        row = existing
    else:
        if candidate.is_registered:
            raise HTTPException(400, "과거 등록완료 자료는 신규 자격증명 발급대장으로 자동 가져오지 않습니다.")
        row = ensure_candidate_ledger(db, candidate, user)
    if not row:
        raise HTTPException(500, "자격증명 발급대장을 생성하지 못했습니다.")

    if body.qualification_number and not row.qualification_number:
        row.qualification_number = body.qualification_number.strip()
        row.latest_operator = operator_name(user)
        db.commit()
        db.refresh(row)

    # body.document_number로 새 번호를 만들거나 덮어쓰지 않는다.
    # 예정자에 이미 부여된 번호가 있으면 그것만 연결한다.
    if (candidate.certificate_number or "").strip():
        row = _sync_existing_number_from_candidate(db, row, operator_name(user))
    return _item(db, row)


@router.post("/{ledger_id}/prepare")
async def prepare_issue(
    ledger_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """하위호환 엔드포인트.

    과거 버전처럼 새 번호를 예약하지 않고, 예정자 입력 단계에서 이미 부여된 번호만 확인/연결한다.
    """
    row = _get_row(db, ledger_id)
    row = _sync_existing_number_from_candidate(db, row, operator_name(user))
    return _item(db, row)


@router.get("/{ledger_id}/history")
async def ledger_history(
    ledger_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    _get_row(db, ledger_id)
    rows = (
        db.query(ledger_models.CertificateIssuanceHistory)
        .filter(ledger_models.CertificateIssuanceHistory.ledger_id == ledger_id)
        .order_by(ledger_models.CertificateIssuanceHistory.id.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "operator": row.operator or "",
                "memo": row.memo or "",
                "created_at": _dt(row.created_at),
            }
            for row in rows
        ]
    }


@router.post("/{ledger_id}/issue")
async def complete_issue(
    ledger_id: int,
    body: IssueLedgerBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _get_row(db, ledger_id)
    if row.status == ISSUED:
        return _item(db, row)

    # 실제 업무상 자격증명은 시청 인가 전에 만들 수 있으므로 인가대기/인가완료 모두 허용한다.
    if row.status not in (WAITING, APPROVED):
        raise HTTPException(400, "현재 상태에서는 발급완료 처리할 수 없습니다.")

    actor = operator_name(user)
    row = _sync_existing_number_from_candidate(db, row, actor)

    qualification_number = (body.qualification_number or row.qualification_number or "").strip()
    issue_date = (body.issue_date or "").strip() or date.today().isoformat()
    requested_number = (body.document_number or "").strip()
    document_number = (row.document_number or "").strip()

    if not qualification_number:
        raise HTTPException(400, "화물운송종사자격증번호를 입력하세요.")
    if not document_number:
        raise HTTPException(400, "자격증명발급번호가 없습니다. 예정자 입력 화면에서 먼저 발급번호를 부여하세요.")
    if requested_number and requested_number != document_number:
        raise HTTPException(400, "자격증명번호는 예정자에게 이미 부여된 번호와 같아야 합니다.")

    previous = row.status
    row.qualification_number = qualification_number
    row.certificate_issue_date = issue_date
    row.status = ISSUED
    row.issued_at = datetime.now(timezone.utc)
    row.latest_operator = actor

    try:
        _sync_after_issue(db, row, actor)
        add_history(
            db,
            row.id,
            "발급완료",
            previous,
            ISSUED,
            actor,
            f"기존 발급번호 {document_number}로 자격증명 발급 / 발급일 {issue_date}",
        )
        db.commit()
        db.refresh(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"자격증명 발급완료 저장 중 오류가 발생했습니다: {exc}") from exc
    return _item(db, row)


@router.get("/{ledger_id}")
async def get_ledger(
    ledger_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    row = _get_row(db, ledger_id)
    # 예정자에 나중에 번호를 부여한 경우 조회 시 대장에 즉시 반영한다.
    candidate = _candidate_for_row(db, row)
    if candidate and (candidate.certificate_number or "").strip() and row.status != ISSUED:
        row = _sync_existing_number_from_candidate(db, row, operator_name(user))
    return _item(db, row)


@router.put("/{ledger_id}")
async def update_ledger(
    ledger_id: int,
    body: UpdateLedgerBody,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """자격증명 발급대장 전용 수정 API.

    예정자(candidates)나 회원(license_holders) API를 전혀 거치지 않는다. 예정자가
    회원으로 전환되어 예정자 목록에서 사라지거나 삭제 처리되어도, 발급대장 행(ledger_id)
    자체는 그대로 남아있으므로 이 API는 계속 동작한다. 여기서 수정하는 항목은 발급대장이
    직접 소유한 필드(발급일자, 비고)뿐이며 개인/택배회원의 이름·주민등록번호·차량번호·
    자격증명번호 등은 절대 변경하지 않는다.
    """
    row = _get_row(db, ledger_id)
    actor = operator_name(user)
    changed = False
    notes = []

    if body.certificate_issue_date is not None:
        new_date = body.certificate_issue_date.strip()
        if new_date != (row.certificate_issue_date or ""):
            row.certificate_issue_date = new_date
            notes.append(f"발급일자 → {new_date or '(비움)'}")
            changed = True

    if body.remark is not None:
        new_remark = body.remark.strip()
        if new_remark != (row.remark or ""):
            row.remark = new_remark
            notes.append("비고 수정")
            changed = True

    if changed:
        row.latest_operator = actor
        add_history(db, row.id, "정보수정", row.status, row.status, actor, " / ".join(notes))
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, f"발급대장 저장 중 오류가 발생했습니다: {exc}") from exc
        db.refresh(row)
        _invalidate_stats_cache()

    return _item(db, row)
