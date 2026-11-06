"""연락처 2개 수정·(대) 이전·복사 UI 안전성 검증. 실제 DB 쓰기 없음."""
import json
import subprocess
from pathlib import Path


def test_phone_edit_and_agent_confirmation(tmp_path):
    script = (Path(__file__).parents[1] / 'app/static/app.js').read_text('utf-8')
    helper = script[script.index('// 회원 수정: 다중 연락처'):script.index('// ===== 연락처 복수 번호 분리 표시')]
    phone = script[script.index('// ===== 연락처 복수 번호 분리 표시'):script.index('// ===== 데이터 정합성 점검')]
    js = '''
const e_=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fv=s=>s||'-';
const _autoBizNo=s=>s;
const fph=(name,label,val)=>'<input name="'+name+'" value="'+val+'">';
const window={confirm:()=>true};
const warnings=[];const toast=(...args)=>warnings.push(args);
''' + helper + phone + '''
function fakeForm(values, marked, name, agentMobile){
  const inputs=values.map((v,i)=>({value:v,name:'mobile_part_'+(i+1),focus(){}}));
  const check=marked===null?null:{checked:marked>=0,dataset:{markedIndex:String(marked)}};
  const box={querySelectorAll:s=>inputs,querySelector:s=>s==='[data-confirm-agent-move]'?check:null};
  return {querySelector:s=>s==='[data-mobile-editor]'?box:null,inputs};
}
const orig='010-6748-0662 010-5365-4008(대)';
const html=_memberMobileEditFields(orig,true);
const untouched={agent_name:'',agent_mobile:''};
const untouchedResult=_collectMemberMobileEdit(fakeForm(['010-6748-0662','010-5365-4008'],-1),untouched,{mobile:orig});
const noName={agent_name:'',agent_mobile:''};
const noNameResult=_collectMemberMobileEdit(fakeForm(['010-6748-0662','010-5365-4008'],1),noName,{mobile:orig});
const moved={agent_name:'가상대리인',agent_mobile:''};
const movedResult=_collectMemberMobileEdit(fakeForm(['010-6748-0662','010-5365-4008'],1),moved,{mobile:orig});
const conflict={agent_name:'가상대리인',agent_mobile:'010-9999-0000'};
const conflictResult=_collectMemberMobileEdit(fakeForm(['010-6748-0662','010-5365-4008'],1),conflict,{mobile:orig});
const detailSingle=_phoneDetailValue('핸드폰','010-1111-2222',false);
const detailMulti=_phoneDetailValue('핸드폰 1','010-1111-2222',true);
console.log(JSON.stringify({html,untouchedResult,untouched,noNameResult,movedResult,moved,conflictResult,conflict,detailSingle,detailMulti}));
'''
    path = tmp_path / 'contact_ui.cjs'
    result = tmp_path / 'result.json'
    path.write_text(js, 'utf-8')
    with result.open('wb') as f:
        proc = subprocess.run(['node', str(path)], stdout=f, stderr=subprocess.PIPE)
    assert proc.returncode == 0, proc.stderr.decode('utf-8', errors='replace')
    v = json.loads(result.read_text('utf-8'))
    assert 'mobile_part_1' in v['html'] and 'mobile_part_2' in v['html']
    assert '대리인 핸드폰으로 이동' in v['html']
    assert v['untouchedResult'] and v['untouched']['mobile'] == '010-6748-0662 010-5365-4008(대)'
    assert not v['noNameResult'] and not v['conflictResult']
    assert v['movedResult'] and v['moved']['mobile'] == '010-6748-0662'
    assert v['moved']['agent_mobile'] == '010-5365-4008'
    assert 'data-copy-phone' in v['detailSingle'] and 'data-copy-phone' in v['detailMulti']
    assert '>복사</button>' not in v['detailSingle'] and '>복사</button>' not in v['detailMulti']


def test_existing_relink_modal_rechecks_target_before_writing():
    code = (Path(__file__).parents[1] / 'app/static/certificate-ledger.js').read_text('utf-8')
    assert "sourceCert !== cleanCert(it.certificate_number)" in code
    assert "sourceVehicle !== cleanVehicle(it.vehicle_number)" in code
    assert '/api/admin/data-corrections/certificate/preview' in code
    assert 'if (!preview.writes_enabled)' in code
    assert 'data-relink-pick' in code
