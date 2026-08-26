from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user
from app import models
from app.receivables_models import (
    ReceivableProfile,
    ReceivableCharge,
    ReceivablePayment,
    ReceivableContactLog,
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
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).date()
        except Exception:
            pass
    m = re.search(r"(20\d{2})\D*(\d{1,2})\D*(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    return None


def _first_of_next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _month_iter(start: date, end: date):
    cur = date(start.year, start.month, 1)
    end = date(end.year, end.month, 1)
    while cur <= end:
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
    nv, nn, nr = _norm(member.vehicle_number), _norm(member.name), _norm(member.region)
    hit = _seed_by_combo.get((nv, nn))
    if hit:
        return hit
    if nv:
        candidates = _seed_by_vehicle.get(nv, [])
        if len(candidates) == 1:
            return candidates[0]
        for r in candidates:
            if _norm(r.get("name")) == nn:
                return r
    candidates = _seed_by_name_region.get((nn, nr), [])
    if len(candidates) == 1:
        return candidates[0]
    return None


def _infer_account(member) -> str:
    status = str(getattr(member, "membership_status", "") or "").strip()
    return "협회비" if status == "가입" else "관리비"


def _member_registration_date(member) -> date:
    return (
        _parse_date(getattr(member, "approval_date", None))
        or _parse_date(getattr(member, "membership_date", None))
        or _parse_date(getattr(member, "created_at", None))
        or datetime.now(KST).date()
    )


def _closure_map(db: Session):
    rows = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None)).order_by(models.Closure.id.desc()).all()
    out = {}
    for c in rows:
        if c.member_id and c.member_id not in out:
            out[c.member_id] = c
    return out


def _sync_profiles(db: Session):
    """기존 회원 기능을 수정하지 않고 회원마스터를 읽어 미수금 프로필만 보강한다."""
    members = db.query(models.LicenseHolder).all()
    existing = {p.member_id: p for p in db.query(ReceivableProfile).all()}
    created = 0
    for member in members:
        # 관리자 삭제 데이터는 제외. 폐업/양도/이관으로 연결된 회원은 보존한다.
        if member.deleted_at is not None and not member.closure_id and (member.status or "active") == "active":
            continue
        if member.id in existing:
            continue
        seed = _match_seed(member)
        if seed:
            acct = seed.get("account_type") or _infer_account(member)
            last_month = int(seed.get("last_month") or 0)
            if last_month:
                first_charge = _first_of_next_month(date(2026, last_month, 1))
            else:
                first_charge = _first_of_next_month(_member_registration_date(member))
            profile = ReceivableProfile(
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
        else:
            acct = _infer_account(member)
            first_charge = _first_of_next_month(_member_registration_date(member))
            # 기존 장부 컷오버 이전 소급부과 방지. 신규등록자는 승인일 기준 다음달부터 정상 부과.
            cutover = date(2026, 9, 1)
            if first_charge < cutover:
                first_charge = cutover
            profile = ReceivableProfile(
                member_id=member.id,
                account_type=acct,
                unit_fee=ACCOUNT_FEES.get(acct, 5000),
                vehicle_count=1,
                first_charge_date=first_charge.isoformat(),
                legacy_balance=0,
                legacy_months=[],
            )
        db.add(profile)
        existing[member.id] = profile
        created += 1
    if created:
        db.commit()
    return created


def _sync_charges(db: Session):
    today = datetime.now(KST).date()
    members = {m.id: m for m in db.query(models.LicenseHolder).all()}
    closures = _closure_map(db)
    existing = {(c.member_id, c.billing_month) for c in db.query(ReceivableCharge.member_id, ReceivableCharge.billing_month).all()}
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
            db.add(ReceivableCharge(
                member_id=p.member_id,
                billing_month=key[1],
                amount=int(p.unit_fee or 0) * int(p.vehicle_count or 1),
                account_type=p.account_type,
                source="auto",
            ))
            existing.add(key)
            added += 1
    if added:
        db.commit()
    return added


def _sync_all(db: Session):
    p = _sync_profiles(db)
    c = _sync_charges(db)
    return {"profiles_created": p, "charges_created": c}


def _balance_maps(db: Session):
    charges = dict(
        db.query(ReceivableCharge.member_id, func.coalesce(func.sum(ReceivableCharge.amount), 0))
        .group_by(ReceivableCharge.member_id).all()
    )
    payments = dict(
        db.query(ReceivablePayment.member_id, func.coalesce(func.sum(ReceivablePayment.amount), 0))
        .filter(ReceivablePayment.cancelled_at.is_(None))
        .group_by(ReceivablePayment.member_id).all()
    )
    return charges, payments


def _latest_contacts(db: Session):
    logs = db.query(ReceivableContactLog).order_by(
        ReceivableContactLog.member_id.asc(),
        ReceivableContactLog.contact_date.desc(),
        ReceivableContactLog.id.desc(),
    ).all()
    out = {}
    for log in logs:
        out.setdefault(log.member_id, log)
    return out


def _serialize_member(member, profile, balance, latest_contact, closure=None):
    active = _is_active(member)
    return {
        "member_id": member.id,
        "name": member.name or "",
        "vehicle_number": member.vehicle_number or "",
        "region": member.region or "",
        "category": member.category or "",
        "account_type": profile.account_type,
        "unit_fee": int(profile.unit_fee or 0),
        "vehicle_count": int(profile.vehicle_count or 1),
        "membership_status": member.membership_status or "",
        "member_status": "활성" if active else "폐업/변동",
        "active": active,
        "balance": int(balance),
        "closure_date": getattr(closure, "closure_date", "") or "",
        "closure_type": getattr(closure, "closure_type", "") or "",
        "contact_status": latest_contact.status if latest_contact else "미연락",
        "last_contact_date": latest_contact.contact_date if latest_contact else "",
    }


@router.get("/receivables")
def receivables_page():
    return FileResponse(STATIC_DIR / "receivables.html")


@router.get("/api/receivables/sync")
def sync_receivables(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {"ok": True, **_sync_all(db)}


@router.get("/api/receivables/summary")
def summary(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _sync_all(db)
    members = {m.id: m for m in db.query(models.LicenseHolder).all()}
    profiles = db.query(ReceivableProfile).all()
    charges, payments = _balance_maps(db)
    active_count = closed_count = active_arrears_members = closed_arrears_members = 0
    active_total = closed_total = 0
    for p in profiles:
        m = members.get(p.member_id)
        if not m:
            continue
        bal = int(p.legacy_balance or 0) + int(charges.get(p.member_id, 0) or 0) - int(payments.get(p.member_id, 0) or 0)
        if _is_active(m):
            active_count += 1
            active_total += max(bal, 0)
            if bal > 0: active_arrears_members += 1
        else:
            closed_count += 1
            closed_total += max(bal, 0)
            if bal > 0: closed_arrears_members += 1
    today = datetime.now(KST).date().isoformat()
    today_paid = db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0)).filter(
        ReceivablePayment.payment_date == today,
        ReceivablePayment.cancelled_at.is_(None),
    ).scalar() or 0
    return {
        "active_members": active_count,
        "active_arrears_members": active_arrears_members,
        "active_arrears_total": active_total,
        "closed_members": closed_count,
        "closed_arrears_members": closed_arrears_members,
        "closed_arrears_total": closed_total,
        "today_paid": int(today_paid),
    }


@router.get("/api/receivables/members")
def list_members(
    scope: str = Query("active", pattern="^(active|closed|all)$"),
    arrears_only: bool = False,
    q: str = "",
    region: str = "",
    account_type: str = "",
    contact_status: str = "",
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _sync_all(db)
    members = {m.id: m for m in db.query(models.LicenseHolder).all()}
    profiles = db.query(ReceivableProfile).all()
    closures = _closure_map(db)
    charges, payments = _balance_maps(db)
    contacts = _latest_contacts(db)
    nq = _norm(q)
    result = []
    for p in profiles:
        m = members.get(p.member_id)
        if not m:
            continue
        active = _is_active(m)
        if scope == "active" and not active: continue
        if scope == "closed" and active: continue
        bal = int(p.legacy_balance or 0) + int(charges.get(p.member_id, 0) or 0) - int(payments.get(p.member_id, 0) or 0)
        if arrears_only and bal <= 0: continue
        if region and (m.region or "") != region: continue
        if account_type and p.account_type != account_type: continue
        latest = contacts.get(m.id)
        latest_status = latest.status if latest else "미연락"
        if contact_status and latest_status != contact_status: continue
        if nq and nq not in _norm(m.name) and nq not in _norm(m.vehicle_number): continue
        result.append(_serialize_member(m, p, bal, latest, closures.get(m.id)))
    result.sort(key=lambda x: (-x["balance"], x["name"], x["vehicle_number"]))
    return {"items": result, "count": len(result)}


@router.get("/api/receivables/members/{member_id}")
def member_detail(
    member_id: int,
    year: int = 2026,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _sync_all(db)
    member = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == member_id).first()
    profile = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == member_id).first()
    if not member or not profile:
        raise HTTPException(404, "회원 또는 미수금 프로필을 찾을 수 없습니다.")
    charges_map, payments_map = _balance_maps(db)
    closure = _closure_map(db).get(member_id)
    latest = _latest_contacts(db).get(member_id)
    balance = int(profile.legacy_balance or 0) + int(charges_map.get(member_id, 0) or 0) - int(payments_map.get(member_id, 0) or 0)

    program_charges = db.query(ReceivableCharge).filter(
        ReceivableCharge.member_id == member_id,
        ReceivableCharge.billing_month.like(f"{year}-%"),
    ).all()
    program_payments = db.query(ReceivablePayment).filter(
        ReceivablePayment.member_id == member_id,
        ReceivablePayment.cancelled_at.is_(None),
        ReceivablePayment.payment_date.like(f"{year}-%"),
    ).all()
    ch_by_month = {int(c.billing_month[-2:]): int(c.amount) for c in program_charges}
    pay_by_month = {}
    pay_dates = {}
    for p in program_payments:
        try: mm = int(p.payment_date[5:7])
        except: continue
        pay_by_month[mm] = pay_by_month.get(mm, 0) + int(p.amount)
        pay_dates.setdefault(mm, []).append(p.payment_date)

    legacy_by_month = {}
    if year == 2026:
        for row in (profile.legacy_months or []):
            legacy_by_month[int(row.get("month") or 0)] = row

    # 연도 시작 잔액: 2026은 원본 장부값을 월별로 이어가고, 이후 연도는 전년도말 잔액에서 시작
    if year <= 2026:
        running = 0
    else:
        before_charges = db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0)).filter(
            ReceivableCharge.member_id == member_id,
            ReceivableCharge.billing_month < f"{year}-01",
        ).scalar() or 0
        before_payments = db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0)).filter(
            ReceivablePayment.member_id == member_id,
            ReceivablePayment.cancelled_at.is_(None),
            ReceivablePayment.payment_date < f"{year}-01-01",
        ).scalar() or 0
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
        monthly.append({
            "month": m,
            "legacy_billed_total": legacy.get("billed_total"),
            "legacy_payment": legacy.get("payment"),
            "legacy_payment_date": legacy.get("payment_date") or "",
            "legacy_arrears": legacy.get("arrears"),
            "auto_charge": auto_charge,
            "additional_payment": extra_paid,
            "additional_payment_dates": pay_dates.get(m, []),
            "current_arrears": running,
        })

    payments = db.query(ReceivablePayment).filter(
        ReceivablePayment.member_id == member_id,
        ReceivablePayment.cancelled_at.is_(None),
    ).order_by(ReceivablePayment.payment_date.desc(), ReceivablePayment.id.desc()).limit(100).all()
    contacts = db.query(ReceivableContactLog).filter(
        ReceivableContactLog.member_id == member_id,
    ).order_by(ReceivableContactLog.contact_date.desc(), ReceivableContactLog.id.desc()).limit(100).all()

    return {
        "member": _serialize_member(member, profile, balance, latest, closure),
        "profile": {
            "account_type": profile.account_type,
            "unit_fee": int(profile.unit_fee or 0),
            "vehicle_count": int(profile.vehicle_count or 1),
            "first_charge_date": profile.first_charge_date or "",
            "legacy_balance": int(profile.legacy_balance or 0),
            "legacy_note": profile.legacy_note or "",
        },
        "monthly": monthly,
        "payments": [{
            "id": p.id, "payment_date": p.payment_date, "amount": p.amount,
            "method": p.method or "", "memo": p.memo or "", "created_by": p.created_by or "",
        } for p in payments],
        "contacts": [{
            "id": c.id, "contact_date": c.contact_date, "contact_method": c.contact_method,
            "status": c.status, "memo": c.memo or "", "created_by": c.created_by or "",
        } for c in contacts],
    }


@router.post("/api/receivables/members/{member_id}/payments")
def add_payment(
    member_id: int,
    payload: PaymentIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not _parse_date(payload.payment_date):
        raise HTTPException(400, "입금일 형식이 올바르지 않습니다.")
    profile = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == member_id).first()
    if not profile:
        _sync_profiles(db)
        profile = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == member_id).first()
    if not profile:
        raise HTTPException(404, "미수금 프로필을 찾을 수 없습니다.")
    row = ReceivablePayment(
        member_id=member_id,
        payment_date=_parse_date(payload.payment_date).isoformat(),
        amount=int(payload.amount),
        method=(payload.method or "").strip() or None,
        memo=(payload.memo or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(row); db.commit(); db.refresh(row)
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
    db.add(row); db.commit(); db.refresh(row)
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
    profile = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == member_id).first()
    if not profile:
        raise HTTPException(404, "미수금 프로필을 찾을 수 없습니다.")
    profile.account_type = payload.account_type
    profile.unit_fee = ACCOUNT_FEES[payload.account_type]
    profile.vehicle_count = payload.vehicle_count
    db.commit()
    return {"ok": True}
