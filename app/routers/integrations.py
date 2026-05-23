from fastapi import APIRouter, Depends, HTTPException, Request
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
        "contract_method": getattr(d, "contract_method", "") or "비대면",
        "updated_at": str(d.updated_at)[:16] if d.updated_at else "",
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

# Webhook (글로싸인 서버 → 우리 서버)
@router.post("/glosign/webhook")
async def glosign_webhook(
    request: Request,
    secret: str = "",
    db: Session = Depends(get_db)
):
    """글로싸인 Webhook 수신 (토큰 불필요).
    payload: {"contract":"c123...","hook_type":"contract","state":"complete"}
    secret 검증(선택): ?secret=... 또는 X-Webhook-Secret 헤더
    """
    import os as _os
    from datetime import date as _date, datetime as _dt, timezone as _tz

    # raw body → JSON 파싱 (글로싸인이 보내는 그대로)
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "JSON 파싱 실패"}

    # Secret 검증 (query param 또는 헤더)
    webhook_secret = _os.getenv("GLOSIGN_WEBHOOK_SECRET", "")
    if webhook_secret:
        header_secret = request.headers.get("X-Webhook-Secret", "")
        received = secret or header_secret
        if received != webhook_secret:
            from fastapi import HTTPException as _Ex
            raise _Ex(403, "Webhook secret 불일치")

    contract_id = (payload.get("contract") or
                   payload.get("document_id") or
                   payload.get("documentId") or "")
    state = (payload.get("state") or payload.get("status") or "").lower()

    STATE_MAP = {
        "complete": "완료", "completed": "완료",
        "reject":   "거절", "rejected":  "거절",
        "cancel":   "취소", "cancelled": "취소",
        "expired":  "만료",
        "waiting":  "서명대기", "progress": "서명대기",
        "partial":  "일부완료",
    }
    new_status = STATE_MAP.get(state, "오류")

    # contract_id로 문서 매칭
    doc = None
    if contract_id:
        doc = db.query(models.GlosignDocument).filter(
            (models.GlosignDocument.glosign_document_id == contract_id) |
            (models.GlosignDocument.glosign_request_id  == contract_id)
        ).first()

    if doc:
        doc.status       = new_status
        doc.raw_response = payload
        doc.updated_at   = _dt.now(_tz.utc)
        if new_status == "완료":
            doc.completed_at = str(_date.today())
            # 연결된 deadline_tasks 완료 처리
            tasks = db.query(models.DeadlineTask).filter(
                models.DeadlineTask.source == f"glosign:{doc.id}",
                models.DeadlineTask.deleted_at.is_(None),
            ).all()
            for t in tasks:
                t.status       = "완료"
                t.completed_at = str(_date.today())
        db.commit()
        return {"ok": True, "matched": True,
                "document_id": doc.id, "new_status": new_status}

    # 매칭 실패: raw payload 보존 후 정상 응답 (서버 죽지 않음)
    try:
        unmatched = models.GlosignDocument(
            glosign_document_id=contract_id or "unknown",
            document_title="Webhook 수신 (매칭 실패)",
            status="오류",
            memo=f"Webhook 매칭 실패 state={state}",
            raw_response=payload,
        )
        db.add(unmatched)
        db.commit()
    except Exception:
        pass  # 저장 실패해도 200 반환

    return {"ok": True, "matched": False,
            "contract": contract_id, "state": state,
            "note": "매칭 문서 없음 - raw payload 보존"}


@router.delete("/glosign/documents/{did}")
async def delete_glosign_doc(did: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    d = db.query(models.GlosignDocument).filter(models.GlosignDocument.id==did).first()
    if not d: raise HTTPException(404)
    from datetime import datetime as _dt, timezone as _tz
    d.deleted_at = _dt.now(_tz.utc)
    db.commit()
    return {"ok": True}


@router.get("/glosign/webhook/test")
async def test_webhook_endpoint(_=Depends(get_current_user)):
    """Webhook endpoint 정상 여부 확인용"""
    return {
        "endpoint": "POST /api/integrations/glosign/webhook",
        "webhook_url": "https://member-management-production.up.railway.app/api/integrations/glosign/webhook",
        "content_type": "application/json",
        "auth_required": False,
        "secret_support": "?secret=... 또는 X-Webhook-Secret 헤더",
        "example_payload": {
            "contract": "c123456789abcdefghijklmn",
            "hook_type": "contract",
            "state": "complete"
        },
        "state_mapping": {
            "complete": "완료", "reject": "거절",
            "cancel": "취소", "expired": "만료",
            "waiting": "서명대기"
        }
    }
