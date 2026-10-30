"""대리인 이력 / 차주·대리인 구분 / 양도양수 연동 / 변경이력 판정 / 마스킹 — 임시 sqlite."""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_agent_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi.testclient import TestClient
from app import models
from app.auth import get_current_user, require_admin
from app.database import Base, engine, SessionLocal
from app.main import app
from app.services import agent_history as ah
from app.services.change_judgement import classify_plate, classify_text, view_row


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


def hist(db, mid):
    return db.query(models.ChangeHistory).filter(models.ChangeHistory.member_id == mid).all()


def test_vehicle_type_change_is_one_replacement_even_same_model(client):
    db = SessionLocal(); m = mk(db, name="가", vehicle_number="강원80자1111", vehicle_type="18.포터II")
    assert client.put(f"/api/members/{m.id}", json={"vehicle_type": "26.포터II"}).status_code == 200
    rows = [h for h in hist(db, m.id) if h.change_type == "대폐차"]
    assert len(rows) == 1 and rows[0].before_value == "18.포터II" and rows[0].after_value == "26.포터II"
    assert view_row(rows[0]).bucket == "차량변경(대폐차)" and view_row(rows[0]).judgement == "정상변경"
    client.put(f"/api/members/{m.id}", json={"vehicle_type": "26.포터II"})            # 같은 값 재저장
    assert len([h for h in hist(db, m.id) if h.change_type == "대폐차"]) == 1
    client.put(f"/api/members/{m.id}", json={"vehicle_type": "26. 포터II"})           # 공백만 다름 → 실적 제외
    assert len([h for h in hist(db, m.id) if h.change_type == "대폐차" and h.judgement == "정상변경"]) == 1
    assert classify_text("15.무쏘", "14.로드엑스터")[0] == "정상변경"


def test_plate_first_registration_vs_change(client):
    for b in ("강원 자", "강원 바", "택배", "-", ""):
        assert classify_plate(b, "강원80자 1234호")[0] == "최초등록"
    assert classify_plate("강운80자 1234호", "강원80자 1234호")[0] == "오류정정"
    db = SessionLocal(); m = mk(db, name="나", vehicle_number="강원 자")
    client.put(f"/api/members/{m.id}", json={"vehicle_number": "강원80자 1234호"})
    h = hist(db, m.id)[0]
    assert h.change_type == "최초번호등록" and view_row(h).judgement != "정상변경"


def test_agent_history_preserved_and_not_counted_as_change(client):
    db = SessionLocal(); m = mk(db, name="차주", vehicle_number="강원80자2222", agent_name="김철수", agent_mobile="010-1")
    r = client.put(f"/api/members/{m.id}", json={"agent_name": "이영희", "agent_resident_number": "800101-1234567",
                                               "agent_start_date": "2025.03.01", "agent_driver_license_number": "11-22-333333-44"})
    assert r.status_code == 200
    r = client.put(f"/api/members/{m.id}", json={"agent_name": "박민수", "agent_start_date": "2026.10.01"})
    items = client.get(f"/api/members/{m.id}/agents").json()["items"]
    assert [i["name"] for i in items] == ["박민수", "이영희", "김철수"]
    assert [i["status"] for i in items] == ["현재", "종료", "종료"]
    legacy = items[-1]; assert legacy["start_date"] == ""                    # 시작일 임의 생성 금지
    assert any(i.get("resident_number") == "800101-1234567" for i in items) and "333333" not in str(items)   # 주민번호 전체 표시, 면허번호는 마스킹 유지
    assert [h for h in hist(db, m.id) if h.change_type in ("대폐차", "상호변경", "차량번호변경")] == []
    assert client.get(f"/api/members/{m.id}").json()["agent_name"] == "박민수"
    # 같은 값 재저장 → 이력 늘지 않음
    client.put(f"/api/members/{m.id}", json={"agent_name": "박민수"})
    assert len(client.get(f"/api/members/{m.id}/agents").json()["items"]) == 3


def test_owner_vs_legacy_license_kept(client):
    db = SessionLocal(); m = mk(db, name="다", vehicle_number="강원80자3333", driver_license_number="OLD-1")
    client.put(f"/api/members/{m.id}", json={"owner_driver_license_number": "NEW-OWNER"})
    d = client.get(f"/api/members/{m.id}").json()
    assert d["driver_license_number"] == "OLD-1" and d["owner_driver_license_number"] == "NEW-OWNER"


def test_domestic_transfer_links_agent_and_masks_lists(client):
    db = SessionLocal(); tor = mk(db, name="양도자", vehicle_number="강원80자4444", management_number="신20-1")
    body = {"transferor_member_id": tor.id, "closure_date": "26.10.30", "transferee_name": "양수자",
            "transferee_resident_number": "900101-1234567", "transferee_vehicle_number": "강원80자4444",
            "agent_name": "대리인A", "agent_resident_number": "700101-2345678", "agent_start_date": "26.10.30",
            "driver_license_number": "DL-9"}
    r = client.post("/api/transfer-ledger/domestic", json=body)
    assert r.status_code == 200, r.text
    tee = db.query(models.LicenseHolder).get(r.json()["transferee_member_id"])
    db.refresh(tee)
    assert tee.agent_name == "대리인A" and tee.owner_driver_license_number == "DL-9"
    ag = client.get(f"/api/members/{tee.id}/agents").json()["items"]
    assert len(ag) == 1 and ag[0]["status"] == "현재"
    tl = client.get("/api/transfer-ledger").json()["items"]
    assert len(tl) == 1 and tl[0]["resident_number"] == "900101-1234567" and tl[0]["agent_resident_number"] == "700101-2345678"   # 화면 목록: 주민번호 전체 표시
    assert client.get(f"/api/transfer-ledger/{tl[0]['id']}").json()["resident_number"] == "900101-1234567"  # 수정 폼용 원본
    # 일반 대장 수정(대리인 칸 비움)이 현재 대리인을 종료시키지 않는다
    client.put(f"/api/transfer-ledger/{tl[0]['id']}", json={"memo": "x", "agent_name": ""})
    assert client.get(f"/api/members/{tee.id}/agents").json()["items"][0]["status"] == "현재"


def test_backfill_idempotent(client):
    db = SessionLocal(); mk(db, name="라", vehicle_number="강원80자5555", agent_name="구대리")
    assert client.post("/api/members/agent-history/backfill").json()["created"] == 1
    assert client.post("/api/members/agent-history/backfill").json()["created"] == 0


def test_agent_history_api_masks_resident_number_even_if_stored_in_plaintext():
    from app.services.agent_history import mask_agent_rrn
    assert mask_agent_rrn("800101-1234567") == "800101-*******"
    assert mask_agent_rrn("8001011234567") == "800101-*******"
