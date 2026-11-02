"""3,662건 원본/초안 검증 및 읽기전용 DB 대조. 이 프로그램에는 DB 쓰기 기능이 없다.

python scripts/history_reconcile.py --source SOURCE.xlsx --draft DRAFT.xlsx --out REPORT_DIR
DB 대조: 위 명령에 --connect 추가 (읽기전용 DATABASE_URL 환경변수 사용)
또는 --snapshot ID포함_DB내보내기.jsonl 추가. 원본행과 DB ID는 별개다.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.change_judgement import (
    SOURCE_FIELDS, RULE_VERSION, NORMAL, FIRST, REVIEW, ERROR_FIX, NOTATION, EXCLUDED,
    classify_for_type, source_hash, bucket_of, pick_report_date,
)
from app.services.history_evidence import classify_record

SOURCE_SHA256 = "4ae4ccae17610156c3e9e08814aa2e5c98cc34692e448fb627c5a10088bdcc1e"


def exact_key(row):
    # 주소의 '호', 공백, 날짜, 메모를 제거하지 않는다. NULL/빈 문자열만 동일시한다.
    return tuple(str(row.get(k) or "") for k in SOURCE_FIELDS)


def match_rows(source, database):
    if any(not {"id", *SOURCE_FIELDS, "deleted_at"} <= set(r) for r in database):
        raise ValueError("DB snapshot is missing required original fields")
    active = [r for r in database if not r.get("deleted_at")]
    ids = [r.get("id") for r in active]
    if any(not isinstance(i, int) or isinstance(i, bool) or i <= 0 for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("DB ID must be unique positive integers")
    by_key, source_counts = defaultdict(list), Counter(exact_key(r) for r in source)
    for row in active:
        by_key[exact_key(row)].append(row["id"])
    output = []
    for row in source:
        ids = by_key[exact_key(row)]
        status = ("SOURCE_DUPLICATE" if source_counts[exact_key(row)] > 1 else
                  "MATCH_UNIQUE" if len(ids) == 1 else "MISSING" if not ids else "AMBIGUOUS")
        output.append({"source_row": row["source_row"], "db_id": ids[0] if status == "MATCH_UNIQUE" else None,
                       "match_status": status, "db_match_count": len(ids), "candidate_db_ids": ids,
                       "source_hash": source_hash(row)})
    return output


LINKED_STATUSES = {"MATCH_UNIQUE", "MATCH_CANONICAL"}


def canonical_key(row):
    """알려진 유형 별칭·유효 날짜 표기·차량번호 공백/'호'만 정규화한다.
    주소/상호/메모/변경 전후 내용은 글자 하나도 삭제하지 않는다.
    """
    values = list(exact_key(row))
    aliases = {"차량번호변경": "번호변경", "주소변경": "주소지변경", "자격재교부": "자격증재교부"}
    values[0] = aliases.get(values[0], values[0])
    index = SOURCE_FIELDS.index("vehicle_number")
    plate = re.sub(r"\s+", "", values[index])
    if re.fullmatch(r"(?:[가-힣]{2})?\d{2,3}[가-힣]\d{4}호?", plate):
        values[index] = plate.removesuffix("호")
    from app.services.change_judgement import parse_ymd
    for name in ("change_date", "receipt_date"):
        index = SOURCE_FIELDS.index(name)
        date = parse_ymd(values[index])
        if date: values[index] = "%04d-%02d-%02d" % date
    return tuple(values)


def match_rows_semantic(source, database):
    output = match_rows(source, database)
    active = [r for r in database if not r.get("deleted_at")]
    candidates = defaultdict(list)
    source_counts = Counter(canonical_key(r) for r in source)
    by_id = {r["id"]: r for r in active}
    for r in active: candidates[canonical_key(r)].append(r["id"])
    claimed = {m["db_id"] for m in output if m["match_status"] == "MATCH_UNIQUE"}
    for r, m in zip(source, output):
        m["normalizations_applied"] = []
        if m["match_status"] == "MISSING":
            ids = candidates[canonical_key(r)]
            if ids:
                m["candidate_db_ids"], m["db_match_count"] = ids, len(ids)
                if source_counts[canonical_key(r)] != 1:
                    m["match_status"] = "SOURCE_CANONICAL_DUPLICATE"
                elif len(ids) != 1 or ids[0] in claimed:
                    m["match_status"] = "CANONICAL_AMBIGUOUS"
                else:
                    m["match_status"], m["db_id"] = "MATCH_CANONICAL", ids[0]
                    m["normalizations_applied"] = [k for k in SOURCE_FIELDS if str(r.get(k) or "") != str(by_id[ids[0]].get(k) or "")]
                    claimed.add(ids[0])
        m["expected_db_source_hash"] = source_hash(by_id[m["db_id"]]) if m["match_status"] in LINKED_STATUSES else None
    return output


def read_database(url):
    # app.database/app.main은 import하지 않는다(시작 시 자동 DDL/보정 차단).
    from sqlalchemy import create_engine, MetaData, Table, select
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql+psycopg://"):
        url = url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = '60s'")
            elif engine.dialect.name == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = ON")
            else:
                raise ValueError("Unsupported DB dialect")
            table = Table("change_history", MetaData(), autoload_with=conn)
            fields = ["id", *SOURCE_FIELDS, "deleted_at"]
            if not set(fields) <= set(table.c.keys()):
                raise ValueError("Required columns missing")
            rows = [dict(r) for r in conn.execute(select(*(table.c[k] for k in fields))).mappings()]
            conn.rollback()
            return rows
    finally:
        engine.dispose()


def read_workbooks(source_path, draft_path):
    from openpyxl import load_workbook
    if hashlib.sha256(Path(source_path).read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Source SHA256 mismatch: fixed ordinal rules must be reviewed for this source")
    wb = load_workbook(source_path, read_only=True, data_only=True)
    raw = list(wb.active.values); wb.close()
    if len(raw) != 3663 or len(raw[0]) != 13:
        raise ValueError("Expected 3662 source rows / 13 original columns")
    rows = [{**dict(zip(raw[0], r)), "source_row": i + 2} for i, r in enumerate(raw[1:])]
    wb = load_workbook(draft_path, read_only=True, data_only=True)
    draft = list(wb.worksheets[0].values); wb.close()
    if len(draft) != 3663 or len({r[0] for r in draft[1:]}) != 3662:
        raise ValueError("Draft row count or row keys differ")
    for r in draft[1:]:
        if not isinstance(r[0], int) or not 2 <= r[0] <= 3663 or list(r[1:14]) != list(raw[r[0] - 1]):
            raise ValueError("Draft originals do not match source")
    return rows, {r[0]: dict(zip(draft[0], r)) for r in draft[1:]}


def review_rows(rows, drafts, policy="evidence-v2"):
    type_ordinals = defaultdict(list)
    for r in rows:
        type_ordinals[r["change_type"]].append(r)
    # 이 순번은 원본 SHA256이 일치할 때만 사용. 이름으로 일괄 제외하지 않는다.
    overrides = {}
    for n in (44, 49, 56, 61, 71, 199, 201):
        r = type_ordinals["구조변경"][n - 1]
        overrides[r["source_row"]] = (EXCLUDED, f"현재 사용자 확정: 구조변경 유형 내 {n}번 실적 제외")
    for n, name, ct, j in ((2394, "어인선", "상호변경", EXCLUDED),
                           (2938, "변해용", "상호변경", EXCLUDED),
                           (3597, "유성종", "차량번호변경", ERROR_FIX),
                           (3646, "우정훈", "차량번호변경", ERROR_FIX),
                           (3647, "우정훈", "차량번호변경", ERROR_FIX),
                           (3663, "허우진", "차량번호변경", NOTATION)):
        r = rows[n - 2]
        if r["name"] != name or r["change_type"] != ct:
            raise ValueError("Named override fingerprint mismatch")
        overrides[n] = (j, "현재 사용자 확정: 지정된 과거 기록 실적 제외")
    transfer_candidates = {}
    for n, name, canonical in ((21, "최철수", 1604), (25, "최공남", 1756)):
        r = type_ordinals["등록이관"][n - 1]
        if r["name"] != name:
            raise ValueError("Transfer ordinal mismatch")
        transfer_candidates[r["source_row"]] = canonical
    exact_groups, repeated_groups, event_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    def event_key(r):
        date, _ = pick_report_date(r["change_date"], r["receipt_date"])
        plate = re.sub(r"\s|호$", "", str(r["vehicle_number"] or "")).removeprefix("강원")
        return (r["name"], plate, date) if r["name"] and plate and date else None
    for r in rows:
        exact_groups[exact_key(r)].append(r["source_row"])
        repeated_groups[tuple(r.get(k) or "" for k in ("change_type", "name", "vehicle_number", "before_value", "after_value"))].append(r["source_row"])
        if event_key(r): event_groups[event_key(r)].append(r)
    result = []
    for r in rows:
        j, why = classify_for_type(r["change_type"], r["before_value"], r["after_value"])
        status, report = ("pending", "hold") if j == REVIEW else ("automatic", "include" if j == NORMAL else "exclude")
        proposed = classify_record(r) if policy == "evidence-v2" else None
        if proposed:
            j, why, report = proposed.judgement, proposed.reason, proposed.report_decision
            status = "pending" if report == "hold" else "automatic"
        if r["change_type"] == "말소/폐차" and not proposed:
            why = "폐업 관련 유형 유지. 번호변경말소인지 실제 사업폐업인지 확인 후 중복 검증"
        if r["source_row"] in overrides:
            j, why = overrides[r["source_row"]]; status, report = "confirmed", "exclude"
        if r["source_row"] in transfer_candidates:
            j, status, report, why = REVIEW, "pending", "hold", "사용자 지정 중복 검토 대상. 날짜가 달라 동일 사건인지 추가 확인 필요"
        group = exact_groups[exact_key(r)]
        empty_record = not any(r.get(k) for k in SOURCE_FIELDS if k != "change_type")
        if empty_record:
            j, status, report, why = EXCLUDED, "automatic", "exclude", "변경유형 외 업무 내용·대상·일자 모두 없는 빈 기록. 원본 보존, 실적 제외"
        elif len(group) > 1 and r["source_row"] not in overrides:
            j, status, report, why = REVIEW, "pending", "hold", "동일 원본 항목 중복 후보. 각 DB ID 및 별도 업무 여부 확인 필요"
        date, date_source = pick_report_date(r["change_date"], r["receipt_date"])
        if not date and report == "include":
            j, status, report, why = REVIEW, "pending", "hold", "업무일자 유효성 확인 필요"
        d = drafts[r["source_row"]]
        related = event_groups.get(event_key(r), [])
        cross = ([x["source_row"] for x in related] if len({x["change_type"] for x in related}) > 1
                 and any(x["change_type"] in ("등록이관", "이전전출", "말소/폐차", "폐업") for x in related) else [])
        result.append({**r, "proposed_judgement": j, "review_status": status,
            "report_decision": report, "review_reason": why, "report_bucket": bucket_of(r["change_type"]),
            "basis_date": "%04d-%02d-%02d" % date if date else None, "date_source": date_source,
            "source_hash": source_hash(r), "rule_version": RULE_VERSION,
            "automation_rule": "USER_CONFIRMED_EXCLUSION" if r["source_row"] in overrides else proposed.rule if proposed else "CONSERVATIVE_V1",
            "work_queue": "DB_DUPLICATE" if r["source_row"] in transfer_candidates else "AUTO" if r["source_row"] in overrides or empty_record else proposed.queue if proposed else "UNCLASSIFIED",
            "automation_evidence": proposed.evidence if proposed else {},
            "empty_record": empty_record,
            "cross_type_event_source_rows": cross,
            "exact_duplicate_source_rows": group if len(group) > 1 and not empty_record else [],
            "same_content_source_rows": repeated_groups[tuple(r.get(k) or "" for k in ("change_type", "name", "vehicle_number", "before_value", "after_value"))],
            "candidate_duplicate_of_source_row": transfer_candidates.get(r["source_row"]),
            "prior_draft_judgement": d["판정상태"], "prior_draft_reason": d["근거"],
            "prior_assertion_unverified": str(d["판정상태"]).startswith("사용자 확인") and r["source_row"] not in overrides})
    return result


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader()
        for row in rows:
            # Excel 수식 실행 방지. JSONL은 원본을 그대로 보존한다.
            writer.writerow({k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else
                                 json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in row.items()})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True); p.add_argument("--draft", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--policy", choices=("conservative-v1", "evidence-v2"), default="evidence-v2")
    db = p.add_mutually_exclusive_group(); db.add_argument("--connect", action="store_true"); db.add_argument("--snapshot")
    a = p.parse_args()
    rows, drafts = read_workbooks(a.source, a.draft)
    plan = review_rows(rows, drafts, a.policy)
    db_rows = (read_database(os.environ["DATABASE_URL"]) if a.connect else
               [json.loads(x) for x in Path(a.snapshot).read_text(encoding="utf-8-sig").splitlines() if x.strip()] if a.snapshot else None)
    matches = (match_rows(rows, db_rows) if db_rows is not None else
               [{"source_row": r["source_row"], "db_id": None, "match_status": "NOT_CONNECTED", "db_match_count": None,
                 "candidate_db_ids": [], "source_hash": source_hash(r)} for r in rows])
    for r, m in zip(plan, matches):
        r.update({k: v for k, v in m.items() if k != "source_row"})
        r["ready_for_db_review"] = m["match_status"] == "MATCH_UNIQUE"
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "review_plan.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in plan), encoding="utf-8")
    write_csv(out / "reconciliation.csv", plan)
    before, after, pending = Counter(), Counter(), Counter()
    for r in plan:
        if not r["basis_date"]:
            continue
        month = r["basis_date"][:7]; bucket = r["report_bucket"]
        # 기존 코드 재현: NULL 판정은 정상 취급, 구조변경은 대폐차에 합산.
        if not r["judgement"] or r["judgement"] == NORMAL:
            old_bucket = "차량변경(대폐차)" if bucket == "구조변경" else bucket
            before[month, old_bucket] += 1
        if r["report_decision"] == "include": after[month, bucket] += 1
        elif r["report_decision"] == "hold": pending[month, bucket] += 1
    keys = sorted(set(before) | set(after) | set(pending))
    write_csv(out / "monthly_comparison.csv", [{"month": m, "bucket": b, "source_old_rule": before[m,b],
        "proposed_include": after[m,b], "proposed_hold": pending[m,b], "delta": after[m,b] - before[m,b],
        "scope": "SOURCE_ONLY_NOT_PRODUCTION"} for m,b in keys])
    summary = {"source_rows": len(rows), "source_sha256": SOURCE_SHA256, "draft_original_mismatches": 0,
        "read_only": True, "production_db_connected": bool(a.connect), "snapshot_used": bool(a.snapshot),
        "db_match_counts": dict(Counter(m["match_status"] for m in matches)),
        "proposed_report_counts": dict(Counter(r["report_decision"] for r in plan)),
        "proposed_status_counts": dict(Counter(r["review_status"] for r in plan)),
        "confirmed_exclusions": sum(r["review_status"] == "confirmed" for r in plan),
        "empty_source_records": sum(r["empty_record"] for r in plan),
        "invalid_dates": sum(not r["basis_date"] for r in plan),
        "exact_duplicate_groups": len({tuple(r["exact_duplicate_source_rows"]) for r in plan if r["exact_duplicate_source_rows"]}),
        "cross_type_event_candidate_groups": len({tuple(r["cross_type_event_source_rows"]) for r in plan if r["cross_type_event_source_rows"]}),
        "source_old_rule_total": sum(before.values()), "proposed_included_total": sum(after.values()),
        "db_write_count": 0, "rule_version": RULE_VERSION}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SQL/접속오류 본문에 접속 문자열이 포함될 수 있어 오류 종류만 반환한다.
        print(f"Stopped safely: {type(exc).__name__}. No database writes performed.", file=sys.stderr)
        sys.exit(1)
