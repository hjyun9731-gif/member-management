"""발급대장 '다른 예정자/회원으로 재연결' 검색 회귀 테스트.

버그: GET /api/certificate-ledger/link-targets 가 GET /{ledger_id} 에 먼저 매칭되어 422(정수 변환 실패)가 났고,
화면은 오류 배열을 글자로 바꿔 '[object Object]' 와 '검색 중 오류가 발생했습니다.' 를 표시했다.
"""
import os, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_link_targets.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import models
from app.auth import get_current_user
from app.database import Base, engine, SessionLocal, get_db
from app.routers import certificate_ledger as CL


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    s.add(models.Candidate(id=1, name="김태은", vehicle_number="강원81배2068", region="원주"))
    s.add(models.LicenseHolder(id=1, name="김태은", vehicle_number="강원80배1111", region="춘천", certificate_number="26-100"))
    s.add(models.LicenseHolder(id=2, name="박영수", vehicle_number="강원80배2222", region="강릉", certificate_number="26-385(김태은)"))
    s.commit(); s.close()
    app = FastAPI(); app.include_router(CL.router)
    def _db():
        d = SessionLocal()
        try: yield d
        finally: d.close()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"username": "t", "role": "staff", "id": 1})()
    return TestClient(app)


def test_link_targets_search_by_name(client):
    r = client.get("/api/certificate-ledger/link-targets", params={"q": "김태은"})
    assert r.status_code == 200, r.text                     # 수정 전에는 422
    items = r.json()["items"]
    assert {(i["type"], i["id"]) for i in items} == {("candidate", 1), ("member", 1), ("member", 2)}   # 발급번호 안의 이름도 검색


def test_link_targets_search_by_vehicle_and_empty(client):
    assert client.get("/api/certificate-ledger/link-targets", params={"q": "2068"}).json()["items"][0]["vehicle_number"] == "강원81배2068"
    assert client.get("/api/certificate-ledger/link-targets", params={"q": "없는이름"}).json()["items"] == []


def test_numeric_ledger_routes_still_work(client):
    assert client.get("/api/certificate-ledger/999999").status_code in (404, 200)    # 숫자 ID 경로는 그대로 동작
    assert client.get("/api/certificate-ledger/abc").status_code == 404              # 문자는 더 이상 {ledger_id}에 걸리지 않음


def test_static_get_paths_never_shadowed_by_ledger_id():
    """같은 오류가 다른 정적 경로에서 재발하지 않도록: /{ledger_id} 계열은 정수 경로 변환기를 써야 한다."""
    import re, inspect
    src = inspect.getsource(CL)
    for m in re.finditer(r'@router\.(?:get|post|put|patch|delete)\("(/\{ledger_id[^"]*)"', src):
        assert "{ledger_id:int}" in m.group(1), m.group(1)
