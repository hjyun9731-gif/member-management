(()=>{
'use strict';
const TABS=[['bank','통장 원장'],['ledger','미수금 원장'],['monthly','월별 부과·수납'],['match','자동매칭 결과'],['review','확인 필요 거래'],['closed','폐업·탈퇴'],['suspense','가수금(미확인 입금)'],['history','수정 이력'],['audit','점검(관리자)'],['adjust','미수금 정정(관리자)']];
const STATUS_OPTS={bank:[['','전체 상태'],['matched','자동확정'],['review','확인필요'],['duplicate','중복제외'],['posted','수납반영']],match:[['','전체'],['matched','자동확정'],['posted','수납반영']],ledger:[['','전체 회원'],['active','활성'],['closed','폐업'],['unpaid','미수(+)'],['credit','초과납(−)']]};
const $=s=>document.querySelector(s);
const AUTH=['authToken','userRole','userName','userFullName'];
const st={tab:(location.hash||'#bank').slice(1),page:1,sort:'',dir:'desc',data:null,sel:null,widths:{}};
if(!TABS.some(t=>t[0]===st.tab))st.tab='bank';
const token=()=>localStorage.getItem('authToken')||'';
function expired(){AUTH.forEach(k=>localStorage.removeItem(k));location.replace('/login?next='+encodeURIComponent('/receivables/workspace'))}
if(!token()){expired();return}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const won=n=>(n===null||n===undefined||n==='')?'':Number(n).toLocaleString('ko-KR');
function toast(m){const t=$('#toast');t.textContent=m;t.classList.remove('hidden');clearTimeout(toast.t);toast.t=setTimeout(()=>t.classList.add('hidden'),2600)}
async function api(url,opt={}){const r=await fetch(url,{...opt,headers:{...(opt.headers||{}),Authorization:'Bearer '+token()},cache:'no-store'});
  if(r.status===401){expired();throw new Error('로그인이 필요합니다.')}
  if(!r.ok){let m='요청 실패';try{const j=await r.json();m=j.detail||m}catch(e){}throw new Error(typeof m==='string'?m:'요청 실패')}
  return r}
function qs(extra={}){const p=new URLSearchParams({q:$('#q').value.trim(),status:$('#status').value,page:st.page,size:$('#size').value,sort:st.sort,dir:st.dir,...extra});return p.toString()}
function renderTabs(){$('#tabs').innerHTML=TABS.map(([k,l])=>`<button class="tab ${k===st.tab?'on':''}" data-t="${k}" role="tab">${l}</button>`).join('');
  $('#tabs').querySelectorAll('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.t))}
function switchTab(t){st.tab=t;st.page=1;st.sort='';st.dir='desc';st.sel=null;location.hash=t;$('#q').value='';renderTabs();setupToolbar();load()}
function setupToolbar(){const s=$('#status'),o=STATUS_OPTS[st.tab];if(o){s.innerHTML=o.map(([v,l])=>`<option value="${v}">${l}</option>`).join('');s.classList.remove('hidden')}else{s.innerHTML='<option value=""></option>';s.classList.add('hidden')}
  const a=st.tab==='audit'||st.tab==='adjust';['#q','#size','#csv'].forEach(i=>$(i).classList.toggle('hidden',a));$('#revealWrap').classList.toggle('hidden',st.tab!=='audit');$('.pager').classList.toggle('hidden',a);$('#panel').classList.add('hidden')}
async function load(){
  if(st.tab==='audit')return loadAudit();
  if(st.tab==='adjust')return loadAdjust();
  $('#gridwrap').classList.remove('hidden');
  try{const r=await api('/api/receivables/workspace/data/'+st.tab+'?'+qs());st.data=await r.json();renderGrid();}catch(e){toast(e.message)}}
function cellHtml(c,v,row){
  if(c.type==='money'){const n=Number(v);return `<td class="money ${n<0?'neg':''}">${v===null||v===undefined||v===''?'':won(v)}</td>`}
  if(c.type==='badge'){return `<td><span class="badge b-${esc(row.status_code||'')}">${esc(v)}</span></td>`}
  return `<td class="${c.type==='num'?'num':''}" title="${esc(v)}">${esc(v)}</td>`}
function renderGrid(){
  const d=st.data,cols=d.columns;let left=0;const offs=cols.map(c=>{const w=st.widths[st.tab+c.key]||c.width;const o=left;if(c.sticky)left+=w;return[w,o]});
  const th=cols.map((c,i)=>`<th class="${c.type} ${c.sortable?'sortable':''} ${c.sticky?'sticky':''}" data-k="${c.key}" style="width:${offs[i][0]}px;${c.sticky?'left:'+offs[i][1]+'px':''}">${esc(c.label)}${st.sort===c.key?(st.dir==='asc'?' ▲':' ▼'):''}<i class="rz" data-i="${i}"></i></th>`).join('');
  const tb=d.rows.map((r,ri)=>`<tr data-i="${ri}" class="${st.sel===ri?'sel':''}">${cols.map((c,i)=>{let h=cellHtml(c,r[c.key],r);if(c.sticky)h=h.replace('<td','<td style="left:'+offs[i][1]+'px" class="sticky"').replace('class="sticky" class="','class="sticky ');return h}).join('')}</tr>`).join('');
  $('#grid').innerHTML=`<colgroup>${offs.map(o=>`<col style="width:${o[0]}px">`).join('')}</colgroup><thead><tr>${th}</tr></thead><tbody>${tb}</tbody>`;
  $('#empty').classList.toggle('hidden',d.rows.length>0);
  $('#grid').querySelectorAll('th.sortable').forEach(h=>h.onclick=e=>{if(e.target.classList.contains('rz'))return;const k=h.dataset.k;st.dir=(st.sort===k&&st.dir==='desc')?'asc':'desc';st.sort=k;st.page=1;load()});
  $('#grid').querySelectorAll('.rz').forEach(h=>h.onmousedown=e=>startResize(e,cols[+h.dataset.i]));
  $('#grid').querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>{st.sel=+tr.dataset.i;showPanel(d.rows[st.sel],cols);renderGrid()});
  const pages=Math.max(1,Math.ceil(d.total/d.size));$('#pageInfo').textContent=`${d.page} / ${pages} 쪽 · 총 ${d.total.toLocaleString()}건`;$('#prev').disabled=d.page<=1;$('#next').disabled=d.page>=pages;
  $('#summary').innerHTML=Object.entries(d.summary||{}).map(([k,v])=>`${esc(k)}<b>${typeof v==='number'?won(v):esc(v)}</b>`).join('');$('#note').textContent=d.note||'';}
function startResize(e,c){e.preventDefault();e.stopPropagation();const sx=e.clientX,w0=st.widths[st.tab+c.key]||c.width;
  const mv=ev=>{st.widths[st.tab+c.key]=Math.max(48,w0+ev.clientX-sx);renderGrid()};const up=()=>{document.removeEventListener('mousemove',mv);document.removeEventListener('mouseup',up)};document.addEventListener('mousemove',mv);document.addEventListener('mouseup',up)}
function showPanel(row,cols){const p=$('#panel');const dl=cols.map(c=>`<dt>${esc(c.label)}</dt><dd>${c.type==='money'?won(row[c.key]):esc(row[c.key])}</dd>`).join('');
  let acts='';
  if(row.status_code==='review'&&row.member_id){acts+=`<button class="btn" id="confirmBtn">후보 회원으로 확정</button>`}
  p.innerHTML=`<h3>상세</h3><dl>${dl}</dl><div class="acts">${acts}</div>`;p.classList.remove('hidden');
  const b=$('#confirmBtn');if(b)b.onclick=async()=>{
    if(!confirm(`이 입금을 '${row.member}' 회원으로 확정합니다.\n\n입금자: ${row.payer_raw||row.payer_name}\n금액: ${won(row.amount)}원\n근거: ${row.reason}\n\n(확정 후 '일괄 반영'을 해야 수납에 들어갑니다)`))return;
    try{await api('/api/receivables/imports/rows/'+row.id+'/match',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({member_id:row.member_id})});toast('회원 연결을 확정했습니다.');load()}catch(e){toast(e.message)}}}
const HINT={v4_state:'V4 보정이 적용됐는지 확인합니다.',cancelled:'V4가 취소 처리한 9/30 이전 수납의 건수와 금액입니다.',stale_payments:'정상이면 0건입니다. 있으면 같은 금액이 이중으로 차감될 수 있습니다.',unissued_charged:'미발급 택배회원에게 월별로 부과된 금액입니다(0원 부과는 제외).',unissued_not_held:'부과 보류 목록에 없는데 부과가 있는 회원입니다. 이 목록이 실제 오부과 후보입니다.',closed_charged:'폐업월 이후 부과가 남은 회원입니다. 폐업 전 미수금은 삭제하지 않고, 폐업 후 부과만 확인합니다.',dup_loss:'구버전 중복키가 정상 입금을 버렸을 가능성입니다(의심 건수 0이면 정상).',dup_payments:'같은 회원·날짜·금액이 이중으로 입력된 수납입니다.',triggers:'부과를 0원으로 강제하는 등 DB 쪽 규칙입니다.'};
async function loadAudit(){
  $('#gridwrap').classList.remove('hidden');$('#grid').innerHTML='';$('#summary').textContent='';$('#note').textContent='읽기 전용 점검입니다. 이 화면은 어떤 자료도 수정하지 않습니다(DB를 읽기 전용 상태로 열어 조회).';
  $('#grid').outerHTML='<div id="grid" class="audit">점검 중…</div>';
  try{const r=await api('/api/receivables/workspace/audit?reveal='+($('#reveal').checked?'true':'false'));const j=await r.json();
    if(!j.ok){$('#grid').innerHTML=`<div class="card"><div class="hint">${esc(j.error)}</div></div>`;return}
    $('#grid').innerHTML=j.sections.map(s=>{const n=s.rows.length;const err=s.error;
      const head=err?`<span class="tag-err">조회 불가(${esc(err)})</span>`:`<span>${n}행</span>`;
      const tbl=n?`<div style="overflow:auto"><table><thead><tr>${s.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${s.rows.map(r=>`<tr>${s.columns.map(c=>`<td>${esc(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'';
      return `<div class="card"><h4><span>${esc(s.title)}</span>${head}</h4><div class="hint">${esc(HINT[s.key]||'')}</div>${tbl}</div>`}).join('')}catch(e){$('#grid').textContent=e.message}}

const ADJ='/api/receivables/adjustments';
const DEC={target:'정정대상(택배)',already_zero:'이미 0원',credit_kept:'선납 유지',excluded:'제외',review_non_bae:'별도검토(비택배)',review_closed:'별도검토(폐업)',review_account:'별도검토(계정불일치)'};
const UNISSUED_CODE='unissued_management_fee_20261010';
async function adjJson(url,opt){const r=await api(url,opt);return r.json()}
async function loadAdjust(){
  $('#note').textContent='자격증명 미발급(발급일자·발급번호 모두 공란) 회원의 관리비 미수금을 0원으로 정정합니다. 실제 입금이 아니며 수납 통계에 들어가지 않습니다. 미리보기는 아무것도 바꾸지 않습니다.';$('#summary').textContent='';
  $('#grid').outerHTML='<div id="grid" class="audit">불러오는 중…</div>';
  try{
    const [pv,bt]=await Promise.all([adjJson(ADJ+'/unissued-management/preview'),adjJson(ADJ+'/batches')]);
    const sm=Object.entries(pv.summary).map(([k,v])=>`<tr><td>${esc(k)}</td><td style="text-align:right"><b>${typeof v==='number'?won(v):esc(v)}</b></td></tr>`).join('');
    const applied=bt.find(b=>b.status==='applied'&&b.reason_code===UNISSUED_CODE);
    const trow=r=>`<tr><td>${r.member_id}</td><td>${esc(r.name)}</td><td>${esc(r.vehicle_number)}</td><td>${esc(r.certificate_issue_date)}</td><td>${esc(r.certificate_number)}</td><td style="text-align:right">${won(r.balance_before)}</td><td style="text-align:right"><b>${won(r.planned_adjustment)}</b></td><td>${esc(DEC[r.decision]||r.decision)}</td><td>${esc((r.reason?r.reason+' ':'')+(r.flags||[]).join(' / '))}</td></tr>`;
    const tgt=pv.rows.filter(r=>r.decision==='target').slice(0,400).map(trow).join('');
    const rev=pv.rows.filter(r=>r.decision.startsWith('review_')||r.decision==='excluded').slice(0,400).map(trow).join('');
    const head='<thead><tr><th>ID</th><th>성명</th><th>차량번호</th><th>발급일자</th><th>발급번호</th><th>현재 관리비 미수금</th><th>정정 예정액</th><th>판정</th><th>사유/확인 필요</th></tr></thead>';
    const batches=bt.map(b=>`<tr><td>${esc(b.batch_id)}</td><td>${esc(b.status)}</td><td style="text-align:right">${b.members}</td><td style="text-align:right">${won(b.total_amount)}</td><td>${esc(b.created_by)}</td><td>${esc(b.created_at.slice(0,19))}</td><td><button class="btn ghost" data-v="${esc(b.batch_id)}">적용 후 검증</button> ${b.status==='applied'?`<button class="btn ghost" data-x="${esc(b.batch_id)}">되돌리기</button>`:''}</td></tr>`).join('');
    $('#grid').innerHTML=`
    <div class="card"><h4><span>1. 미리보기(DB 변경 없음)</span><button class="btn ghost" id="adjCsv">회원별 보고서 CSV</button></h4>
      <table><tbody>${sm}</tbody></table><div class="hint">plan_digest: <code>${esc(pv.plan_digest.slice(0,16))}…</code> · 비택배·폐업·계정불일치는 자동정정하지 않고 '별도 검토 목록'에 분리됩니다. 선납(음수)은 유지됩니다.</div></div>
    <div class="card"><h4><span>2. 정정 대상 — 택배(배 번호판) 미발급 회원(최대 400행, 전체는 CSV)</span></h4><div style="overflow:auto;max-height:300px"><table>${head}<tbody>${tgt||'<tr><td colspan=9>대상 없음</td></tr>'}</tbody></table></div></div>
    <div class="card"><h4><span>2-1. 별도 검토 목록 — 자동정정하지 않음(비택배·폐업·계정불일치 등)</span></h4><div style="overflow:auto;max-height:260px"><table>${head}<tbody>${rev||'<tr><td colspan=9>없음</td></tr>'}</tbody></table></div></div>
    <div class="card"><h4><span>3. 적용(관리자 · 1회만)</span></h4><div class="hint" style="line-height:2">${applied?'<b class="tag-bad">이미 적용된 정정이 있어 다시 적용할 수 없습니다.</b>':`<label><input type="checkbox" id="adjBackup"> 운영 DB 백업을 만들었고 복구할 수 있음을 확인했습니다</label><br>
      확인 문구 <input id="adjConfirm" placeholder="ZERO_UNISSUED_MANAGEMENT" style="width:260px;padding:5px"> <button class="btn" id="adjApply">정정 적용</button><br>
      <span>적용하면 위 '정정 대상(택배)' 회원의 관리비 미수금만 0원이 됩니다. 별도 검토 목록은 바뀌지 않습니다. 실제 입금·부과 내역은 바뀌지 않으며, 오류가 나면 전체가 취소됩니다.</span>`}</div></div>
    <div class="card"><h4><span>4. 적용 이력 · 검증</span></h4><div style="overflow:auto"><table><thead><tr><th>배치</th><th>상태</th><th>회원수</th><th>정정 합계</th><th>작업자</th><th>일시</th><th></th></tr></thead><tbody>${batches||'<tr><td colspan=7>이력 없음</td></tr>'}</tbody></table></div><div id="adjVerify" class="hint"></div></div>`;
    $('#adjCsv').onclick=async()=>{const r=await api(ADJ+'/unissued-management/preview.csv');const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='unissued_management_preview.csv';a.click()};
    const ap=$('#adjApply');if(ap)ap.onclick=async()=>{
      if(!$('#adjBackup').checked){toast('백업 확인에 체크해 주세요.');return}
      if(!confirm(`정정 대상 ${pv.summary['정정 대상(택배 미발급·양수 미수)']}명, 합계 ${won(pv.summary['정정 예정액 합계'])}원을 0원으로 정정합니다.\n되돌리기는 가능하지만 기록이 남습니다. 진행할까요?`))return;
      try{const r=await api(ADJ+'/unissued-management/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plan_digest:pv.plan_digest,confirm:$('#adjConfirm').value.trim(),backup_confirmed:true})});const j=await r.json();toast('정정 완료: '+j.members+'명');loadAdjust()}catch(e){toast(e.message)}};
    $('#grid').querySelectorAll('[data-v]').forEach(b=>b.onclick=async()=>{try{const v=await adjJson(ADJ+'/batches/'+b.dataset.v+'/verify');
      $('#adjVerify').innerHTML=`<b class="${v.members_failed===0&&v.payments_unchanged.ok&&v.charges_unchanged.ok&&v.screens.mismatch_count===0?'tag-ok':'tag-bad'}">검증 결과</b> · 대상 ${v.members_checked}명 중 불일치 ${v.members_failed}명 · 실제 수납 합계 불변: ${v.payments_unchanged.ok?'예':'아니오'}(${won(v.payments_unchanged.before)} → ${won(v.payments_unchanged.after_apply)}) · 부과 합계 불변: ${v.charges_unchanged.ok?'예':'아니오'} · 화면 계산 불일치 ${v.screens.mismatch_count}명 · 활성 미수 합계(상세 ${won(v.screens.active_total_detail)} / 목록 ${won(v.screens.active_total_list)})`}catch(e){toast(e.message)}});
    $('#grid').querySelectorAll('[data-x]').forEach(b=>b.onclick=async()=>{const why=prompt('되돌리는 사유를 입력하세요');if(!why)return;const c=prompt('확인 문구 VOID_ADJUSTMENT_BATCH 를 입력하세요');if(!c)return;
      try{await api(ADJ+'/batches/'+b.dataset.x+'/void',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:c.trim(),reason:why})});toast('되돌렸습니다(기록은 보존).');loadAdjust()}catch(e){toast(e.message)}});
  }catch(e){$('#grid').textContent=e.message}}
function restoreGrid(){if(st.tab!=='audit'&&st.tab!=='adjust'&&$('#grid').tagName!=='TABLE'){$('#grid').outerHTML='<table id="grid"></table>'}}
const _load=load;load=async function(){restoreGrid();return _load()};
$('#q').addEventListener('input',()=>{clearTimeout($('#q').t);$('#q').t=setTimeout(()=>{st.page=1;load()},300)});
$('#status').onchange=()=>{st.page=1;load()};$('#size').onchange=()=>{st.page=1;load()};$('#reload').onclick=()=>load();$('#reveal').onchange=()=>load();
$('#prev').onclick=()=>{if(st.page>1){st.page--;load()}};$('#next').onclick=()=>{st.page++;load()};
$('#csv').onclick=async()=>{try{const r=await api('/api/receivables/workspace/data/'+st.tab+'?'+qs({fmt:'csv'}));const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='receivables_'+st.tab+'.csv';a.click();URL.revokeObjectURL(a.href)}catch(e){toast(e.message)}};
$('#logoutBtn').onclick=()=>{AUTH.forEach(k=>localStorage.removeItem(k));location.replace('/login')};
window.addEventListener('hashchange',()=>{const t=location.hash.slice(1);if(t!==st.tab&&TABS.some(x=>x[0]===t))switchTab(t)});
renderTabs();setupToolbar();load();
})();
