"""데이터 정합성 점검(읽기 전용) 테스트 - 비식별 합성 데이터, 임시 SQLite DB만 사용한다."""
import os
import tempfile

_TMP = os.path.join(tempfile.gettempdir(), "mm_data_integrity_test.db")
# 다른 테스트 모듈과 같은 프로세스에서 엔진을 공유하므로 setdefault 만 사용하고,
# 아래 픽스처가 시작/종료 시 관련 테이블을 비워 다른 테스트에 영향을 남기지 않는다.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}")
assert "postgres" not in os.environ["DATABASE_URL"], "정합성 테스트는 운영/PostgreSQL DB에서 실행하지 않는다"

import pytest
from fastapi.testclient import TestClient

from app import models
from app.auth import get_current_user
from app.database import Base, engine, SessionLocal
from app.main import app
from app.services import contact_parser as cp
from app.services import data_integrity as di
from app import certificate_ledger_models as clm


# ───────────── 연락처 파서 (시나리오 1~8, 10) ─────────────
def test_two_different_mobiles_in_one_cell():
    p = cp.parse_contact("010-3428-2714 010-6740-2714")
    assert p.status == cp.MULTIPLE
    assert [n.formatted for n in p.numbers] == ["010-3428-2714", "010-6740-2714"]
    assert p.raw == "010-3428-2714 010-6740-2714"      # 원본 보존


def test_same_number_different_hyphenation_is_repeated():
    p = cp.parse_contact("010-1111-2222 01011112222")
    assert p.status == cp.REPEATED and len(p.numbers) == 1


@pytest.mark.parametrize("raw", ["010-1111-2222, 010-3333-4444", "01011112222/01033334444",
                                 "010-1111-2222\n010-3333-4444", "010 1111 2222 010 3333 4444"])
def test_separators(raw):
    assert cp.parse_contact(raw).status == cp.MULTIPLE


def test_single_normal_empty_and_invalid():
    assert cp.parse_contact("010-1111-2222").status == cp.OK
    assert cp.parse_contact("").status == cp.EMPTY
    assert cp.parse_contact(None).status == cp.EMPTY
    for bad in ("010-342-271", "abc", "010-1111-2222 (집)"):
        p = cp.parse_contact(bad)
        assert p.status == cp.INVALID and p.needs_review, bad


def test_mobile_and_landline_together_kinds():
    p = cp.parse_contact("010-1111-2222 033-123-4567")
    assert [n.kind for n in p.numbers] == ["mobile", "landline"]


def test_same_number_across_fields():
    assert cp.same_number_across("010-1111-2222", "01011112222") == ["01011112222"]
    assert cp.same_number_across("010-1111-2222", "010-3333-4444") == []


def test_split_numbers_unclear_returns_empty():
    assert cp.split_numbers("010-1111-2222 확인필요") == []
    assert cp.split_numbers("010-1111-2222 010-3333-4444") == ["010-1111-2222", "010-3333-4444"]


# ───────────── DB 스캔 ─────────────
def _wipe(s):
    for m in (clm.CertificateIssuanceHistory, clm.CertificateIssuanceLedger, models.CertificateNumberLog,
              models.MemberAgent, models.Closure, models.TransferLedger, models.Candidate, models.LicenseHolder):
        s.query(m).delete()
    s.commit()


@pytest.fixture(scope="module")
def db():
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    _wipe(s)
    L, C, T = models.LicenseHolder, models.Candidate, models.TransferLedger
    # 연락처 사례
    s.add(L(id=1, management_number="신13-12", name="가상회원A", category="택배", vehicle_number="강원83배1021",
            status="active", mobile="010-3428-2714 010-6740-2714"))
    s.add(L(id=2, management_number="신13-13", name="가상회원B", category="개인", vehicle_number="강원80바1111",
            status="active", phone="010-1111-2222", mobile="01011112222"))
    # 김현수형 사례: 폐업(closed) 회원이 번호 점유 + 마침표 붙은 재등록
    s.add(L(id=11718, management_number="양26-100", name="가상회원C", category="개인", vehicle_number="강원80바2222",
            status="closed", registration_type="양도양수", transfer_ledger_id=10017, closure_id=None))
    s.add(L(id=11733, management_number="양26-100.", name="가상회원C", category="개인", vehicle_number="강원80바2222",
            status="active", registration_type="신규", candidate_id=247, certificate_number="26-441"))
    s.add(T(id=10017, management_number="양26-100", transferee="가상회원C", member_id=11718))
    s.get(T, 10017)
    from datetime import datetime
    s.add(T(id=10025, management_number="양26-100.", transferee="가상회원C", transferee_member_id=11733))
    s.flush()
    s.query(T).filter(T.id == 10017).update({"deleted_at": datetime(2026, 10, 23)})
    s.add(C(id=247, name="가상회원C", vehicle_number="강원80바2222", is_registered=True, member_id=11733,
            management_number="양26-100"))
    # 같은 차량번호가 과거 양도자(closed)/현재 양수자(active)에 나타나는 정상 사례 → DQ-04 아님
    s.add(L(id=21, management_number="신20-1", name="양도자X", category="개인", vehicle_number="강원80바3333", status="closed"))
    s.add(L(id=22, management_number="양26-5", name="양수자Y", category="개인", vehicle_number="강원80바3333", status="active"))
    # 동명이인(이름만 같음) → 중복으로 판정하면 안 됨
    s.add(L(id=31, management_number="신20-2", name="동명이인", category="개인", vehicle_number="강원80바4001", status="active",
            resident_number="8001011111111"))
    s.add(L(id=32, management_number="신20-3", name="동명이인", category="개인", vehicle_number="강원80바4002", status="active",
            resident_number="8002022222222"))
    # 자격증명: 표기 차이 26-85 vs 26-085, 다른 회원에 연결, 대장 미연결
    s.add(L(id=41, management_number="신20-4", name="증명A", category="개인", vehicle_number="강원80바5001", status="active",
            certificate_number="26-085"))
    s.add(clm.CertificateIssuanceLedger(id=1, document_number="26-85", name="증명A", vehicle_number="강원80바5001",
                                        member_id=41, status="발급완료"))
    s.add(L(id=42, management_number="신20-5", name="증명B", category="개인", vehicle_number="강원80바5002", status="active",
            certificate_number="26-90"))
    s.add(clm.CertificateIssuanceLedger(id=2, document_number="26-090", name="타인", vehicle_number="강원80바9999",
                                        member_id=41, status="발급완료"))
    s.add(clm.CertificateIssuanceLedger(id=3, document_number="26-91", name="미연결자", vehicle_number="강원80바8888",
                                        status="발급완료"))
    s.add(models.CertificateNumberLog(certificate_number="26-85", year=26, number=85, status="used"))
    s.add(models.CertificateNumberLog(certificate_number="26-90", year=26, number=90, status="cancelled"))
    # 등록예정자 연결 불일치 / 존재하지 않는 회원 참조
    s.add(C(id=300, name="예정자Z", is_registered=True, member_id=None))
    s.add(T(id=500, management_number="양26-9", transferee="고아", transferee_member_id=999999))
    # 정상 회원
    s.add(L(id=51, management_number="신20-9", name="정상", category="개인", vehicle_number="강원80바7000", status="active",
            mobile="010-5555-6666", membership_status="가입", membership_date="2020-03-01"))
    s.commit()
    yield s
    s.rollback()
    _wipe(s)
    s.close()


@pytest.fixture(scope="module")
def result(db):
    return di.run_scan(db)


def _has(result, code, table, rid, severity=None):
    return any(f["code"] == code and f["table"] == table and f["record_id"] == rid
               and (severity is None or f["severity"] == severity) for f in result["findings"])


def test_dq01_multiple_and_dq02_duplicate(result):
    assert _has(result, "DQ-01", "license_holders", 1, di.CONFIRMED)
    assert _has(result, "DQ-02", "license_holders", 2, di.REVIEW)       # 확정 아님 → 가능성
    assert not any(f["record_id"] == 51 and f["code"].startswith("DQ-0") and f["code"] in ("DQ-01", "DQ-02", "DQ-03")
                   for f in result["findings"])                       # 정상 번호 1개는 무사


def test_dq05_dq06_period_and_closed_occupancy(result):
    assert _has(result, "DQ-06", "license_holders", 11733, di.CONFIRMED)
    # 정규화하면 양26-100 과 충돌 → 확정이 아니라 확인 필요 (closed 와 active 가 같은 번호를 점유)
    assert _has(result, "DQ-05", "license_holders", 11718, di.REVIEW)
    assert _has(result, "DQ-05", "license_holders", 11733, di.REVIEW)


def test_mgmt_diagnosis_distinguishes_closed_from_deleted(db):
    d = di.diagnose_mgmt_number(db, "양26-100")
    tables = {(r["table"], r["id"]): r for r in d["records"]}
    assert tables[("license_holders", 11718)]["status"] == "closed"
    assert tables[("license_holders", 11718)]["blocks_new_registration"] is True    # 폐업이어도 삭제 아님 → 점유
    assert tables[("transfer_ledger", 10017)]["deleted"] is True                    # 삭제된 대장
    assert tables[("candidates", 247)]["blocks_new_registration"] is True           # 예정자도 점유
    assert d["blocking_count"] == 3 and any("폐업(closed)" in c for c in d["causes"])
    assert d["normalized_differs"] is False
    d2 = di.diagnose_mgmt_number(db, "양26-100.")
    assert d2["normalized"] == "양26-100" and d2["normalized_differs"] is True


def test_normal_same_vehicle_and_same_name_not_flagged_as_duplicate(result):
    assert not _has(result, "DQ-04", "license_holders", 21)            # 과거 양도자(closed)는 제외
    assert not _has(result, "DQ-04", "license_holders", 31)            # 이름만 같다고 중복 판정 금지
    assert not _has(result, "DQ-04", "license_holders", 32)


def test_dq08_dq14_dq15(result):
    assert _has(result, "DQ-14", "candidates", 300, di.CONFIRMED)
    assert _has(result, "DQ-15", "transfer_ledger", 500, di.CONFIRMED)


def test_dq09_cert_variants_and_conflicts(result):
    # 26-085 == 26-85 로 인식되어 41번은 정상 연결(오류 없음)
    assert not any(f["record_id"] == 41 and f["code"] == "DQ-09" and f["table"] == "license_holders"
                   for f in result["findings"])
    # 26-90 은 취소 상태 + 다른 회원(41)에게 연결
    assert _has(result, "DQ-09", "license_holders", 42, di.CONFIRMED)
    # 어디에도 연결되지 않은 발급대장 행
    assert _has(result, "DQ-09", "certificate_issuance_ledger", 3, di.REVIEW)


def test_scan_is_read_only(db):
    before = {t: db.query(m).count() for t, m in (("L", models.LicenseHolder), ("C", models.Candidate),
                                                  ("T", models.TransferLedger), ("Z", models.Closure))}
    di.run_scan(db)
    db.rollback()
    after = {t: db.query(m).count() for t, m in (("L", models.LicenseHolder), ("C", models.Candidate),
                                                 ("T", models.TransferLedger), ("Z", models.Closure))}
    assert before == after
    assert not db.dirty and not db.new


def test_mask_rrn():
    assert di.mask_rrn("800101-1234567") == "800101-1******"


# ───────────── API 권한 ─────────────
def test_api_requires_admin(db):
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/api/admin/data-integrity/summary").status_code in (401, 403)
    app.dependency_overrides[get_current_user] = lambda: models.User(id=2, username="staff", role="staff")
    try:
        assert client.get("/api/admin/data-integrity/summary").status_code == 403
        assert client.get("/api/admin/data-integrity/export").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_api_admin_ok_paging_and_filter(db):
    app.dependency_overrides[get_current_user] = lambda: models.User(id=1, username="admin", role="admin")
    try:
        c = TestClient(app, raise_server_exceptions=False)
        s = c.get("/api/admin/data-integrity/summary?refresh=true")
        assert s.status_code == 200 and s.json()["read_only"] is True
        r = c.get("/api/admin/data-integrity/findings?code=DQ-01&page_size=1")
        assert r.status_code == 200 and r.json()["page_size"] == 1 and len(r.json()["items"]) <= 1
        r = c.get("/api/admin/data-integrity/mgmt-number?number=양26-100")
        assert r.status_code == 200 and r.json()["blocking_count"] == 3
        r = c.get("/api/admin/data-integrity/export?code=DQ-01")
        assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
        # 쓰기 메서드는 존재하지 않아야 한다
        assert c.post("/api/admin/data-integrity/summary").status_code in (404, 405)
    finally:
        app.dependency_overrides.clear()
