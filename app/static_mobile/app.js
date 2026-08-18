/* ===================================================================
   전체면허자현황 모바일 버전 (Phase 1: 홈 / 면허자 목록 / 상세)
   - 기존 app/static/app.js, index.html, styles.css 는 전혀 건드리지 않음
   - 기존 API(/api/members, /api/dashboard 등)를 읽기 전용 위주로 재사용
   - localStorage 키를 기존 앱과 분리(m_authToken 등)해서 세션이 서로 섞이지 않게 함
=================================================================== */

const REGIONS = ['춘천시','원주시','강릉시','동해시','태백시','속초시','삼척시',
                 '홍천군','횡성군','영월군','평창군','정선군','철원군','화천군',
                 '양구군','인제군','고성군','양양군'];

const ST = {
  screen: 'home',
  detailId: null,
  list: { page:1, pages:1, total:0, items:[], search:'', filters:{ region:'', category:'', membership_status:'', status:'active' } },
  filterSheetOpen: false,
};

/* ===== AUTH / API ===== */
function getToken(){ return localStorage.getItem('m_authToken'); }
function setSession(token, role, name, full){
  localStorage.setItem('m_authToken', token);
  localStorage.setItem('m_userRole', role||'');
  localStorage.setItem('m_userName', name||'');
  localStorage.setItem('m_userFull', full||'');
}
function logout(){
  ['m_authToken','m_userRole','m_userName','m_userFull'].forEach(k=>localStorage.removeItem(k));
  ST.screen='home'; render();
}
async function api(method, url, body=null){
  const opts = { method, headers: { Authorization: `Bearer ${getToken()}` } };
  if (body) { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(body); }
  let res;
  try { res = await fetch(url, opts); }
  catch(e){ toast('서버 연결 오류','err'); throw e; }
  if (res.status===401){ logout(); throw new Error('unauthorized'); }
  if (!res.ok){
    let msg='오류가 발생했습니다';
    try{ const j=await res.json(); msg = j.detail || msg; }catch{}
    toast(String(msg).slice(0,100),'err');
    throw new Error(msg);
  }
  return res.headers.get('content-type')?.includes('json') ? res.json() : res;
}

/* ===== HELPERS ===== */
const e_ = v => v==null ? '' : String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fv = v => {
  if (v==null || v==='') return '-';
  const s=String(v).trim();
  if (s===''||s==='nan'||s==='None'||s==='null'||s==='NaN') return '-';
  const m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1].slice(2)}.${m[2]}.${m[3]}`;
  return s;
};
function fmtBizNo(v){
  const d=String(v||'').replace(/\D/g,'').slice(0,10);
  if(!d) return '-';
  if(d.length<=3) return d;
  if(d.length<=5) return `${d.slice(0,3)}-${d.slice(3)}`;
  return `${d.slice(0,3)}-${d.slice(3,5)}-${d.slice(5)}`;
}
function todayKr(){
  const d=new Date(), days=['일','월','화','수','목','금','토'];
  return `${d.getFullYear()}년 ${d.getMonth()+1}월 ${d.getDate()}일 (${days[d.getDay()]})`;
}
function toast(msg,type='ok'){
  const box=document.getElementById('toastBox');
  const t=document.createElement('div');
  t.className=`toast ${type==='err'?'err':''}`;
  t.textContent=msg;
  box.appendChild(t);
  setTimeout(()=>t.remove(),2600);
}
function cfm(msg){ return window.confirm(msg); }

/* ===== ROOT RENDER ===== */
function render(){
  const app=document.getElementById('app');
  if (!getToken()){ app.innerHTML=renderLogin(); bindLogin(); return; }
  const body = ST.screen==='home' ? renderHome()
    : ST.screen==='list' ? renderListShell()
    : ST.screen==='detail' ? renderDetailShell()
    : renderSoon(ST.screen);
  app.innerHTML = `${body}${renderBottomNav()}${renderFilterSheet()}`;
  bindBottomNav();
  if (ST.screen==='home') loadHome();
  if (ST.screen==='list') { bindListShell(); loadList(); }
  if (ST.screen==='detail') loadDetail();
  if (ST.filterSheetOpen) bindFilterSheet();
}
function goto(screen, extra){
  ST.screen=screen;
  if (screen==='detail') ST.detailId=extra;
  if (screen==='list' && extra===true) { ST.list.page=1; ST.list.items=[]; }
  window.scrollTo(0,0);
  render();
}

/* ===== LOGIN ===== */
function renderLogin(){
  return `
  <div class="login-wrap">
    <div class="login-logo">🚛</div>
    <div class="login-ttl">전체면허자현황</div>
    <div class="login-sub">모바일 버전 · 기존 계정으로 로그인</div>
    <div class="fi"><label>아이디</label><input id="lgId" autocomplete="username"></div>
    <div class="fi"><label>비밀번호</label><input id="lgPw" type="password" autocomplete="current-password"></div>
    <button class="btn primary" id="lgBtn">로그인</button>
    <div class="login-err" id="lgErr"></div>
  </div>`;
}
function bindLogin(){
  const doLogin=async()=>{
    const id=document.getElementById('lgId').value.trim();
    const pw=document.getElementById('lgPw').value;
    const errEl=document.getElementById('lgErr');
    errEl.textContent='';
    if(!id||!pw){ errEl.textContent='아이디와 비밀번호를 입력하세요'; return; }
    const fd=new FormData(); fd.append('username',id); fd.append('password',pw);
    try{
      const res=await fetch('/api/auth/login',{method:'POST',body:fd});
      if(!res.ok){ errEl.textContent='아이디 또는 비밀번호가 올바르지 않습니다'; return; }
      const j=await res.json();
      setSession(j.access_token, j.role, id, j.full_name);
      ST.screen='home'; render();
    }catch(e){ errEl.textContent='서버 연결 오류'; }
  };
  document.getElementById('lgBtn').onclick=doLogin;
  document.getElementById('lgPw').addEventListener('keydown',e=>{ if(e.key==='Enter') doLogin(); });
}

/* ===== BOTTOM NAV ===== */
function renderBottomNav(){
  const items=[
    {id:'home',  ic:'🏠', lb:'홈'},
    {id:'list',  ic:'🚚', lb:'면허자'},
    {id:'new',   ic:'➕', lb:'등록'},
    {id:'reports', ic:'📊', lb:'보고'},
    {id:'more',  ic:'☰', lb:'더보기'},
  ];
  return `<nav class="bnav">${items.map(it=>`
    <button class="bnav-item ${ST.screen===it.id?'on':''}" data-go="${it.id}">
      <span class="ic">${it.ic}</span><span>${it.lb}</span>
    </button>`).join('')}</nav>`;
}
function bindBottomNav(){
  document.querySelectorAll('.bnav-item').forEach(b=>{
    b.onclick=()=>{
      const id=b.dataset.go;
      if (id==='list') goto('list', true);
      else goto(id);
    };
  });
}

/* ===== SOON (placeholder for phase2/3 screens) ===== */
function renderSoon(which){
  const label={new:'등록',reports:'보고',more:'더보기'}[which]||which;
  const inner = which==='more' ? `
    <div class="topbar-title">더보기</div>
    <div style="padding:0 16px">
      <div class="card" style="margin-bottom:10px">
        <div style="font-weight:800;font-size:15px;margin-bottom:4px">${e_(localStorage.getItem('m_userFull')||localStorage.getItem('m_userName')||'')}</div>
        <div style="font-size:12.5px;color:var(--c-text-3)">${e_(localStorage.getItem('m_userRole')==='admin'?'관리자':'사용자')}</div>
      </div>
      <button class="btn outline" id="mLogoutBtn" style="margin-bottom:10px">로그아웃</button>
      <div style="font-size:12px;color:var(--c-text-3);padding:8px 4px;line-height:1.6">
        엑셀 업로드/다운로드, 통계·보고서, 등록/수정 등 나머지 기능은 다음 단계에서 순차적으로 추가될 예정입니다.<br>
        지금은 PC 버전(기존 시스템)에서 이용해 주세요.
      </div>
    </div>` : `
    <div class="soon-box"><div class="ic">🛠️</div>
      <div style="font-weight:800;font-size:15px;margin-bottom:6px">${label} 화면 준비 중</div>
      <div style="font-size:13px">다음 단계 작업에서 추가될 예정입니다.<br>지금은 PC 버전에서 이용해 주세요.</div>
    </div>`;
  return inner;
}

/* ===== HOME ===== */
function renderHome(){
  return `
  <div class="topbar">
    <div class="topbar-greet">안녕하세요 👋</div>
    <div class="topbar-date">${todayKr()}</div>
  </div>
  <div class="section-hd"><div class="t">현황 요약</div></div>
  <div class="stat-grid" id="homeStats">
    ${Array.from({length:4}).map(()=>`<div class="stat-card"><div class="lbl">&nbsp;</div><div class="val">…</div></div>`).join('')}
  </div>
  <div class="section-hd"><div class="t">빠른 메뉴</div></div>
  <div class="quick-row">
    <button class="quick-btn" data-qgo="list-all">전체 면허자</button>
    <button class="quick-btn" data-qgo="list-ind">개인회원</button>
    <button class="quick-btn" data-qgo="list-del">택배회원</button>
    <button class="quick-btn" data-qgo="list-notjoined">미가입자</button>
  </div>
  `;
}
async function loadHome(){
  document.querySelectorAll('.quick-btn').forEach(b=>{
    b.onclick=()=>{
      const g=b.dataset.qgo;
      ST.list.filters = { region:'', category:'', membership_status:'', status:'active' };
      ST.list.search='';
      if (g==='list-ind') ST.list.filters.category='개인';
      if (g==='list-del') ST.list.filters.category='택배';
      if (g==='list-notjoined') ST.list.filters.membership_status='미가입';
      goto('list', true);
    };
  });
  try{
    const d = await api('GET','/api/dashboard/stats');
    const grid=document.getElementById('homeStats');
    if(!grid) return;
    const cards=[
      {lbl:'전체 면허자', val:d.total, cls:'blue', sub:`개인 ${d.individual} · 택배 ${d.delivery}`},
      {lbl:'회원(가입)', val:d.joined, cls:'green', sub:`미가입 ${d.not_joined}`},
      {lbl:'예정자', val:d.candidates ?? 0, cls:'orange', sub:'준회원'},
      {lbl:'양도양수', val:d.transfers ?? 0, cls:'', sub:'누적 건수'},
      {lbl:'폐업', val:d.closures ?? 0, cls:'red', sub:'누적 건수'},
      {lbl:'취업신고', val:d.employed ?? 0, cls:'green', sub:`미신고 ${d.not_employed ?? '-'}`},
    ];
    grid.innerHTML = cards.map(c=>`
      <div class="stat-card">
        <div class="lbl">${e_(c.lbl)}</div>
        <div class="val ${c.cls}">${(c.val??0).toLocaleString()}<span style="font-size:13px;font-weight:700">명</span></div>
        <div class="sub">${e_(c.sub)}</div>
      </div>`).join('');
  }catch(e){
    const grid=document.getElementById('homeStats');
    if(grid) grid.innerHTML=`<div class="empty-box" style="grid-column:1/-1">현황을 불러오지 못했습니다</div>`;
  }
}

/* ===== 면허자 목록 ===== */
function renderListShell(){
  return `
  <div class="topbar-title">전체면허자현황</div>
  <div class="search-wrap">
    <div class="search-box"><span class="ic">🔍</span>
      <input id="mSearch" placeholder="이름, 차량번호, 관리번호 검색..." value="${e_(ST.list.search)}">
    </div>
    <button class="filter-btn ${_activeFilterCount()?'on':''}" id="mFilterBtn">⚙️${_activeFilterCount()?`<span class="filter-dot"></span>`:''}</button>
  </div>
  ${renderChips()}
  <div id="mListBody">
    <div class="loading"><div class="spin"></div>목록을 불러오는 중...</div>
  </div>`;
}
function _activeFilterCount(){
  const f=ST.list.filters;
  return ['region','category','membership_status'].filter(k=>f[k]).length + (f.status!=='active'?1:0);
}
function renderChips(){
  const f=ST.list.filters;
  const chips=[];
  if(f.region) chips.push(['region', f.region]);
  if(f.category) chips.push(['category', f.category]);
  if(f.membership_status) chips.push(['membership_status', f.membership_status]);
  if(f.status && f.status!=='active') chips.push(['status', f.status==='closed'?'폐업':f.status]);
  if(!chips.length) return '';
  return `<div class="chip-row">${chips.map(([k,label])=>`
    <span class="chip">${e_(label)}<button data-unchip="${k}">×</button></span>`).join('')}</div>`;
}
function bindListShell(){
  const sInput=document.getElementById('mSearch');
  let t=null;
  sInput.addEventListener('input',()=>{
    clearTimeout(t);
    t=setTimeout(()=>{ ST.list.search=sInput.value.trim(); ST.list.page=1; ST.list.items=[]; loadList(); },350);
  });
  document.getElementById('mFilterBtn').onclick=()=>{ ST.filterSheetOpen=true; render(); };
  document.querySelectorAll('[data-unchip]').forEach(b=>{
    b.onclick=()=>{
      const k=b.dataset.unchip;
      ST.list.filters[k] = (k==='status') ? 'active' : '';
      ST.list.page=1; ST.list.items=[]; loadList();
    };
  });
}
async function loadList(){
  const body=document.getElementById('mListBody');
  const f=ST.list.filters;
  const qs=new URLSearchParams({
    page:String(ST.list.page), limit:'20',
    search: ST.list.search||'',
    region: f.region||'', category: f.category||'',
    membership_status: f.membership_status||'', status: f.status||'active',
  });
  try{
    const d=await api('GET', `/api/members?${qs.toString()}`);
    ST.list.pages=d.pages; ST.list.total=d.total;
    ST.list.items = ST.list.page===1 ? d.items : ST.list.items.concat(d.items);
    if (!body) return;
    if (!ST.list.items.length){
      body.innerHTML=`<div class="empty-box">🔍<br><br>조건에 맞는 면허자가 없습니다</div>`;
      return;
    }
    body.innerHTML = `
      <div style="padding:2px 16px 0;font-size:12px;color:var(--c-text-3)">총 ${d.total.toLocaleString()}명</div>
      <div class="list-cards">${ST.list.items.map(cardHtml).join('')}</div>
      ${ST.list.page<ST.list.pages?`<div class="load-more" id="mLoadMore">더보기 (${ST.list.page}/${ST.list.pages})</div>`:''}
    `;
    document.querySelectorAll('.mem-card').forEach(c=>{ c.onclick=()=>goto('detail', c.dataset.id); });
    const lm=document.getElementById('mLoadMore');
    if (lm) lm.onclick=()=>{ ST.list.page++; loadList(); };
  }catch(e){
    if (body) body.innerHTML=`<div class="empty-box">목록을 불러오지 못했습니다</div>`;
  }
}
function cardHtml(r){
  const joined = r.membership_status==='가입';
  return `
  <div class="mem-card" data-id="${r.id}">
    <div class="row1">
      <span class="name">${e_(r.name||'-')}</span>
      <span class="badge ${r.category==='택배'?'b-yellow':'b-pri'}">${e_(r.category||'-')}</span>
    </div>
    <div class="row2">
      <span>🚚 ${e_(r.vehicle_number||'-')}</span>
      <span>📍 ${e_(r.region||'-')}</span>
    </div>
    <div class="row3">
      <span class="mgmt">관리번호 ${e_(r.management_number||'-')}</span>
      <span class="badge ${joined?'b-sky':'b-pink'}"><span class="dot ${joined?'g':'r'}"></span>${e_(r.membership_status||'-')}</span>
    </div>
  </div>`;
}

/* ===== 필터 Bottom Sheet ===== */
function renderFilterSheet(){
  if (!ST.filterSheetOpen) return `<div class="sheet-bg" id="sheetBg"></div><div class="sheet" id="sheetEl"></div>`;
  const f=ST.list.filters;
  const opt=(group,val,label,cur)=>`<button class="filter-opt ${cur===val?'sel':''}" data-fg="${group}" data-fv="${val}">${label}</button>`;
  return `
  <div class="sheet-bg show" id="sheetBg"></div>
  <div class="sheet show" id="sheetEl">
    <div class="sheet-handle"></div>
    <div class="sheet-hd">필터</div>
    <div class="sheet-body">
      <div class="filter-group">
        <div class="fg-lbl">회원구분</div>
        <div class="filter-opts">
          ${opt('category','','전체',f.category)}
          ${opt('category','개인','개인',f.category)}
          ${opt('category','택배','택배',f.category)}
        </div>
      </div>
      <div class="filter-group">
        <div class="fg-lbl">가입여부</div>
        <div class="filter-opts">
          ${opt('membership_status','','전체',f.membership_status)}
          ${opt('membership_status','가입','회원(가입)',f.membership_status)}
          ${opt('membership_status','미가입','미가입',f.membership_status)}
        </div>
      </div>
      <div class="filter-group">
        <div class="fg-lbl">등록상태</div>
        <div class="filter-opts">
          ${opt('status','active','정상',f.status)}
          ${opt('status','closed','폐업',f.status)}
        </div>
      </div>
      <div class="filter-group">
        <div class="fg-lbl">지역</div>
        <div class="filter-opts">
          ${opt('region','','전체',f.region)}
          ${REGIONS.map(r=>opt('region',r,r,f.region)).join('')}
        </div>
      </div>
    </div>
    <div class="sheet-ft">
      <button class="btn outline" id="sheetReset">초기화</button>
      <button class="btn primary" id="sheetApply">적용</button>
    </div>
  </div>`;
}
function bindFilterSheet(){
  document.getElementById('sheetBg').onclick=()=>{ ST.filterSheetOpen=false; render(); };
  document.querySelectorAll('[data-fg]').forEach(b=>{
    b.onclick=()=>{ ST.list.filters[b.dataset.fg]=b.dataset.fv; render(); };
  });
  document.getElementById('sheetReset').onclick=()=>{
    ST.list.filters={ region:'', category:'', membership_status:'', status:'active' };
    render();
  };
  document.getElementById('sheetApply').onclick=()=>{
    ST.filterSheetOpen=false; ST.list.page=1; ST.list.items=[];
    render();
  };
}

/* ===== 상세 ===== */
function renderDetailShell(){
  return `
  <div class="dtl-top">
    <button class="back-btn" id="dBack">‹</button>
    <div style="font-weight:800;font-size:15px">상세정보</div>
  </div>
  <div id="dBody"><div class="loading"><div class="spin"></div>불러오는 중...</div></div>`;
}
async function loadDetail(){
  document.getElementById('dBack').onclick=()=>goto('list');
  const body=document.getElementById('dBody');
  try{
    const r = await api('GET', `/api/members/${ST.detailId}`);
    const joined = r.membership_status==='가입';
    const sec = (title, icon, fields) => {
      const vis = fields.filter(([,v])=>v && fv(v)!=='-');
      if (!vis.length) return '';
      return `<div class="dtl-sec-card">
        <div class="dtl-sec-ttl">${icon} ${title}</div>
        <div class="dtl-grid">${vis.map(([l,v,full])=>`
          <div class="dtl-item ${full?'full':''}">
            <div class="l">${e_(l)}</div><div class="v">${e_(v)}</div>
          </div>`).join('')}</div>
      </div>`;
    };
    body.innerHTML = `
      <div class="dtl-hero">
        <div class="name">${e_(r.name||'-')}</div>
        <div class="meta">
          <span class="badge ${r.category==='택배'?'b-yellow':'b-pri'}">${e_(r.category||'-')}</span>
          <span class="badge ${joined?'b-sky':'b-pink'}"><span class="dot ${joined?'g':'r'}"></span>${e_(r.membership_status||'-')}</span>
          <span class="badge ${r.status==='closed'?'b-pink':'b-gray'}">${r.status==='closed'?'폐업':'정상'}</span>
          <span>관리번호 ${e_(r.management_number||'-')}</span>
        </div>
      </div>
      <div class="dtl-sections">
        ${sec('기본정보','👤',[['성명',r.name],['주민등록번호',r.resident_number],['차량번호',r.vehicle_number],['지역',r.region]])}
        ${sec('연락처 / 주소','📞',[['전화번호',r.phone],['핸드폰',r.mobile],['주소',r.address,true],['공문주소',r.official_address,true]])}
        ${sec('등록정보','📋',[['등록구분',r.registration_type],['인가일자',fv(r.approval_date)],['가입일자',fv(r.membership_date)],['재허가',fv(r.reapproval_date)]])}
        ${sec('자격증명정보','🪪',[['자격증명발급일자',fv(r.certificate_issue_date)],['자격증명발급번호',r.certificate_number],['운전면허번호',r.driver_license_number],['차종',r.vehicle_type],['유종',r.fuel_type]])}
        ${sec('사업자정보','🏢',[['사업자번호',r.business_number?fmtBizNo(r.business_number):''],['소속업체',r.affiliated_company]])}
        ${sec('대리인정보','🧾',[['대리인',r.agent_name],['대리인 주민등록번호',r.agent_resident_number],['대리인 핸드폰',r.agent_mobile],['대리인 주소',r.agent_address,true]])}
        ${sec('비고','📝',[['비고',r.memo,true],['구조변경',r.structure_change,true]])}
      </div>
      <div class="dtl-actions">
        <button class="btn outline" id="dEditBtn">✏️ 수정 (PC버전 이용)</button>
      </div>
      <div style="height:8px"></div>
    `;
    const editBtn=document.getElementById('dEditBtn');
    if (editBtn) editBtn.onclick=()=>toast('등록/수정 기능은 다음 단계에서 추가될 예정입니다. 지금은 PC 버전을 이용해주세요.');
  }catch(e){
    body.innerHTML=`<div class="empty-box">정보를 불러오지 못했습니다</div>`;
  }
}

/* ===== INIT ===== */
render();
