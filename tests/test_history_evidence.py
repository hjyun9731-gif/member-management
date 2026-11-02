import datetime as dt
import json
from types import SimpleNamespace
import pytest
from app.services.history_evidence import classify_record, address_layout_proof
from app.services.change_judgement import NORMAL, FIRST, NOTATION, REVIEW, view_row
from scripts.export_history_readonly import sanitize_raw, normalize_url, capture, TABLE_FIELDS
from scripts.reconcile_history_snapshot import counters, admin_metrics, compare_months
from scripts.history_reconcile import match_rows_semantic


def row(**kw):
    r = dict(id=1, change_type="이전전출", name="검증대상", vehicle_number="강원80자1234호",
             before_value="이전전출 춘천", after_value="경기", change_date="2026.10.01", receipt_date="2026.10.02",
             memo="", region="춘천시", raw_data={}, judgement=None, judge_reason=None, deleted_at=None)
    return {**r, **kw}


@pytest.mark.parametrize("ct", ["이전전출", "등록이관"])
def test_route_before_and_explicit_memo_are_evidence_not_first_registration(ct):
    a = classify_record(row(change_type=ct, before_value=ct + " 춘천"))
    b = classify_record(row(change_type=ct, before_value="-", after_value="기존 주소 101호", memo=ct + " 춘천->경기"))
    assert a.judgement == b.judgement == NORMAL
    assert a.report_decision == b.report_decision == "include"
    assert a.evidence["route"] == b.evidence["route"] == ["춘천", "경기"]


@pytest.mark.parametrize("memo", ["", "회원정보 수정 자동기록"])
def test_unknown_route_or_same_region_not_guessed(memo):
    d = classify_record(row(before_value="-", after_value="101호", memo=memo))
    assert d.report_decision == "hold" and d.queue == "DB_EVENT"
    assert classify_record(row(after_value="춘천")).report_decision == "hold"


def test_address_unit_difference_is_not_automatically_excluded():
    r = row(change_type="주소지변경", before_value="중앙로 1, 101호", after_value="102호")
    d = classify_record(r)
    assert d.report_decision == "hold" and d.queue == "DB_ADDRESS_COLUMNS"
    assert classify_record({**r, "raw_data": {"주소": r["before_value"], "상세주소": r["after_value"]}}).judgement == FIRST
    assert address_layout_proof(r["before_value"], r["after_value"], {"변경전": r["before_value"], "변경후": r["after_value"]}) is None
    assert address_layout_proof(r["before_value"], r["after_value"], {"주소": r["before_value"], "Unnamed:9": r["after_value"]}) is None


def test_address_move_requires_recorded_reason_not_only_strings():
    r = row(change_type="주소지변경", before_value="중앙로 1", after_value="문화로 5")
    assert classify_record(r).report_decision == "hold"
    assert classify_record({**r, "memo": "실제 주소 이전"}).report_decision == "include"
    assert classify_record({**r, "raw_data": {"change_reasons": {"address": "actual"}}}).report_decision == "include"
    assert classify_record({**r, "raw_data": {"change_reasons": {"address": {"invalid": True}}}}).report_decision == "hold"


def test_company_business_event_and_person_name_are_distinguished():
    r = row(change_type="상호변경", before_value="용달화물", after_value="검증이사", memo="상호변경")
    assert classify_record(r).report_decision == "include"
    assert classify_record({**r, "before_value": r["name"]}).report_decision == "hold"
    assert classify_record({**r, "before_value": r["name"] + "(택배)"}).report_decision == "hold"
    assert classify_record({**r, "memo": "회원정보 수정 자동기록"}).report_decision == "hold"


@pytest.mark.parametrize("ct,memo,before,after", [
    ("구조변경", "구조변경", "카고", "내장탑"),
    ("전속계약 업체변경", "전속계약 업체변경", "업체A", "업체B"),
    ("상속", "자녀에게 상속(사망)", None, None),
    ("운행정지", "운행정지명령등록", None, None),
    ("업종변경", "변경허가", "용달화물", None),
])
def test_named_business_events_do_not_require_meaningless_old_new_values(ct, memo, before, after):
    d = classify_record(row(change_type=ct, memo=memo, before_value=before, after_value=after))
    assert d.report_decision == "include"


def test_number_loss_is_not_vehicle_replacement_and_deregistration_is_not_closure():
    r = row(change_type="번호변경", before_value="80자1234", after_value="80자5678", memo="번호판분실")
    assert classify_record(r).report_decision == "include"
    v = view_row(SimpleNamespace(**r))
    assert v.bucket == "차량번호변경"
    assert classify_record(row(change_type="말소/폐차", memo="번호변경말소")).report_decision == "exclude"
    d = classify_record(row(change_type="말소/폐차", memo="자진말소(폐차)"))
    assert d.report_decision == "include" and d.evidence["business_closure_inclusion"] is False


def test_short_first_plate_and_formatted_phone_are_excluded():
    assert classify_record(row(change_type="차량번호변경", before_value="-", after_value="82자2415")).judgement == FIRST
    d = classify_record(row(change_type="연락처변경", before_value="010-1234-5678(본인)", after_value="01012345678"))
    assert d.judgement == NOTATION and d.report_decision == "exclude"


def test_date_identity_and_originals_are_preserved():
    r = row(change_date="잘못된날짜", receipt_date=None)
    copy = dict(r)
    assert classify_record(r).report_decision == "hold" and r == copy
    assert classify_record(row(name=None)).queue == "DB_IDENTITY"


def test_export_url_and_raw_data_do_not_leak_credentials_or_unrelated_identity(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    with pytest.raises(ValueError): normalize_url("postgresql://u:p@postgres.railway.internal/db")
    assert normalize_url("postgresql+psycopg2://u:p@localhost:5432/db").startswith("postgresql://")
    data = sanitize_raw({"주소": "중앙로 1", "상세주소": "101호", "주민등록번호": "hidden",
                         "DATABASE_URL": "secret", "change_reasons": {"address": "actual"}})
    assert set(data) == {"주소", "상세주소", "change_reasons"}


class FakeCursor:
    def __init__(self, conn): self.conn = conn; self.result = []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, args=None):
        self.conn.sql.append(sql)
        assert not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"))
        if sql.startswith("SELECT current_setting"):
            self.result = [(self.conn.mode, "repeatable read")]
        elif sql.startswith("SELECT table_name"):
            self.result = [("change_history", k) for k in TABLE_FIELDS["change_history"]]
        elif 'FROM "change_history"' in sql:
            fields = TABLE_FIELDS["change_history"]
            self.result = [tuple(row().get(k) for k in fields)]
        elif sql == "SHOW transaction_read_only": self.result = [(self.conn.mode,)]
        else: self.result = []
    def fetchone(self): return self.result[0]
    def fetchall(self): return self.result


class FakeConnection:
    def __init__(self, mode="on"): self.mode = mode; self.sql = []; self.rollback_called = False
    def set_session(self, **kw):
        assert kw == {"readonly": True, "isolation_level": "REPEATABLE READ", "autocommit": False}
    def cursor(self): return FakeCursor(self)
    def rollback(self): self.rollback_called = True


def test_export_checks_read_only_and_rolls_back_even_on_failure():
    conn = FakeConnection(); snapshot = capture(conn)
    assert snapshot["read_only_verified"] and snapshot["db_writes"] == 0 and conn.rollback_called
    assert snapshot["tables"]["change_history"][0]["id"] == 1
    unsafe = FakeConnection("off")
    with pytest.raises(RuntimeError): capture(unsafe)
    assert unsafe.rollback_called


def test_db_monthly_comparison_replays_legacy_structure_merge_and_new_separation():
    rows = [row(change_type="구조변경", before_value="카고", after_value="내장탑", memo="구조변경"),
            row(id=2, change_type="등록이관", before_value="등록이관 춘천")]
    before, after = counters(rows, legacy=True), counters(rows)
    b, a = admin_metrics(before["2026-10"], True), admin_metrics(after["2026-10"])
    assert b["차량변경"] == 1 and a["차량변경"] == 0 and a["구조변경"] == 1
    assert b["이전전출"] == 1 and a["이전전출"] == 0 and a["등록이관"] == 1
    assert len(compare_months(before, after, after, "TEST_SNAPSHOT")) > 0


def test_snapshot_pipeline_uses_returned_ids_and_preserves_originals(monkeypatch):
    import copy
    from app.services.change_judgement import source_hash, bucket_of
    from scripts import reconcile_history_snapshot as pipeline
    source = [{**row(), "source_row": 2}]
    database = [{**row(), "id": 901}]
    original = copy.deepcopy(database)
    def review(records, drafts, policy):
        output = []
        for r in records:
            d = classify_record(r)
            output.append({**r, "report_decision": "hold" if policy == "conservative-v1" else d.report_decision,
                "proposed_judgement": REVIEW if policy == "conservative-v1" else d.judgement,
                "review_status": "pending" if policy == "conservative-v1" else "automatic",
                "review_reason": d.reason, "work_queue": d.queue, "basis_date": "2026-10-01",
                "report_bucket": bucket_of(r["change_type"])})
        return output
    monkeypatch.setattr(pipeline, "review_rows", review)
    snapshot = {"format_version": 2, "read_only_verified": True, "tables": {"change_history": database}}
    result = pipeline.build(source, {}, snapshot)
    assert result["plan"][0]["db_id"] == 901 and result["plan"][0]["db_ready"]
    assert result["summary"]["actual_db_status"] == "SNAPSHOT_RECEIVED"
    assert result["summary"]["resolved_previous_hold"] == 1
    assert database == original
    snapshot["tables"]["change_history_decisions"] = [{"id": 77, "history_id": 901,
        "judgement": "실적제외", "status": "confirmed", "report_decision": "exclude", "reason": "기존 명시 판정",
        "source_hash": source_hash(database[0])}]
    result = pipeline.build(source, {}, snapshot)
    total = next(x for x in result["db_monthly"] if x["metric"] == "_변경등록전체")
    assert total["before_main_rule"] == 1 and total["after_review_proposal"] == 0
    assert result["existing_decision_conflicts"][0]["existing_decision_id"] == 77
    snapshot["read_only_verified"] = False
    with pytest.raises(ValueError): pipeline.build(source, {}, snapshot)


@pytest.mark.parametrize("changes", [
    {"change_type": "번호변경"}, {"change_date": "26.10.01"},
    {"vehicle_number": "강원 80자 1234"},
])
def test_only_known_equivalent_formats_can_link_an_existing_db_id(changes):
    from app.services.change_judgement import source_hash
    source = [{**row(change_type="차량번호변경"), "source_row": 2}]
    actual = [{**source[0], **changes, "id": 901}]
    result = match_rows_semantic(source, actual)[0]
    assert result["match_status"] == "MATCH_CANONICAL" and result["db_id"] == 901
    assert result["expected_db_source_hash"] == source_hash(actual[0])
    assert result["normalizations_applied"] == list(changes)


def test_canonical_matching_never_removes_address_room_or_collapses_many_rows():
    source = [{**row(change_type="주소지변경", before_value="1호"), "source_row": 2}]
    assert match_rows_semantic(source, [{**source[0], "id": 901, "before_value": "1"}])[0]["match_status"] == "MISSING"
    actual = [{**source[0], "id": 901, "change_type": "주소변경"},
              {**source[0], "id": 902, "change_type": "주소변경"}]
    assert match_rows_semantic(source, actual)[0]["match_status"] == "CANONICAL_AMBIGUOUS"
