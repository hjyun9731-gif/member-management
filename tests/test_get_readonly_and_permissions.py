"""① 조회(GET)는 DB를 바꾸지 않는다 ② 로그인·직원 수납·관리자 정정 권한 ③ SECRET_KEY 보안 — 가상 데이터, 운영 DB 접속 없음."""
import os, re, sys
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/mm_getro_test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from app import auth, models
from app.auth import admin_for_writes, create_access_token, get_password_hash, staff_write_ok
from app.database import engine, SessionLocal
from app.receivables_models import ReceivableCharge, ReceivablePayment, ReceivableProfile
from app.routers import receivables as R
import test_unissued_adjustments as T

db = T.db  # 가상 회원/프로필/부과/수납 fixture 재사용


@pytest.fixture()
def users(db):
    for n, role in (("admin1", "admin"), ("staff1", "staff")):
        db.add(models.User(username=n, password_hash=get_password_hash("pw-" + n), role=role, full_name=n))
    db.commit()
    return {r: {"Authorization": "Bearer " + create_access_token({"sub": n})} for n, r in (("admin1", "admin"), ("staff1", "staff"))}


@pytest.fixture()
def main_client(db, users):
    import app.main as M
    return TestClient(M.app, raise_server_exceptions=False)


# ───────────────────── ① GET 은 쓰기를 하지 않는다 ─────────────────────
def _get_paths():
    import app.main as M
    out = []
    for path, ops in M.app.openapi()["paths"].items():
        if path.startswith("/api/receivables") and "get" in ops:
            out.append(path)
    return sorted(out)


def test_no_get_endpoint_writes_to_db(db, users, main_client):
    """모든 /api/receivables GET 을 호출하면서 INSERT/UPDATE/DELETE 가 한 번이라도 나가면 실패."""
    writes, current = [], {"path": ""}

    def watch(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append((current["path"], statement[:90]))

    event.listen(engine, "before_cursor_execute", watch)
    try:
        paths = _get_paths()
        assert len(paths) >= 8, paths
        for p in paths:
            current["path"] = p
            url = re.sub(r"\{[^}]+\}", "1", p)
            main_client.get(url, headers=users["admin"])
    finally:
        event.remove(engine, "before_cursor_execute", watch)
    assert writes == [], writes


def test_member_detail_get_does_not_change_profile_or_charges(db, users, main_client):
    """상세 조회가 기준잔액/계정을 덮어쓰거나 부과를 삭제·생성하면 안 된다."""
    before = (
        [(p.member_id, p.legacy_balance, p.account_type) for p in db.query(ReceivableProfile).order_by(ReceivableProfile.member_id)],
        [(c.id, c.amount) for c in db.query(ReceivableCharge).order_by(ReceivableCharge.id)],
    )
    for mid in (2, 3, 6, 11):
        assert main_client.get(f"/api/receivables/members/{mid}", headers=users["admin"]).status_code == 200
    db.expire_all()
    after = (
        [(p.member_id, p.legacy_balance, p.account_type) for p in db.query(ReceivableProfile).order_by(ReceivableProfile.member_id)],
        [(c.id, c.amount) for c in db.query(ReceivableCharge).order_by(ReceivableCharge.id)],
    )
    assert before == after


def test_detail_get_does_not_create_missing_profile(db, users, main_client):
    db.add(models.LicenseHolder(id=99, name="신규회원", vehicle_number="강원80배9999", category="택배", status="active"))
    db.commit()
    r = main_client.get("/api/receivables/members/99", headers=users["admin"])
    assert r.status_code == 404 and "동기화" in r.json()["detail"]
    db.expire_all()
    assert db.query(ReceivableProfile).filter_by(member_id=99).count() == 0          # 조회로 프로필이 생기지 않는다


def test_background_sync_is_off_by_default(monkeypatch):
    import threading
    monkeypatch.delenv("RECEIVABLES_BACKGROUND_SYNC", raising=False)
    assert R._background_sync_enabled() is False
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: (_ for _ in ()).throw(AssertionError("백그라운드 스레드가 시작됨")))
    R._start_monthly_billing_scheduler()                       # 기본 꺼짐: 스레드를 만들지 않고 즉시 반환
    monkeypatch.setenv("RECEIVABLES_BACKGROUND_SYNC", "1")
    assert R._background_sync_enabled() is True


# ───────────────────── 명시적 관리자 동기화 ─────────────────────
def test_explicit_admin_sync(db, users, main_client):
    db.add(models.LicenseHolder(id=99, name="신규회원", vehicle_number="강원80배9999", category="택배", status="active",
                                certificate_issue_date="2026-01-01", certificate_number="N-1", approval_date="2026-01-01"))
    db.commit()
    assert main_client.get("/api/receivables/admin/sync-status", headers=users["staff"]).status_code == 403
    assert main_client.post("/api/receivables/admin/sync", headers=users["staff"]).status_code == 403
    assert main_client.post("/api/receivables/admin/sync").status_code == 401
    st = main_client.get("/api/receivables/admin/sync-status", headers=users["admin"]).json()
    assert "profiles_missing" in st and st["background_sync_enabled"] is False
    charges0 = db.query(ReceivableCharge).count()
    r = main_client.post("/api/receivables/admin/sync", headers=users["admin"])
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(ReceivableProfile).filter_by(member_id=99).count() == 1          # 명시적 실행에서만 프로필 생성
    assert db.query(ReceivableCharge).count() >= charges0                            # 과거 부과를 삭제하지 않는다


# ───────────────────── ② 로그인·직원 수납·관리자 정정 ─────────────────────
def test_login_and_roles_end_to_end(db, users, main_client):
    ok = main_client.post("/api/auth/login", data={"username": "staff1", "password": "pw-staff1"})
    assert ok.status_code == 200 and ok.json()["access_token"]
    assert main_client.post("/api/auth/login", data={"username": "staff1", "password": "wrong"}).status_code == 401
    assert main_client.post("/api/auth/login", data={"username": "nobody", "password": "x"}).status_code == 401
    staff = {"Authorization": "Bearer " + ok.json()["access_token"]}
    adm = {"Authorization": "Bearer " + main_client.post("/api/auth/login", data={"username": "admin1", "password": "pw-admin1"}).json()["access_token"]}

    # 직원: 수납 입력·연락기록·통장 업로드 가능
    pay = main_client.post("/api/receivables/members/2/payments", headers=staff,
                           json={"payment_date": "2026-10-10", "amount": 10000, "method": "현금", "memo": "직원 수납"})
    assert pay.status_code == 200, pay.text
    assert main_client.post("/api/receivables/members/2/contacts", headers=staff,
                            json={"contact_date": "2026-10-10", "contact_method": "전화", "status": "연락완료", "memo": "x"}).status_code == 200
    assert main_client.post("/api/receivables/imports/preview", headers=staff, data={"source_type": "통장"},
                            files={"file": ("a.csv", "거래일자,입금금액,거래기록사항\n2026/10/10,1000,홍길동\n".encode("utf-8"))}).status_code != 403
    # 직원: 수납으로 '금액수정'을 우회 입력할 수 없다(실제 입금만)
    fake = main_client.post("/api/receivables/members/2/payments", headers=staff,
                            json={"payment_date": "2026-10-10", "amount": 1000, "method": "잔액수정", "memo": "우회"})
    assert fake.status_code == 400
    # 직원: 미수금 임의 변경·정정·reconcile·되돌리기·동기화는 403
    for method, url, body in (
        ("PATCH", "/api/receivables/members/2/balance", {"balance_type": "미수금", "amount": 1, "reason": "x"}),
        ("POST", "/api/receivables/adjustments/unissued-management/apply", {"plan_digest": "0" * 64, "confirm": "x"}),
        ("POST", "/api/receivables/adjustments/batches/ADJ1/void", {"confirm": "x", "reason": "xx"}),
        ("POST", "/api/receivables/admin/sync", None),
    ):
        r = main_client.request(method, url, headers=staff, json=body)
        assert r.status_code == 403, (url, r.status_code)
    # 관리자: 금액수정은 가능하고, 가짜 수납을 만들지 않는다
    pay_rows = db.query(ReceivablePayment).count()
    r = main_client.patch("/api/receivables/members/3/balance", headers=adm, json={"balance_type": "미수금", "amount": 100000, "reason": "관리자 정정"})
    assert r.status_code == 200, r.text
    db.expire_all(); assert db.query(ReceivablePayment).count() == pay_rows


def test_deny_by_default_for_new_write_routes(db, users):
    """새로 추가된 변경 API는 표시(@staff_write_ok)가 없으면 자동으로 관리자 전용이다."""
    rt = APIRouter(dependencies=[Depends(admin_for_writes)])

    @rt.post("/new-write")
    def new_write():
        return {"ok": True}

    @rt.post("/daily-input")
    @staff_write_ok
    def daily_input():
        return {"ok": True}

    app = FastAPI(); app.include_router(rt)
    c = TestClient(app)
    assert c.post("/new-write").status_code == 401
    assert c.post("/new-write", headers=users["staff"]).status_code == 403
    assert c.post("/new-write", headers=users["admin"]).status_code == 200
    assert c.post("/daily-input", headers=users["staff"]).status_code == 200


def test_staff_allowlist_is_exactly_the_approved_routes():
    import inspect
    approved = {"add_payment", "cancel_payment", "add_contact", "update_account", "preview_payment_import", "match_import_row", "post_payment_import"}
    found = {name for name, fn in vars(R).items()
             if inspect.isfunction(fn) and fn.__module__ == R.__name__ and getattr(fn, "_staff_write_ok", False) is True}
    assert found == approved, found ^ approved


# ───────────────────── ③ SECRET_KEY ─────────────────────
def test_secret_key_resolution_rules(monkeypatch):
    for k in ("SECRET_KEY", "RAILWAY_ENVIRONMENT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        auth._resolve_secret_key()                                              # 운영 + 미설정 → 시작 거부
    monkeypatch.setenv("SECRET_KEY", "change-this-secret-key-in-production-2024")
    with pytest.raises(RuntimeError):
        auth._resolve_secret_key()                                              # 운영 + 예전 기본값 → 시작 거부
    monkeypatch.setenv("SECRET_KEY", "k" * 48)
    assert auth._resolve_secret_key() == "k" * 48                               # 운영 + 정상 키
    monkeypatch.delenv("RAILWAY_ENVIRONMENT")
    monkeypatch.delenv("SECRET_KEY")
    a, b = auth._resolve_secret_key(), auth._resolve_secret_key()
    assert a != b and len(a) >= 32 and "change-this" not in a                   # 개발: 고정값이 아닌 무작위 임시키


def test_no_hardcoded_default_secret_in_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in list((root / "app").rglob("*.py")):
        if f.name == "auth.py":
            continue
        assert "change-this-secret-key" not in f.read_text(encoding="utf-8", errors="replace"), f


def test_security_status_endpoint_never_leaks_secret(db, users, main_client, monkeypatch):
    canary = "CANARY-SECRET-" + "x" * 40
    monkeypatch.setenv("SECRET_KEY", canary)
    assert main_client.get("/api/admin/security-status").status_code == 401
    assert main_client.get("/api/admin/security-status", headers=users["staff"]).status_code == 403
    r = main_client.get("/api/admin/security-status", headers=users["admin"])
    assert r.status_code == 200 and r.json()["secret_key_configured"] is True and r.json()["secret_key_length_ok"] is True
    assert canary not in r.text and "CANARY" not in r.text
    assert canary not in str(auth.security_status())


def test_check_script_hides_value(monkeypatch, capsys):
    monkeypatch.setenv("SECRET_KEY", "CANARY-SECRET-" + "y" * 40)
    from scripts import check_secret_key
    code = check_secret_key.main()
    out = capsys.readouterr().out
    assert code == 0 and "CANARY" not in out and '"ok": true' in out
