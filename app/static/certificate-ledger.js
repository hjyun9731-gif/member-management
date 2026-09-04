(function () {
  'use strict';

  const state = { search: '', status: '', page: 1 };
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
      .cl-stats{display:grid;grid-template-columns:repeat(5,minmax(82px,1fr));gap:6px;margin-bottom:8px}
      .cl-stat{background:#fff;border:1px solid var(--c-border);border-radius:8px;padding:8px 9px;box-shadow:var(--sh-xs)}
      .cl-stat span{display:block;font-size:10px;color:var(--c-text-3);white-space:nowrap}.cl-stat strong{display:block;font-size:17px;margin-top:2px}
      .cl-pending strong{color:#d97706}.cl-issued strong{color:#059669}.cl-wait strong{color:#6b7280}.cl-approved strong{color:#5e6ad2}
      .cl-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:20px;font-size:10.5px;font-weight:800;white-space:nowrap}
      .cl-badge.pending{color:#b45309;background:#fff7ed}.cl-badge.issued{color:#047857;background:#ecfdf5}
      .cl-badge.wait{color:#6b7280;background:#f3f4f6}.cl-badge.approved{color:#4f46e5;background:#eef2ff}.cl-badge.cancelled{color:#6b7280;background:#f3f4f6}
      .cl-table th,.cl-table td{white-space:nowrap}.cl-table td{font-size:11.5px}.cl-table .cl-name{font-weight:700;color:var(--c-text)}
      .cl-actions{display:flex;gap:4px;justify-content:center;align-items:center}.cl-empty{padding:30px;text-align:center;color:var(--c-text-3)}
      .cl-history{display:grid;gap:8px}.cl-history-item{border-left:3px solid var(--c-pri);padding:7px 10px;background:var(--c-bg);border-radius:0 7px 7px 0}
      .cl-history-item strong{font-size:12px}.cl-history-item div{font-size:11px;color:var(--c-text-3);margin-top:2px}
      @media(max-width:1400px){.candidate-right-pane .cl-stats{grid-template-columns:repeat(3,1fr)}}
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
    if ((data.pages || 1) <= 1) return '';
    return `<div class="pgn"><span>총 <strong>${Number(data.total || 0).toLocaleString()}</strong>건</span><div class="pgn-btns">
      <button class="pgn-btn" data-cl-page="${data.page - 1}" ${data.page <= 1 ? 'disabled' : ''}>‹</button>
      <span style="padding:5px 9px;font-size:12px">${data.page} / ${data.pages}</span>
      <button class="pgn-btn" data-cl-page="${data.page + 1}" ${data.page >= data.pages ? 'disabled' : ''}>›</button>
    </div></div>`;
  }

  function actionButtons(row) {
    const history = `<button class="btn bo btn-xs" data-cl-history="${row.id}">이력</button>`;
    if (row.issuance_status === '발급완료') {
      return `${history}<button class="btn bp btn-xs" data-cl-form="${row.id}">양식 열기</button>`;
    }
    if (row.document_number) {
      return `${history}<button class="btn bg btn-xs" data-cl-form="${row.id}">자격증명 생성</button>`;
    }
    return `${history}<span style="font-size:10.5px;color:var(--c-text-3);padding:3px 5px">예정자에서 발급번호 부여</span>`;
  }

  async function render(page = 1, target = currentTarget) {
    if (!target || !document.documentElement.contains(target)) return;
    currentTarget = target;
    state.page = page;
    injectStyles();
    target.innerHTML = '<div class="card"><div class="loading-box"><div class="spin"></div><p>자격증명 발급대장 불러오는 중...</p></div></div>';
    try {
      const params = new URLSearchParams({ page: String(page), limit: '50' });
      if (state.search) params.set('search', state.search);
      if (state.status) params.set('status', state.status);
      const [data, stats] = await Promise.all([
        request('GET', `/api/certificate-ledger?${params}`),
        request('GET', '/api/certificate-ledger/stats'),
      ]);
      if (!document.documentElement.contains(target) || currentTarget !== target) return;
      const counts = stats.counts || {};
      target.innerHTML = `
        <div class="cl-head" style="display:flex;gap:8px;align-items:center;justify-content:space-between;flex-wrap:wrap">
          <div class="cl-help">신청서류 접수 → 예정자 입력 → 발급번호 확인 → 자격증명 생성·발급완료 → 이후 시청 인가일자 반영</div>
          <div style="display:flex;gap:6px">
            <button class="btn bo btn-sm" id="clRefreshBtn">새로고침</button>
            <button class="btn bp btn-sm" id="clReconcileBtn">대장 정리</button>
          </div>
        </div>
        <div class="cl-stats">
          <div class="cl-stat"><span>누적 자격증명번호</span><strong>${esc(stats.last_certificate_number || stats.total || '-')}</strong></div>
          <div class="cl-stat cl-pending"><span>생성대기</span><strong>${Number(counts['생성대기'] || 0).toLocaleString()}</strong></div>
          <div class="cl-stat cl-issued"><span>발급완료</span><strong>${Number(counts['발급완료'] || 0).toLocaleString()}</strong></div>
          <div class="cl-stat cl-wait"><span>인가대기</span><strong>${Number(counts['인가대기'] || 0).toLocaleString()}</strong></div>
          <div class="cl-stat cl-approved"><span>인가완료</span><strong>${Number(counts['인가완료'] || 0).toLocaleString()}</strong></div>
        </div>
        <div class="card">
          <div class="frow">
            <select class="fsel" id="clStatus">
              <option value="">전체 상태</option>
              ${['생성대기','발급완료','취소','인가대기','인가완료'].map(s => `<option value="${s}" ${state.status === s ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
            <input class="srch" id="clSearch" value="${esc(state.search)}" placeholder="성명, 차량번호, 자격증명번호, 담당자">
            <button class="btn bp btn-sm" id="clSearchBtn">조회</button>
            <button class="btn bo btn-sm" id="clResetBtn">초기화</button>
            <span class="cnt" style="margin-left:auto">대장 ${Number(data.total || 0).toLocaleString()}건</span>
          </div>
          ${data.items.length ? `<div class="tbl-wrap"><table class="cl-table"><thead><tr>
            <th>자격증명번호</th><th>지역</th><th>차량번호</th><th>성명</th><th>자격증명 발급일</th><th>자격증명 상태</th><th>시청 인가</th><th>최근 담당자</th><th>관리</th>
          </tr></thead><tbody>${data.items.map(row => `<tr>
            <td><strong>${esc(row.document_number || '-')}</strong></td><td>${esc(row.region || '-')}</td><td>${esc(row.vehicle_number || '-')}</td>
            <td class="cl-name">${esc(row.name)}</td><td>${esc(display(row.certificate_issue_date))}</td>
            <td>${issuanceBadge(row.issuance_status)}</td><td>${approvalBadge(row.approval_status)}</td><td>${esc(row.latest_operator || '-')}</td>
            <td><div class="cl-actions">${actionButtons(row)}</div></td>
          </tr>`).join('')}</tbody></table></div>${pager(data)}` : '<div class="cl-empty">아직 자격증명 발급대장에 연결된 신규 예정자가 없습니다.</div>'}
        </div>`;

      const searchBtn=target.querySelector('#clSearchBtn');
      const searchInput=target.querySelector('#clSearch');
      const statusSelect=target.querySelector('#clStatus');
      const resetBtn=target.querySelector('#clResetBtn');
      const refreshBtn=target.querySelector('#clRefreshBtn');
      const reconcileBtn=target.querySelector('#clReconcileBtn');
      refreshBtn.onclick = () => render(state.page || 1, target);
      reconcileBtn.onclick = async () => {
        const original = reconcileBtn.textContent;
        reconcileBtn.disabled = true;
        reconcileBtn.textContent = '정리 중...';
        try {
          const result = await request('POST', '/api/certificate-ledger/refresh');
          const changed = Number(result.candidate_changes || 0) + Number(result.number_log_changes || 0);
          reconcileBtn.textContent = changed ? `정리 완료 (${changed})` : '정리 완료';
          await render(1, target);
        } catch (error) {
          reconcileBtn.textContent = '정리 실패';
          alert(error.message || '대장 정리 중 오류가 발생했습니다.');
        } finally {
          setTimeout(() => {
            if (document.documentElement.contains(reconcileBtn)) {
              reconcileBtn.disabled = false;
              reconcileBtn.textContent = original;
            }
          }, 1200);
        }
      };
      searchBtn.onclick = () => {
        state.search = searchInput.value.trim();
        state.status = statusSelect.value;
        render(1, target);
      };
      searchInput.onkeydown = event => { if (event.key === 'Enter') searchBtn.click(); };
      statusSelect.onchange = () => searchBtn.click();
      resetBtn.onclick = () => { state.search = ''; state.status = ''; render(1, target); };
      target.querySelectorAll('[data-cl-page]:not([disabled])').forEach(button => {
        button.onclick = () => render(Number(button.dataset.clPage), target);
      });
      target.querySelectorAll('[data-cl-history]').forEach(button => button.onclick = () => showHistory(Number(button.dataset.clHistory)));
      target.querySelectorAll('[data-cl-form]').forEach(button => button.onclick = () => openForm(Number(button.dataset.clForm)));
    } catch (error) {
      if (document.documentElement.contains(target)) target.innerHTML = `<div class="card"><div class="cl-empty">${esc(error.message)}</div></div>`;
    }
  }

  async function showHistory(id) {
    if (typeof openModal !== 'function') return;
    openModal('자격증명 처리 이력', '<div id="clHistory"><div class="loading-box"><div class="spin"></div></div></div>', '<button class="btn bo btn-sm" onclick="closeModal()">닫기</button>', 'msm');
    try {
      const data = await request('GET', `/api/certificate-ledger/${id}/history`);
      document.getElementById('clHistory').innerHTML = data.items.length ? `<div class="cl-history">${data.items.map(item => `<div class="cl-history-item">
        <strong>${esc(item.event_type)}</strong>
        <div>${esc(display(item.created_at))} · 담당자 ${esc(item.operator || '-')}</div><div>${esc(item.memo || '')}</div>
      </div>`).join('')}</div>` : '<div class="cl-empty">이력이 없습니다.</div>';
    } catch (error) {
      document.getElementById('clHistory').innerHTML = `<div class="cl-empty">${esc(error.message)}</div>`;
    }
  }

  function openForm(id) {
    window.open(`/static/certificate-form.html?ledger_id=${id}`, '_blank', 'noopener');
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
