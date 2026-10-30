"""택배회원의 대리인 입력 차단 및 개인정보 이력 응답 테스트."""
import os
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_category_agent_guard.db")
from fastapi.testclient import TestClient
from app import models
from app.auth import get_current_user
from app.database import Base, engine, SessionLocal
from app.main import app


def test_taekbae_cannot_set_agent_via_member_api():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    member = models.LicenseHolder(status="active", category="택배", name="테스트", vehicle_number="강원80배1234")
    db.add(member); db.commit(); mid = member.id; db.close()
    app.dependency_overrides[get_current_user] = lambda: models.User(id=100, username="test", role="admin")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        r = client.put(f"/api/members/{mid}", json={"agent_name": "대리인", "agent_resident_number": "8001011234567"})
        assert r.status_code == 400
        r = client.get(f"/api/members/{mid}/agents")
        assert r.status_code == 200 and r.json()["items"] == []
    finally:
        app.dependency_overrides.clear()
