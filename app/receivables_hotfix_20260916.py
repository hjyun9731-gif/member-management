"""2026-09-16 수납/미수금 최종원장 1회성 보정.

GitHub main -> Railway 배포 시 app.main의 deferred DB maintenance에서 한 번 실행된다.
기존 입금/연락/폐업 이력은 건드리지 않고 receivable_profiles.legacy_balance만
확정 원장과의 차액(delta)만큼 이동한다.

자격증명 미발급 28명은 first_charge_date=None으로 두어 부과기준일 없음 상태로
전환하고 account_manual_override=1로 표시한다.

멱등성: receivable_system_state의 PATCH_ID가 존재하면 재실행하지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

PATCH_ID = "receivables_hotfix_20260916_v1"
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "receivables_hotfix_20260916.json")


def _norm_vehicle(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or "").strip())
    value = re.sub(r"호$", "", value)
    return value.lower()


def _load_payload() -> dict:
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("patch_id") != PATCH_ID:
        raise RuntimeError(f"hotfix patch_id mismatch: {payload.get('patch_id')!r}")
    corrections = payload.get("corrections") or []
    if len(corrections) != 271:
        raise RuntimeError(f"hotfix correction count mismatch: {len(corrections)} != 271")
    if sum(1 for x in corrections if x.get("disable_billing")) != 28:
        raise RuntimeError("hotfix billing exclusion count mismatch: expected 28")
    return payload


def _current_balance(db, member_id: int) -> int:
    # 운영 화면과 동일 산식: legacy_balance + charge_total - payment_total
    return int(db.execute(text("""
        SELECT
            COALESCE(rp.legacy_balance, 0)
            + COALESCE((
                SELECT SUM(rc.amount)
                FROM receivable_charges rc
                WHERE rc.member_id = :member_id
            ), 0)
            - COALESCE((
                SELECT SUM(rpay.amount)
                FROM receivable_payments rpay
                WHERE rpay.member_id = :member_id
                  AND rpay.cancelled_at IS NULL
            ), 0) AS balance
        FROM receivable_profiles rp
        WHERE rp.member_id = :member_id
    """), {"member_id": member_id}).scalar_one())


def _resolve_member_id(db, row: dict) -> int:
    mgmt = (row.get("management_number") or "").strip()
    if mgmt:
        ids = [int(x[0]) for x in db.execute(text("""
            SELECT id
            FROM license_holders
            WHERE deleted_at IS NULL
              AND management_number = :mgmt
        """), {"mgmt": mgmt}).fetchall()]
        if len(ids) != 1:
            raise RuntimeError(f"management_number {mgmt!r}: matched {len(ids)} members")
        return ids[0]

    # 관리번호가 비어 있던 1건(문용빈)은 성명 + 차량번호로 엄격 매칭한다.
    name = (row.get("fallback_name") or "").strip()
    vehicle = _norm_vehicle(row.get("fallback_vehicle") or "")
    candidates = db.execute(text("""
        SELECT id, vehicle_number
        FROM license_holders
        WHERE deleted_at IS NULL
          AND name = :name
    """), {"name": name}).fetchall()
    ids = [int(r[0]) for r in candidates if _norm_vehicle(r[1]) == vehicle]
    if len(ids) != 1:
        raise RuntimeError(f"fallback {name!r}/{vehicle!r}: matched {len(ids)} members")
    return ids[0]


def apply_receivables_hotfix_20260916() -> dict:
    from app.database import SessionLocal

    payload = _load_payload()
    db = SessionLocal()
    try:
        # 이미 성공한 배포라면 아무것도 건드리지 않는다.
        existing = db.execute(text(
            "SELECT value FROM receivable_system_state WHERE key = :key"
        ), {"key": PATCH_ID}).scalar()
        if existing:
            try:
                result = json.loads(existing)
            except Exception:
                result = {"status": "already_applied", "raw": existing}
            logger.info("수납미수금 2026-09-16 보정은 이미 적용되어 있습니다: %s", result)
            return result

        resolved = []
        for row in payload["corrections"]:
            member_id = _resolve_member_id(db, row)
            profile = db.execute(text("""
                SELECT member_id, legacy_balance, first_charge_date
                FROM receivable_profiles
                WHERE member_id = :member_id
            """), {"member_id": member_id}).first()
            if not profile:
                raise RuntimeError(f"member_id {member_id}: receivable profile missing")
            resolved.append((row, member_id))

        # 전 건 매칭이 성공했을 때만 실제 수정 시작. 중간 실패 시 전체 rollback.
        pre_balances = {}
        for row, member_id in resolved:
            pre = _current_balance(db, member_id)
            pre_balances[member_id] = pre
            delta = int(row["delta"])
            disable = bool(row.get("disable_billing"))

            if disable:
                db.execute(text("""
                    UPDATE receivable_profiles
                    SET legacy_balance = COALESCE(legacy_balance, 0) + :delta,
                        first_charge_date = NULL,
                        account_manual_override = 1,
                        legacy_note = CASE
                            WHEN COALESCE(legacy_note, '') LIKE '%[HOTFIX-20260916-CERT-EXCLUDED]%'
                                THEN legacy_note
                            WHEN COALESCE(legacy_note, '') = ''
                                THEN '[HOTFIX-20260916-CERT-EXCLUDED] 자격증명 미발급 부과 제외'
                            ELSE legacy_note || ' | [HOTFIX-20260916-CERT-EXCLUDED] 자격증명 미발급 부과 제외'
                        END
                    WHERE member_id = :member_id
                """), {"delta": delta, "member_id": member_id})
            else:
                db.execute(text("""
                    UPDATE receivable_profiles
                    SET legacy_balance = COALESCE(legacy_balance, 0) + :delta
                    WHERE member_id = :member_id
                """), {"delta": delta, "member_id": member_id})

        db.flush()

        # 현재 운영 DB에 14:01 이후 입금/수정이 있었더라도 그 내역은 유지되어야 하므로
        # 절대 target_balance로 강제 덮어쓰지 않고 '차액(delta)'만 정확히 적용됐는지 검증한다.
        for row, member_id in resolved:
            before = pre_balances[member_id]
            after = _current_balance(db, member_id)
            expected_after = before + int(row["delta"])
            if after != expected_after:
                raise RuntimeError(
                    f"member_id {member_id}: balance verify failed "
                    f"({before} + {row['delta']} != {after})"
                )
            if row.get("disable_billing"):
                first_charge = db.execute(text(
                    "SELECT first_charge_date FROM receivable_profiles WHERE member_id=:member_id"
                ), {"member_id": member_id}).scalar()
                if first_charge not in (None, ""):
                    raise RuntimeError(
                        f"member_id {member_id}: billing exclusion verify failed ({first_charge!r})"
                    )

        result = {
            "status": "applied",
            "patch_id": PATCH_ID,
            "applied_count": len(resolved),
            "billing_exclusion_count": sum(1 for row, _ in resolved if row.get("disable_billing")),
            "balance_delta_total": sum(int(row["delta"]) for row, _ in resolved),
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        db.execute(text("""
            INSERT INTO receivable_system_state (key, value)
            VALUES (:key, :value)
        """), {
            "key": PATCH_ID,
            "value": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        })
        db.commit()
        logger.info("수납미수금 2026-09-16 최종원장 보정 완료: %s", result)
        return result
    except Exception:
        db.rollback()
        logger.exception("수납미수금 2026-09-16 최종원장 보정 실패 - 전체 rollback")
        raise
    finally:
        db.close()
