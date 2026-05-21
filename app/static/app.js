// ===== 강원도 개인소형화물협회 업무관리 시스템 v6 =====

const REGIONS = ['춘천시','원주시','강릉시','동해시','태백시','속초시','삼척시',
                 '홍천군','횡성군','영월군','평창군','정선군','철원군','화천군',
                 '양구군','인제군','고성군','양양군'];
const CLOSURE_TYPES = ['폐업','양도','이관','사망','말소','기타'];
const CHANGE_TYPES = ['주소지변경','상호변경','구조변경','전속계약 업체변경','등록이관','이전전출','대표자변경','성명변경','번호변경','변동변경','기타'];
const FUEL_TYPES = ['경유','휘발유','LPG','전기','하이브리드','CNG','기타'];
const VEH_TYPES = ['1톤','1톤미만','카고형','밴형','특장차','냉동차','기타'];

const CATS = {
  members: {label:'회원관리',   tabs:[{id:'candidates',label:'예정자/양도양수'},{id:'individual',label:'개인회원'},{id:'delivery',label:'택배회원'}]},
  permits: {label:'인허가/변경', tabs:[{id:'new-registrations',label:'신규등록대장'},{id:'transfer-ledger',label:'양도양수대장'},{id:'closures',label:'폐업현황'},{id:'change-history',label:'변경이력대장'}]},
  reports: {label:'보고/집계',   tabs:[{id:'dashboard',label:'회원대시보드'},{id:'monthly-report',label:'월례보고서'}]},
  excel:   {label:'엑셀 업로드', tabs:[{id:'upload',label:'파일 업로드'},{id:'history',label:'업로드 이력'},{id:'errors',label:'오류 확인'}]},
};

const ST = {
  cat:'members', sub:'candidates',
  user:{role:localStorage.getItem('userRole'),name:localStorage.getItem('userName'),full:localStorage.getItem('userFullName')},
  fl:{}, inner:{},
  reportYear:new Date().getFullYear(), reportMonth:new Date().getMonth()+1,
  sort:{},   // {pageKey: {field, dir}}
};

// ===== API =====
async function api(method,url,body=null,isForm=false){
  const opts={method,headers:{Authorization:`Bearer ${localStorage.getItem('authToken')}`}};
  if(body&&!isForm){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
  else if(body&&isForm) opts.body=body;
  let res;
  try{res=await fetch(url,opts);}
  catch(netErr){toast('서버 연결 오류','err');throw new Error('net:'+netErr.message);}
  if(res.status===401){logout();return null;}
  if(!res.ok){
    let errBody='';
    try{errBody=await res.text();}catch{}
    let msg='';
    try{const j=JSON.parse(errBody);msg=Array.isArray(j.detail)?j.detail.map(x=>(x.loc?x.loc.join('.')+': ':'')+x.msg).join(' | '):(j.detail||j.message||errBody);}
    catch{msg=errBody||res.statusText;}
    const fullMsg=`[${res.status}] ${method} ${url}\n${msg}`;
    console.error('API Error:', fullMsg);
    toast(`${res.status} 오류: ${msg.slice(0,120)}`,'err');
    throw new Error(fullMsg);
  }
  return res.headers.get('content-type')?.includes('json')?res.json():res;
}
async function dl(url,fn){
  try{
    const r=await fetch(url,{headers:{Authorization:`Bearer ${localStorage.getItem('authToken')}`}});
    if(!r.ok){toast('다운로드 실패','err');return;}
    const b=await r.blob(),a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(b),download:fn});
    a.click();URL.revokeObjectURL(a.href);
  }catch{toast('다운로드 오류','err');}
}
function logout(){['authToken','userRole','userName','userFullName'].forEach(k=>localStorage.removeItem(k));window.location.href='/login';}
function isAdmin(){return ST.user.role==='admin';}

// ===== TOAST =====
function toast(msg,type='ok'){
  const c=document.getElementById('toastBox'),t=document.createElement('div');
  t.className=`toast toast-${type}`;t.textContent=msg;c.appendChild(t);
  setTimeout(()=>{t.classList.add('fade');setTimeout(()=>t.remove(),280);},3500);
}

// ===== MODAL =====
let _mr=null;
function openModal(title,body,footer='',cls=''){
  document.getElementById('modalTitle').textContent=title;
  document.getElementById('modalBd').innerHTML=body;
  document.getElementById('modalFt').innerHTML=footer;
  document.getElementById('modal').className='modal '+cls;
  document.getElementById('modalBg').style.display='flex';
}
function closeModal(){document.getElementById('modalBg').style.display='none';if(_mr){_mr(false);_mr=null;}}
function cfm(msg){
  return new Promise(r=>{
    _mr=r;
    openModal('확인',`<p style="font-size:13.5px;padding:4px 0;color:var(--c-text-2)">${msg}</p>`,
      `<button class="btn br btn-sm" id="_cy">확인</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'mxs');
    document.getElementById('_cy').onclick=()=>{r(true);_mr=null;closeModal();};
  });
}

// ===== VALUE HELPERS =====
const e_=v=>v==null?'':String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fv=v=>{
  if(v==null||v==='')return '-';
  const s=String(v).trim();
  return(s===''||s==='nan'||s==='None'||s==='null'||s==='NaN')?'-':s;
};
function fvDate(d1,d2){
  const v1=fv(d1),v2=fv(d2);
  return v1!=='-'?v1:(v2!=='-'?v2:'-');
}
function catBadge(c){return c==='개인'?`<span class="badge b-pri">개인</span>`:c==='택배'?`<span class="badge b-yellow">택배</span>`:`<span class="badge b-gray">${e_(c)||'-'}</span>`;}
function memBadge(s){return s==='가입'?`<span class="badge b-sky">가입</span>`:s==='미가입'?`<span class="badge b-pink">미가입</span>`:`<span class="badge b-gray">${fv(s)}</span>`;}
function dtBadge(d){return d==='이전자료'?`<span class="badge b-purple">이전</span>`:`<span class="badge b-pri">신규</span>`;}
function ctBadge(t){const m={'폐업':'b-danger','양도':'b-warn','이관':'b-purple','사망':'b-gray','말소':'b-gray'};return `<span class="badge ${m[t]||'b-gray'}">${t||'-'}</span>`;}
function chBadge(t){const m={'주소지변경':'b-sky','상호변경':'b-pri','구조변경':'b-teal','전속계약 업체변경':'b-yellow','등록이관':'b-purple','이전전출':'b-pink','대표자변경':'b-warn','성명변경':'b-pri','번호변경':'b-teal'};return `<span class="badge ${m[t]||'b-gray'}" style="font-size:10px">${t||'-'}</span>`;}

// FORM HELPERS
function rsel(name,sel=''){return `<select name="${name}" class="fc"><option value="">선택</option>${REGIONS.map(r=>`<option value="${r}" ${r===sel?'selected':''}>${r}</option>`).join('')}</select>`;}
function rselflt(id,sel=''){return `<select id="${id}" class="fsel"><option value="">전체 지역</option>${REGIONS.map(r=>`<option value="${r}" ${r===sel?'selected':''}>${r}</option>`).join('')}</select>`;}
function ssel(name,opts,sel=''){return `<select name="${name}" class="fc">${opts.map(o=>`<option value="${o}" ${o===sel?'selected':''}>${o}</option>`).join('')}</select>`;}
function fi(name,label,val='',req=false){return `<div class="fi"><label>${label}${req?'<span class="req">*</span>':''}</label><input class="fc" name="${name}" value="${e_(val)}" ${req?'required':''}></div>`;}
function fri(name,label,opts,sel=''){
  // 3인자 호출 대응: fri(name, opts, sel) → label 자동 생성
  if(Array.isArray(label)){sel=opts||'';opts=label;label=name;}
  if(!Array.isArray(opts)){
    console.warn('[fri] opts is not array',{name,label,opts,sel});
    sel=sel||opts||'';opts=[''];
  }
  const safeOpts=opts.map(o=>o??'');
  const safeSel=String(sel??'');
  return `<div class="fi"><label>${label}</label><select name="${name}" class="fc">${safeOpts.map(o=>`<option value="${o}" ${String(o)===safeSel?'selected':''}>${o||'(선택)'}</option>`).join('')}</select></div>`;
}
function fta(name,label,val='',cls=''){return `<div class="fi ${cls}"><label>${label}</label><textarea name="${name}" class="fc">${e_(val)}</textarea></div>`;}
function fph(name,label,val=''){return `<div class="fi"><label>${label}</label><input class="fc fmt-phone" name="${name}" value="${e_(val)}" placeholder="010-0000-0000" inputmode="numeric" maxlength="13"></div>`;}
function frn(name,label,val=''){return `<div class="fi"><label>${label}</label><input class="fc fmt-resident" name="${name}" value="${e_(val)}" placeholder="000000-0000000" inputmode="numeric" maxlength="14"></div>`;}

// 전화번호 자동 하이픈 포맷
function _autoPhone(v){
  const d=v.replace(/\D/g,'').slice(0,11);
  if(!d)return '';
  if(d.startsWith('02')){
    if(d.length<=2)return d;
    if(d.length<=5)return `${d.slice(0,2)}-${d.slice(2)}`;
    if(d.length<=9)return `${d.slice(0,2)}-${d.slice(2,-4)}-${d.slice(-4)}`;
    return `${d.slice(0,2)}-${d.slice(2,6)}-${d.slice(6,10)}`;
  }
  if(d.length<=3)return d;
  if(d.length<=6)return `${d.slice(0,3)}-${d.slice(3)}`;
  if(d.length<=10)return `${d.slice(0,3)}-${d.slice(3,6)}-${d.slice(6)}`;
  return `${d.slice(0,3)}-${d.slice(3,7)}-${d.slice(7)}`;
}
// 주민등록번호 자동 하이픈 포맷
function _autoResident(v){
  const d=v.replace(/\D/g,'').slice(0,13);
  return d.length<=6?d:`${d.slice(0,6)}-${d.slice(6)}`;
}
// 입력 포맷 이벤트 바인딩
function _bindFmt(scope){
  const s=typeof scope==='string'?document.getElementById(scope):scope;
  if(!s)return;
  s.querySelectorAll('.fmt-phone').forEach(el=>{
    el.addEventListener('input',function(){
      const p=this.selectionStart,old=this.value;
      this.value=_autoPhone(this.value);
      const diff=this.value.length-old.length;
      try{this.setSelectionRange(p+diff,p+diff);}catch(e){}
    });
  });
  s.querySelectorAll('.fmt-resident').forEach(el=>{
    el.addEventListener('input',function(){
      const p=this.selectionStart,old=this.value;
      this.value=_autoResident(this.value);
      const diff=this.value.length-old.length;
      try{this.setSelectionRange(p+diff,p+diff);}catch(e){}
    });
  });
}
function _validateFmt(form){return true;} // 형식 검증 없음 - 항상 저장 허용

// PAGINATION
function pgn(data,onPage){
  const {total,page,pages,limit}=data;
  const from=total?(page-1)*limit+1:0,to=Math.min(page*limit,total);
  let nums='';
  for(let i=Math.max(1,page-2);i<=Math.min(pages,page+2);i++)
    nums+=`<button class="pgn-btn ${i===page?'on':''}" data-p="${i}">${i}</button>`;
  return `<div class="pgn"><span>총 <strong>${total.toLocaleString()}</strong>건 (${from}–${to})</span>
    <div class="pgn-btns"><button class="pgn-btn" data-p="${page-1}" ${page<=1?'disabled':''}>‹</button>${nums}
    <button class="pgn-btn" data-p="${page+1}" ${page>=pages?'disabled':''}>›</button></div></div>`;
}
function bindPgn(wid,fn){document.getElementById(wid)?.querySelectorAll('.pgn-btn:not([disabled])').forEach(b=>b.addEventListener('click',()=>fn(+b.dataset.p)));}

// ===== SORTING (날짜 기반 최신순/오래된순만 지원) =====
function plainHeaders(headers){
  return headers.map(({label,noSort})=>
    `<th class="${noSort?'no-sort':''}">${label}</th>`
  ).join('');
}

// 날짜 정렬 셀렉트 HTML
function dateOrderSel(id,val='desc'){
  // 날짜+관리번호 통합 정렬 셀렉트
  return `<select id="${id}" class="fsel" style="min-width:120px">
    <option value="desc" ${val==='desc'?'selected':''}>날짜 최신순</option>
    <option value="asc" ${val==='asc'?'selected':''}>날짜 오래된순</option>
    <option value="mgmt_desc" ${val==='mgmt_desc'?'selected':''}>관리번호↓</option>
    <option value="mgmt_asc" ${val==='mgmt_asc'?'selected':''}>관리번호↑</option>
  </select>`;
}

function getSortParams(){return {};}

window.showYearDetail=async(year,category)=>{
  const labelMap={new:`${year}년 신규 (신${String(year).slice(-2)}-*)`,transfer:`${year}년 양도양수 (양${String(year).slice(-2)}-*)`,closure:`${year}년 폐업/양도/이관`,change:`${year}년 변경`};
  openModal(`📋 ${labelMap[category]}`,`<div id="ydBody">로딩 중...</div>`,`<button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'mlg');
  const d=await api('GET',`/api/dashboard/year-detail?year=${year}&category=${category}`).catch(()=>null);
  if(!d){document.getElementById('ydBody').innerHTML='오류';return;}
  const cols={
    new:'<tr><th>관리번호</th><th>지역</th><th>차량번호</th><th>성명</th><th>인가일자</th><th>상태</th></tr>',
    transfer:'<tr><th>관리번호</th><th>지역</th><th>차량번호</th><th>양도자</th><th>양수자</th><th>접수일자</th></tr>',
    closure:'<tr><th>자료</th><th>관리번호</th><th>구분</th><th>지역</th><th>차량번호</th><th>성명</th><th>접수일자</th></tr>',
    change:'<tr><th>지역</th><th>차량번호</th><th>성명</th><th>변경유형</th><th>변경일자</th><th>변경내용</th></tr>',
  };
  const rowFn={
    new:r=>`<tr><td><strong>${fv(r.management_number)}</strong></td><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${fv(r.approval_date)}</td><td>${r.status==='closed'?'<span class="badge b-gray" style="font-size:10px">폐업</span>':'활성'}</td></tr>`,
    transfer:r=>`<tr><td><strong>${fv(r.management_number)}</strong></td><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.transferor)}</td><td>${fv(r.transferee)}</td><td>${fv(r.receipt_date)}</td></tr>`,
    closure:r=>`<tr><td><span class="badge ${r.data_type==='이전자료'?'b-gray':'b-sky'}" style="font-size:10px">${r.data_type||''}</span></td><td><strong>${fv(r.management_number)}</strong></td><td>${ctBadge(r.closure_type)}</td><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${fv(r.receipt_date||r.closure_date)}</td></tr>`,
    change:r=>`<tr><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${fv(r.change_type)}</td><td>${fv(r.change_date)}</td><td style="max-width:200px;font-size:11px">${fv(r.after_value)}</td></tr>`,
  };
  document.getElementById('ydBody').innerHTML=`<p style="font-size:12px;color:var(--c-text-3);margin-bottom:8px">총 ${d.total}건</p><div class="tbl-wrap"><table><thead>${cols[category]}</thead><tbody>${(d.items||[]).map(rowFn[category]).join('')}</tbody></table></div>`;
};

// ── 대시보드 통계 클릭 → 대상자 목록 모달 ──
window.showVtypeList=async(category)=>{
  openModal(`🚗 차종별 목록: ${category}`,`<div id="vtypeListBody" style="padding:8px">로딩 중...</div>`,
    `<button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'mlg');
  try{
    const d=await api('GET',`/api/dashboard/vtype-list?category=${encodeURIComponent(category)}`);
    if(!d||!d.items){document.getElementById('vtypeListBody').innerHTML='데이터 없음';return;}
    document.getElementById('vtypeListBody').innerHTML=`
      <p style="font-size:12px;color:var(--c-text-3);margin-bottom:8px">총 ${d.total}대</p>
      <div class="tbl-wrap"><table>
        <thead><tr><th>관리번호</th><th>지역</th><th>차량번호</th><th>성명</th><th>원본차종</th><th>유종</th><th>분류결과</th></tr></thead>
        <tbody>${d.items.map(r=>`<tr>
          <td>${fv(r.management_number)}</td><td>${fv(r.region)}</td>
          <td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td>
          <td style="font-size:11px;color:var(--c-text-3)">${fv(r.vehicle_type_raw)}</td>
          <td>${fv(r.fuel_category)}</td>
          <td><span class="badge b-sky" style="font-size:10px">${fv(r.vehicle_category)}</span></td>
        </tr>`).join('')}</tbody>
      </table></div>`;
  }catch(e){document.getElementById('vtypeListBody').innerHTML='오류 발생';}
};

window.showStatList=async(statType)=>{
  const labelMap={
    'joined':'협회 가입자 (가입일자 있음)',
    'not_joined':'미가입자 (가입일자 없음)',
    'delivery_employed':'택배 취업신고 (자격증명발급일자 있음)',
    'delivery_not_employed':'택배 미신고 (자격증명발급일자 없음)',
  };
  const label=labelMap[statType]||statType;
  openModal(`📊 ${label}`,`<div id="statListBody" style="padding:8px">로딩 중...</div>`,
    `<button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'mlg');
  try{
    const d=await api('GET',`/api/dashboard/stat-list?stat_type=${statType}`);
    if(!d||!d.items){document.getElementById('statListBody').innerHTML='데이터 없음';return;}
    const rows=d.items.map(r=>`<tr>
      <td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td>
      <td>${fv(r.category)}</td><td>${fv(r.membership_date)}</td>
      <td>${fv(r.certificate_issue_date)}</td><td>${fv(r.certificate_number)}</td>
      <td>${fv(r.approval_date)}</td>
    </tr>`).join('');
    document.getElementById('statListBody').innerHTML=`
      <p style="font-size:12px;color:var(--c-text-3);margin-bottom:8px">총 ${d.total}명</p>
      <div class="tbl-wrap"><table>
        <thead><tr><th>지역</th><th>차량번호</th><th>성명</th><th>구분</th><th>가입일자</th><th>자격증발급일자</th><th>자격증발급번호</th><th>인가일자</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
  }catch(e){document.getElementById('statListBody').innerHTML='오류 발생';}
};

// ===== DETAIL VIEW (섹션별, no raw_data JSON) =====
function buildDetailSections(sections){
  return `<div class="dtl-sections">${sections.map(sec=>{
    const visFields=sec.fields.filter(([,v])=>v&&fv(v)!=='-');
    if(!visFields.length) return '';
    return `<div class="dtl-sec">
      <div class="dtl-sec-hd">${sec.title}</div>
      <div class="dtl-grid">${visFields.map(([l,v,full])=>`
        <div class="dtl-item ${full?'full':''}">
          <div class="dtl-lbl">${l}</div>
          <div class="dtl-val${l==='관리번호'||l==='자격증번호'?' mono':''}">${e_(fv(v))}</div>
        </div>`).join('')}
      </div>
    </div>`;
  }).join('')}</div>`;
}

function extractRehasaSections(raw){
  if(!raw) return [];
  const rehasa=Object.entries(raw).filter(([k])=>k.includes('재허가')).filter(([,v])=>v&&v.trim()).map(([k,v])=>[k,v]);
  const edu=Object.entries(raw).filter(([k])=>k.includes('교육')||k.includes('점검')||k.includes('서면')||k.includes('일제')).filter(([,v])=>v&&v.trim()).map(([k,v])=>[k,v]);
  return {rehasa,edu};
}

window.viewMember=async(id)=>{
  const r=await api('GET',`/api/members/${id}`).catch(()=>null);if(!r)return;
  const raw=r.raw_data||{};
  const {rehasa,edu}=extractRehasaSections(raw);

  // DB 저장 필드 우선, fallback으로 raw_data
  const rawAddr=r.official_address||raw['공문 주소']||raw['공문주소']||raw['서류주소']||'';
  const rawAgent=r.agent_name||raw['대리인']||raw['위임인']||'';
  const rawAgentRes=r.agent_resident_number||raw['대리인주민등록번호']||raw['대리인주민번호']||'';
  const rawAgentMob=r.agent_mobile||raw['대리인핸드폰']||raw['대리인핸드폰번호']||'';
  const rawReapproval=r.reapproval_date||raw['재허가']||raw['재허가일자']||'';
  const rawStruct=raw['구조변경']||raw['구조']||'';
  const rawCompChg=raw['전속업체 변경']||raw['업체변경']||'';
  const rawMemo2=raw['비고2 ']||raw['비고2']||raw['비고 2']||'';
  const rawMemo3=raw['비고3']||raw['비고 3']||'';
  const rawTelMemo=raw['전화 메모']||raw['전화메모']||raw['연락메모']||'';

  const sections=[
    {title:'기본 정보',fields:[['관리번호',r.management_number],['지역',r.region],['차량번호',r.vehicle_number],['성명',r.name],['개인/택배',r.category],['가입여부',r.membership_status]]},
    {title:'연락처 / 주소',fields:[['전화번호',r.phone],['핸드폰',r.mobile],['주소',r.address,true],['공문주소',rawAddr,true],['대리인',rawAgent],['대리인 주민등록번호',rawAgentRes],['대리인 핸드폰',rawAgentMob]]},
    {title:'인허가 정보',fields:[['인가일자',r.approval_date],['가입일자',r.membership_date],['재허가',rawReapproval],['자격증발급일자',r.certificate_issue_date],['자격증발급번호',r.certificate_number],['운전면허번호',r.driver_license_number],['주민등록번호',r.resident_number],['사업자번호',r.business_number]]},
    {title:'차량 정보',fields:[['차종',r.vehicle_type],['유종',r.fuel_type],['소속업체',r.affiliated_company],['구조변경',r.structure_change||rawStruct,true],['전속업체 변경',rawCompChg,true]]},
    ...(rehasa.length?[{title:'재허가 이력',fields:rehasa}]:[]),
    ...(edu.length?[{title:'교육 / 점검',fields:edu}]:[]),
    {title:'비고 / 메모',fields:[['비고',r.memo,true],['전화메모',rawTelMemo,true],['비고2',rawMemo2,true],['비고3',rawMemo3,true]]},
    // 양도양수 정보 (연결된 양도양수 이력이 있는 경우)
    ...(r.transfer_info?[{title:'양도양수 정보',fields:[
      ['관리번호(양도양수)',r.transfer_info.management_number],
      ['양도인(성명)',r.transfer_info.transferor],
      ['양수자(성명)',r.transfer_info.transferee],
      ['접수일자',r.transfer_info.receipt_date],
      ['인가일자',r.transfer_info.approval_date],
      ['가입일자',r.transfer_info.membership_date],
      ['자격증명발급일자',r.transfer_info.certificate_issue_date],
      ['자격증명발급번호',r.transfer_info.certificate_number],
      ['장부정리',r.transfer_info.ledger_update],
      ['전산보고',r.transfer_info.computer_report],
      ['비고',r.transfer_info.memo,true],
    ]}]:[]),
  ];
  openModal('회원 상세정보',buildDetailSections(sections),
    `<button class="btn bp btn-sm" onclick="editMember(${id});closeModal()">수정</button><button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'mlg');
};

window.viewTransfer=async(id)=>{
  const r=await api('GET',`/api/transfer-ledger/${id}`).catch(()=>null);if(!r)return;
  const raw=r.raw_data||{};
  // raw_data에서 추가 업무 필드 추출
  const rawExtra=Object.entries(raw)
    .filter(([k,v])=>v&&fv(v)!=='-'&&!['id','seq_number','region','vehicle_number','transferor','transferee','receipt_date','approval_date','membership_date','certificate_issue_date','certificate_number','driver_license_number','ledger_update','computer_report','memo','resident_number','phone','mobile','address','management_number','허가번호'].includes(k))
    .slice(0,20);
  const sections=[
    {title:'기본 정보',fields:[['번호',r.seq_number],['관리번호',r.management_number],['접수일자',r.receipt_date],['지역',r.region],['차량번호',r.vehicle_number],['차종',raw['차종']||raw['vehicle_type']||''],['유종',raw['유종']||raw['fuel_type']||'']]},
    {title:'양도자 / 양수자',fields:[['양도자',r.transferor],['양수자',r.transferee],['주민등록번호',r.resident_number],['전화번호',r.phone],['핸드폰',r.mobile],['주소',r.address,true]]},
    {title:'인허가 정보',fields:[['인가일자',r.approval_date],['가입일자',r.membership_date],['자격증발급일자',r.certificate_issue_date],['자격증발급번호',r.certificate_number],['운전면허번호',r.driver_license_number]]},
    {title:'행정 정보',fields:[['장부정리',r.ledger_update],['전산보고',r.computer_report],['비고',r.memo,true]]},
    ...(rawExtra.length?[{title:'원본 엑셀 데이터',fields:rawExtra.map(([k,v])=>[k,v,false])}]:[]),
  ];
  openModal('양도양수 상세정보',buildDetailSections(sections),
    `<button class="btn bp btn-sm" onclick="editTransfer(${id});closeModal()">수정</button><button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'mlg');
};

window.viewClosure=async(id)=>{
  const r=await api('GET',`/api/closures/${id}`).catch(()=>null);if(!r)return;
  const raw=r.raw_data||{};
  // raw_data에서 추가 정보 추출
  const rawPhone=raw['전화번호']||raw['연락처']||'';
  const rawMobile=raw['핸드폰']||raw['휴대폰']||raw['핸드폰번호']||'';
  const rawResNo=raw['주민등록번호']||raw['주민번호']||'';
  const rawMemDate=raw['가입일자']||raw['가입일']||'';
  const rawCertDate=raw['자격증명발급일자']||raw['자격증발급일자']||raw['발급일자']||'';
  const rawCertNo=raw['자격증명발급번호']||raw['자격증발급번호']||raw['발급번호']||'';
  const rawDrvLic=raw['운전면허번호']||raw['면허번호']||'';
  const rawAddr=raw['주소']||raw['주소지']||'';
  const rawReceipt=raw['접수일자']||raw['접수일']||'';
  const rawSeq=raw['번호']||'';
  const sections=[
    {title:'기본 정보',fields:[['관리번호',r.management_number],['번호',rawSeq],['처리구분',r.closure_type],['자료구분',r.data_type],['지역',r.region],['차량번호',r.vehicle_number],['성명',r.name],['상호',r.company_name]]},
    {title:'차량 정보',fields:[['차종',r.vehicle_type||raw['차종']||''],['유종',r.fuel_type||raw['유종']||''],['구조변경',r.structure_change||raw['구조변경']||'',true]]},
    {title:'연락처 / 주소',fields:[['전화번호',r.phone||rawPhone],['핸드폰',r.mobile||rawMobile],['주소',r.address||rawAddr,true],['공문주소',r.official_address||'',true],['주민등록번호',r.resident_number||rawResNo]]},
    {title:'회원 / 자격',fields:[['가입여부',r.membership_status],['가입일자',r.membership_date||rawMemDate],['인가일자',r.approval_date],['자격증명발급일자',r.certificate_issue_date||rawCertDate],['자격증명발급번호',r.certificate_number||rawCertNo],['운전면허번호',r.driver_license_number||rawDrvLic]]},
    {title:'소속 / 대리인',fields:[['소속업체',r.affiliated_company||''],['대리인',r.agent_name||''],['대리인 핸드폰',r.agent_mobile||'']]},
    {title:'폐업처리 정보',fields:[
      ['접수일자',r.receipt_date||rawReceipt],
      ['처리일자',r.closure_date],
      ['사유',r.reason,true],
      ...(r.transferee?[['양수인',r.transferee]]:r.closure_type==='양도'?[['양수인','']]:[] ),
      ...(r.transfer_region?[['이관/양도지역',r.transfer_region]]:r.closure_type==='이관'?[['이관지역','']]:[] ),
      ['비고',r.memo||'',true],
    ]},
  ];
  openModal('폐업현황 상세정보',buildDetailSections(sections),
    `<button class="btn bp btn-sm" onclick="editClosure(${id});closeModal()">수정</button><button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'mlg');
};

window.viewChange=async(id)=>{
  const r=await api('GET',`/api/change-history/${id}`).catch(()=>null);if(!r)return;
  const sections=[
    {title:'기본 정보',fields:[['변경유형',r.change_type],['처리일자',fvDate(r.change_date,r.receipt_date)],['지역',r.region],['차량번호',r.vehicle_number],['성명',r.name]]},
    {title:'변경 내용',fields:[['변경 전',r.before_value,true],['변경 후',r.after_value,true],['비고',r.memo,true]]},
  ];
  openModal('변경이력 상세정보',buildDetailSections(sections),
    `<button class="btn bp btn-sm" onclick="editChange(${id});closeModal()">수정</button><button class="btn bo btn-sm" onclick="closeModal()">닫기</button>`,'msm');
};

// ===== NAVIGATION =====
function navigate(cat,sub){
  ST.cat=cat;ST.sub=sub;
  document.querySelectorAll('.cat-btn').forEach(b=>b.classList.toggle('active',b.dataset.cat===cat));
  const tabs=CATS[cat]?.tabs||[];
  document.getElementById('subBar').innerHTML=tabs.map(t=>
    `<button class="sub-tab ${t.id===sub?'active':''}" data-sub="${t.id}">${t.label}</button>`).join('');
  document.querySelectorAll('.sub-tab').forEach(b=>b.addEventListener('click',()=>navigate(ST.cat,b.dataset.sub)));
  document.getElementById('content').innerHTML=`<div class="loading-box"><div class="spin"></div><p>로딩 중...</p></div>`;
  ({
    candidates:renderCandidates, individual:()=>renderMember('개인'), delivery:()=>renderMember('택배'),
    'new-registrations':renderNewRegistrations,
    'transfer-ledger':renderTransferLedger, closures:renderClosures, 'change-history':renderChangeHistory,
    dashboard:renderDashboard, 'monthly-report':renderMonthlyReport,
    upload:renderUpload, history:renderUploadHistory, errors:renderUploadErrors,
  }[sub]||(() => document.getElementById('content').innerHTML='<p style="padding:20px">준비 중</p>'))();
}

// ===== CANDIDATES =====
async function renderCandidates(){
  const inner=ST.inner.candidates||'candidate';
  document.getElementById('content').innerHTML=`
    <div class="inner-tab-bar">
      <button class="inner-tab ${inner==='candidate'?'active':''}" id="itCand">📋 예정자</button>
      <button class="inner-tab ${inner==='transfer'?'active':''}" id="itTrans">🔄 양도양수 등록</button>
    </div>
    <div id="innerContent"></div>`;
  document.getElementById('itCand').onclick=()=>{ST.inner.candidates='candidate';renderCandidates();};
  document.getElementById('itTrans').onclick=()=>{ST.inner.candidates='transfer';renderCandidates();};
  if(inner==='candidate') await renderCandidateSection();
  else await renderTransferSection();
}

async function renderCandidateSection(){
  const f=ST.fl.cand||{};
  document.getElementById('innerContent').innerHTML=`
    <div class="card">
      <div class="card-hd"><div class="card-hd-l"><span class="card-ico">✏️</span><span class="card-ttl">예정자 입력</span></div></div>
      <div class="card-bd"><form id="candForm"><div class="fg">
        <div class="fi"><label>지역</label>${rsel('region','')}</div>
        ${fi('vehicle_number','차량번호')} ${fi('name','성명')}
        ${frn('resident_number','주민등록번호')}
        ${fi('phone','전화번호')} ${fph('mobile','핸드폰')}
        <div class="fi cs2"><label>주소</label><input class="fc" name="address"></div>
        ${fi('certificate_issue_date','자격증발급일자')} ${fi('certificate_number','자격증발급번호')}
        ${fi('driver_license_number','운전면허번호')}
        <div class="fi"><label>차종</label><input class="fc" name="vehicle_type" placeholder="예: 22,포터Ⅱ내장탑차 / 봉고 / 냉동탑차"></div>
        ${fri('fuel_type','유종',[''].concat(FUEL_TYPES),'')}
        ${fi('business_number','사업자번호')} ${fi('affiliated_company','소속업체')}
        ${fi('membership_date','가입일자','')}<span style="font-size:11px;color:var(--c-text-3);align-self:center">&nbsp;없으면 미가입</span>
        ${fta('memo','비고','','cs4')}
      </div>
      <div class="flex gap-8 mt8" style="justify-content:flex-end">
        <button type="button" class="btn bo btn-sm" onclick="document.getElementById('candForm').reset()">초기화</button>
        <button type="button" class="btn bg btn-sm" id="candSaveBtn">+ 저장</button>
      </div></form></div>
    </div>
    <div class="card">
      <div class="card-hd"><div class="card-hd-l"><span class="card-ico">📂</span><span class="card-ttl">예정자 목록</span><span class="cnt" id="cCnt">0건</span></div></div>
      <div class="frow">
        ${rselflt('cRegF',f.region||'')}
        <input class="srch" id="cSrch" placeholder="성명, 차량번호, 관리번호, 주민번호" value="${e_(f.search||'')}">
        <button class="btn bp btn-sm" id="cSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="cRstBtn">초기화</button>
      </div>
      <div id="cTbl"><div class="loading-box"><div class="spin"></div></div></div>
    </div>`;

  // 포맷 이벤트 바인딩 (폼 렌더 후)
  setTimeout(()=>_bindFmt('candForm'),0);

  const sk='cand';
  const hdrs=[{field:'region',label:'지역'},{field:'vehicle_number',label:'차량번호'},{field:'name',label:'성명'},{field:'phone',label:'전화번호'},{field:'mobile',label:'핸드폰'},{field:'vehicle_type',label:'차종'},{field:'certificate_number',label:'자격증번호'},{field:'affiliated_company',label:'소속업체'},{label:'관리',noSort:true}];
  const doSearch=async(pg=1)=>{
    ST.fl.cand={region:document.getElementById('cRegF').value,search:document.getElementById('cSrch').value.trim()};
    const q=new URLSearchParams({page:pg,limit:50,...getSortParams(sk),...Object.fromEntries(Object.entries(ST.fl.cand).filter(([,v])=>v))});
    const d=await api('GET',`/api/candidates?${q}`).catch(()=>null);if(!d)return;
    document.getElementById('cCnt').textContent=`${d.total}건`;
    const tw=document.getElementById('cTbl');
    if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">📋</div><p class="empty-txt">예정자가 없습니다.</p></div>`;return;}
    tw.innerHTML=`<div class="tbl-wrap"><table>
      <thead><tr>${plainHeaders(hdrs)}</tr></thead>
      <tbody>${d.items.map(r=>`<tr>
        <td>${fv(r.region)}</td>
        <td>${fv(r.vehicle_number)}</td>
        <td><a class="click-link" onclick="editCandidate(${r.id});return false">${fv(r.name)}</a></td>
        <td>${fv(r.phone)}</td><td>${fv(r.mobile)}</td>
        <td>${fv(r.vehicle_type)}</td>
        <td>${fv(r.certificate_number)}</td><td>${fv(r.affiliated_company)}</td>
        <td class="td-act">
          <button class="btn bp btn-xs" onclick="editCandidate(${r.id})">수정</button>
          <button class="btn-check" onclick="registerCandidate(${r.id},'${e_(r.vehicle_number)}','${e_(r.name)}')">✅ 등록</button>
          <button class="btn br btn-xs" onclick="deleteCandidate(${r.id})">삭제</button>
        </td></tr>`).join('')}</tbody>
    </table></div>${pgn(d,doSearch)}`;
    bindPgn('cTbl',doSearch);
  };
  document.getElementById('cSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('cSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById('cRstBtn').onclick=()=>{ST.fl.cand={};renderCandidateSection();};
  document.getElementById('candSaveBtn').onclick=async()=>{
    const form=document.getElementById('candForm');
    if(!_validateFmt(form))return;
    const fd=Object.fromEntries(new FormData(form));
    const r=await api('POST','/api/candidates',fd).catch(()=>null);
    if(r){toast('예정자 저장 완료');form.reset();doSearch(1);}
  };
  await doSearch(1);
}

window.editCandidate=async(id)=>{
  const r=await api('GET',`/api/candidates/${id}`).catch(()=>null);if(!r)return;
  openModal('예정자 수정',`<form id="cEditForm"><div class="fg">
    <div class="fi"><label>지역</label>${rsel('region',r.region||'')}</div>
    ${fi('vehicle_number','차량번호',r.vehicle_number||'')} ${fi('name','성명',r.name||'')}
    ${frn('resident_number','주민등록번호',r.resident_number||'')}
    ${fi('phone','전화번호',r.phone||'')} ${fph('mobile','핸드폰',r.mobile||'')}
    <div class="fi cs2"><label>주소</label><input class="fc" name="address" value="${e_(r.address||'')}"></div>
    ${fi('certificate_issue_date','자격증발급일자',r.certificate_issue_date||'')} ${fi('certificate_number','자격증발급번호',r.certificate_number||'')}
    ${fi('driver_license_number','운전면허번호',r.driver_license_number||'')}
    <div class="fi"><label>차종</label><input class="fc" name="vehicle_type" value="${e_(r.vehicle_type||'')}" placeholder="예: 22,포터Ⅱ내장탑차 / 봉고 / 냉동탑차"></div>
    ${fri('fuel_type','유종',[''].concat(FUEL_TYPES),r.fuel_type||'')}
    ${fi('business_number','사업자번호',r.business_number||'')} ${fi('affiliated_company','소속업체',r.affiliated_company||'')}
    ${fi('membership_date','가입일자',r.membership_date||'')}
    ${fta('memo','비고',r.memo||'','cs4')}
  </div></form>`,
  `<button class="btn bg btn-sm" id="_ceSave">저장</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'mlg');
  setTimeout(()=>_bindFmt(document.getElementById('cEditForm')),0);
  document.getElementById('_ceSave').onclick=async()=>{
    const form=document.getElementById('cEditForm');
    if(!_validateFmt(form))return;
    const fd=Object.fromEntries(new FormData(form));
    const res=await api('PUT',`/api/candidates/${id}`,fd).catch(()=>null);
    if(res){toast('수정되었습니다.');closeModal();renderCandidateSection();}
  };
};

window.registerCandidate=async(cid,vn,name)=>{
  const [nn,cand]=await Promise.all([
    api('GET','/api/members/next-new-number').catch(()=>null),
    api('GET',`/api/candidates/${cid}`).catch(()=>null),
  ]);
  const existingMemDate=cand?.membership_date||'';
  openModal('신규등록 처리',`
    <div class="info-box">차량번호: <strong>${e_(vn)}</strong> / 성명: <strong>${e_(name)}</strong>
    → ${vn.includes('배')?'택배회원':'개인회원'}으로 등록됩니다.</div>
    <div class="fg2 mt8">
      <div class="fi"><label>인가일자 <span class="req">*</span></label><input class="fc" id="regApprDate" placeholder="예: 2026-01-01"></div>
      <div class="fi"><label>가입일자 <span style="font-size:11px;color:var(--c-text-3)">(없으면 미가입)</span></label><input class="fc" id="regMemDate" value="${e_(existingMemDate)}" placeholder="예: 2026-01-15"></div>
      <div class="fi"><label>관리번호</label><input class="fc" id="regMgmtNum" value="${e_(nn?.next_number||'')}"></div>
    </div>`,
    `<button class="btn bg btn-sm" id="_rC">등록 완료</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'msm');
  document.getElementById('_rC').onclick=async()=>{
    const ad=document.getElementById('regApprDate').value.trim();
    if(!ad){toast('인가일자를 입력하세요','warn');return;}
    const md=document.getElementById('regMemDate').value.trim();
    const r=await api('POST',`/api/candidates/${cid}/register`,{
      approval_date:ad,
      membership_date:md,
      management_number:document.getElementById('regMgmtNum').value.trim()
    }).catch(()=>null);
    if(r){toast(`${r.category}회원 등록 완료 (${r.management_number}) - ${md?'가입':'미가입'}`);closeModal();renderCandidateSection();}
  };
};
window.deleteCandidate=async(cid)=>{
  if(!await cfm('이 예정자를 삭제하시겠습니까?'))return;
  try{await api('DELETE',`/api/candidates/${cid}`);toast('삭제');renderCandidateSection();}catch(e){}
};

async function renderTransferSection(){
  document.getElementById('innerContent').innerHTML=`
    <div class="card">
      <div class="card-hd"><div class="card-hd-l"><span class="card-ico">✏️</span><span class="card-ttl">양도양수 등록 (타 지역→강원도 전입)</span></div></div>
      <div class="card-bd"><form id="trSecForm"><div class="fg">
        <div class="fi"><label>지역</label>${rsel('region','')}</div>
        ${fi('vehicle_number','차량번호','')}
        ${fi('transferor','양도인(성명)','')}
        ${fi('name','양수자(성명)','')}
        ${fi('receipt_date','접수일자',new Date().toISOString().slice(0,10))}
        ${fi('approval_date','인가일자','')}
        ${fi('membership_date','가입일자','')}
        ${fi('resident_number','주민등록번호','')}
        ${fi('phone','전화번호','')} ${fi('mobile','핸드폰','')}
        <div class="fi cs2"><label>주소</label><input class="fc" name="address"></div>
        ${fi('certificate_issue_date','자격증발급일자','')} ${fi('certificate_number','자격증발급번호','')}
        ${fi('driver_license_number','운전면허번호','')}
        <div class="fi"><label>차종 (직접입력)</label><input class="fc" name="vehicle_type" placeholder="예: 22,포터Ⅱ내장탑차 / 1톤 냉동탑차"></div>
        ${fri('fuel_type','유종',[''].concat(FUEL_TYPES),'')}
        ${fi('business_number','사업자번호','')} ${fi('affiliated_company','소속업체','')}
        <div class="fi cs3"><label>비고</label><input class="fc" name="memo" placeholder="예: 경기→강원 이전전입"></div>
      </div>
      <div class="flex gap-8 mt8" style="justify-content:flex-end">
        <button type="button" class="btn bo btn-sm" onclick="document.getElementById('trSecForm').reset()">초기화</button>
        <button type="button" class="btn bg btn-sm" id="trSecSave">+ 저장</button>
      </div></form></div>
    </div>`;
  document.getElementById('trSecSave').onclick=async()=>{
    const fd=Object.fromEntries(new FormData(document.getElementById('trSecForm')));
    if(!fd.vehicle_number){toast('차량번호를 입력하세요','warn');return;}
    const nn=await api('GET','/api/members/next-transfer-number').catch(()=>null);
    const tl={
      receipt_date:fd.receipt_date||new Date().toISOString().slice(0,10),
      region:fd.region||'',vehicle_number:fd.vehicle_number,
      transferor:fd.transferor||'',
      transferee:fd.name,
      resident_number:fd.resident_number,address:fd.address,
      phone:fd.phone,mobile:fd.mobile,
      approval_date:fd.approval_date,
      membership_date:fd.membership_date||'',
      certificate_issue_date:fd.certificate_issue_date,certificate_number:fd.certificate_number,
      driver_license_number:fd.driver_license_number,
      vehicle_type:fd.vehicle_type||'',fuel_type:fd.fuel_type||'',
      business_number:fd.business_number||'',affiliated_company:fd.affiliated_company||'',
      memo:fd.memo};
    const trRec=await api('POST','/api/transfer-ledger',tl).catch(()=>null);
    if(!trRec)return;
    const mr=await api('POST',`/api/transfer-ledger/${trRec.id}/register-member`,{management_number:nn?.next_number||''}).catch(()=>null);
    if(mr){toast(`${mr.category}회원 등록 완료 (${mr.management_number})`);document.getElementById('trSecForm').reset();}
  };
}

// ===== MEMBER (개인/택배) =====
async function renderMember(category){
  const key=category==='개인'?'individual':'delivery';
  const f=ST.fl[key]||{};
  document.getElementById('content').innerHTML=`
    <div class="card">
      <div class="card-hd">
        <div class="card-hd-l"><span class="card-ico">${category==='개인'?'👤':'🚚'}</span>
          <span class="card-ttl">${category}회원</span><span class="cnt" id="mCnt">0건</span></div>
        <div class="flex gap-8">
          <button class="btn bg btn-sm" id="mAddBtn">+ 등록</button>
          <button class="btn bxl btn-sm" id="mXlBtn">엑셀 다운로드</button>
        </div>
      </div>
      <div class="frow">
        ${rselflt(`${key}RegF`,f.region||'')}
        <select id="${key}MemF" class="fsel"><option value="">가입/미가입</option><option value="가입">가입</option><option value="미가입">미가입</option></select>
        <select id="${key}SortF" class="fsel">
          <option value="default" ${(!f.member_sort||f.member_sort==='default')?'selected':''}>지역+차량번호순</option>
          <option value="mgmt_desc" ${f.member_sort==='mgmt_desc'?'selected':''}>관리번호 최신순</option>
          <option value="mgmt_asc" ${f.member_sort==='mgmt_asc'?'selected':''}>관리번호 오래된순</option>
          <option value="approval_desc" ${f.member_sort==='approval_desc'?'selected':''}>인가일자 최신순</option>
          <option value="approval_asc" ${f.member_sort==='approval_asc'?'selected':''}>인가일자 오래된순</option>
          <option value="join_desc" ${f.member_sort==='join_desc'?'selected':''}>가입일자 최신순</option>
          <option value="join_asc" ${f.member_sort==='join_asc'?'selected':''}>가입일자 오래된순</option>
        </select>
        <input class="srch" id="${key}Srch" placeholder="성명, 차량번호, 주소, 자격번호" value="${e_(f.search||'')}">
        <button class="btn bp btn-sm" id="${key}SrchBtn">조회</button>
        <button class="btn bo btn-sm" id="${key}RstBtn">초기화</button>
      </div>
      <div id="${key}Tbl"><div class="loading-box"><div class="spin"></div></div></div>
    </div>`;

  const hdrs=[
    {label:'관리번호'},{label:'지역'},{label:'차량번호'},{label:'성명'},{label:'주민등록번호'},
    {label:'핸드폰'},{label:'인가일자'},{label:'가입'},{label:'가입일자'},
    {label:'자격증명발급일자'},{label:'자격증명발급번호'},
    {label:'차종'},{label:'유종'},{label:'주소'},{label:'관리',noSort:true}
  ];

  const doSearch=async(pg=1)=>{
    const rawSort = document.getElementById(`${key}SortF`)?.value || 'default';
    // 이전 캐시에서 'desc'/'asc' 값이 남아있으면 'default'로 변환
    const member_sort_val = (rawSort==='desc'||rawSort==='asc') ? 'default' : rawSort;
    ST.fl[key]={category,region:document.getElementById(`${key}RegF`).value,membership_status:document.getElementById(`${key}MemF`).value,member_sort:member_sort_val,search:document.getElementById(`${key}Srch`).value.trim()};
    const q=new URLSearchParams({page:pg,limit:50,...Object.fromEntries(Object.entries(ST.fl[key]).filter(([,v])=>v))});
    const d=await api('GET',`/api/members?${q}`).catch(()=>null);if(!d)return;
    document.getElementById('mCnt').textContent=`${d.total.toLocaleString()}건`;
    const tw=document.getElementById(`${key}Tbl`);
    if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">${category==='개인'?'👤':'🚚'}</div><p class="empty-txt">데이터가 없습니다.</p></div>`;return;}
    tw.innerHTML=`<div class="tbl-wrap"><table>
      <thead><tr>${plainHeaders(hdrs)}</tr></thead>
      <tbody>${d.items.map(r=>`<tr>
        <td><a class="tbl-link" onclick="viewMember(${r.id});return false">${fv(r.management_number)}</a></td>
        <td>${fv(r.region)}</td>
        <td><a class="tbl-link" onclick="viewMember(${r.id});return false">${fv(r.vehicle_number)}</a></td>
        <td><a class="tbl-link" onclick="viewMember(${r.id});return false">${fv(r.name)}</a></td>
        <td style="font-size:11px">${fv(r.resident_number)}</td>
        <td>${fv(r.mobile)}</td>
        <td>${fv(r.approval_date)}</td>
        <td>${memBadge(r.membership_status)}</td>
        <td>${fv(r.membership_date)}</td>
        <td>${fv(r.certificate_issue_date)}</td>
        <td>${fv(r.certificate_number)}</td>
        <td title="${e_(r.vehicle_type)}">${fv(r.vehicle_type)}</td>
        <td>${fv(r.fuel_type)}</td>
        <td title="${e_(r.address)}">${fv(r.address)}</td>
        <td class="td-act">
          <button class="btn bp btn-xs" onclick="editMember(${r.id})">수정</button>
          <button class="btn br btn-xs" onclick="closeMember(${r.id},'${e_(r.name)}','${e_(r.vehicle_number)}')">폐업</button>
        </td></tr>`).join('')}</tbody>
    </table></div>${pgn(d,doSearch)}`;
    bindPgn(`${key}Tbl`,doSearch);
  };
  document.getElementById(`${key}SrchBtn`).onclick=()=>doSearch(1);
  document.getElementById(`${key}Srch`).onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById(`${key}RstBtn`).onclick=()=>{ST.fl[key]={};renderMember(category);};
  document.getElementById('mAddBtn').onclick=()=>editMember(null,category);
  document.getElementById('mXlBtn').onclick=()=>{
    const q=new URLSearchParams({category,...Object.fromEntries(Object.entries(ST.fl[key]||{}).filter(([k,v])=>v&&k!=='category'))});
    dl(`/api/members/export/excel?${q}`,`${category}회원.xlsx`);
  };
  await doSearch(1);
}

window.editMember=async(id,defaultCat='개인')=>{
  let r={management_number:'',region:'',vehicle_number:'',name:'',company_name:'',
    address:'',phone:'',mobile:'',category:defaultCat,
    membership_status:'가입',membership_date:'',approval_date:'',
    certificate_issue_date:'',certificate_number:'',driver_license_number:'',
    vehicle_type:'',fuel_type:'',business_number:'',affiliated_company:'',
    resident_number:'',memo:'',
    reapproval_date:'',official_address:'',
    agent_name:'',agent_resident_number:'',agent_mobile:''};
  if(id){
    r=await api('GET',`/api/members/${id}`).catch(()=>null);
    if(!r)return;
  }
  const cat=r.category||defaultCat||'개인';
  const isTaxi=(cat==='택배');
  const isInd=(cat==='개인');

  // 택배 전용 섹션 HTML
  const taxiSection=`
    <div class="fi-section-label cs4" style="color:var(--c-primary);font-weight:600;margin-top:8px;padding-top:8px;border-top:1px solid var(--c-border);font-size:12px">── 택배 전용 항목</div>
    ${fi('reapproval_date','재허가',r.reapproval_date||'')}
    <div class="fi cs2"><label>공문주소</label><input class="fc" name="official_address" value="${e_(r.official_address||'')}" placeholder="공문 발송 주소"></div>`;

  // 개인 전용 섹션 HTML
  const indSection=`
    <div class="fi-section-label cs4" style="color:var(--c-primary);font-weight:600;margin-top:8px;padding-top:8px;border-top:1px solid var(--c-border);font-size:12px">── 개인 전용 항목 (대리인)</div>
    ${fi('agent_name','대리인',r.agent_name||'')}
    ${frn('agent_resident_number','대리인 주민등록번호',r.agent_resident_number||'')}
    ${fph('agent_mobile','대리인 핸드폰번호',r.agent_mobile||'')}`;

  const formHtml=`<form id="mForm"><div class="fg">
    <input type="hidden" name="category" value="${e_(cat)}">
    ${fi('management_number','관리번호',r.management_number||'')}
    <div class="fi"><label>지역</label>${rsel('region',r.region||'')}</div>
    ${fi('vehicle_number','차량번호',r.vehicle_number||'',true)} ${fi('name','성명',r.name||'',true)}
    ${fi('phone','전화번호',r.phone||'')} ${fph('mobile','핸드폰',r.mobile||'')}
    <div class="fi cs2"><label>주소</label><input class="fc" name="address" value="${e_(r.address||'')}"></div>
    ${fri('membership_status','가입여부',['가입','미가입'],r.membership_status||'가입')}
    ${fi('membership_date','가입일자',r.membership_date||'')} ${fi('approval_date','인가일자',r.approval_date||'')}
    ${fi('certificate_issue_date','자격증발급일자',r.certificate_issue_date||'')} ${fi('certificate_number','자격증발급번호',r.certificate_number||'')}
    ${fi('driver_license_number','운전면허번호',r.driver_license_number||'')}
    <div class="fi"><label>차종</label><input class="fc" name="vehicle_type" value="${e_(r.vehicle_type||'')}" placeholder="예: 22,포터Ⅱ내장탑차"></div>
    ${fri('fuel_type','유종',[''].concat(FUEL_TYPES),r.fuel_type||'')}
    ${fi('affiliated_company','소속업체',r.affiliated_company||'')} ${frn('resident_number','주민등록번호',r.resident_number||'')}
    <div class="fi cs2"><label>구조변경</label><input class="fc" name="structure_change" value="${e_(r.structure_change||'')}" placeholder="예: 윙바디 변경, 냉동기 장착, 호로→윙바디"></div>
    ${isTaxi?taxiSection:''}
    ${isInd?indSection:''}
    ${(!id)?(taxiSection+indSection):''}
    ${fta('memo','비고',r.memo||'','cs4')}
  </div></form>`;

  openModal(id?'회원 수정':'회원 등록',formHtml,
    `<button class="btn bg btn-sm" id="_mSave">${id?'저장':'등록'}</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'mlg');

  setTimeout(()=>_bindFmt(document.getElementById('mForm')),0);
  document.getElementById('_mSave').onclick=async()=>{
    const form=document.getElementById('mForm');
    const fd=Object.fromEntries(new FormData(form));
    if(!fd.vehicle_number||!fd.name){toast('차량번호와 성명은 필수입니다','warn');return;}
    // 신규 등록 시 category 재계산
    if(!id) fd.category=fd.vehicle_number?.includes('배')?'택배':'개인';
    const btn=document.getElementById('_mSave');
    btn.disabled=true; btn.textContent='저장 중...';
    try{
      const res=await api(id?'PUT':'POST',id?`/api/members/${id}`:'/api/members',fd);
      if(res){toast(id?'수정되었습니다.':'등록되었습니다.');closeModal();navigate(ST.cat,ST.sub);}
    }catch(e){
      console.error('회원 저장 오류:', e, '전송 데이터:', fd);
      toast('저장 실패: '+((e&&e.message)||'서버 오류'),'err');
    }finally{btn.disabled=false; btn.textContent=id?'저장':'등록';}
  };
};

window.closeMember=async(id,name,vn)=>{
  openModal('폐업 처리',`
    <p class="warn-box" style="margin-bottom:10px"><strong>${e_(name)}</strong> (${e_(vn)}) 처리 방식을 선택하세요.</p>
    <div class="close-choices">
      <button class="close-choice" data-type="폐업"><strong>폐업</strong><span>사업 폐업 (폐-N)</span></button>
      <button class="close-choice" data-type="양도"><strong>양도</strong><span>타인 양도 (양-N)</span></button>
      <button class="close-choice" data-type="이관"><strong>이관</strong><span>타 지역 이관 (이-N)</span></button>
    </div>`,`<button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'msm');
  document.querySelectorAll('.close-choice').forEach(btn=>{
    btn.onclick=async()=>{
      const ct=btn.dataset.type;
      const nn=await api('GET',`/api/closures/next-number/${encodeURIComponent(ct)}`).catch(()=>null);
      const extraFields=ct==='양도'?`
        <div class="fi"><label>양수인</label><input class="fc" id="clTransferee" placeholder="양도받는 사람 성명"></div>
        <div class="fi"><label>양수지역</label><input class="fc" id="clTransferRegion" placeholder="예: 강원 원주시"></div>`:
        ct==='이관'?`
        <div class="fi cs2"><label>이관지역</label><input class="fc" id="clTransferRegion" placeholder="예: 서울 → 강원 춘천시"></div>`:'';
      openModal(`${ct} 처리`,`
        <div class="fg2">
          <div class="fi"><label>접수일자</label><input class="fc" id="clReceiptDate" placeholder="2026-01-01" value="${new Date().toISOString().slice(0,10)}"></div>
          <div class="fi"><label>처리일자 <span class="req">*</span></label><input class="fc" id="clDate" placeholder="2026-01-01"></div>
          <div class="fi"><label>관리번호</label><input class="fc" id="clMgmt" value="${e_(nn?.next_number||'')}"></div>
        </div>
        ${extraFields}
        <div class="fi mt8"><label>사유 / 비고</label><input class="fc" id="clReason"></div>`,
        `<button class="btn br btn-sm" id="_clC">${ct} 처리</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'msm');
      document.getElementById('_clC').onclick=async()=>{
        const cd=document.getElementById('clDate').value.trim();
        if(!cd){toast('처리일자를 입력하세요','warn');return;}
        const payload={
          closure_type:ct,
          receipt_date:(document.getElementById('clReceiptDate')?.value||'').trim(),
          closure_date:cd,
          management_number:document.getElementById('clMgmt').value.trim(),
          reason:document.getElementById('clReason').value.trim(),
          transferee:(document.getElementById('clTransferee')?.value||'').trim(),
          transfer_region:(document.getElementById('clTransferRegion')?.value||'').trim(),
        };
        const res=await api('POST',`/api/members/${id}/close`,payload).catch(()=>null);
        if(res){toast(`${ct} 처리 완료 (${res.management_number})`);closeModal();navigate(ST.cat,ST.sub);}
      };
    };
  });
};

// ===== 신규등록대장 =====
async function renderNewRegistrations(){
  const f=ST.fl.nr||{};
  document.getElementById('content').innerHTML=`
    <div class="card">
      <div class="card-hd">
        <div class="card-hd-l"><span class="card-ico">📋</span><span class="card-ttl">신규등록대장</span><span class="cnt" id="nrCnt">0건</span>
          <span class="badge b-sky" style="font-size:10px;margin-left:6px">인가일자 기준</span></div>
        <div class="flex gap-8">
          <button class="btn bxl btn-sm" id="nrXlBtn">엑셀 다운로드</button>
        </div>
      </div>
      <div class="frow">
        ${rselflt('nrRegF',f.region||'')}
        <select id="nrCatF" class="fsel"><option value="">개인+택배</option><option value="개인">개인</option><option value="택배">택배</option></select>
        <select id="nrDateF" class="fsel" style="min-width:120px">
        <option value="mgmt_desc" ${(f.member_sort||'mgmt_desc')==='mgmt_desc'?'selected':''}>관리번호 최신순</option>
        <option value="mgmt_asc" ${f.member_sort==='mgmt_asc'?'selected':''}>관리번호 오래된순</option>
        <option value="approval_desc" ${f.member_sort==='approval_desc'?'selected':''}>인가일자 최신순</option>
        <option value="approval_asc" ${f.member_sort==='approval_asc'?'selected':''}>인가일자 오래된순</option>
      </select>
        <input class="srch" id="nrSrch" placeholder="성명, 차량번호, 주소, 자격번호" value="${e_(f.search||'')}">
        <button class="btn bp btn-sm" id="nrSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="nrRstBtn">초기화</button>
      </div>
      <div id="nrTbl"><div class="loading-box"><div class="spin"></div></div></div>
    </div>`;

  const hdrs=[
    {label:'관리번호'},{label:'지역'},{label:'차량번호'},{label:'성명'},
    {label:'구분'},{label:'가입'},{label:'핸드폰'},{label:'인가일자'},{label:'가입일자'},
    {label:'자격증명발급일자'},{label:'자격증명발급번호'},{label:'차종'},{label:'유종'},
    {label:'주소'},{label:'비고'},{label:'관리',noSort:true}
  ];

  const doSearch=async(pg=1)=>{
    ST.fl.nr={
      region:document.getElementById('nrRegF').value,
      category:document.getElementById('nrCatF').value,
      member_sort:document.getElementById('nrDateF')?.value||'mgmt_desc',
      search:document.getElementById('nrSrch').value.trim()
    };
    const q=new URLSearchParams({page:pg,limit:50,mgmt_prefix:'신',status:'all',...Object.fromEntries(Object.entries(ST.fl.nr).filter(([,v])=>v))});
    const d=await api('GET',`/api/members?${q}`).catch(()=>null);if(!d)return;
    document.getElementById('nrCnt').textContent=`${d.total.toLocaleString()}건`;
    const tw=document.getElementById('nrTbl');
    if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">📋</div><p class="empty-txt">신규등록 데이터가 없습니다.</p></div>`;return;}
    tw.innerHTML=`<div class="tbl-wrap"><table>
      <thead><tr>${plainHeaders(hdrs)}</tr></thead>
      <tbody>${d.items.map(r=>`<tr>
        <td><a class="tbl-link" onclick="viewMember(${r.id});return false">${fv(r.management_number)}</a>${r.status==='closed'?'<span class="badge b-gray" style="font-size:10px;margin-left:4px">폐업</span>':''}</td>
        <td>${fv(r.region)}</td>
        <td><a class="tbl-link" onclick="viewMember(${r.id});return false">${fv(r.vehicle_number)}</a></td>
        <td><a class="tbl-link" onclick="viewMember(${r.id});return false">${fv(r.name)}</a></td>
        <td>${catBadge(r.category)}</td><td>${memBadge(r.membership_status)}</td>
        <td>${fv(r.mobile)}</td>
        <td><strong>${fv(r.approval_date)}</strong></td><td>${fv(r.membership_date)}</td>
        <td>${fv(r.certificate_issue_date)}</td><td>${fv(r.certificate_number)}</td>
        <td title="${e_(r.vehicle_type)}">${fv(r.vehicle_type)}</td><td>${fv(r.fuel_type)}</td>
        <td title="${e_(r.address)}">${fv(r.address)}</td>
        <td class="td-act">
          <button class="btn bp btn-xs" onclick="editMember(${r.id})">수정</button>
        </td></tr>`).join('')}</tbody>
    </table></div>${pgn(d,doSearch)}`;
    bindPgn('nrTbl',doSearch);
  };
  document.getElementById('nrSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('nrSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById('nrRstBtn').onclick=()=>{ST.fl.nr={};renderNewRegistrations();};
  document.getElementById('nrXlBtn').onclick=()=>{
    const q=new URLSearchParams({mgmt_prefix:'신',sort_by:'approval_date',sort_dir:'desc',...Object.fromEntries(Object.entries(ST.fl.nr||{}).filter(([,v])=>v))});
    dl(`/api/members/export/excel?${q}`,'신규등록대장.xlsx');
  };
  await doSearch(1);
}

// ===== TRANSFER LEDGER =====
async function renderTransferLedger(){
  const f=ST.fl.tl||{};
  document.getElementById('content').innerHTML=`
    <div class="card">
      <div class="card-hd">
        <div class="card-hd-l"><span class="card-ico">📋</span><span class="card-ttl">양도양수대장</span><span class="cnt" id="tlCnt">0건</span>
          <span class="badge b-sky" style="font-size:10px;margin-left:6px">접수일자 기준</span></div>
        <div class="flex gap-8">
          <button class="btn bg btn-sm" id="tlAddBtn">+ 등록</button>
          <button class="btn bxl btn-sm" id="tlXlBtn">엑셀 다운로드</button>
        </div>
      </div>
      <div class="frow">
        ${rselflt('tlRegF',f.region||'')}
        ${dateOrderSel('tlDateF',f.date_order||'mgmt_desc')}
        <input class="srch" id="tlSrch" placeholder="양도자, 양수자, 차량번호" value="${e_(f.search||'')}">
        <button class="btn bp btn-sm" id="tlSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="tlRstBtn">초기화</button>
      </div>
      <div id="tlTbl"><div class="loading-box"><div class="spin"></div></div></div>
    </div>`;

  // 요구사항 컬럼 순서 고정
  const hdrs=[
    {label:'관리번호'},{label:'번호'},{label:'접수일자'},{label:'지역'},
    {label:'차량번호'},{label:'양도자'},{label:'양수자'},{label:'핸드폰'},
    {label:'인가일자'},{label:'가입일자'},{label:'자격증명발급일자'},{label:'자격증명발급번호'},
    {label:'장부정리'},{label:'전산보고'},{label:'비고'},{label:'관리',noSort:true}
  ];

  const doSearch=async(pg=1)=>{
    const tlSort=document.getElementById('tlDateF')?.value||'mgmt_desc';const tlIsDate=(tlSort==='desc'||tlSort==='asc');ST.fl.tl={region:document.getElementById('tlRegF').value,member_sort:tlIsDate?undefined:tlSort,date_order:tlIsDate?tlSort:undefined,search:document.getElementById('tlSrch').value.trim()};
    const q=new URLSearchParams({page:pg,limit:50,...Object.fromEntries(Object.entries(ST.fl.tl).filter(([,v])=>v))});
    const tw=document.getElementById('tlTbl');
    let d=null;
    try{d=await Promise.race([api('GET',`/api/transfer-ledger?${q}`),new Promise((_,r)=>setTimeout(()=>r(new Error('timeout')),8000))]);}
    catch(e){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">⚠️</div><p class="empty-txt">데이터 조회 실패. 새로고침 해주세요.</p></div>`;return;}
    if(!d){return;}
    document.getElementById('tlCnt').textContent=`${d.total.toLocaleString()}건`;
    if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">📋</div><p class="empty-txt">데이터가 없습니다.</p></div>`;return;}
    tw.innerHTML=`<div class="tbl-wrap"><table>
      <thead><tr>${plainHeaders(hdrs)}</tr></thead>
      <tbody>${d.items.map(r=>`<tr>
        <td><strong style="color:var(--c-primary)">${fv(r.management_number)}</strong></td>
        <td>${fv(r.seq_number)}</td>
        <td><strong>${fv(r.receipt_date)}</strong></td>
        <td>${fv(r.region)}</td>
        <td><a class="tbl-link" onclick="viewTransfer(${r.id});return false">${fv(r.vehicle_number)}</a></td>
        <td><a class="tbl-link" onclick="viewTransfer(${r.id});return false">${fv(r.transferor)}</a></td>
        <td><a class="tbl-link" onclick="viewTransfer(${r.id});return false">${fv(r.transferee)}</a></td>
        <td>${fv(r.mobile)}</td>
        <td>${fv(r.approval_date)}</td>
        <td>${fv(r.membership_date)}</td>
        <td>${fv(r.certificate_issue_date)}</td>
        <td>${fv(r.certificate_number)}</td>
        <td>${fv(r.ledger_update)}</td>
        <td>${fv(r.computer_report)}</td>
        <td title="${e_(r.memo)}">${fv(r.memo)}</td>
        <td class="td-act" style="white-space:nowrap;min-width:120px">
          <button class="btn bp btn-xs" onclick="editTransfer(${r.id})" title="수정">수정</button>
          ${!r.member_id?`<button class="btn bo btn-xs" onclick="registerTransferMember(${r.id})" title="회원 상세 등록">회원상세</button>`:`<span class="badge b-teal" style="font-size:10px">등록완료</span>`}
          ${isAdmin()?`<button class="btn br btn-xs" onclick="deleteTransfer(${r.id})" title="삭제">삭제</button>`:''}
        </td></tr>`).join('')}</tbody>
    </table></div>${pgn(d,doSearch)}`;
    bindPgn('tlTbl',doSearch);
  };
  document.getElementById('tlSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('tlSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  // 초기화 시 mgmt_desc 유지
  document.getElementById('tlRstBtn').onclick=()=>{ST.fl.tl={member_sort:'mgmt_desc'};renderTransferLedger();};
  document.getElementById('tlAddBtn').onclick=()=>editTransfer(null);
  document.getElementById('tlXlBtn').onclick=()=>{
    const q=new URLSearchParams(Object.fromEntries(Object.entries(ST.fl.tl||{}).filter(([,v])=>v)));
    dl(`/api/transfer-ledger/export/excel?${q}`,'양도양수대장.xlsx');
  };
  await doSearch(1);
}

window.editTransfer=async(id)=>{
  let r={seq_number:'',receipt_date:'',region:'',vehicle_number:'',transferor:'',transferee:'',resident_number:'',address:'',phone:'',mobile:'',approval_date:'',membership_date:'',certificate_issue_date:'',certificate_number:'',ledger_update:'',driver_license_number:'',computer_report:'',memo:'',vehicle_type:'',fuel_type:'',structure_change:'',affiliated_company:''};
  if(id){r=await api('GET',`/api/transfer-ledger/${id}`).catch(()=>null);if(!r)return;}
  const raw_tl=(r.raw_data&&typeof r.raw_data==='object')?r.raw_data:{};
  openModal(id?'양도양수 수정':'양도양수 등록',`<form id="tlForm"><div class="fg">
    ${fi('management_number','관리번호',r.management_number||'')}
    ${fi('receipt_date','접수일자',r.receipt_date||'')}
    <div class="fi"><label>지역</label>${rsel('region',r.region||'')}</div>
    ${fi('vehicle_number','차량번호',r.vehicle_number||'',true)}
    ${fi('vehicle_type','차종',r.vehicle_type||raw_tl['차종']||'')}
    ${fri('fuel_type','유종',['','경유','LPG','전기','휘발유','CNG','하이브리드'],r.fuel_type||raw_tl['유종']||'')}
    <div class="fi cs2"><label>구조변경</label><input class="fc" name="structure_change" value="${e_(r.structure_change||'')}"></div>
    ${fi('transferor','양도자',r.transferor||'')}
    ${fi('transferee','양수자',r.transferee||'')}
    ${frn('resident_number','주민등록번호',r.resident_number||'')}
    <div class="fi cs2"><label>주소</label><input class="fc" name="address" value="${e_(r.address||'')}"></div>
    ${fi('phone','전화번호',r.phone||'')}
    ${fph('mobile','핸드폰',r.mobile||'')}
    ${fi('approval_date','인가일자',r.approval_date||'')}
    ${fi('membership_date','가입일자',r.membership_date||'')}
    ${fi('certificate_issue_date','자격증명발급일자',r.certificate_issue_date||'')}
    ${fi('certificate_number','자격증명발급번호',r.certificate_number||'')}
    ${fi('ledger_update','장부정리',r.ledger_update||'')}
    ${fi('driver_license_number','운전면허번호',r.driver_license_number||'')}
    ${fi('computer_report','전산보고',r.computer_report||'')}
    ${fta('memo','비고',r.memo||'','cs4')}
  </div></form>`,
  `<button class="btn bg btn-sm" id="_tlS">${id?'저장':'등록'}</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'mlg');
  document.getElementById('_tlS').onclick=async()=>{
    const form=document.getElementById('tlForm');if(!form.checkValidity()){form.reportValidity();return;}
    const fd=Object.fromEntries(new FormData(form));
    const res=await api(id?'PUT':'POST',id?`/api/transfer-ledger/${id}`:'/api/transfer-ledger',fd).catch(()=>null);
    if(res){toast(id?'수정':'등록');closeModal();renderTransferLedger();}
  };
};
window.registerTransferMember=async(tid)=>{
  const nn=await api('GET','/api/transfer-ledger/next-number').catch(()=>null);
  openModal('회원등록',`<div class="info-box">양수자 정보를 기준으로 회원 등록합니다.</div>
    <div class="fi mt8"><label>관리번호</label><input class="fc" id="_tMgmt" value="${e_(nn?.next_number||'')}"></div>`,
    `<button class="btn bg btn-sm" id="_tC">회원등록</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`,'msm');
  document.getElementById('_tC').onclick=async()=>{
    const r=await api('POST',`/api/transfer-ledger/${tid}/register-member`,{management_number:document.getElementById('_tMgmt').value.trim()}).catch(()=>null);
    if(r){toast(`${r.category}회원 등록 완료`);closeModal();renderTransferLedger();}
  };
};
window.deleteTransfer=async(id)=>{if(!await cfm('삭제?'))return;try{await api('DELETE',`/api/transfer-ledger/${id}`);toast('삭제');renderTransferLedger();}catch(e){};};

// ===== CLOSURES =====
async function renderClosures(){
  const f=ST.fl.cl||{};
  document.getElementById('content').innerHTML=`
    <div class="card">
      <div class="card-hd">
        <div class="card-hd-l"><span class="card-ico">🚫</span><span class="card-ttl">폐업현황</span><span class="cnt" id="clCnt">0건</span></div>
        <div class="flex gap-8">
          <button class="btn bg btn-sm" id="clAddBtn">+ 등록</button>
          <button class="btn bxl btn-sm" id="clXlBtn">엑셀 다운로드</button>
        </div>
      </div>
      <div class="frow">
        ${rselflt('clRegF',f.region||'')}
        <select id="clTypF" class="fsel"><option value="">전체 구분</option>${CLOSURE_TYPES.map(t=>`<option value="${t}">${t}</option>`).join('')}</select>
        <select id="clDtF" class="fsel"><option value="">신규+이전</option><option value="신규자료">신규자료</option><option value="이전자료">이전자료</option></select>
        ${dateOrderSel('clSortF',f.date_order||'desc')}
        <input class="srch" id="clSrch" placeholder="관리번호, 성명, 차량번호" value="${e_(f.search||'')}">
        <button class="btn bp btn-sm" id="clSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="clRstBtn">초기화</button>
      </div>
      <div id="clTbl"><div class="loading-box"><div class="spin"></div></div></div>
    </div>`;

  const sk='cl';
  const hdrs=[{label:'관리번호'},{label:'구분'},{label:'지역'},{label:'차량번호'},{label:'성명'},{label:'양수인'},{label:'이관지역'},{label:'접수일자'},{label:'처리일자'},{label:'관리',noSort:true}];

  const doSearch=async(pg=1)=>{
    ST.fl.cl={region:document.getElementById('clRegF').value,closure_type:document.getElementById('clTypF').value,data_type:document.getElementById('clDtF').value,date_order:document.getElementById('clSortF').value,search:document.getElementById('clSrch').value.trim()};
    const q=new URLSearchParams({page:pg,limit:50,...getSortParams(sk),...Object.fromEntries(Object.entries(ST.fl.cl).filter(([,v])=>v))});
    const d=await api('GET',`/api/closures?${q}`).catch(()=>null);if(!d)return;
    document.getElementById('clCnt').textContent=`${d.total.toLocaleString()}건`;
    const tw=document.getElementById('clTbl');
    if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">🚫</div><p class="empty-txt">데이터가 없습니다.</p></div>`;return;}
    tw.innerHTML=`<div class="tbl-wrap"><table>
      <thead><tr>${plainHeaders(hdrs)}</tr></thead>
      <tbody>${d.items.map(r=>`<tr>
        <td><a class="click-link" onclick="viewClosure(${r.id});return false"><strong>${fv(r.management_number)}</strong></a></td>
        <td>${ctBadge(r.closure_type)}</td>
        <td>${fv(r.region)}</td>
        <td><a class="click-link" onclick="viewClosure(${r.id});return false">${fv(r.vehicle_number)}</a></td>
        <td><a class="click-link" onclick="viewClosure(${r.id});return false">${fv(r.name)}</a></td>
        <td>${fv(r.transferee)}</td>
        <td>${fv(r.transfer_region)}</td>
        <td style="font-size:11px">${fv(r.receipt_date)}</td>
        <td style="font-size:11px"><strong>${fv(r.closure_date)}</strong></td>
        <td class="td-act">
          <button class="btn bp btn-xs" onclick="editClosure(${r.id})">수정</button>
          ${isAdmin()?`<button class="btn br btn-xs" onclick="deleteClosure(${r.id})">삭제</button>`:''}
        </td></tr>`).join('')}</tbody>
    </table></div>${pgn(d,doSearch)}`;
    bindPgn('clTbl',doSearch);
  };
  document.getElementById('clSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('clSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById('clRstBtn').onclick=()=>{ST.fl.cl={};renderClosures();};
  document.getElementById('clAddBtn').onclick=()=>editClosure(null);
  document.getElementById('clXlBtn').onclick=()=>{
    const q=new URLSearchParams(Object.fromEntries(Object.entries(ST.fl.cl||{}).filter(([,v])=>v)));
    dl(`/api/closures/export/excel?${q}`,'폐업현황.xlsx');
  };
  await doSearch(1);
}

window.editClosure=async(id)=>{
  let r={management_number:'',closure_type:'폐업',data_type:'신규자료',region:'',vehicle_number:'',name:'',company_name:'',closure_date:'',approval_date:'',reason:'',memo:'',vehicle_type:'',fuel_type:'',structure_change:'',phone:'',mobile:'',address:'',official_address:'',membership_status:'',membership_date:'',certificate_issue_date:'',certificate_number:'',resident_number:'',driver_license_number:'',affiliated_company:'',agent_name:'',agent_mobile:'',receipt_date:'',transferee:'',transfer_region:''};
  if(id){r=await api('GET',`/api/closures/${id}`).catch(()=>null);if(!r)return;}
  if(!id){const nn=await api('GET',`/api/closures/next-number/폐업`).catch(()=>null);if(nn)r.management_number=nn.next_number;}
  const raw=(r.raw_data&&typeof r.raw_data==='object')?r.raw_data:{};
  openModal(id?'폐업 수정':'폐업 등록',`<form id="clForm"><div class="fg">
    ${fi('management_number','관리번호',r.management_number||'')}
    <div class="fi"><label>처리구분</label>${ssel('closure_type',CLOSURE_TYPES,r.closure_type||'폐업')}</div>
    <div class="fi"><label>자료구분</label>${ssel('data_type',['신규자료','이전자료'],r.data_type||'신규자료')}</div>
    <div class="fi"><label>지역</label>${rsel('region',r.region||'')}</div>
    ${fi('vehicle_number','차량번호',r.vehicle_number||'',true)} ${fi('name','성명',r.name||'')} ${fi('company_name','상호',r.company_name||'')}
    <div class="fi"><label>차종</label><input class="fc" name="vehicle_type" value="${e_(r.vehicle_type||raw['차종']||'')}" placeholder="예: 22,포터Ⅱ내장탑차"></div>
    ${fri('fuel_type','유종',['','경유','LPG','전기','휘발유','CNG','하이브리드'],r.fuel_type||raw['유종']||'')}
    <div class="fi cs2"><label>구조변경</label><input class="fc" name="structure_change" value="${e_(r.structure_change||'')}"></div>
    ${fi('phone','전화번호',r.phone||'')} ${fph('mobile','핸드폰',r.mobile||'')}
    <div class="fi cs2"><label>주소</label><input class="fc" name="address" value="${e_(r.address||'')}"></div>
    <div class="fi cs2"><label>공문주소</label><input class="fc" name="official_address" value="${e_(r.official_address||'')}"></div>
    <div class="fi"><label>가입여부</label>${ssel('membership_status',['미가입','가입'],r.membership_status||'미가입')}</div>
    ${fi('membership_date','가입일자',r.membership_date||'')}
    ${fi('certificate_issue_date','자격증명발급일자',r.certificate_issue_date||'')}
    ${fi('certificate_number','자격증명발급번호',r.certificate_number||'')}
    ${frn('resident_number','주민등록번호',r.resident_number||'')}
    ${fi('driver_license_number','운전면허번호',r.driver_license_number||'')}
    ${fi('affiliated_company','소속업체',r.affiliated_company||'')}
    ${fi('agent_name','대리인',r.agent_name||'')} ${fph('agent_mobile','대리인 핸드폰',r.agent_mobile||'')}
    ${fi('receipt_date','접수일자',r.receipt_date||'')} ${fi('closure_date','처리일자',r.closure_date||'')} ${fi('approval_date','인가일자',r.approval_date||'')}
    <div class="fi cs2"><label>사유</label><input class="fc" name="reason" value="${e_(r.reason||'')}"></div>
    ${fi('transferee','양수인',r.transferee||'')} ${fi('transfer_region','이관/양도지역',r.transfer_region||'')}
    ${fta('memo','비고',r.memo||'','cs4')}
  </div></form>`,
  `<button class="btn bg btn-sm" id="_clS">${id?'저장':'등록'}</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`);
  if(!id){
    document.querySelector('[name=closure_type]').onchange=async e=>{
      const nn=await api('GET',`/api/closures/next-number/${encodeURIComponent(e.target.value)}`).catch(()=>null);
      if(nn)document.querySelector('[name=management_number]').value=nn.next_number;
    };
  }
  document.getElementById('_clS').onclick=async()=>{
    const form=document.getElementById('clForm');if(!form.checkValidity()){form.reportValidity();return;}
    const fd=Object.fromEntries(new FormData(form));
    const res=await api(id?'PUT':'POST',id?`/api/closures/${id}`:'/api/closures',fd).catch(()=>null);
    if(res){toast(id?'수정':'등록');closeModal();renderClosures();}
  };
};
window.deleteClosure=async(id)=>{if(!await cfm('삭제?'))return;try{await api('DELETE',`/api/closures/${id}`);toast('삭제');renderClosures();}catch(e){};};

// ===== CHANGE HISTORY =====
async function renderChangeHistory(){
  const f=ST.fl.ch||{};
  document.getElementById('content').innerHTML=`
    <div class="card">
      <div class="card-hd">
        <div class="card-hd-l"><span class="card-ico">📝</span><span class="card-ttl">변경이력대장</span><span class="cnt" id="chCnt">0건</span></div>
        <div class="flex gap-8">
          <button class="btn bg btn-sm" id="chAddBtn">+ 등록</button>
          <button class="btn bxl btn-sm" id="chXlBtn">엑셀 다운로드</button>
        </div>
      </div>
      <div class="frow">
        ${rselflt('chRegF',f.region||'')}
        <select id="chTypF" class="fsel"><option value="">전체 유형</option>${CHANGE_TYPES.map(t=>`<option value="${t}">${t}</option>`).join('')}</select>
        ${dateOrderSel('chSortF',f.date_order||'desc')}
        <input class="srch" id="chSrch" placeholder="성명, 차량번호, 변경 전/후" value="${e_(f.search||'')}">
        <button class="btn bp btn-sm" id="chSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="chRstBtn">초기화</button>
      </div>
      <div id="chTbl"><div class="loading-box"><div class="spin"></div></div></div>
    </div>`;

  const sk='ch';
  const hdrs=[{field:'change_type',label:'변경유형'},{field:'change_date',label:'처리일자'},{field:'region',label:'지역'},{field:'vehicle_number',label:'차량번호'},{field:'name',label:'성명'},{field:'before_value',label:'변경 전'},{field:'after_value',label:'변경 후'},{field:'memo',label:'비고'},{label:'관리',noSort:true}];

  const doSearch=async(pg=1)=>{
    ST.fl.ch={region:document.getElementById('chRegF').value,change_type:document.getElementById('chTypF').value,date_order:document.getElementById('chSortF').value,search:document.getElementById('chSrch').value.trim()};
    const q=new URLSearchParams({page:pg,limit:50,...getSortParams(sk),...Object.fromEntries(Object.entries(ST.fl.ch).filter(([,v])=>v))});
    const d=await api('GET',`/api/change-history?${q}`).catch(()=>null);if(!d)return;
    document.getElementById('chCnt').textContent=`${d.total.toLocaleString()}건`;
    const tw=document.getElementById('chTbl');
    if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">📝</div><p class="empty-txt">데이터가 없습니다.</p></div>`;return;}
    tw.innerHTML=`<div class="tbl-wrap"><table>
      <thead><tr>${plainHeaders(hdrs)}</tr></thead>
      <tbody>${d.items.map(r=>`<tr>
        <td>${chBadge(r.change_type)}</td>
        <td>${fvDate(r.change_date,r.receipt_date)}</td>
        <td>${fv(r.region)}</td>
        <td><a class="click-link" onclick="viewChange(${r.id});return false">${fv(r.vehicle_number)}</a></td>
        <td><a class="click-link" onclick="viewChange(${r.id});return false">${fv(r.name)}</a></td>
        <td title="${e_(r.before_value)}" style="max-width:160px;overflow:hidden;text-overflow:ellipsis">${fv(r.before_value)}</td>
        <td title="${e_(r.after_value)}" style="max-width:160px;overflow:hidden;text-overflow:ellipsis">${fv(r.after_value)}</td>
        <td title="${e_(r.memo)}">${fv(r.memo)}</td>
        <td class="td-act">
          <button class="btn bp btn-xs" onclick="editChange(${r.id})">수정</button>
          ${isAdmin()?`<button class="btn br btn-xs" onclick="deleteChange(${r.id})">삭제</button>`:''}
        </td></tr>`).join('')}</tbody>
    </table></div>${pgn(d,doSearch)}`;
    bindPgn('chTbl',doSearch);
  };
  document.getElementById('chSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('chSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById('chRstBtn').onclick=()=>{ST.fl.ch={};renderChangeHistory();};
  document.getElementById('chAddBtn').onclick=()=>editChange(null);
  document.getElementById('chXlBtn').onclick=()=>{
    const q=new URLSearchParams(Object.fromEntries(Object.entries(ST.fl.ch||{}).filter(([,v])=>v)));
    dl(`/api/change-history/export/excel?${q}`,'변경이력대장.xlsx');
  };
  await doSearch(1);
}

window.editChange=async(id)=>{
  let r={change_type:'주소지변경',region:'',vehicle_number:'',name:'',before_value:'',after_value:'',change_date:'',memo:''};
  if(id){r=await api('GET',`/api/change-history/${id}`).catch(()=>null);if(!r)return;}
  openModal(id?'변경이력 수정':'변경이력 등록',`<form id="chForm"><div class="fg">
    <div class="fi"><label>변경유형</label>${ssel('change_type',CHANGE_TYPES,r.change_type||'주소지변경')}</div>
    <div class="fi"><label>지역</label>${rsel('region',r.region||'')}</div>
    ${fi('vehicle_number','차량번호',r.vehicle_number||'',true)} ${fi('name','성명',r.name||'')}
    <div class="fi cs2"><label>변경 전</label><input class="fc" name="before_value" value="${e_(r.before_value||'')}"></div>
    <div class="fi cs2"><label>변경 후</label><input class="fc" name="after_value" value="${e_(r.after_value||'')}"></div>
    ${fi('change_date','처리일자',r.change_date||'')}
    ${fta('memo','비고',r.memo||'','cs3')}
  </div></form>`,
  `<button class="btn bg btn-sm" id="_chS">${id?'저장':'등록'}</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`);
  document.getElementById('_chS').onclick=async()=>{
    const res=await api(id?'PUT':'POST',id?`/api/change-history/${id}`:'/api/change-history',Object.fromEntries(new FormData(document.getElementById('chForm')))).catch(()=>null);
    if(res){toast(id?'수정':'등록');closeModal();renderChangeHistory();}
  };
};
window.deleteChange=async(id)=>{if(!await cfm('삭제?'))return;await api('DELETE',`/api/change-history/${id}`);toast('삭제');renderChangeHistory();};

// ===== DASHBOARD =====
async function renderDashboard(){
  document.getElementById('content').innerHTML=`<div class="loading-box"><div class="spin"></div><p>통계 자동 계산 중...</p></div>`;
  const [full,reg,activity,byYear,recent]=await Promise.all([
    api('GET','/api/dashboard/full-stats'),
    api('GET','/api/dashboard/regional'),
    api('GET','/api/dashboard/stats'),
    api('GET','/api/dashboard/activity-by-year'),
    api('GET','/api/dashboard/recent-by-type'),
  ]).catch(()=>[null,null,null,null,null]);
  if(!full||!reg)return;
  const s=full.summary;const alloc=full.allocation||{};

  document.getElementById('content').innerHTML=`
    <div class="stat-grid">
      <div class="stat-card" onclick="navigate('members','individual')"><div class="stat-lbl">총 사업자</div><div class="stat-val">${s.total.toLocaleString()}</div><div class="stat-sub">폐업 제외</div></div>
      <div class="stat-card sky" onclick="showStatList('joined')"><div class="stat-lbl">협회 가입</div><div class="stat-val">${s.joined.toLocaleString()}</div><div class="stat-sub">가입일자 기준</div></div>
      <div class="stat-card pink" onclick="showStatList('not_joined')"><div class="stat-lbl">미가입</div><div class="stat-val">${s.not_joined.toLocaleString()}</div><div class="stat-sub">가입일자 없음</div></div>
      <div class="stat-card" onclick="navigate('members','individual')"><div class="stat-lbl">개인회원</div><div class="stat-val">${s.individual.toLocaleString()}</div></div>
      <div class="stat-card yellow" onclick="navigate('members','delivery')"><div class="stat-lbl">택배회원</div><div class="stat-val">${s.delivery.toLocaleString()}</div></div>
      <div class="stat-card gray" onclick="showStatList('delivery_employed')"><div class="stat-lbl">택배 취업신고</div><div class="stat-val">${(s.delivery_employed||0).toLocaleString()}</div><div class="stat-sub">자격증명발급일자 기준</div></div>
      <div class="stat-card red" onclick="showStatList('delivery_not_employed')"><div class="stat-lbl">택배 미신고</div><div class="stat-val">${(s.delivery_not_employed||0).toLocaleString()}</div><div class="stat-sub">자격증명발급일자 없음</div></div>
    </div>

    <div class="grid2">
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📍</span><span class="card-ttl">지역별 현황</span></div></div>
        <div class="tbl-wrap"><table>
          <thead><tr><th>지역</th><th>전체</th><th>가입</th><th>미가입</th><th>개인</th><th>택배</th></tr></thead>
          <tbody>${(reg||[]).filter(r=>r.total>0).map(r=>`<tr>
            <td><strong>${r.region}</strong></td>
            <td><a class="click-link" onclick="navigate('members','individual');return false">${r.total.toLocaleString()}</a></td>
            <td style="color:var(--c-sky)">${r.joined}</td><td style="color:var(--c-pink)">${r.not_joined}</td>
            <td>${r.individual}</td><td>${r.delivery}</td>
          </tr>`).join('')||'<tr><td colspan="6" style="text-align:center;padding:16px;color:var(--c-text-4)">데이터 없음</td></tr>'}</tbody>
        </table></div>
      </div>

      <div>
        <div class="card" style="margin-bottom:12px"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🚗</span><span class="card-ttl">차종별</span></div></div>
          <div class="tbl-wrap"><table><thead><tr><th>차종</th><th>대수</th></tr></thead>
            <tbody>${(full.vehicle_types||[]).slice(0,12).map(r=>`<tr style="cursor:pointer" onclick="showVtypeList('${r.type}')"><td>${r.type}</td><td style="color:var(--c-primary)">${r.count}</td></tr>`).join('')||'<tr><td colspan="2" style="text-align:center;padding:10px;color:var(--c-text-4)">데이터 없음</td></tr>'}</tbody>
          </table></div></div>
        <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">⛽</span><span class="card-ttl">유종별</span></div></div>
          <div class="tbl-wrap"><table><thead><tr><th>유종</th><th>대수</th></tr></thead>
            <tbody>${(full.fuel_types||[]).slice(0,6).map(r=>`<tr><td>${r.type}</td><td>${r.count}</td></tr>`).join('')||'<tr><td colspan="2" style="text-align:center;padding:10px;color:var(--c-text-4)">데이터 없음</td></tr>'}</tbody>
          </table></div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-hd"><div class="card-hd-l"><span class="card-ico">📊</span><span class="card-ttl">부과대수 자동 파악</span><span class="badge b-teal" style="font-size:10px;margin-left:6px">자동 계산</span></div></div>
      <div class="card-bd">
        <div class="alloc-grid">
          ${[['협회가입',alloc['협회가입']],['양도',alloc['양도']],['타도(이관)',alloc['타도(이관)']],['폐업',alloc['폐업']],['탈퇴',alloc['탈퇴']],['택배신규',alloc['택배신규']],['관리비폐지',alloc['관리비폐지']],['70세',alloc['70세']],['협회기본대수',alloc['협회기본대수']],['총부과대수',alloc['총부과대수']],['택배관리',alloc['택배관리']]].map(([l,v])=>`
          <div class="alloc-card"><div class="alloc-lbl">${l}</div>
            ${v===null||v===undefined?`<div class="alloc-na">확인 필요</div>`:`<div class="alloc-val">${Number(v).toLocaleString()}</div>`}
          </div>`).join('')}
        </div>
      </div>
    </div>

    <div class="grid2">
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">👥</span><span class="card-ttl">연령대별</span></div></div>
        <div class="tbl-wrap"><table><thead><tr><th>연령대</th><th>인원</th></tr></thead>
          <tbody>${Object.entries(full.age_groups||{}).map(([k,v])=>`<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📈</span><span class="card-ttl">연도별 변동 (최근 10년)</span></div></div>
        <div class="tbl-wrap"><table><thead><tr><th>연도</th><th>신규</th><th>양도양수</th><th>폐업</th><th>변경</th></tr></thead>
          <tbody>${(byYear||[]).slice(-10).reverse().map(r=>`<tr>
            <td><strong>${r.year}</strong></td>
            <td><a class="tbl-link" onclick="showYearDetail(${r.year},'new')">${r.new||0}</a></td>
            <td><a class="tbl-link" onclick="showYearDetail(${r.year},'transfer')">${r.transfer||0}</a></td>
            <td><a class="tbl-link" onclick="showYearDetail(${r.year},'closure')">${r.closure||0}</a></td>
            <td><a class="tbl-link" onclick="showYearDetail(${r.year},'change')">${r.change||0}</a></td>
          </tr>`).join('')||'<tr><td colspan="5" style="text-align:center;padding:14px;color:var(--c-text-4)">데이터 없음</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>

    <div class="grid2">
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🆕</span><span class="card-ttl">최근 신규등록</span><span class="badge b-pri" style="font-size:10px;margin-left:4px">인가일자 기준</span></div></div>
        <div class="tbl-wrap"><table><thead><tr><th>지역</th><th>차량번호</th><th>성명</th><th>인가일자</th></tr></thead>
          <tbody>${(recent?.new_members||[]).map(r=>`<tr><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${fv(r.approval_date)}</td></tr>`).join('')||'<tr><td colspan="4" style="text-align:center;padding:12px;color:var(--c-text-4)">없음</td></tr>'}</tbody>
        </table></div></div>
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🚫</span><span class="card-ttl">최근 폐업처리</span><span class="badge b-pink" style="font-size:10px;margin-left:4px">처리일자 기준</span></div></div>
        <div class="tbl-wrap"><table><thead><tr><th>관리번호</th><th>지역</th><th>차량번호</th><th>성명</th><th>구분</th></tr></thead>
          <tbody>${(recent?.closures||[]).map(r=>`<tr><td>${fv(r.management_number)}</td><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${ctBadge(r.closure_type)}</td></tr>`).join('')||'<tr><td colspan="5" style="text-align:center;padding:12px;color:var(--c-text-4)">없음</td></tr>'}</tbody>
        </table></div></div>
    </div>`;
}

// ===== MONTHLY REPORT =====
async function renderMonthlyReport(){
  const y=ST.reportYear,m=ST.reportMonth;
  document.getElementById('content').innerHTML=`<div class="loading-box"><div class="spin"></div><p>월례보고서 자동 계산 중...</p></div>`;
  const d=await api('GET',`/api/dashboard/monthly-report-auto?year=${y}&month=${m}`).catch(()=>null);
  if(!d){document.getElementById('content').innerHTML=`<div class="empty-box"><div class="empty-ico">📄</div><p class="empty-txt">계산 실패</p></div>`;return;}

  const ms=d.member_stats||{},ts=d.taxi_stats||{},aw=d.admin_work||{},act=d.month_activity||{};
  const ageG=d.age_groups||{},vAge=d.vehicle_age||{};

  function numOrNA(v){return v===null||v===undefined?`<span class="rpt-na">확인 필요</span>`:`<strong>${Number(v).toLocaleString()}</strong>`;}

  document.getElementById('content').innerHTML=`
    <div class="card">
      <div class="rpt-nav">
        <button class="btn bo btn-sm" onclick="ST.reportMonth--;if(ST.reportMonth<1){ST.reportMonth=12;ST.reportYear--;}renderMonthlyReport()">◀ 이전</button>
        <span class="rpt-period">${y}년 ${m}월</span>
        <button class="btn bo btn-sm" onclick="ST.reportMonth++;if(ST.reportMonth>12){ST.reportMonth=1;ST.reportYear++;}renderMonthlyReport()">다음 ▶</button>
        <span style="font-size:11px;color:var(--c-text-4);margin-left:8px">해당 월(${y}년 ${m}월) 기준 자동 계산</span>
        <button class="btn bxl btn-sm" style="margin-left:auto" onclick="dl('/api/reports/monthly/export?year=${y}&month=${m}','월례보고서_${y}_${String(m).padStart(2,'0')}.xlsx')">엑셀</button>
      </div>
    </div>

    <div class="grid2">
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📊</span><span class="card-ttl">1. 사업자수 및 차량대수</span></div></div>
        <div class="card-bd">
          <div class="rpt-sec"><table class="rpt-tbl">
            <thead><tr><th>구분</th><th>총 허가</th><th>협회 가입</th><th>미가입</th></tr></thead>
            <tbody>
              <tr><td class="rl">개인(사업자)</td><td>${ms.individual||0}</td><td>-</td><td>-</td></tr>
              <tr><td class="rl">택배(차량)</td><td>${ms.delivery||0}</td><td>-</td><td>-</td></tr>
              <tr class="total-row"><td class="rl">합계(전체)</td><td>${ms.total||0}</td><td>${ms.joined||0}</td><td>${ms.not_joined||0}</td></tr><tr style="background:var(--c-bg-2)"><td class="rl">※ ${m}월 신규가입</td><td colspan="3"><strong>${ms.month_joined||0}명</strong> (가입일자 기준)</td></tr><tr style="background:var(--c-bg-2)"><td class="rl">※ ${m}월 미가입발생</td><td colspan="3"><strong>${ms.month_not_joined||0}명</strong> (인가일자 기준)</td></tr>
            </tbody></table></div>
          <div class="rpt-sec"><div class="rpt-sec-ttl">1-1. 택배 차량대수</div>
            <table class="rpt-tbl"><thead><tr><th>허가대수</th><th>취업신고</th><th>미신고</th></tr></thead>
              <tbody><tr><td>${ts.total_delivery||0}</td><td>${ts.employed||0}</td><td>${ts.unemployed||0}</td></tr></tbody>
            </table></div>
        </div>
      </div>
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🚗</span><span class="card-ttl">2. 유형별 차량대수</span></div></div>
        <div class="card-bd"><table class="rpt-tbl">
          <thead><tr><th>차종</th><th>대수</th></tr></thead>
          <tbody>${(d.vehicle_types||[]).map(r=>`<tr style="cursor:pointer" onclick="showVtypeList('${r.type}')"><td class="rl">${r.type}</td><td style="color:var(--c-primary)">${r.count}</td></tr>`).join('')||'<tr><td colspan="2">데이터 없음</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>

    <div class="grid2">
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">👥</span><span class="card-ttl">3. 연령대별 사업자</span></div></div>
        <div class="card-bd"><table class="rpt-tbl">
          <thead><tr><th>연령대</th><th>인원</th></tr></thead>
          <tbody>${Object.entries(ageG).map(([k,v])=>`<tr><td class="rl">${k}</td><td>${v}</td></tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🔢</span><span class="card-ttl">4. 연식별 차량대수</span></div></div>
        <div class="card-bd"><table class="rpt-tbl">
          <thead><tr><th>연식</th><th>대수</th></tr></thead>
          <tbody>${Object.entries(vAge).length?Object.entries(vAge).map(([k,v])=>`<tr><td class="rl">${k}</td><td>${v}</td></tr>`).join(''):'<tr><td colspan="2" class="rpt-na" style="padding:8px">연식 정보 없음</td></tr>'}</tbody>
        </table></div>
      </div>
    </div>

    <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📋</span><span class="card-ttl">5. 지정·위탁업무 처리현황</span><span class="badge b-yellow" style="font-size:10px;margin-left:4px">처리일자 기준</span></div></div>
      <div class="card-bd">
        <div class="stat-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:12px">
          <div class="stat-card sky"><div class="stat-lbl">신규등록</div><div class="stat-val">${act.new_registrations||0}</div><div class="stat-sub">인가일자 기준</div></div>
          <div class="stat-card"><div class="stat-lbl">양도양수</div><div class="stat-val">${act.transfers||0}</div></div>
          <div class="stat-card red"><div class="stat-lbl">폐업</div><div class="stat-val">${act.closures||0}</div></div>
          <div class="stat-card purple"><div class="stat-lbl">변경이력</div><div class="stat-val">${act.changes||0}</div></div>
        </div>
        <table class="rpt-tbl"><thead><tr><th>업무 구분</th><th>당월 처리</th></tr></thead>
          <tbody>${Object.entries(aw).map(([k,v])=>`<tr><td class="rl">${k}</td><td>${numOrNA(v)}</td></tr>`).join('')}</tbody>
        </table>
      </div>
    </div>

    <div class="grid2">
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📚</span><span class="card-ttl">6. 교육실시 현황</span></div></div>
        <div class="card-bd"><p class="rpt-na" style="padding:16px;text-align:center">확인 필요 (시스템 미연동)</p></div>
      </div>
      <div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🚨</span><span class="card-ttl">7. 자가용 단속실적</span></div></div>
        <div class="card-bd"><p class="rpt-na" style="padding:16px;text-align:center">확인 필요 (시스템 미연동)</p></div>
      </div>
    </div>

    ${act.new_registrations>0||act.closures>0?`<div class="grid2">
      ${act.new_registrations>0?`<div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📋</span><span class="card-ttl">${y}년 ${m}월 신규등록 목록 (${act.new_registrations}건)</span></div></div>
        <div class="tbl-wrap"><table><thead><tr><th>관리번호</th><th>지역</th><th>차량번호</th><th>성명</th><th>구분</th><th>인가일자</th></tr></thead>
          <tbody>${(d.month_new_list||[]).map(r=>`<tr><td><strong>${fv(r.management_number)}</strong></td><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${fv(r.category)}</td><td>${fv(r.approval_date)}</td></tr>`).join('')}</tbody></table></div></div>`:''}
      ${act.closures>0?`<div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">🚫</span><span class="card-ttl">${y}년 ${m}월 폐업/양도/이관 목록 (${act.closures}건)</span></div></div>
        <div class="tbl-wrap"><table><thead><tr><th>자료</th><th>관리번호</th><th>구분</th><th>지역</th><th>차량번호</th><th>성명</th><th>접수일자</th></tr></thead>
          <tbody>${(d.month_closure_list||[]).map(r=>`<tr><td><span class="badge ${r.data_type==='이전자료'?'b-gray':'b-sky'}" style="font-size:10px">${r.data_type||''}</span></td><td><strong>${fv(r.management_number)}</strong></td><td>${ctBadge(r.closure_type)}</td><td>${fv(r.region)}</td><td>${fv(r.vehicle_number)}</td><td>${fv(r.name)}</td><td>${fv(r.receipt_date||r.closure_date)}</td></tr>`).join('')}</tbody></table></div></div>`:''}
    </div>`:''}`;
}

// ===== EXCEL UPLOAD =====
async function renderUpload(){
  document.getElementById('content').innerHTML=`
    <div class="grid2">
      <div class="card">
        <div class="card-hd">
          <div class="card-hd-l"><span class="card-ico">📤</span><span class="card-ttl">파일 업로드</span></div>
          ${isAdmin()?`<button class="btn br btn-sm" id="resetAllBtn">🗑 데이터 초기화</button><button class="btn bo btn-sm" id="backfillMgmtBtn" style="margin-left:6px">🔧 양도양수 관리번호 재생성</button><button class="btn bo btn-sm" id="fixDatesBtn" style="margin-left:6px">📅 양도양수 날짜 보정</button>`:''}
        </div>
        <div class="card-bd">
          <div class="fi" style="margin-bottom:10px">
            <label>파일 종류 <span class="req">*</span></label>
            <select id="ftSel" class="fc" style="margin-top:3px">
              <option value="">-- 파일 종류를 선택하세요 --</option>
              <option value="면허자현황">면허자현황 (강원도전체면허자현황.xlsm) — 개인/택배 시트</option>
              <option value="양도양수대장">양도양수대장 — 2000~현재 전체 시트 (예정자 제외)</option>
              <option value="폐지현황">폐업현황 (사용)</option>
              <option value="이전폐지현황">이전 폐업현황 — 유형별 시트</option>
              <option value="주소변경등록대장">주소변경등록대장 → 변경이력으로 저장</option>
              <option value="변경이력대장">변경이력대장 → 변경이력으로 저장</option>
              <option value="부과대수">부과대수</option>
            </select>
          </div>
          <div class="fi" style="margin-bottom:12px">
            <label>중복 처리</label>
            <select id="dupSel" class="fc" style="margin-top:3px">
              <option value="skip">건너뛰기</option>
              <option value="overwrite">덮어쓰기</option>
              <option value="add">새로 추가</option>
            </select>
          </div>
          <div class="upzone" id="upZone">
            <div class="upzone-ico">📁</div>
            <div class="upzone-txt">클릭하거나 파일을 드래그하세요</div>
            <div class="upzone-hint">.xlsx / .xls / .xlsm</div>
            <div class="upzone-fn" id="selFn"></div>
          </div>
          <input type="file" id="upFile" accept=".xlsx,.xls,.xlsm" style="display:none">
          <div class="flex gap-8 mt8">
            <button class="btn bo wf btn-sm" id="prvBtn" disabled>🔍 미리보기</button>
            <button class="btn bp wf btn-sm" id="upBtn" disabled>✅ 업로드 확정</button>
          </div>
          <div id="upResult"></div>
        </div>
      </div>
      <div class="card">
        <div class="card-hd"><div class="card-hd-l"><span class="card-ico">📊</span><span class="card-ttl">최근 업로드 이력</span></div></div>
        <div id="upHist"><div class="loading-box"><div class="spin"></div></div></div>
      </div>
    </div>
    <div id="prvWrap" style="margin-top:12px"></div>`;

  let selFile=null;
  const loadHist=async()=>{
    const d=await api('GET','/api/dashboard/upload-history').catch(()=>null);
    const hw=document.getElementById('upHist');
    if(!d||!d.length){hw.innerHTML=`<div class="empty-box"><div class="empty-ico">📂</div><p class="empty-txt">이력 없음</p></div>`;return;}
    const adminMode=isAdmin();
    hw.innerHTML=`<div class="tbl-wrap"><table><thead><tr><th>파일종류</th><th>파일명</th><th>전체</th><th>성공</th><th>오류</th><th>일시</th>${adminMode?'<th>삭제</th>':''}</tr></thead>
      <tbody>${d.slice(0,30).map(h=>`<tr>
        <td><span class="badge b-sky" style="font-size:11px">${e_(h.file_type||'-')}</span></td>
        <td style="max-width:130px;overflow:hidden;text-overflow:ellipsis" title="${e_(h.filename)}">${e_(h.filename||'-')}</td>
        <td>${(h.total_count||0).toLocaleString()}</td>
        <td style="color:var(--c-pri);font-weight:700">${(h.success_count||0).toLocaleString()}</td>
        <td style="color:${h.error_count>0?'var(--c-danger)':'var(--c-text-4)'}">${h.error_count||0}</td>
        <td style="font-size:11px;color:var(--c-text-3)">${(h.created_at||'-').slice(0,16)}</td>
        ${adminMode?`<td style="white-space:nowrap">
          <button class="btn br btn-xs" onclick="deleteUpload(${h.id},'${e_(h.file_type)}',${h.success_count})" title="이 업로드 건만 삭제">이력삭제</button>
          <button class="btn br btn-xs" style="margin-left:4px" onclick="deleteByFileType('${e_(h.file_type)}')" title="${e_(h.file_type)} 전체 삭제">전체삭제</button>
        </td>`:''}
      </tr>`).join('')}</tbody></table></div>`;
  };
  window.deleteUpload=async(histId,fileType,cnt)=>{
    if(!await cfm(`업로드 이력 #${histId} (${fileType}) 삭제\n저장된 데이터 약 ${cnt}건이 삭제됩니다.\n다른 업로드 데이터는 유지됩니다.\n계속하시겠습니까?`))return;
    try{
      const r=await api('DELETE',`/api/admin/upload/${histId}`);
      if(r){
        if(r.deleted_total===0){
          toast(`이력 삭제됨 (데이터 0건: upload_id 미연결)`,'warn');
        }else{
          toast(`삭제 완료: ${r.deleted_total}건 제거`,'info');
        }
        loadHist();
      }
    }catch(e){toast('삭제 실패','error');}
  };
  window.deleteByFileType=async(fileType)=>{
    if(!await cfm(`[${fileType}] 전체 삭제\n이 파일종류로 업로드된 모든 데이터를 삭제합니다.\n재업로드 전 정리용입니다.\n계속하시겠습니까?`))return;
    try{
      const r=await api('DELETE',`/api/admin/upload-by-filetype/${encodeURIComponent(fileType)}`);
      if(r){toast(`${fileType} 삭제 완료: ${r.deleted_total}건`,'info');loadHist();}
    }catch(e){toast('삭제 실패','error');}
  };

  if(isAdmin()&&document.getElementById('resetAllBtn')){
    document.getElementById('resetAllBtn').onclick=async()=>{
      if(!await cfm('⚠️ 모든 업로드 데이터를 삭제합니다.\n이 작업은 되돌릴 수 없습니다.'))return;
      const r=await api('DELETE','/api/admin/reset-all').catch(()=>null);
      if(r){toast('초기화 완료','info');loadHist();}
    };
  }

  if(isAdmin()&&document.getElementById('backfillMgmtBtn')){
    document.getElementById('backfillMgmtBtn').onclick=async()=>{
      if(!await cfm('양도양수대장 관리번호(양YY-NN)를 재생성합니다.\n기존 관리번호가 없는 행에만 적용됩니다.\n계속하시겠습니까?'))return;
      const btn=document.getElementById('backfillMgmtBtn');
      btn.disabled=true;btn.textContent='처리 중...';
      try{
        const r=await api('POST','/api/admin/backfill-transfer-mgmt');
        if(r){
          toast(`관리번호 재생성 완료: ${r.updated}건 업데이트, ${r.skipped}건 스킵`,'info');
          // 결과 표시
          const res=document.getElementById('upResult');
          if(res){
            res.innerHTML=`<div style="margin-top:12px;padding:12px;background:var(--c-bg-2);border-radius:8px;font-size:13px">
              <strong>🔧 양도양수 관리번호 재생성 결과</strong><br>
              ✅ 업데이트: <strong>${r.updated}건</strong><br>
              ⏭ 스킵(번호 없음): ${r.skipped}건<br>
              전체 대상: ${r.total}건<br>
              <span style="color:var(--c-text-3);font-size:11px">양도양수대장 목록에서 관리번호 기준 정렬을 확인해주세요.</span>
            </div>`;
          }
          // 양도양수대장 탭이 열려있으면 즉시 재조회
          if(ST.sub==='transfer-ledger'){ST.fl.tl={member_sort:'mgmt_desc'};renderTransferLedger();}
        }
      }catch(e){}
      btn.disabled=false;btn.textContent='🔧 양도양수 관리번호 재생성';
    };
  }

  if(isAdmin()&&document.getElementById('fixDatesBtn')){
    document.getElementById('fixDatesBtn').onclick=async()=>{
      if(!await cfm('양도양수대장 날짜를 보정합니다.\n(raw_data 기준으로 접수일자/인가일자 재저장)\n계속하시겠습니까?'))return;
      const btn=document.getElementById('fixDatesBtn');
      btn.disabled=true;btn.textContent='처리 중...';
      try{
        const r=await api('POST','/api/admin/fix-transfer-dates');
        if(r){toast(`날짜 보정 완료: ${r.fixed}건`,'info');
          const res=document.getElementById('upResult');
          if(res){res.insertAdjacentHTML('beforeend',`<div style="margin-top:8px;padding:10px;background:var(--c-bg-2);border-radius:8px;font-size:13px">📅 날짜 보정: <strong>${r.fixed}건</strong> 수정 / ${r.skipped}건 스킵</div>`);}
        }
      }catch(e){}
      btn.disabled=false;btn.textContent='📅 양도양수 날짜 보정';
    };
  }

  const uz=document.getElementById('upZone'),fi_=document.getElementById('upFile');
  uz.onclick=()=>fi_.click();
  uz.ondragover=e=>{e.preventDefault();uz.classList.add('over');};
  uz.ondragleave=()=>uz.classList.remove('over');
  uz.ondrop=e=>{e.preventDefault();uz.classList.remove('over');const f=e.dataTransfer.files[0];if(f){selFile=f;document.getElementById('selFn').textContent=f.name;document.getElementById('prvBtn').disabled=false;document.getElementById('upBtn').disabled=false;}};
  fi_.onchange=()=>{selFile=fi_.files[0];if(selFile){document.getElementById('selFn').textContent=selFile.name;document.getElementById('prvBtn').disabled=false;document.getElementById('upBtn').disabled=false;}};

  document.getElementById('prvBtn').onclick=async()=>{
    const ft=document.getElementById('ftSel').value;
    if(!ft){toast('파일 종류를 선택하세요','warn');return;}
    if(!selFile){toast('파일을 선택하세요','warn');return;}
    const fd=new FormData();fd.append('file_type',ft);fd.append('file',selFile);
    document.getElementById('prvBtn').disabled=true;document.getElementById('prvBtn').textContent='분석 중...';
    const d=await api('POST','/api/excel/preview',fd,true).catch(()=>null);
    document.getElementById('prvBtn').disabled=false;document.getElementById('prvBtn').textContent='🔍 미리보기';
    if(!d)return;
    const rows=d.preview_rows||[];
    const skipF=new Set(['raw_data','deleted_at','_sheet','_change_content','_raw_approval','data_year']);
    const keys=rows.length?Object.keys(rows[0]).filter(k=>!skipF.has(k)&&!k.startsWith('_')):[];
    const mapped=Object.entries(d.col_mapping||{}).slice(0,15).map(([k,v])=>`<span class="tag tag-pri">${e_(k)}→${e_(v)}</span>`).join('');
    document.getElementById('prvWrap').innerHTML=`<div class="card">
      <div class="card-hd"><div class="card-hd-l"><span class="card-ico">👁</span><span class="card-ttl">미리보기 (${rows.length}행 | ${e_(d.file_type)})</span></div></div>
      <div class="card-bd">
        <div class="info-box" style="font-size:11px"><strong>인식된 컬럼:</strong> ${mapped}
        ${d.unmapped_columns?.length?`<br><span style="color:var(--c-warn)">raw_data 저장: ${d.unmapped_columns.slice(0,6).join(', ')}${d.unmapped_columns.length>6?` 외 ${d.unmapped_columns.length-6}개`:''}</span>`:''}</div>
        ${rows.length?`<div class="tbl-wrap"><table><thead><tr>${keys.map(k=>`<th>${e_(k)}</th>`).join('')}</tr></thead>
          <tbody>${rows.map(r=>`<tr>${keys.map(k=>`<td>${fv(r[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:'<p style="padding:10px;color:var(--c-text-4)">미리보기 없음</p>'}
      </div></div>`;
  };

  document.getElementById('upBtn').onclick=async()=>{
    const ft=document.getElementById('ftSel').value;
    if(!ft){toast('파일 종류를 선택하세요','warn');return;}
    if(!selFile){toast('파일을 선택하세요','warn');return;}
    if(!await cfm(`"${ft}" 파일을 업로드합니다.\n※ 양도양수대장은 2000~현재 전체 시트를 처리합니다.`))return;
    const fd=new FormData();fd.append('file_type',ft);fd.append('duplicate_handling',document.getElementById('dupSel').value);fd.append('file',selFile);
    document.getElementById('upBtn').disabled=true;document.getElementById('upBtn').textContent='업로드 중...';
    const d=await api('POST','/api/excel/upload',fd,true).catch(()=>null);
    document.getElementById('upBtn').disabled=false;document.getElementById('upBtn').textContent='✅ 업로드 확정';
    if(!d)return;
    toast(`완료: 성공 ${(d.success||0).toLocaleString()}건`);
    const isMember=(d.file_type==='면허자현황');
    document.getElementById('upResult').innerHTML=`<div class="res-box">
      <div class="res-row"><span class="res-lbl">파일 종류</span><span class="res-val">${e_(d.file_type||ft)}</span></div>
      <div class="res-row"><span class="res-lbl">전체 행수</span><span class="res-val">${(d.total||0).toLocaleString()}</span></div>
      <div class="res-row"><span class="res-lbl">저장 성공</span><span class="res-val rv-ok">${(d.success||0).toLocaleString()}</span></div>
      ${isMember?`<div class="res-row"><span class="res-lbl">개인회원</span><span class="res-val" style="color:var(--c-pri)">${d.individual_count||0}</span></div>`:''}
      ${isMember?`<div class="res-row"><span class="res-lbl">택배회원</span><span class="res-val" style="color:var(--c-yellow)">${d.delivery_count||0}</span></div>`:''}
      <div class="res-row"><span class="res-lbl">중복 처리</span><span class="res-val rv-warn">${d.duplicates||0}</span></div>
      <div class="res-row"><span class="res-lbl">실패</span><span class="res-val rv-err">${d.errors||0}</span></div>
      ${d.sheet_logs?.length?`<hr class="div"><p style="font-size:11px;color:var(--c-text-3);font-weight:600">시트별 처리 현황:</p><div class="tbl-wrap" style="margin-top:4px"><table style="font-size:11px"><thead><tr><th>시트명</th><th>처리건수</th><th>상태</th></tr></thead><tbody>${d.sheet_logs.map(s=>`<tr><td>${e_(s.sheet)}</td><td>${s.count||0}</td><td style="color:${s.status==='ok'?'var(--c-success)':s.status==='skip'||s.status==='empty'?'var(--c-text-4)':'var(--c-danger)'}">${s.status||'-'}</td></tr>`).join('')}</tbody></table></div>`:''}
      </div>`;
    // 에러 상세 동적 추가 (중첩 백틱 문제 회피)
    if(d.error_details&&d.error_details.length){
      const errHtml=d.error_details.slice(0,30).map(function(e){
        var lbl=e.label||(e.vehicle_number||'');
        return '<div style="margin:4px 0;padding:7px 10px;background:var(--c-bg-2);border-left:3px solid var(--c-danger);border-radius:4px;font-size:11px"><strong>'+e.row+'행</strong>'+(lbl?' / '+e_(lbl):'')+' &mdash; '+e_(e.error)+'</div>';
      }).join('');
      document.getElementById('upResult').insertAdjacentHTML('beforeend','<hr><p style="font-size:11px;color:var(--c-danger);font-weight:600">실패 상세 (총 '+d.errors+'건):</p>'+errHtml);
    }
    loadHist();
  };
  await loadHist();
}

async function renderUploadHistory(){
  document.getElementById('content').innerHTML=`<div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">📊</span><span class="card-ttl">업로드 이력</span></div></div><div id="histTbl"><div class="loading-box"><div class="spin"></div></div></div></div>`;
  const d=await api('GET','/api/dashboard/upload-history').catch(()=>null);
  const tw=document.getElementById('histTbl');
  if(!d||!d.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">📂</div><p class="empty-txt">이력이 없습니다.</p></div>`;return;}
  tw.innerHTML=`<div class="tbl-wrap"><table><thead><tr><th>파일종류</th><th>파일명</th><th>전체</th><th>성공</th><th>중복</th><th>오류</th><th>업로더</th><th>일시</th></tr></thead>
    <tbody>${d.map(h=>`<tr><td>${h.file_type||'-'}</td><td>${h.filename||'-'}</td>
      <td>${h.total_count}</td><td style="color:var(--c-pri);font-weight:700">${h.success_count}</td>
      <td style="color:var(--c-warn)">${h.duplicate_count}</td><td style="color:var(--c-danger)">${h.error_count}</td>
      <td>${h.uploaded_by||'-'}</td><td>${h.created_at||'-'}</td></tr>`).join('')}</tbody></table></div>`;
}

async function renderUploadErrors(){
  document.getElementById('content').innerHTML=`<div class="card"><div class="card-hd"><div class="card-hd-l"><span class="card-ico">⚠️</span><span class="card-ttl">오류 확인</span></div></div><div id="errTbl"><div class="loading-box"><div class="spin"></div></div></div></div>`;
  const d=await api('GET','/api/dashboard/upload-history').catch(()=>null);
  const tw=document.getElementById('errTbl');
  const withErr=(d||[]).filter(h=>h.error_count>0&&h.error_details?.length);
  if(!withErr.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">✅</div><p class="empty-txt">오류 없음</p></div>`;return;}
  tw.innerHTML=withErr.map(h=>`<div style="margin-bottom:14px">
    <p style="font-size:13px;font-weight:600;color:var(--c-danger);margin-bottom:6px">⚠️ ${h.file_type} — ${h.filename}</p>
    <div class="tbl-wrap"><table><thead><tr><th>행</th><th>오류</th></tr></thead>
      <tbody>${(h.error_details||[]).map(e=>`<tr><td>${e.row}</td><td style="color:var(--c-danger)">${e_(e.error)}</td></tr>`).join('')}</tbody>
    </table></div></div>`).join('');
}

// ===== INIT =====
document.addEventListener('DOMContentLoaded',()=>{
  if(!localStorage.getItem('authToken')){window.location.href='/login';return;}
  document.getElementById('hUser').textContent=`${ST.user.full||ST.user.name} (${ST.user.role==='admin'?'관리자':'직원'})`;
  document.getElementById('logoutBtn').onclick=()=>{if(window.confirm('로그아웃 하시겠습니까?'))logout();};
  document.getElementById('modalX').onclick=closeModal;
  document.getElementById('modalBg').addEventListener('click',e=>{if(e.target===document.getElementById('modalBg'))closeModal();});
  document.querySelectorAll('.cat-btn').forEach(b=>b.addEventListener('click',()=>{
    navigate(b.dataset.cat,CATS[b.dataset.cat]?.tabs[0]?.id);
  }));
  navigate('members','candidates');
});



