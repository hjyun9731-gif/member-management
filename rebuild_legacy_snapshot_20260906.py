"""2026-09-06 기준원장 갱신 스크립트.

입력: /mnt/data/_사용_2026미수금.xlsx (시트 '2026년회비내역')
출력: app/data/legacy_receivables_2026.json  (기존 파일을 덮어씀)

이전(2026-08-31) 스크립트와의 차이:
- data_through_month를 8 -> 9로 확장 (9월 컬럼까지 읽음)
- current_arrears는 "그 행에서 값이 채워진 가장 마지막 달의 미수금 컬럼"을 그대로 사용한다.
  (특정 달 하드코딩 금지 — 사용자가 준 원장이 정답이므로 재계산하지 않고 그대로 반영)
- 이전 스크립트의 CORRECTIONS/RATE_RECALC(수기 보정)는 이번 원장에 그대로 재적용하지 않는다.
  수기 보정 대상 차량번호가 이번 파일에서 이미 바뀌어 있어(예: 이건우 80배1634 -> 확인 불가)
  옛 보정을 맹목적으로 다시 적용하면 오히려 최신 원장 값과 어긋날 수 있기 때문이다.
  사용자가 "최신 원장 금액을 그대로 신뢰하라"고 명시했으므로 원장 원본값을 그대로 쓴다.
- 이름만 같고 차량번호가 다른 행(예: 강성준 83배1007 / 강원83배1012)은 별도 행으로 보존한다.
  (키를 (norm_name, norm_vehicle)로 유지 — 병합하지 않음)
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import openpyxl

SOURCE = Path('/mnt/user-data/uploads/_사용_2026미수금.xlsx')
OUT = Path(__file__).resolve().parent / 'app' / 'data' / 'legacy_receivables_2026.json'
SHEET_NAME = '2026년회비내역'
# 중요: 앱의 컷오버 상수(LEGACY_DATA_THROUGH_MONTH=8, LEGACY_NEXT_BILL_DATE=2026-09-01)는
# "원장 1~8월분은 baseline으로, 9월부터는 프로그램이 자체 자동부과"를 전제로 짜여 있다.
# 원장의 9월 컬럼까지 baseline에 넣으면 프로그램이 만드는 9월 자동부과와 이중 계산된다.
# 따라서 baseline은 그대로 8월까지만 반영하고(기존과 동일), 9월 이후 증가분은
# 이미 운영 중인 자동부과 로직이 처리하도록 둔다. (예: 설악안전서비스 8월=60,000 +
# 9월 자동부과 10,000 = 70,000으로 자연히 원장의 9월 값과 맞아떨어진다.)
DATA_THROUGH_MONTH = 8

EXPECTED_FEES = {'협회비': 10000, '관리비': 5000, '70세': 5000}

# 활성 수납/미수금 대상에서 제외할 사람. 데이터는 원장에 그대로 남기되(삭제 금지),
# 시스템의 receivable_profiles.receivable_active 플래그만 0으로 표시하는 데 사용한다.
# 키: (norm_name, norm_vehicle) — 반드시 차량번호까지 일치해야 제외 대상으로 잡는다(이름만으로 제외 금지).
ACTIVE_EXCLUDE = {
    ('김연심', '강원81자3121호'): '요청에 따라 활성 수납/미수금 대상에서 제외 (데이터는 보존)',
    ('이말분', '강원83자5171호'): '요청에 따라 활성 수납/미수금 대상에서 제외 (데이터는 보존)',
}


def clean(v) -> str:
    if v is None:
        return ''
    return str(v).strip().replace('\u3000', '').strip()


def norm_name(v) -> str:
    return re.sub(r'\s+', '', clean(v))


def norm_vehicle(v) -> str:
    return re.sub(r'[^0-9A-Za-z가-힣]', '', clean(v))


def amount(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = clean(v).replace(',', '').replace('원', '')
    if not s or re.fullmatch(r'[-#\s]*', s):
        return None
    try:
        return int(round(float(s)))
    except Exception:
        return None


def excel_date(v) -> str:
    import datetime as dt
    if v is None:
        return ''
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return clean(v)


def month_has_activity(m: dict) -> bool:
    return any(m.get(k) is not None for k in ('billed_total', 'payment', 'arrears')) or bool(m.get('payment_date'))


def add_fixed_fee_metadata(months: list[dict], account: str, carry: int) -> None:
    expected = EXPECTED_FEES[account]
    active = [int(m['month']) for m in months[:DATA_THROUGH_MONTH] if month_has_activity(m)]
    first_month = min(active) if active else None
    prev_arrears = int(carry or 0)
    for m in months[:DATA_THROUGH_MONTH]:
        month_no = int(m['month'])
        regular = expected if first_month is not None and month_no >= first_month else 0
        m['monthly_charge'] = regular
        if m.get('arrears') is not None:
            payment = int(m.get('payment') or 0)
            m['legacy_adjustment'] = int(m['arrears']) - prev_arrears - regular + payment
            prev_arrears = int(m['arrears'])
        else:
            m['legacy_adjustment'] = None
    for m in months[DATA_THROUGH_MONTH:]:
        m['monthly_charge'] = None
        m['legacy_adjustment'] = None


wb = openpyxl.load_workbook(SOURCE, data_only=True)
ws = wb[SHEET_NAME]

rows = []
excluded = []
active_excluded_log = []
for rn in range(2, ws.max_row + 1):
    account = clean(ws.cell(row=rn, column=2).value)
    if account not in EXPECTED_FEES:
        continue
    name = clean(ws.cell(row=rn, column=5).value)
    region = clean(ws.cell(row=rn, column=1).value)
    vehicle = clean(ws.cell(row=rn, column=4).value)
    if not name:
        excluded.append({
            'source_row': rn,
            'region': region,
            'account_type': account,
            'vehicle_number': vehicle,
            'name': '',
            'current_arrears': 0,
            'reason': '성명 공란 — 회원마스터 안전매칭 불가',
        })
        continue

    carry = amount(ws.cell(row=rn, column=7).value) or 0
    months = []
    for m in range(1, 9):
        base = 8 + (m - 1) * 4  # 1~8월: 부과/입금/입금날짜/미수금 4열 세트
        billed = amount(ws.cell(row=rn, column=base).value)
        payment = amount(ws.cell(row=rn, column=base + 1).value)
        pay_date = excel_date(ws.cell(row=rn, column=base + 2).value)
        arrears = amount(ws.cell(row=rn, column=base + 3).value)
        months.append({'month': m, 'billed_total': billed, 'payment': payment, 'payment_date': pay_date, 'arrears': arrears})
    for m in range(9, 13):
        months.append({'month': m, 'billed_total': None, 'payment': None, 'payment_date': '', 'arrears': None})

    add_fixed_fee_metadata(months, account, carry)

    # current_arrears: 8월까지 중 값이 채워진 가장 마지막 달의 미수금을 그대로 사용
    # (9월치는 baseline에 넣지 않는다 — 위 DATA_THROUGH_MONTH 설명 참고)
    current = None
    for m in months[:8]:
        if m.get('arrears') is not None:
            current = m['arrears']

    key = (norm_name(name), norm_vehicle(vehicle))
    excl_reason = ACTIVE_EXCLUDE.get(key)
    if excl_reason:
        active_excluded_log.append({'source_row': rn, 'name': name, 'vehicle_number': vehicle, 'reason': excl_reason})

    rows.append({
        'source_row': rn,
        'region': region,
        'account_type': account,
        'legacy_note': clean(ws.cell(row=rn, column=3).value),
        'vehicle_number': vehicle,
        'name': name,
        'vehicle_count': 1,
        'unit_fee': EXPECTED_FEES[account],
        'carryover': carry,
        'months': months,
        'last_month': DATA_THROUGH_MONTH,
        'current_arrears': int(current or 0),
        'active_exclude': bool(excl_reason),
        'active_exclude_reason': excl_reason or None,
    })

summary = {
    'arrears_members': 0, 'arrears_total': 0,
    'prepaid_members': 0, 'prepaid_total': 0,
    'settled_members': 0, 'net_balance': 0,
    'by_account': {}, 'excluded_rows': len(excluded),
}
by = defaultdict(lambda: {'count': 0, 'arrears_members': 0, 'arrears_total': 0, 'prepaid_members': 0, 'prepaid_total': 0})
for r in rows:
    bal = int(r['current_arrears'])
    a = r['account_type']
    by[a]['count'] += 1
    summary['net_balance'] += bal
    if bal > 0:
        summary['arrears_members'] += 1
        summary['arrears_total'] += bal
        by[a]['arrears_members'] += 1
        by[a]['arrears_total'] += bal
    elif bal < 0:
        summary['prepaid_members'] += 1
        summary['prepaid_total'] += -bal
        by[a]['prepaid_members'] += 1
        by[a]['prepaid_total'] += -bal
    else:
        summary['settled_members'] += 1
summary['by_account'] = {k: by[k] for k in ('협회비', '관리비', '70세')}

source_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
snapshot_sha = hashlib.sha256((source_sha + f':v2026-09-06:through{DATA_THROUGH_MONTH}').encode()).hexdigest()
payload = {
    'year': 2026,
    'data_through_month': DATA_THROUGH_MONTH,
    'source_filename': SOURCE.name,
    'source_sha256': snapshot_sha,
    'source_file_sha256': source_sha,
    'snapshot_label': f'2026-09-06 최신 [사용]2026미수금 원장 재반영 · 1~{DATA_THROUGH_MONTH}월 baseline(9월은 프로그램 자동부과가 처리) · 원장원본값 그대로 사용',
    'monthly_fee_rules': EXPECTED_FEES,
    'count': len(rows),
    'summary': summary,
    'manual_corrections': [],
    'active_exclude_rows': active_excluded_log,
    'excluded_rows': excluded,
    'rows': rows,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print('written', OUT)
print('count', len(rows), 'excluded', len(excluded))
print(json.dumps(summary, ensure_ascii=False, indent=2))
print('active_exclude_rows', active_excluded_log)
print('snapshot_sha', snapshot_sha)

# --- 검증: 사용자가 확인해 달라고 한 두 건. baseline(8월) + 9월 자동부과 예상액이
# 원장의 9월 미수금 컬럼과 실제로 일치하는지 대조한다 ---
check_targets = [('설악안전서비스', '2297', 70000), ('강성준', '1012', 510000), ('강성준', '1007', 10000)]
for nm, tail, expected_sept in check_targets:
    hits = [r for r in rows if nm in r['name'] and tail in norm_vehicle(r['vehicle_number'])]
    for h in hits:
        expected_after_sept_autocharge = h['current_arrears'] + EXPECTED_FEES[h['account_type']]
        ok = '일치' if expected_after_sept_autocharge == expected_sept else f"불일치(기대 9월값={expected_sept})"
        print('CHECK', h['name'], h['vehicle_number'], 'baseline(8월)=', h['current_arrears'],
              '+9월자동부과 후 예상=', expected_after_sept_autocharge, ok)
