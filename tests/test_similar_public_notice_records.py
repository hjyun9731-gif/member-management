"""Synthetic data only; never contacts Railway production."""
from sqlalchemy.orm import Session
from db_support import create_test_engine
from app.database import Base
from app import models
from app.services.similar_records import date_key, region_key, find_similar, set_candidate_approval_date, candidate_approval_date


def test_approval_date_normalization():
    assert date_key('26.10.26.') == '2026-10-26'
    assert date_key('2026-10-26') == '2026-10-26'
    assert date_key('2026.10.26') == '2026-10-26'
    assert date_key('2026-13-26') == ''
    assert date_key('26.10.26 날짜메모') == ''
    assert region_key('강원특별자치도 강릉시') == region_key('강릉')


def test_candidate_notice_preserved_in_raw_data():
    d={'name':'가상인물','approval_date':'2026-10-26','raw_data':{'original':'preserve'}}
    set_candidate_approval_date(d)
    assert d['raw_data']=={'original':'preserve', 'approval_date':'2026-10-26'}
    assert 'approval_date' not in d
    c=models.Candidate(raw_data=d['raw_data'])
    assert candidate_approval_date(c)=='2026-10-26'
    e={'approval_date':'2026-11-01'}
    set_candidate_approval_date(e,c.raw_data)
    assert e['raw_data']=={'original':'preserve','approval_date':'2026-11-01'}


def test_similar_records_four_fields_and_no_auto_merge():
    engine=create_test_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                models.LicenseHolder(id=11,name='김현수',management_number='양26-100.',
                                     category='개인',region='강릉시',approval_date='26.10.26.',status='active',
                                     resident_number='synthetic_rrn'),
                models.LicenseHolder(id=12,name='김현수',management_number='양23-7',
                                     category='택배',region='원주시',approval_date='2023-10-26',status='closed'),
                models.LicenseHolder(id=13,name='김현수',management_number='양23-8',
                                     category='개인',region='강릉',approval_date='2023-10-26',deleted_at=__import__('datetime').datetime.now()),
                models.Candidate(id=27,name='김현수',management_number='양26-110',
                                 region='강릉',raw_data={'approval_date':'2026-10-26'},is_registered=False),
                models.Candidate(id=28,name='김현수',management_number='양26-111',
                                 is_registered=True,member_id=11),
                models.TransferLedger(id=43,transferee='김현수',region='강릉',
                                      approval_date='2026.10.26',management_number='양26-122'),
            ])
            db.commit()
            r=find_similar(db, management_number=' 양26 - 100 ', name='김현수',
                           approval_date='2026-10-26',region='강원특별자치도 강릉시')
            assert r['has_management_conflict'] is True
            assert r['matches'][0]['id']==11
            assert r['matches'][0]['matched_fields']==['관리번호','이름','인가일자','지역']
            assert {m['type'] for m in r['matches']}=={'member','candidate','transfer'}
            assert {m['id'] for m in r['matches']}=={11,12,27,43}
            assert not any('resident' in m or 'mobile' in m or 'certificate' in m for m in r['matches'])
            # No hidden mutations while suggesting matches.
            assert db.get(models.LicenseHolder,11).status=='active'
            n=find_similar(db,name='김현수',approval_date='2026-10-26',region='강릉')
            assert n['has_management_conflict'] is False
            assert len(n['matches'])==4
            q=find_similar(db,name='김현수',region='속초시')
            assert q['matches'][0]['name']=='김현수'  # name alone is only a suggestion
            assert not find_similar(db,name='완전히다른가상인',region='속초')['matches']
    finally:
        engine.dispose()


def test_existing_candidate_approval_raw_date_not_overwritten():
    d={'memo':'foo','raw_data':{'approval_date':'2026-10-26','other':'kept'}}
    set_candidate_approval_date(d)
    assert d['raw_data']['other']=='kept'


def test_provisional_transfer_number_is_reserved_for_next_number():
    from app import crud
    yy=crud._get_yy()
    engine=create_test_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(models.TransferLedger(transferee='가상임시양수자',
                                         management_number=f'양{yy}-900'))
            db.commit()
            assert crud.get_next_transfer_member_number(db)==f'양{yy}-901'
            assert crud.get_last_issued_management_number(db, 'transfer')==f'양{yy}-900'
    finally:
        engine.dispose()


def test_four_field_provisional_records_cannot_become_member_without_vehicle():
    import pytest
    from app import crud
    engine=create_test_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                models.Candidate(id=100,name='가상인물',management_number='신26-999',
                                 region='강릉',raw_data={'approval_date':'2026-11-08'},is_registered=False),
                models.TransferLedger(id=101,transferee='가상인물2',management_number='양26-999',
                                      region='속초',approval_date='2026-11-08'),
            ])
            db.commit()
            with pytest.raises(ValueError,match='차량번호'):
                crud.register_candidate_as_member(db,100,'2026-11-08','신26-999')
            with pytest.raises(ValueError,match='차량번호'):
                crud.register_transfer_as_member(db,101,'양26-999')
            assert db.query(models.LicenseHolder).count()==0
    finally:
        engine.dispose()
