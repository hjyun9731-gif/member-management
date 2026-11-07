"""전환된 예정자는 재연결 검색에서 제외하되 회원 및 미전환 예정자는 유지."""
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app import models, certificate_ledger_models as clm
from app.services import safe_corrections as sc
from app.routers.certificate_ledger import search_link_targets


def test_relink_search_hides_converted_candidate_but_keeps_member():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                models.LicenseHolder(id=502, name='가상검증홍길동', vehicle_number='강원82자2222호',
                                     certificate_number='26-222', category='개인', status='active'),
                models.Candidate(id=501, name='가상검증홍길동', vehicle_number='강원82자2222호',
                                 certificate_number='26-222', is_registered=True, member_id=502),
                models.Candidate(id=503, name='가상검증홍길동', vehicle_number='강원82자3333호',
                                 certificate_number='26-333', is_registered=False),
            ])
            db.commit()
            result = asyncio.run(search_link_targets(q='가상검증홍길동', db=db, _=None))
            entries = {(item['type'], item['id']) for item in result['items']}
            assert ('candidate', 501) not in entries
            assert ('candidate', 503) in entries
            assert ('member', 502) in entries
    finally:
        engine.dispose()


def test_preview_refuses_converted_candidate_target():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                models.Candidate(id=501, name='가상검증홍길동', vehicle_number='강원82자2222호',
                                 certificate_number='26-222', is_registered=True, member_id=502),
                clm.CertificateIssuanceLedger(id=601, name='가상검증홍길동',
                                              vehicle_number='강원82자2222호',
                                              document_number='26-222', status='인가대기'),
            ])
            db.commit()
            result = sc.preview_certificate_relink(db, 601, 'candidate', 501)
            assert not result['allowed']
            assert any('회원 전환 완료 예정자' in reason for reason in result['reasons'])
    finally:
        engine.dispose()
