"""DB 접속 없이 자격증명 미발급자의 관리비 생성 차단 규칙을 검증한다."""
import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'app' / 'routers' / 'receivables.py'
TREE = ast.parse(SRC.read_text(encoding='utf-8'))
FUNCTIONS = {'_certificate_unissued', '_auto_charge_allowed_for_month'}
MODULE = ast.Module(body=[n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS], type_ignores=[])
NAMESPACE = {'GENERAL_MANAGEMENT_START_KEY': '2027-01', '_is_bae_vehicle': lambda member: '배' in (member.vehicle_number or '')}
exec(compile(MODULE, str(SRC), 'exec'), NAMESPACE)

def member(date='', number='', vehicle='강원81배1234'):
    return SimpleNamespace(certificate_issue_date=date, certificate_number=number, vehicle_number=vehicle)

def profile(account='관리비'):
    return SimpleNamespace(account_type=account)

def test_unissued_both_missing():
    assert NAMESPACE['_certificate_unissued'](member(None,None))
    assert NAMESPACE['_certificate_unissued'](member('  ','  '))

def test_number_only_not_unissued():
    assert not NAMESPACE['_certificate_unissued'](member('', '26-001'))

def test_date_only_not_unissued():
    assert not NAMESPACE['_certificate_unissued'](member('2026-10-01',''))

def test_management_blocked_before_and_after_cutover():
    for month in ('2026-09','2026-10','2027-01','2028-05'):
        assert not NAMESPACE['_auto_charge_allowed_for_month'](profile(), member(), month)

def test_number_only_member_not_blocked():
    assert NAMESPACE['_auto_charge_allowed_for_month'](profile(), member('', '26-001'), '2026-10')

def test_non_bae_2026_still_blocked():
    assert not NAMESPACE['_auto_charge_allowed_for_month'](profile(), member('2026-09-01','26-001','12가3456'), '2026-10')

def test_membership_fee_unchanged():
    assert NAMESPACE['_auto_charge_allowed_for_month'](profile('협회비'),member(), '2026-10')
