/* ================================================================
   FINAL UI DECORATOR 2026-08-29
   PRESENTATION / READ-ONLY ONLY
   - app.js is not modified.
   - Existing save/edit/register/delete/search handlers are untouched.
   - This script only adds dashboard markup and reads existing GET APIs.
   ================================================================ */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const fmt = (n) => Number(n || 0).toLocaleString('ko-KR');
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));

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

  async function loadMember() {
    const el = $('#uxMemberCard');
    if (!el) return;
    const d = await read('/api/dashboard/full-stats');
    const s = d?.summary;
    if (!s) {
      el.querySelector('.ux-card-loading').textContent = '회원통계를 불러오지 못했습니다.';
      return;
    }
    el.innerHTML = `
      <div class="ux-over-title"><span class="ux-icon lavender">♙</span><strong>회원통계</strong></div>
      <div class="ux-member-body">
        <div class="ux-primary-stat"><span>전체 회원</span><b>${fmt(s.total)}명</b></div>
        <div class="ux-member-grid">
          <div><span>협회 가입</span><b>${fmt(s.joined)}명</b></div>
          <div><span>미가입</span><b>${fmt(s.not_joined)}명</b></div>
          <div><span>개인</span><b>${fmt(s.individual)}명</b></div>
          <div><span>택배</span><b>${fmt(s.delivery)}명</b></div>
        </div>
      </div>
      <button type="button" class="ux-more" data-ux-nav="members-individual">회원 관리로 이동 <span>→</span></button>`;
  }

  async function loadPayment() {
    const el = $('#uxPaymentCard');
    if (!el) return;
    const s = await read('/api/receivables/summary');
    if (!s) {
      el.querySelector('.ux-card-loading').textContent = '수납정보를 불러오지 못했습니다.';
      return;
    }
    el.innerHTML = `
      <div class="ux-over-title"><span class="ux-icon rose">▣</span><strong>수납 요약</strong></div>
      <div class="ux-pay-main"><span>오늘 수납</span><b>${fmt(s.today_paid)}원</b></div>
      <div class="ux-pay-grid">
        <div><span>활성 미수금</span><b>${fmt(s.active_arrears_total)}원</b></div>
        <div><span>미수 회원</span><b>${fmt(s.active_arrears_members)}명</b></div>
        <div><span>선납</span><b>${fmt(s.active_prepaid_total)}원</b></div>
        <div><span>부과 대기</span><b>${fmt(s.pending_members)}명</b></div>
      </div>
      <button type="button" class="ux-more" data-ux-nav="receivables">수납/미수금 관리로 이동 <span>→</span></button>`;
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
        <div><span>진행</span><b>${fmt(activeAll.length)}건</b></div>
        <div><span>7일 이내</span><b class="soon-txt">${fmt(near7)}건</b></div>
        <div><span>기한 경과</span><b class="over-txt">${fmt(s['기한초과'])}건</b></div>
        <div><span>완료</span><b class="ok-txt">${fmt(s['완료'])}건</b></div>
      </div>
      <div class="ux-dead-head"><span>업무</span><span>기한</span><span>남은일</span></div>
      <div class="ux-dead-list">
        ${active.length ? active.map((x) => {
          const title = x.title || x.task_type || '기한';
          const who = [x.region, x.name || x.vehicle_number].filter(Boolean).join(' ') || '-';
          return `
          <div class="ux-dead-row">
            <span class="ux-dead-main"><b>${esc(title)}</b><em>${esc(who)}</em></span>
            <span class="ux-dead-date">${esc(displayDate(x.due_date || x.start_date || '-'))}</span>
            <span class="ux-dday ${ddayClass(x)}">${esc(x.dday_label || x.status || '-')}</span>
          </div>`;
        }).join('') : '<div class="ux-empty-line">진행 중인 등록 기한이 없습니다.</div>'}
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
          </div>`).join('') : '<div class="ux-empty-line">최근 업무 기록이 없습니다.</div>'}
      </div>`;
  }

  function bindOverviewNavigation(root) {
    root.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-ux-nav]');
      if (!b) return;
      const to = b.dataset.uxNav;
      if (to === 'receivables') {
        location.href = '/receivables';
      } else if (to === 'deadlines' && typeof window.navigate === 'function') {
        window.navigate('deadlines','deadlines');
      } else if (to === 'members-individual' && typeof window.navigate === 'function') {
        window.navigate('members','individual');
      }
    });
  }

  function markCandidate() {
    const content = $('#content');
    if (!content) return;
    const active = isCandidatePage();
    content.classList.toggle('ux-candidate-page', active);
    document.body.classList.toggle('ux-candidate-body', active);

    if (!active) return;
    const tabs = $('#content .inner-tab-bar');
    if (!tabs || $('#uxOverview')) return;
    tabs.insertAdjacentHTML('beforebegin', shell());
    const overview = $('#uxOverview');
    if (overview) bindOverviewNavigation(overview);
    loadMember();
    loadPayment();
    loadDeadline();
    loadRecent();
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
