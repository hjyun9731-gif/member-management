"""미수금 정정(실제 입금 아님) 서비스 — 자격증명 미발급 회원의 관리비 미수금 0원 정정.

설계 원칙
  · 정정은 receivable_payments(실제 수납)에 넣지 않고 receivable_adjustments 에만 기록한다.
    → 수납 통계·통장 입금액·월별 수납액에는 절대 들어가지 않는다.
  · 잔액 계산은 receivables._canonical_balance_parts 한 곳만 쓴다(상세/금액수정/일괄정정 공통).
    목록·통계·엑셀·지로 대상은 같은 규칙을 옮긴 SQL(_balance_sql_core)을 쓰고, consistency_report 로 둘의 일치를 검증한다.
  · 회원 매칭은 이름/차량번호가 아니라 license_holders.id = receivable_profiles.member_id 로만 한다.
  · 미리보기는 DB를 바꾸지 않는다. 적용은 (미리보기 digest + 확인문구 + 백업확인 + 관리자) 모두 맞아야 하고,
    한 번만 실행되며(유효 배치 1개, 회원별 부분 유니크 인덱스), 하나라도 실패하면 전체 롤백된다.
  · 과거 부과/수납/폐업 이력은 삭제·수정하지 않는다. 되돌리기는 void(무효 표시)로 하며 기록은 남는다.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app import models
from app.receivables_models import (
    ReceivableAdjustment,
    ReceivableAdjustmentBatch,
    ReceivableCharge,
    ReceivablePayment,
    ReceivableProfile,
)

KST = ZoneInfo("Asia/Seoul")
REASON_CODE = "unissued_management_fee_20261010"
REASON_TEXT = "자격증명 미발급(발급일자·발급번호 모두 공란) 회원의 관리비 미수금 정정 — 실제 입금 아님"
CONFIRM_PHRASE = "ZERO_UNISSUED_MANAGEMENT"
VOID_PHRASE = "VOID_ADJUSTMENT_BATCH"
LOCK_KEY = 20261010301


class AdjustmentError(Exception):
    """사용자에게 그대로 보여 줘도 되는 업무 오류."""


def _R():
    from app.routers import receivables as R  # 지연 import(순환 방지)
    return R


def _blank(v) -> bool:
    return not str(v or "").strip()


def _mask(name) -> str:
    n = str(name or "").strip()
    return (n[:1] + "*" * (len(n) - 1)) if n else ""


# ───────────────────────────── 계획(미리보기) ─────────────────────────────
def build_plan(db: Session, exclude_member_ids=()) -> dict:
    """대상 판정 + 회원별 정정 예정액. DB를 수정하지 않는다."""
    R = _R()
    exclude = {int(x) for x in (exclude_member_ids or [])}
    rows_q = (
        db.query(models.LicenseHolder, ReceivableProfile)
        .join(ReceivableProfile, ReceivableProfile.member_id == models.LicenseHolder.id)
        .filter(models.LicenseHolder.deleted_at.is_(None))
        .order_by(models.LicenseHolder.id)
    )
    rows, candidates_all = [], 0
    for member, profile in rows_q.all():
        unissued = _blank(member.certificate_issue_date) and _blank(member.certificate_number)
        if not unissued:
            continue            # 발급일 또는 발급번호가 하나라도 있으면 미발급이 아니다(예: 발급번호만 있는 회원)
        candidates_all += 1
        if profile.account_type != "관리비":
            continue            # 협회비/70세 등은 이번 정정 대상이 아니다
        flags = []
        closure = R._current_closure_for_member(db, member)
        parts = R._canonical_balance_parts(db, member, profile, closure)
        bal = parts["balance"]
        is_bae = "배" in str(member.vehicle_number or "").replace(" ", "")
        if member.company_name:
            flags.append("법인/업체명 있음(확인 권장)")
        # 판정 순서: 정정할 미수금(양수)이 없으면 그대로 두고, 있으면 '택배 미발급'만 자동 대상이다.
        # 비택배·폐업·계정불일치는 자동정정하지 않고 별도 검토 목록으로 분리한다.
        if bal == 0:
            decision, reason = "already_zero", "이미 0원"
        elif bal < 0:
            decision, reason = "credit_kept", "선납(음수) 유지"
        elif int(member.id) in exclude:
            decision, reason = "excluded", "관리자가 제외 지정"
        elif int(getattr(profile, "receivable_active", 1) or 0) != 1:
            decision, reason = "excluded", "미수금 비활성 프로필"
        elif (member.status or "active") == "closed":
            decision, reason = "review_closed", "폐업 회원 — 폐업 전 미수금은 임의 삭제하지 않음(별도 검토)"
        elif int(getattr(profile, "account_manual_override", 0) or 0) != 1 and R._infer_account(member) != profile.account_type:
            decision, reason = "review_account", f"계정 불일치(회원정보상 {R._infer_account(member)} / 프로필 {profile.account_type}) — 별도 검토"
        elif not is_bae:
            decision, reason = "review_non_bae", "비택배(배 번호판 아님) — 자동정정 제외, 회원유형·자격증명 대상 여부 별도 검토"
        else:
            decision, reason = "target", ""
        planned = bal if decision == "target" else 0
        rows.append({
            "member_id": int(member.id), "name": member.name or "", "vehicle_number": member.vehicle_number or "",
            "category": member.category or "", "member_status": member.status or "active",
            "certificate_issue_date": member.certificate_issue_date or "", "certificate_number": member.certificate_number or "",
            "account_type": profile.account_type, "baseline": parts["baseline"], "charges": parts["charges"],
            "payments": parts["payments"], "adjustments": parts["adjustments"],
            "balance_before": bal, "planned_adjustment": planned,
            "balance_after": bal - planned, "decision": decision, "reason": reason, "flags": flags,
        })
    targets = [r for r in rows if r["decision"] == "target"]
    digest_src = json.dumps(
        {"targets": [(r["member_id"], r["balance_before"], r["planned_adjustment"]) for r in targets],
         "exclude": sorted(exclude)}, sort_keys=True)
    by = {}
    for r in rows:
        by[r["decision"]] = by.get(r["decision"], 0) + 1
    return {
        "reason_code": REASON_CODE,
        "plan_digest": hashlib.sha256(digest_src.encode()).hexdigest(),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "summary": {
            "미발급 후보(모든 계정)": candidates_all,
            "관리비 계정 미발급": len(rows),
            "정정 대상(택배 미발급·양수 미수)": len(targets),
            "정정 예정액 합계": sum(r["planned_adjustment"] for r in targets),
            "별도 검토 — 비택배": by.get("review_non_bae", 0),
            "별도 검토 — 폐업": by.get("review_closed", 0),
            "별도 검토 — 계정 불일치": by.get("review_account", 0),
            "별도 검토 미수 합계(정정하지 않음)": sum(r["balance_before"] for r in rows if r["decision"].startswith("review_")),
            "선납 유지": by.get("credit_kept", 0), "이미 0원": by.get("already_zero", 0),
            "제외(관리자 지정/비활성)": by.get("excluded", 0),
        },
        "rows": rows,
        "excluded_member_ids": sorted(exclude),
    }


# ───────────────────────────── 합계 스냅샷 ─────────────────────────────
def _totals(db: Session) -> dict:
    pay = db.query(func.coalesce(func.sum(ReceivablePayment.amount), 0)).filter(ReceivablePayment.cancelled_at.is_(None)).scalar()
    cnt = db.query(func.count(ReceivablePayment.id)).scalar()
    chg = db.query(func.coalesce(func.sum(ReceivableCharge.amount), 0)).scalar()
    ccnt = db.query(func.count(ReceivableCharge.id)).scalar()
    return {"payments_total": int(pay or 0), "payments_rows": int(cnt or 0),
            "charges_total": int(chg or 0), "charges_rows": int(ccnt or 0)}


def _active_batch(db: Session):
    return (db.query(ReceivableAdjustmentBatch)
            .filter(ReceivableAdjustmentBatch.reason_code == REASON_CODE, ReceivableAdjustmentBatch.status == "applied")
            .first())


# ───────────────────────────── 적용 ─────────────────────────────
def apply_plan(db: Session, *, plan_digest: str, confirm: str, backup_confirmed: bool, actor: str,
               exclude_member_ids=()) -> dict:
    """한 트랜잭션으로 적용. 호출자는 성공 시 commit, 예외 시 rollback 해야 한다(라우터/CLI가 처리)."""
    if confirm != CONFIRM_PHRASE:
        raise AdjustmentError(f"확인 문구가 다릅니다. {CONFIRM_PHRASE} 를 정확히 입력해 주세요.")
    if not backup_confirmed:
        raise AdjustmentError("운영 DB 백업과 복구 확인이 되지 않았습니다. 백업을 확인한 뒤 체크해 주세요.")
    if not plan_digest:
        raise AdjustmentError("미리보기의 plan_digest 가 필요합니다. 먼저 미리보기를 확인해 주세요.")
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})   # 동시 실행 차단(커밋/롤백 시 자동 해제)
    prev = _active_batch(db)
    if prev is not None:
        raise AdjustmentError(f"이미 적용된 정정 배치({prev.batch_id})가 있습니다. 이 정정은 한 번만 실행할 수 있습니다.")

    plan = build_plan(db, exclude_member_ids)
    if plan["plan_digest"] != plan_digest:
        raise AdjustmentError("미리보기 이후 자료가 바뀌었습니다(수납·부과·회원정보 변경). 미리보기를 다시 확인해 주세요.")
    targets = [r for r in plan["rows"] if r["decision"] == "target"]
    if not targets:
        raise AdjustmentError("정정 대상이 없습니다.")

    before = _totals(db)
    batch_id = datetime.now(KST).strftime("ADJ%Y%m%d%H%M%S-") + uuid.uuid4().hex[:8]
    today = datetime.now(KST).date().isoformat()
    batch = ReceivableAdjustmentBatch(
        batch_id=batch_id, reason_code=REASON_CODE, status="applied", plan_digest=plan_digest,
        member_count=len(targets), total_amount=sum(r["planned_adjustment"] for r in targets),
        payments_total_before=before["payments_total"], charges_total_before=before["charges_total"],
        created_by=actor, report={"summary": plan["summary"], "excluded_member_ids": plan["excluded_member_ids"]},
    )
    db.add(batch)
    R = _R()
    for r in targets:
        db.add(ReceivableAdjustment(
            batch_id=batch_id, member_id=r["member_id"], account_type=r["account_type"],
            balance_before=r["balance_before"], adjustment_amount=r["planned_adjustment"],
            balance_after=r["balance_before"] - r["planned_adjustment"], reason_code=REASON_CODE,
            reason=REASON_TEXT, effective_date=today, created_by=actor,
        ))
    db.flush()   # 부분 유니크 인덱스가 중복 정정을 여기서 거부한다

    # 검증: 대상 전원이 정확히 0원, 실제 수납/부과 합계는 변하지 않음 — 하나라도 틀리면 예외 → 전체 롤백
    for r in targets:
        member = db.query(models.LicenseHolder).get(r["member_id"])
        profile = db.query(ReceivableProfile).filter(ReceivableProfile.member_id == r["member_id"]).first()
        after = R._canonical_balance_parts(db, member, profile, R._current_closure_for_member(db, member))["balance"]
        if after != 0:
            raise AdjustmentError(f"회원 {r['member_id']}: 정정 후 잔액 검증 실패({after}) — 전체 취소")
    after_tot = _totals(db)
    if (after_tot["payments_total"], after_tot["payments_rows"], after_tot["charges_total"], after_tot["charges_rows"]) != \
       (before["payments_total"], before["payments_rows"], before["charges_total"], before["charges_rows"]):
        raise AdjustmentError("실제 수납/부과 합계가 변했습니다 — 전체 취소")
    batch.payments_total_after = after_tot["payments_total"]
    batch.charges_total_after = after_tot["charges_total"]
    return {"batch_id": batch_id, "members": len(targets), "total_adjusted": batch.total_amount,
            "payments_total_before": before["payments_total"], "payments_total_after": after_tot["payments_total"]}


# ───────────────────────────── 되돌리기 ─────────────────────────────
def void_batch(db: Session, batch_id: str, *, confirm: str, reason: str, actor: str) -> dict:
    if confirm != VOID_PHRASE:
        raise AdjustmentError(f"확인 문구가 다릅니다. {VOID_PHRASE} 를 입력해 주세요.")
    if len((reason or "").strip()) < 2:
        raise AdjustmentError("되돌리는 사유를 입력해 주세요.")
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": LOCK_KEY})
    batch = db.query(ReceivableAdjustmentBatch).filter(ReceivableAdjustmentBatch.batch_id == batch_id).first()
    if not batch:
        raise AdjustmentError("정정 배치를 찾을 수 없습니다.")
    if batch.status != "applied":
        raise AdjustmentError("이미 되돌린 배치입니다.")
    now = datetime.now(KST)
    n = (db.query(ReceivableAdjustment)
         .filter(ReceivableAdjustment.batch_id == batch_id, ReceivableAdjustment.voided_at.is_(None))
         .update({"voided_at": now, "voided_by": actor}, synchronize_session=False))
    batch.status, batch.voided_at, batch.voided_by, batch.void_reason = "voided", now, actor, reason.strip()
    return {"batch_id": batch_id, "voided_rows": int(n)}


# ───────────────────────────── 검증 보고서 ─────────────────────────────
def _sql_balances(db: Session, member_ids):
    R = _R()
    charges_sq, payments_sq = R._charge_payment_subqueries(db)
    expr = R._balance_sql_core(charges_sq, payments_sq).label("balance")
    q = (db.query(ReceivableProfile.member_id, expr)
         .outerjoin(charges_sq, charges_sq.c.member_id == ReceivableProfile.member_id)
         .outerjoin(payments_sq, payments_sq.c.member_id == ReceivableProfile.member_id))
    if member_ids is not None:
        q = q.filter(ReceivableProfile.member_id.in_(list(member_ids)))
    return {int(mid): int(b or 0) for mid, b in q.all()}


def consistency_report(db: Session, member_ids=None, limit_rows: int = 200) -> dict:
    """상세화면 계산(canonical)과 목록/통계 SQL 계산이 회원별로 일치하는지 비교한다. 읽기 전용."""
    R = _R()
    q = (db.query(models.LicenseHolder, ReceivableProfile)
         .join(ReceivableProfile, ReceivableProfile.member_id == models.LicenseHolder.id)
         .filter(models.LicenseHolder.deleted_at.is_(None)))
    if member_ids is not None:
        q = q.filter(models.LicenseHolder.id.in_(list(member_ids)))
    pairs = q.all()
    sqlb = _sql_balances(db, [m.id for m, _ in pairs])
    mism, checked, tot_c, tot_s = [], 0, 0, 0
    for member, profile in pairs:
        closure = R._current_closure_for_member(db, member)
        c = R._canonical_balance_parts(db, member, profile, closure)["balance"]
        s = sqlb.get(int(member.id), 0)
        checked += 1
        if (member.status or "active") != "closed":      # 합계는 활성 회원 기준(화면 합계와 같은 범위)
            tot_c += c
            tot_s += s
        if c != s:
            mism.append({"member_id": int(member.id), "name": _mask(member.name), "status": member.status or "active",
                         "detail_screen": c, "list_screen": s, "diff": s - c})
    return {"checked": checked, "mismatch_count": len(mism), "mismatches": mism[:limit_rows],
            "active_total_detail": tot_c, "active_total_list": tot_s, "totals_match": tot_c == tot_s}


def verify_batch(db: Session, batch_id: str) -> dict:
    """적용 후 검증 보고서: 정정 대상 잔액 0원, 실제 수납액 불변, 화면 계산 일치."""
    R = _R()
    batch = db.query(ReceivableAdjustmentBatch).filter(ReceivableAdjustmentBatch.batch_id == batch_id).first()
    if not batch:
        raise AdjustmentError("정정 배치를 찾을 수 없습니다.")
    adjs = db.query(ReceivableAdjustment).filter(ReceivableAdjustment.batch_id == batch_id).order_by(ReceivableAdjustment.member_id).all()
    ids = [a.member_id for a in adjs]
    members = {m.id: m for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.id.in_(ids)).all()} if ids else {}
    profiles = {p.member_id: p for p in db.query(ReceivableProfile).filter(ReceivableProfile.member_id.in_(ids)).all()} if ids else {}
    sqlb = _sql_balances(db, ids) if ids else {}
    rows, bad = [], 0
    for a in adjs:
        m, p = members.get(a.member_id), profiles.get(a.member_id)
        detail = R._canonical_balance_parts(db, m, p, R._current_closure_for_member(db, m))["balance"] if m and p else None
        listb = sqlb.get(a.member_id)
        expect_zero = batch.status == "applied" and a.voided_at is None
        ok = (detail == listb) and ((detail == 0) if expect_zero else True)
        bad += 0 if ok else 1
        rows.append({"member_id": a.member_id, "name": _mask(m.name if m else ""), "balance_before": a.balance_before,
                     "adjustment": a.adjustment_amount, "detail_balance_now": detail, "list_balance_now": listb,
                     "voided": a.voided_at is not None, "ok": ok})
    now = _totals(db)
    return {
        "batch": {"batch_id": batch.batch_id, "status": batch.status, "members": batch.member_count,
                  "total_amount": batch.total_amount, "created_by": batch.created_by,
                  "created_at": str(batch.created_at), "voided_at": str(batch.voided_at) if batch.voided_at else None},
        "payments_unchanged": {"before": batch.payments_total_before, "after_apply": batch.payments_total_after,
                               "now": now["payments_total"],
                               "ok": batch.payments_total_before == batch.payments_total_after},
        "charges_unchanged": {"before": batch.charges_total_before, "after_apply": batch.charges_total_after,
                              "now": now["charges_total"], "ok": batch.charges_total_before == batch.charges_total_after},
        "members_checked": len(rows), "members_failed": bad, "rows": rows,
        "screens": consistency_report(db),
    }


# ───────────────────────────── 금액수정(수동 1건) — 가짜 수납 대신 정정 전용 테이블 ─────────────────────────────
MANUAL_PREFIX = "manual_balance_edit"


def record_manual_adjustment(db: Session, *, member_id: int, account_type: str, balance_before: int, balance_after: int,
                             reason: str, actor: str, effective_date: str):
    """'금액수정' 1건을 receivable_adjustments 에 기록한다(실제 수납 아님). 호출자가 commit 한다.

    adjustment_amount = 정정 전 잔액 − 정정 후 잔액 (양수=미수금 감소, 음수=증가).
    """
    uid = uuid.uuid4().hex[:8]
    batch_id = datetime.now(KST).strftime("MAN%Y%m%d%H%M%S-") + uid
    amount = int(balance_before) - int(balance_after)
    db.add(ReceivableAdjustmentBatch(
        batch_id=batch_id, reason_code=f"{MANUAL_PREFIX}_{uid}", status="applied", member_count=1, total_amount=amount,
        created_by=actor, report={"kind": "manual_balance_edit", "member_id": int(member_id), "reason": reason}))
    adj = ReceivableAdjustment(
        batch_id=batch_id, member_id=int(member_id), account_type=account_type, balance_before=int(balance_before),
        adjustment_amount=amount, balance_after=int(balance_after), reason_code=f"{MANUAL_PREFIX}_{uid}",
        reason=f"[금액수정] {reason}", effective_date=effective_date, created_by=actor)
    db.add(adj)
    db.flush()
    return adj
