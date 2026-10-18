"""자격증명발급번호 비교 규칙 단위 테스트 (SQLAlchemy/FastAPI 없이 실행 가능).

실행:  python -m pytest -q tests/test_certificate_number_rules.py
또는:  python tests/test_certificate_number_rules.py
"""
import ast
import importlib.util
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_rules():
    path = os.path.join(ROOT, "app", "services", "certificate_number_rules.py")
    spec = importlib.util.spec_from_file_location("certificate_number_rules_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load_rules()


# ── 정규화: 26-085 와 26-85 는 같은 번호 ─────────────────────────────
def test_leading_zero_is_same_number():
    assert R.canonical_number("26-085") == R.canonical_number("26-85") == "26-85"
    assert R.canonical_number("26-0085") == "26-85"
    assert R.number_key("26-085") == R.number_key("26-85") == (26, 85)


def test_other_notations_are_same_number():
    for raw in ["2026-0425", " 26 - 425 ", "26–425", "26－425", "２６-４２５", "26-425(강동규)", "26-0425 (홍길동)"]:
        assert R.canonical_number(raw) == "26-425", raw


def test_non_numbers_are_not_checked():
    for raw in [None, "", "  ", "메모성 문자열", "26-", "-425", "26-4a5", "123-4"]:
        assert R.canonical_number(raw) == "", raw


def test_different_numbers_stay_different():
    assert R.canonical_number("26-425") != R.canonical_number("26-426")
    assert R.canonical_number("26-85") != R.canonical_number("25-85")
    assert R.canonical_number("26-85") != R.canonical_number("26-850")


def test_sequence_digits_cover_every_storage_form():
    digits = R.sequence_digits("26-085")
    assert digits == "85"
    for stored in ["26-85", "26-085", "26-0085", "26 – 85", "26-85(홍길동)"]:
        assert digits in stored


# ── crud.py 원본 구현과 같은 결과인지 (소스를 읽어 직접 비교) ─────────────
def _crud_normalize():
    src = open(os.path.join(ROOT, "app", "crud.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in ("_CERT_NUMBER_RE", "_CERT_HYPHENS") for t in node.targets):
            wanted.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_certificate_number":
            wanted.append(node)
    assert len(wanted) == 3, "crud.py에서 normalize_certificate_number를 찾지 못함"
    ns = {"re": re, "unicodedata": unicodedata}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "crud_extract", "exec"), ns)
    return ns["normalize_certificate_number"]


def test_same_rule_as_crud_normalize():
    crud_norm = _crud_normalize()
    samples = ["26-085", "26-85", "2026-0085", " 26 - 425 ", "26–425", "26－425", "２６-４２５", "26-425(강동규)",
               "", None, "abc", "26-", "26-0", "26-000", "99-999999", "100-5", "26-1234567", "26 85", "26-85 (a)(b)"]
    for s in samples:
        assert R.canonical_number(s) == crud_norm(s), repr(s)


# ── 같은 대상 판단 ─────────────────────────────────────────────────
def test_same_subject_needs_name_and_vehicle():
    assert R.same_subject("홍길동", "강원80자 1234", "홍길동", "강원80자1234")
    assert not R.same_subject("홍길동", "강원80자1234", "홍길동", "강원80자9999")
    assert not R.same_subject("홍길동", "", "홍길동", "")
    assert not R.same_subject("", "강원80자1234", "", "강원80자1234")


def _ctx(**kw):
    c = R.SubjectContext()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_new_registration_collides_with_existing_member():
    hits = [{"source": "license_holders", "id": 7, "name": "김기존", "vehicle_number": "강원80자1111", "region": "춘천시"}]
    got = R.conflicts(hits, _ctx(name="새사람", vehicle_number="강원80자2222"))
    assert len(got) == 1
    msg = R.duplicate_message("26-425", got)
    assert msg.startswith("이미 등록된 자격증명발급번호입니다.")
    assert "26-425" in msg and "회원 김기존 / 강원80자1111 / 춘천시" in msg


def test_editing_own_record_is_not_duplicate():
    hits = [{"source": "candidates", "id": 3, "name": "홍길동", "vehicle_number": "강원80자1234"}]
    assert R.conflicts(hits, _ctx(candidate_ids={3})) == []


def test_candidate_and_converted_member_are_same_person():
    hits = [
        {"source": "candidates", "id": 3, "name": "홍길동", "vehicle_number": "강원80자1234", "member_id": 9},
        {"source": "license_holders", "id": 9, "name": "홍길동", "vehicle_number": "강원80자1234", "candidate_id": 3},
        {"source": "certificate_ledger", "id": 1, "name": "홍길동", "vehicle_number": "강원80자1234", "candidate_id": 3, "member_id": 9},
    ]
    assert R.conflicts(hits, _ctx(candidate_ids={3}, member_ids={9})) == []
    # 회원 쪽 id만 알아도 (연결 확장 전이라도) 후보/대장 행은 member_id 로 이어진다
    assert R.conflicts(hits, _ctx(member_ids={9}, candidate_ids={3})) == []


def test_transfer_chain_is_not_duplicate_but_stranger_is():
    hits = [
        {"source": "license_holders", "id": 1, "name": "양도자", "vehicle_number": "강원80자1111"},
        {"source": "closures", "id": 5, "name": "양도자", "vehicle_number": "강원80자1111", "member_id": 1, "transferee_member_id": 2},
    ]
    # 양수자(회원 2) 저장: 양도자(회원 1)가 연결 확장으로 member_ids 에 들어 있으면 중복 아님
    assert R.conflicts(hits, _ctx(member_ids={1, 2}, name="양수자", vehicle_number="강원80자1111")) == []
    # 전혀 무관한 신규 등록은 중복
    assert len(R.conflicts(hits, _ctx(name="타인", vehicle_number="강원80자7777"))) == 2


def test_unlinked_reservation_row_rules():
    reserved_same_vehicle = {"source": "certificate_ledger", "id": 1, "name": "홍길동", "vehicle_number": "강원80자1234"}
    reserved_nobody = {"source": "certificate_ledger", "id": 2, "name": R.UNASSIGNED_NAME, "vehicle_number": ""}
    other = {"source": "certificate_ledger", "id": 3, "name": "남의것", "vehicle_number": "강원80자0000"}
    ctx = _ctx(name="홍길동", vehicle_number="강원80자1234")
    assert R.conflicts([reserved_same_vehicle, reserved_nobody], ctx) == []
    assert R.conflicts([other], ctx) == [other]
    # 예약 뒤 성명 오타만 고친 경우(차량번호는 같음)에도 막지 않는다
    assert R.conflicts([reserved_same_vehicle], _ctx(name="홍길둥", vehicle_number="강원80자1234")) == []


def test_linked_ledger_row_of_someone_else_is_duplicate():
    row = {"source": "certificate_ledger", "id": 4, "name": "다른사람", "vehicle_number": "강원81배9999", "candidate_id": 50, "member_id": None}
    assert len(R.conflicts([row], _ctx(name="새사람", vehicle_number="강원80자1"))) == 1


def test_group_subjects_for_existing_duplicate_report():
    hits = [
        {"source": "candidates", "id": 3, "name": "A", "vehicle_number": "v1", "member_id": 9},
        {"source": "license_holders", "id": 9, "name": "A", "vehicle_number": "v1", "candidate_id": 3},
        {"source": "license_holders", "id": 20, "name": "B", "vehicle_number": "v2", "candidate_id": None},
    ]
    groups = R.group_subjects(hits)
    assert sorted(len(g) for g in groups) == [1, 2]
    same_person_only = hits[:2]
    assert len(R.group_subjects(same_person_only)) == 1


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print("FAIL", name, "->", repr(exc))
    sys.exit(1 if failed else 0)
