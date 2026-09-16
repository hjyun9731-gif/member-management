"""One-time 2026-09-16 receivables reconciliation + certificate hold protection.

Source of correction deltas:
- production /receivables export captured 2026-09-16 15:33 KST
- authoritative 2026 fee ledger September balance

The 296 deltas are applied exactly once to receivable_profiles.legacy_balance.
This preserves payments/other ledger activity recorded after the 15:33 export.

For 28 designated certificate-unissued members, a persistent DB-side guard keeps
first_charge_date NULL and forces future monthly charges (2026-10 onward) to zero
until license_holders.certificate_issue_date is populated. When a certificate date
is populated, first_charge_date is set to the first day of the following month.

Payload contains hashes only; no member name, vehicle number, address or phone data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from calendar import monthrange

from sqlalchemy import text

logger = logging.getLogger(__name__)
PATCH_ID = "receivables_reconcile_20260916_v4"
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "receivables_reconcile_20260916_v4.json")
EXPECTED = 296
EXPECTED_HOLDS = 28
FUTURE_BLOCK_MONTH = "2026-10"


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _norm_vehicle(value: str) -> str:
    value = str(value or "").replace("강원", "")
    value = re.sub(r"호\s*$", "", value)
    value = re.sub(r"[\s\-]+", "", value)
    return value.strip().lower()


def _match_hash(name: str, vehicle: str) -> str:
    raw = f"{_norm_name(name)}|{_norm_vehicle(vehicle)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_payload() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("patch_id") != PATCH_ID:
        raise RuntimeError("V4 patch_id mismatch")
    rows = payload.get("corrections") or []
    if len(rows) != EXPECTED:
        raise RuntimeError(f"V4 correction count mismatch: {len(rows)} != {EXPECTED}")
    if len({r.get("match_hash") for r in rows}) != EXPECTED:
        raise RuntimeError("V4 duplicate match_hash")
    if sum(1 for r in rows if r.get("certificate_hold")) != EXPECTED_HOLDS:
        raise RuntimeError("V4 certificate hold count mismatch")
    return payload


def _state_get(db, key: str):
    return db.execute(text("SELECT value FROM receivable_system_state WHERE key=:k"), {"k": key}).scalar()


def _state_set(db, key: str, value: dict):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("""
            INSERT INTO receivable_system_state (key, value, created_at, updated_at)
            VALUES (:k, :v, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE
            SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP
        """), {"k": key, "v": raw})
    else:
        existing = db.execute(text("SELECT key FROM receivable_system_state WHERE key=:k"), {"k": key}).first()
        if existing:
            db.execute(text("UPDATE receivable_system_state SET value=:v, updated_at=CURRENT_TIMESTAMP WHERE key=:k"), {"k": key, "v": raw})
        else:
            db.execute(text("INSERT INTO receivable_system_state (key, value) VALUES (:k, :v)"), {"k": key, "v": raw})


def _ensure_support_table(db):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS receivable_billing_exclusions (
            member_id INTEGER PRIMARY KEY,
            reason VARCHAR(200) NOT NULL,
            patch_id VARCHAR(120) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))


def _ensure_postgres_triggers(db):
    if db.bind.dialect.name != "postgresql":
        return

    # Profile guard: while a designated member has no certificate date, sync code
    # cannot accidentally restore first_charge_date.
    db.execute(text(r"""
        CREATE OR REPLACE FUNCTION receivable_cert_hold_profile_guard()
        RETURNS trigger AS $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM receivable_billing_exclusions e
            JOIN license_holders lh ON lh.id = e.member_id
            WHERE e.member_id = NEW.member_id
              AND BTRIM(COALESCE(lh.certificate_issue_date, '')) = ''
          ) THEN
            NEW.first_charge_date := NULL;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    db.execute(text("DROP TRIGGER IF EXISTS trg_receivable_cert_hold_profile ON receivable_profiles"))
    db.execute(text(r"""
        CREATE TRIGGER trg_receivable_cert_hold_profile
        BEFORE INSERT OR UPDATE ON receivable_profiles
        FOR EACH ROW EXECUTE FUNCTION receivable_cert_hold_profile_guard()
    """))

    # Charge guard: current September is reconciled by the one-time delta. New
    # October+ charges are forced to zero until certificate issuance.
    db.execute(text(r"""
        CREATE OR REPLACE FUNCTION receivable_cert_hold_charge_guard()
        RETURNS trigger AS $$
        BEGIN
          IF NEW.billing_month >= '2026-10' AND EXISTS (
            SELECT 1
            FROM receivable_billing_exclusions e
            JOIN license_holders lh ON lh.id = e.member_id
            WHERE e.member_id = NEW.member_id
              AND BTRIM(COALESCE(lh.certificate_issue_date, '')) = ''
          ) THEN
            NEW.amount := 0;
            NEW.source := 'certificate_hold';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    db.execute(text("DROP TRIGGER IF EXISTS trg_receivable_cert_hold_charge ON receivable_charges"))
    db.execute(text(r"""
        CREATE TRIGGER trg_receivable_cert_hold_charge
        BEFORE INSERT OR UPDATE ON receivable_charges
        FOR EACH ROW EXECUTE FUNCTION receivable_cert_hold_charge_guard()
    """))

    # Certificate guard: the designation remains permanently recorded. Blank date
    # means hold; populated date means billing starts on the next month's first day.
    db.execute(text(r"""
        CREATE OR REPLACE FUNCTION receivable_cert_hold_member_sync()
        RETURNS trigger AS $$
        DECLARE
          digits TEXT;
          issued DATE;
          next_first DATE;
        BEGIN
          IF EXISTS (SELECT 1 FROM receivable_billing_exclusions e WHERE e.member_id = NEW.id) THEN
            IF BTRIM(COALESCE(NEW.certificate_issue_date, '')) = '' THEN
              UPDATE receivable_profiles SET first_charge_date = NULL WHERE member_id = NEW.id;
            ELSE
              digits := regexp_replace(COALESCE(NEW.certificate_issue_date, ''), '[^0-9]', '', 'g');
              BEGIN
                IF length(digits) = 8 THEN
                  issued := to_date(digits, 'YYYYMMDD');
                ELSIF length(digits) = 6 THEN
                  issued := to_date('20' || digits, 'YYYYMMDD');
                ELSE
                  issued := CURRENT_DATE;
                END IF;
              EXCEPTION WHEN OTHERS THEN
                issued := CURRENT_DATE;
              END;
              next_first := (date_trunc('month', issued) + interval '1 month')::date;
              UPDATE receivable_profiles
              SET first_charge_date = to_char(next_first, 'YYYY-MM-DD')
              WHERE member_id = NEW.id;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    db.execute(text("DROP TRIGGER IF EXISTS trg_receivable_cert_hold_member ON license_holders"))
    db.execute(text(r"""
        CREATE TRIGGER trg_receivable_cert_hold_member
        AFTER UPDATE OF certificate_issue_date ON license_holders
        FOR EACH ROW EXECUTE FUNCTION receivable_cert_hold_member_sync()
    """))


def _parse_issue_date(value: str | None) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = re.sub(r"[^0-9]", "", raw)
    try:
        if len(digits) == 8:
            return datetime.strptime(digits, "%Y%m%d").date()
        if len(digits) == 6:
            return datetime.strptime("20" + digits, "%Y%m%d").date()
    except ValueError:
        pass
    # Nonblank means an issuance has been recorded; if its formatting is unusual,
    # use today's month only for determining the next charge start.
    return date.today()


def _next_month_first(d: date) -> str:
    if d.month == 12:
        return f"{d.year + 1:04d}-01-01"
    return f"{d.year:04d}-{d.month + 1:02d}-01"


def _refresh_hold_profiles(db, hold_member_ids: list[int]):
    if not hold_member_ids:
        return
    for mid in hold_member_ids:
        issue = db.execute(text("SELECT certificate_issue_date FROM license_holders WHERE id=:mid"), {"mid": mid}).scalar()
        parsed = _parse_issue_date(issue)
        if parsed is None:
            db.execute(text("UPDATE receivable_profiles SET first_charge_date=NULL WHERE member_id=:mid"), {"mid": mid})
        else:
            db.execute(text("UPDATE receivable_profiles SET first_charge_date=:d WHERE member_id=:mid"),
                       {"d": _next_month_first(parsed), "mid": mid})


def _resolve_members(db, payload_rows: list[dict]) -> list[tuple[dict, int, str | None]]:
    # Active, nondeleted master rows only. This mirrors the current /receivables list.
    candidates = db.execute(text("""
        SELECT lh.id, lh.name, lh.vehicle_number, lh.certificate_issue_date
        FROM license_holders lh
        JOIN receivable_profiles rp ON rp.member_id = lh.id
        WHERE lh.deleted_at IS NULL
          AND (lh.status IS NULL OR lh.status = '' OR lh.status = 'active')
    """)).fetchall()
    index: dict[str, list[tuple[int, str | None]]] = {}
    for mid, name, vehicle, issue_date in candidates:
        index.setdefault(_match_hash(name, vehicle), []).append((int(mid), issue_date))

    resolved = []
    for row in payload_rows:
        matches = index.get(row["match_hash"], [])
        if len(matches) != 1:
            raise RuntimeError(
                f"V4 anonymous member match failed: matched={len(matches)} hash={row['match_hash'][:12]}"
            )
        mid, issue_date = matches[0]
        resolved.append((row, mid, issue_date))
    if len({mid for _, mid, _ in resolved}) != EXPECTED:
        raise RuntimeError("V4 did not resolve to 296 unique active members")
    return resolved


def apply_receivables_reconcile_20260916_v4() -> dict:
    from app.database import SessionLocal

    payload = _load_payload()
    db = SessionLocal()
    try:
        _ensure_support_table(db)
        _ensure_postgres_triggers(db)
        resolved = _resolve_members(db, payload["corrections"])

        hold_ids = []
        for row, mid, _ in resolved:
            if not row.get("certificate_hold"):
                continue
            hold_ids.append(mid)
            if db.bind.dialect.name == "postgresql":
                db.execute(text("""
                    INSERT INTO receivable_billing_exclusions(member_id, reason, patch_id, created_at)
                    VALUES (:mid, 'certificate_unissued', :pid, CURRENT_TIMESTAMP)
                    ON CONFLICT (member_id) DO UPDATE
                    SET reason=EXCLUDED.reason, patch_id=EXCLUDED.patch_id
                """), {"mid": mid, "pid": PATCH_ID})
            else:
                exists = db.execute(text("SELECT member_id FROM receivable_billing_exclusions WHERE member_id=:mid"), {"mid": mid}).first()
                if exists:
                    db.execute(text("UPDATE receivable_billing_exclusions SET reason='certificate_unissued', patch_id=:pid WHERE member_id=:mid"), {"pid": PATCH_ID, "mid": mid})
                else:
                    db.execute(text("INSERT INTO receivable_billing_exclusions(member_id, reason, patch_id) VALUES (:mid, 'certificate_unissued', :pid)"), {"mid": mid, "pid": PATCH_ID})

        _refresh_hold_profiles(db, hold_ids)

        already = _state_get(db, PATCH_ID)
        if already:
            db.commit()  # infrastructure/hold refresh is intentionally maintained on every deploy
            try:
                result = json.loads(already)
            except Exception:
                result = {"status": "already_applied", "patch_id": PATCH_ID}
            result["infrastructure_refreshed"] = True
            return result

        baseline_drift_count = 0
        for row, mid, _ in resolved:
            # Baseline is audit-only. We apply the precomputed delta, not a target
            # overwrite, so legitimate payments after 15:33 remain intact.
            db_balance = db.execute(text("SELECT COALESCE(legacy_balance,0) FROM receivable_profiles WHERE member_id=:mid"), {"mid": mid}).scalar_one()
            # We cannot compare profile legacy_balance with UI baseline directly; count
            # is intentionally left at zero rather than producing a misleading metric.
            _ = db_balance
            changed = db.execute(text("""
                UPDATE receivable_profiles
                SET legacy_balance = COALESCE(legacy_balance, 0) + :delta
                WHERE member_id = :mid
            """), {"delta": int(row["delta"]), "mid": mid})
            if changed.rowcount != 1:
                raise RuntimeError(f"V4 profile update failed for member_id={mid}")

        result = {
            "status": "applied",
            "patch_id": PATCH_ID,
            "applied_count": EXPECTED,
            "certificate_hold_count": len(hold_ids),
            "delta_total": int(payload.get("delta_total", 0)),
            "baseline_drift_count": baseline_drift_count,
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _state_set(db, PATCH_ID, result)
        db.commit()
        logger.info("Receivables V4 reconciliation applied: %s", result)
        return result
    except Exception:
        db.rollback()
        logger.exception("Receivables V4 reconciliation failed; transaction rolled back")
        raise
    finally:
        db.close()


def get_receivables_reconcile_v4_status() -> dict:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        raw = _state_get(db, PATCH_ID)
        hold_total = 0
        active_hold = 0
        try:
            hold_total = int(db.execute(text("SELECT COUNT(*) FROM receivable_billing_exclusions WHERE patch_id=:pid"), {"pid": PATCH_ID}).scalar() or 0)
            active_hold = int(db.execute(text("""
                SELECT COUNT(*)
                FROM receivable_billing_exclusions e
                JOIN license_holders lh ON lh.id=e.member_id
                WHERE e.patch_id=:pid AND BTRIM(COALESCE(lh.certificate_issue_date,''))=''
            """), {"pid": PATCH_ID}).scalar() or 0)
        except Exception:
            pass
        if not raw:
            return {
                "status": "pending",
                "patch_id": PATCH_ID,
                "expected_count": EXPECTED,
                "expected_certificate_holds": EXPECTED_HOLDS,
                "hold_designations": hold_total,
                "active_holds": active_hold,
            }
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
        return {**data, "hold_designations": hold_total, "active_holds": active_hold}
    except Exception as e:
        return {"status": "error", "patch_id": PATCH_ID, "detail": str(e)}
    finally:
        db.close()
