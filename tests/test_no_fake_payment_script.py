"""정정 프로그램이 다시 '가짜 수납(잔액수정)'을 만들지 않도록 막는 회귀 테스트."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

def test_bulk_tools_never_create_payments():
    for rel in ("scripts/zero_unissued_management_arrears.py", "app/receivable_adjustments.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "ReceivablePayment(" not in src, rel          # 수납 행을 만들지 않는다
        assert "잔액수정" not in src, rel

def test_no_startup_execution_of_adjustments():
    for rel in ("app/main.py", "app/railway_entry.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "apply_plan" not in src and "zero_unissued" not in src, rel     # 서버 시작 시 자동 정정 금지
