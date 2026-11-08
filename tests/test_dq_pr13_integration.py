"""DQ diagnostics and PR #13 guards must agree without changing stored history."""
import csv
import io
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient
from db_support import create_test_engine
from app import models, certificate_ledger_models  # register ledger metadata
from app.database import Base, get_db
from app.auth import require_admin
from app.routers import data_integrity as router
from app.services import data_integrity as di, member_integrity as guard
from app.services.agent_phone_link import link_known_current_agent


@pytest.fixture
def db():
    engine = create_test_engine(connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
        s.rollback()
    engine.dispose()


@pytest.mark.parametrize('variant', ['양26-0100', ' 양26 - 100. ', '양２６-１００', '양26-100..', '양26-100,', '양26-100;', '양26-100·'])
def test_occupancy_and_registration_guard_use_same_key(db, variant):
    db.add_all([
        models.LicenseHolder(id=1, name='가상원본', management_number=variant, status='closed'),
        models.Candidate(id=2, name='가상예정', management_number=variant),
        models.TransferLedger(id=3, transferee='가상양수', management_number=variant),
        models.Closure(id=4, name='가상폐업', original_management_number=variant),
    ])
    db.commit()
    assert guard.management_conflicts(db, '양26-100') == ['회원 #1', '예정자 #2']
    result = di.diagnose_mgmt_number(db, '양26-100')
    assert result['blocking_count'] == 2
    assert {r['table'] for r in result['records']} == {'license_holders', 'candidates', 'transfer_ledger', 'closures'}
    assert all(r['management_number'] == variant for r in result['records'])
    assert db.get(models.LicenseHolder, 1).management_number == variant


def test_zero_padded_number_collision_is_visible_in_dq05(db):
    db.add_all([models.LicenseHolder(id=1, management_number='양26-0100', status='active'),
                models.LicenseHolder(id=2, management_number='양26-100', status='active')])
    db.commit()
    rows = di.run_scan(db, ['DQ-05'])['findings']
    assert {r['record_id'] for r in rows} == {1, 2}
    assert all(r['severity'] == di.REVIEW for r in rows)


def test_ambiguous_current_agents_do_not_mirror_even_if_one_phone_matches(db):
    member = models.LicenseHolder(id=1, name='가상차주', category='개인')
    db.add_all([member, models.MemberAgent(member_id=1, name='가상A', mobile='010-1111-2222', status='현재'),
                models.MemberAgent(member_id=1, name='가상B', mobile='010-3333-4444', status='현재')])
    db.commit()
    assert link_known_current_agent(db, member, '01011112222(대)') == 'review_required'
    assert not member.agent_name and not member.agent_mobile
    assert any(r['code'] == 'DQ-13' for r in di.run_scan(db)['findings'])


def test_all_fifteen_checks_are_exercised_and_issue_no_writes(db):
    db.add_all([
        models.LicenseHolder(id=1, name='가상A', management_number='양26-1.', registration_type='신규',
            phone='01011112222', mobile='01011112222 01033334444', category='개인', status='active',
            vehicle_number='가상차량', resident_number='8001011234567', certificate_number='26-1',
            membership_status='가입', membership_date='', approval_date='2026-02-30'),
        models.LicenseHolder(id=2, name='가상B', management_number='양26-1', status='active',
            vehicle_number='가상차량', resident_number='8001011234567', mobile='확인필요', transfer_ledger_id=999),
        models.LicenseHolder(id=3, name='가상C', status='closed'),
        models.Candidate(id=1, name='가상예정', is_registered=True),
        models.TransferLedger(id=1, transferee='가상양수', member_id=999, transferee_member_id=2),
        models.CertificateNumberLog(certificate_number='26-1', year=26, number=1, status='cancelled'),
        models.MemberAgent(member_id=1, name='가상대리A', status='현재'),
        models.MemberAgent(member_id=1, name='가상대리B', status='현재'),
    ])
    db.commit()
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', record)
    try:
        if db.bind.dialect.name == 'postgresql':
            db.execute(text('SET TRANSACTION READ ONLY'))
        result = di.run_scan(db)
        di.diagnose_mgmt_number(db, '양26-1')
        assert {r['code'] for r in result['findings']} == set(di.CHECKS)
        assert not db.new and not db.dirty and not db.deleted
        assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'ALTER', 'CREATE', 'DROP')) for s in statements)
        assert result['summary']['auto_fixable'] == 0
    finally:
        event.remove(db.bind, 'before_cursor_execute', record)


@pytest.mark.parametrize('query', ['category=개인', 'region=춘천', 'status=active',
                                    'management_number=신26-1', 'vehicle_number=80바1111', 'name=가상A'])
def test_export_matches_each_findings_filter(db, query):
    db.add_all([
        models.LicenseHolder(id=1, name='가상A', category='개인', region='춘천', status='active',
                            management_number='신26-1', vehicle_number='80바1111', mobile='잘못된번호'),
        models.LicenseHolder(id=2, name='가상B', category='택배', region='원주', status='closed',
                            management_number='신26-2', vehicle_number='80바2222', mobile='잘못된번호'),
    ])
    db.commit()
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[require_admin] = lambda: models.User(id=1, username='test', role='admin')
    app.dependency_overrides[get_db] = lambda: db
    router._CACHE.update(at=0, data=None)
    try:
        c = TestClient(app)
        listing = c.get('/api/admin/data-integrity/findings?refresh=true&' + query).json()
        response = c.get('/api/admin/data-integrity/export?' + query)
        assert response.status_code == 200
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))[1:]
        assert len(rows) == listing['total'] > 0
        assert {r[2] for r in rows} == {'가상A'}
    finally:
        router._CACHE.update(at=0, data=None)


@pytest.mark.parametrize('raw', [
    'postgresql://user@production.example/mm_test_ci',
    'postgresql://user@127.0.0.1/member_management',
    'postgresql://user@127.0.0.1/mm_test_ci?host=production.example',
    'sqlite:///mm_test_ci',
])
def test_regression_runner_rejects_unapproved_databases(monkeypatch, raw):
    from db_support import postgres_url
    monkeypatch.setenv('MM_TEST_POSTGRES_URL', raw)
    with pytest.raises(RuntimeError, match='loopback PostgreSQL'):
        postgres_url()


def test_concurrent_candidate_conversion_creates_only_one_member(db):
    if db.bind.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL row/advisory lock regression')
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import crud
    db.add(models.Candidate(id=55, name='가상동시등록', vehicle_number='강원80바5555', management_number='신26-55'))
    db.commit()
    barrier = Barrier(2)
    def convert():
        with Session(db.bind) as other:
            barrier.wait(timeout=5)
            try:
                return ('ok', crud.register_candidate_as_member(other, 55, '2026-11-08', '신26-55').id)
            except ValueError as exc:
                return ('blocked', str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: convert(), range(2)))
    assert sorted(r[0] for r in results) == ['blocked', 'ok']
    assert db.query(models.LicenseHolder).count() == 1
    db.expire_all()
    candidate = db.get(models.Candidate, 55)
    assert candidate.is_registered and candidate.member_id == db.query(models.LicenseHolder).one().id
