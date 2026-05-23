from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from app.database import get_db
from app.auth import get_current_user
from app import models
from app.services.glosign_client import glosign

router = APIRouter(prefix="/api/integrations", tags=["연동"])

def _fmt(d) -> dict:
    return {
        "id": d.id, "member_id": d.member_id, "vehicle_number": d.vehicle_number or "",
        "name": d.name or "", "mobile": d.mobile or "", "region": d.region or "",
        "document_title": d.document_title or "", "glosign_document_id": d.glosign_document_id or "",
        "status": d.status or "", "requested_at": d.requested_at or "",
        "due_date": d.due_date or "", "completed_at": d.completed_at or "",
        "document_url": d.document_url or "", "memo": d.memo or "",
        "created_at": str(d.created_at)[:16] if d.created_at else "",
    }

# 연결 테스트
@router.get("/glosign/test")
async def test_glosign(_=Depends(get_current_user)):
    return await glosign.test_connection()

# 문서 목록
@router.get("/glosign/documents")
async def list_docs(
    status: str = "", name: str = "", vehicle: str = "",
    page: int = 1, size: int = 50,
    db: Session = Depends(get_db), _=Depends(get_current_user)
):
    q = db.query(models.GlosignDocument).filter(models.GlosignDocument.deleted_at.is_(None))
    if status:  q = q.filter(models.GlosignDocument.status == status)
    if name:    q = q.filter(models.GlosignDocument.name.contains(name))
    if vehicle: q = q.filter(models.GlosignDocument.vehicle_number.contains(vehicle))
    rows = q.order_by(models.GlosignDocument.created_at.desc()).all()
    items = [_fmt(r) for r in rows]
    return {"total": len(items), "items": items[(page-1)*size:page*size]}

# 수동 등록
@router.post("/glosign/documents")
async def create_doc(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    d = models.GlosignDocument(**{k: v for k, v in data.items()
                                  if hasattr(models.GlosignDocument, k) and k not in ("id","created_at","updated_at")})
    db.add(d); db.commit(); db.refresh(d)
    # 기한관리 연결
    if d.due_date:
        task = models.DeadlineTask(
            member_id=d.member_id, vehicle_number=d.vehicle_number, name=d.name,
            region=d.region, mobile=d.mobile,
            task_type="전자서명기한",
            title=f"전자서명 요청 - {d.name or ''}{(' / '+d.vehicle_number) if d.vehicle_number else ''}",
            due_date=d.due_date, status="예정", source=f"glosign:{d.id}",
        )
        db.add(task); db.commit()
    return _fmt(d)

# 상태 새로고침
@router.post("/glosign/documents/{did}/refresh")
async def refresh_doc(did: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    d = db.query(models.GlosignDocument).filter(models.GlosignDocument.id==did).first()
    if not d: raise HTTPException(404)
    if not d.glosign_document_id:
        return {"ok": False, "message": "glosign_document_id 없음"}
    result = await glosign.get_document_status(d.glosign_document_id)
    if result.get("ok"):
        raw = result["data"]
        d.raw_response = raw
        d.status = raw.get("status", d.status)
        if raw.get("completed_at"): d.completed_at = raw["completed_at"][:10]
        d.updated_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": result.get("ok"), "status": d.status, "raw": result}

# 수동 상태 수정
@router.put("/glosign/documents/{did}")
async def update_doc(did: int, data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    d = db.query(models.GlosignDocument).filter(models.GlosignDocument.id==did).first()
    if not d: raise HTTPException(404)
    for k, v in data.items():
        if hasattr(d, k) and k not in ("id","created_at"): setattr(d, k, v)
    d.updated_at = datetime.now(timezone.utc)
    db.commit(); return _fmt(d)

# Webhook (구조 준비)
@router.post("/glosign/webhook")
async def glosign_webhook(payload: dict, db: Session = Depends(get_db)):
    doc_id = payload.get("document_id") or payload.get("documentId")
    if not doc_id: return {"ok": False}
    d = db.query(models.GlosignDocument).filter(
        models.GlosignDocument.glosign_document_id == doc_id).first()
    if d:
        d.status = payload.get("status", d.status)
        if payload.get("completed_at"): d.completed_at = payload["completed_at"][:10]
        d.raw_response = payload; d.updated_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}
