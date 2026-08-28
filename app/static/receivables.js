const state={view:'payment',scope:'active',arrearsOnly:false,members:[],selected:null,detail:null,smsTarget:null};
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const fmt=n=>`${Number(n||0).toLocaleString('ko-KR')}원`;
const today=()=>new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
function token(){return localStorage.getItem('access_token')||localStorage.getItem('token')||localStorage.getItem('authToken')||''}
function saveToken(t){localStorage.setItem('access_token',t);localStorage.setItem('token',t)}
async function api(url,opt={}){const headers={...(opt.headers||{})};if(token())headers.Authorization=`Bearer ${token()}`;if(opt.body&&!headers['Content-Type'])headers['Content-Type']='application/json';const r=await fetch(url,{...opt,headers});if(r.status===401){$('#loginOverlay').classList.remove('hidden');throw new Error('로그인이 필요합니다.')}const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail||'요청을 처리하지 못했습니다.');return data}
function toast(msg){const el=$('#toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),1900)}
function contactClass(s){return s==='재연락 필요'?'contact-tag recall':'contact-tag'}
function acctClass(a){return a==='협회비'?'acct assoc':a==='70세'?'acct age':'acct mgmt'}
async function loadSummary(){const d=await api('/api/receivables/summary');$('#kpiActiveTotal').textContent=fmt(d.active_arrears_total);$('#kpiActiveCount').textContent=`미수 ${d.active_arrears_members.toLocaleString()}명`;$('#kpiArrearsMembers').textContent=`${d.active_arrears_members.toLocaleString()}명`;$('#kpiClosedTotal').textContent=fmt(d.closed_arrears_total);$('#kpiClosedCount').textContent=`폐업 미수 ${d.closed_arrears_members.toLocaleString()}명`;$('#kpiTodayPaid').textContent=fmt(d.today_paid)}
function viewConfig(){if(state.view==='closed')return{scope:'closed',arrears:true,title:'폐업 미수금',sub:'폐업·양도·이관 후 남은 미수금을 별도로 관리합니다.'};if(state.view==='arrears')return{scope:'active',arrears:true,title:'활성 미수금',sub:'현재 미수금이 남아 있는 활성회원만 표시합니다.'};if(state.view==='contacts')return{scope:'all',arrears:true,title:'미수 연락관리',sub:'활성·폐업 미수회원의 연락 여부를 함께 관리합니다.'};return{scope:'active',arrears:false,title:'회원 찾기',sub:'신규등록 회원도 전체회원관리에서 자동으로 들어옵니다.'}}
async function loadMembers(){const c=viewConfig();state.scope=c.scope;state.arrearsOnly=c.arrears;$('#listTitle').textContent=c.title;$('#listSubtitle').textContent=c.sub;const p=new URLSearchParams({scope:c.scope,arrears_only:String(c.arrears),q:$('#searchInput').value.trim(),region:$('#regionFilter').value,account_type:$('#accountFilter').value,contact_status:$('#contactFilter').value});const d=await api(`/api/receivables/members?${p}`);state.members=d.items;$('#memberCount').textContent=`${d.count.toLocaleString()}명`;renderMembers();fillRegions();if(state.selected&&!state.members.some(x=>x.member_id===state.selected)){clearDetail()}}
function fillRegions(){const sel=$('#regionFilter'),cur=sel.value;const existing=new Set([...sel.options].slice(1).map(o=>o.value));[...new Set(state.members.map(x=>x.region).filter(Boolean))].sort().forEach(r=>{if(!existing.has(r)){const o=document.createElement('option');o.value=o.textContent=r;sel.appendChild(o)}});sel.value=cur}
function renderMembers(){const tb=$('#memberRows');tb.innerHTML='';if(!state.members.length){tb.innerHTML='<tr><td colspan="6" style="height:130px;text-align:center;color:#98a1ae">표시할 회원이 없습니다 🐸</td></tr>';return}for(const m of state.members){const tr=document.createElement('tr');if(m.member_id===state.selected)tr.classList.add('selected');const mobile=m.mobile||m.phone||'';tr.innerHTML=`<td><div class="member-main">${esc(m.name)}</div><div class="member-sub">${esc(m.management_number||'')}</div></td><td><span class="${acctClass(m.account_type)}">${esc(m.account_type)}</span></td><td>${esc(m.vehicle_number)}</td><td><div class="member-main small">${esc(m.region)}</div><div class="member-sub addr-clip" title="${esc(m.address||'')}">${esc(m.address||'')}</div></td><td><div class="phone-line"><span>${esc(mobile||'-')}</span>${mobile?`<button class="row-sms-btn" type="button" data-sms-id="${m.member_id}">U+ 문자</button>`:''}</div><div class="member-sub"><span class="${contactClass(m.contact_status)}">${esc(m.contact_status)}</span></div></td><td class="num balance-cell">${fmt(m.balance)}</td>`;tr.onclick=()=>selectMember(m.member_id);tr.querySelector('[data-sms-id]')?.addEventListener('click',e=>{e.stopPropagation();openSmsComposer(m)});tb.appendChild(tr)}}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function selectMember(id){state.selected=id;renderMembers();const d=await api(`/api/receivables/members/${id}?year=2026`);state.detail=d;renderDetail(d)}
function clearDetail(){state.selected=null;state.detail=null;$('#emptyState').classList.remove('hidden');$('#detailContent').classList.add('hidden');renderMembers()}
function renderDetail(d){const m=d.member;$('#emptyState').classList.add('hidden');$('#detailContent').classList.remove('hidden');$('#detailName').textContent=m.name||'-';$('#detailStatus').textContent=m.member_status;$('#detailStatus').classList.toggle('closed',!m.active);$('#detailManagement').textContent=m.management_number||'-';$('#detailAccount').textContent=m.account_type;$('#detailVehicle').textContent=m.vehicle_number||'-';$('#detailRegion').textContent=m.region||'-';$('#detailMobile').textContent=m.mobile||m.phone||'-';$('#detailAddress').textContent=m.address||'-';$('#detailAddress').title=m.address||'';$('#detailContact').textContent=`${m.contact_status}${m.last_contact_date?` · ${m.last_contact_date}`:''}`;$('#detailBalance').textContent=fmt(m.balance);$('#detailClosure').textContent=!m.active?`${m.closure_type||'폐업/변동'} ${m.closure_date||''}`:'';const smsBtn=$('#smsQuickBtn');smsBtn.disabled=!(m.mobile||m.phone);smsBtn.onclick=()=>openSmsComposer(m);renderMonthly(d.monthly);renderHistory(d);$('#paymentDate').value=today();$('#contactDate').value=today();$('#paymentAmount').value='';$('#paymentMemo').value='';$('#contactMemo').value=''}
function renderMonthly(rows){const tb=$('#monthlyRows');tb.innerHTML='';for(const r of rows){const current=Number(r.current_arrears||0);const tr=document.createElement('tr');tr.innerHTML=`<td>${r.month}월</td><td class="num">${r.legacy_billed_total==null?'-':Number(r.legacy_billed_total).toLocaleString()}</td><td class="num">${r.legacy_payment==null?'-':Number(r.legacy_payment).toLocaleString()}</td><td>${esc(r.legacy_payment_date||'-')}</td><td class="num">${r.auto_charge?Number(r.auto_charge).toLocaleString():'-'}</td><td class="num">${r.additional_payment?Number(r.additional_payment).toLocaleString():'-'}</td><td class="num ${current>0?'positive':'zero'}">${Number(current).toLocaleString()}</td>`;tb.appendChild(tr)}}
function renderHistory(d){$('#paymentHistory').innerHTML=d.payments.length?d.payments.map(p=>`<div class="history-item"><div><b>${p.payment_date}</b> · ${esc(p.method||'')}</div><div><b>${fmt(p.amount)}</b><br><span>${esc(p.created_by||'')}</span></div></div>`).join(''):'<div class="history-item"><span>추가 입금 없음</span></div>';$('#contactHistory').innerHTML=d.contacts.length?d.contacts.map(c=>`<div class="history-item"><div><b>${c.contact_date}</b> · ${esc(c.contact_method)}</div><div><b>${esc(c.status)}</b><br><span>${esc(c.memo||c.created_by||'')}</span></div></div>`).join(''):'<div class="history-item"><span>연락 기록 없음</span></div>'}
function defaultSmsText(m){
  const name=m.name||'회원';
  const bal=Number(m.balance||0);
  if(bal>0)return `[강원도 개인소형화물협회] ${name}님, 현재 ${m.account_type||'회비'} 미수금 ${fmt(bal)}이 확인되어 안내드립니다. 확인 부탁드립니다.`;
  return `[강원도 개인소형화물협회] ${name}님, 안내드립니다.`;
}
const UPLUS_BRIDGE='http://127.0.0.1:18765';
async function uplusBridgeFetch(path,opt={}){
  const ctl=new AbortController();const timer=setTimeout(()=>ctl.abort(),1800);
  try{
    const r=await fetch(`${UPLUS_BRIDGE}${path}`,{...opt,signal:ctl.signal,headers:{'Content-Type':'application/json',...(opt.headers||{})}});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.message||`U+ 브리지 오류 (${r.status})`);
    return d;
  }finally{clearTimeout(timer)}
}
async function checkUplusBridge(showToast=false){
  const st=$('#uplusBridgeStatus'),box=$('#uplusSetupBox');
  try{
    const d=await uplusBridgeFetch('/health',{method:'GET'});
    if(st){st.textContent=`연결됨 · ${d.product||'LG U+ CRM Pro'} · Bridge ${d.version||''}`;st.className='uplus-bridge-status ok'}
    box?.classList.add('hidden');
    if(showToast)toast('U+ CRM Pro PC 연동이 연결되어 있습니다.');
    return true;
  }catch(e){
    if(st){st.textContent='연결 안 됨 · 이 PC에서 U+ CRM Pro Bridge를 실행해주세요.';st.className='uplus-bridge-status bad'}
    box?.classList.remove('hidden');
    if(showToast)toast('U+ PC 연동 프로그램이 실행되지 않았습니다.');
    return false;
  }
}
async function copyUplusPayload(){
  const m=state.smsTarget;if(!m)return;
  const mobile=(m.mobile||m.phone||'').replace(/\D/g,'');
  const text=$('#smsText').value.trim();
  const payload=`수신번호: ${mobile}\n\n${text}`;
  try{await navigator.clipboard.writeText(payload);toast('수신번호와 문자내용을 복사했습니다.')}catch(e){toast('클립보드 복사에 실패했습니다.')}
}
function openSmsComposer(m){
  const mobile=m.mobile||m.phone||'';
  if(!mobile)return toast('등록된 핸드폰번호가 없습니다.');
  state.smsTarget=m;
  $('#smsRecipient').textContent=`${m.name||''} · ${mobile}`;
  $('#smsText').value=defaultSmsText(m);
  $('#smsOverlay').classList.remove('hidden');
  checkUplusBridge(false);
  setTimeout(()=>$('#smsText').focus(),0);
}
function closeSmsComposer(){state.smsTarget=null;$('#smsOverlay').classList.add('hidden')}
async function sendSmsNow(){
  const m=state.smsTarget;if(!m)return;
  const phone=(m.mobile||m.phone||'').replace(/\D/g,'');
  const text=$('#smsText').value.trim();
  if(!phone)return toast('등록된 핸드폰번호가 없습니다.');
  if(!text)return toast('문자 내용을 입력해주세요.');
  const btn=$('#smsSendBtn');btn.disabled=true;btn.textContent='U+ 전송 중...';
  try{
    const rr=await uplusBridgeFetch('/send',{method:'POST',body:JSON.stringify({phone,name:m.name||'',message:text,auto_send:true})});
    if(rr.sent===true){
      await api(`/api/receivables/members/${m.member_id}/contacts`,{method:'POST',body:JSON.stringify({contact_date:today(),contact_method:'문자',status:'문자발송',memo:`U+ CRM Pro 문자발송: ${text.slice(0,120)}`})}).catch(()=>null);
      toast('U+ CRM Pro에서 문자를 전송했습니다.');
      closeSmsComposer();
      await Promise.all([loadMembers(),state.selected===m.member_id?selectMember(m.member_id):Promise.resolve()]);
      return;
    }
    if(rr.prepared===true){
      toast('U+ CRM Pro 작성창에 번호/내용을 넣었습니다. 전송 버튼을 확인해주세요.');
      return;
    }
    throw new Error(rr.message||'CRM Pro 자동입력을 완료하지 못했습니다.');
  }catch(e){
    $('#uplusSetupBox')?.classList.remove('hidden');
    toast(e.name==='AbortError'?'U+ PC 연동 프로그램에 연결할 수 없습니다.':(e.message||'U+ 문자 연동에 실패했습니다.'));
  }finally{btn.disabled=false;btn.textContent='U+로 전송'}
}
async function savePayment(){if(!state.selected)return toast('회원을 먼저 선택해주세요.');const amount=Number($('#paymentAmount').value||0);if(amount<=0)return toast('입금액을 입력해주세요.');await api(`/api/receivables/members/${state.selected}/payments`,{method:'POST',body:JSON.stringify({payment_date:$('#paymentDate').value,amount,method:$('#paymentMethod').value,memo:$('#paymentMemo').value})});toast('입금이 저장되었습니다.');await Promise.all([loadSummary(),loadMembers(),selectMember(state.selected)])}
async function saveContact(){if(!state.selected)return toast('회원을 먼저 선택해주세요.');await api(`/api/receivables/members/${state.selected}/contacts`,{method:'POST',body:JSON.stringify({contact_date:$('#contactDate').value,contact_method:$('#contactMethod').value,status:$('#contactStatus').value,memo:$('#contactMemo').value})});toast('연락 기록이 저장되었습니다.');await Promise.all([loadMembers(),selectMember(state.selected)])}
function bind(){$('#smsCloseBtn').onclick=closeSmsComposer;$('#smsCancelBtn').onclick=closeSmsComposer;$('#smsSendBtn').onclick=sendSmsNow;$('#uplusCheckBtn').onclick=()=>checkUplusBridge(true);$('#uplusCopyBtn').onclick=copyUplusPayload;$('#smsOverlay').addEventListener('click',e=>{if(e.target===$('#smsOverlay'))closeSmsComposer()});$$('.tab').forEach(b=>b.onclick=async()=>{$$('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.view=b.dataset.view;clearDetail();await loadMembers()});let timer;$('#searchInput').oninput=()=>{clearTimeout(timer);timer=setTimeout(loadMembers,220)};['regionFilter','accountFilter','contactFilter'].forEach(id=>$('#'+id).onchange=loadMembers);$$('.quick-amounts [data-amount]').forEach(b=>b.onclick=()=>$('#paymentAmount').value=b.dataset.amount);$('#fullAmountBtn').onclick=()=>{if(state.detail)$('#paymentAmount').value=Math.max(0,state.detail.member.balance)};$('#savePaymentBtn').onclick=()=>savePayment().catch(e=>toast(e.message));$('#saveContactBtn').onclick=()=>saveContact().catch(e=>toast(e.message));$('#syncBtn').onclick=async()=>{const d=await api('/api/receivables/sync');toast(`동기화 완료 · 회원 ${d.profiles_created} / 부과 ${d.charges_created}`);await Promise.all([loadSummary(),loadMembers()])};$('#logoutBtn').onclick=()=>{['access_token','token','authToken'].forEach(k=>localStorage.removeItem(k));location.href='/login'};$('#loginForm').onsubmit=async e=>{e.preventDefault();const body=new URLSearchParams({username:$('#loginUsername').value,password:$('#loginPassword').value});try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});const d=await r.json();if(!r.ok)throw new Error(d.detail||'로그인 실패');saveToken(d.access_token);$('#loginOverlay').classList.add('hidden');await init()}catch(err){$('#loginError').textContent=err.message}}}
async function init(){if(!token()){$('#loginOverlay').classList.remove('hidden');return}try{await Promise.all([loadSummary(),loadMembers()]);$('#paymentDate').value=today();$('#contactDate').value=today()}catch(e){if(!/로그인/.test(e.message))toast(e.message)}}
bind();init();
