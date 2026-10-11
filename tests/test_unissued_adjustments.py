"""미발급 관리비 정정 시나리오 테스트(가상 데이터). DATABASE_URL 이 postgresql(로컬 테스트 DB)이면 PostgreSQL 에서 실행된다."""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_adj_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func
from app import models, receivable_adjustments as svc
from app.database import Base, engine, SessionLocal, get_db
from app.auth import get_current_user, require_admin, admin_for_writes
from app.receivables_models import ReceivableProfile, ReceivableCharge, ReceivablePayment, ReceivableAdjustment, ReceivableAdjustmentBatch
from app.routers import receivables as R, receivables_adjustments as RA, receivables_workspace as W

KST = ZoneInfo("Asia/Seoul")
NOW = datetime.now(KST)
THIS_MONTH = NOW.strftime("%Y-%m")
PREV_MONTH = f"{NOW.year}-{NOW.month-1:02d}" if NOW.month > 1 else f"{NOW.year-1}-12"
TODAY = NOW.date().isoformat()

# id: (name, vehicle, category, status, cert_date, cert_no, membership_date, account, legacy_balance, charges[(month,amt)], payments[(date,amt)], override)
MEMBERS = {
 1: ("송제욱", "강원80배1001", "택배", "active", None, "A-2026-0001", None, "관리비", 50000, [(THIS_MONTH, 5000)], [], 0),      # 발급번호만 있음 → 미발급 아님
 2: ("김종진", "강원80배1002", "택배", "active", "", "  ", None, "관리비", 210000, [(THIS_MONTH, 5000)], [], 0),               # 미발급, 양수 → 대상
 3: ("허정호", "강원80배1003", "택배", "active", None, None, None, "관리비", 240000, [(THIS_MONTH, 5000)], [], 0),             # 미발급, 양수 → 대상
 4: ("박선납", "강원80배1004", "택배", "active", None, None, None, "관리비", -20000, [], [], 0),                              # 선납 유지
 5: ("이협회", "강원80배1005", "택배", "active", None, None, "2025-03-01", "협회비", 90000, [], [], 1),                      # 협회비 → 제외
 6: ("최폐업", "강원80배1006", "택배", "closed", None, None, None, "관리비", 30000, [], [], 0),                               # 폐업 → 제외
 7: ("정영점", "강원80배1007", "택배", "active", None, None, None, "관리비", 0, [], [], 0),                                   # 이미 0원
 8: ("한발급", "강원80배1008", "택배", "active", "2026-01-05", "B-77", None, "관리비", 15000, [(THIS_MONTH, 5000)], [], 0),   # 발급 완료 → 변경 없음
 9: ("오일반", "강원12바1009", "일반화물", "active", None, None, None, "관리비", 30000, [], [], 0),                          # 비택배 → 대상(확인필요 표시)
 10: ("계정불일치", "강원80배1010", "택배", "active", None, None, "2025-05-05", "관리비", 25000, [], [], 0),                   # 가입자인데 관리비 프로필 → 제외
 11: ("수납있음", "강원80배1011", "택배", "active", None, None, None, "관리비", 100000, [], [(TODAY, 30000)], 0),              # 실수납 있음 → 70,000 만 정정
}

@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    R._receivables_schema_ready = False; R._ensure_receivables_schema_ready()
    s = SessionLocal()
    for mid, (n, v, cat, st, cd, cn, md, acct, lb, chs, pays, ov) in MEMBERS.items():
        s.add(models.LicenseHolder(id=mid, name=n, vehicle_number=v, category=cat, status=st, certificate_issue_date=cd,
                                   certificate_number=cn, membership_date=md))
        s.add(ReceivableProfile(member_id=mid, account_type=acct, unit_fee=R.ACCOUNT_FEES[acct], vehicle_count=1, legacy_balance=lb,
                                legacy_months=[], receivable_active=1, account_manual_override=ov, first_charge_date="2026-01-01"))
        for m, a in chs:
            s.add(ReceivableCharge(member_id=mid, billing_month=m, amount=a, account_type=acct, source="auto"))
        for d, a in pays:
            s.add(ReceivablePayment(member_id=mid, payment_date=d, amount=a, method="계좌이체", created_by="t"))
    s.commit()
    yield s
    s.close()

def ready(db):
    plan = svc.build_plan(db); db.rollback(); return plan

def test_target_selection(db):
    p = ready(db); by = {r["member_id"]: r for r in p["rows"]}
    assert 1 not in by and 8 not in by                     # 발급번호/발급일 중 하나라도 있으면 미발급 아님(송제욱)
    assert 5 not in by                                      # 협회비 계정은 대상 아님
    assert by[2]["decision"] == by[3]["decision"] == "target"
    assert by[4]["decision"] == "credit_kept" and by[4]["planned_adjustment"] == 0
    assert by[6]["decision"] == "review_closed" and "폐업" in by[6]["reason"] and by[6]["planned_adjustment"] == 0
    assert by[7]["decision"] == "already_zero"
    assert by[9]["decision"] == "review_non_bae" and by[9]["planned_adjustment"] == 0   # 비택배는 자동정정 제외 → 별도 검토 목록
    assert by[10]["decision"] == "review_account" and "계정 불일치" in by[10]["reason"]
    assert by[11]["balance_before"] == 70000 and by[11]["planned_adjustment"] == 70000   # 실수납은 이미 반영, 남은 70,000만 정정

def test_preview_is_readonly(db):
    before = (db.query(ReceivableAdjustment).count(), db.query(ReceivableAdjustmentBatch).count(), svc._totals(db))
    ready(db); ready(db)
    assert before == (db.query(ReceivableAdjustment).count(), db.query(ReceivableAdjustmentBatch).count(), svc._totals(db))

def test_apply_zeroes_without_fake_payments(db):
    p = ready(db); tot0 = svc._totals(db)
    res = svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="admin")
    db.commit()
    tot1 = svc._totals(db)
    assert tot0 == tot1                                      # 실제 수납/부과 행·합계 불변(가짜 수납 없음)
    assert db.query(ReceivablePayment).filter(ReceivablePayment.method == "잔액수정").count() == 0
    v = svc.verify_batch(db, res["batch_id"])
    assert v["members_failed"] == 0 and v["payments_unchanged"]["ok"] and v["charges_unchanged"]["ok"]
    assert v["screens"]["mismatch_count"] == 0 and v["screens"]["totals_match"]
    ids = {r["member_id"] for r in v["rows"]}
    assert ids == {2, 3, 11} and all(r["detail_balance_now"] == 0 == r["list_balance_now"] for r in v["rows"])
    # 선납·발급자·협회비·폐업은 그대로
    R_ = R; parts = lambda i: R_._canonical_balance_parts(db, db.query(models.LicenseHolder).get(i), db.query(ReceivableProfile).filter_by(member_id=i).first(),
                                                         R_._current_closure_for_member(db, db.query(models.LicenseHolder).get(i)))["balance"]
    assert parts(4) == -20000 and parts(5) == 90000 and parts(6) == 30000 and parts(1) == 55000 and parts(8) == 20000
    assert parts(9) == 30000 and parts(10) == 25000          # 비택배·계정불일치는 정정되지 않음

def test_apply_only_once_and_idempotent(db):
    p = ready(db)
    svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a"); db.commit()
    n = db.query(ReceivableAdjustment).count()
    with pytest.raises(svc.AdjustmentError):
        svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a")
    db.rollback()
    assert db.query(ReceivableAdjustment).count() == n

def test_guards(db):
    p = ready(db)
    for kw in ({"confirm": "wrong"}, {"backup_confirmed": False}, {"plan_digest": "0" * 64}):
        args = dict(plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a"); args.update(kw)
        with pytest.raises(svc.AdjustmentError):
            svc.apply_plan(db, **args)
        db.rollback()
    assert db.query(ReceivableAdjustment).count() == 0

def test_data_change_after_preview_rejected(db):
    p = ready(db)
    db.add(ReceivablePayment(member_id=2, payment_date=TODAY, amount=1000, method="계좌이체")); db.commit()   # 미리보기 후 입금 발생
    with pytest.raises(svc.AdjustmentError):
        svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a")
    db.rollback(); assert db.query(ReceivableAdjustment).count() == 0

def test_full_rollback_on_failure(db, monkeypatch):
    p = ready(db)
    real = R._canonical_balance_parts; calls = {"n": 0}
    def flaky(*a, **k):
        out = real(*a, **k)
        if calls["n"] >= 1 and svc.REASON_CODE and out["adjustments"] > 0:   # 정정 후 검증 단계에서 3번째 회원이 어긋난 것처럼
            calls["n"] += 1
            if calls["n"] == 3: out = {**out, "balance": 999}
        elif out["adjustments"] > 0: calls["n"] += 1
        return out
    monkeypatch.setattr(R, "_canonical_balance_parts", flaky)
    with pytest.raises(svc.AdjustmentError):
        svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a")
    db.rollback()
    assert db.query(ReceivableAdjustment).count() == 0 and db.query(ReceivableAdjustmentBatch).count() == 0

def test_void_restores_balances(db):
    p = ready(db)
    r = svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a"); db.commit()
    svc.void_batch(db, r["batch_id"], confirm=svc.VOID_PHRASE, reason="테스트 되돌리기", actor="a"); db.commit()
    assert svc.consistency_report(db)["mismatch_count"] == 0
    p2 = ready(db); assert p2["summary"]["정정 대상(택배 미발급·양수 미수)"] == p["summary"]["정정 대상(택배 미발급·양수 미수)"]   # 다시 대상이 됨(기록은 보존)
    assert db.query(ReceivableAdjustment).count() == 3               # 무효 처리만, 삭제 없음

def test_consistency_before_and_site_numbers(db):
    c = svc.consistency_report(db)
    assert c["mismatch_count"] == 0 and c["totals_match"], c["mismatches"]

def test_new_management_fee_blocked_for_unissued(db):
    prof = lambda i: db.query(ReceivableProfile).filter_by(member_id=i).first()
    mem = lambda i: db.query(models.LicenseHolder).get(i)
    for month in ("2026-11", "2027-03", THIS_MONTH):
        assert R._auto_charge_allowed_for_month(prof(2), mem(2), month) is False      # 미발급 → 연도 무관 차단
    assert R._auto_charge_allowed_for_month(prof(1), mem(1), "2026-11") is True       # 발급번호 있는 송제욱은 차단 안 함
    assert R._auto_charge_allowed_for_month(prof(5), mem(5), "2026-11") is True       # 협회비는 영향 없음

# ───── HTTP: 관리자 API ─────
@pytest.fixture()
def client(db):
    app = FastAPI(); app.include_router(RA.router); app.include_router(W.router)
    def _db():
        d = SessionLocal()
        try: yield d
        finally: d.close()
    U = lambda: type("U", (), {"username": "admin1", "role": "admin"})()
    app.dependency_overrides.update({get_db: _db, get_current_user: U, require_admin: U, admin_for_writes: lambda: None})
    return TestClient(app)

def test_http_flow(client):
    pv = client.get("/api/receivables/adjustments/unissued-management/preview").json()
    assert pv["summary"]["정정 대상(택배 미발급·양수 미수)"] == 3 and pv["summary"]["정정 예정액 합계"] == 210000 + 240000 + 70000
    assert pv["summary"]["별도 검토 — 비택배"] == 1 and pv["summary"]["별도 검토 — 폐업"] == 1 and pv["summary"]["별도 검토 — 계정 불일치"] == 1
    assert client.get("/api/receivables/adjustments/unissued-management/preview.csv").content.startswith(b"\xef\xbb\xbf")
    body = {"plan_digest": pv["plan_digest"], "confirm": "no", "backup_confirmed": True}
    assert client.post("/api/receivables/adjustments/unissued-management/apply", json=body).status_code == 400
    body["confirm"] = svc.CONFIRM_PHRASE
    ok = client.post("/api/receivables/adjustments/unissued-management/apply", json=body); assert ok.status_code == 200, ok.text
    assert client.post("/api/receivables/adjustments/unissued-management/apply", json=body).status_code == 400   # 두 번째는 거부
    bid = ok.json()["batch_id"]
    v = client.get(f"/api/receivables/adjustments/batches/{bid}/verify").json()
    assert v["members_failed"] == 0 and v["screens"]["mismatch_count"] == 0
    led = client.get("/api/receivables/workspace/data/ledger?q=김종진").json()["rows"][0]
    assert led["balance"] == 0 and led["adjustments"] > 0 and led["payments"] == 0   # 정정은 수납 칸에 없다
    assert client.get("/api/receivables/adjustments/consistency").json()["mismatch_count"] == 0
