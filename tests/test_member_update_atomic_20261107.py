"""회원 저장/발급대장 원자성 통합 테스트 (비식별 임시 SQLite)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from db_support import create_test_engine as create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.database import Base, get_db
from app import models, certificate_ledger_models as clm
from app.routers import members


@pytest.fixture
def client_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.include_router(members.router, prefix="/api/members")
    user = type("User", (), {"username": "tester", "full_name": "테스터"})()
    def get_test_db():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: user
    with Session(engine) as s:
        s.add(models.LicenseHolder(id=155, name="가상회원", category="개인", management_number="신26-155",
                                   certificate_number="26-500", vehicle_number="강원81자1555", status="active", address="옛주소"))
        s.add(clm.CertificateIssuanceLedger(member_id=155, document_number="26-500", name="가상회원",
                                           vehicle_number="강원81자1555", status="인가대기"))
        s.commit()
    yield TestClient(app), engine
    engine.dispose()


def test_address_change_does_not_relink_certificate(client_db):
    client, engine = client_db
    r = client.put("/api/members/155", json={"address": "새주소"})
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        assert s.get(models.LicenseHolder, 155).address == "새주소"
        rows = s.query(clm.CertificateIssuanceLedger).all()
        assert len(rows) == 1 and rows[0].document_number == "26-500"
        assert s.query(models.CertificateNumberLog).count() == 0


def test_certificate_change_updates_original_ledger_atomically(client_db):
    client, engine = client_db
    r = client.put("/api/members/155", json={"certificate_number": "26-501"})
    assert r.status_code == 200, r.text
    assert r.json()["certificate_number"] == "26-501"
    with Session(engine) as s:
        assert s.get(models.LicenseHolder, 155).certificate_number == "26-501"
        rows = s.query(clm.CertificateIssuanceLedger).all()
        assert len(rows) == 1 and rows[0].document_number == "26-501" and rows[0].member_id == 155
        new_log = s.query(models.CertificateNumberLog).filter_by(certificate_number="26-501").one()
        assert new_log.status == "used" and new_log.linked_id == 155


def test_sync_failure_rolls_back_whole_member_change(client_db, monkeypatch):
    client, engine = client_db
    def failure(*_args, **_kwargs):
        raise RuntimeError("강제 동기화 실패")
    monkeypatch.setattr(members, "ensure_member_ledger", failure)
    r = client.put("/api/members/155", json={"certificate_number": "26-501", "address": "실패시변경금지"})
    assert r.status_code == 500
    assert "저장 전체를 취소" in r.json()["detail"]
    with Session(engine) as s:
        m = s.get(models.LicenseHolder, 155)
        assert m.certificate_number == "26-500" and m.address == "옛주소"
        rows = s.query(clm.CertificateIssuanceLedger).all()
        assert len(rows) == 1 and rows[0].document_number == "26-500"
        assert s.query(models.CertificateNumberLog).count() == 0
