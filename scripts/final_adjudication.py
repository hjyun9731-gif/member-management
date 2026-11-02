"""운영 DB 읽기전용 스냅샷 + 원본행↔DB ID 대응표로 3,662건 최종 자동판정과 월례보고 전후 비교를 만든다.

DB 접속 없음, 쓰기 없음. 입력은 export_history_readonly.py가 만든 JSON과 대응표 xlsx뿐이다.
python scripts/final_adjudication.py --snapshot S.json --mapping M.xlsx --old-judgement OLD.py --out OUTDIR
"""
import argparse, collections, hashlib, importlib.util, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.change_judgement import (NORMAL, FIRST, NOTATION, ERROR_FIX, REVIEW, EXCLUDED, DUPLICATE,
    bucket_of, pick_report_date, source_hash, _k)
from app.services.history_evidence import classify_record

STRUCT_ORDINALS = {44: 1908, 49: 1960, 56: 1998, 61: 2022, 71: 2090, 199: 3440, 201: 3465}
NAMED = {2394: ("어인선", "상호변경", EXCLUDED), 2938: ("변해용", "상호변경", EXCLUDED),
         3597: ("유성종", "차량번호변경", ERROR_FIX), 3646: ("우정훈", "차량번호변경", ERROR_FIX),
         3647: ("우정훈", "차량번호변경", ERROR_FIX), 3663: ("허우진", "차량번호변경", NOTATION)}
DUP_HOLD = {1752: 1604, 1765: 1756}  # 최철수·최공남: 날짜가 달라 확정 중복 아님
LEGACY_AUTO_REASONS = {"내용 변경", "차량번호 형식이 달라 자동 판정 불가", "한 글자 차이: 오타인지 실제 번호변경인지 불확실",
                       "실제 번호변경/입력 오류정정 사유 선택 필요(대폐차 근거 아님)", "차량번호 변경"}
CONF = {"MANUAL_PRESERVED": "사용자 판정 보존", "USER_CONFIRMED_EXCLUSION": "사용자 확정", "ADDRESS_HYPHEN_SPLIT_PROOF": "높음(원본 셀 복원 일치)",
        "BASE_EXCLUSION": "높음(전후값 규칙)", "STORED_USER_CORRECTION": "높음(저장된 사용자 선택)"}


def load_old_module(path):
    spec = importlib.util.spec_from_file_location("old_cj", path); m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m); return m


class Row:  # view_row 호환 객체
    def __init__(self, d): self.__dict__.update(d); self.decisions = []


def build(snapshot, mapping_rows, old_cj):
    ch = snapshot["tables"]["change_history"]; byid = {r["id"]: r for r in ch}
    mapped = [r for r in mapping_rows if r[0] is not None]
    problems = []
    ids = [r[1] for r in mapped]
    if len(set(ids)) != len(ids): problems.append("DB ID 중복 대응")
    src_by_id = {r[1]: r[0] for r in mapped}
    for r in mapped:
        d = byid.get(r[1])
        if not d: problems.append(f"원본행 {r[0]}: DB ID {r[1]} 없음"); continue
        if d.get("deleted_at"): problems.append(f"원본행 {r[0]}: DB ID {r[1]} 삭제표시")
        if (d["change_type"] or "") != (r[2] or "") or _k(d["name"]) != _k(r[4]) or _k(d["vehicle_number"]) != _k(r[5]):
            problems.append(f"원본행 {r[0]}: DB ID {r[1]} 유형/성명/번호 불일치")
    # 순번 규칙 검증
    by_type = collections.defaultdict(list)
    for r in sorted(mapped, key=lambda x: x[0]): by_type[r[2]].append(r)
    lst = by_type["구조변경"]
    struct_ok = all(len(lst) >= n and lst[n - 1][0] == row for n, row in STRUCT_ORDINALS.items())
    if not struct_ok: problems.append("구조변경 순번 지정 제외 대응 불일치")
    overrides = {}
    for n, row in STRUCT_ORDINALS.items():
        if struct_ok: overrides[row] = (EXCLUDED, f"사용자 확정: 구조변경 유형 내 {n}번 실적 제외")
    id_of_src = {r[0]: r[1] for r in mapped}
    for src, (nm, ct, j) in NAMED.items():
        d = byid.get(id_of_src.get(src))
        if d and d["name"] == nm and d["change_type"] == ct: overrides[src] = (j, "사용자 확정: 지정된 과거 기록 실적 제외")
        else: problems.append(f"지정 제외 원본행 {src} 지문 불일치")
    out = []
    for r in sorted(mapped, key=lambda x: x[0]):
        src, i = r[0], r[1]; d = byid[i]
        p = classify_record(d)
        j, rep, why, rule, queue, ev = p.judgement, p.report_decision, p.reason, p.rule, p.queue, dict(p.evidence)
        if src in overrides:
            j, why = overrides[src]; rep, rule, queue = "exclude", "USER_CONFIRMED_EXCLUSION", "AUTO"
        elif src in DUP_HOLD:
            j, rep, why, rule, queue = REVIEW, "hold", f"사용자 지정 중복 후보(기준 원본행 {DUP_HOLD[src]}): 날짜가 달라 동일 사건 여부 확인 필요", "USER_DUPLICATE_CANDIDATE", "DB_DUPLICATE"
        if d.get("judgement") and d.get("judge_reason") not in LEGACY_AUTO_REASONS and d["judgement"] in (NORMAL, FIRST, NOTATION, ERROR_FIX, REVIEW, DUPLICATE, EXCLUDED):
            j = d["judgement"]; rep = "hold" if j == REVIEW else "include" if j == NORMAL else "exclude"
            why, rule, queue = f"기존 수동 판정 보존: {d.get('judge_reason')} (자동안: {p.judgement})", "MANUAL_PRESERVED", "AUTO"
        if not any(d.get(k) for k in ("name", "vehicle_number", "before_value", "after_value", "change_date", "receipt_date", "memo")):
            j, rep, why, rule, queue = EXCLUDED, "exclude", "업무 내용·대상·일자가 없는 빈 기록(원본 보존)", "EMPTY_RECORD", "AUTO"
        ymd, dsrc = pick_report_date(d["change_date"], d["receipt_date"])
        out.append(dict(src=src, id=i, d=d, j=j, rep=rep, why=why, rule=rule, queue=queue, ev=ev, ymd=ymd, dsrc=dsrc,
                        conf=CONF.get(rule, "미확정(자동판정 불가)" if rep == "hold" else "높음(기록 근거: 규칙 판정)"),
                        existing=d.get("judgement") or "", hash=source_hash(d)))
    return out, problems, byid, overrides


def monthly(items, old_cj, new=True):
    agg = collections.defaultdict(list)
    for it in items:
        d = it["d"]
        if new:
            if it["rep"] == "include" and it["ymd"]: agg[(it["ymd"][0], it["ymd"][1], bucket_of(d["change_type"]))].append(it["id"])
        else:
            v = old_cj.view_row(Row(d))
            if v.judgement == old_cj.NORMAL and v.ymd:
                agg[(v.ymd[0], v.ymd[1], v.bucket)].append(it["id"])
    return agg


def deleted_analysis(snapshot, mapped_ids):
    rows = []
    active = [x for x in snapshot["tables"]["change_history"] if not x.get("deleted_at")]
    for r in snapshot["tables"]["change_history"]:
        if not r.get("deleted_at") or r["id"] in mapped_ids: continue
        header = (r.get("name") == "성명" and r.get("vehicle_number") == "차량번호") or "신고일자" in str(r.get("change_date") or "")
        blank = not any(r.get(k) for k in ("name", "vehicle_number", "before_value", "after_value", "change_date"))
        dup = None
        twin = [x["id"] for x in active if x["id"] != r["id"] and all(_k(x.get(k)) == _k(r.get(k)) for k in ("change_type", "name", "vehicle_number", "before_value", "after_value", "change_date"))]
        cat = ("반복 제목 행(성명/차량번호/신고일자)" if header else "빈 기록" if blank else
               f"활성 기록과 동일 내용 존재(DB ID {twin[0]}): 중복 삭제로 보이나 삭제 사유는 미기록" if twin else
               "삭제 사유 미기록·동일 활성 기록 없음: 개별 확인")
        rows.append((r["id"], r["change_type"], cat, r.get("name"), r.get("vehicle_number"), r.get("change_date"), str(r["deleted_at"])[:19]))
    return rows


def write_excel(path, sheets):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook(); wb.remove(wb.active)
    for name, header, data in sheets:
        ws = wb.create_sheet(name); ws.append(header)
        for c in ws[1]: c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="334155"); c.alignment = Alignment(wrap_text=True, vertical="center")
        for row in data: ws.append(["" if v is None else v for v in row])
        ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            w = max((len(str(c.value)) for c in list(col)[:300] if c.value is not None), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max(10, w * 1.2 + 2), 60)
    wb.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True); ap.add_argument("--mapping", required=True)
    ap.add_argument("--old-judgement", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from openpyxl import load_workbook
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    snap = json.load(open(a.snapshot, encoding="utf-8"))
    wb = load_workbook(a.mapping, read_only=True); rows = list(wb["원본3662_DB연결"].iter_rows(values_only=True))[1:]
    old = load_old_module(a.old_judgement)
    items, problems, byid, _ = build(snap, rows, old)
    mapped_ids = {i["id"] for i in items}
    # 운영 DB 추가 활성 기록(원본 3,662건 밖) — 신규 데이터로 같은 규칙 적용
    extra = []
    for r in snap["tables"]["change_history"]:
        if r["id"] in mapped_ids or r.get("deleted_at"): continue
        p = classify_record(r); ymd, dsrc = pick_report_date(r["change_date"], r["receipt_date"])
        extra.append(dict(src="(원본 외)", id=r["id"], d=r, j=p.judgement, rep=p.report_decision, why=p.reason, rule=p.rule,
                          queue=p.queue, ev=p.evidence, ymd=ymd, dsrc=dsrc, conf="높음(기록 근거: 규칙 판정)", existing=r.get("judgement") or "", hash=source_hash(r)))
    all_items = items + extra
    old_m, new_m = monthly(all_items, old, False), monthly(all_items, old, True)
    keys = sorted(set(old_m) | set(new_m))
    cmp_rows, diff_rows = [], []
    for k in keys:
        o, n = old_m.get(k, []), new_m.get(k, [])
        cmp_rows.append((f"{k[0]}-{k[1]:02d}", k[2], len(o), len(n), len(n) - len(o)))
        so, sn = set(o), set(n)
        if so != sn:
            for i in sorted(so - sn): diff_rows.append((f"{k[0]}-{k[1]:02d}", k[2], i, "제외로 변경(기존 집계에는 포함)", next(x for x in all_items if x["id"] == i)["why"]))
            for i in sorted(sn - so): diff_rows.append((f"{k[0]}-{k[1]:02d}", k[2], i, "신규 포함(기존 집계에는 없음)", next(x for x in all_items if x["id"] == i)["why"]))
    hdr = ["원본엑셀행", "운영DB_ID", "변경유형", "지역", "성명", "차량번호", "변경전", "변경후", "변경일", "접수일", "집계기준일", "기준일 출처",
           "최종판정", "집계여부", "확인필요", "판정규칙", "신뢰수준", "판정사유", "기존 DB 판정값", "집계구분", "원본지문(SHA256)"]
    def line(it):
        d = it["d"]; ymd = "%04d-%02d-%02d" % it["ymd"] if it["ymd"] else ""
        return [it["src"], it["id"], d["change_type"], d["region"], d["name"], d["vehicle_number"], d["before_value"], d["after_value"], d["change_date"], d["receipt_date"],
                ymd, it["dsrc"] or "", it["j"], {"include": "포함", "exclude": "제외", "hold": "보류"}[it["rep"]], "예" if it["rep"] == "hold" else "아니오",
                it["rule"], it["conf"], it["why"], it["existing"], bucket_of(d["change_type"]), it["hash"]]
    write_excel(out / "1_최종_자동판정_3662건.xlsx", [("최종판정", hdr, [line(i) for i in items]), ("원본외_운영DB추가", hdr, [line(i) for i in extra])])
    unresolved = [i for i in items if i["rep"] == "hold"]
    write_excel(out / "2_미해결_기록.xlsx", [("미해결", hdr[:10] + ["작업묶음", "자동판정 불가 사유"], [line(i)[:10] + [i["queue"], i["why"]] for i in unresolved])])
    write_excel(out / "3_월례보고_전후비교.xlsx", [("월별_유형별_비교", ["월", "집계구분", "기존 프로그램 집계", "수정 후 집계", "증감"], cmp_rows),
                                              ("차이_DB_ID", ["월", "집계구분", "DB_ID", "구분", "사유"], diff_rows),
                                              ("삭제표시_기록", ["DB_ID", "변경유형", "분석", "성명", "차량번호", "신고일", "삭제시각"], deleted_analysis(snap, mapped_ids))])
    summary = dict(total=len(items), by_decision=collections.Counter(i["rep"] for i in items), by_judgement=collections.Counter(i["j"] for i in items),
                   by_rule_hold=collections.Counter(i["rule"] for i in unresolved), extra=[(e["id"], e["rep"], e["j"]) for e in extra],
                   old_total=sum(len(v) for v in old_m.values()), new_total=sum(len(v) for v in new_m.values()), problems=problems,
                   deleted=collections.Counter(r[2] for r in deleted_analysis(snap, mapped_ids)),
                   existing_judgements=[(i["id"], i["existing"], i["j"], i["rep"]) for i in all_items if i["existing"]])
    json.dump(summary, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=lambda o: dict(o) if isinstance(o, collections.Counter) else str(o))
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=lambda o: dict(o) if isinstance(o, collections.Counter) else str(o)))


if __name__ == "__main__":
    main()
