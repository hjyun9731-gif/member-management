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
    j = client.get("/api/receivables/workspace/audit").json()
    assert j["ok"] and j["readonly"] and not [s for s in j["sections"] if s.get("error")]
    by = {s["key"]: s for s in j["sections"]}
    assert by["stale_payments"]["rows"] and by["unissued_not_held"]["rows"] and by["closed_charged"]["rows"]
    assert by["stale_payments"]["rows"][0]["name"].endswith("*")        # 이름 마스킹
