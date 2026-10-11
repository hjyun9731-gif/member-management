"""통합 원장 API 테스트 — 읽기 전용 확인. DATABASE_URL 이 postgresql(로컬 테스트 DB)이면 점검 API까지 검증."""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_test_ws.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.database import Base, engine, SessionLocal, get_db
from app import models
from app.auth import get_current_user
from app.routers import receivables_workspace as W

IS_PG = engine.dialect.name == "postgresql"

@pytest.fixture()
def client():
    app = FastAPI(); app.include_router(W.router)
    def _db():
        d = SessionLocal()
        try: yield d
        finally: d.close()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"username": "t", "role": "admin"})()
    from app.auth import require_admin
    app.dependency_overrides[require_admin] = lambda: type("U", (), {"username": "t", "role": "admin"})()
    return TestClient(app)

@pytest.mark.parametrize("tab", ["bank","ledger","monthly","match","review","closed","suspense","history"])
def test_tabs_return_json(client, tab):
    if not IS_PG:
        Base.metadata.create_all(bind=engine)
    r = client.get(f"/api/receivables/workspace/data/{tab}")
    assert r.status_code == 200, r.text
    j = r.json(); assert j["tab"] == tab and isinstance(j["rows"], list) and j["columns"]

def test_csv_and_bad_tab(client):
    if not IS_PG: Base.metadata.create_all(bind=engine)
    r = client.get("/api/receivables/workspace/data/ledger?fmt=csv"); assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf")
    assert client.get("/api/receivables/workspace/data/nope").status_code == 404
    assert client.get("/api/receivables/workspace/data/ledger?sort=name;drop").status_code == 200   # 정렬키는 허용목록만 사용

def test_no_write_endpoints():
    methods = {m for r in W.router.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD"}

@pytest.mark.skipif(not IS_PG, reason="점검 API는 Postgres 전용")
def test_audit_readonly_and_results(client):
    """점검 API(READ ONLY 트랜잭션): 테스트가 직접 가상 자료를 넣고, 의심 항목이 잡히는지·이름이 가려지는지·DB가 안 바뀌는지 확인."""
    from sqlalchemy import text
    from app.receivables_models import ReceivableProfile, ReceivableCharge, ReceivablePayment
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    d = SessionLocal()
    d.execute(text("CREATE TABLE IF NOT EXISTS receivable_billing_exclusions(member_id int primary key, reason text, patch_id text, created_at timestamptz default now())"))
    d.add(models.Closure(id=1, management_number="폐-1", closure_type="폐업", name="박폐업", vehicle_number="강원81배2222", closure_date="2026-03-10"))
    d.add_all([
        models.LicenseHolder(id=1, name="이미발급", vehicle_number="강원80배1111", category="택배", status="active"),
        models.LicenseHolder(id=2, name="박폐업", vehicle_number="강원81배2222", category="택배", status="closed", closure_id=1,
                             certificate_number="X1"),
    ])
    d.add_all([
        ReceivableProfile(member_id=1, account_type="관리비", unit_fee=5000, vehicle_count=1, legacy_balance=30000, legacy_months=[], receivable_active=1,
                          account_manual_override=0, legacy_note="[20261008 월별장부 전수정정 V4]"),
        ReceivableProfile(member_id=2, account_type="관리비", unit_fee=5000, vehicle_count=1, legacy_balance=0, legacy_months=[], receivable_active=1,
                          account_manual_override=0),
        ReceivableCharge(member_id=1, billing_month="2026-10", amount=5000, account_type="관리비", source="auto"),
        ReceivableCharge(member_id=2, billing_month="2026-05", amount=5000, account_type="관리비", source="auto"),
        ReceivablePayment(member_id=1, payment_date="2026-09-05", amount=15000, method="계좌이체"),
    ])
    d.commit(); before = d.execute(text("select count(*), coalesce(sum(amount),0) from receivable_payments")).fetchone(); d.close()
    j = client.get("/api/receivables/workspace/audit").json()
    assert j["ok"] and j["readonly"] and not [x for x in j["sections"] if x.get("error")], [x for x in j["sections"] if x.get("error")]
    by = {x["key"]: x for x in j["sections"]}
    assert len(by["stale_payments"]["rows"]) == 1 and by["stale_payments"]["rows"][0]["name"] == "이***"        # 이름 마스킹
    assert len(by["unissued_not_held"]["rows"]) == 1 and len(by["closed_charged"]["rows"]) == 1
    d = SessionLocal(); after = d.execute(text("select count(*), coalesce(sum(amount),0) from receivable_payments")).fetchone(); d.close()
    assert tuple(before) == tuple(after)                                                                    # 점검은 DB를 바꾸지 않는다
