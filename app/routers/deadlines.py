from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, date, timezone
from app.database import get_db
from app.auth import get_current_user
from app import models

router = APIRouter(prefix="/api/deadlines", tags=["deadlines"])

TASK_TYPES = ["휴업만료","대폐차기한","대폐차기간연장","차량출고지연확인서","보완서류제출",
              "자격증명발급대기","공문회신기한","시청확인요청","전자서명기한","기타"]

def _calc_dday(due: str) -> int | None:
    if not due: return None
    try:
        d = datetime.strptime(due[:10], "%Y-%m-%d").date()
        return (d - date.today()).days
    except: return None

def _auto_status(task) -> str:
    if task.status in ("완료",): return task.status
    dd = _calc_dday(task.due_date)
    if dd is None: return task.status
    if dd < 0: return "기한초과"
    return task.status or "예정"

def _fmt(t) -> dict:
    dd = _calc_dday(t.due_date)
    st = _auto_status(t)
    return {
        "id": t.id, "member_id": t.member_id, "license_holder_id": t.license_holder_id,
        "vehicle_number": t.vehicle_number or "", "name": t.name or "",
        "region": t.region or "", "mobile": t.mobile or "",
        "task_type": t.task_type or "", "title": t.title or "",
        "content": t.content or "", "start_date": t.start_date or "",
        "due_date": t.due_date or "", "reminder_days": t.reminder_days or "7,3,0",
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
        dd = _calc_dday(t.due_date)
        st = _auto_status(t)
        if st == "완료": done += 1; continue
        if dd is None: continue
        if dd < 0: over += 1
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
        dd = _calc_dday(t.due_date)
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
