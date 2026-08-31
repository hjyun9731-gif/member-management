from __future__ import annotations

import datetime as dt
import hashlib
import json
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

SOURCE = Path('/mnt/data/[사용]2026미수금 (1)(1).xlsx')
OUT = Path(__file__).resolve().parent / 'app' / 'data' / 'legacy_receivables_2026.json'
SHEET_NAME = '2026년회비내역'

EXPECTED_FEES = {'협회비': 10000, '관리비': 5000, '70세': 5000}

# 통장 원본으로 직접 확인된 개별 정정.
# 박종훈(8/13 40,000)과 박재구(8/11 100,000)는 2026년 통장에 실제 입금이 존재하므로
# 원본 [사용]2026미수금 값을 그대로 유지한다.
CORRECTIONS = {
    ('이건우', '80배1634'): {
        'payment': 30000,
        'payment_date': '2026-08-25',
        'arrears': 145000,
        'reason': '8/25 통장 원본 관리비 30,000원 확인. 자격증명 관련 금액이 관리비 선납으로 섞이지 않도록 30,000원만 반영',
    },
}

# 계정 자체가 고정 월회비인데 원본 누적 청구행에서 월회비가 잘못 적용된 것이 명확한 건.
# 이 세 행은 월별 잔액을 고정요율로 다시 계산한다.
RATE_RECALC = {
    ('오병춘', '852058'): '70세 월 5,000원 고정요율 재계산',
    ('정석양', '1351'): '70세 월 5,000원 고정요율 재계산',
    ('이선엽', '80배1616'): '관리비 월 5,000원 고정요율 재계산',
}

NS = {
    'a': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
RNS = {'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}


def col_idx(ref: str) -> int:
    m = re.match(r'([A-Z]+)', ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n


def clean(v) -> str:
    return str(v or '').strip().replace('\u3000', '').strip()


def norm_name(v) -> str:
    return re.sub(r'\s+', '', clean(v))


def norm_vehicle(v) -> str:
    return re.sub(r'[^0-9A-Za-z가-힣]', '', clean(v))


def amount(v):
    s = clean(v).replace(',', '').replace('원', '')
    if not s or re.fullmatch(r'[-#\s]*', s):
        return None
    try:
        return int(round(float(s)))
    except Exception:
        return None


def excel_date(v) -> str:
    s = clean(v)
    if not s:
        return ''
    try:
        n = float(s)
        if 1 <= n <= 100000:
            d = dt.datetime(1899, 12, 30) + dt.timedelta(days=n)
            return d.date().isoformat()
    except Exception:
        pass
    return s


def read_sheet_rows(path: Path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall('a:si', NS):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['a'])))
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relmap = {x.attrib['Id']: x.attrib['Target'] for x in rels.findall('p:Relationship', RNS)}
        sheet = next(s for s in wb.findall('a:sheets/a:sheet', NS) if s.attrib['name'] == SHEET_NAME)
        target = relmap[sheet.attrib['{%s}id' % NS['r']]].lstrip('/')
        if not target.startswith('xl/'):
            target = posixpath.normpath(posixpath.join('xl', target))
        root = ET.fromstring(z.read(target))
        for row in root.findall('.//a:sheetData/a:row', NS):
            rn = int(row.attrib['r'])
            if rn == 1:
                continue
            vals = {}
            for c in row.findall('a:c', NS):
                typ = c.attrib.get('t')
                v = c.find('a:v', NS)
                isel = c.find('a:is', NS)
                val = ''
                if typ == 's' and v is not None:
                    val = shared[int(v.text)]
                elif typ == 'inlineStr' and isel is not None:
                    val = ''.join(t.text or '' for t in isel.iter('{%s}t' % NS['a']))
                elif v is not None:
                    val = v.text or ''
                vals[col_idx(c.attrib['r'])] = val
            yield rn, vals


def month_has_activity(m: dict) -> bool:
    return any(m.get(k) is not None for k in ('billed_total', 'payment', 'arrears')) or bool(m.get('payment_date'))


def add_fixed_fee_metadata(months: list[dict], account: str, carry: int) -> None:
    """Excel '월 부과금'은 누적청구액이다.

    프로그램이 이를 전월 잔액과 단순 차감해 월회비로 표시하면 중간 ####/빈칸 때문에
    20,000/40,000원처럼 보이는 문제가 생긴다. 최초 부과월 이후의 정규 월회비는
    계정별 고정액을 명시적으로 저장하고, 원본 잔액과의 차이는 legacy_adjustment로 분리한다.
    """
    expected = EXPECTED_FEES[account]
    active = [int(m['month']) for m in months[:8] if month_has_activity(m)]
    first_month = min(active) if active else None
    prev_arrears = int(carry or 0)
    for m in months[:8]:
        month_no = int(m['month'])
        regular = expected if first_month is not None and month_no >= first_month else 0
        m['monthly_charge'] = regular
        if m.get('arrears') is not None:
            payment = int(m.get('payment') or 0)
            # 원본 월말잔액을 보존하면서 정규 월회비 외의 조정분을 별도 기록한다.
            m['legacy_adjustment'] = int(m['arrears']) - prev_arrears - regular + payment
            prev_arrears = int(m['arrears'])
        else:
            m['legacy_adjustment'] = None
    for m in months[8:]:
        m['monthly_charge'] = None
        m['legacy_adjustment'] = None


def recalc_fixed_rate(months: list[dict], account: str, carry: int) -> None:
    expected = EXPECTED_FEES[account]
    active = [int(m['month']) for m in months[:8] if month_has_activity(m)]
    first_month = min(active) if active else 1
    running = int(carry or 0)
    for m in months[:8]:
        month_no = int(m['month'])
        if month_no < first_month:
            continue
        payment = int(m.get('payment') or 0)
        billed_total = running + expected
        running = billed_total - payment
        m['monthly_charge'] = expected
        m['billed_total'] = billed_total
        m['arrears'] = running
        m['legacy_adjustment'] = 0


rows = []
excluded = []
correction_log = []
for rn, v in read_sheet_rows(SOURCE):
    account = clean(v.get(2))
    if account not in EXPECTED_FEES:
        continue
    name = clean(v.get(5))
    region = clean(v.get(1))
    vehicle = clean(v.get(4))
    current = amount(v.get(39))
    if not name:
        excluded.append({
            'source_row': rn,
            'region': region,
            'account_type': account,
            'vehicle_number': vehicle,
            'name': '',
            'current_arrears': int(current or 0),
            'reason': '성명 공란 — 회원마스터 안전매칭 불가',
        })
        continue

    carry = amount(v.get(7)) or 0
    months = []
    for m in range(1, 9):
        base = 8 + (m - 1) * 4
        months.append({
            'month': m,
            'billed_total': amount(v.get(base)),
            'payment': amount(v.get(base + 1)),
            'payment_date': excel_date(v.get(base + 2)),
            'arrears': amount(v.get(base + 3)),
        })
    for m in range(9, 13):
        months.append({
            'month': m,
            'billed_total': None,
            'payment': None,
            'payment_date': '',
            'arrears': None,
        })

    add_fixed_fee_metadata(months, account, carry)

    key = (norm_name(name), norm_vehicle(vehicle))

    rate_reason = RATE_RECALC.get(key)
    if rate_reason:
        before_months = [dict(x) for x in months[:8]]
        before_current = int(current or 0)
        recalc_fixed_rate(months, account, carry)
        current = int(months[7].get('arrears') or 0)
        correction_log.append({
            'source_row': rn,
            'name': name,
            'vehicle_number': vehicle,
            'account_type': account,
            'before_current_arrears': before_current,
            'after_current_arrears': current,
            'before_months': before_months,
            'after_months': [dict(x) for x in months[:8]],
            'reason': rate_reason,
        })

    corr = CORRECTIONS.get(key)
    if corr:
        aug = months[7]
        before = dict(aug)
        aug['payment'] = corr['payment']
        aug['payment_date'] = corr['payment_date']
        aug['arrears'] = corr['arrears']
        # 7월말 잔액 + 정규월회비 - 실제 관리비 입금 = 8월말 잔액으로 정합화
        prev = int(months[6].get('arrears') or carry or 0)
        aug['monthly_charge'] = EXPECTED_FEES[account]
        aug['legacy_adjustment'] = int(corr['arrears']) - prev - int(aug['monthly_charge']) + int(corr['payment'] or 0)
        current = corr['arrears']
        correction_log.append({
            'source_row': rn,
            'name': name,
            'vehicle_number': vehicle,
            'account_type': account,
            'before_august': before,
            'after_august': dict(aug),
            'reason': corr['reason'],
        })

    rows.append({
        'source_row': rn,
        'region': region,
        'account_type': account,
        'legacy_note': clean(v.get(3)),
        'vehicle_number': vehicle,
        'name': name,
        # 계정별 월회비는 '사람/계정 1건' 기준 고정액이다. 차량 대수 배수부과 금지.
        'vehicle_count': 1,
        'unit_fee': EXPECTED_FEES[account],
        'carryover': carry,
        'months': months,
        'last_month': 8,
        'current_arrears': int(current or 0),
    })

summary = {
    'arrears_members': 0,
    'arrears_total': 0,
    'prepaid_members': 0,
    'prepaid_total': 0,
    'settled_members': 0,
    'net_balance': 0,
    'by_account': {},
    'excluded_rows': len(excluded),
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
correction_sha = hashlib.sha256(json.dumps(correction_log, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
rule_sha = hashlib.sha256(json.dumps(EXPECTED_FEES, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
snapshot_sha = hashlib.sha256((source_sha + ':' + correction_sha + ':' + rule_sha).encode()).hexdigest()
payload = {
    'year': 2026,
    'data_through_month': 8,
    'source_filename': SOURCE.name,
    'source_sha256': snapshot_sha,
    'source_file_sha256': source_sha,
    'snapshot_label': '2026-08 최신 미수금 원장 · 고정월회비/통장검증/매칭보강 2026-08-31',
    'monthly_fee_rules': EXPECTED_FEES,
    'count': len(rows),
    'summary': summary,
    'manual_corrections': correction_log,
    'excluded_rows': excluded,
    'rows': rows,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print('written', OUT)
print('count', len(rows), 'excluded', len(excluded))
print(json.dumps(summary, ensure_ascii=False, indent=2))
print('corrections', len(correction_log))
for x in correction_log:
    print(x['name'], x['vehicle_number'], x['reason'])
print('snapshot_sha', snapshot_sha)
