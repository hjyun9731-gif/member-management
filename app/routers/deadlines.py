from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, date, timezone
from app.database import get_db
from app.auth import get_current_user
from app import models

router = APIRouter(prefix="/api/deadlines", tags=["deadlines"])


# Calendar holiday data.  Solar holidays are kept here and lunar holidays are
# calculated at runtime so the calendar can move across years without a static table.
# Current rules include Labour Day / Constitution Day from 2026 onward.
KNOWN_ELECTION_HOLIDAYS = {
    "2020-04-15": "국회의원선거",
    "2022-03-09": "대통령선거",
    "2022-06-01": "지방선거",
    "2024-04-10": "국회의원선거",
    "2025-06-03": "대통령선거",
    "2026-06-03": "지방선거",
}

def _korean_public_holidays(year: int) -> list[dict]:
    from datetime import timedelta

    base: dict[date, list[str]] = {}
    substitutes: dict[date, list[str]] = {}

    def add(day: date, name: str):
        base.setdefault(day, []).append(name)

    # Solar public holidays.
    add(date(year, 1, 1), "신정")
    add(date(year, 3, 1), "삼일절")
    if year >= 2026:
        add(date(year, 5, 1), "노동절")
    add(date(year, 5, 5), "어린이날")
    add(date(year, 6, 6), "현충일")
    if year >= 2026:
        add(date(year, 7, 17), "제헌절")
    add(date(year, 8, 15), "광복절")
    add(date(year, 10, 3), "개천절")
    add(date(year, 10, 9), "한글날")
    add(date(year, 12, 25), "성탄절")

    # Lunar holidays.  If the lunar helper is unavailable for an out-of-range
    # year, fixed-date holidays still render instead of failing the calendar.
    seollal_group: list[date] = []
    chuseok_group: list[date] = []
    buddha: date | None = None
    try:
        from korean_lunar_calendar import KoreanLunarCalendar

        def lunar_to_solar(y: int, m: int, d: int) -> date:
            cal = KoreanLunarCalendar()
            if not cal.setLunarDate(y, m, d, False):
                raise ValueError("lunar date out of range")
            return date.fromisoformat(cal.SolarIsoFormat())

        lunar_new_year = lunar_to_solar(year, 1, 1)
        seollal_group = [lunar_new_year - timedelta(days=1), lunar_new_year, lunar_new_year + timedelta(days=1)]
        for day, name in zip(seollal_group, ("설날 연휴", "설날", "설날 연휴")):
            add(day, name)

        buddha = lunar_to_solar(year, 4, 8)
        add(buddha, "부처님오신날")

        chuseok = lunar_to_solar(year, 8, 15)
        chuseok_group = [chuseok - timedelta(days=1), chuseok, chuseok + timedelta(days=1)]
        for day, name in zip(chuseok_group, ("추석 연휴", "추석", "추석 연휴")):
            add(day, name)
    except Exception:
        pass

    for iso, name in KNOWN_ELECTION_HOLIDAYS.items():
        if iso.startswith(f"{year:04d}-"):
            add(date.fromisoformat(iso), name)

    def next_non_holiday(after: date) -> date:
        candidate = after + timedelta(days=1)
        while candidate.weekday() >= 5 or candidate in base or candidate in substitutes:
            candidate += timedelta(days=1)
        return candidate

    def add_substitute(after: date, source_name: str):
        day = next_non_holiday(after)
        substitutes.setdefault(day, []).append(f"{source_name} 대체공휴일")

    # Substitute-holiday rules. New Year's Day and Memorial Day do not create
    # a substitute simply because they fall on a weekend.
    weekend_eligible: list[tuple[date, str]] = []
    if year >= 2021:
        weekend_eligible += [
            (date(year, 3, 1), "삼일절"),
            (date(year, 8, 15), "광복절"),
            (date(year, 10, 3), "개천절"),
            (date(year, 10, 9), "한글날"),
        ]
    if year >= 2026:
        weekend_eligible += [
            (date(year, 5, 1), "노동절"),
            (date(year, 7, 17), "제헌절"),
        ]
    # Children's Day has long had a substitute-holiday rule.
    weekend_eligible.append((date(year, 5, 5), "어린이날"))
    if year >= 2023:
        if buddha:
            weekend_eligible.append((buddha, "부처님오신날"))
        weekend_eligible.append((date(year, 12, 25), "성탄절"))

    for day, name in weekend_eligible:
        # Weekend overlap or another public holiday on the same weekday.
        overlaps = len(base.get(day, [])) > 1
        if day.weekday() >= 5 or overlaps:
            add_substitute(day, name)

    # Seollal / Chuseok: Sunday (not Saturday alone) or another public-holiday
    # overlap produces the next non-holiday substitute day.
    for group, name in ((seollal_group, "설날"), (chuseok_group, "추석")):
        if group and (any(d.weekday() == 6 for d in group) or any(len(base.get(d, [])) > 1 for d in group)):
            add_substitute(max(group), name)

    merged: dict[date, list[str]] = {}
    for day, names in base.items():
        merged.setdefault(day, []).extend(names)
    for day, names in substitutes.items():
        merged.setdefault(day, []).extend(names)

    return [
        {"date": day.isoformat(), "name": " · ".join(dict.fromkeys(names))}
        for day, names in sorted(merged.items())
        if day.year == year
    ]

TASK_TYPES = ["휴업만료","대폐차기한","대폐차기간연장","차량출고지연확인서","보완서류제출",
              "자격증명발급대기","공문회신기한","시청확인요청","전자서명기한","기타"]

def _calc_dday(due: str) -> int | None:
    if not due: return None
    try:
        d = datetime.strptime(due[:10], "%Y-%m-%d").date()
        return (d - date.today()).days
    except: return None

def _dday_base(task) -> str:
    # 단일 일정은 due_date, 기간 일정은 start_date를 D-day/알림 기준으로 사용한다.
    # 기존 기한 데이터(start_date==due_date)는 그대로 동작한다.
    start = (task.start_date or "")[:10]
    due = (task.due_date or "")[:10]
    if start and due and start != due:
        return start
    return due or start

def _auto_status(task) -> str:
    if task.status in ("완료",): return task.status
    dd = _calc_dday(_dday_base(task))
    if dd is None: return task.status
    # 기간 일정(예: 8/5~8/10 휴가)은 일반 기한처럼 종료 후 '기한초과'로 만들지 않는다.
    start = (task.start_date or "")[:10]
    due = (task.due_date or "")[:10]
    if start and due and start != due:
        return task.status or "예정"
    if dd < 0: return "기한초과"
    return task.status or "예정"

def _fmt(t) -> dict:
    dd = _calc_dday(_dday_base(t))
    st = _auto_status(t)
    return {
        "id": t.id, "member_id": t.member_id, "license_holder_id": t.license_holder_id,
        "vehicle_number": t.vehicle_number or "", "name": t.name or "",
        "region": t.region or "", "mobile": t.mobile or "",
        "task_type": t.task_type or "", "title": t.title or "",
        "content": t.content or "", "start_date": t.start_date or "",
        "due_date": t.due_date or "", "reminder_days": t.reminder_days or "7,3,0",
        "event_color": t.event_color or "#5B6CF0",
        "status": st, "dday": dd,
        "dday_label": (f"D{dd:+d}" if dd is not None else "-") if dd != 0 else "D-day",
        "completed_at": t.completed_at or "", "extended_from": t.extended_from or "",
        "extended_to": t.extended_to or "", "extension_reason": t.extension_reason or "",
        "memo": t.memo or "", "manager": t.manager or "", "source": t.source or "",
        "created_at": str(t.created_at)[:16] if t.created_at else "",
    }

def _base_q(db):
    return db.query(models.DeadlineTask).filter(models.DeadlineTask.deleted_at.is_(None))

@router.get("/summary")
async def deadline_summary(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows = _base_q(db).all()
    today_n = d3 = d7 = over = done = 0
    for t in rows:
        dd = _calc_dday(_dday_base(t))
        st = _auto_status(t)
        if st == "완료": done += 1; continue
        if dd is None: continue
        if st == "기한초과": over += 1
        elif dd == 0: today_n += 1
        elif dd <= 3: d3 += 1
        elif dd <= 7: d7 += 1
    return {"오늘기한": today_n, "3일이내": d3, "7일이내": d7, "기한초과": over, "완료": done}

@router.get("")
async def list_deadlines(
    filter: str = "전체", task_type: str = "",
    vehicle: str = "", name: str = "", region: str = "",
    page: int = 1, size: int = 50,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = _base_q(db)
    if task_type: q = q.filter(models.DeadlineTask.task_type == task_type)
    if vehicle:   q = q.filter(models.DeadlineTask.vehicle_number.contains(vehicle))
    if name:      q = q.filter(models.DeadlineTask.name.contains(name))
    if region:    q = q.filter(models.DeadlineTask.region == region)
    rows = q.order_by(models.DeadlineTask.due_date.asc()).all()

    today = date.today()
    def _pass(t):
        dd = _calc_dday(_dday_base(t))
        st = _auto_status(t)
        if filter == "전체": return True
        if filter == "완료": return st == "완료"
        if filter == "기한초과": return st == "기한초과"
        if filter == "오늘": return dd == 0 and st != "완료"
        if filter == "3일이내": return dd is not None and 0 <= dd <= 3 and st != "완료"
        if filter == "7일이내": return dd is not None and 0 <= dd <= 7 and st != "완료"
        return True

    items = [_fmt(t) for t in rows if _pass(t)]
    total = len(items)
    return {"total": total, "items": items[(page-1)*size : page*size]}


@router.get("/holidays")
async def calendar_holidays(
    year: int = Query(..., ge=1900, le=2100),
    _=Depends(get_current_user),
):
    return {"year": year, "items": _korean_public_holidays(year)}

@router.get("/{tid}")
async def get_deadline(tid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = _base_q(db).filter(models.DeadlineTask.id == tid).first()
    if not t: raise HTTPException(404)
    return _fmt(t)

@router.post("")
async def create_deadline(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = models.DeadlineTask(**{k: v for k, v in data.items()
                               if hasattr(models.DeadlineTask, k) and k not in ("id","created_at","updated_at")})
    db.add(t); db.commit(); db.refresh(t)
    return _fmt(t)

@router.put("/{tid}")
async def update_deadline(tid: int, data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = _base_q(db).filter(models.DeadlineTask.id == tid).first()
    if not t: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(t, k) and k not in ("id","created_at"): setattr(t, k, v)
    t.updated_at = datetime.now(timezone.utc)
    db.commit(); return _fmt(t)

@router.post("/{tid}/complete")
async def complete_deadline(tid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = _base_q(db).filter(models.DeadlineTask.id == tid).first()
    if not t: raise HTTPException(404)
    t.status = "완료"; t.completed_at = str(date.today())
    db.commit(); return _fmt(t)

@router.post("/{tid}/extend")
async def extend_deadline(tid: int, data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = _base_q(db).filter(models.DeadlineTask.id == tid).first()
    if not t: raise HTTPException(404)
    t.extended_from = t.due_date
    t.extended_to   = data.get("extended_to", "")
    t.extension_reason = data.get("reason", "")
    t.due_date = t.extended_to
    t.status = "연장"
    db.commit(); return _fmt(t)

@router.delete("/{tid}")
async def delete_deadline(tid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = _base_q(db).filter(models.DeadlineTask.id == tid).first()
    if not t: raise HTTPException(404)
    t.deleted_at = datetime.now(timezone.utc)
    db.commit(); return {"ok": True}

@router.get("/member/{member_id}")
async def member_deadlines(member_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    rows = _base_q(db).filter(models.DeadlineTask.member_id == member_id).all()
    return {"total": len(rows), "items": [_fmt(t) for t in rows]}
