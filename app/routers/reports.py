from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import io, re, pandas as pd
from datetime import datetime, timezone

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import is_association_member

router = APIRouter()


# ===================================================================
# 월례보고서 항목 관리 (관리자가 화면에서 항목을 추가/수정할 수 있는 구조)
# ===================================================================

# 최초 기동 시 1회만 채워지는 기본 항목 (기존 월례_업무현황_보고서.html 양식 기준)
# (section, key, label, field_type, auto_path, display_order)
# field_type: number(숫자) / amount(금액) / text(텍스트) / longtext(장문텍스트) / auto(자동계산)
_DEFAULT_REPORT_FIELDS = [
    ("1. 허가 및 회원 현황", "stat_total", "총 허가/등록", "auto", "member_stats.total", 10),
    ("1. 허가 및 회원 현황", "stat_joined", "협회 가입", "auto", "member_stats.joined", 20),
    ("1. 허가 및 회원 현황", "stat_not_joined", "미가입", "auto", "member_stats.not_joined", 30),
    ("1. 허가 및 회원 현황", "stat_delivery_unreported", "택배 미신고", "auto", "taxi_stats.unemployed", 40),
    ("1. 허가 및 회원 현황", "tbl1_ind_total", "개인 총허가", "auto", "member_stats.individual", 50),
    ("1. 허가 및 회원 현황", "tbl1_ind_joined", "개인 협회가입", "auto", "member_stats.ind_joined", 60),
    ("1. 허가 및 회원 현황", "tbl1_ind_not_joined", "개인 미가입", "auto", "member_stats.ind_not_joined", 70),
    ("1. 허가 및 회원 현황", "tbl1_del_total", "택배 총허가", "auto", "member_stats.delivery", 80),
    ("1. 허가 및 회원 현황", "tbl1_del_joined", "택배 협회가입", "auto", "member_stats.del_joined", 90),
    ("1. 허가 및 회원 현황", "tbl1_del_not_joined", "택배 미가입", "auto", "member_stats.del_not_joined", 100),
    ("1. 허가 및 회원 현황", "month_new_join", "당월 신규가입", "auto", "member_stats.month_joined", 110),
    ("1. 허가 및 회원 현황", "month_not_joined_new", "당월 미가입발생", "auto", "member_stats.month_not_joined", 120),

    ("2. 당월 허가·회원 업무 처리현황", "new_registration_month", "신규등록(가입자) 월계", "auto", "month_activity.new_registrations", 200),
    ("2. 당월 허가·회원 업무 처리현황", "new_registration_cum", "신규등록 누계", "number", None, 205),
    ("2. 당월 허가·회원 업무 처리현황", "transfer_month", "양도양수(가입자) 월계", "auto", "month_activity.transfers", 210),
    ("2. 당월 허가·회원 업무 처리현황", "transfer_cum", "양도양수 누계", "number", None, 215),
    ("2. 당월 허가·회원 업무 처리현황", "move_in_month", "전입(가입자) 월계", "number", None, 220),
    ("2. 당월 허가·회원 업무 처리현황", "move_in_cum", "전입 누계", "number", None, 225),
    ("2. 당월 허가·회원 업무 처리현황", "move_out_month", "전출 월계", "number", None, 230),
    ("2. 당월 허가·회원 업무 처리현황", "move_out_cum", "전출 누계", "number", None, 235),
    ("2. 당월 허가·회원 업무 처리현황", "cancel_replace_month", "말소 및 대체등록 월계", "number", None, 240),
    ("2. 당월 허가·회원 업무 처리현황", "cancel_replace_cum", "말소 및 대체등록 누계", "number", None, 245),
    ("2. 당월 허가·회원 업무 처리현황", "address_change_month", "주소지변경 월계", "auto", "admin_work.주소변경", 250),
    ("2. 당월 허가·회원 업무 처리현황", "address_change_cum", "주소지변경 누계", "number", None, 255),
    ("2. 당월 허가·회원 업무 처리현황", "change_reg_month", "변경등록(상호변경·구조변경·전속계약 업체변경) 월계", "auto", "admin_work._변경등록전체", 260),
    ("2. 당월 허가·회원 업무 처리현황", "change_reg_cum", "변경등록 누계", "number", None, 265),
    ("2. 당월 허가·회원 업무 처리현황", "closure_month", "폐업(허가취소) 월계", "auto", "month_activity.closures", 270),
    ("2. 당월 허가·회원 업무 처리현황", "closure_cum", "폐업 누계", "number", None, 275),
    ("2. 당월 허가·회원 업무 처리현황", "cert_reissue_month", "자격증 재교부 월계", "auto", "admin_work.자격증재교부", 280),
    ("2. 당월 허가·회원 업무 처리현황", "cert_reissue_cum", "자격증 재교부 누계", "number", None, 285),
    ("2. 당월 허가·회원 업무 처리현황", "employment_report_month", "취업신고 월계", "auto", "admin_work.취업신고", 290),
    ("2. 당월 허가·회원 업무 처리현황", "employment_report_cum", "취업신고 누계", "number", None, 295),
    ("2. 당월 허가·회원 업무 처리현황", "resignation_report_month", "퇴사신고 월계", "auto", "admin_work.퇴사신고", 300),
    ("2. 당월 허가·회원 업무 처리현황", "resignation_report_cum", "퇴사신고 누계", "number", None, 305),
    ("2. 당월 허가·회원 업무 처리현황", "new_association_join_month", "신규가입(협회) 월계", "number", None, 310),
    ("2. 당월 허가·회원 업무 처리현황", "new_association_join_cum", "신규가입(협회) 누계", "number", None, 315),

    ("4. 문서·증명서 등 행정업무", "doc_receive_month", "문서 접수 (건)", "number", None, 400),
    ("4. 문서·증명서 등 행정업무", "doc_receive_cum", "문서 접수 누계", "number", None, 405),
    ("4. 문서·증명서 등 행정업무", "doc_send_month", "문서 발송 (건)", "number", None, 410),
    ("4. 문서·증명서 등 행정업무", "doc_send_cum", "문서 발송 누계", "number", None, 415),
    ("4. 문서·증명서 등 행정업무", "cert_issue_month", "경력증명서 발급 (건)", "number", None, 420),
    ("4. 문서·증명서 등 행정업무", "cert_issue_cum", "경력증명서 발급 누계", "number", None, 425),
    ("4. 문서·증명서 등 행정업무", "work_cert_issue_month", "화물운송종사자격증명 발급 (건)", "number", None, 430),
    ("4. 문서·증명서 등 행정업무", "work_cert_issue_cum", "화물운송종사자격증명 발급 누계", "number", None, 435),
    ("4. 문서·증명서 등 행정업무", "edu_guide_month", "교육·검사 안내 (건)", "number", None, 440),
    ("4. 문서·증명서 등 행정업무", "edu_guide_cum", "교육·검사 안내 누계", "number", None, 445),
    ("4. 문서·증명서 등 행정업무", "etc_complaint_month", "기타 민원 (건)", "number", None, 450),
    ("4. 문서·증명서 등 행정업무", "etc_complaint_cum", "기타 민원 누계", "number", None, 455),

    ("5. 주요 특이사항 / 다음달 관리사항", "remarks", "특이사항 및 다음달 관리사항", "longtext", None, 500),
]


def _seed_default_report_fields(db: Session):
    if db.query(models.ReportFieldDef).count() > 0:
        return
    for section, key, label, ftype, auto_path, order in _DEFAULT_REPORT_FIELDS:
        db.add(models.ReportFieldDef(
            key=key, label=label, section=section, field_type=ftype,
            auto_path=auto_path, display_order=order,
            is_active=True, is_printable=True))
    db.commit()


def _field_out(r: "models.ReportFieldDef"):
    return {
        "id": r.id, "key": r.key, "label": r.label, "section": r.section,
        "field_type": r.field_type, "auto_path": r.auto_path,
        "display_order": r.display_order, "is_active": r.is_active,
        "is_printable": r.is_printable,
    }


def _resolve_path(data: dict, path: str):
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


@router.get("/monthly-report/field-defs")
async def list_report_field_defs(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """월례보고서 항목 목록 (관리 화면 + 보고서 화면 공용)"""
    _seed_default_report_fields(db)
    rows = db.query(models.ReportFieldDef).order_by(models.ReportFieldDef.display_order, models.ReportFieldDef.id).all()
    return [_field_out(r) for r in rows]


@router.post("/monthly-report/field-defs")
async def create_report_field_def(data: dict, db: Session = Depends(get_db), _=Depends(require_admin)):
    """월례보고서 항목 추가 (관리자 전용)"""
    key = (data.get("key") or "").strip()
    if not key:
        raise HTTPException(400, "항목 key는 필수입니다")
    if db.query(models.ReportFieldDef).filter(models.ReportFieldDef.key == key).first():
        raise HTTPException(400, "이미 존재하는 key입니다")
    ftype = data.get("field_type", "text")
    if ftype not in ("number", "amount", "text", "longtext", "table", "auto"):
        raise HTTPException(400, "지원하지 않는 항목 유형입니다")
    row = models.ReportFieldDef(
        key=key, label=data.get("label", key), section=data.get("section", "기타"),
        field_type=ftype, auto_path=data.get("auto_path"),
        display_order=data.get("display_order", 999),
        is_active=data.get("is_active", True), is_printable=data.get("is_printable", True))
    db.add(row)
    db.commit()
    db.refresh(row)
    return _field_out(row)


@router.put("/monthly-report/field-defs/{field_id}")
async def update_report_field_def(field_id: int, data: dict, db: Session = Depends(get_db), _=Depends(require_admin)):
    """월례보고서 항목 수정 (관리자 전용)"""
    row = db.query(models.ReportFieldDef).filter(models.ReportFieldDef.id == field_id).first()
    if not row:
        raise HTTPException(404, "항목을 찾을 수 없습니다")
    for f in ["label", "section", "field_type", "auto_path", "display_order", "is_active", "is_printable"]:
        if f in data:
            setattr(row, f, data[f])
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _field_out(row)


@router.delete("/monthly-report/field-defs/{field_id}")
async def delete_report_field_def(field_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    """월례보고서 항목 삭제 (관리자 전용). 저장된 과거 보고서의 값 자체는 삭제하지 않음(custom_data에 그대로 남음)."""
    row = db.query(models.ReportFieldDef).filter(models.ReportFieldDef.id == field_id).first()
    if not row:
        raise HTTPException(404, "항목을 찾을 수 없습니다")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/monthly-report/full")
async def monthly_report_full(year: int = Query(...), month: int = Query(...),
                               db: Session = Depends(get_db), _=Depends(get_current_user)):
    """월례_업무현황_보고서.html 양식용 통합 데이터: 항목정의 + 자동집계값 + 저장된 직접입력값"""
    _seed_default_report_fields(db)
    fields = db.query(models.ReportFieldDef).filter(models.ReportFieldDef.is_active == True) \
        .order_by(models.ReportFieldDef.display_order, models.ReportFieldDef.id).all()

    # 기존 대시보드 자동집계 로직을 그대로 재사용 (중복 구현 방지)
    from app.routers.dashboard import monthly_report_auto
    auto_data = await monthly_report_auto(year=year, month=month, db=db, _=None)

    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    manual_values = dict(entry.custom_data or {}) if entry else {}

    field_list = []
    for f in fields:
        if f.field_type == "auto":
            val = _resolve_path(auto_data, f.auto_path or "")
        else:
            val = manual_values.get(f.key, "")
        field_list.append({**_field_out(f), "value": val})

    return {
        "year": year, "month": month,
        "fields": field_list,
        "meta": {
            "writer": manual_values.get("_writer", ""),
            "checker": manual_values.get("_checker", ""),
            "base_date": manual_values.get("_base_date") or f"{year}. {month:02d}. ",
        },
        "vehicle_types": auto_data.get("vehicle_types", []),
        "age_groups": auto_data.get("age_groups", {}),
        "vehicle_age": auto_data.get("vehicle_age", {}),
        "saved": entry is not None,
        "updated_at": entry.updated_at.isoformat() if (entry and entry.updated_at) else None,
    }


@router.post("/monthly-report/full")
async def save_monthly_report_full(year: int = Query(...), month: int = Query(...),
                                    data: dict = None, db: Session = Depends(get_db),
                                    _=Depends(get_current_user)):
    """직접입력 항목 + 작성자/확인자/기준일 저장. 자동계산 항목은 저장 대상에서 제외."""
    data = data or {}
    values = data.get("values", {}) or {}
    meta = data.get("meta", {}) or {}

    field_defs = {f.key: f for f in db.query(models.ReportFieldDef).all()}

    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    if not entry:
        entry = models.MonthlyReportEntry(year=year, month=month, custom_data={})
        db.add(entry)

    merged = dict(entry.custom_data or {})
    for k, v in values.items():
        fd = field_defs.get(k)
        if fd and fd.field_type == "auto":
            continue  # 자동계산 항목은 저장하지 않음 (항상 실시간 재계산)
        merged[k] = v
    if "writer" in meta:
        merged["_writer"] = meta["writer"]
    if "checker" in meta:
        merged["_checker"] = meta["checker"]
    if "base_date" in meta:
        merged["_base_date"] = meta["base_date"]

    entry.custom_data = merged
    entry.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


def _extract_year_month(date_str: str):
    """날짜 문자열에서 (year, month) 추출. 실패시 (None, None)"""
    if not date_str:
        return None, None
    s = str(date_str).strip()
    # 4자리 연도
    m = re.search(r'(19[0-9]{2}|20[0-9]{2})\s*[\.\-/]\s*(\d{1,2})', s)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 2자리 연도
    m = re.match(r'^(\d{2})\s*[\.\-/]\s*(\d{1,2})', s)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy if yy <= 30 else 1900 + yy
        return year, int(m.group(2))
    return None, None


def _in_month(date_str: str, year: int, month: int) -> bool:
    y, mo = _extract_year_month(date_str)
    return y == year and mo == month


@router.get("/monthly")
async def monthly(year: int = Query(...), month: int = Query(...),
                   db: Session = Depends(get_db), _=Depends(get_current_user)):
    """월례보고서: 해당 연/월 기준으로 가입/미가입/신규/양도/폐업 집계"""
    all_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active"
    ).all()

    # 해당 월 가입자: membership_date(가입일자)가 해당 월인 사람
    month_joined = sum(1 for m in all_members
                       if _in_month(m.membership_date or '', year, month))
    # 해당 월 미가입자: 가입일자가 없고(공통 판정 함수 기준) approval_date(인가일자)가 해당 월인 사람
    month_not_joined = sum(1 for m in all_members
                           if not is_association_member(m.membership_date) and _in_month(m.approval_date or '', year, month))

    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    alloc = db.query(models.AllocationCount).filter(
        models.AllocationCount.year == year, models.AllocationCount.month == month
    ).first()

    # 해당 월 신규/양도/폐업
    month_new = sum(1 for m in all_members
                    if m.registration_type == '신규' and _in_month(m.approval_date or '', year, month))
    # 양도: receipt_date(접수일자) 기준
    month_transfers = sum(1 for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)).all()
        if _in_month(t.receipt_date or '', year, month))
    # 폐업: closure_date 기준
    month_closures = sum(1 for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()
        if _in_month(c.closure_date or '', year, month))

    return {
        "year": year, "month": month,
        "member_stats": {
            "total": len(all_members),
            "joined": sum(1 for m in all_members if is_association_member(m.membership_date)),
            "individual": sum(1 for m in all_members if m.category == "개인"),
            "delivery": sum(1 for m in all_members if m.category == "택배"),
            "month_joined": month_joined,
            "month_not_joined": month_not_joined,
        },
        "monthly_counts": {
            "new_members": month_new,
            "transfers": month_transfers,
            "closures": month_closures,
        },
        "manual_entry": {
            "document_number": entry.document_number if entry else "",
            "execution_date": entry.execution_date if entry else "",
            "memo": entry.memo if entry else "",
            "custom_data": entry.custom_data if entry else {},
        },
        "allocation": {
            "association_join": alloc.association_join if alloc else 0,
            "transfer_in": alloc.transfer_in if alloc else 0,
            "other_region": alloc.other_region if alloc else 0,
            "closed": alloc.closed if alloc else 0,
            "withdrawn": alloc.withdrawn if alloc else 0,
            "delivery_new": alloc.delivery_new if alloc else 0,
            "mgmt_fee_closed": alloc.mgmt_fee_closed if alloc else 0,
            "over_70": alloc.over_70 if alloc else 0,
            "base_count": alloc.base_count if alloc else 0,
            "total_count": alloc.total_count if alloc else 0,
            "delivery_mgmt": alloc.delivery_mgmt if alloc else 0,
        } if alloc else None,
    }


@router.post("/monthly/save")
async def save_entry(year: int = Query(...), month: int = Query(...),
                      data: dict = None,
                      db: Session = Depends(get_db), _=Depends(get_current_user)):
    entry = db.query(models.MonthlyReportEntry).filter(
        models.MonthlyReportEntry.year == year, models.MonthlyReportEntry.month == month
    ).first()
    from datetime import datetime, timezone
    if entry:
        entry.document_number = data.get("document_number", "")
        entry.execution_date = data.get("execution_date", "")
        entry.memo = data.get("memo", "")
        entry.custom_data = data.get("custom_data", {})
        entry.updated_at = datetime.now(timezone.utc)
    else:
        entry = models.MonthlyReportEntry(year=year, month=month,
            document_number=data.get("document_number", ""),
            execution_date=data.get("execution_date", ""),
            memo=data.get("memo", ""), custom_data=data.get("custom_data", {}))
        db.add(entry)
    db.commit()
    return {"ok": True}


@router.get("/monthly/export")
async def export(year: int = Query(...), month: int = Query(...),
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    all_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.status == "active").all()
    new_members = [m for m in all_members
                   if m.registration_type == "신규" and _in_month(m.approval_date or '', year, month)]
    transfer_list = [t for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)).all()
        if _in_month(t.receipt_date or '', year, month)]
    closure_list = [c for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()
        if _in_month(c.closure_date or '', year, month)]

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        def sheet(data, name):
            pd.DataFrame(data or [{}]).to_excel(w, sheet_name=name, index=False)
        sheet([{"관리번호": r.management_number, "지역": r.region, "차량번호": r.vehicle_number,
                "성명": r.name, "등록구분": r.registration_type} for r in new_members], "신규등록")
        sheet([{"지역": r.region, "차량번호": r.vehicle_number, "양도자": r.transferor,
                "양수자": r.transferee, "접수일자": r.receipt_date, "인가일자": r.approval_date} for r in transfer_list], "양도양수")
        sheet([{"관리번호": r.management_number, "지역": r.region, "차량번호": r.vehicle_number,
                "성명": r.name, "구분": "폐업"} for r in closure_list], "폐업")
    out.seek(0)
    return StreamingResponse(out,
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": f"attachment; filename=report_{year}_{month:02d}.xlsx"})
