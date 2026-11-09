from pathlib import Path


def test_similar_record_check_is_automatic_and_has_no_manual_button():
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert "관리번호 (공문)" not in source
    assert "similar-check-manual" not in source
    assert "기존 개인·택배회원 / 예정자 중복 확인" not in source
    assert "input.addEventListener('input'" in source
    assert "input.addEventListener('change'" in source
    assert "Boolean(p.name && p.approval_date && p.region)" in source
    assert "아니요, 동명이인입니다 · 계속 입력" in source
    assert "similarDifferent').onclick=()=>finish('different')" in source
    assert "네, 같은 사람입니다 · 기존 기록 열기" in source


def test_static_cache_version_changes_with_popup_ui():
    root = Path(__file__).resolve().parents[1]
    index = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    worker = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    assert "v=20261109-management-number-restore" in index
    assert "assoc-shell-v4-20261109-management-number-restore" in worker
