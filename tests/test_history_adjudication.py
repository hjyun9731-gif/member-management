"""새 업무 기준의 경계·동시수정·원본 보존·실제 API/집계/DB 회귀 검증."""
import datetime
import hashlib
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text, inspect
from app import models
from app.database import SessionLocal
from app.services.change_judgement import (
    classify_for_type, view_row, source_hash, NORMAL, REVIEW, FIRST, EXCLUDED, ERROR_FIX,
)
from app.services.history_decisions import append_decision
from scripts.history_reconcile import match_rows, exact_key, read_database
from test_change_judgement import client, _member, _rows, _add_hist

DATE = datetime.date.today().isoformat()


@pytest.mark.parametrize("ct", ["등록이관", "이전전출", "말소/폐차", "상속", "운행정지", "구조변경"])
def test_event_with_blank_before_is_not_first_registration(ct):
    assert classify_for_type(ct, "-", "타지역 전출")[0] == REVIEW


@pytest.mark.parametrize("ct", ["주소지변경", "상호변경", "전속계약 업체변경"])
def test_text_difference_alone_is_not_real_business_change(ct):
    assert classify_for_type(ct, "기존 내용", "새 내용")[0] == REVIEW
    assert classify_for_type(ct, "미상", "새 내용")[0] == FIRST


def test_legacy_unclassified_or_automatic_old_judgement_never_assumed_normal():
    r = SimpleNamespace(change_type="주소지변경", before_value="A", after_value="B", change_date=DATE,
                        receipt_date=None, judgement=NORMAL, judge_reason="내용 변경")
    assert view_row(r).report_decision == "hold"
    r.judgement = None
    assert view_row(r).judgement == REVIEW


def test_roman_model_notation_is_not_replacement_but_year_change_is():
    assert classify_for_type("대폐차", "18.포터Ⅱ", "18.포터II")[0] == "표기정정"
    assert classify_for_type("대폐차", "18.포터Ⅱ", "26.포터II")[0] == NORMAL


def test_plate_reason_blocks_before_any_write_and_correction_is_excluded(client):
    mid = _member(vehicle_number="강원98바3278호", name="기존이름")
    response = client.put(f"/api/members/{mid}", json={"vehicle_number": "강원98바3728호", "name": "새이름"})
    assert response.status_code == 409
    assert client.get(f"/api/members/{mid}").json()["name"] == "기존이름"
    assert _rows(member_id=mid) == []
    response = client.put(f"/api/members/{mid}", json={"vehicle_number": "강원98바3728호", "change_reasons": {"vehicle_number": "correction"}})
    assert response.status_code == 200
    assert _rows(member_id=mid)[0][1] == ERROR_FIX
    assert client.get("/api/change-stats/summary").json()["total"] == 0


def test_address_first_correction_and_pending_are_distinct(client):
    mid = _member(address="-")
    client.put(f"/api/members/{mid}", json={"address": "도로명 1"})
    client.put(f"/api/members/{mid}", json={"address": "도로명 2"})
    client.put(f"/api/members/{mid}", json={"address": "도로명 3", "change_reasons": {"address": "correction"}})
    assert [r[1] for r in _rows(member_id=mid)] == [FIRST, REVIEW, ERROR_FIX]
    items = client.get("/api/change-history").json()["items"]
    assert {r["review_status"] for r in items} == {"automatic", "pending", "confirmed"}
    assert client.get("/api/change-stats/summary").json()["total"] == 0


def test_year_in_vehicle_type_counts_once_and_structure_stays_separate(client):
    mid = _member(vehicle_type="18.포터II", fuel_type="경유")
    assert client.put(f"/api/members/{mid}", json={"vehicle_type": "26.포터II"}).status_code == 200
    assert client.put(f"/api/members/{mid}", json={"fuel_type": "전기", "auto_change_type": "vehicleReplacement"}).status_code == 200
    assert [r[0] for r in _rows(member_id=mid)] == ["대폐차", "구조변경"]
    stats = client.get("/api/change-stats/summary").json()["buckets"]
    assert stats["차량변경(대폐차)"] == 1 and stats["구조변경"] == 1
    now = datetime.date.today()
    admin = client.get(f"/api/dashboard/monthly-report-auto?year={now.year}&month={now.month}").json()["admin_work"]
    assert admin["차량변경"] == 1 and admin["구조변경"] == 1


def _history(client, **data):
    default = dict(change_type="주소지변경", name="가", vehicle_number="강원80자1234호", region="춘천시",
                   before_value="A", after_value="B", change_date=DATE)
    default.update(data)
    r = client.post("/api/change-history", json=default)
    assert r.status_code == 200, r.text
    return r.json()


def _decision(client, r, **changes):
    payload = {"expected_source_hash": r["source_hash"], "expected_decision_id": r["decision_id"],
               "judgement": NORMAL, "report_decision": "include", "reason": "증빙 확인 완료"}
    payload.update(changes)
    return client.post(f'/api/change-history/{r["id"]}/decisions', json=payload)


def test_append_only_decision_concurrency_and_original_preservation(client):
    r = _history(client)
    assert r["report_decision"] == "hold"
    accepted = _decision(client, r)
    assert accepted.status_code == 200
    assert accepted.json()["report_decision"] == "include"
    assert _decision(client, r).status_code == 409  # stale decision ID
    with SessionLocal() as db:
        original = db.get(models.ChangeHistory, r["id"])
        assert original.before_value == "A" and original.after_value == "B" and original.judgement is None
        assert db.query(models.ChangeHistoryDecision).count() == 2
    assert client.put(f'/api/change-history/{r["id"]}', json={"before_value": "overwritten"}).status_code == 409
    assert client.delete(f'/api/change-history/{r["id"]}').status_code == 409
    excluded = _decision(client, accepted.json(), judgement=EXCLUDED, report_decision="exclude", reason="사용자 지정 제외")
    assert excluded.status_code == 200
    assert client.get("/api/change-stats/summary").json()["total"] == 0


def test_structural_exclusion_and_transfer_are_not_address_or_closure(client):
    for ct in ("구조변경", "등록이관", "이전전출", "말소/폐차"):
        r = _history(client, change_type=ct)
        assert _decision(client, r).status_code == 200
    stats = client.get("/api/change-stats/summary").json()["buckets"]
    assert [stats[k] for k in ("구조변경", "등록이관", "이전전출", "말소/폐차")] == [1, 1, 1, 1]
    assert stats["차량변경(대폐차)"] == stats["주소변경"] == 0
    now = datetime.date.today()
    admin = client.get(f"/api/dashboard/monthly-report-auto?year={now.year}&month={now.month}").json()["admin_work"]
    assert admin["퇴사신고"] == 0 and admin["이전전출"] == admin["등록이관"] == 1


def test_replacement_with_plate_values_cannot_be_included(client):
    r = _history(client, change_type="대폐차", before_value="강원80자1234호", after_value="강원80자9876호")
    assert _decision(client, r).status_code == 422


def test_missing_date_cannot_be_included(client):
    r = _history(client, change_date="날짜없음")
    assert _decision(client, r).status_code == 422


def test_duplicate_requires_canonical_id_preserves_both_originals(client):
    a, b = _history(client), _history(client)
    assert _decision(client, b, judgement="중복", report_decision="exclude").status_code == 422
    accepted = _decision(client, b, judgement="중복", report_decision="exclude", duplicate_of_id=a["id"])
    assert accepted.status_code == 200
    assert client.get("/api/change-history").json()["total"] == 2
    assert client.get("/api/change-stats/summary").json()["separate"]["중복"] == 1


def test_failed_decision_rolls_back_member_and_agent_edits(client, monkeypatch):
    from app.routers import members
    mid = _member(vehicle_type="18.포터II")
    def fail(*args, **kwargs):
        raise RuntimeError("simulated audit write failure")
    monkeypatch.setattr(members, "append_decision", fail)
    assert client.put(f"/api/members/{mid}", json={"vehicle_type": "26.포터II", "name": "미반영"}).status_code == 500
    m = client.get(f"/api/members/{mid}").json()
    assert m["vehicle_type"] == "18.포터II" and m["name"] != "미반영" and _rows(member_id=mid) == []


def test_hash_invalidates_stale_decision_without_editing_audit(client):
    r = _history(client, change_reason="actual")
    assert r["report_decision"] == "include"
    with SessionLocal() as db:
        c = db.get(models.ChangeHistory, r["id"]); c.after_value = "concurrent legacy import"; db.commit()
    current = client.get(f'/api/change-history/{r["id"]}').json()
    assert current["report_decision"] == "hold" and current["review_status"] == "pending"


def row(**kw):
    from app.services.change_judgement import SOURCE_FIELDS
    r = dict.fromkeys(SOURCE_FIELDS, "")
    r.update(change_type="주소지변경", name="가", before_value="1호", after_value="2호", source_row=2, deleted_at=None)
    r.update(kw)
    return r


def test_matching_is_exact_one_to_one_and_rejects_deleted_or_ambiguous():
    a = row(); b = row(source_row=3)
    assert match_rows([a, b], [dict(a, id=4)])[0]["match_status"] == "SOURCE_DUPLICATE"
    assert match_rows([a], [dict(a, id=4), dict(a, id=5)])[0]["match_status"] == "AMBIGUOUS"
    assert match_rows([a], [dict(a, id=4, deleted_at="2026-01-01")])[0]["match_status"] == "MISSING"
    assert match_rows([a], [dict(a, id=4, before_value="1")])[0]["match_status"] == "MISSING"
    assert match_rows([a], [dict(a, id=4)])[0]["db_id"] == 4
    with pytest.raises(ValueError): match_rows([a], [dict(a, id=4), dict(a, id=4)])


def test_database_reader_never_migrates_or_updates(tmp_path):
    path = tmp_path / "read_only.db"
    engine = create_engine(f"sqlite:///{path}")
    fields = ",".join(f'"{k}" TEXT' for k in exact_key.__globals__["SOURCE_FIELDS"])
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE change_history (id INTEGER PRIMARY KEY, {fields}, deleted_at TEXT)"))
        conn.execute(text("INSERT INTO change_history (id,name) VALUES (1,'읽기전용')"))
    engine.dispose()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert read_database(f"sqlite:///{path}")[0]["name"] == "읽기전용"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert inspect(create_engine(f"sqlite:///{path}")).get_table_names() == ["change_history"]


def test_saved_manual_history_totals_cannot_hide_current_automatic_result(client):
    now = datetime.date.today()
    with SessionLocal() as db:
        db.add(models.MonthlyReportEntry(year=now.year, month=now.month,
            custom_data={"address_change_month": 99, "_writer": "기존작성자"})); db.commit()
    url = f"/api/reports/monthly-report/full?year={now.year}&month={now.month}"
    result = client.get(url).json()
    field = next(f for f in result["fields"] if f["key"] == "address_change_month")
    assert field["value"] == 0 and field["locked_by_history_policy"]
    assert result["history_override_conflicts"][0]["saved_value"] == 99
    assert client.post(url, json={"values": {"address_change_month": 777, "remarks": "검토 완료"}}).status_code == 200
    with SessionLocal() as db:
        data = db.query(models.MonthlyReportEntry).first().custom_data
        assert data["address_change_month"] == 99 and data["remarks"] == "검토 완료"


def test_structure_correction_and_cleared_address_do_not_count_as_real_change(client):
    mid = _member(address="기존주소", fuel_type="경유")
    assert client.put(f"/api/members/{mid}", json={"fuel_type": "전기", "auto_change_type": "vehicleTypeCorrection"}).status_code == 200
    assert _rows(member_id=mid)[0][1] == ERROR_FIX
    assert client.put(f"/api/members/{mid}", json={"address": "", "change_reasons": {"address": "actual"}}).status_code == 200
    assert _rows(member_id=mid, change_type="주소지변경")[0][1] == REVIEW
    assert client.get("/api/change-stats/summary").json()["total"] == 0
