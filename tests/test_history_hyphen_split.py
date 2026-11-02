"""주소 하이픈 분리 저장(원본 단일 셀을 변경전/변경후로 쪼갠 기록) 판정 회귀 테스트."""
import importlib.util
from pathlib import Path

from app.services.change_judgement import FIRST, REVIEW, NORMAL
from app.services.history_evidence import classify_record, hyphen_split_proof


def row(before, after, raw, ct="주소지변경", memo=None):
    return {"change_type": ct, "before_value": before, "after_value": after, "name": "홍길동", "vehicle_number": "강원80자 1234호",
            "change_date": "2018.01.03", "receipt_date": "2018.01.03", "memo": memo, "raw_data": raw}


def test_split_address_is_first_registration_not_move():
    d = classify_record(row("연립길 13, 가", "301", {"변 경 내 용": "연립길 13, 가-301"}))
    assert (d.judgement, d.report_decision, d.rule) == (FIRST, "exclude", "ADDRESS_HYPHEN_SPLIT_PROOF")


def test_full_address_halves_are_also_proven_when_cell_matches():
    d = classify_record(row("효자남길 8", "7 , 201동 201호", {"변 경 내 용": "효자남길 8-7 , 201동 201호"}))
    assert d.rule == "ADDRESS_HYPHEN_SPLIT_PROOF" and d.report_decision == "exclude"


def test_real_before_after_not_matching_cell_is_not_auto_excluded():
    d = classify_record(row("춘주로 1", "안현로 274, 101-902", {"변 경 내 용": "안현로 274, 101-902"}))
    assert d.report_decision == "hold" and d.judgement == REVIEW


def test_member_auto_log_without_cell_stays_on_hold():
    d = classify_record(row("새들길 115", "안현로 274", {"source": "member_auto_log"}, memo="회원정보 수정 자동기록"))
    assert d.report_decision == "hold"


def test_proof_requires_both_sides_and_exact_reconstruction():
    assert hyphen_split_proof("A 1", "", {"변 경 내 용": "A 1-"}) is None
    assert hyphen_split_proof("A 1", "2", {"변 경 내 용": "A 1-3"}) is None
    assert hyphen_split_proof("A 1", "2", None) is None
    assert hyphen_split_proof("A 1", "2", {"변 경 내 용": "A 1-2"})


def test_explicit_move_reason_still_counts_when_not_split():
    d = classify_record(row("가길 1", "나길 2", {"비고": "주사무소 변경"}, memo="주사무소 변경"))
    assert d.judgement == NORMAL and d.report_decision == "include"


def _load():
    p = Path(__file__).resolve().parents[1] / "scripts" / "final_adjudication.py"
    s = importlib.util.spec_from_file_location("final_adj", p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def test_build_rejects_unmapped_and_keeps_all_originals(tmp_path):
    fa = _load()
    snap = {"tables": {"change_history": [
        {"id": 1, "change_type": "주소지변경", "region": "춘천시", "vehicle_number": "강원80자 1호", "name": "가", "before_value": "-", "after_value": "x로 1",
         "change_date": "2020.01.01", "receipt_date": None, "memo": None, "judgement": None, "judge_reason": None, "deleted_at": None, "raw_data": {"변 경 내 용": "x로 1"}},
        {"id": 2, "change_type": "주소지변경", "region": "춘천시", "vehicle_number": "강원80자 2호", "name": "나", "before_value": "x로 1", "after_value": "3",
         "change_date": "2020.02.01", "receipt_date": None, "memo": None, "judgement": None, "judge_reason": None, "deleted_at": None, "raw_data": {"변 경 내 용": "x로 1-3"}}]}}
    mapping = [(2, 1, "주소지변경", "춘천시", "가", "강원80자 1호"), (3, 2, "주소지변경", "춘천시", "나", "강원80자 2호")]
    class Old:  # 테스트에서는 기존 집계 모듈 불필요
        pass
    items, problems, _, _ = fa.build(snap, mapping, Old)
    assert len(items) == 2 and [i["id"] for i in items] == [1, 2]
    assert all(i["rep"] == "exclude" for i in items) and any("구조변경 순번" in p for p in problems)


def test_hyphen_proof_requires_numeric_detail_on_right():
    # 오른쪽이 독립 주소처럼 한글로 시작하면 하이픈 분리로 단정하지 않는다
    assert hyphen_split_proof("춘주로 1", "안현로 274", {"변 경 내 용": "춘주로 1-안현로 274"}) is None
    assert hyphen_split_proof("춘주로 1", "2 (가동)", {"변 경 내 용": "춘주로 1-2 (가동)"})


def test_pending_by_type_counts_only_review():
    from types import SimpleNamespace as S
    from app.routers.dashboard import _pending_by_type
    rows = [(S(change_type="주소지변경"), S(judgement="확인필요")), (S(change_type="주소지변경"), S(judgement="최초등록")),
            (S(change_type=None), S(judgement="확인필요"))]
    assert _pending_by_type(rows) == {"주소지변경": 1, "기타": 1}
