#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# member-management / app/static/app.js 폐업현황 UI 패치
# - 폐업사유(reason) 항상 표시
# - 양수인(transferee), 이관지역(transfer_region) 기본 숨김
# - 상단 버튼으로 두 컬럼 전체 토글
# - 백엔드/DB/API는 건드리지 않음

from __future__ import annotations
from pathlib import Path
import sys
import shutil
from datetime import datetime

repo = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
target = repo / "app" / "static" / "app.js"

if not target.exists():
    raise SystemExit(f"[중단] 파일을 찾을 수 없습니다: {target}")

raw = target.read_text(encoding="utf-8")
if "async function renderClosures()" not in raw:
    raise SystemExit("[중단] renderClosures()를 찾지 못했습니다. 현재 app.js 구조를 확인하세요.")

if "closure-ledger-toggle-v1" in raw and 'id="clTransferToggle"' in raw and "{label:'폐업사유'}" in raw:
    print("[완료] 이미 동일 패치가 적용되어 있습니다.")
    raise SystemExit(0)

start = raw.find("// ===== CLOSURES =====")
end = raw.find("// ===== CHANGE HISTORY =====", start)
if start < 0 or end < 0:
    raise SystemExit("[중단] CLOSURES 블록 경계를 찾지 못했습니다.")

before = raw[:start]
section = raw[start:end]
after = raw[end:]

style_helper = r"""
function ensureClosureLedgerToggleStyle(){
  if(document.getElementById('closure-ledger-toggle-v1')) return;
  const st=document.createElement('style');
  st.id='closure-ledger-toggle-v1';
  st.textContent=`
    #closureLedgerCard .cl-transfer-col{display:none!important}
    #closureLedgerCard.cl-show-transfer .cl-transfer-col{display:table-cell!important}
    #closureLedgerCard .cl-reason-col{min-width:120px;max-width:240px}
    #closureLedgerCard tbody td.cl-reason-col{
      white-space:normal;
      line-height:1.35;
      word-break:keep-all;
      overflow-wrap:anywhere;
    }
    #clTransferToggle{white-space:nowrap}
  `;
  document.head.appendChild(st);
}

"""

if "function ensureClosureLedgerToggleStyle()" not in before:
    before += style_helper

def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"[중단] {label} 앵커가 {count}개입니다. 안전을 위해 수정하지 않았습니다.")
    return text.replace(old, new, 1)

section = replace_once(
    section,
    """async function renderClosures(){
  ensurePermitLedgerLikeNewStyle();
  const f=ST.fl.cl||{};""",
    """async function renderClosures(){
  ensurePermitLedgerLikeNewStyle();
  ensureClosureLedgerToggleStyle();
  if(typeof ST.inner.clTransferVisible!=='boolean') ST.inner.clTransferVisible=false;
  const f=ST.fl.cl||{};""",
    "renderClosures 시작부"
)

section = replace_once(
    section,
    """<div class="card permit-ledger-like-new" id="closureLedgerCard">""",
    """<div class="card permit-ledger-like-new ${ST.inner.clTransferVisible?'cl-show-transfer':''}" id="closureLedgerCard">""",
    "closureLedgerCard"
)

section = replace_once(
    section,
    """        <button class="btn bp btn-sm" id="clSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="clRstBtn">초기화</button>""",
    """        <button class="btn bp btn-sm" id="clSrchBtn">조회</button>
        <button class="btn bo btn-sm" id="clRstBtn">초기화</button>
        <button class="btn bo btn-sm" id="clTransferToggle">${ST.inner.clTransferVisible?'양수인/이관지역 숨기기':'양수인/이관지역 보기'}</button>""",
    "폐업현황 검색버튼 영역"
)

section = replace_once(
    section,
    """    {label:'구분'},{label:'가입'},{label:'핸드폰'},{label:'접수일자'},{label:'처리일자'},{label:'가입일자'},
    {label:'양수인'},{label:'이관지역'},{label:'차종'},{label:'유종'},{label:'주소'},{label:'비고'},{label:'관리',noSort:true}
  ];

  const doSearch=async(pg=1)=>{""",
    """    {label:'구분'},{label:'가입'},{label:'핸드폰'},{label:'접수일자'},{label:'처리일자'},{label:'가입일자'},
    {label:'폐업사유'},{label:'양수인'},{label:'이관지역'},{label:'차종'},{label:'유종'},{label:'주소'},{label:'비고'},{label:'관리',noSort:true}
  ];

  const closureHeaders=plainHeaders(hdrs)
    .replace('<th class="">폐업사유</th>','<th class="cl-reason-col">폐업사유</th>')
    .replace('<th class="">양수인</th>','<th class="cl-transfer-col">양수인</th>')
    .replace('<th class="">이관지역</th>','<th class="cl-transfer-col">이관지역</th>');

  const doSearch=async(pg=1)=>{""",
    "폐업현황 헤더 목록"
)

section = replace_once(
    section,
    """      <thead><tr>${plainHeaders(hdrs)}</tr></thead>""",
    """      <thead><tr>${closureHeaders}</tr></thead>""",
    "폐업현황 thead"
)

section = replace_once(
    section,
    """          <td>${fv(joinDate)}</td>
          <td>${fv(r.transferee)}</td>
          <td>${fv(r.transfer_region)}</td>
          <td title="${e_(r.vehicle_type||'')}">${fv(r.vehicle_type)}</td>""",
    """          <td>${fv(joinDate)}</td>
          <td class="cl-reason-col" title="${e_(r.reason||'')}">${fv(r.reason)}</td>
          <td class="cl-transfer-col">${fv(r.transferee)}</td>
          <td class="cl-transfer-col">${fv(r.transfer_region)}</td>
          <td title="${e_(r.vehicle_type||'')}">${fv(r.vehicle_type)}</td>""",
    "폐업현황 행 데이터"
)

section = replace_once(
    section,
    """  document.getElementById('clSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('clSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById('clRstBtn').onclick=()=>{ST.fl.cl={};renderClosures();};""",
    """  document.getElementById('clSrchBtn').onclick=()=>doSearch(1);
  document.getElementById('clSrch').onkeydown=e=>{if(e.key==='Enter')doSearch(1);};
  document.getElementById('clRstBtn').onclick=()=>{ST.fl.cl={};renderClosures();};
  document.getElementById('clTransferToggle').onclick=()=>{
    ST.inner.clTransferVisible=!ST.inner.clTransferVisible;
    const card=document.getElementById('closureLedgerCard');
    const btn=document.getElementById('clTransferToggle');
    card?.classList.toggle('cl-show-transfer',ST.inner.clTransferVisible);
    if(btn) btn.textContent=ST.inner.clTransferVisible?'양수인/이관지역 숨기기':'양수인/이관지역 보기';
  };""",
    "폐업현황 이벤트 바인딩"
)

result = before + section + after

checks = [
    "closure-ledger-toggle-v1",
    'id="clTransferToggle"',
    "{label:'폐업사유'}",
    'class="cl-reason-col"',
    'class="cl-transfer-col"',
    "${fv(r.reason)}",
    "${fv(r.transferee)}",
    "${fv(r.transfer_region)}",
]
missing = [x for x in checks if x not in result]
if missing:
    raise SystemExit("[중단] 패치 후 검증 실패: " + ", ".join(missing))

if result.count("function ensureClosureLedgerToggleStyle()") != 1:
    raise SystemExit("[중단] 스타일 함수 중복 검증 실패")
if result.count('id="clTransferToggle"') != 1:
    raise SystemExit("[중단] 토글 버튼 중복 검증 실패")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = target.with_name(f"app.js.before-closure-toggle-{stamp}.bak")
shutil.copy2(target, backup)
target.write_text(result, encoding="utf-8", newline="")

print("[완료] app/static/app.js 수정")
print(f"[백업] {backup}")
print("[반영] 폐업사유(reason) 항상 표시")
print("[반영] 양수인/이관지역 기본 숨김 + 상단 토글 버튼")
print("[미변경] 백엔드/DB/API/다른 화면")
