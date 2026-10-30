"""운영 DB에 change_history.judgement 가 없을 때의 500 재현 및 복구(비파괴·멱등) 검증 — 임시 sqlite."""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_guard_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from app import models
from app.auth import get_current_user, require_admin
from app.database import Base, engine, SessionLocal
from app.main import app
from app.services import schema_guard

CH_COLS = lambda: {c["name"] for c in inspect(engine).get_columns("change_history")}


@pytest.fixture()
def old_schema_db():
    """이전 버전(판정 컬럼 없음) 운영 스키마 + 기존 이력 3건을 재현"""
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS ix_change_history_judgement"))
        c.execute(text("ALTER TABLE change_history DROP COLUMN judgement"))
        c.execute(text("ALTER TABLE change_history DROP COLUMN judge_reason"))
        for i, (t, d) in enumerate([("주소지변경", "2026.10.05"), ("상호변경", "2026.10.06"), ("번호변경", "2026.10.07")], 1):
            c.execute(text("INSERT INTO change_history (change_type, region, vehicle_number, name, before_value, after_value, change_date) "
                           "VALUES (:t,'춘천시',:v,'가나다','a','b',:d)"), {"t": t, "v": f"강원80배{i:04d}", "d": d})
    assert "judgement" not in CH_COLS()
    schema_guard.reset()
    user = models.User(id=1, username="t", password_hash="x", role="admin", full_name="t")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[require_admin] = lambda: user
    yield
    app.dependency_overrides.clear(); schema_guard.reset()


def test_reproduces_500_without_guard_then_guard_fixes_without_touching_data(old_schema_db):
    app.dependency_overrides[schema_guard.guard_dependency] = lambda: None      # 보호장치 끔 = 현재 운영 상태
    broken = TestClient(app, raise_server_exceptions=False).get("/api/change-history")
    assert broken.status_code == 500                                              # 원인 재현 (no such column: judgement)
    del app.dependency_overrides[schema_guard.guard_dependency]

    client = TestClient(app, raise_server_exceptions=False)                      # 보호장치 켬
    for url in ["/api/change-history", "/api/change-stats/summary?year=2026&month=10", "/api/change-stats/detail?bucket=전체&year=2026&month=10",
                "/api/dashboard/monthly-report-auto?year=2026&month=10", "/api/dashboard/full-stats"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:200])
    assert {"judgement", "judge_reason"} <= CH_COLS()
    db = SessionLocal()
    try:   # 기존 3건 보존, 판정은 NULL 유지(소급 재분류 없음)
        rows = db.query(models.ChangeHistory).all()
        assert len(rows) == 3 and all(r.judgement is None and r.judge_reason is None for r in rows)
        assert sorted(r.change_type for r in rows) == ["번호변경", "상호변경", "주소지변경"]
    finally:
        db.close()
    # 월례보고서: 기존 이력은 그대로 집계(상호변경 1, 주소변경 1)
    aw = client.get("/api/dashboard/monthly-report-auto?year=2026&month=10").json()["admin_work"]
    assert aw["상호변경"] == 1 and aw["주소변경"] == 1 and aw["차량변경"] == 0


def test_ensure_columns_is_idempotent_and_non_destructive(old_schema_db):
    assert schema_guard.ensure_columns(engine) is True
    assert schema_guard.ensure_columns(engine) is True        # 이미 존재 — 오류 없이 재실행
    assert schema_guard.ensure_columns(engine) is True
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM change_history")).scalar() == 3
    # 기존 _run_migrations(컬럼 이미 존재)도 오류 없이 재실행
    from app.main import _run_migrations
    _run_migrations(); _run_migrations()
    assert {"judgement", "judge_reason"} <= CH_COLS()


def test_member_edit_works_on_old_schema(old_schema_db):
    db = SessionLocal()
    try:
        m = models.LicenseHolder(vehicle_number="강원80배9001", name="회원", status="active", address="가 1번지"); db.add(m); db.commit(); mid = m.id
    finally:
        db.close()
    r = TestClient(app, raise_server_exceptions=False).put(f"/api/members/{mid}", json={"address": "나 2번지"})
    assert r.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(models.ChangeHistory).filter(models.ChangeHistory.member_id == mid).count() == 1
    finally:
        db.close()
