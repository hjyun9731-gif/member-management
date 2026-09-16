"""2026-09-16 수납/미수금 최종원장 보정 + 자격증명 미발급 부과제외 보호.

목적
- 2026-09-16 14:01 운영 원장 대비 최종 확정 원장의 271건 차액을 1회 반영한다.
- 자격증명 미발급으로 최종 확정된 28명은 발급 전까지 부과기준일 없음 상태를 유지한다.
- 자격증명이 발급되면 다음 달 1일부터 정상 부과가 시작되도록 자동 해제한다.
- 기존 수납/선납/연락/폐업/양도/이관 이력은 삭제하거나 재작성하지 않는다.

설계
- 잔액 보정: receivable_profiles.legacy_balance에 확정 delta만 더한다.
- 멱등성: receivable_system_state에 PATCH_ID를 기록하고 재실행을 막는다.
- 영구 부과제외: 별도 receivable_billing_exclusions 테이블 + PostgreSQL trigger 3개로 보호한다.
  1) receivable_profiles first_charge_date가 미발급 상태에서 다시 채워지는 것을 차단
  2) 혹시 잘못된 월부과 INSERT/UPDATE가 발생해도 금액을 0원으로 강제
  3) license_holders의 자격증명 발급정보가 생기면 다음 달 1일 자동 해제

이 파일과 JSON에는 주소/전화/주민번호가 들어가지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

PATCH_ID = "receivables_hotfix_20260916_v2"
LEGACY_PATCH_ID = "receivables_hotfix_20260916_v1"
GUARD_STATE_ID = "receivables_certificate_billing_guard_20260916_v2"
DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "receivables_hotfix_20260916.json")
EXPECTED_CORRECTIONS = 271
EXPECTED_EXCLUSIONS = 28


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
    if len(corrections) != EXPECTED_CORRECTIONS:
        raise RuntimeError(
            f"hotfix correction count mismatch: {len(corrections)} != {EXPECTED_CORRECTIONS}"
        )
    excluded = [x for x in corrections if x.get("disable_billing")]
    if len(excluded) != EXPECTED_EXCLUSIONS:
        raise RuntimeError(
            f"hotfix billing exclusion count mismatch: {len(excluded)} != {EXPECTED_EXCLUSIONS}"
        )
    return payload


def _resolve_member_id(db, row: dict) -> int:
    """관리번호를 최우선으로 엄격 매칭. 관리번호가 비어 있는 1건만 성명+차량번호 매칭."""
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


def _current_balance(db, member_id: int) -> int:
    value = db.execute(text("""
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
    """), {"member_id": member_id}).scalar_one()
    return int(value or 0)


def _certificate_present(db, member_id: int) -> bool:
    row = db.execute(text("""
        SELECT certificate_issue_date, certificate_number
        FROM license_holders
        WHERE id = :member_id AND deleted_at IS NULL
    """), {"member_id": member_id}).first()
    if not row:
        return False
    return bool(str(row[0] or "").strip() or str(row[1] or "").strip())


def _next_month_first_iso(base: date | None = None) -> str:
    d = base or date.today()
    if d.month == 12:
        return f"{d.year + 1:04d}-01-01"
    return f"{d.year:04d}-{d.month + 1:02d}-01"


def _state_get(db, key: str):
    return db.execute(
        text("SELECT value FROM receivable_system_state WHERE key=:key"),
        {"key": key},
    ).scalar()


def _state_set(db, key: str, value: dict) -> None:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        db.execute(text("""
            INSERT INTO receivable_system_state (key, value)
            VALUES (:key, :value)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """), {"key": key, "value": raw})
    else:
        db.execute(text("""
            INSERT INTO receivable_system_state (key, value)
            VALUES (:key, :value)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value
        """), {"key": key, "value": raw})


def _ensure_exclusion_table(db) -> None:
    if db.bind.dialect.name == "sqlite":
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS receivable_billing_exclusions (
                member_id INTEGER PRIMARY KEY,
                reason VARCHAR(120) NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                release_effective_date VARCHAR(10),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                released_at DATETIME
            )
        """))
    else:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS receivable_billing_exclusions (
                member_id INTEGER PRIMARY KEY,
                reason VARCHAR(120) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                release_effective_date VARCHAR(10),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                released_at TIMESTAMPTZ
            )
        """))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_receivable_billing_exclusions_active "
            "ON receivable_billing_exclusions(active)"
        ))


def _upsert_confirmed_exclusions(db, payload: dict) -> list[int]:
    """최종 원장에서 확정된 28명만 exclusion 테이블에 등록한다."""
    member_ids: list[int] = []
    for row in payload["corrections"]:
        if not row.get("disable_billing"):
            continue
        member_id = _resolve_member_id(db, row)
        member_ids.append(member_id)
        cert = _certificate_present(db, member_id)
        existing = db.execute(text("""
            SELECT active, release_effective_date
            FROM receivable_billing_exclusions
            WHERE member_id=:member_id
        """), {"member_id": member_id}).first()

        if cert:
            release_date = (existing[1] if existing and existing[1] else _next_month_first_iso())
            if existing:
                db.execute(text("""
                    UPDATE receivable_billing_exclusions
                    SET reason=:reason,
                        active=:active,
                        release_effective_date=:release_date,
                        released_at=COALESCE(released_at, CURRENT_TIMESTAMP)
                    WHERE member_id=:member_id
                """), {
                    "reason": "자격증명 미발급 · 부과 제외",
                    "active": False,
                    "release_date": release_date,
                    "member_id": member_id,
                })
            else:
                db.execute(text("""
                    INSERT INTO receivable_billing_exclusions
                        (member_id, reason, active, release_effective_date, released_at)
                    VALUES
                        (:member_id, :reason, :active, :release_date, CURRENT_TIMESTAMP)
                """), {
                    "member_id": member_id,
                    "reason": "자격증명 미발급 · 부과 제외",
                    "active": False,
                    "release_date": release_date,
                })
            db.execute(text("""
                UPDATE receivable_profiles
                SET first_charge_date = CASE
                    WHEN first_charge_date IS NULL OR first_charge_date = '' OR first_charge_date < :release_date
                    THEN :release_date
                    ELSE first_charge_date
                END
                WHERE member_id=:member_id
            """), {"release_date": release_date, "member_id": member_id})
        else:
            if existing:
                db.execute(text("""
                    UPDATE receivable_billing_exclusions
                    SET reason=:reason,
                        active=:active,
                        release_effective_date=NULL,
                        released_at=NULL
                    WHERE member_id=:member_id
                """), {
                    "reason": "자격증명 미발급 · 부과 제외",
                    "active": True,
                    "member_id": member_id,
                })
            else:
                db.execute(text("""
                    INSERT INTO receivable_billing_exclusions
                        (member_id, reason, active)
                    VALUES
                        (:member_id, :reason, :active)
                """), {
                    "member_id": member_id,
                    "reason": "자격증명 미발급 · 부과 제외",
                    "active": True,
                })
            db.execute(text("""
                UPDATE receivable_profiles
                SET first_charge_date=NULL,
                    legacy_note = CASE
                        WHEN COALESCE(legacy_note, '') LIKE '%[CERT-BILLING-EXCLUDED-20260916]%'
                            THEN legacy_note
                        WHEN COALESCE(legacy_note, '') = ''
                            THEN '[CERT-BILLING-EXCLUDED-20260916] 자격증명 미발급 부과 제외'
                        ELSE legacy_note || ' | [CERT-BILLING-EXCLUDED-20260916] 자격증명 미발급 부과 제외'
                    END
                WHERE member_id=:member_id
            """), {"member_id": member_id})

    if len(set(member_ids)) != EXPECTED_EXCLUSIONS:
        raise RuntimeError(
            f"resolved exclusion member count mismatch: {len(set(member_ids))} != {EXPECTED_EXCLUSIONS}"
        )
    return member_ids


def _install_postgresql_guards(db) -> None:
    """라우터 코드와 무관하게 DB 자체에서 미발급 28명의 잘못된 재부과를 막는다."""
    if db.bind.dialect.name != "postgresql":
        return

    # 1) sync/프로필 갱신이 first_charge_date를 되살려도 미발급 상태면 다시 NULL.
    db.execute(text(r"""
        CREATE OR REPLACE FUNCTION fn_receivable_profile_cert_exclusion_guard()
        RETURNS trigger AS $$
        DECLARE
            ex_active BOOLEAN;
            ex_release VARCHAR(10);
            cert_ok BOOLEAN;
        BEGIN
            SELECT e.active,
                   e.release_effective_date,
                   (COALESCE(BTRIM(lh.certificate_issue_date), '') <> ''
                    OR COALESCE(BTRIM(lh.certificate_number), '') <> '')
              INTO ex_active, ex_release, cert_ok
              FROM receivable_billing_exclusions e
              JOIN license_holders lh ON lh.id = e.member_id
             WHERE e.member_id = NEW.member_id
               AND lh.deleted_at IS NULL;

            IF FOUND THEN
                IF ex_active AND NOT cert_ok THEN
                    NEW.first_charge_date := NULL;
                ELSIF (NOT ex_active) AND ex_release IS NOT NULL
                      AND (NEW.first_charge_date IS NULL OR NEW.first_charge_date = '' OR NEW.first_charge_date < ex_release) THEN
                    NEW.first_charge_date := ex_release;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    db.execute(text("DROP TRIGGER IF EXISTS trg_receivable_profile_cert_exclusion_guard ON receivable_profiles"))
    db.execute(text(r"""
        CREATE TRIGGER trg_receivable_profile_cert_exclusion_guard
        BEFORE INSERT OR UPDATE ON receivable_profiles
        FOR EACH ROW EXECUTE FUNCTION fn_receivable_profile_cert_exclusion_guard()
    """))

    # 2) 혹시 월부과 로직이 first_charge_date를 무시하더라도 실제 부과금은 0원으로 방어.
    #    자격증명 발급 후 release_effective_date(다음 달 1일)부터 정상 금액 허용.
    db.execute(text(r"""
        CREATE OR REPLACE FUNCTION fn_receivable_charge_cert_exclusion_guard()
        RETURNS trigger AS $$
        DECLARE
            ex_active BOOLEAN;
            ex_release VARCHAR(10);
        BEGIN
            SELECT active, release_effective_date
              INTO ex_active, ex_release
              FROM receivable_billing_exclusions
             WHERE member_id = NEW.member_id;

            IF FOUND THEN
                IF ex_active
                   OR (ex_release IS NOT NULL AND NEW.billing_month < SUBSTRING(ex_release FROM 1 FOR 7)) THEN
                    NEW.amount := 0;
                    NEW.source := 'cert-excluded';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    db.execute(text("DROP TRIGGER IF EXISTS trg_receivable_charge_cert_exclusion_guard ON receivable_charges"))
    db.execute(text(r"""
        CREATE TRIGGER trg_receivable_charge_cert_exclusion_guard
        BEFORE INSERT OR UPDATE ON receivable_charges
        FOR EACH ROW EXECUTE FUNCTION fn_receivable_charge_cert_exclusion_guard()
    """))

    # 3) 자격증명 발급/취소 상태가 바뀌면 부과 제외 상태도 자동 연동.
    db.execute(text(r"""
        CREATE OR REPLACE FUNCTION fn_receivable_release_cert_exclusion()
        RETURNS trigger AS $$
        DECLARE
            cert_ok BOOLEAN;
            release_date VARCHAR(10);
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM receivable_billing_exclusions WHERE member_id = NEW.id
            ) THEN
                RETURN NEW;
            END IF;

            cert_ok := (
                COALESCE(BTRIM(NEW.certificate_issue_date), '') <> ''
                OR COALESCE(BTRIM(NEW.certificate_number), '') <> ''
            );

            IF cert_ok THEN
                release_date := TO_CHAR(
                    (DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month')::date,
                    'YYYY-MM-DD'
                );
                UPDATE receivable_billing_exclusions
                   SET active = FALSE,
                       release_effective_date = COALESCE(release_effective_date, release_date),
                       released_at = COALESCE(released_at, NOW())
                 WHERE member_id = NEW.id;

                UPDATE receivable_profiles
                   SET first_charge_date = CASE
                       WHEN first_charge_date IS NULL OR first_charge_date = '' OR first_charge_date < release_date
                       THEN release_date
                       ELSE first_charge_date
                   END
                 WHERE member_id = NEW.id;
            ELSE
                UPDATE receivable_billing_exclusions
                   SET active = TRUE,
                       release_effective_date = NULL,
                       released_at = NULL
                 WHERE member_id = NEW.id;

                UPDATE receivable_profiles
                   SET first_charge_date = NULL
                 WHERE member_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """))
    db.execute(text("DROP TRIGGER IF EXISTS trg_receivable_release_cert_exclusion ON license_holders"))
    db.execute(text(r"""
        CREATE TRIGGER trg_receivable_release_cert_exclusion
        AFTER INSERT OR UPDATE OF certificate_issue_date, certificate_number ON license_holders
        FOR EACH ROW EXECUTE FUNCTION fn_receivable_release_cert_exclusion()
    """))


def ensure_receivables_billing_exclusion_guards() -> dict:
    """28명 부과제외 테이블/trigger를 매 배포 시 멱등 확인한다."""
    from app.database import SessionLocal

    payload = _load_payload()
    db = SessionLocal()
    try:
        _ensure_exclusion_table(db)
        member_ids = _upsert_confirmed_exclusions(db, payload)
        _install_postgresql_guards(db)

        active_count = int(db.execute(text("""
            SELECT COUNT(*) FROM receivable_billing_exclusions WHERE active = :active
        """), {"active": True}).scalar() or 0)
        result = {
            "status": "guard_ready",
            "guard_id": GUARD_STATE_ID,
            "confirmed_exclusion_count": len(member_ids),
            "active_exclusion_count": active_count,
            "database": db.bind.dialect.name,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _state_set(db, GUARD_STATE_ID, result)
        db.commit()
        logger.info("자격증명 미발급 부과제외 보호장치 확인 완료: %s", result)
        return result
    except Exception:
        db.rollback()
        logger.exception("자격증명 미발급 부과제외 보호장치 구성 실패")
        raise
    finally:
        db.close()


def apply_receivables_hotfix_20260916() -> dict:
    """271건 확정 delta를 정확히 한 번 반영한다."""
    from app.database import SessionLocal

    # 보호장치를 먼저 확보한다. 이 함수 자체도 멱등이다.
    ensure_receivables_billing_exclusion_guards()

    payload = _load_payload()
    db = SessionLocal()
    try:
        current_state = _state_get(db, PATCH_ID)
        if current_state:
            try:
                result = json.loads(current_state)
            except Exception:
                result = {"status": "already_applied", "raw": current_state}
            logger.info("수납미수금 2026-09-16 v2 보정은 이미 적용됨: %s", result)
            return result

        # 이전 v1 패키지가 이미 실제 DB에 적용된 경우 잔액 delta를 절대 두 번 적용하지 않는다.
        legacy_state = _state_get(db, LEGACY_PATCH_ID)
        if legacy_state:
            result = {
                "status": "balance_already_applied_by_v1_guards_upgraded",
                "patch_id": PATCH_ID,
                "applied_count": EXPECTED_CORRECTIONS,
                "billing_exclusion_count": EXPECTED_EXCLUSIONS,
                "applied_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            _state_set(db, PATCH_ID, result)
            db.commit()
            logger.info("v1 잔액보정 감지 - v2에서는 보호장치만 업그레이드: %s", result)
            return result

        resolved: list[tuple[dict, int]] = []
        for row in payload["corrections"]:
            member_id = _resolve_member_id(db, row)
            profile = db.execute(text("""
                SELECT member_id
                FROM receivable_profiles
                WHERE member_id = :member_id
            """), {"member_id": member_id}).first()
            if not profile:
                raise RuntimeError(f"member_id {member_id}: receivable profile missing")
            resolved.append((row, member_id))

        if len({member_id for _, member_id in resolved}) != EXPECTED_CORRECTIONS:
            raise RuntimeError("271 corrections did not resolve to 271 unique receivable members")

        # 전 건 매칭이 성공한 뒤에만 수정 시작. 실패 시 전체 rollback.
        pre_balances: dict[int, int] = {}
        for row, member_id in resolved:
            pre_balances[member_id] = _current_balance(db, member_id)
            delta = int(row["delta"])
            db.execute(text("""
                UPDATE receivable_profiles
                SET legacy_balance = COALESCE(legacy_balance, 0) + :delta
                WHERE member_id = :member_id
            """), {"delta": delta, "member_id": member_id})

            if row.get("disable_billing"):
                db.execute(text("""
                    UPDATE receivable_profiles
                    SET first_charge_date = NULL,
                        legacy_note = CASE
                            WHEN COALESCE(legacy_note, '') LIKE '%[CERT-BILLING-EXCLUDED-20260916]%'
                                THEN legacy_note
                            WHEN COALESCE(legacy_note, '') = ''
                                THEN '[CERT-BILLING-EXCLUDED-20260916] 자격증명 미발급 부과 제외'
                            ELSE legacy_note || ' | [CERT-BILLING-EXCLUDED-20260916] 자격증명 미발급 부과 제외'
                        END
                    WHERE member_id=:member_id
                """), {"member_id": member_id})

        db.flush()

        # 14:01 이후 운영에서 새 수납/수정이 있었다면 보존해야 하므로 target 강제덮어쓰기가 아니라
        # '현재값 + 확정 delta'가 되었는지만 검증한다.
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
                first_charge = db.execute(text("""
                    SELECT first_charge_date FROM receivable_profiles WHERE member_id=:member_id
                """), {"member_id": member_id}).scalar()
                if first_charge not in (None, ""):
                    raise RuntimeError(
                        f"member_id {member_id}: exclusion verify failed ({first_charge!r})"
                    )

        result = {
            "status": "applied",
            "patch_id": PATCH_ID,
            "applied_count": len(resolved),
            "billing_exclusion_count": sum(1 for row, _ in resolved if row.get("disable_billing")),
            "balance_delta_total": sum(int(row["delta"]) for row, _ in resolved),
            "applied_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _state_set(db, PATCH_ID, result)
        db.commit()
        logger.info("수납미수금 2026-09-16 최종원장 v2 보정 완료: %s", result)
        return result
    except Exception:
        db.rollback()
        logger.exception("수납미수금 2026-09-16 최종원장 v2 보정 실패 - 전체 rollback")
        raise
    finally:
        db.close()


def get_receivables_hotfix_status() -> dict:
    """Railway 배포 후 코드+DB 반영을 PII 없이 확인하기 위한 health 정보."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        patch_raw = _state_get(db, PATCH_ID)
        legacy_raw = _state_get(db, LEGACY_PATCH_ID)
        guard_raw = _state_get(db, GUARD_STATE_ID)
        table_exists = True
        try:
            total = int(db.execute(text("SELECT COUNT(*) FROM receivable_billing_exclusions")).scalar() or 0)
            active = int(db.execute(text("""
                SELECT COUNT(*) FROM receivable_billing_exclusions WHERE active=:active
            """), {"active": True}).scalar() or 0)
            leaks = int(db.execute(text("""
                SELECT COUNT(*)
                FROM receivable_billing_exclusions e
                JOIN receivable_profiles rp ON rp.member_id=e.member_id
                WHERE e.active=:active
                  AND COALESCE(rp.first_charge_date, '') <> ''
            """), {"active": True}).scalar() or 0)
        except Exception:
            table_exists = False
            total = active = leaks = -1

        balance_applied = bool(patch_raw or legacy_raw)
        ok = bool(
            balance_applied
            and guard_raw
            and table_exists
            and total == EXPECTED_EXCLUSIONS
            and leaks == 0
        )
        return {
            "status": "ok" if ok else "pending",
            "code_version": PATCH_ID,
            "balance_patch_applied": balance_applied,
            "guard_ready": bool(guard_raw),
            "confirmed_exclusion_count": total,
            "active_unissued_count": active,
            "first_charge_date_leak_count": leaks,
        }
    except Exception as e:
        return {
            "status": "error",
            "code_version": PATCH_ID,
            "detail": str(e),
        }
    finally:
        db.close()
