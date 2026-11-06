"""DB 접근 없는 합성 데이터로 운영 정정 API의 검증·보호 검증."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app import models, certificate_ledger_models as clm
from app.services import safe_corrections as sc


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
        s.rollback()
    engine.dispose()


def seed_transfer(db, old='양26-100.', expected='양26-100', blocker=False):
    m = models.LicenseHolder(id=2, name='가상 양수자', vehicle_number='강원82자1111',
                             category='개인', management_number=old, status='active',
                             registration_type='신규', transfer_ledger_id=20, candidate_id=30)
    t = models.TransferLedger(id=20, management_number=old, member_id=2, transferee_member_id=2,
                              transferee='가상 양수자', vehicle_number='강원82자1111')
    c = models.Candidate(id=30, management_number=old, member_id=2, is_registered=True,
                         name='가상 양수자', vehicle_number='강원82자1111')
    db.add_all([m,t,c])
    if blocker:
        db.add(models.LicenseHolder(id=1, name='과거 번호점유자', management_number=expected,
                                    status='closed', category='개인', vehicle_number='강원82자0000'))
    db.commit()
    return m,t,c


def seed_cert(db, status='issued'):
    db.add(models.LicenseHolder(id=7, name='가상 발급자', vehicle_number='강원82자7777',
                                certificate_number='26-85', status='active'))
    db.add(clm.CertificateIssuanceLedger(id=8, document_number='26-085', name='가상 발급자',
                                        vehicle_number='강원82자7777', status='발급완료'))
    db.add(models.CertificateNumberLog(id=9, certificate_number='26-85', year=26, number=85,
                                       status=status))
    db.commit()


def test_closed_number_holder_blocks_without_write(db):
    seed_transfer(db, blocker=True)
    p=sc.preview_transfer_correction(db, 2, '양26-100')
    assert not p['allowed'] and p['related']['conflicting_members'][0]['status']=='closed'
    assert db.get(models.LicenseHolder,2).management_number=='양26-100.'
    assert db.query(models.MemberEditLog).count()==0


def test_transfer_duplicate_candidate_blocks(db):
    seed_transfer(db)
    db.add(models.Candidate(id=99, management_number='양26-100', name='다른 예정자'))
    db.commit()
    assert not sc.preview_transfer_correction(db,2,'양26-100')['allowed']


def test_transfer_preview_safe_and_commit_guard(db,monkeypatch):
    seed_transfer(db)
    p=sc.preview_transfer_correction(db,2,'양26-100')
    assert p['allowed'] and p['related']['transfer_ledger_id']==20
    monkeypatch.delenv('ENABLE_VERIFIED_DATA_CORRECTIONS', raising=False)
    with pytest.raises(ValueError,match='비활성화'):
        sc.apply_transfer_correction(db,2,'양26-100','양26-100.','수정사유 가상 검증 목적','tester')
    assert db.get(models.LicenseHolder,2).management_number=='양26-100.'
    monkeypatch.setenv('ENABLE_VERIFIED_DATA_CORRECTIONS','I_VERIFIED_BACKUP')
    with pytest.raises(ValueError, match='변경됐습니다'):
        sc.apply_transfer_correction(db,2,'양26-100','old','수정사유 가상 검증 목적','tester')
    result=sc.apply_transfer_correction(db,2,'양26-100','양26-100.','수정사유 가상 검증 목적','tester')
    assert result['changed']==4
    assert db.get(models.LicenseHolder,2).management_number=='양26-100'
    assert db.get(models.LicenseHolder,2).registration_type=='양도양수'
    assert db.get(models.Candidate,30).management_number=='양26-100'
    assert db.get(models.TransferLedger,20).management_number=='양26-100'
    assert db.query(models.MemberEditLog).count()==4


def test_certificate_preview_blocks_other_identity(db):
    seed_cert(db)
    row=db.get(clm.CertificateIssuanceLedger,8)
    row.name='다른 사람'
    db.commit()
    assert not sc.preview_certificate_relink(db,8,'member',7)['allowed']


def test_certificate_cancelled_blocks(db):
    seed_cert(db,status='cancelled')
    assert not sc.preview_certificate_relink(db,8,'member',7)['allowed']


def test_certificate_conflict_blocks(db):
    seed_cert(db)
    db.add(clm.CertificateIssuanceLedger(id=10,document_number='26-85',name='가상 발급자',status='발급완료'))
    db.commit()
    assert not sc.preview_certificate_relink(db,8,'member',7)['allowed']


def test_certificate_relink_guard_and_status_preservation(db,monkeypatch):
    seed_cert(db)
    assert sc.preview_certificate_relink(db,8,'member',7)['allowed']
    monkeypatch.delenv('ENABLE_VERIFIED_DATA_CORRECTIONS',raising=False)
    with pytest.raises(ValueError,match='비활성화'):
        sc.apply_certificate_relink(db,8,'member',7,'테스트 시나리오 정정 신청','tester')
    monkeypatch.setenv('ENABLE_VERIFIED_DATA_CORRECTIONS','I_VERIFIED_BACKUP')
    result=sc.apply_certificate_relink(db,8,'member',7,'테스트 시나리오 정정 신청','tester')
    assert result['member_id']==7
    row=db.get(clm.CertificateIssuanceLedger,8)
    assert row.document_number=='26-085' and row.status=='발급완료'
    assert db.query(clm.CertificateIssuanceHistory).count()==1
    assert db.get(models.CertificateNumberLog,9).status=='used'
    assert db.get(models.CertificateNumberLog,9).linked_table=='license_holders'


def test_certificate_candidate_preview_read_only(db):
    seed_cert(db)
    before=db.get(clm.CertificateIssuanceLedger,8).member_id
    p=sc.certificate_relink_candidates(db,limit=10)
    assert p['total']==1 and p['items'][0]['result']=='연결 가능'
    assert db.get(clm.CertificateIssuanceLedger,8).member_id==before
    assert db.query(clm.CertificateIssuanceHistory).count()==0


def test_pending_candidate_without_member_is_not_error(db):
    db.add(models.Candidate(id=70,name='가상미인가',vehicle_number='강원83배0070',
                            certificate_number='26-70',is_registered=False))
    db.add(clm.CertificateIssuanceLedger(id=71,candidate_id=70,name='가상미인가',
                                        document_number='26-70',status='인가대기'))
    db.commit()
    assert sc.certificate_relink_candidates(db)['total']==0


def test_certificate_used_log_other_owner_blocks(db):
    seed_cert(db)
    row=db.get(models.CertificateNumberLog,9)
    row.status='used';row.linked_table='license_holders';row.linked_id=999
    db.commit()
    p=sc.preview_certificate_relink(db,8,'member',7)
    assert not p['allowed'] and '다른 대상자' in ' '.join(p['reasons'])


def test_transfer_collision_does_not_change_log_or_ledger(db,monkeypatch):
    seed_transfer(db,blocker=True)
    monkeypatch.setenv('ENABLE_VERIFIED_DATA_CORRECTIONS','I_VERIFIED_BACKUP')
    with pytest.raises(ValueError,match='차단'):
        sc.apply_transfer_correction(db,2,'양26-100','양26-100.','기록 보존을 위한 확인 테스트','tester')
    assert db.get(models.TransferLedger,20).management_number=='양26-100.'
    assert db.get(models.Candidate,30).management_number=='양26-100.'
    assert db.query(models.MemberEditLog).count()==0


def test_certificate_duplicate_target_ledger_blocks(db):
    seed_cert(db)
    db.add(clm.CertificateIssuanceLedger(id=12,document_number='26-86',name='가상 발급자',member_id=7,
                                        status='발급완료'))
    db.commit()
    assert not sc.preview_certificate_relink(db,8,'member',7)['allowed']
