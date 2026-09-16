from __future__ import annotations
import pathlib, sys

if len(sys.argv) != 2:
    raise SystemExit("usage: python patch_main.py <path-to-app/main.py>")

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

import_line = "from app.receivables_hotfix_20260916 import apply_receivables_hotfix_20260916\n"
if import_line not in text:
    anchor = "import app.receivables_models as _receivables_models\n"
    if anchor not in text:
        raise SystemExit("ERROR: main.py import anchor not found; no changes made")
    text = text.replace(anchor, anchor + import_line, 1)

job_line = '            ("2026-09-16 수납미수금 최종원장 보정", apply_receivables_hotfix_20260916),\n'
if job_line not in text:
    anchor = '            ("수납/미수금 인덱스", _ensure_receivables_indexes),\n'
    if anchor not in text:
        raise SystemExit("ERROR: main.py jobs anchor not found; no changes made")
    # 테이블/컬럼/인덱스가 준비된 직후, 다른 대량 백필보다 먼저 1회 적용.
    text = text.replace(anchor, anchor + job_line, 1)

path.write_text(text, encoding="utf-8")
print(f"PATCHED: {path}")
