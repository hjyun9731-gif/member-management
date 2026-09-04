(function () {
  'use strict';

  const state = { search: '', status: '', page: 1, pageSize: 50, rows: [], meta: {} };
  let currentTarget = null;

  const esc = value => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const tokenHeaders = () => ({ Authorization: `Bearer ${localStorage.getItem('authToken') || ''}` });

  function dateText(value) {
    if (!value) return '-';
    const s = String(value).trim();
    const m = s.match(/^(\d{4})[-./](\d{1,2})[-./](\d{1,2})/);
    if (m) return `${m[1].slice(2)}.${String(m[2]).padStart(2, '0')}.${String(m[3]).padStart(2, '0')}`;
    return s;
  }

  function injectStyles() {
    if (document.getElementById('certificateLedgerStyles')) return;
    const style = document.createElement('style');
    style.id = 'certificateLedgerStyles';
    style.textContent = `
      .candidate-right-column{min-width:0}.candidate-right-tabs{align-items:center}.candidate-right-pane{min-width:0}
      .cl-help{font-size:10.5px;color:var(--c-text-3);line-height:1.45;margin:0 0 8px}
      .cl-stats{display:grid;grid-template-columns:repeat(6,minmax(80px,1fr));gap:6px;margin-bottom:8px}
      .cl-stat{background:#fff;border:1px solid var(--c-border);border-radius:8px;padding:8px 9px;box-shadow:var(--sh-xs)}
      .cl-stat span{display:block;font-size:10px;color:var(--c-text-3);white-space:nowrap}.cl-stat strong{display:block;font-size:17px;margin-top:2px}
      .cl-stat.issue strong{color:#059669}.cl-stat.unused strong{color:#d97706}.cl-stat.cancel strong{color:#6b7280}
      .cl-stat.approved strong{color:#4f46e5}.cl-stat.wait strong{color:#7c3aed}.cl-stat.check strong{color:#dc2626}
      .cl-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:20px;font-size:10.5px;font-weight:800;white-space:nowrap}
      .cl-badge.issue{color:#047857;background:#ecfdf5}.cl-badge.unused{color:#b45309;background:#fff7ed}.cl-badge.cancel{color:#6b7280;background:#f3f4f6}
      .cl-badge.approved{color:#4f46e5;background:#eef2ff}.cl-badge.wait{color:#6b7280;background:#f3f4f6}.cl-badge.check{color:#b91c1c;background:#fef2f2}
      .cl-table th,.cl-table td{white-space:nowrap}.cl-table td{font-size:11.5px}.cl-table .cl-name{font-weight:700;color:var(--c-text)}
      .cl-table .cl-memo{max-width:240px;overflow:hidden;text-overflow:ellipsis}.cl-empty{padding:30px;text-align:center;color:var(--c-text-3)}
      @media(max-width:1500px){.cl-stats{grid-template-columns:repeat(3,1fr)}}
    `;
    document.head.appendChild(style);
  }

  async function request(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    let response;
    try {
      response = await fetch(url, { headers: tokenHeaders(), signal: controller.signal });
    } catch (error) {
      if (error && error.name === 'AbortError') throw new Error('자격증명발급대장 조회가 지연되고 있습니다.');
      throw error;
    } finally {
      clearTimeout(timer);
    }
    if (response.status === 401) {
      window.location.replace('/login');
      throw new Error('로그인이 필요합니다.');
    }
    if (!response.ok) {
      let msg = '자격증명발급대장을 불러오지 못했습니다.';
      try { const d = await response.json(); msg = d.detail || d.message || msg; } catch (_) {}
      throw new Error(msg);
    }
    return response.json();
  }

  function issueBadge(status) {
    if (status === '발급완료') return '<span class="cl-badge issue">발급완료</span>';
    if (status === '취소') return '<span class="cl-badge cancel">취소</span>';
    return '<span class="cl-badge unused">발급(미사용)</span>';
  }

  function approvalBadge(status) {
    if (status === '인가완료') return '<span class="cl-badge approved">인가완료</span>';
    if (status === '확인필요') return '<span class="cl-badge check">확인필요</span>';
    return '<span class="cl-badge wait">인가대기</span>';
  }

  function currentYearRows(rows, meta) {
    const yy = meta.latest_year == null ? '' : String(meta.latest_year).padStart(2, '0');
    if (!yy) return rows;
    return rows.filter(r => String(r.certificate_number || '').startsWith(`${yy}-`));
  }

  function filteredRows() {
    const needle = state.search.trim().toLowerCase();
    return state.rows.filter(row => {
      if (state.status) {
        const ok = row.issuance_status === state.status || row.approval_status === state.status;
        if (!ok) return false;
      }
      if (!needle) return true;
      const hay = [row.certificate_number,row.region,row.vehicle_number,row.target_name,row.management_number,row.issued_by,row.memo]
        .map(v => String(v || '').toLowerCase()).join(' ');
      return hay.includes(needle);
    });
  }

  function statCounts(rows) {
    const count = key => rows.filter(r => r.issuance_status === key || r.approval_status === key).length;
    return {
      issued: count('발급완료'), unused: count('발급(미사용)'), cancelled: count('취소'),
      approved: count('인가완료'), waiting: count('인가대기'), check: count('확인필요')
    };
  }

  function renderTable(target) {
    const filtered = filteredRows();
    const pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
    if (state.page > pages) state.page = pages;
    const start = (state.page - 1) * state.pageSize;
    const items = filtered.slice(start, start + state.pageSize);
    const counts = statCounts(state.rows);
    const overall = Number(state.meta.last_number || state.rows.length || 0);

    target.innerHTML = `
      <div class="cl-help">자격증명발급번호 발급 이력을 기준으로 현재 개인회원·택배회원·예정자 자료의 자격증명번호와 인가일자를 다시 대조합니다.</div>
      <div class="cl-stats">
        <div class="cl-stat"><span>전체</span><strong>${overall.toLocaleString()}</strong></div>
        <div class="cl-stat issue"><span>발급완료</span><strong>${counts.issued.toLocaleString()}</strong></div>
        <div class="cl-stat unused"><span>발급(미사용)</span><strong>${counts.unused.toLocaleString()}</strong></div>
        <div class="cl-stat cancel"><span>취소</span><strong>${counts.cancelled.toLocaleString()}</strong></div>
        <div class="cl-stat approved"><span>인가완료</span><strong>${counts.approved.toLocaleString()}</strong></div>
        <div class="cl-stat wait"><span>인가대기 / 확인필요</span><strong>${(counts.waiting + counts.check).toLocaleString()}</strong></div>
      </div>
      <div class="card">
        <div class="frow">
          <select class="fsel" id="clStatus">
            <option value="">전체 상태</option>
            ${['발급완료','발급(미사용)','취소','인가완료','인가대기','확인필요'].map(s => `<option value="${s}" ${state.status===s?'selected':''}>${s}</option>`).join('')}
          </select>
          <input class="srch" id="clSearch" value="${esc(state.search)}" placeholder="자격증명번호, 성명, 차량번호, 관리번호, 담당자, 비고">
          <button class="btn bp btn-sm" id="clSearchBtn">조회</button>
          <button class="btn bo btn-sm" id="clResetBtn">초기화</button>
          <span class="cnt" style="margin-left:auto">${Number(filtered.length).toLocaleString()}건</span>
        </div>
        ${items.length ? `<div class="tbl-wrap"><table class="cl-table"><thead><tr>
          <th>자격증명번호</th><th>자격증명 발급일</th><th>지역</th><th>차량번호</th><th>성명</th><th>관리번호</th><th>자격증명 상태</th><th>시청 인가</th><th>인가일자</th><th>담당자</th><th>비고</th>
        </tr></thead><tbody>${items.map(row => `<tr>
          <td><strong>${esc(row.certificate_number || '-')}</strong></td>
          <td>${esc(dateText(row.certificate_issue_date || row.issued_at))}</td>
          <td>${esc(row.region || '-')}</td><td>${esc(row.vehicle_number || '-')}</td><td class="cl-name">${esc(row.target_name || '-')}</td>
          <td>${esc(row.management_number || '-')}</td><td>${issueBadge(row.issuance_status)}</td><td>${approvalBadge(row.approval_status)}</td>
          <td>${esc(dateText(row.approval_date))}</td><td>${esc(row.issued_by || '-')}</td>
          <td class="cl-memo" title="${esc(row.memo || '')}">${esc(row.memo || '-')}</td>
        </tr>`).join('')}</tbody></table></div>
        <div class="pgn"><span>총 <strong>${filtered.length.toLocaleString()}</strong>건</span><div class="pgn-btns">
          <button class="pgn-btn" id="clPrev" ${state.page<=1?'disabled':''}>‹</button>
          <span style="padding:5px 9px;font-size:12px">${state.page} / ${pages}</span>
          <button class="pgn-btn" id="clNext" ${state.page>=pages?'disabled':''}>›</button>
        </div></div>` : '<div class="cl-empty">조건에 맞는 자격증명 발급이력이 없습니다.</div>'}
      </div>`;

    const status = target.querySelector('#clStatus');
    const search = target.querySelector('#clSearch');
    target.querySelector('#clSearchBtn').onclick = () => {
      state.status = status.value;
      state.search = search.value.trim();
      state.page = 1;
      renderTable(target);
    };
    search.onkeydown = e => { if (e.key === 'Enter') target.querySelector('#clSearchBtn').click(); };
    status.onchange = () => target.querySelector('#clSearchBtn').click();
    target.querySelector('#clResetBtn').onclick = () => { state.status=''; state.search=''; state.page=1; renderTable(target); };
    const prev = target.querySelector('#clPrev');
    const next = target.querySelector('#clNext');
    if (prev) prev.onclick = () => { if (state.page > 1) { state.page--; renderTable(target); } };
    if (next) next.onclick = () => { if (state.page < pages) { state.page++; renderTable(target); } };
  }

  async function load(target) {
    injectStyles();
    target.innerHTML = '<div class="card"><div class="loading-box"><div class="spin"></div><p>자격증명발급대장 불러오는 중...</p></div></div>';
    try {
      // 기존에 정상 운영 중인 '자격증명발급번호 발급 이력 관리' API를 그대로 사용한다.
      // 별도 certificate_issuance_ledger API/테이블에 의존하지 않아 Railway 기동에도 영향을 주지 않는다.
      const data = await request('/api/admin/certificate-numbers?page=1&limit=2000&sort=desc');
      state.meta = data || {};
      state.rows = currentYearRows(Array.isArray(data.items) ? data.items : [], state.meta);
      state.page = 1;
      if (document.documentElement.contains(target) && currentTarget === target) renderTable(target);
    } catch (error) {
      if (document.documentElement.contains(target)) {
        target.innerHTML = `<div class="card"><div class="cl-empty">${esc(error.message)}</div></div>`;
      }
    }
  }

  async function renderInto(target) {
    const el = typeof target === 'string' ? document.querySelector(target) : target;
    if (!el) return;
    currentTarget = el;
    await load(el);
  }

  async function refreshInto(target) {
    const el = target || currentTarget;
    if (!el) return;
    currentTarget = el;
    await load(el);
  }

  window.CertificateLedger = { renderInto, refreshInto };

  function removeLegacyTopMenu() {
    document.querySelectorAll('.certificate-ledger-nav').forEach(el => el.remove());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', removeLegacyTopMenu);
  else removeLegacyTopMenu();
})();
