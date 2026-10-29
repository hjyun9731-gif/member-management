"""월별 변경이력 통계 (인가/변경 → 변경이력대장).

월례보고서(dashboard.monthly_report_auto)와 같은 인정 기준(services.change_judgement.view_row)을 쓴다.
- 기준일: 유효한 변경일자 → 유효한 접수일자. 둘 다 없으면 확인필요(어느 달에도 귀속하지 않음). 원본 날짜는 수정하지 않는다.
- 실적 합계 = 정상변경만. 최초등록·표기정정·오류정정·확인필요는 별도 집계.
- 이 라우터는 읽기 전용이다 (DB를 수정하지 않는다).
"""
import io
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user
from app.database import get_db
from app.services.change_judgement import (
    BUCKET_ORDER, FIRST, NOTATION, ERROR_FIX, REVIEW, NORMAL,
    classify_for_type, legacy_address_split_suspect, mask_rrn, parse_ymd, view_row,
)

router = APIRouter()

SEPARATE = [FIRST, NOTATION, ERROR_FIX, REVIEW]
_SRC_LABEL = {"change_date": "변경일자", "receipt_date": "접수일자", None: "없음"}


def _bounds(date_from, date_to):
    f = parse_ymd(date_from) if date_from else None
    t = parse_ymd(date_to) if date_to else None
    return f, t


def _in_period(ymd, year, month, f, t):
    if ymd is None:
        return False
    if f or t:
        return (not f or ymd >= f) and (not t or ymd <= t)
    if year and ymd[0] != year:
        return False
    if month and ymd[1] != month:
        return False
    return True


def collect(db: Session, year=None, month=None, region=None, change_type=None, date_from=None, date_to=None, search=None):
    """(기간 내 행 목록 [(row, view)], 월 귀속 불가 행 목록 [(row, view)])"""
    f, t = _bounds(date_from, date_to)
    q = db.query(models.ChangeHistory).filter(models.ChangeHistory.deleted_at.is_(None))
    if region:
        q = q.filter(models.ChangeHistory.region == region)
    if change_type:
        q = q.filter(models.ChangeHistory.change_type == change_type)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter((models.ChangeHistory.name.like(like)) | (models.ChangeHistory.vehicle_number.like(like)))
    rows, undated = [], []
    for c in q.all():
        v = view_row(c)
        if v.ymd is None:
            undated.append((c, v))
        elif _in_period(v.ymd, year, month, f, t):
            rows.append((c, v))
    return rows, undated


def _item(c, v):
    return {
        "id": c.id, "name": c.name or "", "vehicle_number": c.vehicle_number or "", "region": c.region or "",
        "change_type": c.change_type or "", "bucket": v.bucket, "judgement": v.judgement, "reason": v.reason,
        "legacy_unclassified": bool(v.legacy),
        "change_date": mask_rrn(c.change_date or ""), "receipt_date": mask_rrn(c.receipt_date or ""),
        "basis_date": ("%04d-%02d-%02d" % v.ymd) if v.ymd else "",
        "basis_source": _SRC_LABEL[v.date_source],
        "before_value": mask_rrn(c.before_value or ""), "after_value": mask_rrn(c.after_value or ""),
        "memo": mask_rrn(c.memo or ""),
    }


def summarize(rows, undated):
    buckets = {b: 0 for b in BUCKET_ORDER}
    by_type, by_region = {}, {}
    sep = {j: 0 for j in SEPARATE}
    legacy = 0
    for c, v in rows:
        r = c.region or "(지역 미상)"
        reg = by_region.setdefault(r, {"합계": 0, **{b: 0 for b in BUCKET_ORDER}, **{j: 0 for j in SEPARATE}})
        if v.judgement == NORMAL:
            buckets[v.bucket] += 1
            by_type[c.change_type or "기타"] = by_type.get(c.change_type or "기타", 0) + 1
            reg["합계"] += 1
            reg[v.bucket] += 1
            legacy += 1 if v.legacy else 0
        else:
            sep[v.judgement] += 1
            reg[v.judgement] += 1
    total = sum(buckets.values())
    return {
        "total": total,
        "buckets": buckets,
        "separate": {"최초등록": sep[FIRST], "표기정정": sep[NOTATION], "오류정정": sep[ERROR_FIX],
                     "확인필요": sep[REVIEW], "월귀속불가(날짜 없음/불량)": len(undated)},
        "by_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "by_region": by_region,
        "legacy_unclassified_in_total": legacy,
    }


@router.get("/summary")
async def stats_summary(year: Optional[int] = Query(None), month: Optional[int] = Query(None, ge=0, le=12),
                        region: Optional[str] = Query(None), change_type: Optional[str] = Query(None),
                        date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                        db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows, undated = collect(db, year, month or None, region, change_type, date_from, date_to)
    out = summarize(rows, undated)
    out["period"] = {"year": year, "month": month or None, "date_from": date_from, "date_to": date_to}
    out["note"] = ("정상변경만 변경실적 합계에 포함합니다. 기존(판정 미저장) 이력은 정상변경으로 간주하되 "
                   "재분류 미리보기·승인 전까지 확정하지 않습니다.")
    return out


@router.get("/detail")
async def stats_detail(bucket: str = Query("전체", description="상호변경/차량변경(대폐차)/주소변경/차량번호변경/대표자변경/자격증재교부/기타/전체/최초등록/표기정정/오류정정/확인필요/월귀속불가"),
                       year: Optional[int] = Query(None), month: Optional[int] = Query(None, ge=0, le=12),
                       region: Optional[str] = Query(None), change_type: Optional[str] = Query(None),
                       date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                       search: Optional[str] = Query(None), limit: int = Query(500, ge=1, le=2000),
                       db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows, undated = collect(db, year, month or None, region, change_type, date_from, date_to, search)
    if bucket == "월귀속불가":
        sel = undated
    elif bucket in SEPARATE:
        sel = [(c, v) for c, v in rows if v.judgement == bucket]
        if bucket == REVIEW:
            sel = sel  # 기간 내 확인필요만. 날짜 불량은 '월귀속불가'에서 확인
    elif bucket == "전체":
        sel = [(c, v) for c, v in rows if v.judgement == NORMAL]
    else:
        sel = [(c, v) for c, v in rows if v.judgement == NORMAL and v.bucket == bucket]
    sel.sort(key=lambda cv: (cv[1].ymd or (0, 0, 0), cv[0].id), reverse=True)
    return {"bucket": bucket, "total": len(sel), "items": [_item(c, v) for c, v in sel[:limit]]}


@router.get("/export")
async def stats_export(year: Optional[int] = Query(None), month: Optional[int] = Query(None, ge=0, le=12),
                       region: Optional[str] = Query(None), change_type: Optional[str] = Query(None),
                       date_from: Optional[str] = Query(None), date_to: Optional[str] = Query(None),
                       db: Session = Depends(get_db), _=Depends(get_current_user)):
    import pandas as pd
    rows, undated = collect(db, year, month or None, region, change_type, date_from, date_to)
    s = summarize(rows, undated)
    summ = [("정상변경 합계", s["total"])] + [(k, v) for k, v in s["buckets"].items()] + [(f"[별도] {k}", v) for k, v in s["separate"].items()]
    reg = pd.DataFrame([{"지역": r, **vals} for r, vals in sorted(s["by_region"].items())])
    cols = ["id", "name", "vehicle_number", "region", "change_type", "bucket", "judgement", "reason", "basis_date",
            "basis_source", "change_date", "receipt_date", "before_value", "after_value", "memo", "legacy_unclassified"]
    labels = {"id": "ID", "name": "성명", "vehicle_number": "차량번호", "region": "지역", "change_type": "변경유형",
              "bucket": "집계구분", "judgement": "판정", "reason": "판정근거", "basis_date": "집계기준일",
              "basis_source": "기준일출처", "change_date": "변경일자", "receipt_date": "접수일자",
              "before_value": "변경 전", "after_value": "변경 후", "memo": "비고", "legacy_unclassified": "기존미분류"}
    def df(sel):
        return pd.DataFrame([_item(c, v) for c, v in sel], columns=cols).rename(columns=labels)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        pd.DataFrame(summ, columns=["구분", "건수"]).to_excel(w, sheet_name="요약", index=False)
        (reg if not reg.empty else pd.DataFrame(columns=["지역"])).to_excel(w, sheet_name="지역별", index=False)
        df([(c, v) for c, v in rows if v.judgement == NORMAL]).to_excel(w, sheet_name="정상변경 상세", index=False)
        df([(c, v) for c, v in rows if v.judgement != NORMAL] + undated).to_excel(w, sheet_name="별도(최초·정정·확인필요)", index=False)
    return StreamingResponse(io.BytesIO(out.getvalue()),
                             media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=change_stats.xlsx"})


@router.get("/reclassify-preview")
async def reclassify_preview(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """기존(판정 미저장) 이력을 새 규칙으로 판정하면 어떻게 달라지는지 '미리보기'만 제공한다. DB는 수정하지 않는다."""
    from collections import Counter
    cnt, moves, no_date = Counter(), Counter(), 0
    samples = []
    for c in db.query(models.ChangeHistory).filter(models.ChangeHistory.deleted_at.is_(None),
                                                    models.ChangeHistory.judgement.is_(None)).all():
        j, why = classify_for_type(c.change_type, c.before_value, c.after_value) \
            if (c.change_type or "") in ("차량번호변경", "번호변경", "주소지변경", "상호변경", "전속계약 업체변경", "대폐차", "차종변경") \
            else (NORMAL, "비교 대상 아님")
        if (c.change_type or "") == "주소지변경" and j == NORMAL and legacy_address_split_suspect(c.before_value, c.after_value):
            j, why = REVIEW, "주소가 상세값만 분리 저장된 것으로 의심(업로드 열 어긋남)"
        v = view_row(c)
        if v.ymd is None:
            no_date += 1
            j, why = REVIEW, "변경일자·접수일자 모두 유효하지 않음"
        cnt[j] += 1
        if j != NORMAL and len(samples) < 200:
            samples.append(_item(c, v) | {"proposed_judgement": j, "proposed_reason": why})
    return {"read_only": True, "applied": False, "legacy_total": sum(cnt.values()),
            "proposed_counts": dict(cnt), "no_valid_date": no_date, "samples": samples}
