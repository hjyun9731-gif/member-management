/* 2026-08-31
   업무관리시스템 > 인허가/변경 > 폐업현황
   가입/미가입 상태를 목록에서 즉시 확인하기 위한 UI 보강.
   기존 app.js의 API/수정/삭제/상세 기능은 그대로 사용하고 renderClosures만 덮어쓴다.
*/
(function(){
  if (typeof renderClosures !== 'function') return;

  renderClosures = async function(){
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
    const hdrs=[
      {label:'관리번호'},{label:'구분'},{label:'지역'},{label:'차량번호'},{label:'성명'},
      {label:'가입'},{label:'양수인'},{label:'이관지역'},{label:'접수일자'},{label:'처리일자'},
      {label:'관리',noSort:true}
    ];

    const doSearch=async(pg=1)=>{
      ST.fl.cl={
        region:document.getElementById('clRegF').value,
        closure_type:document.getElementById('clTypF').value,
        data_type:document.getElementById('clDtF').value,
        date_order:document.getElementById('clSortF').value,
        search:document.getElementById('clSrch').value.trim()
      };
      const q=new URLSearchParams({page:pg,limit:50,...getSortParams(sk),...Object.fromEntries(Object.entries(ST.fl.cl).filter(([,v])=>v))});
      const d=await api('GET',`/api/closures?${q}`).catch(()=>null);if(!d)return;
      document.getElementById('clCnt').textContent=`${d.total.toLocaleString()}건`;
      const tw=document.getElementById('clTbl');if(!tw)return;
      if(!d.items.length){tw.innerHTML=`<div class="empty-box"><div class="empty-ico">🚫</div><p class="empty-txt">데이터가 없습니다.</p></div>`;return;}
      tw.innerHTML=`<div class="tbl-wrap"><table>
        <thead><tr>${plainHeaders(hdrs)}</tr></thead>
        <tbody>${d.items.map(r=>{
          const membership=(r.membership_status||'').trim() || (r.membership_date?'가입':'미가입');
          return `<tr>
            <td><a class="click-link" onclick="viewClosure(${r.id});return false"><strong>${fv(r.management_number)}</strong></a></td>
            <td>${ctBadge(r.closure_type)}</td>
            <td>${fv(r.region)}</td>
            <td><a class="click-link" onclick="viewClosure(${r.id});return false">${fv(r.vehicle_number)}</a></td>
            <td><a class="click-link" onclick="viewClosure(${r.id});return false">${fv(r.name)}</a></td>
            <td title="${r.membership_date?`가입일자 ${r.membership_date}`:'가입일자 없음'}">${memBadge(membership)}</td>
            <td>${fv(r.transferee)}</td>
            <td>${fv(r.transfer_region)}</td>
            <td style="font-size:11px">${fv(r.receipt_date)}</td>
            <td style="font-size:11px"><strong>${fv(r.closure_date)}</strong></td>
            <td class="td-act">
              <button class="btn bp btn-xs" onclick="editClosure(${r.id})">수정</button>
              ${isAdmin()?`<button class="btn br btn-xs" onclick="deleteClosure(${r.id})">삭제</button>`:''}
            </td></tr>`;
        }).join('')}</tbody>
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
  };
})();
