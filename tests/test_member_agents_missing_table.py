"""운영 장애 재현: member_agents 테이블 누락 → PUT /api/members/{id} 500.
복구(필수 테이블 요청 전 보장·멱등·비파괴), 실패 시 회원정보 부분 저장 없음(롤백), 배포 전 마이그레이션 검증 — 임시 sqlite."""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_agents_missing_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from app import models, migrate
from app.auth import get_current_user, require_admin
from app.database import Base, engine, SessionLocal
from app.main import app
from app.services import schema_guard

HAS = lambda: inspect(engine).has_table("member_agents")


@pytest.fixture()
def prod_like():
    """운영 상태 재현: 기존 회원·변경이력 데이터가 있고 member_agents 테이블만 없다."""
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    m = models.LicenseHolder(status="active", category="개인", region="춘천시", name="차주",
                             vehicle_number="강원80자1111", address="옛주소", agent_name="김철수", agent_mobile="010-1")
    db.add(m); db.commit(); db.refresh(m)
    db.add(models.ChangeHistory(change_type="상호변경", region="춘천시", vehicle_number="강원80자1111", name="차주",
                                before_value="a", after_value="b", change_date="2026.10.01", member_id=m.id)); db.commit()
    mid = m.id; db.close()
    with engine.begin() as c:
        c.execute(text("DROP TABLE member_agents"))
    assert not HAS()
    schema_guard.reset()
    u = models.User(id=1, username="t", password_hash="x", role="admin", full_name="t")
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[require_admin] = lambda: u
    yield mid
    app.dependency_overrides.clear(); schema_guard.reset()


def _member(mid):
    db = SessionLocal()
    try:
        m = db.get(models.LicenseHolder, mid)
        return m.address, m.agent_name
    finally:
        db.close()


def test_without_guard_500_and_no_partial_save(prod_like):
    app.dependency_overrides[schema_guard.guard_dependency] = lambda: None          # 현재 운영 상태 재현
    r = TestClient(app, raise_server_exceptions=False).put(
        f"/api/members/{prod_like}", json={"address": "새주소", "agent_name": "이영희"})
    assert r.status_code == 500
    assert _member(prod_like) == ("옛주소", "김철수")                                 # 주소만 바뀌는 부분 저장 없음(롤백)
    db = SessionLocal()
    assert db.query(models.ChangeHistory).filter_by(member_id=prod_like).count() == 1   # 변경이력도 추가되지 않음
    db.close()


def test_guard_creates_table_then_put_and_history_work_data_preserved(prod_like):
    client = TestClient(app, raise_server_exceptions=False)
    r = client.put(f"/api/members/{prod_like}", json={"address": "새주소", "agent_name": "이영희",
                                                      "agent_start_date": "2026.10.30"})
    assert r.status_code == 200, r.text[:300]
    assert HAS()
    assert _member(prod_like) == ("새주소", "이영희")
    hist = client.get(f"/api/members/{prod_like}/agents")
    assert hist.status_code == 200
    names = [h["name"] for h in hist.json()["items"]]
    assert "이영희" in names and "김철수" in names                                    # 이전 대리인 보존
    db = SessionLocal()
    assert db.query(models.LicenseHolder).count() == 1                               # 기존 회원 유지
    assert db.query(models.ChangeHistory).filter_by(change_type="상호변경").count() == 1
    db.close()


def test_ensure_tables_is_idempotent_and_non_destructive(prod_like):
    assert schema_guard.ensure_tables(engine) and HAS()
    db = SessionLocal(); db.add(models.MemberAgent(member_id=prod_like, name="보존", status="현재")); db.commit(); db.close()
    assert schema_guard.ensure_tables(engine) and schema_guard.ensure_tables(engine)
    db = SessionLocal(); assert db.query(models.MemberAgent).filter_by(name="보존").count() == 1; db.close()


def test_migrate_cli_repairs_and_verifies(prod_like):
    assert migrate.run(max_wait=5, interval=1) == 0 and HAS()
    assert migrate.run(max_wait=5, interval=1) == 0                                  # 재실행 안전
    assert _member(prod_like) == ("옛주소", "김철수")


def test_admin_schema_status_endpoint(prod_like):
    app.dependency_overrides[schema_guard.guard_dependency] = lambda: None
    body = TestClient(app).get("/api/admin/schema-status").json()
    assert body["ok"] is False and body["missing_tables"] == ["member_agents"]
    schema_guard.ensure_tables(engine)
    body = TestClient(app).get("/api/admin/schema-status").json()
    assert body["ok"] is True and body["missing_tables"] == []
