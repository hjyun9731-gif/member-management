const state={view:'payment',scope:'active',closureMode:'current',arrearsOnly:false,members:[],selected:null,selectedClosure:null,detail:null,actionMember:null,page:1,pages:1,limit:50,total:0,bulkBatchId:null,bulkRows:[],manualRowId:null,smsMember:null,dashboard:null,performanceView:'summary'};
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const fmt=n=>`${Number(n||0).toLocaleString('ko-KR')}원`;
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[ch]));
const today=()=>new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
let membersAbort=null;
function token(){return localStorage.getItem('access_token')||localStorage.getItem('token')||localStorage.getItem('authToken')||''}
function saveToken(t){localStorage.setItem('access_token',t);localStorage.setItem('token',t)}
async function api(url,opt={}){const headers={...(opt.headers||{})};if(token())headers.Authorization=`Bearer ${token()}`;if(opt.body&&!headers['Content-Type'])headers['Content-Type']='application/json';const r=await fetch(url,{cache:'no-store',...opt,headers});if(r.status===401){$('#loginOverlay').classList.remove('hidden');throw new Error('로그인이 필요합니다.')}const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.detail||'요청을 처리하지 못했습니다.');return data}
function toast(msg){const el=$('#toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),1900)}
function contactClass(s){return s==='재연락 필요'?'contact-tag recall':'contact-tag'}
function moneyState(m){if(m?.balance===null||m?.balance===undefined)return{label:'잔액',text:'-',cls:'zero-money'};const b=Number(m.balance||0);if(b>0)return{label:'현재 미수금',text:fmt(b),cls:'arrears-money'};if(b<0)return{label:'선납금',text:fmt(-b),cls:'prepaid-money'};return{label:'현재 미수금',text:'0원',cls:'zero-money'}}
function renderMemberHeader(){
  const h=$('#memberHead');
  if(!h)return;
  if(state.view==='closed'){
    const base='<th class="col-closure-no">관리번호</th><th class="col-type">구분</th><th class="col-member">회원</th><th class="col-region">지역</th><th class="col-vehicle">차량번호</th><th class="col-phone">핸드폰</th>';
    h.innerHTML=state.closureMode==='current'
      ?`<tr>${base}<th class="num col-balance">잔액</th></tr>`
      :`<tr>${base}<th class="col-current-state">현재상태</th><th class="num col-balance">잔액</th></tr>`;
  }else{
    const dateLabel=state.view==='contacts'?'최근 연락일':'첫 부과일';
    const contact=state.view==='contacts'?'<th class="col-contact">연락상태</th>':'';
    h.innerHTML=`<tr><th class="col-member">회원</th><th class="col-account">계정</th><th class="col-vehicle">차량번호</th><th class="col-region">지역</th><th class="col-phone">핸드폰</th><th class="col-first-charge">${dateLabel}</th><th class="num col-balance">현재 잔액</th><th class="col-billing">부과상태</th>${contact}<th class="col-manage">관리</th></tr>`;
  }
}
function acctClass(a){return a==='협회비'?'acct assoc':a==='70세'?'acct age':'acct mgmt'}
function billClass(s){return s==='부과대기'?'bill-tag pending':s.includes('폐업')?'bill-tag closed':s==='미수'?'bill-tag arrears':s==='선납'?'bill-tag prepaid':'bill-tag settled'}
function shortDate(s){return s&&s.length>=10?s.slice(5):s||''}
async function loadMeta(){try{const d=await api('/api/receivables/meta');const sel=$('#regionFilter'),cur=sel.value;sel.innerHTML='<option value="">전체 지역</option>';(d.regions||[]).forEach(r=>{const o=document.createElement('option');o.value=o.textContent=r;sel.appendChild(o)});sel.value=cur}catch(e){}}
function signedWon(v){const n=Number(v||0);return `${n>0?'+':n<0?'−':''}${fmt(Math.abs(n))}`}
function pct(v){return `${Number(v||0).toFixed(1)}%`}
function renderDashboard(d){
  state.dashboard=d||null;
  if(!d)return;
  // 미수금 대시보드에서는 연락/지역 통계를 사용하지 않는다.
  // 연락관리 탭의 요약에 필요한 값만 별도로 유지한다.
  const co=d.contact_overview||{}, sc=co.status_counts||{};
  const set=(id,val)=>{const el=$('#'+id);if(el)el.textContent=val};
  set('contactOverviewTotal',`총 ${Number(co.total_contacted||0).toLocaleString()}명`);
  set('contactTotal',`${Number(co.total_contacted||0).toLocaleString()}명`);
  set('contactDone',`${Number(sc['연락완료']||0).toLocaleString()}명`);
  set('contactSms',`${Number(sc['문자발송']||0).toLocaleString()}명`);
  set('contactRecall',`${Number(sc['재연락 필요']||0).toLocaleString()}명`);
  set('contactAbsent',`${Number(sc['부재']||0).toLocaleString()}명`);
  set('contactRecent',`${Number(co.recent_7_days||0).toLocaleString()}명`);
}
function monthText(key){if(!key)return '-';const [y,m]=String(key).split('-');return `${y}년 ${Number(m)}월`}
function ratioText(v){return v===null||v===undefined?'-':`${Number(v).toFixed(1)}%`}
function signedCount(v){const n=Number(v||0);return `${n>0?'+':''}${n.toLocaleString()}명`}
function syncMonthOptions(a){
  const from=$('#compareMonthA'),to=$('#compareMonthB');if(!from||!to)return;
  const rows=a.available_months||[];
  const html=rows.map(x=>`<option value="${esc(x.value)}">${esc(x.label)}</option>`).join('');
  from.innerHTML=html;to.innerHTML=html;
  if(a.period?.from)from.value=a.period.from;
  if(a.period?.to)to.value=a.period.to;
}
function renderMonthlyAnalysis(a){
  if(!a)return;
  if(a.__error){
    ['perfFromTotal','perfToTotal','perfNetChange','perfPayments','perfCharges','perfPayRate'].forEach(id=>{const el=$('#'+id);if(el)el.textContent='조회오류'});
    return;
  }
  state.analysis=a;
  syncMonthOptions(a);
  const p=a.period||{},fr=a.from||{},to=a.to||{},c=a.comparison||{};
  const set=(id,val)=>{const el=$('#'+id);if(el)el.textContent=val};
  set('perfFromLabel',`${monthText(fr.month)} 월말 미수`);
  set('perfFromTotal',fmt(fr.arrears_total||0));
  set('perfFromMembers',`미수 ${Number(fr.arrears_members||0).toLocaleString()}명 · 1인 평균 ${fmt(fr.average_arrears||0)}`);
  set('perfToLabel',`${monthText(to.month)} 월말 미수`);
  set('perfToTotal',fmt(to.arrears_total||0));
  set('perfToMembers',`미수 ${Number(to.arrears_members||0).toLocaleString()}명 · 1인 평균 ${fmt(to.average_arrears||0)}`);
  set('perfNetChange',signedWon(c.net_change||0));
  const net=Number(c.net_change||0);
  const direction=net<0?'감소':net>0?'증가':'변동 없음';
  const pctAbs=c.net_change_pct===null||c.net_change_pct===undefined?null:Math.abs(Number(c.net_change_pct));
  set('perfHeadline',`${monthText(fr.month)} 대비 ${monthText(to.month)} 미수금이 ${net===0?'변동 없습니다':`${fmt(Math.abs(net))} ${direction}했습니다`}.`);
  set('perfHeadlineSub',`실제 수납 ${fmt(c.period_payments||0)} · 부과 ${fmt(c.period_charges||0)} · 수납/부과율 ${ratioText(c.payment_charge_ratio)}`);
  set('perfNetPct',pctAbs===null?'비교 불가':`${monthText(fr.month)} 대비 ${pctAbs.toFixed(1)}% ${direction}`);
  const netCard=$('#perfVerdictCard');if(netCard){netCard.classList.toggle('good',net<0);netCard.classList.toggle('bad',net>0)}
  set('perfPayments',fmt(c.period_payments||0));
  set('perfPeriodText',p.from===p.to?`${monthText(p.to)} 당월`:`${monthText(p.from)} 말 → ${monthText(p.to)} 말 · ${Number(p.months||0)}개월`);
  set('perfCharges',fmt(c.period_charges||0));
  set('perfPayRate',ratioText(c.payment_charge_ratio));
  set('perfDecrease',fmt(c.decreased_amount||0));
  set('perfDecreaseMembers',`${Number(c.decreased_members||0).toLocaleString()}명에서 감소`);
  set('perfIncrease',fmt(c.increased_amount||0));
  set('perfIncreaseMembers',`${Number(c.increased_members||0).toLocaleString()}명에서 증가`);
  set('perfMemberChange',signedCount(c.arrears_member_change||0));
  set('perfSettled',`${Number(c.settled_members||0).toLocaleString()}명`);
  set('perfSettledRate',c.settled_rate===null||c.settled_rate===undefined?'-':`기준 미수회원의 ${Number(c.settled_rate).toFixed(1)}%`);
  set('perfNewArrears',`${Number(c.new_arrears_members||0).toLocaleString()}명`);
  set('perfRecoveryRate',c.gross_recovery_rate===null||c.gross_recovery_rate===undefined?'-':`${Number(c.gross_recovery_rate).toFixed(1)}%`);
  set('perfAdjustment',signedWon(c.period_adjustment||0));

  const history=a.history||[];
  const maxArrears=Math.max(1,...history.map(x=>Number(x.end_arrears||0)));
  const trend=$('#performanceTrend');
  if(trend){
    trend.innerHTML=history.map(r=>{
      const h=Math.max(10,Math.round(Number(r.end_arrears||0)/maxArrears*100));
      const n=Number(r.net_change||0);const cls=n<0?'good':n>0?'bad':'flat';
      return `<div class="perf-trend-item" title="${esc(r.label)} · 월말 미수 ${fmt(r.end_arrears||0)} · 전월 대비 ${signedWon(n)}"><strong>${(Number(r.end_arrears||0)/1000000).toFixed(1)}M</strong><div class="perf-trend-bar"><i style="height:${h}%"></i></div><b>${esc(String(r.month).slice(5))}월</b><small class="${cls}">${n===0?'0원':signedWon(n)}</small></div>`;
    }).join('')||'<div class="dash-empty">월별 데이터 없음</div>';
  }

  const maxFlow=Math.max(1,...history.flatMap(x=>[Number(x.charges||0),Number(x.payments||0)]));
  const flow=$('#monthlyFlowChart');
  if(flow){
    flow.innerHTML=history.map(r=>`<div class="flow-row"><b>${esc(String(r.month).slice(2).replace('-','.'))}</b><div class="flow-bars"><span class="charge"><i style="width:${Math.max(1,Number(r.charges||0)/maxFlow*100)}%"></i><em>부과 ${fmt(r.charges||0)}</em></span><span class="paid"><i style="width:${Math.max(1,Number(r.payments||0)/maxFlow*100)}%"></i><em>수납 ${fmt(r.payments||0)}</em></span></div></div>`).join('')||'<div class="dash-empty">부과·수납 데이터 없음</div>';
  }

  const ratio=$('#monthlyRatioChart');
  if(ratio){
    ratio.innerHTML=history.map(r=>{
      const rv=r.collection_ratio;const width=rv===null||rv===undefined?0:Math.min(100,Math.max(2,Number(rv)));
      const cls=rv===null||rv===undefined?'none':Number(rv)>=100?'great':Number(rv)>=80?'good':Number(rv)>=50?'mid':'low';
      return `<div class="ratio-row"><b>${esc(String(r.month).slice(2).replace('-','.'))}</b><div><i class="${cls}" style="width:${width}%"></i></div><strong>${ratioText(rv)}</strong></div>`;
    }).join('')||'<div class="dash-empty">수납률 데이터 없음</div>';
  }

  const comp=$('#monthComparisonDetail');
  if(comp){
    const diffAvg=Number(to.average_arrears||0)-Number(fr.average_arrears||0);
    comp.innerHTML=`<div class="month-side"><span>${monthText(fr.month)}</span><strong>${fmt(fr.arrears_total||0)}</strong><small>미수회원 ${Number(fr.arrears_members||0).toLocaleString()}명</small><small>1인 평균 ${fmt(fr.average_arrears||0)}</small></div><div class="month-vs"><span>월말 미수 차이</span><b class="${Number(c.net_change||0)<0?'good':Number(c.net_change||0)>0?'bad':''}">${signedWon(c.net_change||0)}</b><small>평균 ${signedWon(diffAvg)}</small></div><div class="month-side"><span>${monthText(to.month)}</span><strong>${fmt(to.arrears_total||0)}</strong><small>미수회원 ${Number(to.arrears_members||0).toLocaleString()}명</small><small>1인 평균 ${fmt(to.average_arrears||0)}</small></div>`;
  }
  set('compareDetailTitle',`${monthText(fr.month)} vs ${monthText(to.month)}`);

  const ins=a.insights||{}, insights=$('#performanceInsights');
  if(insights){
    const br=ins.best_reduction,wi=ins.worst_increase,bc=ins.best_collection;
    const row=(label,r,value,cls)=>`<div class="insight-row"><span>${label}</span><div><b>${r?esc(r.label):'-'}</b><small>${r?value(r):'데이터 없음'}</small></div><em class="${cls||''}">${r&&r.net_change!==undefined?signedWon(r.net_change):''}</em></div>`;
    insights.innerHTML=
      row('미수 가장 많이 감소',br,r=>`월말 ${fmt(r.end_arrears||0)}`,'good')+
      row('미수 가장 많이 증가',wi,r=>`월말 ${fmt(r.end_arrears||0)}`,'bad')+
      `<div class="insight-row"><span>수납/부과율 최고</span><div><b>${bc?esc(bc.label):'-'}</b><small>${bc?`수납 ${fmt(bc.payments||0)} / 부과 ${fmt(bc.charges||0)}`:'데이터 없음'}</small></div><em class="good">${bc?ratioText(bc.collection_ratio):''}</em></div>`;
  }

  const tbody=$('#performanceTableRows');
  if(tbody){
    tbody.innerHTML=history.map(r=>{
      const n=Number(r.net_change||0),cls=n<0?'good':n>0?'bad':'flat';
      return `<tr class="${r.month===p.to?'selected-month':''}"><td><b>${esc(r.label)}</b></td><td class="num">${fmt(r.start_arrears||0)}</td><td class="num">${fmt(r.charges||0)}</td><td class="num paid-cell">${fmt(r.payments||0)}</td><td class="num"><b>${ratioText(r.collection_ratio)}</b></td><td class="num end-balance">${fmt(r.end_arrears||0)}</td><td class="num ${cls}">${signedWon(n)}</td><td class="num ${cls}">${r.net_change_pct===null||r.net_change_pct===undefined?'-':`${Number(r.net_change_pct)>0?'+':''}${Number(r.net_change_pct).toFixed(1)}%`}</td><td class="num">${Number(r.arrears_members||0).toLocaleString()}명</td><td class="num good">${Number(r.settled_members||0).toLocaleString()}명</td><td class="num bad">${Number(r.new_arrears_members||0).toLocaleString()}명</td></tr>`;
    }).join('')||'<tr><td colspan="11" class="dash-empty">월별 데이터 없음</td></tr>';
  }
}
function syncPerformanceView(){
  const isArrears=state.view==='arrears';
  const view=state.performanceView||'summary';
  $$('[data-perf-view]').forEach(btn=>btn.classList.toggle('active',btn.dataset.perfView===view));
  $$('[data-perf-panel]').forEach(panel=>panel.classList.toggle('hidden',panel.dataset.perfPanel!==view));
  const ws=document.querySelector('.workspace');
  if(ws)ws.classList.toggle('performance-workspace-hidden',isArrears&&view!=='members');
  document.body.classList.toggle('arrears-performance-mode',isArrears&&view!=='members');
  document.body.classList.toggle('arrears-members-mode',isArrears&&view==='members');
}
function setPerformanceView(view){
  state.performanceView=view||'summary';
  syncPerformanceView();
}
async function loadMonthComparison(){
  const from=$('#compareMonthA')?.value||'',to=$('#compareMonthB')?.value||'';
  if(!from||!to)return;
  if(from>to){toast('기준월은 비교월보다 앞선 월로 선택해주세요.');return}
  const btn=$('#applyMonthCompare');if(btn){btn.disabled=true;btn.textContent='비교 중…'}
  try{
    const a=await api(`/api/receivables/monthly-analysis?from_month=${encodeURIComponent(from)}&to_month=${encodeURIComponent(to)}`);
    renderMonthlyAnalysis(a);
  }finally{if(btn){btn.disabled=false;btn.textContent='비교'}}
}
async function loadSummary(){
  const ma=$('#compareMonthA')?.value||'',mb=$('#compareMonthB')?.value||'';
  const analysisUrl=ma&&mb?`/api/receivables/monthly-analysis?from_month=${encodeURIComponent(ma)}&to_month=${encodeURIComponent(mb)}`:'/api/receivables/monthly-analysis';
  const [d,a,dash]=await Promise.all([api('/api/receivables/summary'),api(analysisUrl).catch(e=>({__error:(e&&e.message)||'분석 API 오류'})),api('/api/receivables/dashboard').catch(()=>null)]);
  $('#kpiActiveTotal').textContent=fmt(d.active_arrears_total);$('#kpiActiveCount').textContent=`미수 ${Number(d.active_arrears_members||0).toLocaleString()}명`;$('#kpiArrearsMembers').textContent=`${Number(d.active_arrears_members||0).toLocaleString()}명`;$('#kpiPending').textContent=`${Number(d.pending_members||0).toLocaleString()}명`;$('#kpiPrepaidTotal').textContent=fmt(d.active_prepaid_total);$('#kpiPrepaidCount').textContent=`선납 ${Number(d.active_prepaid_members||0).toLocaleString()}명`;$('#kpiClosedTotal').textContent=fmt(d.closed_arrears_total);$('#kpiClosedCount').textContent=`폐업 미수 ${Number(d.closed_arrears_members||0).toLocaleString()}명`;$('#kpiTodayPaid').textContent=fmt(d.today_paid);
  if(a)renderMonthlyAnalysis(a);if(dash)renderDashboard(dash);
}
function viewConfig(){if(state.view==='closed')return{scope:'closed',arrears:false,contactedOnly:false,title:state.closureMode==='current'?'폐업관리':'전체 폐업이력',sub:state.closureMode==='current'?'현재 실제 폐업·양도·이관 처리되어 회원상태가 폐업인 회원만 표시합니다.':'인허가/변경의 과거 폐업·양도·이관 전체 기록을 조회합니다.'};if(state.view==='arrears')return{scope:'active',arrears:true,contactedOnly:false,title:'미수회원 상세 조회',sub:'대시보드 아래에서 미수회원 개별 상세와 월별 장부를 확인합니다.'};if(state.view==='contacts')return{scope:'all',arrears:false,contactedOnly:true,title:'연락 기록 회원',sub:'연락 기록이 1건 이상 있는 회원만 표시합니다. 최근 연락일·상태·잔액을 함께 확인합니다.'};return{scope:'active',arrears:false,contactedOnly:false,title:'수납 대상 회원',sub:'실제 입금을 등록할 회원을 찾는 화면 · 미수/완납/선납을 포함한 활성회원 전체'}}
async function loadMembers(resetPage=false){if(resetPage)state.page=1;const c=viewConfig();const billing=$('#billingFilter').value;const explicitBilling=Boolean(billing);const effectiveArrears=c.arrears&&!explicitBilling;state.scope=c.scope;state.arrearsOnly=effectiveArrears;let title=c.title,sub=c.sub;if(state.view==='arrears'&&billing==='prepaid'){title='활성 선납금';sub='현재 잔액이 음수인 활성회원, 즉 선납금이 남아 있는 회원을 표시합니다.'}else if(state.view==='arrears'&&billing==='settled'){title='활성 완납회원';sub='현재 잔액이 0원인 활성회원을 표시합니다.'}else if(state.view==='arrears'&&billing==='pending'){title='신규 부과대기';sub='신규등록 후 첫 부과일이 아직 도래하지 않은 활성회원을 표시합니다.'}$('#listTitle').textContent=title;$('#listSubtitle').textContent=sub;const ws=document.querySelector('.workspace');if(ws){ws.classList.toggle('closure-mode',state.view==='closed');ws.classList.toggle('closure-history',state.view==='closed'&&state.closureMode==='history');ws.classList.toggle('payment-mode',state.view==='payment');ws.classList.toggle('arrears-mode',state.view==='arrears');ws.classList.toggle('contacts-mode',state.view==='contacts');syncWorkspaceDetailState()}const cms=$('#closureModeSwitch');if(cms)cms.classList.toggle('hidden',state.view!=='closed');const cf=$('#contactFilter');if(cf)cf.classList.toggle('hidden',state.view==='payment');const bf=$('#billingFilter');if(bf){bf.classList.toggle('hidden',state.view==='arrears');if(state.view==='arrears')bf.value=''}const ma=$('#monthlyAnalysis');if(ma)ma.classList.toggle('hidden',state.view!=='arrears');const ad=$('#arrearsDashboard');if(ad)ad.classList.toggle('hidden',state.view!=='arrears');syncPerformanceView();const co=$('#contactOverview');if(co)co.classList.toggle('hidden',state.view!=='contacts');const kg=document.querySelector('.kpi-grid');if(kg)kg.classList.toggle('dashboard-hidden',state.view==='arrears');const mg=$('#modeGuide'),mgt=$('#modeGuideTitle'),mgx=$('#modeGuideText'),mgb=$('#modeGuideBadge');if(mg&&mgt&&mgx&&mgb){mg.classList.toggle('arrears-mode-guide',state.view==='arrears');mg.classList.toggle('payment-mode-guide',state.view==='payment');mg.classList.toggle('contacts-mode-guide',state.view==='contacts');mg.classList.toggle('hidden',!['payment','arrears','contacts'].includes(state.view));if(state.view==='arrears'){mgt.textContent='미수금현황';mgx.textContent='월말 미수금이 전월·과거월 대비 얼마나 줄고 늘었는지, 부과액과 실제 수납액을 비교하는 성과 대시보드입니다.';mgb.textContent='월별 성과 · 비교'}else if(state.view==='contacts'){mgt.textContent='연락관리';mgx.textContent='연락 기록이 있는 회원만 모아서 최근 연락일·상태·잔액을 관리합니다.';mgb.textContent='연락기록만'}else if(state.view==='payment'){mgt.textContent='수납처리';mgx.textContent='회원을 찾아 실제 입금을 등록하는 화면입니다. 미수·완납·선납을 포함한 활성회원 전체에서 찾습니다.';mgb.textContent='입금 업무'}}renderMemberHeader();if(membersAbort)membersAbort.abort();membersAbort=new AbortController();const p=new URLSearchParams({scope:c.scope,closure_mode:state.closureMode,arrears_only:String(effectiveArrears),q:$('#searchInput').value.trim(),region:$('#regionFilter').value,account_type:$('#accountFilter').value,contact_status:state.view==='payment'?'':$('#contactFilter').value,contacted_only:String(Boolean(c.contactedOnly)),billing_status:billing,page:String(state.page),limit:String(state.limit)});try{const d=await api(`/api/receivables/members?${p}`,{signal:membersAbort.signal});state.members=d.items||[];state.total=Number(d.count||0);state.page=Number(d.page||1);state.pages=Number(d.pages||1);$('#memberCount').textContent=`${state.total.toLocaleString()}${state.view==='closed'?(state.closureMode==='current'?'명':'건'):'명'}`;renderMembers();renderPager();if(state.selected&&!state.members.some(x=>x.member_id===state.selected)&&!state.detail)clearDetail(false)}catch(e){if(e.name!=='AbortError')throw e}}
function currentExportParams(){const c=viewConfig();const billing=$('#billingFilter').value;const explicitBilling=Boolean(billing);const effectiveArrears=c.arrears&&!explicitBilling;return new URLSearchParams({view:state.view,scope:c.scope,closure_mode:state.closureMode,arrears_only:String(effectiveArrears),q:$('#searchInput').value.trim(),region:$('#regionFilter').value,account_type:$('#accountFilter').value,contact_status:state.view==='payment'?'':$('#contactFilter').value,contacted_only:String(Boolean(c.contactedOnly)),billing_status:billing})}
async function downloadFilteredExcel(){const btn=$('#excelExportBtn');if(!btn)return;const old=btn.textContent;btn.disabled=true;btn.textContent='내려받는 중…';try{const headers={};if(token())headers.Authorization=`Bearer ${token()}`;const r=await fetch(`/api/receivables/export.xlsx?${currentExportParams()}`,{headers,cache:'no-store'});if(r.status===401){$('#loginOverlay').classList.remove('hidden');throw new Error('로그인이 필요합니다.')}if(!r.ok){let msg='엑셀 다운로드에 실패했습니다.';try{const d=await r.json();msg=d.detail||msg}catch(e){}throw new Error(msg)}const blob=await r.blob();const cd=r.headers.get('Content-Disposition')||'';let filename=`수납미수금_${today()}.xlsx`;const m=cd.match(/filename\*=UTF-8''([^;]+)/i);if(m){try{filename=decodeURIComponent(m[1])}catch(e){}}const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);const count=r.headers.get('X-Export-Count');toast(`엑셀 다운로드 완료${count?` · ${Number(count).toLocaleString()}건`:''}`)}finally{btn.disabled=false;btn.textContent=old}}
function renderPager(){const txt=$('#pageInfo');txt.textContent=`${state.page.toLocaleString()} / ${state.pages.toLocaleString()}`;$('#prevPageBtn').disabled=state.page<=1;$('#nextPageBtn').disabled=state.page>=state.pages}
function renderMembers(){
  const tb=$('#memberRows');
  tb.innerHTML='';
  const cols=state.view==='closed'?(state.closureMode==='current'?7:8):state.view==='contacts'?10:9;
  if(!state.members.length){
    tb.innerHTML=`<tr><td colspan="${cols}" class="empty-row">표시할 자료가 없습니다.</td></tr>`;
    return;
  }
  const frag=document.createDocumentFragment();
  for(const m of state.members){
    const tr=document.createElement('tr');
    if(m.member_id===state.selected&&(!state.selectedClosure||m.closure_id===state.selectedClosure))tr.classList.add('selected');
    const ms=moneyState(m);
    if(state.view==='closed'){
      const phone=m.mobile||m.phone||'-';
      const sms=m.member_id&&phone!=='-'?`<button type="button" class="row-sms-btn" data-sms-id="${m.member_id}">U+ 문자</button>`:'';
      const currentState=!m.member_id?'과거기록':m.active?'현재활동':'폐업';
      const currentClass=!m.member_id?'history':m.active?'active':'closed';
      const stateCell=state.closureMode==='history'?`<td class="col-current-state"><span class="current-state-tag ${currentClass}">${currentState}</span></td>`:'';
      tr.innerHTML=`<td class="col-closure-no"><b>${esc(m.closure_management_number||'-')}</b></td><td class="col-type"><span class="closure-type-tag">${esc(m.closure_type||'-')}</span></td><td class="col-member"><div class="closure-person"><b>${esc(m.name||'-')}</b></div></td><td class="col-region"><span class="region-text">${esc(m.region||'-')}</span></td><td class="col-vehicle"><span class="vehicle-text">${esc(m.vehicle_number||'-')}</span></td><td class="col-phone"><div class="phone-line"><span class="phone-number">${esc(phone)}</span>${sms}</div></td>${stateCell}<td class="num col-balance ${ms.cls}">${ms.label==='선납금'?'선납 ':''}${ms.text}</td>`;
      if(!m.member_id)tr.classList.add('unlinked');
      tr.onclick=()=>m.member_id?selectMember(m.member_id,m.closure_id):toast('폐업현황 기록은 확인되지만 현재 수납 원장과 연결된 회원이 없습니다.');
      const sb=tr.querySelector('[data-sms-id]');
      if(sb)sb.onclick=e=>{e.stopPropagation();openSmsComposer(m)};
    }else{
      const phone=m.mobile||m.phone||'-';
      const firstCharge=state.view==='contacts'?(m.last_contact_date||'-'):(m.first_charge_date&&m.first_charge_date!=='0'?m.first_charge_date:'-');
      const manage=`<button type="button" class="mini-action" data-member-action="${m.member_id}">업무</button>`;
      const sms=phone!=='-'?`<button type="button" class="row-sms-btn" data-sms-id="${m.member_id}">U+ 문자</button>`:'';
      const contact=state.view==='contacts'?`<td class="col-contact"><span class="${contactClass(m.contact_status)}">${esc(m.contact_status)}</span></td>`:'';
      tr.innerHTML=`<td class="col-member"><div class="member-main">${esc(m.name)}</div><div class="member-sub">${esc(m.management_number||'')}</div></td><td class="col-account"><span class="${acctClass(m.account_type)}">${esc(m.account_type)}</span></td><td class="col-vehicle"><span class="vehicle-text">${esc(m.vehicle_number||'-')}</span></td><td class="col-region"><span class="region-text">${esc(m.region||'-')}</span></td><td class="col-phone"><div class="phone-line"><span class="phone-number">${esc(phone)}</span>${sms}</div></td><td class="col-first-charge"><span class="first-charge-text">${esc(firstCharge)}</span></td><td class="num col-balance ${ms.cls}">${ms.label==='선납금'?'선납 ':''}${ms.text}</td><td class="col-billing"><span class="${billClass(m.billing_state)}">${esc(m.billing_state)}</span></td>${contact}<td class="manage-cell col-manage">${m.active?manage:'<span class="closed-mini">처리완료</span>'}</td>`;
      tr.onclick=()=>selectMember(m.member_id);
      const btn=tr.querySelector('[data-member-action]');
      if(btn)btn.onclick=e=>{e.stopPropagation();openMemberAction(m)};
      const sb=tr.querySelector('[data-sms-id]');
      if(sb)sb.onclick=e=>{e.stopPropagation();openSmsComposer(m)};
    }
    frag.appendChild(tr);
  }
  tb.appendChild(frag);
}

function syncWorkspaceDetailState(){const ws=document.querySelector('.workspace');if(!ws)return;ws.classList.toggle('has-detail',Boolean(state.detail&&state.selected));}

// 회원 행 선택/상세 열기 — 기존 기능 복구.
// 이전 패치에서 이 함수 두 개가 실수로 빠져 행 클릭 시 ReferenceError가 발생했다.
async function selectMember(id,closureId=null){
  state.selected=id;
  state.selectedClosure=closureId;
  renderMembers();
  const extra=closureId?`&closure_id=${encodeURIComponent(closureId)}`:'';
  try{
    const d=await api(`/api/receivables/members/${id}?year=2026${extra}`);
    if(state.selected!==id||state.selectedClosure!==closureId)return;
    state.detail=d;
    syncWorkspaceDetailState();
    renderDetail(d);
    const detail=$('#detailContent');
    if(detail)detail.scrollTop=0;
  }catch(e){
    toast(e.message||'회원 상세정보를 불러오지 못했습니다.');
  }
}
function clearDetail(resetSelection=true){
  if(resetSelection){state.selected=null;state.selectedClosure=null}
  state.detail=null;
  syncWorkspaceDetailState();
  $('#emptyState').classList.remove('hidden');
  $('#detailContent').classList.add('hidden');
  renderMembers();
}

function renderDetail(d){const m=d.member;$('#emptyState').classList.add('hidden');$('#detailContent').classList.remove('hidden');$('#detailName').textContent=m.name||'-';$('#detailStatus').textContent=m.member_status;$('#detailStatus').classList.toggle('closed',!m.active);$('#detailAccount').textContent=m.account_type;$('#detailVehicle').textContent=m.vehicle_number||'-';$('#detailRegion').textContent=m.region||'-';$('#detailMobile').textContent=m.mobile||m.phone||'-';const detailSmsBtn=$('#detailSmsBtn');if(detailSmsBtn){const hasSmsNumber=Boolean(m.mobile||m.phone);detailSmsBtn.disabled=!hasSmsNumber;detailSmsBtn.title=hasSmsNumber?'U+ CRM Pro 문자 보내기':'핸드폰번호가 없습니다.'}$('#detailAddress').textContent=m.address||'-';$('#detailContact').textContent=`${m.contact_status}${m.last_contact_date?` · ${m.last_contact_date}`:''}`;$('#detailBilling').textContent=m.billing_state||'-';$('#detailFirstCharge').textContent=(m.first_charge_date===undefined||m.first_charge_date===null||m.first_charge_date==='')?'0':m.first_charge_date;const ms=moneyState(m);$('#detailBalanceLabel').textContent=ms.label;$('#detailBalance').textContent=ms.text;$('#detailBalance').className=ms.cls;$('#detailClosure').textContent=!m.active?`${m.closure_management_number?m.closure_management_number+' · ':''}${m.closure_type||'폐업'} ${m.closure_date||''}`:'';const ctx=m.closure_context||(!m.active?{management_number:m.closure_management_number,closure_type:m.closure_type,closure_date:m.closure_date,receipt_date:m.closure_receipt_date,transferee:m.transferee,transfer_region:m.transfer_region,reason:m.closure_reason}:null);const ci=$('#closureInfo');if(ctx){ci.classList.remove('hidden');$('#detailClosureMgmt').textContent=ctx.management_number||'-';$('#detailClosureType').textContent=ctx.closure_type||'-';$('#detailClosureDate').textContent=ctx.closure_date||'-';$('#detailClosureReceipt').textContent=ctx.receipt_date||'-';$('#detailTransferee').textContent=ctx.transferee||'-';$('#detailTransferRegion').textContent=ctx.transfer_region||'-';$('#detailClosureReason').textContent=ctx.reason||'-'}else ci.classList.add('hidden');const wb=$('#memberWorkBar');const workBtn=$('#openMemberWorkBtn');if(wb&&workBtn){workBtn.classList.toggle('hidden',!m.active);$('#memberWorkHint').textContent=m.active?'현재 활성회원입니다. 여기서 기존 폐업·양도·이관 처리를 바로 실행할 수 있습니다.':`${m.closure_management_number||''} ${m.closure_type||'폐업'} 처리된 회원입니다. 입금·연락 이력은 계속 유지됩니다.`}const notice=$('#pendingNotice');if(m.billing_state==='부과대기'){notice.classList.remove('hidden');notice.textContent=`신규등록 회원입니다. ${m.first_charge_date}부터 ${m.account_type} ${fmt(Number(m.unit_fee||0)*Number(m.vehicle_count||1))}이 부과될 예정입니다.`}else notice.classList.add('hidden');const co=$('#ledgerCarryover');if(co){const v=Number(d.profile?.legacy_carryover||0);co.textContent=v?`· 이월금 ${v.toLocaleString()}원`:''}renderMonthly(d.monthly);renderHistory(d);$('#paymentDate').value=today();$('#contactDate').value=today();$('#paymentAmount').value='';$('#paymentMemo').value='';$('#contactMemo').value=''}
function ledgerDate(v){const s=String(v||'').trim();if(!s||/^#+$/.test(s))return '-';return s}
function renderMonthly(rows){const tb=$('#monthlyRows');tb.innerHTML='';const frag=document.createDocumentFragment();for(const r of rows){const hasCurrent=r.current_arrears!==null&&r.current_arrears!==undefined;const current=hasCurrent?Number(r.current_arrears):null;const currentText=!hasCurrent?'-':current<0?`선납 ${Math.abs(current).toLocaleString()}`:current.toLocaleString();const currentClass=!hasCurrent?'':current>0?'positive':current<0?'prepaid':'zero';const adj=Number(r.balance_adjustment||0);const adjText=adj===0?'-':`${adj>0?'+':'−'}${Math.abs(adj).toLocaleString()}`;const adjClass=adj>0?'adjustment-positive':adj<0?'adjustment-negative':'';const legacyCharge=Number(r.legacy_monthly_charge||0);const autoCharge=Number(r.auto_charge||0);const totalCharge=legacyCharge+autoCharge;const hasCharge=r.legacy_monthly_charge!==null&&r.legacy_monthly_charge!==undefined||autoCharge!==0;const tr=document.createElement('tr');tr.innerHTML=`<td>${r.month}월</td><td class="num">${hasCharge?totalCharge.toLocaleString():'-'}</td><td class="num">${r.legacy_payment==null?'-':Number(r.legacy_payment).toLocaleString()}</td><td>${esc(ledgerDate(r.legacy_payment_date))}</td><td class="num">${r.additional_payment?Number(r.additional_payment).toLocaleString():'-'}</td><td class="num ${adjClass}">${adjText}</td><td class="num ${currentClass}">${currentText}</td>`;frag.appendChild(tr)}tb.appendChild(frag)}
function renderHistory(d){$('#paymentHistory').innerHTML=d.payments.length?d.payments.map(p=>{if(p.is_balance_edit){const e=Number(p.balance_effect||0);return `<div class="history-item balance-edit-history"><div><b>${p.payment_date}</b> · 금액수정<br><span>${esc(p.memo||'')}</span></div><div><b class="balance-edit-effect">${e>0?'+':e<0?'−':''}${fmt(Math.abs(e))}</b><br><span>${esc(p.created_by||'')}</span></div></div>`}return `<div class="history-item"><div><b>${p.payment_date}</b> · ${esc(p.method||'')}<br><span>${esc(p.memo||'')}</span></div><div><b>${fmt(p.amount)}</b><br><span>${esc(p.created_by||'')}</span></div></div>`}).join(''):'<div class="history-item"><span>추가 입금·금액수정 없음</span></div>';$('#contactHistory').innerHTML=d.contacts.length?d.contacts.map(c=>`<div class="history-item"><div><b>${c.contact_date}</b> · ${esc(c.contact_method)}</div><div><b>${esc(c.status)}</b><br><span>${esc(c.memo||c.created_by||'')}</span></div></div>`).join(''):'<div class="history-item"><span>연락 기록 없음</span></div>'}
async function savePayment(){if(!state.selected)return toast('회원을 먼저 선택해주세요.');const amount=Number($('#paymentAmount').value||0);if(amount<=0)return toast('입금액을 입력해주세요.');await api(`/api/receivables/members/${state.selected}/payments`,{method:'POST',body:JSON.stringify({payment_date:$('#paymentDate').value,amount,method:$('#paymentMethod').value,memo:$('#paymentMemo').value})});toast('입금이 저장되었습니다.');await Promise.all([loadMembers(false),selectMember(state.selected,state.selectedClosure)]);await loadSummary()}
function openBalanceEdit(){const m=state.detail?.member;if(!m)return toast('회원을 먼저 선택해주세요.');const b=Number(m.balance||0);$('#balanceEditMember').textContent=`${m.name||''} · ${m.vehicle_number||'-'}`;$('#balanceEditCurrent').textContent=b<0?`선납 ${fmt(Math.abs(b))}`:fmt(b);$('#balanceEditType').value=b<0?'선납':b>0?'미수금':'완납';$('#balanceEditAmount').value=b===0?'':String(Math.abs(b));$('#balanceEditAmount').disabled=b===0;$('#balanceEditDate').value=today();$('#balanceEditReason').value='';$('#balanceEditModal').classList.remove('hidden');$('#balanceEditModal').setAttribute('aria-hidden','false');setTimeout(()=>$('#balanceEditReason').focus(),40)}
function closeBalanceEdit(){const el=$('#balanceEditModal');if(!el)return;el.classList.add('hidden');el.setAttribute('aria-hidden','true')}
function syncBalanceEditType(){const type=$('#balanceEditType').value;const input=$('#balanceEditAmount');input.disabled=type==='완납';if(type==='완납')input.value='0';else if(Number(input.value||0)===0&&state.detail?.member){input.value=String(Math.abs(Number(state.detail.member.balance||0))||'')}}
async function saveBalanceEdit(){if(!state.selected||!state.detail?.member)return toast('회원을 먼저 선택해주세요.');const type=$('#balanceEditType').value;const amount=type==='완납'?0:Number($('#balanceEditAmount').value||0);if(type!=='완납'&&amount<0)return toast('금액을 확인해주세요.');const reason=$('#balanceEditReason').value.trim();if(reason.length<2)return toast('수정 사유를 입력해주세요.');const old=Number(state.detail.member.balance||0);const target=type==='선납'?-amount:type==='완납'?0:amount;const oldText=old<0?`선납 ${fmt(Math.abs(old))}`:fmt(old);const targetText=target<0?`선납 ${fmt(Math.abs(target))}`:fmt(target);if(old===target){closeBalanceEdit();return toast('현재 금액과 같습니다.')}if(!confirm(`${state.detail.member.name} 금액을 수정합니다.\n\n${oldText} → ${targetText}\n\n사유: ${reason}\n\n저장할까요?`))return;const btn=$('#saveBalanceEditBtn'),txt=btn.textContent;btn.disabled=true;btn.textContent='저장 중…';try{await api(`/api/receivables/members/${state.selected}/balance`,{method:'PATCH',body:JSON.stringify({balance_type:type,amount,effective_date:$('#balanceEditDate').value,reason})});closeBalanceEdit();toast('금액이 수정되었습니다.');await Promise.all([loadMembers(false),selectMember(state.selected,state.selectedClosure),loadSummary()])}finally{btn.disabled=false;btn.textContent=txt}}
async function saveContact(){if(!state.selected)return toast('회원을 먼저 선택해주세요.');await api(`/api/receivables/members/${state.selected}/contacts`,{method:'POST',body:JSON.stringify({contact_date:$('#contactDate').value,contact_method:$('#contactMethod').value,status:$('#contactStatus').value,memo:$('#contactMemo').value})});toast('연락 기록이 저장되었습니다.');await Promise.all([loadMembers(false),selectMember(state.selected,state.selectedClosure)])}
function closeMemberActionModal(){state.actionMember=null;$('#memberActionModal').classList.add('hidden');$('#memberActionModal').setAttribute('aria-hidden','true')}
async function refreshActionNumber(){const type=$('#memberActionType').value;const d=await api(`/api/closures/next-number/${encodeURIComponent(type)}`).catch(()=>null);$('#memberActionMgmt').value=d?.next_number||'';const extra=$('#transferExtra');extra.classList.toggle('hidden',type==='폐업');$('#transfereeWrap').classList.toggle('hidden',type!=='양도');$('#memberActionTransferee').value='';$('#memberActionRegion').value=''}
async function openMemberAction(m){if(!m||!m.member_id)return;if(!m.active)return toast('이미 폐업·양도·이관 처리된 회원입니다.');state.actionMember=m;$('#actionMemberName').textContent=`${m.name||'-'} · 회원업무`;$('#actionMemberVehicle').textContent=`${m.vehicle_number||'-'} · ${m.region||'-'}`;$('#memberActionType').value='폐업';$('#memberActionReceipt').value=today();$('#memberActionDate').value=today();$('#memberActionReason').value='';$('#memberActionModal').classList.remove('hidden');$('#memberActionModal').setAttribute('aria-hidden','false');await refreshActionNumber()}
async function saveMemberAction(){const m=state.actionMember;if(!m)return;const type=$('#memberActionType').value;const payload={closure_type:type,receipt_date:$('#memberActionReceipt').value,closure_date:$('#memberActionDate').value,management_number:$('#memberActionMgmt').value.trim(),reason:$('#memberActionReason').value.trim(),transferee:type==='양도'?$('#memberActionTransferee').value.trim():'',transfer_region:type==='폐업'?'':$('#memberActionRegion').value.trim()};if(!payload.closure_date)return toast('처리일을 입력해주세요.');if(!payload.management_number)return toast('관리번호를 확인해주세요.');if(type==='양도'&&!payload.transferee)return toast('양수인을 입력해주세요.');if(type==='이관'&&!payload.transfer_region)return toast('이관지역을 입력해주세요.');if(!confirm(`${m.name} 회원을 ${type} 처리하시겠습니까?\n처리 후 신규 부과는 중단되고 기존 미수·선납·입금·연락 이력은 유지됩니다.`))return;const btn=$('#saveMemberActionBtn');btn.disabled=true;try{const d=await api(`/api/members/${m.member_id}/close`,{method:'POST',body:JSON.stringify(payload)});toast(`${type} 처리 완료 · ${d.management_number||payload.management_number}`);closeMemberActionModal();clearDetail();await Promise.all([loadMembers(true),loadSummary()])}finally{btn.disabled=false}}
function openBulkImport(){state.bulkBatchId=null;state.bulkRows=[];state.manualRowId=null;$('#bulkFileInput').value='';$('#bulkSummary').classList.add('hidden');$('#bulkTableWrap').classList.add('hidden');$('#manualMatchPanel').classList.add('hidden');$('#bulkPostBtn').disabled=true;$('#bulkImportModal').classList.remove('hidden');$('#bulkImportModal').setAttribute('aria-hidden','false')}
function closeBulkImport(){state.manualRowId=null;$('#manualMatchPanel').classList.add('hidden');$('#bulkImportModal').classList.add('hidden');$('#bulkImportModal').setAttribute('aria-hidden','true')}
function bulkStatusLabel(s){return s==='matched'?'자동매칭':s==='review'?'확인필요':s==='duplicate'?'중복제외':s==='posted'?'반영완료':s}
function renderBulkImport(d){const b=d.batch||{};state.bulkBatchId=b.id||null;state.bulkRows=d.rows||[];$('#bulkSummary').classList.remove('hidden');$('#bulkTableWrap').classList.remove('hidden');$('#bulkTotalRows').textContent=Number(b.total_rows||0).toLocaleString();$('#bulkMatchedRows').textContent=Number(b.matched_rows||0).toLocaleString();$('#bulkReviewRows').textContent=Number(b.review_rows||0).toLocaleString();$('#bulkDuplicateRows').textContent=Number(b.duplicate_rows||0).toLocaleString();$('#bulkPostedRows').textContent=Number(b.posted_rows||0).toLocaleString();$('#bulkTotalAmount').textContent=fmt(b.total_amount||0);$('#bulkPostBtn').disabled=Number(b.matched_rows||0)<=0;const order={review:0,matched:1,duplicate:2,posted:3};const rows=[...state.bulkRows].sort((a,b)=>(order[a.status]??9)-(order[b.status]??9)||a.id-b.id);const tb=$('#bulkImportRows');tb.innerHTML='';const frag=document.createDocumentFragment();for(const r of rows.slice(0,500)){const tr=document.createElement('tr');tr.className=`bulk-row ${r.status}`;const matched=r.matched_member_id?`<b>${esc(r.matched_name||'-')}</b><small>${esc(r.matched_vehicle||'')} · ${esc(r.matched_region||'')}</small>`:'<b>-</b><small>회원 확인 필요</small>';const manage=r.status==='posted'?'<span class="bulk-done">완료</span>':r.status==='duplicate'?'<span class="bulk-muted">제외</span>':`<button type="button" class="bulk-match-btn" data-row-id="${r.id}">${r.matched_member_id?'변경':'회원 연결'}</button>`;tr.innerHTML=`<td>${esc(r.transaction_date||'-')}</td><td><b>${esc(r.payer_name||'-')}</b><small>${esc(r.vehicle_number||r.memo||'')}</small></td><td class="num"><b>${fmt(r.amount)}</b></td><td><div class="bulk-match-cell">${matched}<em>${esc(r.match_reason||'')}</em></div></td><td><span class="bulk-status ${r.status}">${bulkStatusLabel(r.status)}</span></td><td>${manage}</td>`;frag.appendChild(tr)}tb.appendChild(frag);tb.querySelectorAll('.bulk-match-btn').forEach(btn=>btn.onclick=()=>openManualMatch(Number(btn.dataset.rowId)));if(rows.length>500){const tr=document.createElement('tr');tr.innerHTML=`<td colspan="6" class="empty-row">화면에는 우선 500건만 표시합니다. 일괄반영은 전체 ${rows.length.toLocaleString()}건 기준으로 처리됩니다.</td>`;tb.appendChild(tr)}}
async function previewBulkImport(){const file=$('#bulkFileInput').files?.[0];if(!file)return toast('통장/결제 파일을 선택해주세요.');const btn=$('#bulkPreviewBtn');btn.disabled=true;const old=btn.textContent;btn.textContent='분석 중…';try{const fd=new FormData();fd.append('source_type',$('#bulkSourceType').value);fd.append('file',file);const headers={};if(token())headers.Authorization=`Bearer ${token()}`;const r=await fetch('/api/receivables/imports/preview',{method:'POST',headers,body:fd,cache:'no-store'});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'파일 분석에 실패했습니다.');renderBulkImport(d);toast(`자동매칭 ${Number(d.batch?.matched_rows||0).toLocaleString()}건 · 확인필요 ${Number(d.batch?.review_rows||0).toLocaleString()}건`)}finally{btn.disabled=false;btn.textContent=old}}
async function reloadBulkImport(){if(!state.bulkBatchId)return;renderBulkImport(await api(`/api/receivables/imports/${state.bulkBatchId}`))}
async function openManualMatch(rowId){state.manualRowId=rowId;const r=state.bulkRows.find(x=>x.id===rowId);if(!r)return;$('#manualMatchRowInfo').textContent=`${r.payer_name||'입금자 없음'} · ${fmt(r.amount)}`;$('#manualMatchSearch').value=r.payer_name||r.vehicle_number||'';$('#manualMatchResults').innerHTML='';$('#manualMatchPanel').classList.remove('hidden');await searchManualMembers()}
async function searchManualMembers(){const q=$('#manualMatchSearch').value.trim();if(!q)return;const d=await api(`/api/receivables/import-member-search?q=${encodeURIComponent(q)}&limit=15`);const box=$('#manualMatchResults');box.innerHTML='';if(!(d.items||[]).length){box.innerHTML='<div class="bulk-no-result">검색 결과 없음</div>';return}for(const m of d.items){const b=document.createElement('button');b.type='button';b.className='manual-result';b.innerHTML=`<b>${esc(m.name)}</b><span>${esc(m.vehicle_number||'-')} · ${esc(m.region||'-')} · ${m.active?'활성':'폐업'}</span>`;b.onclick=()=>applyManualMatch(m.member_id);box.appendChild(b)}}
async function applyManualMatch(memberId){if(!state.manualRowId)return;await api(`/api/receivables/imports/rows/${state.manualRowId}/match`,{method:'PATCH',body:JSON.stringify({member_id:memberId})});$('#manualMatchPanel').classList.add('hidden');state.manualRowId=null;await reloadBulkImport();toast('회원 연결 완료')}
async function postBulkImport(){if(!state.bulkBatchId)return;const btn=$('#bulkPostBtn');if(!confirm('자동매칭/수동연결된 거래를 수납원장에 일괄 반영하시겠습니까?\n확인필요·중복 건은 반영하지 않습니다.'))return;btn.disabled=true;const old=btn.textContent;btn.textContent='반영 중…';try{const d=await api(`/api/receivables/imports/${state.bulkBatchId}/post`,{method:'POST'});toast(`일괄수납 ${Number(d.posted_rows||0).toLocaleString()}건 · ${fmt(d.posted_amount||0)} 반영`);await reloadBulkImport();await Promise.all([loadMembers(false),loadSummary()])}finally{btn.disabled=false;btn.textContent=old}}

const UPLUS_BRIDGE='http://127.0.0.1:18765';
async function uplusBridgeFetch(path,opt={}){const ctl=new AbortController();const t=setTimeout(()=>ctl.abort(),3000);try{const r=await fetch(UPLUS_BRIDGE+path,{...opt,signal:ctl.signal,headers:{'Content-Type':'application/json',...(opt.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.message||'U+ PC 연동 오류');return d}finally{clearTimeout(t)}}
async function checkUplusBridge(showToast=false){const st=$('#uplusBridgeStatus'),box=$('#uplusSetupBox');try{const d=await uplusBridgeFetch('/health',{method:'GET'});if(st){st.textContent=`연결됨 · ${d.product||'LG U+ CRM Pro'} · Bridge ${d.version||''}`;st.className='uplus-bridge-status ok'}box?.classList.add('hidden');if(showToast)toast('U+ CRM Pro PC 연동이 연결되어 있습니다.');return true}catch(e){if(st){st.textContent='연결 안 됨 · 이 PC에서 U+ CRM Pro Bridge를 실행해주세요.';st.className='uplus-bridge-status bad'}box?.classList.remove('hidden');if(showToast)toast('U+ CRM Pro PC 연동을 확인해주세요.');return false}}
function defaultSmsText(m){const ms=moneyState(m);if(Number(m.balance||0)>0)return `[강원도 개인소형화물협회] ${m.name}님, 현재 ${m.account_type} 미수금 ${ms.text}이 확인되어 안내드립니다. 확인 부탁드립니다.`;if(Number(m.balance||0)<0)return `[강원도 개인소형화물협회] ${m.name}님, 현재 선납금 ${ms.text}이 확인됩니다. 문의사항이 있으시면 협회로 연락 바랍니다.`;return `[강원도 개인소형화물협회] ${m.name}님 안내드립니다.`}
function openSmsComposer(m){state.smsMember=m;$('#smsRecipient').textContent=`${m.name||''} · ${m.mobile||m.phone||'번호없음'}`;$('#smsText').value=defaultSmsText(m);$('#smsOverlay').classList.remove('hidden');$('#smsOverlay').setAttribute('aria-hidden','false');checkUplusBridge(false)}
function closeSmsComposer(){state.smsMember=null;$('#smsOverlay').classList.add('hidden');$('#smsOverlay').setAttribute('aria-hidden','true')}
async function copyUplusPayload(){const m=state.smsMember;if(!m)return;const payload=`수신번호: ${m.mobile||m.phone||''}\n\n${$('#smsText').value||''}`;try{await navigator.clipboard.writeText(payload);toast('수신번호와 문자내용을 복사했습니다.')}catch(e){toast('클립보드 복사에 실패했습니다.')}}
async function sendSmsNow(){const m=state.smsMember;if(!m)return;const phone=(m.mobile||m.phone||'').replace(/\D/g,'');const text=($('#smsText').value||'').trim();if(!phone)return toast('핸드폰번호가 없습니다.');if(!text)return toast('문자 내용을 입력해주세요.');try{const rr=await uplusBridgeFetch('/send',{method:'POST',body:JSON.stringify({phone,name:m.name||'',message:text,auto_send:true})});if(rr.sent){await api(`/api/receivables/members/${m.member_id}/contacts`,{method:'POST',body:JSON.stringify({contact_date:today(),contact_method:'문자',status:'문자발송',memo:`U+ CRM Pro 문자발송: ${text.slice(0,120)}`})}).catch(()=>null);toast('U+ CRM Pro에서 문자를 전송했습니다.');closeSmsComposer();await loadMembers(false);return}if(rr.prepared){toast('U+ CRM Pro 작성창에 번호/내용을 넣었습니다. 전송 버튼을 확인해주세요.');return}throw new Error(rr.message||'CRM Pro 자동입력을 완료하지 못했습니다.')}catch(e){$('#uplusSetupBox')?.classList.remove('hidden');toast(e.name==='AbortError'?'U+ PC 연동 프로그램에 연결할 수 없습니다.':(e.message||'U+ 문자 연동에 실패했습니다.'))}}

function bind(){$$('[data-perf-view]').forEach(btn=>btn.onclick=()=>setPerformanceView(btn.dataset.perfView));const compareBtn=$('#applyMonthCompare');if(compareBtn)compareBtn.onclick=()=>loadMonthComparison().catch(e=>toast(e.message));const monthA=$('#compareMonthA'),monthB=$('#compareMonthB');if(monthA)monthA.onchange=()=>{if(monthB&&monthA.value>monthB.value)monthB.value=monthA.value};if(monthB)monthB.onchange=()=>{if(monthA&&monthA.value>monthB.value)monthA.value=monthB.value};const smsClose=$('#smsCloseBtn');if(smsClose)smsClose.onclick=closeSmsComposer;const smsCancel=$('#smsCancelBtn');if(smsCancel)smsCancel.onclick=closeSmsComposer;const smsSend=$('#smsSendBtn');if(smsSend)smsSend.onclick=()=>sendSmsNow();const uplusCheck=$('#uplusCheckBtn');if(uplusCheck)uplusCheck.onclick=()=>checkUplusBridge(true);const uplusCopy=$('#uplusCopyBtn');if(uplusCopy)uplusCopy.onclick=copyUplusPayload;const smsOverlay=$('#smsOverlay');if(smsOverlay)smsOverlay.onclick=e=>{if(e.target===smsOverlay)closeSmsComposer()};const detailSms=$('#detailSmsBtn');if(detailSms)detailSms.onclick=()=>{if(state.detail?.member)openSmsComposer(state.detail.member)};const editBalance=$('#editBalanceBtn');if(editBalance)editBalance.onclick=openBalanceEdit;const closeBalance=$('#closeBalanceEditBtn');if(closeBalance)closeBalance.onclick=closeBalanceEdit;const cancelBalance=$('#cancelBalanceEditBtn');if(cancelBalance)cancelBalance.onclick=closeBalanceEdit;const saveBalance=$('#saveBalanceEditBtn');if(saveBalance)saveBalance.onclick=()=>saveBalanceEdit().catch(e=>toast(e.message));const balanceType=$('#balanceEditType');if(balanceType)balanceType.onchange=syncBalanceEditType;const balanceModal=$('#balanceEditModal');if(balanceModal)balanceModal.onclick=e=>{if(e.target===balanceModal)closeBalanceEdit()};const bulkBtn=$('#bulkImportBtn');if(bulkBtn)bulkBtn.onclick=openBulkImport;$('#closeBulkImportBtn').onclick=closeBulkImport;$('#bulkImportModal').onclick=e=>{if(e.target.id==='bulkImportModal')closeBulkImport()};$('#bulkPreviewBtn').onclick=()=>previewBulkImport().catch(e=>toast(e.message));$('#bulkPostBtn').onclick=()=>postBulkImport().catch(e=>toast(e.message));$('#closeManualMatchBtn').onclick=()=>{$('#manualMatchPanel').classList.add('hidden');state.manualRowId=null};$('#manualMatchSearchBtn').onclick=()=>searchManualMembers().catch(e=>toast(e.message));$('#manualMatchSearch').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();searchManualMembers().catch(err=>toast(err.message))}};const excelBtn=$('#excelExportBtn');if(excelBtn)excelBtn.onclick=()=>downloadFilteredExcel().catch(e=>toast(e.message));$$('[data-closure-mode]').forEach(b=>b.onclick=async()=>{$$('[data-closure-mode]').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.closureMode=b.dataset.closureMode;clearDetail();await loadMembers(true)});$$('.tab').forEach(b=>b.onclick=async()=>{$$('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.view=b.dataset.view;if(state.view==='closed'){state.closureMode='current';$$('[data-closure-mode]').forEach(x=>x.classList.toggle('active',x.dataset.closureMode==='current'))}clearDetail();await loadMembers(true)});let timer;$('#searchInput').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>loadMembers(true).catch(e=>toast(e.message)),120)};['regionFilter','accountFilter','contactFilter','billingFilter'].forEach(id=>$('#'+id).onchange=()=>loadMembers(true).catch(e=>toast(e.message)));$('#prevPageBtn').onclick=()=>{if(state.page>1){state.page--;loadMembers(false).catch(e=>toast(e.message))}};$('#nextPageBtn').onclick=()=>{if(state.page<state.pages){state.page++;loadMembers(false).catch(e=>toast(e.message))}};$$('.quick-amounts [data-amount]').forEach(b=>b.onclick=()=>$('#paymentAmount').value=b.dataset.amount);$('#fullAmountBtn').onclick=()=>{if(state.detail)$('#paymentAmount').value=Math.max(0,state.detail.member.balance)};$('#savePaymentBtn').onclick=()=>savePayment().catch(e=>toast(e.message));$('#saveContactBtn').onclick=()=>saveContact().catch(e=>toast(e.message));$('#openMemberWorkBtn').onclick=()=>{if(state.detail?.member)openMemberAction(state.detail.member).catch(e=>toast(e.message))};$('#memberActionType').onchange=()=>refreshActionNumber().catch(e=>toast(e.message));$('#closeActionModalBtn').onclick=closeMemberActionModal;$('#cancelActionBtn').onclick=closeMemberActionModal;$('#memberActionModal').onclick=e=>{if(e.target.id==='memberActionModal')closeMemberActionModal()};$('#saveMemberActionBtn').onclick=()=>saveMemberAction().catch(e=>toast(e.message));$('#syncBtn').onclick=async()=>{const d=await api('/api/receivables/sync');toast(`DB 동기화 완료 · 신규 ${d.profiles_created} / 부과 ${d.charges_created} / 잘못된부과정리 ${d.charges_removed||0} · Excel 재반영 없음`);await loadMembers(false);await loadSummary()};$('#kpiPendingCard').onclick=()=>{$('.tab[data-view="payment"]').click();$('#billingFilter').value='pending';loadMembers(true).catch(e=>toast(e.message))};$('#kpiPrepaidCard').onclick=()=>{$('.tab[data-view="payment"]').click();$('#billingFilter').value='prepaid';loadMembers(true).catch(e=>toast(e.message))};$('#logoutBtn').onclick=()=>{['access_token','token','authToken'].forEach(k=>localStorage.removeItem(k));location.href='/login'};$('#loginForm').onsubmit=async e=>{e.preventDefault();const body=new URLSearchParams({username:$('#loginUsername').value,password:$('#loginPassword').value});try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body});const d=await r.json();if(!r.ok)throw new Error(d.detail||'로그인 실패');saveToken(d.access_token);$('#loginOverlay').classList.add('hidden');await init()}catch(err){$('#loginError').textContent=err.message}}}
async function init(){if(!token()){$('#loginOverlay').classList.remove('hidden');return}try{await Promise.all([loadMeta(),loadMembers(true)]);await loadSummary();$('#paymentDate').value=today();$('#contactDate').value=today()}catch(e){if(!/로그인/.test(e.message))toast(e.message)}}
bind();init();
