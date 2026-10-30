"""No DB dependencies: source-level regression checks where runtime dependencies are unavailable."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_member_creation_and_edit_reject_taekbae_agents():
    code = (ROOT/'app/routers/members.py').read_text(encoding='utf-8')
    assert 'data.get("category") == "택배"' in code
    assert 'effective_category == "택배"' in code
    assert '대리인 정보를 등록하거나 변경할 수 없습니다.' in code

def test_history_response_masks_rrn():
    code = (ROOT/'app/services/agent_history.py').read_text(encoding='utf-8')
    assert '"resident_number": mask_agent_rrn(r.resident_number)' in code

def test_transfer_form_features_present():
    code = (ROOT/'app/static/app.js').read_text(encoding='utf-8')
    assert '대리인 있음' in code
    assert 'agentFormSection' in code
    assert '예: 22.포터Ⅱ냉동탑차' in code
