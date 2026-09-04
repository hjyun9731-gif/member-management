(function () {
  'use strict';

  const state = { search: '', status: '', page: 1 };
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
      .certificate-ledger-nav{padding:6px 11px!important;font-size:12px!important}
      .cl-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px}
      .cl-title{font-size:20px;font-weight:800;letter-spacing:-.5px}.cl-help{font-size:11px;color:var(--c-text-3);margin-top:2px}
      .cl-stats{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:8px;margin-bottom:10px}
      .cl-stat{background:#fff;border:1px solid var(--c-border);border-radius:9px;padding:10px 12px;box-shadow:var(--sh-xs)}
      .cl-stat span{display:block;font-size:11px;color:var(--c-text-3)}.cl-stat strong{display:block;font-size:20px;margin-top:2px}
      .cl-wait strong{color:#d97706}.cl-approved strong{color:#5e6ad2}.cl-issued strong{color:#059669}
      .cl-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:20px;font-size:10.5px;font-weight:800;white-space:nowrap}
      .cl-badge.wait{color:#b45309;background:#fff7ed}.cl-badge.approved{color:#4f46e5;background:#eef2ff}.cl-badge.issued{color:#047857;background:#ecfdf5}
      .cl-table th,.cl-table td{white-space:nowrap}.cl-table td{font-size:12px}.cl-table .cl-name{font-weight:700;color:var(--c-text)}
      .cl-actions{display:flex;gap:4px;justify-content:center;align-items:center}.cl-empty{padding:34px;text-align:center;color:var(--c-text-3)}
      .cl-candidate-results{border:1px solid var(--c-border);border-radius:7px;max-height:210px;overflow:auto;margin-top:5px;background:#fff}
      .cl-candidate{display:block;width:100%;border:0;border-bottom:1px solid var(--c-border-l);background:#fff;text-align:left;padding:8px 10px;cursor:pointer;font-family:inherit}
      .cl-candidate:hover{background:var(--c-pri-bg)}.cl-candidate[disabled]{cursor:not-allowed;opacity:.5;background:#f9fafb}
      .cl-candidate strong{font-size:12px}.cl-candidate small{display:block;color:var(--c-text-3);margin-top:2px}
      .cl-selected{margin-top:8px;padding:9px 10px;background:var(--c-pri-bg);border:1px solid #dfe3ff;border-radius:7px;font-size:12px}
      .cl-history{display:grid;gap:8px}.cl-history-item{border-left:3px solid var(--c-pri);padding:7px 10px;background:var(--c-bg);border-radius:0 7px 7px 0}
      .cl-history-item strong{font-size:12px}.cl-history-item div{font-size:11px;color:var(--c-text-3);margin-top:2px}
      @media(max-width:1100px){.cl-stats{grid-template-columns:repeat(2,1fr)}.certificate-ledger-nav{padding:6px 8px!important;font-size:11px!important}}
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

  function statusBadge(status) {
    const cls = status === '발급완료' ? 'issued' : status === '인가완료' ? 'approved' : 'wait';
    return `<span class="cl-badge ${cls}">${esc(status)}</span>`;
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
    if (row.status === '발급완료') {
      return `${history}<button class="btn bp btn-xs" data-cl-form="${row.id}">양식 열기</button>`;
    }
    if (row.document_number) {
      return `${history}<button class="btn bg btn-xs" data-cl-form="${row.id}">자격증명 생성</button>`;
    }
    return `${history}<span style="font-size:10.5px;color:var(--c-text-3);padding:3px 5px">발급번호 먼저 부여</span>`;
  }

  async function render(page = 1) {
    state.page = page;
    const content = document.getElementById('content');
    if (!content) return;
    content.innerHTML = '<div class="loading-box"><div class="spin"></div><p>자격증명 발급대장 불러오는 중...</p></div>';
    try {
      const params = new URLSearchParams({ page: String(page), limit: '50' });
      if (state.search) params.set('search', state.search);
      if (state.status) params.set('status', state.status);
      const [data, stats] = await Promise.all([
        request('GET', `/api/certificate-ledger?${params}`),
        request('GET', '/api/certificate-ledger/stats'),
      ]);
      const counts = stats.counts || {};
      content.innerHTML = `
        <div class="cl-head">
          <div><div class="cl-title">자격증명 발급대장</div><div class="cl-help">서류 접수 → 예정자 입력·발급번호 부여 → 자격증명 생성 / 시청 인가 공문 후 예정자 등록·인가일자 반영</div></div>
          <button class="btn bg btn-sm" id="clCreateBtn">+ 자격증명생성</button>
        </div>
        <div class="cl-stats">
          <div class="cl-stat"><span>전체</span><strong>${Number(stats.total || 0).toLocaleString()}</strong></div>
          <div class="cl-stat cl-wait"><span>인가대기</span><strong>${Number(counts['인가대기'] || 0).toLocaleString()}</strong></div>
          <div class="cl-stat cl-approved"><span>인가완료</span><strong>${Number(counts['인가완료'] || 0).toLocaleString()}</strong></div>
          <div class="cl-stat cl-issued"><span>발급완료</span><strong>${Number(counts['발급완료'] || 0).toLocaleString()}</strong></div>
        </div>
        <div class="card">
          <div class="frow">
            <select class="fsel" id="clStatus">
              <option value="">전체 상태</option>
              ${['인가대기','인가완료','발급완료'].map(s => `<option value="${s}" ${state.status === s ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
            <input class="srch" id="clSearch" value="${esc(state.search)}" placeholder="성명, 차량번호, 증명서 No., 담당자">
            <button class="btn bp btn-sm" id="clSearchBtn">조회</button>
            <button class="btn bo btn-sm" id="clResetBtn">초기화</button>
            <span class="cnt" style="margin-left:auto">${Number(data.total || 0).toLocaleString()}건</span>
          </div>
          ${data.items.length ? `<div class="tbl-wrap"><table class="cl-table"><thead><tr>
            <th>자격증명 발급일</th><th>증명서 No.</th><th>지역</th><th>차량번호</th><th>성명</th><th>자격증번호</th><th>처리상태</th><th>최근 담당자</th><th>관리</th>
          </tr></thead><tbody>${data.items.map(row => `<tr>
            <td>${esc(display(row.certificate_issue_date))}</td><td><strong>${esc(row.document_number || '-')}</strong></td><td>${esc(row.region || '-')}</td>
            <td>${esc(row.vehicle_number || '-')}</td><td class="cl-name">${esc(row.name)}</td><td>${esc(row.qualification_number || '-')}</td>
            <td>${statusBadge(row.status)}</td><td>${esc(row.latest_operator || '-')}</td><td><div class="cl-actions">${actionButtons(row)}</div></td>
          </tr>`).join('')}</tbody></table></div>${pager(data)}` : '<div class="cl-empty">자격증명 발급대장 기록이 없습니다.</div>'}
        </div>`;

      document.getElementById('clCreateBtn').onclick = openCreate;
      document.getElementById('clSearchBtn').onclick = () => {
        state.search = document.getElementById('clSearch').value.trim();
        state.status = document.getElementById('clStatus').value;
        render(1);
      };
      document.getElementById('clSearch').onkeydown = event => {
        if (event.key === 'Enter') document.getElementById('clSearchBtn').click();
      };
      document.getElementById('clStatus').onchange = () => document.getElementById('clSearchBtn').click();
      document.getElementById('clResetBtn').onclick = () => {
        state.search = ''; state.status = ''; render(1);
      };
      content.querySelectorAll('[data-cl-page]:not([disabled])').forEach(button => {
        button.onclick = () => render(Number(button.dataset.clPage));
      });
      content.querySelectorAll('[data-cl-history]').forEach(button => button.onclick = () => showHistory(Number(button.dataset.clHistory)));
      content.querySelectorAll('[data-cl-form]').forEach(button => button.onclick = () => openForm(Number(button.dataset.clForm)));
    } catch (error) {
      content.innerHTML = `<div class="card"><div class="cl-empty">${esc(error.message)}</div></div>`;
    }
  }

  async function searchCandidates(term = '') {
    const box = document.getElementById('clCandidateResults');
    if (!box) return;
    box.innerHTML = '<div style="padding:10px;font-size:12px;color:var(--c-text-3)">검색 중...</div>';
    try {
      const data = await request('GET', `/api/certificate-ledger/candidates?limit=30&search=${encodeURIComponent(term)}`);
      if (!data.items.length) {
        box.innerHTML = '<div style="padding:10px;font-size:12px;color:var(--c-text-3)">검색 결과가 없습니다.</div>';
        return;
      }
      const candidates = data.items;
      box.innerHTML = candidates.map(row => `<button type="button" class="cl-candidate" data-candidate-id="${row.id}">
        <strong>${esc(row.name || '-')} · ${esc(row.vehicle_number || '-')}</strong>
        <small>${esc(row.region || '-')} · ${row.is_registered ? '등록완료' : '예정자'} · 대장상태 ${esc(row.ledger_status || (row.is_registered ? '인가완료' : '인가대기'))} · 증명서 No. ${esc(row.document_number || '미발급')}</small>
      </button>`).join('');
      box.querySelectorAll('.cl-candidate').forEach(button => {
        button.onclick = () => selectCandidate(candidates.find(row => row.id === Number(button.dataset.candidateId)));
      });
    } catch (error) {
      box.innerHTML = `<div style="padding:10px;color:var(--c-danger)">${esc(error.message)}</div>`;
    }
  }

  function selectCandidate(candidate) {
    window.__clSelectedCandidate = candidate;
    document.getElementById('clCandidateId').value = candidate.id;
    document.getElementById('clSelected').innerHTML = `<strong>${esc(candidate.name)}</strong> · ${esc(candidate.vehicle_number || '-')} · ${esc(candidate.region || '-')}<br>
      <span style="color:var(--c-text-3)">현재 상태: ${esc(candidate.ledger_status || (candidate.is_registered ? '인가완료' : '인가대기'))} / 증명서 No.: ${esc(candidate.document_number || '미부여')}</span>`;
    document.getElementById('clCandidateResults').style.display = 'none';
  }

  function openCreate() {
    if (typeof openModal !== 'function') return;
    openModal('자격증명 생성', `
      <input type="hidden" id="clCandidateId">
      <div class="fi"><label>예정자 찾기 <span class="req">*</span></label><input class="fc" id="clCandidateSearch" placeholder="성명 또는 차량번호 입력"></div>
      <div class="cl-candidate-results" id="clCandidateResults"></div>
      <div class="cl-selected" id="clSelected">목록에서 예정자를 선택하세요.</div>
      <div class="fi" style="margin-top:10px"><label>자격증번호</label><input class="fc" id="clQualificationNumber" placeholder="자격증번호가 있으면 입력"></div>
      <div style="font-size:11px;color:var(--c-text-3);margin-top:8px">예정자 입력 화면에서 자격증명발급번호를 먼저 부여한 뒤 자격증명을 생성합니다. 시청 인가 전(인가대기)에도 자격증명 생성이 가능합니다.</div>
    `, '<button class="btn bg btn-sm" id="clSaveBtn">자격증명 생성</button><button class="btn bo btn-sm" onclick="closeModal()">취소</button>', 'msm');
    let timer;
    const input = document.getElementById('clCandidateSearch');
    input.oninput = () => {
      clearTimeout(timer);
      document.getElementById('clCandidateResults').style.display = '';
      timer = setTimeout(() => searchCandidates(input.value.trim()), 220);
    };
    input.onfocus = () => { document.getElementById('clCandidateResults').style.display = ''; };
    document.getElementById('clSaveBtn').onclick = async () => {
      const candidateId = Number(document.getElementById('clCandidateId').value || 0);
      const qualification = document.getElementById('clQualificationNumber').value.trim();
      if (!candidateId) { if (typeof toast === 'function') toast('예정자를 선택하세요.', 'warn'); return; }
      const button = document.getElementById('clSaveBtn');
      button.disabled = true; button.textContent = '생성 중...';
      try {
        const selected = window.__clSelectedCandidate || {};
        let row;
        if (!selected.document_number) {
          if (typeof toast === 'function') toast('자격증명발급번호가 없습니다. 예정자 입력 화면에서 발급번호를 먼저 부여하세요.', 'warn');
          button.disabled = false; button.textContent = '자격증명 생성';
          return;
        }
        if (selected.already_connected && selected.ledger_id) {
          row = await request('GET', `/api/certificate-ledger/${selected.ledger_id}`);
        } else {
          row = await request('POST', '/api/certificate-ledger', { candidate_id: candidateId, qualification_number: qualification });
        }
        if (typeof closeModal === 'function') closeModal();
        openForm(row.id);
        render(1);
      } catch (_) {
        button.disabled = false; button.textContent = '자격증명 생성';
      }
    };
    searchCandidates('');
  }

  async function showHistory(id) {
    if (typeof openModal !== 'function') return;
    openModal('자격증명 처리 이력', '<div id="clHistory"><div class="loading-box"><div class="spin"></div></div></div>', '<button class="btn bo btn-sm" onclick="closeModal()">닫기</button>', 'msm');
    try {
      const data = await request('GET', `/api/certificate-ledger/${id}/history`);
      document.getElementById('clHistory').innerHTML = data.items.length ? `<div class="cl-history">${data.items.map(item => `<div class="cl-history-item">
        <strong>${esc(item.event_type)} · ${esc(item.to_status)}</strong>
        <div>${esc(display(item.created_at))} · 담당자 ${esc(item.operator || '-')}</div><div>${esc(item.memo || '')}</div>
      </div>`).join('')}</div>` : '<div class="cl-empty">이력이 없습니다.</div>';
    } catch (error) {
      document.getElementById('clHistory').innerHTML = `<div class="cl-empty">${esc(error.message)}</div>`;
    }
  }

  function openForm(id) {
    window.open(`/static/certificate-form.html?ledger_id=${id}`, '_blank', 'noopener');
  }

  function activate() {
    if (typeof ST !== 'undefined') { ST.cat = 'certificate-ledger'; ST.sub = 'certificate-ledger'; }
    document.body.classList.remove('deadline-mode');
    document.querySelectorAll('.cat-btn').forEach(button => button.classList.toggle('active', button.dataset.cat === 'certificate-ledger'));
    const sub = document.getElementById('subBar');
    if (sub) sub.innerHTML = '<button class="sub-tab active" type="button">자격증명 발급대장</button>';
    render(1);
  }

  function installMenu() {
    injectStyles();
    const nav = document.getElementById('catNav');
    if (!nav || document.querySelector('.certificate-ledger-nav')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'cat-btn certificate-ledger-nav';
    button.dataset.cat = 'certificate-ledger';
    button.textContent = '자격증명 발급대장';
    button.addEventListener('click', activate);
    nav.appendChild(button);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', installMenu);
  else installMenu();
})();
