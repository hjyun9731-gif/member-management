from types import SimpleNamespace
from scripts.zero_unissued_management_arrears import is_unissued

def test_unissued_fields_both_missing():
    assert is_unissued(SimpleNamespace(certificate_issue_date=None, certificate_number=None))
    assert is_unissued(SimpleNamespace(certificate_issue_date=' ', certificate_number=''))

def test_number_prevents_unissued_classification():
    assert not is_unissued(SimpleNamespace(certificate_issue_date=None, certificate_number='AB123'))

def test_date_prevents_unissued_classification():
    assert not is_unissued(SimpleNamespace(certificate_issue_date='2026-02-01', certificate_number=''))


def test_sqlite_arrears_reset_is_idempotent_and_preserves_real_payments():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import LicenseHolder
    from app.receivables_models import ReceivableProfile, ReceivableCharge, ReceivablePayment
    from scripts.zero_unissued_management_arrears import execute, calculate_workspace_balance
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            LicenseHolder(id=1, name='미발급', category='택배', certificate_issue_date=None, certificate_number=None),
            LicenseHolder(id=2, name='번호있음', category='택배', certificate_issue_date=None, certificate_number='26-100'),
            LicenseHolder(id=3, name='기타계정', category='택배', certificate_issue_date=None, certificate_number=None),
            LicenseHolder(id=4, name='선납', category='택배', certificate_issue_date=None, certificate_number=None),
            ReceivableProfile(member_id=1, account_type='관리비', legacy_balance=120000),
            ReceivableProfile(member_id=2, account_type='관리비', legacy_balance=20000),
            ReceivableProfile(member_id=3, account_type='자격증명', legacy_balance=30000),
            ReceivableProfile(member_id=4, account_type='관리비', legacy_balance=-10000),
            ReceivableCharge(member_id=1, billing_month='2026-10', amount=5000, account_type='관리비'),
            ReceivablePayment(member_id=1, payment_date='2026-10-02', amount=20000, method='계좌이체'),
        ])
        session.commit()
        preview = execute(session, apply=False)
        assert preview['eligible_members'] == 2
        assert preview['affected_members'] == 1
        assert preview['arrears_cleared'] == 105000
        assert session.query(ReceivablePayment).count() == 1
        result = execute(session, apply=True)
        session.commit()
        assert result['affected_members'] == 1
        assert calculate_workspace_balance(session, session.query(ReceivableProfile).filter_by(member_id=1).one()) == 0
        assert session.query(ReceivablePayment).filter_by(method='계좌이체').count() == 1
        assert session.query(ReceivablePayment).filter_by(method='잔액수정').count() == 1
        assert session.query(ReceivableCharge).count() == 1
        assert execute(session, apply=True)['affected_members'] == 0
        assert calculate_workspace_balance(session, session.query(ReceivableProfile).filter_by(member_id=4).one()) == -10000
