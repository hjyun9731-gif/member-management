from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "app.js"


def test_candidate_entry_has_no_manual_management_number_or_approval_date():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function renderCandidateSection(){")
    end = source.index("window.editCandidate=async(id)=>{", start)
    form = source[start:end]
    # The initial candidate-entry form is the first candForm block only.
    form = form.split("// 포맷 이벤트 바인딩", 1)[0]
    assert "fi('management_number'" not in form
    assert "fi('approval_date'" not in form
    assert "similar-check-manual" not in form
    assert "fi('vehicle_number','차량번호')" in form
    assert "fi('name','성명')" in form


def test_candidate_registration_omits_management_number_and_uses_server_auto_number():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("window.registerCandidate=async(cid,vn,name)=>{")
    end = source.index("window.deleteCandidate=async(cid)=>{", start)
    flow = source[start:end]
    assert "regMgmtNum" not in flow
    assert "management_number:" not in flow
    assert "approval_date:ad" in flow
    assert "api('POST',`/api/candidates/${cid}/register`" in flow
    # 등록 팝업에 일반 입력폼의 중복검사 헬퍼가 없으므로, 별도 확인 없이 검사했다고 주장하지 않는다.


def test_transfer_entry_has_no_manual_management_number():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function renderTransferSection(){")
    end = source.index("// ===== MEMBER (개인/택배) =====", start)
    flow = source[start:end]
    assert "fi('management_number'" not in flow
    assert "fd.management_number" not in flow
    assert "register-member`,{})" in flow


def test_similar_search_uses_only_name_approval_date_and_region():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function _similarPayload(form){")
    end = source.index("function _similarSignature", start)
    payload = source[start:end]
    assert "management_number" not in payload
    assert "name:read('name')" in payload
    assert "approval_date:_dateToISO(read('approval_date'))" in payload
    assert "region:read('region')" in payload


def test_candidate_register_api_generates_number_when_ui_omits_it():
    router = Path(__file__).resolve().parents[1] / "app" / "routers" / "candidates.py"
    source = router.read_text(encoding="utf-8")
    assert "management_number: Optional[str] = None" in source
    assert "mgmt = body.management_number or crud.get_next_new_member_number(db)" in source


def test_static_asset_cache_versions_are_bumped():
    root = Path(__file__).resolve().parents[1]
    index = (root / "app" / "static" / "index.html").read_text(encoding="utf-8")
    sw = (root / "app" / "static" / "sw.js").read_text(encoding="utf-8")
    assert "app.js?v=20261109-management-number-restore" in index
    assert "assoc-shell-v4-20261109-management-number-restore" in sw
