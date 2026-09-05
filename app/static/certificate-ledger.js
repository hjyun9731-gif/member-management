(function () {
  'use strict';

  const state = { search: '', status: '', page: 1 };
  let statsCache = null;
  let statsCacheAt = 0;
  const STATS_TTL_MS = 60000;
  let currentTarget = null;
  const esc = value => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const display = value => {
    if (!value) return '-';
    const text = String(value);
    const m = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[1].slice(2)}.${m[2]}.${m[3]}` : text;
  };
  const tokenHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem('authToken') || ''}` });

  function injectStyles() {
    if (document.getElementById('certificateLedgerStyles')) return;
    const style = document.createElement('style');
    style.id = 'certificateLedgerStyles';
    style.textContent = `
      .candidate-right-column{min-width:0}
      .candidate-right-tabs{align-items:center}
      .candidate-right-pane{min-width:0}
      .candidate-right-pane .cl-head{margin:0 0 8px}
      .cl-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
      .cl-title{font-size:16px;font-weight:800;letter-spacing:-.4px}.cl-help{font-size:10.5px;color:var(--c-text-3);line-height:1.45}
      .cl-stats{display:grid;grid-template-columns:repeat(2,minmax(140px,1fr));gap:8px;margin-bottom:8px}
      .cl-stat{background:#fff;border:1px solid var(--c-border);border-radius:8px;padding:8px 9px;box-shadow:var(--sh-xs)}
      .cl-stat span{display:block;font-size:10px;color:var(--c-text-3);white-space:nowrap}.cl-stat strong{display:block;font-size:17px;margin-top:2px}
      .cl-wait strong{color:#d97706}.cl-approved strong{color:#059669}
      .cl-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:20px;font-size:10.5px;font-weight:800;white-space:nowrap}
      .cl-badge.pending{color:#b45309;background:#fff7ed}.cl-badge.issued{color:#047857;background:#ecfdf5}
      .cl-badge.wait{color:#6b7280;background:#f3f4f6}.cl-badge.approved{color:#4f46e5;background:#eef2ff}.cl-badge.cancelled{color:#6b7280;background:#f3f4f6}
      .cl-table th,.cl-table td{white-space:nowrap}.cl-table td{font-size:11.5px}.cl-table .cl-name{font-weight:700;color:var(--c-text)}
      .cl-actions{display:flex;gap:4px;justify-content:center;align-items:center}.cl-empty{padding:30px;text-align:center;color:var(--c-text-3)}
      .cl-history{display:grid;gap:8px}.cl-history-item{border-left:3px solid var(--c-pri);padding:7px 10px;background:var(--c-bg);border-radius:0 7px 7px 0}
      .cl-history-item strong{font-size:12px}.cl-history-item div{font-size:11px;color:var(--c-text-3);margin-top:2px}
      @media(max-width:1400px){.candidate-right-pane .cl-stats{grid-template-columns:repeat(2,1fr)}}
    `;
    document.head.appendChild(style);
  }

  async function request(method, url, body) {
    const options = { method, headers: tokenHeaders() };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    if (response.status === 401) {
      window.location.replace('/login');
      throw new Error('로그인이 필요합니다.');
    }
    if (!response.ok) {
      let message = '요청 처리에 실패했습니다.';
      try {
        const error = await response.json();
        message = error.detail || error.message || message;
      } catch (_) {}
      if (typeof toast === 'function') toast(message, 'err');
      throw new Error(message);
    }
    return response.json();
  }

  function issuanceBadge(status) {
    if (status === '취소') return `<span class="cl-badge cancelled">취소</span>`;
    const issued = status === '발급완료';
    return `<span class="cl-badge ${issued ? 'issued' : 'pending'}">${esc(status || '생성대기')}</span>`;
  }

  function approvalBadge(status) {
    const approved = status === '인가완료';
    return `<span class="cl-badge ${approved ? 'approved' : 'wait'}">${esc(status || '인가대기')}</span>`;
  }

  function pager(data) {
    const pages = Number(data.pages || 1);
    if (pages <= 1) return `<div class="pgn"><span>총 <strong>${Number(data.total || 0).toLocaleString()}</strong>건 (1-${Math.min(Number(data.total || 0), 20)})</span></div>`;
    const current = Number(data.page || 1);
    let first = Math.max(1, current - 2);
    let last = Math.min(pages, first + 4);
    first = Math.max(1, last - 4);
    const nums = [];
    for (let n = first; n <= last; n++) nums.push(`<button class="pgn-btn ${n === current ? 'active' : ''}" data-cl-page="${n}">${n}</button>`);
    return `<div class="pgn"><span>총 <strong>${Number(data.total || 0).toLocaleString()}</strong>건 (${(current-1)*20+1}-${Math.min(current*20, Number(data.total || 0))})</span><div class="pgn-btns">
      <button class="pgn-btn" data-cl-page="${current - 1}" ${current <= 1 ? 'disabled' : ''}>‹</button>
      ${nums.join('')}
      <button class="pgn-btn" data-cl-page="${current + 1}" ${current >= pages ? 'disabled' : ''}>›</button>
    </div></div>`;
  }

  function actionButtons(row) {
    // 예정자/회원 API가 아니라 발급대장 전용 API(ledger_id)를 사용한다.
    // 예정자가 회원으로 전환되어 예정자 목록에서 사라지거나 삭제 처리되어도
    // 발급대장 행 자체(row.id)는 남아 있으므로 항상 수정할 수 있다.
    return `<button class="btn bp btn-xs" data-cl-edit-ledger="${row.id}">수정</button>`;
  }

  function remarkText(row) {
    const memo = (row.remark || '').trim();
    if (!memo) return '-';
    const text = esc(memo);
    return memo.length > 12 ? `<span title="${text}">${text.slice(0, 12)}…</span>` : text;
  }

  async function editLedgerEntry(id, target) {
    const row = await request('GET', `/api/certificate-ledger/${id}`).catch(() => null);
    if (!row) return;
    if (typeof window.openModal !== 'function') return;
    window.openModal('🖨️ 자격증명발급대장 수정', `<form id="clEditForm"><div class="fg">
      <div class="fi"><label>발급번호</label><input class="fc" value="${esc(row.document_number || '-')}" disabled></div>
      <div class="fi"><label>성명</label><input class="fc" value="${esc(row.name || '-')}" disabled></div>
      <div class="fi"><label>차량번호</label><input class="fc" value="${esc(row.vehicle_number || '-')}" disabled></div>
      <div class="fi"><label>자격증명 발급일자</label><input class="fc" name="certificate_issue_date" value="${esc(row.certificate_issue_date || '')}" placeholder="YYYY-MM-DD"></div>
      <div class="fi cs4"><label>비고</label><textarea class="fc" name="remark" rows="3">${esc(row.remark || '')}</textarea></div>
    </div></form>`,
    `<button class="btn bg btn-sm" id="_clSave">저장</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>`, 'mlg');

    const saveBtn = document.getElementById('_clSave');
    if (!saveBtn) return;
    saveBtn.onclick = async () => {
      const form = document.getElementById('clEditForm');
      const data = Object.fromEntries(new FormData(form));
      const res = await request('PUT', `/api/certificate-ledger/${id}`, data).catch(() => null);
      if (res) {
        if (typeof toast === 'function') toast('저장되었습니다.');
        if (typeof window.closeModal === 'function') window.closeModal();
        refreshInto(target);
      }
    };
  }

  async function getStats(force = false) {
    const now = Date.now();
    if (!force && statsCache && (now - statsCacheAt) < STATS_TTL_MS) return statsCache;
    statsCache = await request('GET', '/api/certificate-ledger/stats');
    statsCacheAt = Date.now();
    return statsCache;
  }

  async function render(page = 1, target = currentTarget) {
    if (!target || !document.documentElement.contains(target)) return;
    currentTarget = target;
    state.page = page;
    injectStyles();
    target.innerHTML = '<div class="card"><div class="loading-box"><div class="spin"></div><p>자격증명 발급대장 불러오는 중...</p></div></div>';
    try {
      const params = new URLSearchParams({ page: String(page), limit: '20' });
      if (state.search) params.set('search', state.search);
      if (state.status) params.set('status', state.status);

      // 목록을 먼저 보여주고 통계는 뒤에서 갱신해 체감 속도를 높인다.
      const data = await request('GET', `/api/certificate-ledger?${params}`);
      if (!document.documentElement.contains(target) || currentTarget !== target) return;

      target.innerHTML = `
        <div class="cl-stats">
          <div class="cl-stat cl-wait"><span>인가대기</span><strong id="clWaitCount">-</strong></div>
          <div class="cl-stat cl-approved"><span>인가완료</span><strong id="clApprovedCount">-</strong></div>
        </div>
        <div class="card">
          <div class="frow">
            <select class="fsel" id="clStatus">
              <option value="">전체 상태</option>
              ${['인가대기','인가완료'].map(v => `<option value="${v}" ${state.status === v ? 'selected' : ''}>${v}</option>`).join('')}
            </select>
            <input class="srch" id="clSearch" value="${esc(state.search)}" placeholder="성명, 차량번호, 자격증명번호">
            <button class="btn bp btn-sm" id="clSearchBtn">조회</button>
            <button class="btn bo btn-sm" id="clResetBtn">초기화</button>
            <button class="btn bo btn-sm" id="clRefreshBtn">새로고침</button>
            <span class="cnt" style="margin-left:auto">표시 20개</span>
          </div>
          ${data.items.length ? `<div class="tbl-wrap"><table class="cl-table"><thead><tr>
            <th>발급번호</th><th>지역</th><th>차량번호</th><th>성명</th><th>발급일자</th><th>상태</th><th>비고</th><th>관리</th>
          </tr></thead><tbody>${data.items.map(row => `<tr>
            <td><strong>${esc(row.document_number || '-')}</strong></td><td>${esc(row.region || '-')}</td><td>${esc(row.vehicle_number || '-')}</td>
            <td class="cl-name">${esc(row.name || '-')}</td><td>${esc(display(row.certificate_issue_date))}</td>
            <td>${approvalBadge(row.approval_status)}</td><td>${remarkText(row)}</td>
            <td><div class="cl-actions">${actionButtons(row)}</div></td>
          </tr>`).join('')}</tbody></table></div>${pager(data)}` : '<div class="cl-empty">자격증명 발급대장 기록이 없습니다.</div>'}
        </div>`;

      const searchBtn = target.querySelector('#clSearchBtn');
      const searchInput = target.querySelector('#clSearch');
      const statusSelect = target.querySelector('#clStatus');
      const resetBtn = target.querySelector('#clResetBtn');
      const refreshBtn = target.querySelector('#clRefreshBtn');

      refreshBtn.onclick = () => { statsCache = null; statsCacheAt = 0; render(state.page || 1, target); };
      searchBtn.onclick = () => { state.search = searchInput.value.trim(); state.status = statusSelect.value; render(1, target); };
      searchInput.onkeydown = event => { if (event.key === 'Enter') searchBtn.click(); };
      statusSelect.onchange = () => searchBtn.click();
      resetBtn.onclick = () => { state.search = ''; state.status = ''; render(1, target); };
      target.querySelectorAll('[data-cl-page]:not([disabled])').forEach(button => {
        button.onclick = () => render(Number(button.dataset.clPage), target);
      });
      target.querySelectorAll('[data-cl-edit-ledger]').forEach(button => {
        button.onclick = () => editLedgerEntry(Number(button.dataset.clEditLedger), target);
      });

      // 통계 때문에 표가 늦게 뜨지 않도록 별도 비동기 갱신.
      getStats(false).then(stats => {
        if (!document.documentElement.contains(target) || currentTarget !== target) return;
        const counts = stats?.counts || {};
        const w = target.querySelector('#clWaitCount');
        const a = target.querySelector('#clApprovedCount');
        if (w) w.textContent = Number(counts['인가대기'] || 0).toLocaleString() + '건';
        if (a) a.textContent = Number(counts['인가완료'] || 0).toLocaleString() + '건';
      }).catch(() => {});
    } catch (error) {
      if (document.documentElement.contains(target)) target.innerHTML = `<div class="card"><div class="cl-empty">${esc(error.message)}</div></div>`;
    }
  }


  async function renderInto(target) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return;
    currentTarget = el;
    await render(1, el);
  }

  async function refreshInto(target) {
    const el = target || currentTarget;
    if (!el) return;
    await render(state.page || 1, el);
  }

  // 1단계: 자격증명발급대장은 회원관리 > 예정자 화면의 오른쪽 탭에서만 연다.
  // 기존처럼 상단 전역 메뉴를 추가하지 않는다.
  window.CertificateLedger = { renderInto, refreshInto };

  function removeLegacyTopMenu() {
    document.querySelectorAll('.certificate-ledger-nav').forEach(el => el.remove());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', removeLegacyTopMenu);
  else removeLegacyTopMenu();
})();

// PHASE1_DEPLOY_MARKER: 20260904_1605_FORCE_DEPLOY (certificate-ledger.js)
