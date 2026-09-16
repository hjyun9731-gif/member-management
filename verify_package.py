import json
import pathlib
root = pathlib.Path(__file__).parent
p = json.loads((root/'app'/'data'/'receivables_reconcile_20260916_v3.json').read_text(encoding='utf-8'))
rows = p['corrections']
assert p['patch_id'] == 'receivables_reconcile_20260916_v3'
assert len(rows) == 269, len(rows)
assert len({r['match_hash'] for r in rows}) == 269
assert all(len(r['match_hash']) == 64 for r in rows)
assert sum(int(r['delta']) for r in rows) == int(p['delta_total']) == 1038000
print('V3 PACKAGE VERIFY PASS: corrections=269 / delta_total=1,038,000 / hashed matching')
