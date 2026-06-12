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
    """날짜/연도 문자열에서 연도 추출.
    - 4자리 연도: 2026년, 2026.03.30, 2026-03-30 등
    - 2자리 연도: 26.03.30, 26-03-30, 26년 등
    - 미래 연도(현재+1 이상)는 None 반환
    """
    if not s:
        return None
    s = str(s).strip()
    cur_year = datetime.now().year
    cur_yy = cur_year % 100

    # 4자리 연도 우선 탐색 (19xx, 20xx) - 뒤에 숫자/한글/구분자 무관
    m4 = re.search(r'(19[0-9]{2}|20[0-9]{2})', s)
    if m4:
        y = int(m4.group())
        return y if y <= cur_year else None

    # 2자리 연도: 날짜 형식 내에서만 추출
    m2 = re.match(r'^(\d{2})\s*[.\-/년]', s)
    if m2:
        yy = int(m2.group(1))
        year = (2000 + yy) if yy <= cur_yy else (1900 + yy)
        return year if year <= cur_year else None

    return None


def _ext_year_from_date(s: str) -> Optional[int]:
    """날짜 문자열 전용 연도 추출 (관리번호 등 비날짜 문자열 제외)"""
    return _ext_year(s)


def _ext_month(s: str) -> Optional[int]:
    if not s: return None
    # "14. 7. 8." → 7
    m = re.search(r'[\.\-/]\s*(\d{1,2})\s*[\.\-/]', str(s))
    if m:
        val = int(m.group(1))
        if 1 <= val <= 12: return val
    return None


def classify_vt(vt: str, fuel: str = "") -> str:
    """차종 분류 - 구조/형태 기준. 유종(전기/EV) 절대 반환 금지.
    우선순위: 냉동>윙>사다리>렉카>픽업/덮개>밴/특수밴>탑차/내장탑>카고>기타특수>미분류
    """
    import re as _re
    v = str(vt or "").strip()
    vl = v.lower()
    # EV/전기 키워드 제거 후 구조만 판단
    vl_s = _re.sub(r"(전기|일렉트릭|electric|\bev\b|하이브리드|hybrid)", "", vl).strip()

    # 1. 냉동/냉장
    if any(k in vl for k in ["냉동","냉장","저온","보냉"]):
        return "냉동탑/냉장탑"
    # 2. 윙바디
    if any(k in vl for k in ["윙바디","윙","wing"]):
        return "윙바디"
    # 3. 사다리/고소
    if any(k in vl for k in ["사다리","사다라","사다리차","사다라차","고소","고소작업","엘리카","호룡"]):
        return "사다리/고소"
    # 4. 렉카/구난
    if any(k in vl for k in ["렉카","렉커","구난","견인"]):
        return "렉카/구난"
    # 5. 픽업/덮개 (밴보다 먼저)
    PICKUP = ["픽업","덮개","렉스턴스포츠","렉스턴 스포츠","코란도스포츠","무쏘스포츠",
              "무쏘ev","스타렉스픽업","스타리아픽업","포트로-픽업","포트로픽업"]
    if any(k in vl for k in PICKUP):
        return "픽업/덮개"
    # 6. 밴/특수밴
    VAN = ["밴","van","워크스루","미닫이","se-a2","masada","pv5",
           "스타리아","스타렉스","그랜드스타렉스","st1","t4k","master","마스터"]
    if any(k in vl for k in VAN):
        return "밴/특수밴"
    # 7. 탑차/내장탑
    TAP = ["탑차","내장탑","하이내장","플러스내장","하이탑","내장차","택배전용","내장","탑"]
    if any(k in vl_s for k in TAP):
        return "탑차/내장탑"
    # 8. 카고
    CARGO = ["포터","봉고","카고","마이티","이-마이티","이마이티","메가트럭","빅트럭",
             "1톤","1.2톤","1.4톤","2.2톤","2.5톤","3.5톤","5톤","트럭",
             "장축","초장축","일반형","더블캡","파워게이트","킹캡",
             "총중량","최대적재량","표준","기본형"]
    if any(k in vl_s for k in CARGO):
        return "카고"
    # 9. 기타특수
    SPEC = ["특장","특수","크레인","덤프","믹서","탱크","소방","암롤",
            "리프트","집게","로우베드","카캐리어","청소차","살수차","레미콘",
            "진공","고압","분뇨","음식물"]
    if any(k in vl for k in SPEC):
        return "기타특수"
    # 10. 미분류
    return "미분류"


def classify_fuel(fuel: str, vt: str = "") -> str:
    f = str(fuel or "").strip().lower()
    v = str(vt or "").lower()
    if any(k in f for k in ["전기", "ev", "일렉트릭", "electric"]):
        return "전기"
    if any(k in v for k in ["전기", "ev", "일렉트릭", "electric"]):
        return "전기"
    if any(k in f for k in ["lpg", "l.p.g", "엘피지", "엘피", "lp가스", "액화석유"]):
        return "LPG"
    if "가스" in f and "cng" not in f and "천연" not in f:
        return "LPG"
    if any(k in f for k in ["하이브리드", "hybrid"]):
        return "하이브리드"
    if any(k in f for k in ["cng", "씨엔지", "천연가스"]):
        return "CNG"
    if any(k in f for k in ["경유", "디젤", "diesel"]):
        return "경유"
    if any(k in f for k in ["휘발유", "가솔린", "gasoline"]):
        return "휘발유"
    if not f or f in ["-", "x", "none", "nan", "없음", "0"]:
        return "미분류"
    return "기타"



def _normalize_fuel_stat(fuel: str) -> Optional[str]:
    """공통 normalize_fuel 래퍼 - 빈 값이면 None 반환 (통계 집계 제외용)"""
    result = normalize_fuel(fuel)
    return result if result else None


def ext_veh_year(vt: str) -> Optional[int]:
    """차량연식 추출. 지원 형태:
    '26,포터Ⅱ' → 2026
    '26포터' → 2026 (쉼표 없음)
    '26.카고' → 2026 (점 구분)
    '2026,포터' → 2026 (4자리)
    """
    s = str(vt or '').strip()
    # 4자리 연도: 2000~2099
    m4 = re.match(r'^(20\d{2})[,.\s]', s)
    if m4:
        return int(m4.group(1))
    # 2자리 연도 + 구분자 (쉼표/점/공백)
    m2 = re.match(r'^(\d{2})[,.\s]', s)
    if m2:
        yy = int(m2.group(1))
        return 2000 + yy
    # 2자리 연도 + 바로 한글/영문 (쉼표 없음)
    m2b = re.match(r'^(\d{2})[가-힣A-Za-z]', s)
    if m2b:
        yy = int(m2b.group(1))
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
    all_lh = lh_q.all()

    import re as _re2
    _NOT_JOINED_SET = {
        'x','미가입','가입희망','가입 희망','개별등록','개별 등록',
        '개별대폐차','개별 대폐차','대폐차','신규등록','예정','신청','문의',
        '보류','확인중','기타','none','nan','-','',
    }

    def _has_val(v):
        return bool(v and str(v).strip() and str(v).strip().lower() not in ('-','x','none','nan'))

    def _is_joined(v):
        v = str(v or '').strip()
        if not v: return False
        if v.lower() in _NOT_JOINED_SET: return False
        if v.lower() in ('o','ㅇ'): return True
        if _re2.search(r'\d{2}[\.\-/]\d{1,2}[\.\-/]\d{1,2}', v): return True
        if _re2.search(r'\d{4}', v): return True
        return False

    # 가입: membership_date(가입일자) 기준 (날짜/O/o/ㅇ 만)
    joined     = sum(1 for m in all_lh if _is_joined(m.membership_date))
    individual = sum(1 for m in all_lh if m.category == "개인")
    delivery   = sum(1 for m in all_lh if m.category == "택배")

    # 취업신고: certificate_issue_date(자격증명발급일자) 기준
    delivery_employed = sum(1 for m in all_lh
                            if m.category == "택배" and _has_val(m.certificate_issue_date))

    # 차종별: fuel_type도 함께 전달
    vtype_counts: dict = {}
    for m in all_lh:
        cat = classify_vt(m.vehicle_type or "")
        vtype_counts[cat] = vtype_counts.get(cat, 0) + 1

    # 유종별: fuel_type 기준 + 차종명에 EV/전기 포함이면 전기로 판정
    fuel_counts: dict = {}
    for m in all_lh:
        fc = classify_fuel(m.fuel_type or "", m.vehicle_type or "")
        if fc and fc != '미분류':
            fuel_counts[fc] = fuel_counts.get(fc, 0) + 1

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

    # 연식별 (vehicle_type "18,포터II..." 형식) - 1년 단위 버킷, 현재 연도 동적 계산
    _VEH_BUCKETS = ["1년 미만","2년 미만","3년 미만","4년 미만","5년 미만","6년 미만",
                    "7년 미만","8년 미만","9년 미만","10년 미만","11년 미만","12년 미만","12년 이상"]
    veh_year_raw: dict = {}
    cur_year = datetime.now().year
    for m in lh_q.all():
        vy = ext_veh_year(m.vehicle_type or "")
        if vy:
            age_y = cur_year - vy
            if age_y < 0: bkt = "1년 미만"
            elif age_y < 1: bkt = "1년 미만"
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
            veh_year_raw[bkt] = veh_year_raw.get(bkt, 0) + 1
    # 1년 미만 → 12년 이상 순으로 정렬된 dict
    veh_year_dist = {bkt: veh_year_raw[bkt] for bkt in _VEH_BUCKETS if bkt in veh_year_raw}

    # 폐지/양도/이관 집계 ('폐지'는 '폐업'으로 통일)
    cl_q = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None))
    closure_by_type = {}
    for r in (cl_q.with_entities(models.Closure.closure_type, func.count())
              .group_by(models.Closure.closure_type).all()):
        ct = r[0] or "기타"
        # 폐지 → 폐업으로 통일
        if ct == '폐지':
            ct = '폐업'
        closure_by_type[ct] = closure_by_type.get(ct, 0) + r[1]

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
    # 폐지(폐업) 건수 - '폐지'로 저장된 데이터도 포함
    closed_count = closure_by_type.get("폐업", 0)
    # 이관 건수
    transfer_out_count = closure_by_type.get("이관", 0)

    allocation = {
        "협회가입": joined,
        "양도": transfer_count,
        "타도(이관)": transfer_out_count,
        "폐업": closed_count,
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
            "delivery_not_employed": delivery - delivery_employed,  # 프론트 호환
            "not_joined": total - joined,
        },
        "vehicle_types": [{"type": k, "count": v}
                          for k, v in sorted(vtype_counts.items(), key=lambda x: -x[1])
                          if k != "전기차"],
        "fuel_types": [{"type": k, "count": v}
                       for k, v in sorted(fuel_counts.items(), key=lambda x: -x[1])],
        "debug_version": "vt-fix-20260520-1800",
        "age_groups": age_groups,
        "vehicle_age": veh_year_dist,
        "closure_by_type": closure_by_type,
        "allocation": allocation,
    }


@router.get("/activity-by-year")
async def activity_by_year(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """연도별 집계 (문서 확정 기준):
    신규: 관리번호 신YY-* 기준
    양도양수: 관리번호 양YY-* 기준
    폐업/양도/이관: 접수일자(receipt_date) 기준, 없으면 closure_date
    변경: change_date 기준
    """
    cur_year = datetime.now().year
    cur_yy   = cur_year % 100
    min_year = cur_year - 9
    result: dict = {}

    def _r(y):
        result.setdefault(y, {"year": y, "new": 0, "transfer": 0, "closure": 0, "change": 0})
        return result[y]

    def _mgmt_yy(prefix, mgmt):
        m = re.match(rf'^{prefix}(\d{{2}})[-]', (mgmt or '').strip())
        if not m: return None
        yy = int(m.group(1))
        return 2000 + yy if yy <= cur_yy else 1900 + yy

    # 1. 신규: 관리번호 신YY-* (status/날짜 무관)
    for m in db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
    ).all():
        y = _mgmt_yy("신", m.management_number)
        if y and min_year <= y <= cur_year:
            _r(y)["new"] += 1

    # 2. 양도양수: 관리번호 양YY-* (날짜 무관)
    for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
    ).all():
        y = _mgmt_yy("양", t.management_number)
        if y and min_year <= y <= cur_year:
            _r(y)["transfer"] += 1

    # 3. 폐업/양도/이관: 접수일자(receipt_date) 기준, 없으면 closure_date
    #    이전자료+신규자료 합산
    for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
    ).all():
        date_str = (c.receipt_date or c.closure_date or "").strip()
        y = _ext_year(date_str)
        if y and min_year <= y <= cur_year:
            _r(y)["closure"] += 1

    # 4. 변경: change_date 기준
    for c in db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
    ).all():
        y = _ext_year(c.change_date or "")
        if y and min_year <= y <= cur_year:
            _r(y)["change"] += 1

    for yr in range(min_year, cur_year + 1):
        result.setdefault(yr, {"year": yr, "new": 0, "transfer": 0, "closure": 0, "change": 0})

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
    """월례보고서 자동 계산 - 선택한 연도/월 기준 (해당 월에 발생한 데이터만 집계)"""
    now = datetime.now()
    target_year = year or now.year
    target_month = month or now.month

    def _ym(date_str: str) -> tuple:
        if not date_str: return None, None
        s = str(date_str).strip()
        m = re.search(r'(19[0-9]{2}|20[0-9]{2})\s*[\.\-/]\s*(\d{1,2})', s)
        if m: return int(m.group(1)), int(m.group(2))
        m = re.match(r'^(\d{2})\s*[\.\-/]\s*(\d{1,2})', s)
        if m:
            yy = int(m.group(1))
            return (2000+yy if yy<=30 else 1900+yy), int(m.group(2))
        return None, None

    def matches(date_str: str) -> bool:
        y, mo = _ym(date_str)
        return y == target_year and mo == target_month

    lh_q = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    )
    all_members = lh_q.all()
    total = len(all_members)
    individual = sum(1 for m in all_members if m.category == "개인")
    delivery = sum(1 for m in all_members if m.category == "택배")

    # 가입 판정: 날짜 또는 O/o/ㅇ 만 협회가입자로 인정
    import re as _re
    _NOT_JOINED = {
        'x','X','미가입','가입희망','가입 희망','개별등록','개별 등록',
        '개별대폐차','개별 대폐차','대폐차','신규등록','예정','신청','문의',
        '보류','확인중','기타','none','nan','-','',
    }

    def _is_joined(v):
        v = str(v or '').strip()
        if not v: return False
        vl = v.lower()
        if vl in {x.lower() for x in _NOT_JOINED}: return False
        if vl in ('o','ㅇ'): return True  # 오래된 가입자 표시
        # 날짜 패턴: 4자리 연도 또는 2자리 연도
        if _re.search(r'\d{2}[\.\-/]\d{1,2}[\.\-/]\d{1,2}', v): return True
        if _re.search(r'\d{4}', v): return True  # 연도만 있는 경우
        return False

    # 가입: membership_date(가입일자) 기준
    joined     = sum(1 for m in all_members if _is_joined(m.membership_date))
    ind_joined = sum(1 for m in all_members if m.category == "개인" and _is_joined(m.membership_date))
    del_joined = sum(1 for m in all_members if m.category == "택배" and _is_joined(m.membership_date))

    # 해당 월 신규가입 / 미가입발생
    month_joined     = sum(1 for m in all_members if matches(m.membership_date or ''))
    month_not_joined = sum(1 for m in all_members
                           if not _has_val(m.membership_date) and matches(m.approval_date or ''))

    # 취업신고: certificate_issue_date(자격증명발급일자) 기준
    cert_del = sum(1 for m in all_members if m.category=="택배" and _has_val(m.certificate_issue_date))
    cert_ind = sum(1 for m in all_members if m.category=="개인" and _has_val(m.certificate_issue_date))

    member_stats = {
        "total": total, "individual": individual, "delivery": delivery,
        "joined": joined, "not_joined": total - joined,
        "ind_joined": ind_joined, "ind_not_joined": individual - ind_joined,
        "del_joined": del_joined, "del_not_joined": delivery - del_joined,
        "month_joined": month_joined, "month_not_joined": month_not_joined,
    }

    taxi_stats = {
        "total_delivery": delivery,
        "employed": cert_del,
        "unemployed": delivery - cert_del,
        "individual_employed": cert_ind,
        "individual_not_employed": individual - cert_ind,
    }

    vtype_counts: dict = {}
    for m in all_members:
        cat = classify_vt(m.vehicle_type or "")
        vtype_counts[cat] = vtype_counts.get(cat, 0) + 1

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

    # 연식별 - 현재 연도(target_year) 기준, 1년 미만→12년 이상 순서로 정렬
    _VEH_BUCKETS = ["1년 미만","2년 미만","3년 미만","4년 미만","5년 미만","6년 미만",
                    "7년 미만","8년 미만","9년 미만","10년 미만","11년 미만","12년 미만","12년 이상"]
    veh_age_raw: dict = {}
    for m in all_members:
        vy = ext_veh_year(m.vehicle_type or "")
        if vy:
            age_y = target_year - vy
            if age_y < 0: bkt = "1년 미만"
            elif age_y < 1: bkt = "1년 미만"
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
            veh_age_raw[bkt] = veh_age_raw.get(bkt, 0) + 1
    # 1년 미만 → 12년 이상 순서 정렬
    veh_age = {bkt: veh_age_raw[bkt] for bkt in _VEH_BUCKETS if bkt in veh_age_raw}

    # 해당 월 신규/양도/폐업/변경
    # ── 해당 월 신규: 관리번호 신YY-* 이고 인가일자가 해당 월인 건
    cur_year = datetime.now().year
    cur_yy = cur_year % 100
    def _mgmt_year_match(mgmt, prefix, t_year):
        m2 = re.match(rf'^{prefix}(\d{{2}})[-]', (mgmt or '').strip())
        if not m2: return False
        yy = int(m2.group(1))
        y = 2000 + yy if yy <= cur_yy else 1900 + yy
        return y == t_year

    # 신규: 관리번호 신YY-* 이고 approval_date 해당 월
    month_new = [m for m in db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
    ).all() if _mgmt_year_match(m.management_number, "신", target_year)
              and matches(m.approval_date or '')]

    # 양도양수: 관리번호 양YY-* 이고 receipt_date 해당 월
    month_transfers = [t for t in db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
    ).all() if _mgmt_year_match(t.management_number, "양", target_year)
             and matches(t.receipt_date or '')]

    # 폐업/양도/이관: 접수일자(receipt_date) 기준, 이전+신규 합산
    month_closures = [c for c in db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()
        if matches((c.receipt_date or c.closure_date or ''))]

    month_changes = [c for c in db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()
        if matches(c.change_date or c.receipt_date or '')]

    month_changes_auto = []  # 구분용 (집계에는 포함)

    change_by_type: dict = {}
    for c in month_changes:
        ct = c.change_type or "기타"
        change_by_type[ct] = change_by_type.get(ct, 0) + 1

    admin_work = {
        # 변경등록대장 기반
        "상호변경":    change_by_type.get("상호변경", 0),
        "대표자변경":   change_by_type.get("대표자변경", 0),
        "차량변경":    change_by_type.get("구조변경", 0) + change_by_type.get("번호변경", 0),
        "주소변경":    change_by_type.get("주소지변경", 0),
        "자격증재교부":  change_by_type.get("자격증재교부", 0) or change_by_type.get("자격재교부", 0),
        "이전전출":    change_by_type.get("이전전출", 0) + change_by_type.get("등록이관", 0),
        "전속업체변경":  change_by_type.get("전속계약 업체변경", 0),
        # 신규등록대장 = 취업신고
        "취업신고":    len(month_new),
        # 폐업현황 중 폐-* (폐업)만 퇴사신고
        "퇴사신고":    sum(1 for c in month_closures
                       if (c.closure_type or '').replace('폐지','폐업') == '폐업'
                       or (c.management_number or '').startswith('폐-')),
        # 양도양수대장 기준
        "양도양수":    len(month_transfers),
        # 이관 별도 표시
        "이관":       sum(1 for c in month_closures
                       if (c.management_number or '').startswith('이-')
                       or (c.closure_type or '') == '이관'),
        # 전체 변경 건수
        "_변경등록전체":   len(month_changes),
        "_자동기록제외":   len(month_changes_auto),
        "_자동기록유형별": {ct: sum(1 for c in month_changes_auto if (c.change_type or '') == ct)
                        for ct in set(c.change_type or '기타' for c in month_changes_auto)},
        "_변경유형별":    change_by_type,
    }

    # 관리번호 자연정렬 (숫자 기준 내림차순)
    from app.excel_utils import mgmt_sort_key
    def _sort_desc(lst, key_fn):
        return sorted(lst, key=key_fn, reverse=True)

    # 신규 목록: 관리번호 내림차순
    month_new_sorted = _sort_desc(month_new,
        lambda m: mgmt_sort_key(m.management_number or ''))

    # 폐업 목록: 관리번호 내림차순, closure_type 폐지→폐업 통일
    month_closures_sorted = _sort_desc(month_closures,
        lambda c: mgmt_sort_key(c.management_number or ''))

    closure_list = []
    for c in month_closures_sorted:
        ct = c.closure_type or ''
        if ct == '폐지': ct = '폐업'
        data_label = "이전자료" if c.data_type == "이전자료" else "신규자료"
        closure_list.append({
            "management_number": c.management_number, "region": c.region,
            "vehicle_number": c.vehicle_number, "name": c.name,
            "closure_type": ct,
            "receipt_date": c.receipt_date or "",
            "closure_date": c.closure_date or "",
            "data_type": data_label,
        })

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
        "education": None,
        "enforcement": None,
        "month_new_list": [{"management_number": m.management_number,
                             "region": m.region, "vehicle_number": m.vehicle_number,
                             "name": m.name, "approval_date": m.approval_date,
                             "category": m.category}
                           for m in month_new_sorted],
        "month_transfer_list": [{"management_number": t.management_number,
                                  "region": t.region, "vehicle_number": t.vehicle_number,
                                  "transferor": t.transferor, "transferee": t.transferee,
                                  "receipt_date": t.receipt_date}
                                for t in _sort_desc(month_transfers,
                                    lambda t: mgmt_sort_key(t.management_number or ''))],
        "month_closure_list": closure_list,
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


@router.get("/debug-new-count")
async def debug_new_count(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """신규 집계 디버그: 실제 DB 수치 확인용"""
    from datetime import datetime as dt
    cur_year = dt.now().year
    min_year = cur_year - 9

    # 신26-* 전체
    all_shin26 = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
    ).count()

    # approval_date 없는 신26-*
    no_date = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
    ).filter(
        (models.LicenseHolder.approval_date.is_(None)) |
        (models.LicenseHolder.approval_date == "")
    ).count()

    # activity_by_year 신규 로직 동일하게 재현 + 파싱 실패 건 수집
    rows = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
    ).all()

    year_cnt = {}
    parse_failed = []  # 인가일자가 있는데 2026으로 파싱 안 되는 건
    for m in rows:
        y = _ext_year(m.approval_date or "")
        if y == 2026:
            year_cnt[2026] = year_cnt.get(2026, 0) + 1
        else:
            parse_failed.append({
                "management_number": m.management_number,
                "approval_date": m.approval_date,
                "parsed_year": y,
            })

    # 관리번호 목록 (마지막 20개)
    from sqlalchemy import func
    mgmt_rows = db.query(models.LicenseHolder.management_number).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
    ).all()
    from app.excel_utils import mgmt_sort_key
    mgmt_list = sorted([r[0] for r in mgmt_rows], key=mgmt_sort_key)

    return {
        "신26_전체수": all_shin26,
        "신26_인가일자없는수": no_date,
        "신26_인가일자2026으로집계되는수": year_cnt.get(2026, 0),
        "신26_파싱실패건": parse_failed,  # 인가일자가 있는데 2026으로 안 읽히는 건
        "신26_마지막20개": mgmt_list[-20:],
    }


@router.get("/stat-list")
async def stat_list(
    stat_type: str = Query(...),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    """대시보드 통계 클릭 시 대상자 목록"""
    def _has_val(v):
        return bool(v and str(v).strip() and str(v).strip().lower() not in ('-','x','none','nan'))

    base = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    )

    if stat_type == "joined":
        members = [m for m in base.all() if _has_val(m.membership_date)]
    elif stat_type == "not_joined":
        members = [m for m in base.all() if not _has_val(m.membership_date)]
    elif stat_type == "delivery_employed":
        members = [m for m in base.filter(models.LicenseHolder.category=="택배").all()
                   if _has_val(m.certificate_issue_date)]
    elif stat_type == "delivery_not_employed":
        members = [m for m in base.filter(models.LicenseHolder.category=="택배").all()
                   if not _has_val(m.certificate_issue_date)]
    else:
        members = []

    from app.excel_utils import mgmt_sort_key
    members.sort(key=lambda m: mgmt_sort_key(m.management_number or ''), reverse=True)

    return {
        "total": len(members),
        "stat_type": stat_type,
        "items": [{
            "management_number": m.management_number or "",
            "region": m.region or "",
            "vehicle_number": m.vehicle_number or "",
            "name": m.name or "",
            "category": m.category or "",
            "membership_date": m.membership_date or "",
            "membership_status": m.membership_status or "",
            "certificate_issue_date": m.certificate_issue_date or "",
            "certificate_number": m.certificate_number or "",
            "approval_date": m.approval_date or "",
        } for m in members[:500]],  # 최대 500명
    }


@router.get("/vtype-list")
async def vtype_list(
    category: str = Query(...),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    """차종별 클릭 시 해당 차량 목록"""
    members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    ).all()

    items = []
    for m in members:
        cat = classify_vt(m.vehicle_type or "")
        if cat == category:
            fuel_cat = classify_fuel(m.fuel_type or "", m.vehicle_type or "")
            items.append({
                "region": m.region or "",
                "vehicle_number": m.vehicle_number or "",
                "name": m.name or "",
                "vehicle_type_raw": m.vehicle_type or "",
                "fuel_type": m.fuel_type or "",
                "vehicle_category": cat,
                "fuel_category": fuel_cat,
                "management_number": m.management_number or "",
            })

    from app.excel_utils import mgmt_sort_key
    items.sort(key=lambda x: mgmt_sort_key(x["management_number"]), reverse=True)

    return {"category": category, "total": len(items), "items": items[:200]}


@router.get("/year-detail")
async def year_detail(
    year: int = Query(...),
    category: str = Query(...),  # new/transfer/closure/change
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    """연도별 변동 숫자 클릭 시 상세 목록"""
    import re as _re
    from app.excel_utils import mgmt_sort_key

    cur_yy = year % 100
    yy = str(cur_yy).zfill(2)

    if category == "new":
        rows = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.deleted_at.is_(None),
            models.LicenseHolder.management_number.like(f"신{yy}-%"),
        ).all()
        rows.sort(key=lambda r: mgmt_sort_key(r.management_number or ""), reverse=True)
        return {"total": len(rows), "year": year, "category": "신규",
                "items": [{"management_number": r.management_number, "region": r.region,
                            "vehicle_number": r.vehicle_number, "name": r.name,
                            "approval_date": r.approval_date, "status": r.status}
                           for r in rows]}

    elif category == "transfer":
        rows = db.query(models.TransferLedger).filter(
            models.TransferLedger.deleted_at.is_(None),
            models.TransferLedger.management_number.like(f"양{yy}-%"),
        ).all()
        rows.sort(key=lambda r: mgmt_sort_key(r.management_number or ""), reverse=True)
        return {"total": len(rows), "year": year, "category": "양도양수",
                "items": [{"management_number": r.management_number, "region": r.region,
                            "vehicle_number": r.vehicle_number, "transferor": r.transferor,
                            "transferee": r.transferee, "receipt_date": r.receipt_date}
                           for r in rows]}

    elif category == "closure":
        # 접수일자 기준, 이전+신규 합산
        rows = db.query(models.Closure).filter(
            models.Closure.deleted_at.is_(None),
        ).all()
        result = []
        for r in rows:
            date_str = (r.receipt_date or r.closure_date or "").strip()
            y = _ext_year(date_str)
            if y == year:
                result.append(r)
        result.sort(key=lambda r: mgmt_sort_key(r.management_number or ""), reverse=True)
        return {"total": len(result), "year": year, "category": "폐업/양도/이관",
                "items": [{"management_number": r.management_number,
                            "closure_type": r.closure_type, "data_type": r.data_type,
                            "region": r.region, "vehicle_number": r.vehicle_number,
                            "name": r.name, "receipt_date": r.receipt_date,
                            "closure_date": r.closure_date}
                           for r in result]}

    elif category == "change":
        rows = db.query(models.ChangeHistory).filter(
            models.ChangeHistory.deleted_at.is_(None),
        ).all()
        result = [r for r in rows if _ext_year(r.change_date or "") == year]
        return {"total": len(result), "year": year, "category": "변경",
                "items": [{"region": r.region, "vehicle_number": r.vehicle_number,
                            "name": r.name, "change_type": r.change_type,
                            "change_date": r.change_date, "after_value": r.after_value}
                           for r in result[:200]]}

    return {"error": "unknown category"}
