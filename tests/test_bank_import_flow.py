"""농협 거래내역 업로드→미리보기→반영 통합 테스트(가상 데이터 · 임시 sqlite · 운영 DB 접속 없음)."""
import io, os, sys
os.environ["DATABASE_URL"] = "sqlite:////tmp/mm_test_flow.db"
if os.path.exists("/tmp/mm_test_flow.db"): os.remove("/tmp/mm_test_flow.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd, pytest
from datetime import datetime
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import models
from app.database import Base, engine, SessionLocal
from app.routers import receivables as R
from app.auth import get_current_user
from app.database import get_db

COLS = ["구분","거래일자","출금금액(원)","입금금액(원)","거래 후 잔액(원)","거래내용","거래기록사항","거래점","거래시간","이체메모","거래메모"]

def nh_file(rows):
    """실제 농협 파일 형태: 상단 계좌정보 9줄 + 헤더(10번째 줄) + 거래(최신순)."""
    meta = [["입출금거래내역조회 결과"]+[""]*10, [""]*11, [""]*11, ["현재시간 : 2026/10/09"]+[""]*10, [""]*11,
            ["계좌번호","","000-00-000000","","","조회일","","2026/10/01 ~ 2026/10/08","","",""],
            ["예금주명","","테스트협회","","","예금종류","","보통예금","","",""], ["현재통장잔액","","1,000,000원","","","","","","","",""], [""]*11, COLS]
    body = [[i+1,*r] for i,r in enumerate(rows)]
    buf = io.BytesIO(); pd.DataFrame(meta+body).to_excel(buf, index=False, header=False); return buf.getvalue()

ROWS = [  # 거래일자, 출금, 입금, 잔액, 거래내용, 기록사항, 거래점, 시간, 이체메모, 거래메모
 ["2026/10/08","",30000,2030000,"스마트당행","김철수","농협 000001","18:17:16","",""],
 ["2026/10/08","",30000,2000000,"스마트당행","박영희","농협 000002","14:00:00","",""],      # 같은 날·같은 금액·같은 거래내용, 다른 사람
 ["2026/10/08","",30000,1970000,"스마트당행","김철수","농협 000001","09:01:00","",""],      # 같은 사람이 같은 날 같은 금액 두 번(정상 별도입금)
 ["2026/10/07","",59760,1940240,"PC우리은행","우123456789","우리 0202905","10:00:00","",""],  # 카드정산형 → 회원 연결 금지
 ["2026/10/07",20834124,"",1880480,"인터넷당행","관리비출금","농협 000003","09:00:00","",""],
]

@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine)
    R._receivables_schema_ready = False
    db = SessionLocal()
    for i,(n,v) in enumerate([("김철수","강원12바1111"),("박영희","강원13바2222")], 1):
        db.add(models.LicenseHolder(id=i, name=n, vehicle_number=v, category="개인", status="active"))
    db.commit(); db.close()
    app = FastAPI(); app.include_router(R.router)
    def _db():
        d = SessionLocal()
        try: yield d
        finally: d.close()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"username":"tester","role":"admin","id":1,"is_admin":True})()
    return TestClient(app)

def preview(c, rows=ROWS):
    r = c.post("/api/receivables/imports/preview", data={"source_type":"통장"}, files={"file":("nh.xlsx", nh_file(rows))})
    assert r.status_code == 200, r.text; return r.json()

def test_preview_classification(client):
    d = preview(client); rows = d["rows"]
    assert len(rows) == 4                                   # 출금 1건 제외
    kc = [r for r in rows if r["payer_name"]=="김철수"]
    assert len(kc) == 2 and all(r["status"] != "duplicate" for r in kc)      # 같은 날 같은 금액 정상 거래 보존
    card = [r for r in rows if "우123456789" in (r["payer_name"] or "")][0]
    assert card["matched_member_id"] is None and card["status"] == "review"  # 카드정산형은 회원 연결 금지
    # 이름만 일치 → 후보(review)로만, 자동 반영(matched) 금지
    assert all(r["status"] == "review" for r in rows if r["payer_name"] in ("김철수","박영희"))
    assert all(r["matched_member_id"] for r in rows if r["payer_name"] in ("김철수","박영희"))   # 후보는 표시

def test_reupload_same_file_is_duplicate_after_posting(client):
    d1 = preview(client)
    for r in d1["rows"]:
        if r["matched_member_id"]:   # 사람이 후보 확정
            assert client.patch(f"/api/receivables/imports/rows/{r['id']}/match", json={"member_id": r["matched_member_id"]}).status_code == 200
    p = client.post(f"/api/receivables/imports/{d1['batch']['id']}/post"); assert p.status_code == 200, p.text
    d2 = preview(client)             # 같은 파일 다시 업로드
    states = sorted(r["status"] for r in d2["rows"])
    assert states.count("duplicate") == 3, states   # 반영된 3건은 중복, 카드정산형 1건은 아직 미반영(확인필요)
    d3 = preview(client)             # 한 번 더
    assert sorted(r["status"] for r in d3["rows"]).count("duplicate") == 3

def test_overlap_new_and_old_rows(client):
    d1 = preview(client, ROWS[:3])
    for r in d1["rows"]: client.patch(f"/api/receivables/imports/rows/{r['id']}/match", json={"member_id": r["matched_member_id"]})
    client.post(f"/api/receivables/imports/{d1['batch']['id']}/post")
    extra = ["2026/10/08","",30000,2060000,"스마트당행","김철수","농협 000001","19:30:00","",""]   # 같은 날 같은 금액 신규 입금
    d2 = preview(client, [extra]+ROWS[:3])
    by = {(r["payer_name"], r["match_reason"]): r["status"] for r in d2["rows"]}
    assert sum(1 for r in d2["rows"] if r["status"]=="duplicate") == 3
    new = [r for r in d2["rows"] if r["status"]!="duplicate"]; assert len(new) == 1   # 신규 1건만 살아남음
