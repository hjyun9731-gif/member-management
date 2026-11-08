"""합성 데이터로 등록, 대리인, 발급 이력 무결성을 재검증한다 (운영 DB 무접속)."""
import pytest
from fastapi import HTTPException
from db_support import create_test_engine as create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app import models, crud, certificate_ledger_models as clm
from app.services import member_integrity as integrity
from app.services import certificate_number_rules as rules
from app.services import certificate_number_guard as cert_guard
from app.services.agent_phone_link import link_known_current_agent
from app.services.certificate_ledger_service import ensure_member_ledger, ensure_ledger_schema


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_different_names_same_certificate_rejected(db):
    db.add(models.LicenseHolder(id=1, name="원소유자", certificate_number="26-085", status="active"))
    db.commit()
    with pytest.raises(ValueError, match="회원 #1"):
        integrity.assert_registration_safe(db, {"name": "다른이름", "certificate_number": "26-85"})


def test_different_names_same_complete_resident_number_rejected(db):
    db.add(models.LicenseHolder(id=1, name="원소유자", resident_number="800101-1234567", status="active"))
    db.commit()
    with pytest.raises(ValueError, match="회원 #1"):
        integrity.assert_registration_safe(db, {"name": "이름변경", "resident_number": "8001011234567"})


def test_same_name_alone_does_not_block(db):
    db.add(models.LicenseHolder(id=1, name="동명이인", status="active", vehicle_number="강원81자1000"))
    db.commit()
    integrity.assert_registration_safe(db, {"name": "동명이인", "vehicle_number": "강원81자2000"})


def test_agent_phone_shared_with_other_current_agent_not_auto_linked(db):
    own = models.LicenseHolder(id=1, name="본인", category="개인")
    db.add_all([own,
        models.MemberAgent(member_id=1, name="대리인A", mobile="010-1111-2222", status="현재"),
        models.MemberAgent(member_id=2, name="대리인B", mobile="01011112222", status="현재")])
    db.commit()
    assert link_known_current_agent(db, own, "010-1111-2222(대)") == "review_required"
    assert not own.agent_name


def test_two_independent_members_same_name_plate_not_same_identity():
    hit = {"source": "license_holders", "id": 2, "name": "홍길동", "vehicle_number": "강원81자1234"}
    context = rules.SubjectContext(member_ids={1}, name="홍길동", vehicle_number="강원81자1234")
    assert not rules.is_exempt(hit, context)


def test_legacy_closure_same_name_plate_keeps_compatibility():
    hit = {"source": "closures", "id": 2, "name": "홍길동", "vehicle_number": "강원81자1234"}
    context = rules.SubjectContext(member_ids={1}, name="홍길동", vehicle_number="강원81자1234")
    assert rules.is_exempt(hit, context)


def test_guard_failure_fails_closed(db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("시뮬레이션 DB 읽기 오류")
    monkeypatch.setattr(cert_guard, "find_conflicts", broken)
    with pytest.raises(HTTPException) as exc:
        cert_guard.guard_certificate_number(db, "26-400")
    assert exc.value.status_code == 503


def test_certificate_log_sync_without_commit_can_rollback(db):
    crud.sync_certificate_number_usage(db, "26-411", "license_holders", 101, "테스트", "강원81자1111", commit=False)
    assert db.query(models.CertificateNumberLog).filter_by(certificate_number="26-411").count() == 1
    db.rollback()
    assert db.query(models.CertificateNumberLog).filter_by(certificate_number="26-411").count() == 0


def test_member_ledger_sync_without_commit_can_rollback(db):
    m = models.LicenseHolder(id=120, name="테스트", category="개인", certificate_number="26-412", vehicle_number="강원81자1001")
    db.add(m)
    db.flush()
    ensure_ledger_schema(db, read_only=True)
    ensure_member_ledger(db, m, commit=False)
    assert db.query(clm.CertificateIssuanceLedger).filter_by(member_id=120).count() == 1
    db.rollback()
    assert db.query(clm.CertificateIssuanceLedger).filter_by(member_id=120).count() == 0
    assert db.query(models.LicenseHolder).filter_by(id=120).count() == 0


def test_member_ledger_rejects_existing_other_member(db):
    m = models.LicenseHolder(id=120, name="테스트", category="개인", certificate_number="26-413", vehicle_number="강원81자1001")
    existing = clm.CertificateIssuanceLedger(member_id=99, document_number="26-413", name="타인", vehicle_number="강원81자1002", status="인가대기")
    db.add_all([m, existing]); db.commit()
    with pytest.raises(ValueError, match="다른 회원"):
        ensure_member_ledger(db, m, commit=False)
    db.rollback()
    assert db.query(clm.CertificateIssuanceLedger).filter_by(document_number="26-413").first().member_id == 99


def test_certificate_change_updates_existing_ledger_in_one_transaction(db):
    m = models.LicenseHolder(id=130, name="테스트", category="개인", certificate_number="26-501", vehicle_number="강원81자2001")
    old_ledger = clm.CertificateIssuanceLedger(member_id=130, document_number="26-500", name="테스트", status="인가대기")
    old_log = models.CertificateNumberLog(certificate_number="26-500", year=26, number=500, status="used", linked_table="license_holders", linked_id=130)
    db.add_all([m, old_ledger, old_log]); db.flush()
    # 자료를 아직 commit하지 않은 상태에서 두 동기화 함수를 실행한다.
    crud.resync_certificate_number_change(db, "26-500", "26-501", "license_holders", 130,
                                          "테스트", "강원81자2001", commit=False, strict=True)
    ensure_member_ledger(db, m, commit=False)
    assert db.query(clm.CertificateIssuanceLedger).count() == 1
    assert db.query(clm.CertificateIssuanceLedger).first().document_number == "26-501"
    db.rollback()
    assert db.query(clm.CertificateIssuanceLedger).count() == 0


def test_ambiguous_ledger_history_is_blocked_before_overwrite(db):
    m = models.LicenseHolder(id=131, name="테스트", category="개인", certificate_number="26-511", vehicle_number="강원81자2002")
    db.add_all([m,
        clm.CertificateIssuanceLedger(member_id=131, document_number="26-508", name="테스트", status="인가대기"),
        clm.CertificateIssuanceLedger(member_id=131, document_number="26-509", name="테스트", status="인가대기")])
    db.commit()
    with pytest.raises(ValueError, match="여러 건"):
        crud.resync_certificate_number_change(db, "26-510", "26-511", "license_holders", 131,
                                              "테스트", "강원81자2002", commit=False, strict=True)
    db.rollback()
    assert set(r.document_number for r in db.query(clm.CertificateIssuanceLedger).all()) == {"26-508", "26-509"}
