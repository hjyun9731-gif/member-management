"""정합성 진단·관리번호 형식·자격증명 동기화 보존 회귀 테스트.

실제 Railway DB와 회원 개인정보를 사용하지 않는다. 각 테스트는 독립 SQLite 메모리 DB.
"""
import pytest
from db_support import create_test_engine as create_engine
from sqlalchemy.orm import Session

from app import crud, models
from app import certificate_ledger_models as clm
from app.database import Base
from app.services import data_integrity as di
from app.services.certificate_ledger_service import ISSUED, ensure_member_ledger


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
        session.rollback()
    engine.dispose()


def _member(mid, cert="26-85", **kwargs):
    return models.LicenseHolder(id=mid, name=f"가상{mid}", category="개인",
                                vehicle_number=f"강원80바{mid:04d}",
                                certificate_number=cert, status="active", **kwargs)


@pytest.mark.parametrize("value", ["양26-100.", "양26-100..", "양26-100,", "양26-100#", "신26-12.."])
def test_reject_fake_suffix_management_numbers(value):
    with pytest.raises(ValueError):
        crud.validate_management_number_format(value)


@pytest.mark.parametrize("value", ["양26-100", "신13-12", "폐26-33", ""])
def test_existing_standard_formats_stay_valid(value):
    crud.validate_management_number_format(value)


def test_candidate_transfer_number_creates_transfer_type(db):
    db.add(models.Candidate(id=1, name="가상예정자", vehicle_number="강원80바1999",
                            management_number="양26-105", is_registered=False))
    db.commit()
    member = crud.register_candidate_as_member(db, 1, "2026-11-05", "양26-105")
    assert member.registration_type == "양도양수"
    assert member.transfer_ledger_id is not None
    ledger = db.get(models.TransferLedger, member.transfer_ledger_id)
    assert ledger.management_number == "양26-105"
    assert ledger.transferee_member_id == member.id


def test_certificate_another_owner_never_overwritten(db):
    db.add_all([_member(1), _member(2)])
    db.add(clm.CertificateIssuanceLedger(document_number="26-85", name="가상1",
                                        member_id=1, status=ISSUED))
    db.commit()
    with pytest.raises(ValueError, match="다른 회원"):
        ensure_member_ledger(db, db.get(models.LicenseHolder, 2))
    db.rollback()
    row = db.query(clm.CertificateIssuanceLedger).one()
    assert row.member_id == 1 and row.status == ISSUED


def test_certificate_issued_state_preserved_and_no_typeerror(db):
    db.add(_member(1))
    db.add(clm.CertificateIssuanceLedger(document_number="26-85", name="가상1",
                                        member_id=1, status=ISSUED))
    db.commit()
    ensure_member_ledger(db, db.get(models.LicenseHolder, 1))
    ledger = db.query(clm.CertificateIssuanceLedger).one()
    assert ledger.member_id == 1 and ledger.status == ISSUED
    logs = db.query(models.CertificateNumberLog).all()
    assert len(logs) == 1 and logs[0].linked_id == 1


def test_certificate_normalized_duplicate_ledger_blocks_automatic_relink(db):
    db.add(_member(1))
    db.add_all([
        clm.CertificateIssuanceLedger(document_number="26-85", name="가상1", member_id=1, status=ISSUED),
        clm.CertificateIssuanceLedger(document_number="26-085", name="가상1", member_id=1, status=ISSUED),
    ])
    db.commit()
    with pytest.raises(ValueError, match="표준화 번호"):
        ensure_member_ledger(db, db.get(models.LicenseHolder, 1))
    db.rollback()
    assert db.query(clm.CertificateIssuanceLedger).count() == 2


def test_certificate_log_points_to_other_member_blocks_relink(db):
    db.add_all([_member(1), _member(2)])
    db.add(models.CertificateNumberLog(certificate_number="26-85", year=26, number=85,
                                       status="used", linked_table="license_holders", linked_id=2))
    db.commit()
    with pytest.raises(ValueError, match="다른 대상"):
        ensure_member_ledger(db, db.get(models.LicenseHolder, 1))
    db.rollback()
    log = db.query(models.CertificateNumberLog).one()
    assert log.linked_id == 2
    assert db.query(clm.CertificateIssuanceLedger).count() == 0


def test_certificate_canonical_duplicate_log_diagnostic_not_order_dependent(db):
    db.add(_member(1))
    db.add_all([
        models.CertificateNumberLog(certificate_number="26-085", year=26, number=85, status="cancelled"),
        models.CertificateNumberLog(certificate_number="26-85", year=26, number=85, status="used"),
        clm.CertificateIssuanceLedger(document_number="26-85", name="가상1", member_id=1, status=ISSUED),
    ])
    db.commit()
    res = di.run_scan(db)
    assert any(f["table"] == "certificate_number_logs" and f["code"] == "DQ-09" for f in res["findings"])
    assert not any(f["table"] == "license_holders" and f["record_id"] == 1 and
                   f["code"] == "DQ-09" and f["severity"] == di.CONFIRMED for f in res["findings"])


def test_ledger_missing_candidate_reference_is_flagged(db):
    db.add(clm.CertificateIssuanceLedger(document_number="26-91", name="가상미확인",
                                        candidate_id=99999, status=ISSUED))
    db.commit()
    res = di.run_scan(db)
    assert any(f["table"] == "certificate_issuance_ledger" and f["code"] == "DQ-15" and
               f["field"] == "candidate_id" for f in res["findings"])
    assert not db.dirty and not db.new


def test_real_calendar_dates_and_legacy_spaces():
    assert di._date_ok("13. 6.11.")
    assert di._date_ok("2024-02-29")
    assert not di._date_ok("2026-02-29")
    assert not di._date_ok("2026-02-31")


def test_csv_formula_values_are_escaped_without_changing_safe_phone():
    from app.services.csv_security import safe_csv_cell
    for raw in ('=1+2', '+1+2', '-1+2', '@SUM(1)', '  =1', '\t=cmd'):
        assert safe_csv_cell(raw).startswith("'"), raw
    assert safe_csv_cell('010-1234-5678') == '010-1234-5678'
    assert safe_csv_cell(None) == ''


def test_certificate_cancelled_number_log_cannot_be_reused(db):
    db.add(_member(1))
    db.add(models.CertificateNumberLog(certificate_number="26-85", year=26, number=85,
                                       status="cancelled"))
    db.commit()
    with pytest.raises(ValueError, match="취소된 발급번호"):
        ensure_member_ledger(db, db.get(models.LicenseHolder, 1))
    db.rollback()
    assert db.query(clm.CertificateIssuanceLedger).count() == 0


def test_certificate_unowned_ledger_wrong_person_is_not_reused(db):
    db.add(_member(1))
    db.add(clm.CertificateIssuanceLedger(document_number="26-85", name="다른 사람",
                                        vehicle_number="강원80바9876", status=ISSUED))
    db.commit()
    with pytest.raises(ValueError, match="성명·차량번호"):
        ensure_member_ledger(db, db.get(models.LicenseHolder, 1))
    db.rollback()
    ledger = db.query(clm.CertificateIssuanceLedger).one()
    assert ledger.member_id is None and ledger.name == "다른 사람"


def test_certificate_candidate_log_may_be_linked_to_its_confirmed_member(db):
    db.add(_member(1, candidate_id=7))
    db.add(models.Candidate(id=7, name="가상1", vehicle_number="강원80바0001",
                            certificate_number="26-85", member_id=1, is_registered=True))
    db.add(clm.CertificateIssuanceLedger(document_number="26-85", name="가상1",
                                        vehicle_number="강원80바0001", candidate_id=7, status=ISSUED))
    db.add(models.CertificateNumberLog(certificate_number="26-85", year=26, number=85,
                                       status="used", linked_table="candidates", linked_id=7))
    db.commit()
    ensure_member_ledger(db, db.get(models.LicenseHolder, 1))
    ledger = db.query(clm.CertificateIssuanceLedger).one()
    log = db.query(models.CertificateNumberLog).one()
    assert ledger.member_id == 1 and ledger.candidate_id == 7 and ledger.status == ISSUED
    assert log.linked_table == "license_holders" and log.linked_id == 1
