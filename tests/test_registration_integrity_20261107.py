"""운영 DB 비접속 합성 데이터 테스트: 관리번호 중복, 동명이인, 대리인 안전 연결."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import crud, models
from app.services import member_integrity as g
from app.services.agent_phone_link import link_known_current_agent, tagged_numbers


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def test_number_normalization():
    assert g.mgmt_key(' 양26 - 100.  ') == '양26-100'
    assert g.mgmt_key('양26-0100') == '양26-100'
    assert g.mgmt_key('양26-1000') != '양26-100'


def test_member_number_duplicate_and_soft_delete(db):
    m = models.LicenseHolder(id=1,name='가상기존', management_number='양26-100.',status='closed')
    db.add(m); db.commit()
    assert crud.check_mgmt_dup(db,models.LicenseHolder,'양26-100')
    with pytest.raises(ValueError,match='회원 #1'):
        g.assert_registration_safe(db, {'name':'완전다른사람'}, '양26-100')
    from datetime import datetime, timezone
    m.deleted_at = datetime.now(timezone.utc);db.commit()
    assert not crud.check_mgmt_dup(db,models.LicenseHolder,'양26-100')


def test_other_candidate_reserves_management_number(db):
    db.add(models.Candidate(id=15,name='가상예정자', management_number='양26-100.',is_registered=False))
    db.commit()
    with pytest.raises(ValueError,match='예정자 #15'):
        g.assert_registration_safe(db,{'name':'다른사람'},'양26-100')
    assert not g.management_conflicts(db,'양26-100',candidate_id=15)


def test_same_name_only_not_blocked(db):
    db.add(models.LicenseHolder(id=11,name='홍길동',vehicle_number='강원81자1111',certificate_number='26-111',status='active'))
    db.commit()
    assert not g.identity_conflicts(db,{'name':'홍길동','vehicle_number':'강원88자9999','certificate_number':'26-222'})


def test_same_vehicle_different_name_not_blocked_for_transfer(db):
    db.add(models.LicenseHolder(id=11,name='양도자',vehicle_number='강원81자1111',status='closed'))
    db.commit()
    assert not g.identity_conflicts(db,{'name':'양수자','vehicle_number':'강원81자1111'})


def test_same_cert_and_name_blocked(db):
    db.add(models.LicenseHolder(id=11,name='검증 이름',certificate_number='26-085',status='active'))
    db.commit()
    with pytest.raises(ValueError,match='회원 #11'):
        g.assert_registration_safe(db,{'name':'검증이름','certificate_number':'26-85'})


def test_real_id_like_candidate_conversion_keeps_own_candidate(db):
    db.add(models.Candidate(id=247,name='가상검증',vehicle_number='강원82자 2421호',
                            certificate_number='26-441',management_number='양26-100', is_registered=False))
    db.commit()
    g.assert_registration_safe(db,{'name':'가상검증','vehicle_number':'강원82자 2421호',
                                   'certificate_number':'26-441'},'양26-100',candidate_id=247)


def test_two_identities_with_same_phone_not_auto_merge(db):
    db.add_all([models.LicenseHolder(id=1,name='동명이인',mobile='010-1111-2222',vehicle_number='A'),
                models.LicenseHolder(id=2,name='동명이인',mobile='010-1111-2222',vehicle_number='B')])
    db.commit()
    assert not g.identity_conflicts(db,{'name':'동명이인','mobile':'010-1111-2222','vehicle_number':'C'})


def test_marked_agent_phone_identified():
    assert tagged_numbers('010-1111-2222 010-3333-4444(대)') == {'01033334444'}


def test_agent_link_unique_current_same_member(db):
    m=models.LicenseHolder(id=1,name='차주',category='개인',mobile='010-1111-2222 010-3333-4444(대)')
    a=models.MemberAgent(id=7,member_id=1,name='가상대리인',mobile='010-3333-4444',status='현재')
    db.add_all([m,a]);db.commit()
    assert link_known_current_agent(db,m,m.mobile)=='linked'
    assert m.agent_name=='가상대리인'
    assert m.agent_mobile=='010-3333-4444'
    assert link_known_current_agent(db,m,m.mobile)=='already_linked'
    assert m.mobile=='010-1111-2222 010-3333-4444(대)'
    assert db.query(models.MemberAgent).count()==1


def test_agent_collision_cannot_relink(db):
    m=models.LicenseHolder(id=1,name='차주',category='개인',agent_name='다른대리인')
    a=models.MemberAgent(id=7,member_id=1,name='가상대리인',mobile='010-3333-4444',status='현재')
    db.add_all([m,a]);db.commit()
    assert link_known_current_agent(db,m,'010-3333-4444(대)')=='review_required'
    assert m.agent_name=='다른대리인'


def test_agent_different_member_never_links(db):
    m=models.LicenseHolder(id=1,name='차주',category='개인')
    a=models.MemberAgent(id=7,member_id=2,name='타인대리인',mobile='010-3333-4444',status='현재')
    db.add_all([m,a]);db.commit()
    assert link_known_current_agent(db,m,'010-3333-4444(대)')=='review_required'
    assert not m.agent_name


def test_agent_current_duplicates_rejected(db):
    m=models.LicenseHolder(id=1,name='차주',category='개인')
    db.add(m)
    db.add_all([models.MemberAgent(member_id=1,name='대리인1',mobile='010-3333-4444',status='현재'),
                models.MemberAgent(member_id=1,name='대리인2',mobile='010-3333-4444',status='현재')]);db.commit()
    assert link_known_current_agent(db,m,'010-3333-4444(대)')=='review_required'
    assert not m.agent_name


def test_agent_historical_not_relinked(db):
    m=models.LicenseHolder(id=1,name='차주',category='개인')
    db.add_all([m,models.MemberAgent(member_id=1,name='과거대리인',mobile='010-3333-4444',status='종료')]);db.commit()
    assert link_known_current_agent(db,m,'010-3333-4444(대)')=='review_required'
    assert not m.agent_name


def test_member_search_two_phones_hyphenless(db):
    m=models.LicenseHolder(id=40,name='가상검색',mobile='010-3456-1111 010-9876-2222(대)',status='active')
    db.add(m);db.commit()
    for query in ('01034561111','01098762222','98762222'):
        rows,total=crud.get_list(db,models.LicenseHolder,search=query,
                                 search_fields=['name','mobile'],limit=50)
        assert total==1 and rows[0].id==40


def test_member_search_agent_mobile_without_hyphens(db):
    m=models.LicenseHolder(id=50,name='가상검색',mobile='010-1111-2222',
                           agent_name='가상대리인',agent_mobile='010-3333-4444',status='active')
    db.add(m);db.commit()
    rows,total=crud.get_list(db,models.LicenseHolder,search='01033334444',
                             search_fields=['name','mobile','agent_mobile'],limit=50)
    assert total==1 and rows[0].id==50


def test_same_name_plate_active_requires_review(db):
    db.add(models.LicenseHolder(id=45,name='가상홍길동',vehicle_number='강원82자 2421호',status='active'))
    db.commit()
    with pytest.raises(ValueError,match='수동 확인'):
        g.assert_registration_safe(db,{'name':'가상홍길동','vehicle_number':'강원82자2421호'})


def test_candidate_conversion_prevents_second_member(db):
    db.add(models.Candidate(id=301,name='가상전환완료',is_registered=True,member_id=501,
                            vehicle_number='강원82자3010',management_number='양26-301'))
    db.commit()
    with pytest.raises(ValueError, match='이미 등록 처리'):
        crud.register_candidate_as_member(db,301,'2026-11-07','양26-301')
    assert db.query(models.LicenseHolder).count()==0


def test_candidate_conversion_blocks_another_active_member_same_cert(db):
    db.add_all([
        models.Candidate(id=301,name='가상전환',is_registered=False,certificate_number='26-301',
                         vehicle_number='강원82자3010',management_number='양26-301'),
        models.LicenseHolder(id=501,name='가상전환',status='active',certificate_number='26-301',
                             vehicle_number='강원82자3010',management_number='양26-302'),
    ]);db.commit()
    with pytest.raises(ValueError,match='중복등록 위험'):
        crud.register_candidate_as_member(db,301,'2026-11-07','양26-301')
    assert db.query(models.LicenseHolder).count()==1
    assert db.get(models.Candidate,301).is_registered is False


def test_transfer_registration_refuses_existing_link(db):
    db.add(models.TransferLedger(id=331,transferor='과거',transferee='가상',
                                 vehicle_number='강원82자3311',member_id=501,
                                 management_number='양26-331'))
    db.commit()
    with pytest.raises(ValueError,match='이미 회원에 연결'):
        crud.register_transfer_as_member(db,331,'양26-331')
    assert db.query(models.LicenseHolder).count()==0
