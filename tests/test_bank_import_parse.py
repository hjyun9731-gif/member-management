"""통장 거래내역 파싱 테스트 — 가상 데이터, 임시 sqlite 전용(운영 DB 접속 없음)."""
import io, os, sys
os.environ["DATABASE_URL"] = "sqlite:////tmp/mm_test_parse.db"   # 반드시 임시 DB
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from datetime import datetime
from app.routers import receivables as R

def xlsx(rows, cols):
    buf = io.BytesIO(); pd.DataFrame(rows, columns=cols).to_excel(buf, index=False); return buf.getvalue()

NH_COLS = ["거래일자","출금금액(원)","입금금액(원)","거래 후 잔액(원)","거래내용","거래기록사항","거래점","거래시간","이체메모"]
rows = [
 [datetime(2026,10,3),0,30000,1030000,"스마트뱅킹","김철수","강원시지부","08:59:43",""],
 [datetime(2026,10,3),0,30000,1060000,"스마트뱅킹","김철수","강원시지부","09:10:05",""],   # 같은 날·같은 금액·같은 사람, 별도 입금
 [datetime(2026,10,4),0,10000,1070000,"타행이체","신한 이영희","강원시지부","10:00:00",""],
 [datetime(2026,10,4),0,5000,1075000,"NH스마트뱅킹","타행이체","강원시지부","11:00:00",""],   # 입금자 확인 불가
 [datetime(2026,10,5),20000,0,1055000,"출금","관리비","강원시지부","12:00:00",""],             # 출금: 제외
]
def parse(rows=rows, cols=NH_COLS):
    return R._parse_import_rows(xlsx(rows, cols), "nh.xlsx", "통장")

def test_payer_is_record_text_not_channel():
    p = parse()
    assert [x["payer_name"] for x in p] == ["김철수","김철수","이영희",""]
    assert all(x["payer_name"] not in ("스마트뱅킹","타행이체","NH스마트뱅킹") for x in p)

def test_withdrawals_excluded_and_amounts():
    p = parse(); assert [x["amount"] for x in p] == [30000,30000,10000,5000]

def test_same_day_same_amount_are_distinct():
    p = parse(); assert p[0]["fingerprint"] != p[1]["fingerprint"] and p[0]["has_discriminator"]

def test_repaste_same_row_same_fingerprint():
    a, b = parse()[0], parse()[0]; assert a["fingerprint"] == b["fingerprint"]

def test_time_and_raw_preserved():
    p = parse()[0]; assert p["transaction_time"] == "08:59:43" and p["raw_data"]["_payer_raw"] == "김철수"

def test_legacy_fp_matches_old_logic():
    # 이전 로직은 payer=거래내용(수단), memo=거래내용 → 이미 반영된 구 거래와 대조 가능해야 한다.
    import hashlib
    p = parse()[0]
    base = "|".join(["통장","2026-10-03","30000", R._norm_col("스마트뱅킹"), "", R._norm_col("스마트뱅킹")])
    assert p["legacy_fingerprint"] == hashlib.sha256(base.encode()).hexdigest()

def test_no_time_no_balance_flags_no_discriminator():
    cols = ["거래일자","입금금액","거래기록사항"]; p = parse([[datetime(2026,10,3),30000,"박민수"]]*2, cols)
    assert p[0]["fingerprint"] == p[1]["fingerprint"] and not p[0]["has_discriminator"]

def test_csv_cp949():
    df = pd.DataFrame(rows, columns=NH_COLS); data = df.to_csv(index=False).encode("cp949")
    p = R._parse_import_rows(data, "nh.csv", "통장"); assert p[0]["payer_name"] == "김철수"
