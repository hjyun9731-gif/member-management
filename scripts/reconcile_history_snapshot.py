"""원본·읽기전용 스냅샷 대조와 운영 DB 기반 집계 전후 '예상' 비교.

DB 쓰기/앱 startup import 없음. --snapshot이 없으면 원본 미리보기만 만들며
실제 DB 대조 결과를 0건으로 꾸미지 않고 미수신으로 표시한다.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.history_reconcile import read_workbooks, review_rows, match_rows_semantic, LINKED_STATUSES, write_csv
from app.services.change_judgement import NORMAL, REVIEW, pick_report_date, source_hash, view_row

CORE = ("상호변경", "대표자변경", "차량변경", "구조변경", "주소변경", "자격증재교부", "이전전출",
        "등록이관", "차량번호변경", "말소/폐차", "전속업체변경", "_변경등록전체")


def counters(rows, legacy=False, reviews=None, decisions=None):
    by_month = defaultdict(Counter)
    for r in rows:
        if r.get("deleted_at"):
            continue
        date, _ = pick_report_date(r.get("change_date"), r.get("receipt_date"))
        if not date:
            continue
        if legacy:
            if r.get("judgement") not in (None, "", NORMAL) and r.get("judgement") in ("최초등록", "표기정정", "오류정정", REVIEW):
                continue
        else:
            latest = (decisions or {}).get(r["id"])
            v = view_row(SimpleNamespace(**{**r, "decisions": [SimpleNamespace(**latest)] if latest else []}))
            included = v.report_decision == "include" and v.judgement == NORMAL
            proposed = (reviews or {}).get(r["id"])
            # 저장된 명시 판정은 새 자동안으로 덮지 않는다.
            if proposed and not latest:
                included = proposed["report_decision"] == "include"
            if not included:
                continue
        month = "%04d-%02d" % date[:2]
        by_month[month][r.get("change_type") or "기타"] += 1
    return by_month


def admin_metrics(by_type, legacy=False):
    t = by_type
    return {"상호변경": t["상호변경"] + t["전속계약 업체변경"], "대표자변경": t["대표자변경"],
        "차량변경": t["대폐차"] + t["차종변경"] + (t["구조변경"] if legacy else 0),
        "구조변경": None if legacy else t["구조변경"], "주소변경": t["주소지변경"] + t["주소변경"],
        "자격증재교부": (t["자격증재교부"] or t["자격재교부"]),
        "이전전출": t["이전전출"] + (t["등록이관"] if legacy else 0),
        "등록이관": None if legacy else t["등록이관"],
        "차량번호변경": t["차량번호변경"] + t["번호변경"], "말소/폐차": t["말소/폐차"],
        "전속업체변경": t["전속계약 업체변경"], "_변경등록전체": sum(t.values())}


def compare_months(before, code_after, review_after, scope):
    output = []
    for month in sorted(set(before) | set(code_after) | set(review_after)):
        b, c, a = admin_metrics(before[month], True), admin_metrics(code_after[month]), admin_metrics(review_after[month])
        for key in CORE:
            output.append({"month": month, "metric": key, "before_main_rule": b[key],
                "after_code_rule": c[key], "after_review_proposal": a[key],
                "delta": None if b[key] is None else a[key] - b[key], "scope": scope,
                "note": "구버전 별도 항목 없음" if b[key] is None else "실제 반영 전 예상 비교"})
    return output


def enrich_sources(source, matches, db_rows):
    by_id = {r["id"]: r for r in db_rows}
    enriched = []
    for r, m in zip(source, matches):
        # ID가 유일하게 연결된 경우에만 DB 원본 열을 자동판정 근거로 사용한다.
        raw = by_id[m["db_id"]].get("raw_data", {}) if m["match_status"] in LINKED_STATUSES else {}
        enriched.append({**r, "raw_data": raw})
    return enriched


def build(source, drafts, snapshot=None):
    before_plan = review_rows(source, drafts, "conservative-v1")
    baseline_holds = {r["source_row"] for r in before_plan if r["report_decision"] == "hold"}
    db_rows, matches = [], None
    if snapshot is not None:
        if snapshot.get("format_version") != 2 or not snapshot.get("read_only_verified"):
            raise ValueError("읽기전용 내보내기 형식/확인값이 일치하지 않습니다.")
        db_rows = snapshot["tables"]["change_history"]
        matches = match_rows_semantic(source, db_rows)
        source = enrich_sources(source, matches, db_rows)
    plan = review_rows(source, drafts, "evidence-v2")
    old_by_row = {r["source_row"]: r for r in before_plan}
    for r in plan:
        r["previous_report_decision"] = old_by_row[r["source_row"]]["report_decision"]
        r["previous_review_reason"] = old_by_row[r["source_row"]]["review_reason"]
    transitions = []
    for ct in dict.fromkeys(r["change_type"] for r in before_plan if r["source_row"] in baseline_holds):
        rows = [r for r in plan if r["change_type"] == ct and r["source_row"] in baseline_holds]
        c = Counter(r["report_decision"] for r in rows)
        transitions.append({"change_type": ct, "previous_hold": len(rows), "auto_include": c["include"],
            "auto_exclude": c["exclude"], "remaining_hold": c["hold"],
            "queues": dict(Counter(r["work_queue"] for r in rows if r["report_decision"] == "hold"))})
    source_before = counters([{**r, "id": r["source_row"]} for r in source], legacy=True)
    source_after = defaultdict(Counter)
    for r in plan:
        if r["basis_date"] and r["report_decision"] == "include":
            source_after[r["basis_date"][:7]][r["change_type"]] += 1
    source_code = counters([{**r, "id": r["source_row"]} for r in source])
    source_compare = compare_months(source_before, source_code, source_after, "SOURCE_PREVIEW_NOT_PRODUCTION")
    actual_compare, saved_compare, db_extra, conflicts = [], [], [], []
    if snapshot is not None:
        for r, m in zip(plan, matches):
            r.update({k: v for k, v in m.items() if k != "source_row"})
            r["db_ready"] = m["match_status"] in LINKED_STATUSES
        reviews = {r["db_id"]: r for r in plan if r["match_status"] in LINKED_STATUSES}
        latest = {}
        for d in snapshot["tables"].get("change_history_decisions", []):
            if d["history_id"] not in latest or d["id"] > latest[d["history_id"]]["id"]:
                latest[d["history_id"]] = d
        for db_id, proposal in reviews.items():
            if db_id in latest:
                conflicts.append({"db_id": db_id, "source_row": proposal["source_row"], "existing_decision_id": latest[db_id]["id"],
                    "note": "기존 판정은 자동 덮어쓰기하지 않음"})
        before = counters(db_rows, legacy=True)
        code_after = counters(db_rows, decisions=latest)
        after = counters(db_rows, reviews=reviews, decisions=latest)
        actual_compare = compare_months(before, code_after, after, "LIVE_DB_SNAPSHOT_WHAT_IF_NOT_APPLIED")
        for entry in snapshot["tables"].get("monthly_report_entries", []):
            month = f"{entry['year']:04d}-{entry['month']:02d}"
            bm, am = admin_metrics(before[month], True), admin_metrics(after[month])
            for f in snapshot["tables"].get("report_field_defs", []):
                path = str(f.get("auto_path") or "")
                key = path.removeprefix("admin_work.")
                if not path.startswith("admin_work.") or key not in CORE:
                    continue
                manual = entry.get("custom_data") or {}
                saved_compare.append({"month": month, "field_key": f["key"], "label": f.get("label"),
                    "before_calculated": bm[key], "saved_override": manual.get(f["key"]),
                    "displayed_before": manual[f["key"]] if f["key"] in manual else bm[key],
                    "displayed_after_proposal": am[key], "scope": "LIVE_DB_SNAPSHOT_WHAT_IF_NOT_APPLIED"})
        matched = set(reviews)
        db_extra = [{"db_id": r["id"], "change_type": r.get("change_type"), "deleted": bool(r.get("deleted_at"))}
                    for r in db_rows if r["id"] not in matched]
    else:
        for r in plan:
            r.update(db_id=None, match_status="NOT_CONNECTED", db_match_count=None, candidate_db_ids=[], db_ready=False)
    remaining = sum(r["report_decision"] == "hold" and r["source_row"] in baseline_holds for r in plan)
    summary = {"source_rows": len(source), "baseline_hold": len(baseline_holds),
        "resolved_previous_hold": len(baseline_holds) - remaining, "remaining_previous_hold": remaining,
        "resolved_ratio": (len(baseline_holds) - remaining) / len(baseline_holds),
        "final_decisions": dict(Counter(r["report_decision"] for r in plan)),
        "remaining_queues": dict(Counter(r["work_queue"] for r in plan if r["report_decision"] == "hold")),
        "actual_db_status": "SNAPSHOT_RECEIVED" if snapshot is not None else "AWAITING_READONLY_EXPORT",
        "db_rows": len(db_rows) if snapshot is not None else None,
        "db_matches": dict(Counter(r["match_status"] for r in plan)),
        "db_writes": 0, "applied": False}
    return {"summary": summary, "plan": plan, "hold_transitions": transitions, "source_monthly": source_compare,
        "db_monthly": actual_compare, "saved_report_comparison": saved_compare, "unmatched_db": db_extra,
        "existing_decision_conflicts": conflicts}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True); p.add_argument("--draft", required=True)
    p.add_argument("--snapshot"); p.add_argument("--out", required=True)
    a = p.parse_args()
    source, drafts = read_workbooks(a.source, a.draft)
    snapshot = json.loads(Path(a.snapshot).read_text(encoding="utf-8-sig")) if a.snapshot else None
    result = build(source, drafts, snapshot)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for key in ("hold_transitions", "source_monthly", "db_monthly", "saved_report_comparison", "unmatched_db", "existing_decision_conflicts"):
        path = out / (key + ".csv")
        if result[key]: write_csv(path, result[key])
        else: path.write_text("", encoding="utf-8")  # 이전 실행의 결과를 남기지 않는다.
    write_csv(out / "reconciliation.csv", result["plan"])
    (out / "review_plan.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in result["plan"]), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(result["summary"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "analysis_tables.json").write_text(json.dumps({k: v for k, v in result.items() if k != "plan"},
                                                        ensure_ascii=False, indent=2), encoding="utf-8")
    actual = {"status": result["summary"]["actual_db_status"], "db_rows": result["summary"]["db_rows"],
        "matches": result["summary"]["db_matches"] if snapshot is not None else None,
        "monthly_comparison_rows": len(result["db_monthly"]) if snapshot is not None else None,
        "snapshot_sha256": hashlib.sha256(Path(a.snapshot).read_bytes()).hexdigest() if a.snapshot else None,
        "applied": False, "note": "스냅샷 재계산·반영 전 예상 비교" if snapshot is not None else "실제 운영 DB 자료 미수신. 원본 미리보기와 구분."}
    (out / "actual_db_result.json").write_text(json.dumps(actual, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Stopped safely: {type(exc).__name__}. DB writes: 0.", file=sys.stderr)
        sys.exit(1)
