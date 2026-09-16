from __future__ import annotations
import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: python patch_main.py <path-to-app/main.py>")

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

# 이전 v1 패키지를 아직 커밋하지 않았거나 이미 일부 적용한 경우에도 v2로 정리한다.
old_import = "from app.receivables_hotfix_20260916 import apply_receivables_hotfix_20260916\n"
text = text.replace(old_import, "")
old_job = '            ("2026-09-16 수납미수금 최종원장 보정", apply_receivables_hotfix_20260916),\n'
text = text.replace(old_job, "")

import_line = (
    "from app.receivables_hotfix_20260916 import (\n"
    "    ensure_receivables_billing_exclusion_guards,\n"
    "    apply_receivables_hotfix_20260916,\n"
    "    get_receivables_hotfix_status,\n"
    ")\n"
)
if "get_receivables_hotfix_status" not in text:
    anchor = "import app.receivables_models as _receivables_models\n"
    if anchor not in text:
        raise SystemExit("ERROR: main.py receivables import anchor not found; no changes written")
    text = text.replace(anchor, anchor + import_line, 1)

jobs_block = (
    '            ("자격증명 미발급 부과제외 보호장치", ensure_receivables_billing_exclusion_guards),\n'
    '            ("2026-09-16 수납미수금 최종원장 보정", apply_receivables_hotfix_20260916),\n'
)
if "자격증명 미발급 부과제외 보호장치" not in text:
    anchor = '            ("수납/미수금 인덱스", _ensure_receivables_indexes),\n'
    if anchor not in text:
        raise SystemExit("ERROR: main.py maintenance job anchor not found; no changes written")
    text = text.replace(anchor, anchor + jobs_block, 1)

route_block = '''\n@app.get("/health/receivables-hotfix-20260916")\nasync def receivables_hotfix_20260916_health():\n    return get_receivables_hotfix_status()\n\n'''
if "/health/receivables-hotfix-20260916" not in text:
    anchor = '@app.get("/health")\n'
    if anchor not in text:
        raise SystemExit("ERROR: main.py health route anchor not found; no changes written")
    text = text.replace(anchor, route_block + anchor, 1)

path.write_text(text, encoding="utf-8")
print(f"PATCHED: {path}")
