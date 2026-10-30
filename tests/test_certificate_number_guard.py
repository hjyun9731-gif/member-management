"""자격증명발급번호 중복 방지 통합 테스트 (SQLite + TestClient).

- 026-085 / 26-85 같은 표기 차이도 같은 번호로 막는다.
- 개인/택배회원, 예정자, 양도양수, 발급대장 번호수정 모두 서버에서 409로 차단한다.
- 본인 수정(번호 유지)과 전환/연결된 기록은 막지 않는다.
- 기존 중복은 목록 조회만 하고 데이터를 바꾸지 않는다.
"""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_cert_guard.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import models
from app import certificate_ledger_models as LM
from app.auth import get_current_user, require_admin
from app.database import Base, engine, SessionLocal, get_db
from app.routers import certificate_ledger as CL, candidates as CA, members as ME, transfer_ledger as TL, license_holders as LH, closures as CLO


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    s.add(models.Candidate(id=1, name="홍길동", vehicle_number="강원80자1234", region="춘천", certificate_number="26-425"))
    s.add(models.LicenseHolder(id=1, name="박영수", vehicle_number="강원80배2222", region="강릉", certificate_number="26-100", management_number="신26-1"))
    s.add(models.LicenseHolder(id=2, name="최민수", vehicle_number="강원80자3333", region="원주", certificate_number="26-200", management_number="신26-2"))
    s.commit(); s.close()
    app = FastAPI()
    app.include_router(CL.router)
    app.include_router(CA.router, prefix="/api/candidates")
    app.include_router(ME.router, prefix="/api/members")
    app.include_router(TL.router, prefix="/api/transfer-ledger")
    app.include_router(LH.router, prefix="/api/license-holders")
    app.include_router(CLO.router, prefix="/api/closures")
    def _db():
        d = SessionLocal()
        try: yield d
        finally: d.close()
    user = lambda: type("U", (), {"username": "t", "role": "admin", "id": 1, "full_name": "테스터"})()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[require_admin] = user
    return TestClient(app)


def _msg(r):
    return r.json().get("detail", "")


def test_check_endpoint_detects_duplicate_and_shows_target(client):
    r = client.get("/api/certificate-ledger/number-check", params={"number": "26-425"})
    j = r.json()
    assert r.status_code == 200 and j["duplicate"] is True
    assert "이미 등록된 자격증명발급번호입니다" in j["message"]
    assert "홍길동" in j["message"] and j["matches"][0]["source_label"] == "예정자"


@pytest.mark.parametrize("variant", ["26-425", "26-0425", "2026-425", "26 - 425", "26-425(홍길동)"])
def test_notation_variants_are_same_number(client, variant):
    assert client.get("/api/certificate-ledger/number-check", params={"number": variant}).json()["duplicate"] is True


def test_leading_zero_equivalence(client):
    assert client.get("/api/certificate-ledger/number-check", params={"number": "26-085"}).json()["duplicate"] is False
    r = client.post("/api/candidates", json={"name": "가", "vehicle_number": "강원80자0001", "certificate_number": "26-085"})
    assert r.status_code == 200, r.text
    assert client.get("/api/certificate-ledger/number-check", params={"number": "26-85"}).json()["duplicate"] is True
    assert client.post("/api/candidates", json={"name": "나", "vehicle_number": "강원80자0002", "certificate_number": "26-85"}).status_code == 409


def test_candidate_create_blocked(client):
    r = client.post("/api/candidates", json={"name": "신규", "vehicle_number": "강원80자9999", "certificate_number": "26-425"})
    assert r.status_code == 409 and "이미 등록된 자격증명발급번호입니다" in _msg(r)


def test_member_create_blocked_personal_and_courier(client):
    for veh in ("강원80자7777", "강원80배7777"):
        r = client.post("/api/members", json={"name": "신규", "vehicle_number": veh, "certificate_number": "26-0100"})
        assert r.status_code == 409, r.text


def test_member_update_other_number_blocked_own_number_ok(client):
    assert client.put("/api/members/2", json={"certificate_number": "26-100"}).status_code in (400, 409)
    assert client.put("/api/members/2", json={"certificate_number": "26-200", "memo": "x"}).status_code == 200


def test_license_holder_paths_blocked(client):
    assert client.post("/api/license-holders", json={"name": "z", "vehicle_number": "강원80자5555", "certificate_number": "26-200"}).status_code == 409
    assert client.put("/api/license-holders/1", json={"certificate_number": "26-200"}).status_code == 409


def test_transfer_create_and_update_blocked(client):
    r = client.post("/api/transfer-ledger", json={"transferee": "양수", "vehicle_number": "강원80자4444", "certificate_number": "26-425"})
    assert r.status_code == 409
    ok = client.post("/api/transfer-ledger", json={"transferee": "양수", "vehicle_number": "강원80자4444", "certificate_number": "26-777"})
    assert ok.status_code == 200, ok.text
    tid = ok.json()["id"]
    assert client.put(f"/api/transfer-ledger/{tid}", json={"certificate_number": "26-100"}).status_code in (400, 409)


def test_ledger_number_edit_blocked(client):
    s = SessionLocal()
    row = LM.CertificateIssuanceLedger(name="양", vehicle_number="강원80자1111", document_number="26-300", status="인가대기")
    s.add(row); s.commit(); lid = row.id; s.close()
    r = client.put(f"/api/certificate-ledger/{lid}", json={"document_number": "26-0425"})
    assert r.status_code == 409 and "이미 등록된 자격증명발급번호입니다" in _msg(r)
    assert client.put(f"/api/certificate-ledger/{lid}", json={"document_number": "26-301"}).status_code == 200


def test_same_person_linked_records_are_not_duplicates(client):
    # 예정자 홍길동(26-425)이 회원으로 전환되어도(candidate_id 연결) 같은 사람 번호는 허용
    r = client.post("/api/members", json={"name": "홍길동", "vehicle_number": "강원80자1234", "certificate_number": "26-425", "candidate_id": 1})
    assert r.status_code == 200, r.text


def test_existing_duplicates_reported_not_modified(client):
    s = SessionLocal()
    s.add(models.Candidate(id=50, name="다른사람", vehicle_number="강원80자5050", certificate_number="26-100"))
    s.commit(); s.close()
    r = client.get("/api/certificate-ledger/number-duplicates")
    assert r.status_code == 200
    assert any(i["number"] == "26-100" for i in r.json()["items"])
    s = SessionLocal()
    assert s.query(models.Candidate).filter(models.Candidate.id == 50).one().certificate_number == "26-100"
    assert s.query(models.LicenseHolder).filter(models.LicenseHolder.id == 1).one().certificate_number == "26-100"
    s.close()


def test_closure_create_blocked_and_same_person_allowed(client):
    # 생판 남의 번호(26-100, license_holders id=1 박영수)를 그대로 쓰면 막힌다.
    r = client.post("/api/closures", json={"name": "엉뚱한사람", "vehicle_number": "강원99자9999",
                                            "closure_type": "폐업", "certificate_number": "26-100"})
    assert r.status_code == 409 and "이미 등록된" in _msg(r)
    # 박영수 본인의 폐업 등록(성명+차량번호가 같음)은 막지 않는다 — 번호를 그대로 옮겨 적는 정상 업무.
    r = client.post("/api/closures", json={"name": "박영수", "vehicle_number": "강원80배2222",
                                            "closure_type": "폐업", "certificate_number": "26-100"})
    assert r.status_code == 200


def test_closure_update_other_number_blocked_own_number_ok(client):
    r = client.post("/api/closures", json={"name": "김신규", "vehicle_number": "강원70자7777",
                                            "closure_type": "폐업", "certificate_number": "26-700"})
    assert r.status_code == 200
    cid = r.json()["id"]
    # 본인 번호 그대로 재저장은 허용
    r = client.put(f"/api/closures/{cid}", json={"certificate_number": "26-700"})
    assert r.status_code == 200
    # 다른 사람(26-200, 최민수)의 번호로 바꾸면 차단
    r = client.put(f"/api/closures/{cid}", json={"certificate_number": "26-200"})
    assert r.status_code == 409 and "이미 등록된" in _msg(r)


def test_ledger_list_fast_path_pagination(client):
    # 900번대를 써서 fixture의 예정자(26-425)보다 항상 숫자가 크게 만든다.
    # 목록 정렬은 id가 아니라 숫자 내림차순이 의도된 동작이므로(26-370이 26-369보다 항상 먼저),
    # 번호 폭이 겹치면 정렬 순서가 fixture 데이터에 영향을 받아 테스트가 깨진다.
    s = SessionLocal()
    for n in range(1, 8):
        s.add(LM.CertificateIssuanceLedger(name=f"n{n}", vehicle_number=f"v{n}", document_number=f"26-{900 + n}", status="인가대기"))
    s.commit(); s.close()
    j = client.get("/api/certificate-ledger", params={"limit": 3, "page": 1}).json()
    assert j["total"] >= 7 and [i["document_number"] for i in j["items"]][0] == "26-907"
    assert len(j["items"]) == 3
    j2 = client.get("/api/certificate-ledger", params={"limit": 3, "page": 2}).json()
    assert [i["document_number"] for i in j2["items"]][0] != "26-907"
