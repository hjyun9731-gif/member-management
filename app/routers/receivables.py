from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.excel_utils import is_association_member
from app.receivables_models import (
    ReceivableCharge,
    ReceivableContactLog,
    ReceivablePayment,
    ReceivableProfile,
)

router = APIRouter()
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "legacy_receivables_2026.json"
KST = ZoneInfo("Asia/Seoul")

ACCOUNT_FEES = {"협회비": 10000, "관리비": 5000, "70세": 5000}
CONTACT_STATUSES = {"미연락", "연락완료", "부재", "재연락 필요", "문자발송"}


class PaymentIn(BaseModel):
    payment_date: str
    amount: int = Field(gt=0)
    method: Optional[str] = None
    memo: Optional[str] = None


class ContactIn(BaseModel):
    contact_date: str
    contact_method: str = "전화"
    status: str = "연락완료"
    memo: Optional[str] = None


class AccountIn(BaseModel):
    account_type: str
    vehicle_count: int = Field(default=1, ge=1, le=100)


def _norm(v) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(v or "")).lower()


def _parse_date(v) -> Optional[date]:
    """회원 DB의 2026-08-27 / 26.08.27 / 26-8-27 등을 모두 처리."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v

    s = str(v).strip().rstrip(".")
    for fmt in (
        "%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d",
        "%y-%m-%d", "%y.%m.%d", "%y/%m/%d",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            return datetime.strptime(s[:26], fmt).date()
        except Exception:
            pass

    m = re.search(r"(19\d{2}|20\d{2})\D*(\d{1,2})\D*(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None

    m = re.search(r"^(\d{2})\D+(\d{1,2})\D+(\d{1,2})", s)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy if yy <= 30 else 1900 + yy
        try:
            return date(year, int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def _first_of_next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _month_iter(start: date, end: date):
    cur = date(start.year, start.month, 1)
    end_m = date(end.year, end.month, 1)
    while cur <= end_m:
        yield cur
        cur = date(cur.year + (1 if cur.month == 12 else 0), 1 if cur.month == 12 else cur.month + 1, 1)


def _user_name(user) -> str:
    return getattr(user, "full_name", None) or getattr(user, "username", None) or "사용자"


def _is_active(member) -> bool:
    return (
        member is not None
        and getattr(member, "deleted_at", None) is None
        and (getattr(member, "status", None) or "active") == "active"
    )


def _active_sql():
    return and_(
        models.LicenseHolder.deleted_at.is_(None),
        or_(models.LicenseHolder.status.is_(None), models.LicenseHolder.status == "active"),
    )


def _closed_sql():
    return or_(
        models.LicenseHolder.deleted_at.isnot(None),
        and_(models.LicenseHolder.status.isnot(None), models.LicenseHolder.status != "active"),
    )


_seed_cache = None
_seed_by_combo = None
_seed_by_vehicle = None
_seed_by_name_region = None


def _load_seed():
    global _seed_cache, _seed_by_combo, _seed_by_vehicle, _seed_by_name_region
    if _seed_cache is not None:
        return _seed_cache
    if not DATA_FILE.exists():
        _seed_cache = []
        _seed_by_combo, _seed_by_vehicle, _seed_by_name_region = {}, {}, {}
        return _seed_cache

    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    rows = payload.get("rows", [])
    by_combo, by_vehicle, by_nr = {}, {}, {}
    for r in rows:
        nv, nn, nr = _norm(r.get("vehicle_number")), _norm(r.get("name")), _norm(r.get("region"))
        by_combo[(nv, nn)] = r
        if nv:
            by_vehicle.setdefault(nv, []).append(r)
        if nn:
            by_nr.setdefault((nn, nr), []).append(r)
    _seed_cache, _seed_by_combo, _seed_by_vehicle, _seed_by_name_region = rows, by_combo, by_vehicle, by_nr
    return rows


def _match_seed(member):
    _load_seed()
    # Legacy 자동매칭은 차량번호+성명 동시일치만 허용한다.
    # 성명+지역 단독매칭은 동명이인 신규회원에게 과거 미수금을 잘못 붙일 수 있어 금지.
    nv, nn = _norm(member.vehicle_number), _norm(member.name)
    if not nv or not nn:
        return None
    return _seed_by_combo.get((nv, nn))


def _infer_account(member) -> str:
    # 가입일자 형식은 26.08.27 같은 2자리 연도도 공식 공통 판정함수에서 가입으로 인정한다.
    return "협회비" if is_association_member(getattr(member, "membership_date", None)) else "관리비"


def _member_registration_date(member) -> date:
    """신규 첫 부과 기준일: 인가/등록일 우선, 없으면 실제 생성일, 마지막으로 가입일자."""
    return (
        _parse_date(getattr(member, "approval_date", None))
        or _parse_date(getattr(member, "created_at", None))
        or _parse_date(getattr(member, "membership_date", None))
        or datetime.now(KST).date()
    )


def _make_profile(member, seed=None) -> ReceivableProfile:
    if seed:
        acct = seed.get("account_type") or _infer_account(member)
        last_month = int(seed.get("last_month") or 0)
        if last_month:
            first_charge = _first_of_next_month(date(2026, last_month, 1))
        else:
            first_charge = _first_of_next_month(_member_registration_date(member))
        return ReceivableProfile(
            member_id=member.id,
            account_type=acct,
            unit_fee=ACCOUNT_FEES.get(acct, 5000),
            vehicle_count=max(1, int(seed.get("vehicle_count") or 1)),
            first_charge_date=first_charge.isoformat(),
            legacy_balance=int(seed.get("current_arrears") or 0),
            legacy_months=seed.get("months") or [],
            legacy_source_row=seed.get("source_row"),
            legacy_note=seed.get("legacy_note") or None,
        )

    acct = _infer_account(member)
    first_charge = _first_of_next_month(_member_registration_date(member))
    # 기존 원장 컷오버 이전 회원을 profile 신규생성했다고 과거부터 소급부과하지 않는다.
    cutover = date(2026, 9, 1)
    if first_charge < cutover:
        first_charge = cutover
    return ReceivableProfile(
        member_id=member.id,
        account_type=acct,
        unit_fee=ACCOUNT_FEES.get(acct, 5000),
        vehicle_count=1,
        first_charge_date=first_charge.isoformat(),
        legacy_balance=0,
        legacy_months=[],
    )


def _eligible_missing_profiles_query(db: Session):
    return (
        db.query(models.LicenseHolder)
        .outerjoin(ReceivableProfile, ReceivableProfile.member_id == models.LicenseHolder.id)
        .filter(ReceivableProfile.id.is_(None))
        .filter(or_(models.LicenseHolder.status.is_(None), models.LicenseHolder.status != "pending"))
        .filter(or_(
            models.LicenseHolder.deleted_at.is_(None),
            models.LicenseHolder.closure_id.isnot(None),
            models.LicenseHolder.status != "active",
        ))
        .order_by(models.LicenseHolder.id.asc())
    )


def _sync_missing_profiles_fast(db: Session, limit: int = 100) -> int:
    """신규회원 즉시 노출용. 전체 3천명을 읽지 않고 '프로필 없는 회원'만 인덱스로 조회."""
    members = _eligible_missing_profiles_query(db).limit(limit).all()
    if not members:
        return 0
    created = 0
    for member in members:
        # 관리자 단순삭제(폐업기록 없음)는 미수금 신규대상에서 제외.
        if member.deleted_at is not None and not member.closure_id and (member.status or "active") == "active":
            continue
        db.add(_make_profile(member, _match_seed(member)))
        created += 1
    if created:
        try:
            db.commit()
        except IntegrityError:
            # 여러 직원이 같은 순간 화면을 열어도 unique(member_id)로 중복 방지.
            db.rollback()
    return created


def _sync_profiles_full(db: Session) -> int:
    created = 0
    while True:
        batch = _eligible_missing_profiles_query(db).limit(500).all()
        if not batch:
            break
        batch_created = 0
        for member in batch:
            if member.deleted_at is not None and not member.closure_id and (member.status or "active") == "active":
                # 다시 조회되지 않도록 이 경우는 full sync에서 건너뛰되 loop 탈출 방지
                continue
            db.add(_make_profile(member, _match_seed(member)))
            batch_created += 1
        if not batch_created:
            break
        try:
            db.commit()
            created += batch_created
        except IntegrityError:
            db.rollback()
            # 다른 worker가 동시에 생성한 경우 재조회
    return created


def _repair_account_types(db: Session) -> int:
    """legacy/수동계정은 보존하고, 나머지만 가입일자 기준으로 정합성 보정."""
    rows = (
        db.query(ReceivableProfile, models.LicenseHolder)
        .join(models.LicenseHolder, models.LicenseHolder.id == ReceivableProfile.member_id)
        .filter(ReceivableProfile.legacy_source_row.is_(None))
        .filter(func.coalesce(ReceivableProfile.account_manual_override, 0) != 1)
        .all()
    )
    fixed = 0
    for p, member in rows:
        correct = _infer_account(member)
        if p.account_type != correct or int(p.unit_fee or 0) != ACCOUNT_FEES.get(correct, 5000):
            p.account_type = correct
            p.unit_fee = ACCOUNT_FEES.get(correct, 5000)
            fixed += 1
    if fixed:
        db.commit()
    return fixed


def _closure_map(db: Session):
    rows = (
        db.query(models.Closure)
        .filter(models.Closure.deleted_at.is_(None))
        .order_by(models.Closure.id.desc())
        .all()
    )
    out = {}
    for c in rows:
        if c.member_id and c.member_id not in out:
            out[c.member_id] = c
    return out


def _sync_charges(db: Session) -> int:
    """월 자동부과 생성. 수동 sync/백그라운드에서만 실행되어 화면 조회를 막지 않는다."""
    today = datetime.now(KST).date()
    members = {m.id: m for m in db.query(models.LicenseHolder).all()}
    closures = _closure_map(db)
    existing = {
        (mid, month)
        for mid, month in db.query(ReceivableCharge.member_id, ReceivableCharge.billing_month).all()
    }
    added = 0
    for p in db.query(ReceivableProfile).all():
        member = members.get(p.member_id)
        if not member or not p.first_charge_date:
            continue
        start = _parse_date(p.first_charge_date)
        if not start:
            continue
        end = today
        if not _is_active(member):
            cl = closures.get(member.id)
            close_d = _parse_date(getattr(cl, "closure_date", None)) if cl else None
            close_d = close_d or _parse_date(getattr(member, "updated_at", None)) or today
            end = close_d
        if date(start.year, start.month, 1) > date(end.year, end.month, 1):
            continue
        for month_start in _month_iter(start, end):
            key = (p.member_id, month_start.strftime("%Y-%m"))
            if key in existing:
                continue
            db.add(
                ReceivableCharge(
                    member_id=p.member_id,
                    billing_month=key[1],
                    amount=int(p.unit_fee or 0) * int(p.vehicle_count or 1),
                    account_type=p.account_type,
                    source="auto",
                )
            )
            existing.add(key)
            added += 1
    if added:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # UNIQUE(member_id,billing_month)이 최종적으로 중복부과를 차단한다.
    return added


def _sync_all(db: Session):
    # 계정 정합성을 먼저 맞춘 뒤 charge를 생성해야 새 월부터 올바른 계정 snapshot이 들어간다.
    p = _sync_profiles_full(db)
    r = _repair_account_types(db)
    c = _sync_charges(db)
    return {"profiles_created": p, "charges_created": c, "accounts_repaired": r}


# 조회 API는 즉시 응답하고, 무거운 전체 동기화는 응답 뒤 백그라운드에서만 실행.
_BG_SYNC_INTERVAL_SECONDS = 600
_bg_lock = threading.Lock()
_last_bg_sync_ts = 0.0
_bg_sync_queued = False


def _background_sync_runner():
    global _last_bg_sync_ts, _bg_sync_queued
    if not _bg_lock.acquire(blocking=False):
        return
    db = SessionLocal()
    try:
        _sync_all(db)
        _last_bg_sync_ts = time.monotonic()
    except Exception:
        db.rollback()
    finally:
        db.close()
        _bg_sync_queued = False
        _bg_lock.release()


def _schedule_background_sync(background_tasks: BackgroundTasks):
    global _bg_sync_queued
    if _bg_sync_queued:
        return
    if time.monotonic() - _last_bg_sync_ts < _BG_SYNC_INTERVAL_SECONDS:
        return
    _bg_sync_queued = True
    background_tasks.add_task(_background_sync_runner)


def _charge_payment_subqueries(db: Session):
    charges_sq = (
        db.query(
            ReceivableCharge.member_id.label("member_id"),
            func.coalesce(func.sum(ReceivableCharge.amount), 0).label("charge_total"),
        )
        .group_by(ReceivableCharge.member_id)
        .subquery()
    )
    payments_sq = (
        db.query(
            ReceivablePayment.member_id.label("member_id"),
            func.coalesce(func.sum(ReceivablePayment.amount), 0).label("payment_total"),
        )
        .filter(ReceivablePayment.cancelled_at.is_(None))
        .group_by(ReceivablePayment.member_id)
        .subquery()
    )
    return charges_sq, payments_sq


def _latest_contact_subquery(db: Session):
    ranked = (
        db.query(
            ReceivableContactLog.member_id.label("member_id"),
            ReceivableContactLog.status.label("status"),
            ReceivableContactLog.contact_date.label("contact_date"),
            func.row_number().over(
                partition_by=ReceivableContactLog.member_id,
                order_by=(ReceivableContactLog.contact_date.desc(), ReceivableContactLog.id.desc()),
            ).label("rn"),
        )
        .subquery()
    )
    return (
        db.query(
            ranked.c.member_id.label("member_id"),
            ranked.c.status.label("status"),
            ranked.c.contact_date.label("contact_date"),
        )
        .filter(ranked.c.rn == 1)
        .subquery()
    )


def _latest_closure_subquery(db: Session):
    ranked = (
        db.query(
            models.Closure.member_id.label("member_id"),
            models.Closure.closure_date.label("closure_date"),
            models.Closure.closure_type.label("closure_type"),
            func.row_number().over(
                partition_by=models.Closure.member_id,
                order_by=models.Closure.id.desc(),
            ).label("rn"),
        )
        .filter(models.Closure.deleted_at.is_(None), models.Closure.member_id.isnot(None))
        .subquery()
    )
    return (
        db.query(
            ranked.c.member_id.label("member_id"),
            ranked.c.closure_date.label("closure_date"),
            ranked.c.closure_type.label("closure_type"),
        )
        .filter(ranked.c.rn == 1)
        .subquery()
    )


def _billing_state(active: bool, balance: int, first_charge_date: str) -> str:
    today = datetime.now(KST).date()
    first = _parse_date(first_charge_date)
    if not active:
        return "폐업 미수" if balance > 0 else "폐업 완납"
    if first and first > today:
        return "부과대기"
    if balance > 0:
        return "미수"
    return "완납"


def _serialize_member(member, profile, balance, contact_status="미연락", last_contact_date="", closure_date="", closure_type=""):
    active = _is_active(member)
    canonical_membership = "가입" if is_association_member(getattr(member, "membership_date", None)) else "미가입"
    return {
        "member_id": member.id,
        "name": member.name or "",
        "vehicle_number": member.vehicle_number or "",
        "region": member.region or "",
        "category": member.category or "",
        "account_type": profile.account_type,
        "unit_fee": int(profile.unit_fee or 0),
        "vehicle_count": int(profile.vehicle_count or 1),
        "membership_status": canonical_membership,
        "membership_date": member.membership_date or "",
        "member_status": "활성" if active else "폐업/변동",
        "active": active,
        "balance": int(balance),
        "first_charge_date": profile.first_charge_date or "",
        "billing_state": _billing_state(active, int(balance), profile.first_charge_date or ""),
        "closure_date": closure_date or "",
        "closure_type": closure_type or "",
        "contact_status": contact_status or "미연락",
        "last_contact_date": last_contact_date or "",
    }


def _ensure_profile_for_member(db: Session, member_id: int):
    p = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == member_id).first()
    if p:
        return p
    member = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == member_id).first()
    if not member:
        return None
    if (member.status or "active") == "pending":
        return None
    p = _make_profile(member, _match_seed(member))
    db.add(p)
    try:
        db.commit()
        db.refresh(p)
    except IntegrityError:
        db.rollback()
        p = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == member_id).first()
    return p


@router.get("/receivables")
def receivables_page():
    return FileResponse(STATIC_DIR / "receivables.html")


@router.get("/api/receivables/meta")
def meta(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    regions = [
        r[0]
        for r in db.query(models.LicenseHolder.region)
        .filter(models.LicenseHolder.region.isnot(None), models.LicenseHolder.region != "")
        .distinct()
        .order_by(models.LicenseHolder.region.asc())
        .all()
    ]
    return {"regions": regions, "account_types": list(ACCOUNT_FEES), "contact_statuses": sorted(CONTACT_STATUSES)}


@router.get("/api/receivables/sync")
def sync_receivables(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = _sync_all(db)
    return {"ok": True, **result}


@router.get("/api/receivables/verify")
def verify_legacy_import(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _load_seed()
    seed_rows = _seed_cache or []
    seed_total = sum(int(r.get("current_arrears") or 0) for r in seed_rows)
    seed_by_account = {}
    for r in seed_rows:
        k = r.get("account_type") or "미상"
        seed_by_account[k] = seed_by_account.get(k, 0) + 1

    profiles = db.query(ReceivableProfile).filter(ReceivableProfile.legacy_source_row.isnot(None)).all()
    db_total_legacy_balance = sum(int(p.legacy_balance or 0) for p in profiles)
    db_by_account = {}
    for p in profiles:
        db_by_account[p.account_type] = db_by_account.get(p.account_type, 0) + 1

    charges_sq, payments_sq = _charge_payment_subqueries(db)
    balance_expr = (
        func.coalesce(ReceivableProfile.legacy_balance, 0)
        + func.coalesce(charges_sq.c.charge_total, 0)
        - func.coalesce(payments_sq.c.payment_total, 0)
    )
    db_current_total = (
        db.query(func.coalesce(func.sum(balance_expr), 0))
        .outerjoin(charges_sq, charges_sq.c.member_id == ReceivableProfile.member_id)
        .outerjoin(payments_sq, payments_sq.c.member_id == ReceivableProfile.member_id)
        .filter(ReceivableProfile.legacy_source_row.isnot(None))
        .scalar()
        or 0
    )

    return {
        "legacy_json": {
            "row_count": len(seed_rows),
            "total_arrears_2026_08": seed_total,
            "by_account_type": seed_by_account,
        },
        "current_db": {
            "matched_profile_count": len(profiles),
            "total_legacy_balance_asis": db_total_legacy_balance,
            "total_current_balance_incl_auto_charges": int(db_current_total),
            "by_account_type": db_by_account,
        },
        "match": {
            "row_count_matches": len(seed_rows) == len(profiles),
            "legacy_balance_matches": seed_total == db_total_legacy_balance,
        },
    }


@router.get("/api/receivables/summary")
def summary(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    charges_sq, payments_sq = _charge_payment_subqueries(db)
    balance_expr = (
        func.coalesce(ReceivableProfile.legacy_balance, 0)
        + func.coalesce(charges_sq.c.charge_total, 0)
        - func.coalesce(payments_sq.c.payment_total, 0)
    )
    positive_balance = case((balance_expr > 0, balance_expr), else_=0)
    active_cond = _active_sql()
    closed_cond = _closed_sql()
    today_iso = datetime.now(KST).date().isoformat()
    pending_cond = and_(active_cond, ReceivableProfile.first_charge_date > today_iso)

    row = (
        db.query(
            func.coalesce(func.sum(case((active_cond, 1), else_=0)), 0).label("active_members"),
            func.coalesce(func.sum(case((and_(active_cond, balance_expr > 0), 1), else_=0)), 0).label("active_arrears_members"),
            func.coalesce(func.sum(case((active_cond, positive_balance), else_=0)), 0).label("active_arrears_total"),
            func.coalesce(func.sum(case((closed_cond, 1), else_=0)), 0).label("closed_members"),
            func.coalesce(func.sum(case((and_(closed_cond, balance_expr > 0), 1), else_=0)), 0).label("closed_arrears_members"),
            func.coalesce(func.sum(case((closed_cond, positive_balance), else_=0)), 0).label("closed_arrears_total"),
            func.coalesce(func.sum(case((pending_cond, 1), else_=0)), 0).label("pending_members"),
        )
        .select_from(ReceivableProfile)
        .join(models.LicenseHolder, models.LicenseHolder.id == ReceivableProfile.member_id)
        .outerjoin(charges_sq, charges_sq.c.member_id == ReceivableProfile.member_id)
        .outerjoin(payments_sq, payments_sq.c.member_id == ReceivableProfile.member_id)
        .one()
    )

    today_paid = (
        db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0))
        .filter(
            ReceivablePayment.payment_date == today_iso,
            ReceivablePayment.cancelled_at.is_(None),
        )
        .scalar()
        or 0
    )
    _schedule_background_sync(background_tasks)
    return {
        "active_members": int(row.active_members or 0),
        "active_arrears_members": int(row.active_arrears_members or 0),
        "active_arrears_total": int(row.active_arrears_total or 0),
        "closed_members": int(row.closed_members or 0),
        "closed_arrears_members": int(row.closed_arrears_members or 0),
        "closed_arrears_total": int(row.closed_arrears_total or 0),
        "pending_members": int(row.pending_members or 0),
        "today_paid": int(today_paid),
    }


@router.get("/api/receivables/members")
def list_members(
    background_tasks: BackgroundTasks,
    scope: str = Query("active", pattern="^(active|closed|all)$"),
    arrears_only: bool = False,
    q: str = "",
    region: str = "",
    account_type: str = "",
    contact_status: str = "",
    billing_status: str = Query("", pattern="^(|pending|arrears|settled)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=10, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # 핵심: 신규등록 회원만 즉시 소량 동기화. 전체 3,239명 full sync는 하지 않는다.
    _sync_missing_profiles_fast(db, limit=100)

    charges_sq, payments_sq = _charge_payment_subqueries(db)
    latest_contact_sq = _latest_contact_subquery(db)
    latest_closure_sq = _latest_closure_subquery(db)
    balance_expr = (
        func.coalesce(ReceivableProfile.legacy_balance, 0)
        + func.coalesce(charges_sq.c.charge_total, 0)
        - func.coalesce(payments_sq.c.payment_total, 0)
    ).label("balance")

    query = (
        db.query(
            models.LicenseHolder,
            ReceivableProfile,
            balance_expr,
            latest_contact_sq.c.status.label("contact_status"),
            latest_contact_sq.c.contact_date.label("last_contact_date"),
            latest_closure_sq.c.closure_date.label("closure_date"),
            latest_closure_sq.c.closure_type.label("closure_type"),
        )
        .join(ReceivableProfile, ReceivableProfile.member_id == models.LicenseHolder.id)
        .outerjoin(charges_sq, charges_sq.c.member_id == ReceivableProfile.member_id)
        .outerjoin(payments_sq, payments_sq.c.member_id == ReceivableProfile.member_id)
        .outerjoin(latest_contact_sq, latest_contact_sq.c.member_id == models.LicenseHolder.id)
        .outerjoin(latest_closure_sq, latest_closure_sq.c.member_id == models.LicenseHolder.id)
    )

    if scope == "active":
        query = query.filter(_active_sql())
    elif scope == "closed":
        query = query.filter(_closed_sql())

    if arrears_only:
        query = query.filter(balance_expr > 0)
    if region:
        query = query.filter(models.LicenseHolder.region == region)
    if account_type:
        query = query.filter(ReceivableProfile.account_type == account_type)
    if contact_status:
        if contact_status == "미연락":
            query = query.filter(or_(latest_contact_sq.c.status.is_(None), latest_contact_sq.c.status == "미연락"))
        else:
            query = query.filter(latest_contact_sq.c.status == contact_status)

    today_iso = datetime.now(KST).date().isoformat()
    if billing_status == "pending":
        query = query.filter(_active_sql(), ReceivableProfile.first_charge_date > today_iso)
    elif billing_status == "arrears":
        query = query.filter(balance_expr > 0)
    elif billing_status == "settled":
        query = query.filter(balance_expr <= 0)

    raw_q = (q or "").strip()
    if raw_q:
        pat = f"%{raw_q}%"
        query = query.filter(
            or_(
                models.LicenseHolder.name.ilike(pat),
                models.LicenseHolder.vehicle_number.ilike(pat),
                models.LicenseHolder.management_number.ilike(pat),
                models.LicenseHolder.mobile.ilike(pat),
            )
        )

    total = query.order_by(None).with_entities(func.count()).scalar() or 0
    rows = (
        query.order_by(balance_expr.desc(), models.LicenseHolder.name.asc(), models.LicenseHolder.vehicle_number.asc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    items = [
        _serialize_member(
            member,
            profile,
            int(balance or 0),
            contact_status or "미연락",
            last_contact_date or "",
            closure_date or "",
            closure_type or "",
        )
        for member, profile, balance, contact_status, last_contact_date, closure_date, closure_type in rows
    ]
    _schedule_background_sync(background_tasks)
    return {
        "items": items,
        "count": int(total),
        "page": page,
        "limit": limit,
        "pages": max(1, (int(total) + limit - 1) // limit),
    }


@router.get("/api/receivables/members/{member_id}")
def member_detail(
    member_id: int,
    year: int = 2026,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    member = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == member_id).first()
    if not member:
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    profile = _ensure_profile_for_member(db, member_id)
    if not profile:
        raise HTTPException(404, "미수금 프로필을 찾을 수 없습니다.")

    charge_total = (
        db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0))
        .filter(ReceivableCharge.member_id == member_id)
        .scalar()
        or 0
    )
    payment_total = (
        db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0))
        .filter(ReceivablePayment.member_id == member_id, ReceivablePayment.cancelled_at.is_(None))
        .scalar()
        or 0
    )
    balance = int(profile.legacy_balance or 0) + int(charge_total) - int(payment_total)

    closure = (
        db.query(models.Closure)
        .filter(models.Closure.member_id == member_id, models.Closure.deleted_at.is_(None))
        .order_by(models.Closure.id.desc())
        .first()
    )
    latest = (
        db.query(ReceivableContactLog)
        .filter(ReceivableContactLog.member_id == member_id)
        .order_by(ReceivableContactLog.contact_date.desc(), ReceivableContactLog.id.desc())
        .first()
    )

    program_charges = (
        db.query(ReceivableCharge)
        .filter(
            ReceivableCharge.member_id == member_id,
            ReceivableCharge.billing_month.like(f"{year}-%"),
        )
        .all()
    )
    program_payments = (
        db.query(ReceivablePayment)
        .filter(
            ReceivablePayment.member_id == member_id,
            ReceivablePayment.cancelled_at.is_(None),
            ReceivablePayment.payment_date.like(f"{year}-%"),
        )
        .all()
    )
    ch_by_month = {int(c.billing_month[-2:]): int(c.amount) for c in program_charges}
    pay_by_month = {}
    pay_dates = {}
    for p in program_payments:
        try:
            mm = int(p.payment_date[5:7])
        except Exception:
            continue
        pay_by_month[mm] = pay_by_month.get(mm, 0) + int(p.amount)
        pay_dates.setdefault(mm, []).append(p.payment_date)

    legacy_by_month = {}
    if year == 2026:
        for row in profile.legacy_months or []:
            legacy_by_month[int(row.get("month") or 0)] = row

    if year <= 2026:
        running = 0
    else:
        before_charges = (
            db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0))
            .filter(ReceivableCharge.member_id == member_id, ReceivableCharge.billing_month < f"{year}-01")
            .scalar()
            or 0
        )
        before_payments = (
            db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0))
            .filter(
                ReceivablePayment.member_id == member_id,
                ReceivablePayment.cancelled_at.is_(None),
                ReceivablePayment.payment_date < f"{year}-01-01",
            )
            .scalar()
            or 0
        )
        running = int(profile.legacy_balance or 0) + int(before_charges) - int(before_payments)

    monthly = []
    for m in range(1, 13):
        legacy = legacy_by_month.get(m) or {}
        if legacy.get("arrears") is not None:
            running = int(legacy.get("arrears") or 0)
        auto_charge = int(ch_by_month.get(m, 0) or 0)
        extra_paid = int(pay_by_month.get(m, 0) or 0)
        running += auto_charge
        running -= extra_paid
        monthly.append(
            {
                "month": m,
                "legacy_billed_total": legacy.get("billed_total"),
                "legacy_payment": legacy.get("payment"),
                "legacy_payment_date": legacy.get("payment_date") or "",
                "legacy_arrears": legacy.get("arrears"),
                "auto_charge": auto_charge,
                "additional_payment": extra_paid,
                "additional_payment_dates": pay_dates.get(m, []),
                "current_arrears": running,
            }
        )

    payments = (
        db.query(ReceivablePayment)
        .filter(ReceivablePayment.member_id == member_id, ReceivablePayment.cancelled_at.is_(None))
        .order_by(ReceivablePayment.payment_date.desc(), ReceivablePayment.id.desc())
        .limit(100)
        .all()
    )
    contacts = (
        db.query(ReceivableContactLog)
        .filter(ReceivableContactLog.member_id == member_id)
        .order_by(ReceivableContactLog.contact_date.desc(), ReceivableContactLog.id.desc())
        .limit(100)
        .all()
    )

    member_json = _serialize_member(
        member,
        profile,
        balance,
        latest.status if latest else "미연락",
        latest.contact_date if latest else "",
        getattr(closure, "closure_date", "") or "",
        getattr(closure, "closure_type", "") or "",
    )
    return {
        "member": member_json,
        "profile": {
            "account_type": profile.account_type,
            "unit_fee": int(profile.unit_fee or 0),
            "vehicle_count": int(profile.vehicle_count or 1),
            "first_charge_date": profile.first_charge_date or "",
            "legacy_balance": int(profile.legacy_balance or 0),
            "legacy_note": profile.legacy_note or "",
        },
        "monthly": monthly,
        "payments": [
            {
                "id": p.id,
                "payment_date": p.payment_date,
                "amount": p.amount,
                "method": p.method or "",
                "memo": p.memo or "",
                "created_by": p.created_by or "",
            }
            for p in payments
        ],
        "contacts": [
            {
                "id": c.id,
                "contact_date": c.contact_date,
                "contact_method": c.contact_method,
                "status": c.status,
                "memo": c.memo or "",
                "created_by": c.created_by or "",
            }
            for c in contacts
        ],
    }


@router.post("/api/receivables/members/{member_id}/payments")
def add_payment(
    member_id: int,
    payload: PaymentIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    d = _parse_date(payload.payment_date)
    if not d:
        raise HTTPException(400, "입금일 형식이 올바르지 않습니다.")
    profile = _ensure_profile_for_member(db, member_id)
    if not profile:
        raise HTTPException(404, "미수금 프로필을 찾을 수 없습니다.")
    row = ReceivablePayment(
        member_id=member_id,
        payment_date=d.isoformat(),
        amount=int(payload.amount),
        method=(payload.method or "").strip() or None,
        memo=(payload.memo or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "payment_id": row.id}


@router.delete("/api/receivables/payments/{payment_id}")
def cancel_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.query(ReceivablePayment).filter(ReceivablePayment.id == payment_id).first()
    if not row:
        raise HTTPException(404, "입금내역을 찾을 수 없습니다.")
    if row.cancelled_at is None:
        row.cancelled_at = datetime.now(KST)
        row.cancelled_by = _user_name(current_user)
        db.commit()
    return {"ok": True}


@router.post("/api/receivables/members/{member_id}/contacts")
def add_contact(
    member_id: int,
    payload: ContactIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if payload.status not in CONTACT_STATUSES:
        raise HTTPException(400, "지원하지 않는 연락상태입니다.")
    d = _parse_date(payload.contact_date)
    if not d:
        raise HTTPException(400, "연락일 형식이 올바르지 않습니다.")
    if not db.query(models.LicenseHolder).filter(models.LicenseHolder.id == member_id).first():
        raise HTTPException(404, "회원을 찾을 수 없습니다.")
    row = ReceivableContactLog(
        member_id=member_id,
        contact_date=d.isoformat(),
        contact_method=payload.contact_method.strip() or "전화",
        status=payload.status,
        memo=(payload.memo or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "contact_id": row.id}


@router.patch("/api/receivables/members/{member_id}/account")
def update_account(
    member_id: int,
    payload: AccountIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if payload.account_type not in ACCOUNT_FEES:
        raise HTTPException(400, "계정은 협회비/관리비/70세 중 하나여야 합니다.")
    profile = _ensure_profile_for_member(db, member_id)
    if not profile:
        raise HTTPException(404, "미수금 프로필을 찾을 수 없습니다.")
    profile.account_type = payload.account_type
    profile.unit_fee = ACCOUNT_FEES[payload.account_type]
    profile.vehicle_count = payload.vehicle_count
    profile.account_manual_override = 1
    db.commit()
    return {"ok": True}
