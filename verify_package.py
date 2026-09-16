from __future__ import annotations
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parent
payload = json.loads((root / "app" / "data" / "receivables_hotfix_20260916.json").read_text(encoding="utf-8"))
rows = payload.get("corrections") or []
excluded = [r for r in rows if r.get("disable_billing")]
mgmts = [r.get("management_number") for r in rows if r.get("management_number")]

assert payload.get("patch_id") == "receivables_hotfix_20260916_v2"
assert len(rows) == 271, len(rows)
assert len(excluded) == 28, len(excluded)
assert len(mgmts) == len(set(mgmts)), "duplicate management_number in correction payload"
assert sum(1 for r in rows if not r.get("management_number")) == 1
assert all(isinstance(r.get("delta"), int) for r in rows)
assert all((int(r.get("target_balance", 0)) == 0) for r in excluded)
print("PACKAGE VERIFY PASS: corrections=271 / certificate exclusions=28 / duplicate mgmt=0")
