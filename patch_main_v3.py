from __future__ import annotations
import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: python patch_main_v3.py <path-to-app/main.py>")

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

import_line = (
    "from app.receivables_reconcile_20260916_v3 import (\n"
    "    apply_receivables_reconcile_20260916_v3,\n"
    "    get_receivables_reconcile_v3_status,\n"
    ")\n"
)
if "apply_receivables_reconcile_20260916_v3" not in text:
    anchor = "import app.receivables_models as _receivables_models\n"
    if anchor not in text:
        raise SystemExit("ERROR: receivables import anchor not found")
    text = text.replace(anchor, anchor + import_line, 1)

job = '            ("2026-09-16 수납미수금 원본 재대조 v3", apply_receivables_reconcile_20260916_v3),\n'
if "수납미수금 원본 재대조 v3" not in text:
    anchor = '            ("2026-09-16 수납미수금 최종원장 보정", apply_receivables_hotfix_20260916),\n'
    if anchor not in text:
        raise SystemExit("ERROR: v2 maintenance job not found; v3 must be applied on top of v2")
    text = text.replace(anchor, anchor + job, 1)

route = '''\n@app.get("/health/receivables-reconcile-20260916-v3")
async def receivables_reconcile_20260916_v3_health():
    return get_receivables_reconcile_v3_status()

'''
if "/health/receivables-reconcile-20260916-v3" not in text:
    anchor = '@app.get("/health")\n'
    if anchor not in text:
        raise SystemExit("ERROR: health route anchor not found")
    text = text.replace(anchor, route + anchor, 1)

path.write_text(text, encoding="utf-8")
print(f"PATCHED V3: {path}")
