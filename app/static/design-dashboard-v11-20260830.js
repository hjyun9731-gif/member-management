/* ================================================================
   V11 APPROVED REFERENCE UI DECORATOR 2026-08-30
   PRESENTATION / READ-ONLY ONLY
   - app.js is not modified.
   - Existing save/edit/register/delete/search handlers are untouched.
   - This script only adds/rearranges presentation markup and reads GET APIs.
   ================================================================ */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const fmt = (n) => Number(n || 0).toLocaleString('ko-KR');
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
  const norm = (v) => String(v ?? '').replace(/\s+/g, '').replace(/[·:]/g, '').trim();

  function dateParts(raw) {
    const s = String(raw ?? '').trim();
    if (!s) return null;
    let m = s.match(/(19\d{2}|20\d{2})\D+(\d{1,2})\D+(\d{1,2})/);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    m = s.match(/(?:^|\D)(\d{2})\D+(\d{1,2})\D+(\d{1,2})(?:\D|$)/);
    if (m) {
      const yy = Number(m[1]);
      return [yy <= 69 ? 2000 + yy : 1900 + yy, Number(m[2]), Number(m[3])];
    }
    return null;
  }

  function displayDate(raw) {
    const p = dateParts(raw);
    if (!p) return String(raw ?? '-').trim() || '-';
    const [y,m,d] = p;
    return `${y}.${String(m).padStart(2,'0')}.${String(d).padStart(2,'0')}`;
  }

  function dateSortKey(raw) {
    const p = dateParts(raw);
    if (!p) return 0;
    return p[0] * 10000 + p[1] * 100 + p[2];
  }

  async function read(path) {
    try {
      if (typeof window.api === 'function') return await window.api('GET', path);
      const token = localStorage.getItem('authToken');
      const res = await fetch(path, {
        method: 'GET',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        credentials: 'same-origin'
      });
      if (!res.ok) return null;
      return await res.json();
    } catch (_) {
      return null;
    }
  }

  function isCandidatePage() {
    return !!($('#content #itCand') && $('#content #innerContent'));
  }

  function dateLabel() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    const ko = ['일','월','화','수','목','금','토'][d.getDay()];
    return `${y}.${m}.${day} (${ko})`;
  }

  function shell() {
    return `
      <section class="ux-page-head" id="uxPageHead">
        <div>
          <h1>예정자 / 양도양수 관리</h1>
          <p>예정자 등록 및 양도양수 신청 관리를 한눈에 확인하고 처리할 수 있습니다.</p>
        </div>
        <div class="ux-date-chip" aria-label="오늘 날짜">▣&nbsp;&nbsp;${dateLabel()}</div>
      </section>

      <section class="ux-overview" id="uxOverview" aria-label="업무 요약">
        <article class="ux-over-card ux-member" id="uxMemberCard">
          <div class="ux-over-title"><span class="ux-icon lavender">♙</span><strong>회원통계</strong></div>
          <div class="ux-card-loading">불러오는 중…</div>
        </article>

        <article class="ux-over-card ux-payment" id="uxPaymentCard">
          <div class="ux-over-title"><span class="ux-icon rose">▣</span><strong>수납 요약</strong></div>
          <div class="ux-card-loading">불러오는 중…</div>
        </article>

        <article class="ux-over-card ux-deadline" id="uxDeadlineCard">
          <div class="ux-over-title"><span class="ux-icon mint">▦</span><strong>기한 관리</strong></div>
          <div class="ux-card-loading">불러오는 중…</div>
        </article>

        <article class="ux-over-card ux-recent" id="uxRecentCard">
          <div class="ux-over-title"><span class="ux-icon mint">☷</span><strong>최근 업무</strong></div>
          <div class="ux-card-loading">불러오는 중…</div>
        </article>
      </section>`;
  }

  function firstNum(obj, keys) {
    for (const k of keys) {
      if (obj && obj[k] !== undefined && obj[k] !== null && obj[k] !== '') return Number(obj[k]);
    }
    return null;
  }

  async function loadMember() {
    const el = $('#uxMemberCard');
    if (!el) return;
    const d = await read('/api/dashboard/full-stats');
    const s = d?.summary;
    if (!s) {
      el.querySelector('.ux-card-loading').textContent = '회원통계를 불러오지 못했습니다.';
      return;
    }

    const gender = s.gender || s.gender_counts || s.by_gender || d.gender || d.gender_counts || d.by_gender || {};
    const male = firstNum(s, ['male','male_count','men','gender_male','남성']) ?? firstNum(gender, ['male','male_count','men','남','남성']);
    const female = firstNum(s, ['female','female_count','women','gender_female','여성']) ?? firstNum(gender, ['female','female_count','women','여','여성']);
    const sideA = male !== null ? ['남성', `${fmt(male)}명`] : ['협회 가입', `${fmt(s.joined)}명`];
    const sideB = female !== null ? ['여성', `${fmt(female)}명`] : ['미가입', `${fmt(s.not_joined)}명`];

    el.innerHTML = `
      <div class="ux-over-title"><span class="ux-icon lavender">♙</span><strong>회원통계</strong></div>
      <div class="ux-member-simple">
        <div class="ux-member-total"><span>전체 회원</span><b>${fmt(s.total)}명</b></div>
        <div class="ux-member-side">
          <div><span>${esc(sideA[0])}</span><b>${esc(sideA[1])}</b></div>
          <div><span>${esc(sideB[0])}</span><b>${esc(sideB[1])}</b></div>
        </div>
      </div>
      <button type="button" class="ux-more" data-ux-nav="members-individual">상세 보기 <span>→</span></button>`;
  }

  async function loadPayment() {
    const el = $('#uxPaymentCard');
    if (!el) return;
    const s = await read('/api/receivables/summary');
    if (!s) {
      el.querySelector('.ux-card-loading').textContent = '수납정보를 불러오지 못했습니다.';
      return;
    }

    const monthPaid = firstNum(s, ['month_paid','monthly_paid','this_month_paid','current_month_paid']);
    const mainValue = monthPaid !== null ? monthPaid : Number(s.today_paid || 0);
    const mainLabel = monthPaid !== null ? '이번 달 수납' : '오늘 수납';
    const pct = firstNum(s, ['month_change_pct','monthly_change_pct','mom_pct','previous_month_change_pct']);
    const secondary = pct !== null
      ? `<span class="ux-pay-compare-label">전월 대비</span><b class="ux-pay-compare ${pct >= 0 ? 'up' : 'down'}">${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%</b>`
      : `<span class="ux-pay-compare-label">활성 미수금</span><b class="ux-pay-subvalue">${fmt(s.active_arrears_total)}원</b>`;

    el.innerHTML = `
      <div class="ux-over-title"><span class="ux-icon rose">▣</span><strong>수납 요약</strong></div>
      <div class="ux-payment-simple">
        <span class="ux-pay-main-label">${mainLabel}</span>
        <b class="ux-pay-main-value">${fmt(mainValue)}원</b>
        <div class="ux-pay-compare-row">${secondary}</div>
      </div>
      <button type="button" class="ux-more" data-ux-nav="receivables">상세 보기 <span>→</span></button>`;
  }

  function ddayClass(item) {
    const d = Number(item?.dday);
    if (item?.status === '기한초과' || d < 0) return 'over';
    if (d === 0) return 'today';
    if (d <= 7) return 'soon';
    return 'normal';
  }

  async function loadDeadline() {
    const el = $('#uxDeadlineCard');
    if (!el) return;
    const [s, list] = await Promise.all([
      read('/api/deadlines/summary'),
      read('/api/deadlines?filter=전체&page=1&size=50')
    ]);
    if (!s) {
      el.querySelector('.ux-card-loading').textContent = '기한정보를 불러오지 못했습니다.';
      return;
    }

    const activeAll = (list?.items || [])
      .filter((x) => x.status !== '완료')
      .sort((a, b) => dateSortKey(a.due_date || a.start_date) - dateSortKey(b.due_date || b.start_date));
    const active = activeAll.slice(0, 4);
    const near7 = Number(s['오늘기한'] || 0) + Number(s['3일이내'] || 0) + Number(s['7일이내'] || 0);

    el.innerHTML = `
      <div class="ux-over-title"><span class="ux-icon mint">▦</span><strong>기한 관리</strong></div>
      <div class="ux-dead-summary">
        <div><span>전체</span><b>${fmt(activeAll.length)}건</b></div>
        <div><span>7일 이내</span><b class="soon-txt">${fmt(near7)}건</b></div>
        <div><span>기한 경과</span><b class="over-txt">${fmt(s['기한초과'])}건</b></div>
        <div><span>정상</span><b class="ok-txt">${fmt(Math.max(activeAll.length - near7 - Number(s['기한초과'] || 0), 0))}건</b></div>
      </div>
      <div class="ux-dead-head"><span>구분</span><span>대상</span><span>기한</span><span>남은일</span></div>
      <div class="ux-dead-list">
        ${active.length ? active.map((x) => {
          const title = x.title || x.task_type || '기한';
          const who = [x.region, x.name || x.vehicle_number].filter(Boolean).join(' ') || '-';
          return `
          <div class="ux-dead-row">
            <span class="ux-dead-title">${esc(title)}</span>
            <span class="ux-dead-who">${esc(who)}</span>
            <span class="ux-dead-date">${esc(displayDate(x.due_date || x.start_date || '-'))}</span>
            <span class="ux-dday ${ddayClass(x)}">${esc(x.dday_label || x.status || '-')}</span>
          </div>`;
        }).join('') : '<div class="ux-empty-line ux-empty-small">진행 중인 등록 기한이 없습니다.</div>'}
      </div>
      <button type="button" class="ux-more" data-ux-nav="deadlines">전체 보기 <span>→</span></button>`;
  }

  async function loadRecent() {
    const el = $('#uxRecentCard');
    if (!el) return;
    const d = await read('/api/dashboard/recent-by-type?limit=5');
    if (!d) {
      el.querySelector('.ux-card-loading').textContent = '최근업무를 불러오지 못했습니다.';
      return;
    }
    const rows = [];
    (d.new_members || []).forEach((x) => rows.push({date:x.approval_date || '', text:`${x.name || ''} 회원 신규 등록`, tag:'회원', cls:'lav'}));
    (d.transfers || []).forEach((x) => rows.push({date:x.approval_date || '', text:`${x.transferee || x.transferor || ''} 양도양수 처리`, tag:'양도양수', cls:'peach'}));
    (d.closures || []).forEach((x) => rows.push({date:x.closure_date || '', text:`${x.name || ''} ${x.closure_type || '폐업'} 처리`, tag:x.closure_type || '폐업', cls:'rose'}));
    (d.changes || []).forEach((x) => rows.push({date:x.change_date || '', text:`${x.name || ''} ${x.change_type || '정보 수정'}`, tag:'변경', cls:'mint'}));
    rows.sort((a,b) => dateSortKey(b.date) - dateSortKey(a.date));
    const show = rows.slice(0,5);
    el.innerHTML = `
      <div class="ux-over-title"><span class="ux-icon mint">☷</span><strong>최근 업무</strong></div>
      <div class="ux-recent-list">
        ${show.length ? show.map((x) => `
          <div class="ux-recent-row">
            <span class="ux-recent-date">${esc(displayDate(x.date || '-'))}</span>
            <span class="ux-recent-text">${esc(x.text)}</span>
            <span class="ux-tag ${x.cls}">${esc(x.tag)}</span>
          </div>`).join('') : '<div class="ux-empty-line ux-empty-small">최근 업무 기록이 없습니다.</div>'}
      </div>
      <div class="ux-more ux-more-static">전체 기록 보기 <span>→</span></div>`;
  }

  function bindOverviewNavigation(root) {
    root.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-ux-nav]');
      if (!b) return;
      const to = b.dataset.uxNav;
      if (to === 'receivables') location.href = '/receivables';
      else if (to === 'deadlines' && typeof window.navigate === 'function') window.navigate('deadlines','deadlines');
      else if (to === 'members-individual' && typeof window.navigate === 'function') window.navigate('members','individual');
    });
  }

  function classifyFormFields() {
    const form = $('#candForm');
    if (!form) return;
    const mapping = [
      ['지역','ux-f-region'], ['차량번호','ux-f-vehicle-no'], ['성명','ux-f-name'], ['주민등록번호','ux-f-resident'],
      ['전화번호','ux-f-phone'], ['핸드폰','ux-f-mobile'], ['주소','ux-f-address'],
      ['자격증발급일자','ux-f-cert-date'], ['자격증발급번호','ux-f-cert-no'], ['운전면허번호','ux-f-driver'],
      ['차종','ux-f-vehicle-type'], ['유종','ux-f-fuel'],
      ['사업자번호','ux-f-business-no'], ['소속업체','ux-f-company'], ['가입일자','ux-f-membership-date'],
      ['비고','ux-f-note']
    ];
    $$('.fi', form).forEach((fi) => {
      const label = norm($('label', fi)?.textContent || '');
      for (const [txt, cls] of mapping) {
        if (label === norm(txt)) fi.classList.add(cls);
      }
    });
    $$('span', form).forEach((sp) => {
      if (norm(sp.textContent).includes('없으면미가입')) {
        sp.hidden = true;
        sp.setAttribute('aria-hidden','true');
        sp.classList.add('ux-hide-membership-hint');
      }
    });
  }

  function classifyCandidateTable() {
    const table = $('#cTbl table');
    if (!table) return;
    const heads = $$('thead th', table);
    heads.forEach((th, idx) => {
      const t = norm(th.textContent);
      if (t.includes('주민등록번호')) table.classList.add('ux-has-resident');
      if (t.includes('소속업체')) {
        th.classList.add('ux-hide-company-col');
        $$(`tbody tr`, table).forEach((tr) => tr.children[idx]?.classList.add('ux-hide-company-col'));
      }
      if (t === '관리') {
        th.classList.add('ux-manage-col');
        $$(`tbody tr`, table).forEach((tr) => tr.children[idx]?.classList.add('ux-manage-col'));
      }
      if (t.includes('주민등록번호')) {
        th.classList.add('ux-resident-col');
        $$(`tbody tr`, table).forEach((tr) => tr.children[idx]?.classList.add('ux-resident-col'));
      }
    });
  }

  function cleanCandidatePresentation() {
    classifyFormFields();
    classifyCandidateTable();
  }

  function markCandidate() {
    const content = $('#content');
    if (!content) return;
    const active = isCandidatePage();
    content.classList.toggle('ux-candidate-page', active);
    document.body.classList.toggle('ux-candidate-body', active);
    if (!active) return;

    cleanCandidatePresentation();
    const tabs = $('#content .inner-tab-bar');
    if (!tabs) return;

    // App rerenders can remove one part of the decorator while leaving another.
    // Keep the approved dashboard shell complete and immediately before the work tabs.
    let head = $('#uxPageHead');
    let overview = $('#uxOverview');
    if (!head || !overview) {
      head?.remove(); overview?.remove();
      tabs.insertAdjacentHTML('beforebegin', shell());
      head = $('#uxPageHead');
      overview = $('#uxOverview');
      if (overview) {
        bindOverviewNavigation(overview);
        loadMember(); loadPayment(); loadDeadline(); loadRecent();
      }
    } else {
      tabs.parentNode.insertBefore(head, tabs);
      tabs.parentNode.insertBefore(overview, tabs);
    }
  }

  const observer = new MutationObserver(() => requestAnimationFrame(markCandidate));
  function boot() {
    const c = $('#content');
    if (!c) { setTimeout(boot, 80); return; }
    observer.observe(c, {childList:true, subtree:true});
    markCandidate();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
