"""2026-10-08 monthly-ledger V4 repair.

Authoritative rules:
- Corrected workbook data owns Jan-Sep 2026.
- Oct+ is calculated from the corrected Sep closing balance plus live DB charges/payments.
- The profile baseline is repaired again under a NEW state key, so a prior V2
  `already_applied` marker cannot prevent this correction.
- Pre-Oct DB rows are only neutralized idempotently; they are never physically deleted.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import crud, models
from app.auth import get_current_user, require_admin
from app.database import SessionLocal, get_db
from app.receivables_models import (
    ReceivableProfile,
    ReceivableCharge,
    ReceivablePayment,
    ReceivableContactLog,
    ReceivableSystemState,
)

router = APIRouter(
    prefix="/api/receivables/reconcile-20261008",
    tags=["수납미수금-20261008-monthly-v4"],
)
PATCH_ID = "receivables_reconcile_20261008_monthly_v4"
DATA_PATCH_IDS = {
    PATCH_ID,
    "receivables_reconcile_20261008_monthly_v3",
    "receivables_reconcile_20261008_monthly_v2",
}
STATE_KEY = PATCH_ID
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "receivables_reconcile_20261008_final.json"
MEMO = "[20261008 월별장부 전수정정 V4]"


def _n(v):
    return re.sub(r"\s+", "", str(v or "")).strip()


def _v(v):
    s = str(v or "").strip().lower().replace("강원", "")
    s = re.sub(r"호\s*$", "", s)
    return re.sub(r"[\s\-]+", "", s)


def _load():
    p = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if p.get("patch_id") not in DATA_PATCH_IDS:
        raise RuntimeError(f"patch data mismatch: {p.get('patch_id')}")
    return p


def _index(db):
    out = {}
    for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all():
        k = (_n(m.name), _v(m.vehicle_number))
        if k[0] and k[1]:
            out.setdefault(k, []).append(m)
    return out


def _pick(items):
    items = list(items or [])
    if len(items) == 1:
        return items[0], "matched"
    active = [x for x in items if (x.status or "active") == "active"]
    if len(active) == 1:
        return active[0], "matched_active_among_duplicates"
    return None, "ambiguous" if items else "unmatched"


def _profile(db, mid):
    return db.query(ReceivableProfile).filter(ReceivableProfile.member_id == mid).first()


def _append_note(p, t):
    old = (p.legacy_note or "").strip()
    if t not in old:
        p.legacy_note = (old + " / " if old else "") + t


def _legacy_months(rec):
    out = []
    for x in rec.get("months", []):
        out.append(
            {
                "month": int(x["month"]),
                "monthly_charge": int(x.get("monthly_charge") or 0),
                "charge": int(x.get("monthly_charge") or 0),
                "billed_total": x.get("billed_total"),
                "payment": x.get("payment"),
                "payment_date": x.get("payment_date"),
                "arrears": x.get("arrears"),
                "current_arrears": x.get("arrears"),
                "balance_adjustment": int(x.get("balance_adjustment") or 0),
            }
        )
    return out


def _archive_through_sep(db, mid):
    charges = 0
    payments = 0
    for c in (
        db.query(ReceivableCharge)
        .filter(ReceivableCharge.member_id == mid, ReceivableCharge.billing_month <= "2026-09")
        .all()
    ):
        if int(c.amount or 0) != 0:
            c.amount = 0
            charges += 1
        c.source = "legacy_reconciled"

    now = datetime.now(timezone.utc)
    for p in (
        db.query(ReceivablePayment)
        .filter(
            ReceivablePayment.member_id == mid,
            ReceivablePayment.payment_date <= "2026-09-30",
            ReceivablePayment.cancelled_at.is_(None),
        )
        .all()
    ):
        p.cancelled_at = now
        p.cancelled_by = "20261008-monthly-v4"
        if MEMO not in (p.memo or ""):
            p.memo = ((p.memo or "") + " / " if p.memo else "") + MEMO + " 최종원장 baseline 편입"
        payments += 1
    return charges, payments


def _apply_baseline(db, m, p, rec):
    p.account_type = rec.get("account_type") or p.account_type
    p.unit_fee = 10000 if p.account_type == "협회비" else 5000
    p.vehicle_count = int(rec.get("vehicle_count") or 1)
    p.account_manual_override = 1
    p.legacy_months = _legacy_months(rec)
    # Critical V3 repair: use the FINAL corrected Sep closing balance, not the
    # prior DB/V4 value.  Example: 이민행 = 0 after 60,000 paid on 2026-09-01.
    p.legacy_balance = int(rec.get("target_sep_balance") or 0)
    _append_note(p, MEMO)
    c, pay = _archive_through_sep(db, m.id)

    # If October was already auto-created, normalize the monthly fee itself.
    oc = (
        db.query(ReceivableCharge)
        .filter(ReceivableCharge.member_id == m.id, ReceivableCharge.billing_month == "2026-10")
        .first()
    )
    if oc and not rec.get("certificate_hold"):
        oc.amount = int(p.unit_fee or 0) * int(p.vehicle_count or 1)
        oc.account_type = p.account_type
    return c, pay


def _post_sep_charge(db, mid, month):
    return int(
        db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0))
        .filter(ReceivableCharge.member_id == mid, ReceivableCharge.billing_month == month)
        .scalar()
        or 0
    )


def _month_payments(db, mid, month):
    start = month + "-01"
    y, m = map(int, month.split("-"))
    if m == 12:
        end = f"{y+1:04d}-01-01"
    else:
        end = f"{y:04d}-{m+1:02d}-01"
    rows = (
        db.query(ReceivablePayment)
        .filter(
            ReceivablePayment.member_id == mid,
            ReceivablePayment.payment_date >= start,
            ReceivablePayment.payment_date < end,
            ReceivablePayment.cancelled_at.is_(None),
        )
        .order_by(ReceivablePayment.payment_date.asc(), ReceivablePayment.id.asc())
        .all()
    )
    amount = sum(int(x.amount or 0) for x in rows)
    date = rows[-1].payment_date if rows else None
    return amount, date


def _current_balance_from_rec(db, mid, rec):
    bal = int(rec.get("target_sep_balance") or 0)
    charges = int(
        db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0))
        .filter(ReceivableCharge.member_id == mid, ReceivableCharge.billing_month > "2026-09")
        .scalar()
        or 0
    )
    pays = int(
        db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0))
        .filter(
            ReceivablePayment.member_id == mid,
            ReceivablePayment.payment_date > "2026-09-30",
            ReceivablePayment.cancelled_at.is_(None),
        )
        .scalar()
        or 0
    )
    return bal + charges - pays


def _monthly_for(db, mid, rec):
    """Return one internally consistent ledger, Jan-Dec.

    Jan-Sep comes only from the corrected workbook.  Oct-Dec is rolled forward
    from the corrected Sep close using live DB transactions.  We intentionally
    do not merge the old `/members/{id}` October row because it can carry the
    pre-reconcile Sep balance forward a second time.
    """
    by_month = {}
    for x in rec.get("months", []):
        m = int(x["month"])
        by_month[m] = {
            "month": m,
            "legacy_monthly_charge": int(x.get("monthly_charge") or 0),
            "auto_charge": 0,
            "legacy_payment": x.get("payment"),
            "legacy_payment_date": x.get("payment_date"),
            "additional_payment": 0,
            "balance_adjustment": int(x.get("balance_adjustment") or 0),
            "current_arrears": x.get("arrears"),
        }

    balance = int(rec.get("target_sep_balance") or 0)
    now = datetime.now(timezone.utc)
    current_ym = f"{now.year:04d}-{now.month:02d}"
    for m in (10, 11, 12):
        ym = f"2026-{m:02d}"
        charge = _post_sep_charge(db, mid, ym)
        paid, paid_date = _month_payments(db, mid, ym)
        balance = balance + charge - paid
        has_activity = bool(charge or paid or ym <= current_ym)
        by_month[m] = {
            "month": m,
            "legacy_monthly_charge": 0,
            "auto_charge": charge,
            "legacy_payment": None,
            "legacy_payment_date": None,
            "additional_payment": paid,
            "additional_payment_date": paid_date,
            "balance_adjustment": 0,
            "current_arrears": balance if has_activity else None,
        }
    return [by_month[m] for m in sorted(by_month)]



def _db_formula_balance(db, mid, profile):
    """Balance formula used by the ordinary receivables data model after V4.

    V4 makes Jan-Sep authoritative in profile.legacy_balance and neutralizes
    dynamic rows through Sep, so this generic formula must equal the
    authoritative roll-forward balance.
    """
    if not profile:
        return None
    charges = int(
        db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0))
        .filter(ReceivableCharge.member_id == mid)
        .scalar()
        or 0
    )
    pays = int(
        db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0))
        .filter(
            ReceivablePayment.member_id == mid,
            ReceivablePayment.cancelled_at.is_(None),
        )
        .scalar()
        or 0
    )
    return int(profile.legacy_balance or 0) + charges - pays


def _audit_source_recurrence(payload):
    """Verify every corrected Jan-Sep source row is internally continuous."""
    bad = []
    checked = 0
    for group in ("active", "closures"):
        for rec in payload.get(group, []):
            bal = int(rec.get("carryover") or 0)
            for x in rec.get("months", []):
                expected = (
                    bal
                    + int(x.get("monthly_charge") or 0)
                    - int(x.get("payment") or 0)
                    + int(x.get("balance_adjustment") or 0)
                )
                actual = x.get("arrears")
                checked += 1
                if actual is not None and expected != int(actual):
                    bad.append({
                        "name": rec.get("name"),
                        "vehicle_number": rec.get("vehicle_number"),
                        "month": int(x.get("month") or 0),
                        "expected": expected,
                        "actual": int(actual),
                    })
                if actual is not None:
                    bal = int(actual)
    return {"checked_month_rows": checked, "mismatches": bad}


def audit_runtime(db):
    payload = _load()
    idx = _index(db)
    source = _audit_source_recurrence(payload)
    out = {
        "patch_id": PATCH_ID,
        "source_month_rows_checked": source["checked_month_rows"],
        "source_recurrence_mismatches": source["mismatches"],
        "matched": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "profile_missing": 0,
        "baseline_mismatches": [],
        "residual_pre_oct_charges": 0,
        "residual_pre_oct_live_payments": 0,
        "db_formula_mismatches": [],
    }
    for rec in payload.get("active", []):
        m, why = _pick(idx.get((rec["match_name"], rec["match_vehicle"]), []))
        if not m:
            out["ambiguous" if why == "ambiguous" else "unmatched"] += 1
            continue
        p = _profile(db, m.id)
        if not p:
            out["profile_missing"] += 1
            continue
        out["matched"] += 1
        target = int(rec.get("target_sep_balance") or 0)
        if int(p.legacy_balance or 0) != target:
            out["baseline_mismatches"].append({
                "member_id": m.id,
                "name": m.name,
                "vehicle_number": m.vehicle_number,
                "profile_legacy_balance": int(p.legacy_balance or 0),
                "target_sep_balance": target,
            })
        out["residual_pre_oct_charges"] += int(
            db.query(ReceivableCharge)
            .filter(
                ReceivableCharge.member_id == m.id,
                ReceivableCharge.billing_month <= "2026-09",
                ReceivableCharge.amount != 0,
            )
            .count()
        )
        out["residual_pre_oct_live_payments"] += int(
            db.query(ReceivablePayment)
            .filter(
                ReceivablePayment.member_id == m.id,
                ReceivablePayment.payment_date <= "2026-09-30",
                ReceivablePayment.cancelled_at.is_(None),
            )
            .count()
        )
        authoritative = _current_balance_from_rec(db, m.id, rec)
        db_formula = _db_formula_balance(db, m.id, p)
        if db_formula != authoritative:
            out["db_formula_mismatches"].append({
                "member_id": m.id,
                "name": m.name,
                "vehicle_number": m.vehicle_number,
                "db_formula": db_formula,
                "authoritative": authoritative,
            })
    out["ok"] = not (
        out["source_recurrence_mismatches"]
        or out["baseline_mismatches"]
        or out["residual_pre_oct_charges"]
        or out["residual_pre_oct_live_payments"]
        or out["db_formula_mismatches"]
    )
    return out


def _next_num(db, ctype):
    if ctype == "탈퇴":
        rows = (
            db.query(models.Closure)
            .filter(models.Closure.management_number.like("탈-%"), models.Closure.deleted_at.is_(None))
            .all()
        )
        mx = 0
        for x in rows:
            try:
                mx = max(mx, int(str(x.management_number).split("-", 1)[1]))
            except Exception:
                pass
        return f"탈-{mx+1}"
    base = "폐업" if ctype in ("폐업", "폐지") else ctype
    return crud.get_next_closure_number(db, base)


def _existing_closure(db, m):
    if getattr(m, "closure_id", None):
        c = (
            db.query(models.Closure)
            .filter(models.Closure.id == m.closure_id, models.Closure.deleted_at.is_(None))
            .first()
        )
        if c:
            return c
    rows = (
        db.query(models.Closure)
        .filter(models.Closure.deleted_at.is_(None), models.Closure.name == m.name)
        .all()
    )
    exact = [c for c in rows if _v(c.vehicle_number) == _v(m.vehicle_number)]
    return exact[0] if len(exact) == 1 else None


def _remove_post_closure(db, mid, date):
    mon = str(date or "")[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", mon):
        return 0
    return int(
        db.query(ReceivableCharge)
        .filter(ReceivableCharge.member_id == mid, ReceivableCharge.billing_month > mon)
        .delete(synchronize_session=False)
        or 0
    )


def _remove_future(db, mid):
    return int(
        db.query(ReceivableCharge)
        .filter(ReceivableCharge.member_id == mid, ReceivableCharge.billing_month >= "2026-10")
        .delete(synchronize_session=False)
        or 0
    )


def _state(db):
    r = db.query(ReceivableSystemState).filter(ReceivableSystemState.key == STATE_KEY).first()
    if not r or not r.value:
        return None
    try:
        return json.loads(r.value)
    except Exception:
        return {"patch_id": PATCH_ID, "status": "state_parse_error"}


def _save_state(db, obj):
    r = db.query(ReceivableSystemState).filter(ReceivableSystemState.key == STATE_KEY).first()
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if r:
        r.value = s
    else:
        db.add(ReceivableSystemState(key=STATE_KEY, value=s))


def _rec_for_member(payload, m):
    k = (_n(m.name), _v(m.vehicle_number))
    for group in ("active", "closures"):
        for r in payload[group]:
            if (r["match_name"], r["match_vehicle"]) == k:
                return r
    return None


def dry_run():
    payload = _load()
    db = SessionLocal()
    try:
        idx = _index(db)
        st = {
            "matched": 0,
            "unmatched": [],
            "ambiguous": [],
            "profiles_missing": [],
            "wrong_profile_baseline": 0,
            "pre_oct_charges_to_archive": 0,
            "pre_oct_payments_to_archive": 0,
        }
        for r in payload["active"]:
            m, why = _pick(idx.get((r["match_name"], r["match_vehicle"]), []))
            if not m:
                st["ambiguous" if why == "ambiguous" else "unmatched"].append(
                    {"name": r["name"], "vehicle_number": r["vehicle_number"]}
                )
                continue
            p = _profile(db, m.id)
            if not p:
                st["profiles_missing"].append({"name": r["name"], "vehicle_number": r["vehicle_number"]})
                continue
            st["matched"] += 1
            if int(p.legacy_balance or 0) != int(r.get("target_sep_balance") or 0):
                st["wrong_profile_baseline"] += 1
            st["pre_oct_charges_to_archive"] += (
                db.query(ReceivableCharge)
                .filter(
                    ReceivableCharge.member_id == m.id,
                    ReceivableCharge.billing_month <= "2026-09",
                    ReceivableCharge.amount != 0,
                )
                .count()
            )
            st["pre_oct_payments_to_archive"] += (
                db.query(ReceivablePayment)
                .filter(
                    ReceivablePayment.member_id == m.id,
                    ReceivablePayment.payment_date <= "2026-09-30",
                    ReceivablePayment.cancelled_at.is_(None),
                )
                .count()
            )
        return {"patch_id": PATCH_ID, "status": "dry_run", "source_counts": payload["source_counts"], "stats": st}
    finally:
        db.close()


def apply_once(force=False):
    payload = _load()
    db = SessionLocal()
    try:
        prev = _state(db)
        if prev and not force and prev.get("status") in ("applied", "applied_with_skips"):
            return {**prev, "status": "already_applied"}

        preview = dry_run()
        if preview["stats"]["matched"] < 2500:
            raise RuntimeError(f"safety stop: only {preview['stats']['matched']} active rows matched")

        idx = _index(db)
        out = {
            "active": {"matched": 0, "charges_archived": 0, "payments_archived": 0, "skipped": [], "errors": []},
            "closures": {"matched": 0, "created": 0, "linked": 0, "charges_archived": 0, "payments_archived": 0, "future_charges_removed": 0, "skipped": [], "errors": []},
            "holds": {"matched": 0, "future_charges_removed": 0},
            "exclusions": {"matched": 0, "future_charges_removed": 0},
        }

        for r in payload["active"]:
            m, why = _pick(idx.get((r["match_name"], r["match_vehicle"]), []))
            if not m or (m.status or "active") != "active":
                out["active"]["skipped"].append(
                    {"name": r["name"], "vehicle_number": r["vehicle_number"], "why": why if not m else "closed_in_db"}
                )
                continue
            p = _profile(db, m.id)
            if not p:
                out["active"]["skipped"].append({"name": r["name"], "vehicle_number": r["vehicle_number"], "why": "profile_missing"})
                continue
            try:
                with db.begin_nested():
                    c, pay = _apply_baseline(db, m, p, r)
                    out["active"]["matched"] += 1
                    out["active"]["charges_archived"] += c
                    out["active"]["payments_archived"] += pay
                    if r.get("note"):
                        _append_note(p, "[원장비고] " + str(r["note"])[:500])
            except Exception as exc:
                out["active"]["errors"].append(
                    {"name": r["name"], "vehicle_number": r["vehicle_number"], "error": f"{type(exc).__name__}: {exc}"}
                )

        for r in payload["certificate_holds"]:
            m, _ = _pick(idx.get((r["match_name"], r["match_vehicle"]), []))
            p = _profile(db, m.id) if m else None
            if m and p:
                p.first_charge_date = None
                _append_note(p, MEMO + " 자격증명 미발급·부과 제외")
                out["holds"]["matched"] += 1
                out["holds"]["future_charges_removed"] += _remove_future(db, m.id)

        for r in payload["exclusions"]:
            m, _ = _pick(idx.get((r["match_name"], r["match_vehicle"]), []))
            p = _profile(db, m.id) if m else None
            if m and p:
                p.first_charge_date = None
                p.legacy_balance = 0
                _append_note(p, MEMO + " " + r["reason"])
                out["exclusions"]["matched"] += 1
                out["exclusions"]["future_charges_removed"] += _remove_future(db, m.id)

        for r in payload["closures"]:
            m, why = _pick(idx.get((r["match_name"], r["match_vehicle"]), []))
            if not m:
                out["closures"]["skipped"].append({"name": r["name"], "vehicle_number": r["vehicle_number"], "why": why})
                continue
            p = _profile(db, m.id)
            if not p:
                out["closures"]["skipped"].append({"name": r["name"], "vehicle_number": r["vehicle_number"], "why": "profile_missing"})
                continue
            try:
                with db.begin_nested():
                    cnum, pay = _apply_baseline(db, m, p, r)
                    out["closures"]["charges_archived"] += cnum
                    out["closures"]["payments_archived"] += pay
                    out["closures"]["matched"] += 1
                    c = _existing_closure(db, m)
                    if c:
                        m.status = "closed"
                        m.closure_id = c.id
                        c.member_id = m.id
                        out["closures"]["linked"] += 1
                    else:
                        bt = "폐업" if r["closure_type"] in ("폐업", "폐지") else r["closure_type"]
                        c = crud.close_member_no_commit(
                            db,
                            m.id,
                            bt,
                            r.get("closure_date") or "",
                            _next_num(db, bt),
                            r.get("reason") or MEMO,
                        )
                        out["closures"]["created"] += 1
                        if r["closure_type"] == "폐지":
                            c.closure_type = "폐지"
                    if r.get("reason"):
                        c.reason = r["reason"]
                    out["closures"]["future_charges_removed"] += _remove_post_closure(
                        db, m.id, r.get("closure_date") or ""
                    )
            except Exception as exc:
                out["closures"]["errors"].append(
                    {"name": r["name"], "vehicle_number": r["vehicle_number"], "error": f"{type(exc).__name__}: {exc}"}
                )

        result = {
            "patch_id": PATCH_ID,
            "status": "applied_with_skips"
            if (out["active"]["skipped"] or out["active"]["errors"] or out["closures"]["skipped"] or out["closures"]["errors"])
            else "applied",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": payload["source_counts"],
            "result": out,
            "safety": {
                "jan_sep_source": "corrected workbook",
                "october_plus_source": "live DB rolled forward from corrected Sep close",
                "contacts_modified": False,
                "member_identity_modified": False,
                "pre_oct_payments_deleted": False,
                "pre_oct_payments_archived_by_cancel": True,
            },
        }
        db.flush()
        result["audit_after"] = audit_runtime(db)
        _save_state(db, result)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/giro-targets")
def giro_targets(_=Depends(get_current_user)):
    p = _load()
    return {"count": len(p["giro_preferred"]), "items": p["giro_preferred"]}


@router.get("/giro-members")
def giro_members(
    q: str = Query(""),
    region: str = Query(""),
    account_type: str = Query(""),
    contact_status: str = Query(""),
    contacted_only: bool = Query(False),
    billing_status: str = Query(""),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Dedicated 지로희망 list.

    지로희망은 이름/비고 검색어가 아니라 독립 플래그다.  The ordinary
    `/members?q=지로` search can legitimately hit notes and therefore must not
    be used as the 지로 filter.  This endpoint resolves only the authoritative
    workbook giro targets to current active members and returns the same basic
    row shape the receivables UI expects.
    """
    payload = _load()
    idx = _index(db)
    rows = []
    seen = set()
    qn = re.sub(r"[\s\-]+", "", str(q or "")).lower()
    rgn = str(region or "").strip()
    acct = str(account_type or "").strip()
    cstat = str(contact_status or "").strip()
    bstat = str(billing_status or "").strip()

    for target in payload.get("giro_preferred", []):
        m, _why = _pick(idx.get((target.get("match_name"), target.get("match_vehicle")), []))
        if not m or m.id in seen or (m.status or "active") != "active":
            continue
        seen.add(m.id)
        rec = _rec_for_member(payload, m)
        prof = _profile(db, m.id)
        if not rec or not prof:
            continue

        latest_contact = (
            db.query(ReceivableContactLog)
            .filter(ReceivableContactLog.member_id == m.id)
            .order_by(ReceivableContactLog.contact_date.desc(), ReceivableContactLog.id.desc())
            .first()
        )
        last_contact_date = latest_contact.contact_date if latest_contact else None
        row_contact_status = latest_contact.status if latest_contact else "미연락"
        if contacted_only and not latest_contact:
            continue
        if cstat and row_contact_status != cstat:
            continue

        row_account = rec.get("account_type") or prof.account_type or "관리비"
        if rgn and (m.region or "") != rgn:
            continue
        if acct and row_account != acct:
            continue

        bal = int(_current_balance_from_rec(db, m.id, rec))
        billing_state = "미수" if bal > 0 else "선납" if bal < 0 else "완납"
        # Existing 2026 giro targets are established ledger members.  Keep the
        # normal pending semantics only if a future first-charge date actually exists.
        first_charge = prof.first_charge_date or ""
        if first_charge and first_charge > datetime.now(timezone.utc).date().isoformat():
            billing_state = "부과대기"
        if bstat == "arrears" and not (bal > 0):
            continue
        if bstat == "settled" and not (bal == 0 and billing_state != "부과대기"):
            continue
        if bstat == "prepaid" and not (bal < 0):
            continue
        if bstat == "pending" and billing_state != "부과대기":
            continue

        hay = "|".join(
            str(x or "")
            for x in (
                m.name,
                m.vehicle_number,
                m.management_number,
                m.region,
                m.mobile,
                m.phone,
            )
        )
        if qn and qn not in re.sub(r"[\s\-]+", "", hay).lower():
            continue

        rows.append({
            "member_id": m.id,
            "name": m.name or "",
            "management_number": m.management_number or "",
            "account_type": row_account,
            "vehicle_number": m.vehicle_number or "",
            "region": m.region or "",
            "phone": m.phone or "",
            "mobile": m.mobile or "",
            "first_charge_date": first_charge,
            "last_contact_date": last_contact_date,
            "contact_status": row_contact_status,
            "balance": bal,
            "billing_state": billing_state,
            "active": True,
            "giro_preferred": True,
        })

    total = len(rows)
    pages = max(1, (total + limit - 1) // limit)
    if page > pages:
        page = pages
    start = (page - 1) * limit
    items = rows[start:start + limit]
    return {
        "items": items,
        "count": total,
        "page": page,
        "pages": pages,
        "limit": limit,
        "giro_source_count": len(payload.get("giro_preferred", [])),
        "ledger_source": "20261008-monthly-v4",
    }


@router.get("/ledger/{member_id}")
def ledger(member_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    m = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == member_id).first()
    if not m:
        raise HTTPException(404, "member not found")
    rec = _rec_for_member(_load(), m)
    if not rec:
        raise HTTPException(404, "authoritative ledger not found")
    return {
        "member_id": m.id,
        "name": m.name,
        "vehicle_number": m.vehicle_number,
        "monthly": _monthly_for(db, m.id, rec),
        "current_balance": _current_balance_from_rec(db, m.id, rec),
        "target_sep_balance": rec.get("target_sep_balance"),
        "ledger_source": "20261008-monthly-v4",
    }



@router.get("/balances")
def balances(
    ids: str = Query("", description="comma-separated member ids"),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    payload = _load()
    raw_ids = []
    for part in str(ids or "").split(","):
        part = part.strip()
        if part.isdigit():
            raw_ids.append(int(part))
    raw_ids = list(dict.fromkeys(raw_ids))[:200]
    if not raw_ids:
        return {"items": {}}
    members = db.query(models.LicenseHolder).filter(models.LicenseHolder.id.in_(raw_ids)).all()
    items = {}
    for m in members:
        rec = _rec_for_member(payload, m)
        if not rec:
            continue
        bal = _current_balance_from_rec(db, m.id, rec)
        items[str(m.id)] = {
            "balance": bal,
            "billing_state": "미수" if bal > 0 else "선납" if bal < 0 else "완납",
            "target_sep_balance": int(rec.get("target_sep_balance") or 0),
        }
    return {"items": items, "ledger_source": "20261008-monthly-v4"}


@router.get("/audit")
def audit_api(db: Session = Depends(get_db), _=Depends(require_admin)):
    return audit_runtime(db)


@router.get("/dry-run")
def dry_run_api(_=Depends(require_admin)):
    return dry_run()


@router.get("/status")
def status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _state(db) or {
        "patch_id": PATCH_ID,
        "status": "not_applied",
        "source_counts": _load()["source_counts"],
    }


@router.post("/apply")
def apply_api(_=Depends(require_admin)):
    try:
        return apply_once(force=True)
    except Exception as exc:
        raise HTTPException(500, f"2026-10-08 monthly V4 repair failed: {type(exc).__name__}: {exc}")
