"""2026-09-16 수납/미수금 v3 재대조 보정.

기준:
- 운영 수납미수금 export 2026-09-16 15:33
- [사용]2026미수금.xlsx 의 '2026년회비내역' 9월 미수금(AQ)
- 현재 DB 이름+차량번호가 원장과 엄격히 일치하는 활성 대상만 비교

v2에서 놓친 269건의 차액만 1회 추가 반영한다.
중요: delta는 15:33 운영값 대비 차이이므로, 15:33 이후 실제 수납/금액수정은 보존된다.
기존 자격증명 미발급 28명 보호장치는 v2를 그대로 사용하며 이 패치는 건드리지 않는다.

JSON에는 성명/차량번호/주소/전화번호를 저장하지 않고, 성명+차량번호 정규화값의 SHA-256만 저장한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from sqlalchemy import text

logger = logging.getLogger(__name__)
PATCH_ID = "receivables_reconcile_20260916_v3"
REQUIRED_V2_STATE = "receivables_hotfix_20260916_v2"
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "receivables_reconcile_20260916_v3.json")
EXPECTED = 269


def _norm_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _norm_vehicle(value: str) -> str:
    value = str(value or "").replace("강원", "").replace("호", "")
    return re.sub(r"[\s\-]", "", value).strip()


def _match_hash(name: str, vehicle: str) -> str:
    raw = f"{_norm_name(name)}|{_norm_vehicle(vehicle)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_payload() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("patch_id") != PATCH_ID:
        raise RuntimeError("v3 patch_id mismatch")
    rows = payload.get("corrections") or []
    if len(rows) != EXPECTED:
        raise RuntimeError(f"v3 correction count mismatch: {len(rows)} != {EXPECTED}")
    if len({r.get("match_hash") for r in rows}) != EXPECTED:
        raise RuntimeError("v3 match_hash duplicate")
    return payload


def _state_get(db, key: str):
    return db.execute(text("SELECT value FROM receivable_system_state WHERE key=:k"), {"k": key}).scalar()


def _state_set(db, key: str, value: dict):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        db.execute(text("""
            INSERT INTO receivable_system_state (key, value, created_at, updated_at)
            VALUES (:k, :v, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP
        """), {"k": key, "v": raw})
    else:
        existing = db.execute(text("SELECT key FROM receivable_system_state WHERE key=:k"), {"k": key}).first()
        if existing:
            db.execute(text("UPDATE receivable_system_state SET value=:v, updated_at=CURRENT_TIMESTAMP WHERE key=:k"), {"k": key, "v": raw})
        else:
            db.execute(text("INSERT INTO receivable_system_state (key, value) VALUES (:k, :v)"), {"k": key, "v": raw})


def _current_balance(db, member_id: int) -> int:
    value = db.execute(text("""
        SELECT COALESCE(rp.legacy_balance, 0)
             + COALESCE((SELECT SUM(rc.amount) FROM receivable_charges rc WHERE rc.member_id=:mid), 0)
             - COALESCE((SELECT SUM(rpmt.amount) FROM receivable_payments rpmt
                         WHERE rpmt.member_id=:mid AND rpmt.cancelled_at IS NULL), 0)
        FROM receivable_profiles rp
        WHERE rp.member_id=:mid
    """), {"mid": member_id}).scalar_one()
    return int(value or 0)


def apply_receivables_reconcile_20260916_v3() -> dict:
    from app.database import SessionLocal
    payload = _load_payload()
    db = SessionLocal()
    try:
        already = _state_get(db, PATCH_ID)
        if already:
            try:
                return json.loads(already)
            except Exception:
                return {"status": "already_applied", "patch_id": PATCH_ID}

        if not _state_get(db, REQUIRED_V2_STATE):
            raise RuntimeError("v3 requires receivables_hotfix_20260916_v2 to be applied first")

        candidates = db.execute(text("""
            SELECT lh.id, lh.name, lh.vehicle_number
            FROM license_holders lh
            JOIN receivable_profiles rp ON rp.member_id=lh.id
            WHERE lh.deleted_at IS NULL
        """)).fetchall()
        index = {}
        for mid, name, vehicle in candidates:
            h = _match_hash(name, vehicle)
            index.setdefault(h, []).append(int(mid))

        resolved = []
        for row in payload["corrections"]:
            ids = index.get(row["match_hash"], [])
            if len(ids) != 1:
                raise RuntimeError(
                    f"v3 anonymous member match failed: matched={len(ids)} hash={row['match_hash'][:12]}"
                )
            resolved.append((row, ids[0]))
        if len({mid for _, mid in resolved}) != EXPECTED:
            raise RuntimeError("v3 did not resolve to 269 unique members")

        baseline_drift = 0
        before_map = {}
        for row, mid in resolved:
            before = _current_balance(db, mid)
            before_map[mid] = before
            if before != int(row["baseline_balance"]):
                baseline_drift += 1
            db.execute(text("""
                UPDATE receivable_profiles
                SET legacy_balance = COALESCE(legacy_balance, 0) + :delta
                WHERE member_id = :mid
            """), {"delta": int(row["delta"]), "mid": mid})

        db.flush()
        target_match = 0
        for row, mid in resolved:
            after = _current_balance(db, mid)
            expected = before_map[mid] + int(row["delta"])
            if after != expected:
                raise RuntimeError(
                    f"v3 balance verify failed for member_id={mid}: {after} != {expected}"
                )
            if after == int(row["target_balance"]):
                target_match += 1

        result = {
            "status": "applied",
            "patch_id": PATCH_ID,
            "applied_count": EXPECTED,
            "delta_total": int(payload.get("delta_total", 0)),
            "baseline_drift_count": baseline_drift,
            "target_match_count_at_apply": target_match,
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _state_set(db, PATCH_ID, result)
        db.commit()
        logger.info("수납미수금 v3 재대조 보정 완료: %s", result)
        return result
    except Exception:
        db.rollback()
        logger.exception("수납미수금 v3 재대조 보정 실패 - 전체 rollback")
        raise
    finally:
        db.close()


def get_receivables_reconcile_v3_status() -> dict:
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        raw = _state_get(db, PATCH_ID)
        if not raw:
            return {"status": "pending", "patch_id": PATCH_ID, "expected_count": EXPECTED}
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
        return {"status": "ok", "patch_id": PATCH_ID, "expected_count": EXPECTED, **data}
    except Exception as e:
        return {"status": "error", "patch_id": PATCH_ID, "detail": str(e)}
    finally:
        db.close()
