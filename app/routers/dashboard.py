"""
대시보드 - 전부 자동 계산, 입력 없음
날짜 기준: 신규→인가일자, 양도/폐지/변경→처리일자(approval_date or closure_date or change_date)
"""
import re
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.auth import get_current_user
from app import models, crud
from app.excel_utils import normalize_fuel

router = APIRouter()


def _ext_year(s: str) -> Optional[int]:
    if not s: return None
    m = re.search(r'(19[0-9]{2}|20[0-2][0-9])', str(s))
    if m: return int(m.group())
    m = re.match(r'^(\d{2})\s*[\.\-/년]', str(s).strip())
    if m:
        yy = int(m.group(1))
        return (2000 + yy) if yy <= 30 else (1900 + yy)
    return None


def _ext_month(s: str) -> Optional[int]:
    if not s: return None
    # "14. 7. 8." → 7
    m = re.search(r'[\.\-/]\s*(\d{1,2})\s*[\.\-/]', str(s))
    if m:
        val = int(m.group(1))
        if 1 <= val <= 12: return val
    return None


def classify_vt(vt: str) -> str:
    if not vt: return '기타'
    v = vt.lower()
    if '냉동' in v or '냉장' in v: return '냉동탑차'
    if '사다리' in v: return '사다리차'
    if '전기' in v or '일렉트릭' in v: return '전기차'
    if '하이브리드' in v: return '전기차'
    if '봉고' in v and ('탑' in v or '내장' in v): return '봉고탑차'
    if '봉고' in v: return '봉고'
    if ('포터' in v) and ('탑' in v or '내장' in v): return '포터탑차'
    if '포터' in v: return '포터'
    if '픽업' in v or '렉스턴' in v: return 'SUV/픽업'
    if '1톤' in v: return '1톤트럭'
    return '기타'


def _normalize_fuel_stat(fuel: str) -> Optional[str]:
    """공통 normalize_fuel 래퍼 - 빈 값이면 None 반환 (통계 집계 제외용)"""
    result = normalize_fuel(fuel)
    return result if result else None


def ext_veh_year(vt: str) -> Optional[int]:
    m = re.match(r'^(\d{2})\s*,', str(vt or '').strip())
    if m:
        yy = int(m.group(1))
        return 2000 + yy
    return None


def calc_age_from_resident(rn: str) -> Optional[int]:
    if not rn or len(rn) < 8: return None
    try:
        yy = int(rn[:2])
        n = int(rn[7])
        birth_year = (2000 + yy) if n in (3, 4) else (1900 + yy)
        return datetime.now().year - birth_year
    except Exception:
        return None


@router.get("/stats")
async def stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return crud.get_dashboard_stats(db)


@router.get("/regional")
async def regional(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return crud.get_regional_stats(db)


@router.get("/full-stats")
async def full_stats(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """대시보드 전체 자동 계산 통계"""
    lh_q = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    )
    total = lh_q.count()
    joined = lh_q.filter(models.LicenseHolder.membership_status == "가입").count()
    individual = lh_q.filter(models.LicenseHolder.category == "개인").count()
    delivery = lh_q.filter(models.LicenseHolder.category == "택배").count()
    delivery_employed = lh_q.filter(
        models.LicenseHolder.category == "택배",
        models.LicenseHolder.affiliated_company.isnot(None),
        models.LicenseHolder.affiliated_company != ""
    ).count()

    # 차종별
    vtype_counts: dict = {}
    for m in lh_q.all():
        cat = classify_vt(m.vehicle_type or "")
        vtype_counts[cat] = vtype_counts.get(cat, 0) + 1

    # 유종별 (정규화: 전기/경유/LPG/휘발유/기타만 표시)
    fuel_counts: dict = {}
    fuel_rows = (db.query(models.LicenseHolder.fuel_type, func.count())
                 .filter(models.LicenseHolder.deleted_at.is_(None),
                         models.LicenseHolder.status == "active",
                         models.LicenseHolder.fuel_type.isnot(None),
                         models.LicenseHolder.fuel_type != "")
                 .group_by(models.LicenseHolder.fuel_type).all())
    for raw_fuel, cnt in fuel_rows:
        normalized = _normalize_fuel_stat(raw_fuel)
        if normalized:
            fuel_counts[normalized] = fuel_counts.get(normalized, 0) + cnt

    # 연령대별 (주민등록번호 기반)
    age_groups = {"29이하": 0, "30~39": 0, "40~49": 0, "50~59": 0, "60~64": 0, "65~69": 0, "70이상": 0, "불명": 0}
    for m in lh_q.all():
        age = calc_age_from_resident(m.resident_number or "")
        if age is None:
            age_groups["불명"] += 1
        elif age <= 29: age_groups["29이하"] += 1
        elif age <= 39: age_groups["30~39"] += 1
        elif age <= 49: age_groups["40~49"] += 1
        elif age <= 59: age_groups["50~59"] += 1
        elif age <= 64: age_groups["60~64"] += 1
        elif age <= 69: age_groups["65~69"] += 1
        else: age_groups["70이상"] += 1

    # 연식별 (vehicle_type "18,포터II..." 형식) - 1년 단위 버킷
    veh_year_dist: dict = {}
    cur_year = datetime.now().year
    for m in lh_q.all():
        vy = ext_veh_year(m.vehicle_type or "")
        if vy:
            age_y = cur_year - vy
            if age_y < 1: bkt = "1년 미만"
            elif age_y < 2: bkt = "2년 미만"
            elif age_y < 3: bkt = "3년 미만"
            elif age_y < 4: bkt = "4년 미만"
            elif age_y < 5: bkt = "5년 미만"
            elif age_y < 6: bkt = "6년 미만"
            elif age_y < 7: bkt = "7년 미만"
            elif age_y < 8: bkt = "8년 미만"
            elif age_y < 9: bkt = "9년 미만"
            elif age_y < 10: bkt = "10년 미만"
            elif age_y < 11: bkt = "11년 미만"
            elif age_y < 12: bkt = "12년 미만"
            else: bkt = "12년 이상"
            veh_year_dist[bkt] = veh_year_dist.get(bkt, 0) + 1

    # 폐지/양도/이관 집계
    cl_q = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None))
    closure_by_type = {}
    for r in (cl_q.with_entities(models.Closure.closure_type, func.count())
              .group_by(models.Closure.closure_type).all()):
        closure_by_type[r[0] or "기타"] = r[1]

    # 부과대수 자동 계산
    now = datetime.now()
    # 70세 이상
    over_70 = age_groups.get("70이상", 0)
    # 신규등록 (기준: registration_type='신규')
    # 신규등록 건수 - 관리번호 '신' 시작 기준
    new_reg_count = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
    ).count()
    # 양도 건수 (양도양수대장 기준)
    transfer_count = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)).count()
    # 폐지(폐업) 건수
    closed_count = closure_by_type.get("폐업", 0)
    # 이관 건수
    transfer_out_count = closure_by_type.get("이관", 0)

    allocation = {
        "협회가입": joined,
        "양도": transfer_count,
        "타도(이관)": transfer_out_count,
        "폐지": closed_count,
        "탈퇴": None,  # 데이터 없음
        "택배신규": db.query(models.LicenseHolder).filter(
            models.LicenseHolder.deleted_at.is_(None),
            models.LicenseHolder.category == "택배",
            models.LicenseHolder.management_number.like("신%"),
        ).count(),
        "관리비폐지": None,  # 데이터 없음
        "70세": over_70,
        "협회기본대수": total,
        "총부과대수": total,
        "택배관리": delivery,
    }

    return {
        "summary": {
            "total": total, "joined": joined, "not_joined": total - joined,
            "individual": individual, "delivery": delivery,
            "delivery_employed": delivery_employed,
            "delivery_unemployed": delivery - delivery_employed,
        },
        "vehicle_types": [{"type": k, "count": v}
                          for k, v in sorted(vtype_counts.items(), key=lambda x: -x[1])],
        "fuel_types": [{"type": k, "count": v}
                       for k, v in sorted(fuel_counts.items(), key=lambda x: -x[1])],
        "age_groups": age_groups,
        "vehicle_age": veh_year_dist,
        "closure_by_type": closure_by_type,
        "allocation": allocation,
    }


@router.get("/activity-by-year")
async def activity_by_year(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """연도별 신규/양도/폐지/변경 건수 (날짜 기준)"""
    result: dict = {}

    # 신규등록 - 인가일자 기준, 관리번호 '신'으로 시작하는 자료
    for m in db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
    ).all():
        y = _ext_year(m.approval_date or "")
        if y:
            result.setdefault(y, {"year": y, "new": 0, "transfer": 0, "closure": 0, "change": 0})
            result[y]["new"] += 1

    # 양도양수 - process_date (처리일자) 기준
    for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
    ).all():
        y = _ext_year(t.process_date or t.approval_date or t.receipt_date or "")
        if y:
            result.setdefault(y, {"year": y, "new": 0, "transfer": 0, "closure": 0, "change": 0})
            result[y]["transfer"] += 1

    # 폐지 - closure_date 기준
    for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
    ).all():
        y = _ext_year(c.closure_date or "")
        if y:
            result.setdefault(y, {"year": y, "new": 0, "transfer": 0, "closure": 0, "change": 0})
            result[y]["closure"] += 1

    # 변경이력 - change_date 기준
    for c in db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
    ).all():
        y = _ext_year(c.change_date or "")
        if y:
            result.setdefault(y, {"year": y, "new": 0, "transfer": 0, "closure": 0, "change": 0})
            result[y]["change"] += 1

    return sorted(result.values(), key=lambda x: x["year"])


@router.get("/recent-by-type")
async def recent_by_type(
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    """데이터 내 최신순 목록 (신규/양도/폐지/변경)"""
    new_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
        models.LicenseHolder.registration_type == "신규",
    ).order_by(models.LicenseHolder.id.desc()).limit(limit).all()

    transfers = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.vehicle_number.isnot(None),
        models.TransferLedger.vehicle_number != "",
    ).order_by(models.TransferLedger.id.desc()).limit(limit).all()

    closures = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
        models.Closure.vehicle_number.isnot(None),
    ).order_by(models.Closure.id.desc()).limit(limit).all()

    changes = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.vehicle_number.isnot(None),
    ).order_by(models.ChangeHistory.id.desc()).limit(limit).all()

    return {
        "new_members": [{"region": m.region, "vehicle_number": m.vehicle_number,
                          "name": m.name, "category": m.category,
                          "approval_date": m.approval_date, "management_number": m.management_number}
                        for m in new_members],
        "transfers": [{"region": t.region, "vehicle_number": t.vehicle_number,
                        "transferor": t.transferor, "transferee": t.transferee,
                        "approval_date": t.approval_date}
                      for t in transfers],
        "closures": [{"management_number": c.management_number, "region": c.region,
                       "vehicle_number": c.vehicle_number, "name": c.name,
                       "closure_type": c.closure_type, "closure_date": c.closure_date}
                     for c in closures],
        "changes": [{"change_type": c.change_type, "region": c.region,
                      "vehicle_number": c.vehicle_number, "name": c.name,
                      "after_value": c.after_value, "change_date": c.change_date}
                    for c in changes],
    }


@router.get("/monthly-report-auto")
async def monthly_report_auto(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    """월례보고서 자동 계산 - 연도/월 지정 또는 최신 데이터 기준"""
    now = datetime.now()
    target_year = year or now.year
    target_month = month or now.month

    def matches(date_str: str) -> bool:
        if not date_str: return False
        y = _ext_year(date_str)
        m_ = _ext_month(date_str)
        return y == target_year and m_ == target_month

    lh_q = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    )
    all_members = lh_q.all()
    total = len(all_members)
    joined = sum(1 for m in all_members if m.membership_status == "가입")
    individual = sum(1 for m in all_members if m.category == "개인")
    delivery = sum(1 for m in all_members if m.category == "택배")

    # 사업자수/차량대수
    member_stats = {
        "total": total, "individual": individual, "delivery": delivery,
        "joined": joined, "not_joined": total - joined,
    }

    # 택배 취업신고 (소속업체 있으면 취업신고)
    del_employed = sum(1 for m in all_members
                       if m.category == "택배" and m.affiliated_company and m.affiliated_company.strip())
    taxi_stats = {
        "total_delivery": delivery,
        "employed": del_employed,
        "unemployed": delivery - del_employed,
    }

    # 유형별 (차종 분류)
    vtype_counts: dict = {}
    for m in all_members:
        cat = classify_vt(m.vehicle_type or "")
        vtype_counts[cat] = vtype_counts.get(cat, 0) + 1

    # 연령대
    age_groups = {"29이하": 0, "30~39": 0, "40~49": 0, "50~59": 0,
                  "60~64": 0, "65~69": 0, "70이상": 0, "불명": 0}
    for m in all_members:
        age = calc_age_from_resident(m.resident_number or "")
        if age is None: age_groups["불명"] += 1
        elif age <= 29: age_groups["29이하"] += 1
        elif age <= 39: age_groups["30~39"] += 1
        elif age <= 49: age_groups["40~49"] += 1
        elif age <= 59: age_groups["50~59"] += 1
        elif age <= 64: age_groups["60~64"] += 1
        elif age <= 69: age_groups["65~69"] += 1
        else: age_groups["70이상"] += 1

    # 연식별 - 1년 단위 버킷
    veh_age: dict = {}
    for m in all_members:
        vy = ext_veh_year(m.vehicle_type or "")
        if vy:
            age_y = target_year - vy
            if age_y < 1: bkt = "1년 미만"
            elif age_y < 2: bkt = "2년 미만"
            elif age_y < 3: bkt = "3년 미만"
            elif age_y < 4: bkt = "4년 미만"
            elif age_y < 5: bkt = "5년 미만"
            elif age_y < 6: bkt = "6년 미만"
            elif age_y < 7: bkt = "7년 미만"
            elif age_y < 8: bkt = "8년 미만"
            elif age_y < 9: bkt = "9년 미만"
            elif age_y < 10: bkt = "10년 미만"
            elif age_y < 11: bkt = "11년 미만"
            elif age_y < 12: bkt = "12년 미만"
            else: bkt = "12년 이상"
            veh_age[bkt] = veh_age.get(bkt, 0) + 1

    # 지정/위탁 처리현황 (해당 월 기준 - change_date/closure_date/approval_date)
    month_transfers = [t for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)).all()
        if matches(t.process_date or t.approval_date or t.receipt_date or "")]
    month_closures = [c for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()
        if matches(c.closure_date or "")]
    month_changes = [c for c in db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()
        if matches(c.change_date or "")]
    month_new = [m for m in all_members
                 if m.registration_type == "신규" and matches(m.approval_date or "")]

    change_by_type: dict = {}
    for c in month_changes:
        ct = c.change_type or "기타"
        change_by_type[ct] = change_by_type.get(ct, 0) + 1

    admin_work = {
        "상호변경": change_by_type.get("상호변경", 0),
        "대표자변경": change_by_type.get("대표자변경", 0),
        "차량변경": change_by_type.get("구조변경", 0) + change_by_type.get("번호변경", 0),
        "주소변경": change_by_type.get("주소지변경", 0),
        "취업신고": 0,  # 확인 필요
        "퇴사신고": 0,  # 확인 필요
        "자격증재교부": None,  # 확인 필요
        "양도양수": len(month_transfers),
    }

    return {
        "period": {"year": target_year, "month": target_month},
        "member_stats": member_stats,
        "taxi_stats": taxi_stats,
        "vehicle_types": [{"type": k, "count": v}
                          for k, v in sorted(vtype_counts.items(), key=lambda x: -x[1])],
        "age_groups": age_groups,
        "vehicle_age": veh_age,
        "month_activity": {
            "new_registrations": len(month_new),
            "transfers": len(month_transfers),
            "closures": len(month_closures),
            "changes": len(month_changes),
        },
        "admin_work": admin_work,
        "education": None,   # 확인 필요
        "enforcement": None, # 확인 필요
        "month_new_list": [{"region": m.region, "vehicle_number": m.vehicle_number,
                             "name": m.name, "approval_date": m.approval_date}
                           for m in month_new[:10]],
        "month_transfer_list": [{"region": t.region, "vehicle_number": t.vehicle_number,
                                  "transferor": t.transferor, "transferee": t.transferee,
                                  "approval_date": t.approval_date}
                                for t in month_transfers[:10]],
        "month_closure_list": [{"management_number": c.management_number, "region": c.region,
                                  "vehicle_number": c.vehicle_number, "name": c.name,
                                  "closure_type": c.closure_type, "closure_date": c.closure_date}
                                for c in month_closures[:10]],
    }


@router.get("/upload-history")
async def upload_history(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows = db.query(models.UploadHistory).order_by(
        models.UploadHistory.id.desc()).limit(30).all()
    return [{"id": r.id, "file_type": r.file_type, "filename": r.filename,
             "total_count": r.total_count, "success_count": r.success_count,
             "duplicate_count": r.duplicate_count, "error_count": r.error_count,
             "uploaded_by": r.uploaded_by, "error_details": r.error_details,
             "created_at": str(r.created_at)[:16] if r.created_at else ""}
            for r in rows]
