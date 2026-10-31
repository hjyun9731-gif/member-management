"""대리인 자격증명발급번호(agent_certificate_number) 입력 → 저장 → 재조회 회귀 테스트 — 임시 sqlite.

- 대리인 화물운송종사자격증 번호 / 차주 자격증명번호와는 별개 필드여야 한다.
- 대리인 교체 시 이전 대리인의 번호는 이력에 보존된다.
- 구버전 스키마(컬럼 없음)에서도 보강 후 기존 데이터가 유지된다.
"""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_agent_cert_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from app import models
from app.auth import get_current_user, require_admin
from app.database import Base, engine, SessionLocal
from app.main import app
from app.services import schema_guard


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    u = models.User(id=1, username="t", password_hash="x", role="admin", full_name="t")
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[require_admin] = lambda: u
    yield TestClient(app)
    app.dependency_overrides.clear()


def mk(db, **kw):
    m = models.LicenseHolder(status="active", category="개인", region="춘천시", **kw)
    db.add(m); db.commit(); db.refresh(m); return m


def test_member_edit_saves_and_rereads_agent_certificate_number(client):
    db = SessionLocal(); m = mk(db, name="차주", vehicle_number="강원80자3001", certificate_number="OWNER-1")
    r = client.put(f"/api/members/{m.id}", json={
        "agent_name": "김대리", "agent_freight_qualification_number": "FQ-111",
        "agent_certificate_number": "AGENT-CERT-9"})
    assert r.status_code == 200
    got = client.get(f"/api/members/{m.id}").json()                       # 다시 조회(수정창 재오픈과 동일 경로)
    assert got["agent_certificate_number"] == "AGENT-CERT-9"
    assert got["agent_freight_qualification_number"] != "AGENT-CERT-9"    # 서로 다른 필드
    assert got["certificate_number"] == "OWNER-1"                         # 차주 번호는 그대로
    items = client.get(f"/api/members/{m.id}/agents").json()["items"]
    assert [i["certificate_number"] for i in items if i["status"] == "현재"] == ["AGENT-CERT-9"]
    db.close()


def test_agent_replacement_keeps_previous_certificate_in_history(client):
    db = SessionLocal(); m = mk(db, name="차주", vehicle_number="강원80자3002")
    client.put(f"/api/members/{m.id}", json={"agent_name": "김대리", "agent_certificate_number": "A-1"})
    client.put(f"/api/members/{m.id}", json={"agent_name": "이대리", "agent_certificate_number": "B-2"})
    items = client.get(f"/api/members/{m.id}/agents").json()["items"]
    by_name = {i["name"]: i for i in items}
    assert by_name["이대리"]["status"] == "현재" and by_name["이대리"]["certificate_number"] == "B-2"
    assert by_name["김대리"]["status"] == "종료" and by_name["김대리"]["certificate_number"] == "A-1"
    assert client.get(f"/api/members/{m.id}").json()["agent_certificate_number"] == "B-2"
    # 대리인 변경은 월례보고 실적(ChangeHistory)에 기록하지 않는다
    assert db.query(models.ChangeHistory).filter(models.ChangeHistory.member_id == m.id,
                                                 models.ChangeHistory.change_type == "대리인").count() == 0
    db.close()


def test_existing_agent_without_certificate_is_not_damaged(client):
    db = SessionLocal(); m = mk(db, name="차주", vehicle_number="강원80자3003", agent_name="박기존", agent_mobile="010-1")
    r = client.put(f"/api/members/{m.id}", json={"agent_name": "박기존", "agent_certificate_number": "NEW-7"})
    assert r.status_code == 200
    got = client.get(f"/api/members/{m.id}").json()
    assert got["agent_name"] == "박기존" and got["agent_mobile"] == "010-1" and got["agent_certificate_number"] == "NEW-7"
    assert len(client.get(f"/api/members/{m.id}/agents").json()["items"]) == 1   # 같은 대리인 = 이력 1건 유지
    db.close()


def test_transfer_registration_saves_agent_certificate_and_links_to_member(client):
    tl = {"receipt_date": "2026-10-01", "region": "춘천시", "vehicle_number": "강원80자3004", "transferee": "양수자",
          "agent_name": "정대리", "agent_freight_qualification_number": "FQ-222",
          "agent_certificate_number": "TRF-CERT-5", "agent_start_date": "2026.10.01"}
    rec = client.post("/api/transfer-ledger", json=tl)
    assert rec.status_code == 200, rec.text
    tid = rec.json()["id"]
    assert rec.json()["agent_certificate_number"] == "TRF-CERT-5"
    assert client.get(f"/api/transfer-ledger/{tid}").json()["agent_certificate_number"] == "TRF-CERT-5"
    reg = client.post(f"/api/transfer-ledger/{tid}/register-member", json={"management_number": "TEST-9001"})
    assert reg.status_code == 200, reg.text
    mid = reg.json()["member_id"]
    got = client.get(f"/api/members/{mid}").json()
    assert got["agent_certificate_number"] == "TRF-CERT-5" and got["agent_freight_qualification_number"] == "FQ-222"
    cur = [i for i in client.get(f"/api/members/{mid}/agents").json()["items"] if i["status"] == "현재"]
    assert len(cur) == 1 and cur[0]["certificate_number"] == "TRF-CERT-5"


def test_old_schema_gets_columns_added_without_data_loss(client):
    db = SessionLocal(); m = mk(db, name="차주", vehicle_number="강원80자3005", agent_name="구대리"); mid = m.id; db.close()
    with engine.begin() as c:
        c.execute(text("ALTER TABLE license_holders DROP COLUMN agent_certificate_number"))
        c.execute(text("ALTER TABLE transfer_ledger DROP COLUMN agent_certificate_number"))
        c.execute(text("ALTER TABLE member_agents DROP COLUMN certificate_number"))
    schema_guard.reset()
    try:
        assert schema_guard.ensure_columns(engine) is True
        assert schema_guard.ensure_columns(engine) is True                    # 멱등
        insp = inspect(engine)
        assert "agent_certificate_number" in {c["name"] for c in insp.get_columns("license_holders")}
        assert "agent_certificate_number" in {c["name"] for c in insp.get_columns("transfer_ledger")}
        assert "certificate_number" in {c["name"] for c in insp.get_columns("member_agents")}
        db = SessionLocal()
        assert db.query(models.LicenseHolder).filter_by(id=mid).one().agent_name == "구대리"
        db.close()
    finally:
        schema_guard.reset()
