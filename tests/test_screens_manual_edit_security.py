"""① 모든 화면 잔액 일치(김종진·허정호 5,000원 차이 재발 방지) ② 금액수정은 가짜 수납을 만들지 않음 ③ 변경 API 관리자 권한 전수 점검."""
import io, os, re, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_screens_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import models, receivable_adjustments as svc
from app.auth import create_access_token
from app.database import Base, engine, SessionLocal, get_db
from app.receivables_models import ReceivableAdjustment, ReceivablePayment, ReceivableProfile
from app.routers import receivables as R, receivables_workspace as W, receivables_adjustments as RA
from app.routers import receivables_reconcile_20261008 as RC, receivables_patch_20261007 as P1, receivables_patch_20261007_v2 as P2
import test_unissued_adjustments as T      # 같은 가상 회원 데이터 재사용

db = T.db   # fixture 재사용

@pytest.fixture()
def tokens(db):
    for n, role in (("admin1", "admin"), ("staff1", "staff")):
        db.add(models.User(username=n, password_hash="x", role=role))
    db.commit()
    return {r: {"Authorization": "Bearer " + create_access_token({"sub": n})} for n, r in (("admin1", "admin"), ("staff1", "staff"))}

@pytest.fixture()
def client(db, tokens, monkeypatch):
    for fn in ("_ensure_receivables_read_ready", "_ensure_db_ledger_ready", "_ensure_current_month_billing"):
        monkeypatch.setattr(R, fn, lambda *a, **k: None)
    app = FastAPI()
    for r in (R.router, W.router, RA.router):
        app.include_router(r)
    return TestClient(app)

def bal(db, mid):
    m = db.query(models.LicenseHolder).get(mid); p = db.query(ReceivableProfile).filter_by(member_id=mid).first()
    return R._canonical_balance_parts(db, m, p, R._current_closure_for_member(db, m))["balance"]

# ───────── ① 화면 일치 ─────────
def test_all_screens_same_balance_no_5000_gap(db, client, tokens):
    h = tokens["admin"]
    list_items = {i["name"]: i["balance"] for i in client.get("/api/receivables/members?scope=active&limit=200", headers=h).json()["items"]}
    ledger = {r["name"]: r["balance"] for r in client.get("/api/receivables/workspace/data/ledger?status=active&size=500", headers=h).json()["rows"]}
    for mid, name in ((2, "김종진"), (3, "허정호"), (1, "송제욱"), (8, "한발급"), (11, "수납있음")):
        detail = client.get(f"/api/receivables/members/{mid}", headers=h).json()["member"]["balance"]
        c = bal(db, mid)
        assert detail == list_items[name] == ledger[name] == c, (name, detail, list_items[name], ledger[name], c)
    # 미발급 택배의 이번 달 자동부과 5,000원은 어느 화면에서도 더해지지 않는다(김종진 210,000 / 허정호 240,000).
    assert list_items["김종진"] == 210000 and list_items["허정호"] == 240000
    # 발급번호가 있는 송제욱·발급 완료 한발급은 이번 달 5,000원이 정상 반영된다.
    assert list_items["송제욱"] == 55000 and list_items["한발급"] == 20000

def test_dashboard_summary_monthly_export_match(db, client, tokens):
    h = tokens["admin"]
    active = [m for m in db.query(models.LicenseHolder).all() if (m.status or "active") != "closed"]
    exp_arrears = sum(max(bal(db, m.id), 0) for m in active)
    assert client.get("/api/receivables/summary", headers=h).json()["active_arrears_total"] == exp_arrears
    assert client.get("/api/receivables/dashboard", headers=h).json()["summary"]["active_arrears_total"] == exp_arrears
    # 월별 원장(전체 활성+폐업 기준)의 현재월 미수 합계
    all_pos = sum(max(bal(db, m.id), 0) for m in db.query(models.LicenseHolder).all())
    assert client.get("/api/receivables/monthly-analysis", headers=h).json()["current"]["arrears_total"] == all_pos
    # 엑셀: 김종진 행에 210,000 은 있고 215,000 은 없다
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(client.get("/api/receivables/export.xlsx?view=arrears", headers=h).content))
    nums = [c.value for ws in wb for row in ws.iter_rows() if any(c.value == "김종진" for c in row) for c in row if isinstance(c.value, (int, float))]
    assert 210000 in nums and 215000 not in nums

def test_after_correction_every_screen_zero_and_stays(db, client, tokens):
    h = tokens["admin"]
    p = svc.build_plan(db); db.rollback()
    body = {"plan_digest": p["plan_digest"], "confirm": svc.CONFIRM_PHRASE, "backup_confirmed": True}
    assert client.post("/api/receivables/adjustments/unissued-management/apply", json=body, headers=h).status_code == 200
    items = {i["name"]: i["balance"] for i in client.get("/api/receivables/members?scope=active&limit=200", headers=h).json()["items"]}
    assert items["김종진"] == items["허정호"] == items["수납있음"] == 0
    assert items["오일반"] == 30000 and items["계정불일치"] == 25000       # 별도 검토 대상은 그대로
    assert client.get("/api/receivables/members/2", headers=h).json()["member"]["balance"] == 0
    all_pos = sum(max(bal(db, m.id), 0) for m in db.query(models.LicenseHolder).all())
    assert client.get("/api/receivables/monthly-analysis", headers=h).json()["current"]["arrears_total"] == all_pos
    assert client.get("/api/receivables/adjustments/consistency", headers=h).json()["mismatch_count"] == 0
    d = client.get("/api/receivables/members/2", headers=h).json()
    assert d["adjustments"] and all(a["adjustment_amount"] > 0 for a in d["adjustments"]) and d["payments"] == []   # 정정은 수납 목록에 없음

# ───────── ② 금액수정 ─────────
def test_manual_balance_edit_uses_adjustments_not_payments(db, client, tokens):
    pay0 = (db.query(ReceivablePayment).count(), svc._totals(db))
    body = {"balance_type": "미수금", "amount": 50000, "reason": "테스트 정정"}
    assert client.patch("/api/receivables/members/2/balance", json=body, headers=tokens["staff"]).status_code == 403    # 관리자 전용
    r = client.patch("/api/receivables/members/2/balance", json=body, headers=tokens["admin"]); assert r.status_code == 200, r.text
    assert r.json()["old_balance"] == 210000 and r.json()["new_balance"] == 50000
    db.expire_all()
    assert (db.query(ReceivablePayment).count(), svc._totals(db)) == pay0                 # 수납 행·합계 불변(가짜 수납 없음)
    assert db.query(ReceivablePayment).filter(ReceivablePayment.method == "잔액수정").count() == 0
    a = db.query(ReceivableAdjustment).one(); assert a.adjustment_amount == 160000 and a.balance_before == 210000 and a.balance_after == 50000
    assert bal(db, 2) == 50000 and svc.consistency_report(db)["mismatch_count"] == 0
    # 선납으로 수정 + 증액도 가능, 모두 정정 테이블에만 기록
    assert client.patch("/api/receivables/members/2/balance", json={"balance_type": "선납", "amount": 10000, "reason": "선납 정정"}, headers=tokens["admin"]).status_code == 200
    assert client.patch("/api/receivables/members/3/balance", json={"balance_type": "미수금", "amount": 300000, "reason": "증액 정정"}, headers=tokens["admin"]).status_code == 200
    db.expire_all()
    assert bal(db, 2) == -10000 and bal(db, 3) == 300000 and db.query(ReceivablePayment).count() == pay0[0]
    assert db.query(ReceivableAdjustment).count() == 3
    # 되돌리기(void) 가능
    for b in client.get("/api/receivables/adjustments/batches", headers=tokens["admin"]).json():
        assert client.post(f"/api/receivables/adjustments/batches/{b['batch_id']}/void", json={"confirm": svc.VOID_PHRASE, "reason": "테스트"}, headers=tokens["admin"]).status_code == 200
    db.expire_all(); assert bal(db, 2) == 210000 and bal(db, 3) == 240000

def test_manual_edit_does_not_block_unissued_correction(db, client, tokens):
    client.patch("/api/receivables/members/2/balance", json={"balance_type": "미수금", "amount": 100000, "reason": "중간 정정"}, headers=tokens["admin"])
    p = svc.build_plan(db); db.rollback()
    row = next(r for r in p["rows"] if r["member_id"] == 2); assert row["decision"] == "target" and row["planned_adjustment"] == 100000
    assert svc.apply_plan(db, plan_digest=p["plan_digest"], confirm=svc.CONFIRM_PHRASE, backup_confirmed=True, actor="a")["members"] == 3
    db.commit(); db.expire_all(); assert bal(db, 2) == 0

def test_edit_endpoint_source_has_no_payment_row():
    import inspect
    src = inspect.getsource(R.edit_current_balance)
    assert "ReceivablePayment(" not in src and "잔액수정" not in src.split('"""', 2)[2]

# ───────── ③ 변경 API 권한 전수 점검 ─────────
# 일반 직원도 사용해야 하는 '정상 업무' API — 로그인만 있으면 403이 나지 않아야 한다.
# (정상 수납 입력, 수납 취소, 연락기록, 계정 설정, 통장 업로드 미리보기/매칭/반영)
STAFF_ALLOWED_WRITE_ROUTES = {
    ("POST", "/api/receivables/members/{member_id}/payments"),
    ("DELETE", "/api/receivables/payments/{payment_id}"),
    ("POST", "/api/receivables/members/{member_id}/contacts"),
    ("PATCH", "/api/receivables/members/{member_id}/account"),
    ("POST", "/api/receivables/imports/preview"),
    ("PATCH", "/api/receivables/imports/rows/{row_id}/match"),
    ("POST", "/api/receivables/imports/{batch_id}/post"),
}

def _mutating_routes(app):
    """OpenAPI 에 노출된 모든 경로를 사용한다(중첩 라우터 포함)."""
    out = []
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/api/receivables"):
            continue
        for m in ops:
            if m.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                out.append((m.upper(), path))
        if path == "/api/receivables/sync":                     # GET 이지만 데이터를 만든다
            out.append(("GET", path))
    return out

def test_admin_only_write_routes_require_admin(db, tokens):
    """정정·금액수정·reconcile·동기화는 일반 직원이 호출하면 403이어야 한다."""
    import app.main as M
    sub = FastAPI()
    for r in (RC.router, P1.router, P2.router):                 # railway_entry / 보정 모듈 라우터
        sub.include_router(r)
    checked = 0
    for app in (M.app, sub):
        c = TestClient(app, raise_server_exceptions=False)
        for method, path in _mutating_routes(app):
            if (method, path) in STAFF_ALLOWED_WRITE_ROUTES:
                continue
            url = re.sub(r"\{[^}]+\}", "1", path)
            no_token = c.request(method, url, json={})
            assert no_token.status_code == 401, (method, path, no_token.status_code)          # 로그인 없이 변경 불가
            staff = c.request(method, url, json={}, headers=tokens["staff"])
            assert staff.status_code == 403, (method, path, staff.status_code)               # 일반 직원 변경 불가
            checked += 1
    assert checked >= 8, checked                                 # 엔드포인트가 실제로 점검되었는지(빈 루프 방지)
    # 점검 대상에는 금액수정·정정·reconcile/apply·동기화가 모두 포함되어야 한다
    paths = {p for app in (M.app, sub) for _, p in _mutating_routes(app)}
    for must in ("/api/receivables/members/{member_id}/balance", "/api/receivables/sync", "/api/receivables/adjustments/unissued-management/apply",
                 "/api/receivables/reconcile-20261008/apply", "/api/receivables/reconcile-20260916-v4/apply"):
        assert must in paths, must

def test_staff_write_routes_require_login_but_not_admin(db, tokens):
    """정상 수납 입력·통장 업로드·연락기록은 로그인만 하면 일반 직원도 사용할 수 있어야 한다(403이면 안 됨)."""
    import app.main as M
    c = TestClient(M.app, raise_server_exceptions=False)
    checked = 0
    for method, path in _mutating_routes(M.app):
        if (method, path) not in STAFF_ALLOWED_WRITE_ROUTES:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        no_token = c.request(method, url, json={})
        assert no_token.status_code == 401, (method, path, no_token.status_code)              # 로그인은 여전히 필수
        staff = c.request(method, url, json={}, headers=tokens["staff"])
        assert staff.status_code != 403, (method, path, staff.status_code)                    # 관리자가 아니어도 403은 아님
        checked += 1
    assert checked == len(STAFF_ALLOWED_WRITE_ROUTES), checked

def test_staff_can_record_normal_payment_and_contact(db, client, tokens):
    """일반 직원 토큰으로 정상 수납 입력·연락기록이 실제로 성공해야 한다."""
    h = tokens["staff"]
    pay_body = {"payment_date": "2026-10-10", "amount": 30000, "method": "현금", "memo": "직원 테스트 수납"}
    r = client.post("/api/receivables/members/2/payments", json=pay_body, headers=h)
    assert r.status_code == 200, r.text
    payment_id = r.json()["payment_id"]
    assert client.delete(f"/api/receivables/payments/{payment_id}", headers=h).status_code == 200
    contact_body = {"contact_date": "2026-10-10", "contact_method": "전화", "status": "연락완료", "memo": "직원 테스트 연락"}
    r = client.post("/api/receivables/members/2/contacts", json=contact_body, headers=h)
    assert r.status_code == 200, r.text
    # 금액수정은 여전히 일반 직원에게 막혀 있어야 한다
    assert client.patch(
        "/api/receivables/members/2/balance",
        json={"balance_type": "미수금", "amount": 1, "reason": "x"},
        headers=h,
    ).status_code == 403
