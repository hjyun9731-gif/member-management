"""미수금 정정(자격증명 미발급 관리비) 관리자 API. 미리보기/보고서는 읽기 전용, 적용·되돌리기는 관리자 전용 + 확인절차."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import receivable_adjustments as svc
from app.auth import admin_for_writes, require_admin
from app.database import get_db
from app.receivables_models import ReceivableAdjustmentBatch
from app.routers.receivables import _ensure_receivables_schema_ready

router = APIRouter(prefix="/api/receivables/adjustments", tags=["receivable-adjustments"], dependencies=[Depends(admin_for_writes)])


def _ids(exclude: str) -> list[int]:
    try:
        return [int(x) for x in exclude.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, "exclude 는 쉼표로 구분한 회원 ID 숫자여야 합니다.")


def _actor(u) -> str:
    return getattr(u, "username", None) or "admin"


class ApplyIn(BaseModel):
    plan_digest: str = Field(..., min_length=16, max_length=64)
    confirm: str = Field(..., max_length=60)
    backup_confirmed: bool = False
    exclude_member_ids: list[int] = []


class VoidIn(BaseModel):
    confirm: str = Field(..., max_length=60)
    reason: str = Field(..., min_length=2, max_length=300)


@router.get("/unissued-management/preview")
def preview(exclude: str = Query("", max_length=2000), db: Session = Depends(get_db), _a=Depends(require_admin)):
    """읽기 전용. 어떤 자료도 바꾸지 않는다."""
    _ensure_receivables_schema_ready()
    plan = svc.build_plan(db, _ids(exclude))
    db.rollback()
    return plan


@router.get("/unissued-management/preview.csv")
def preview_csv(exclude: str = Query("", max_length=2000), db: Session = Depends(get_db), _a=Depends(require_admin)):
    _ensure_receivables_schema_ready()
    plan = svc.build_plan(db, _ids(exclude))
    db.rollback()
    head = ["회원ID", "성명", "차량번호", "회원구분", "회원상태", "자격증명 발급일자", "자격증명 발급번호", "계정", "기준잔액", "유효 부과합",
            "실수납합", "기존 정정합", "현재 관리비 미수금", "정정 예정액", "정정 후 잔액", "판정", "사유", "확인 필요"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    label = {"target": "정정대상(택배)", "already_zero": "이미0원", "credit_kept": "선납유지", "excluded": "제외",
             "review_non_bae": "별도검토(비택배)", "review_closed": "별도검토(폐업)", "review_account": "별도검토(계정불일치)"}
    for r in plan["rows"]:
        w.writerow([r["member_id"], r["name"], r["vehicle_number"], r["category"], r["member_status"], r["certificate_issue_date"],
                    r["certificate_number"], r["account_type"], r["baseline"], r["charges"], r["payments"], r["adjustments"],
                    r["balance_before"], r["planned_adjustment"], r["balance_after"], label.get(r["decision"], r["decision"]),
                    r["reason"], " / ".join(r["flags"])])
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return StreamingResponse(io.BytesIO(data), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="unissued_management_preview.csv"'})


@router.post("/unissued-management/apply")
def apply(body: ApplyIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _ensure_receivables_schema_ready()
    try:
        result = svc.apply_plan(db, plan_digest=body.plan_digest, confirm=body.confirm, backup_confirmed=body.backup_confirmed,
                                actor=_actor(admin), exclude_member_ids=body.exclude_member_ids)
        db.commit()
        return {"ok": True, **result}
    except svc.AdjustmentError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    except Exception:
        db.rollback()
        raise HTTPException(500, "정정 중 오류가 발생해 전체 취소(롤백)되었습니다. 아무 자료도 바뀌지 않았습니다.")


@router.get("/batches")
def batches(db: Session = Depends(get_db), _a=Depends(require_admin)):
    _ensure_receivables_schema_ready()
    rows = db.query(ReceivableAdjustmentBatch).order_by(ReceivableAdjustmentBatch.id.desc()).limit(50).all()
    return [{"batch_id": b.batch_id, "reason_code": b.reason_code, "status": b.status, "members": b.member_count, "total_amount": b.total_amount,
             "created_by": b.created_by, "created_at": str(b.created_at), "voided_at": str(b.voided_at) if b.voided_at else None}
            for b in rows]


@router.get("/batches/{batch_id}/verify")
def verify(batch_id: str, db: Session = Depends(get_db), _a=Depends(require_admin)):
    _ensure_receivables_schema_ready()
    try:
        return svc.verify_batch(db, batch_id)
    except svc.AdjustmentError as e:
        raise HTTPException(404, str(e))


@router.post("/batches/{batch_id}/void")
def void(batch_id: str, body: VoidIn, db: Session = Depends(get_db), admin=Depends(require_admin)):
    _ensure_receivables_schema_ready()
    try:
        r = svc.void_batch(db, batch_id, confirm=body.confirm, reason=body.reason, actor=_actor(admin))
        db.commit()
        return {"ok": True, **r}
    except svc.AdjustmentError as e:
        db.rollback()
        raise HTTPException(400, str(e))


@router.get("/consistency")
def consistency(db: Session = Depends(get_db), _a=Depends(require_admin)):
    """상세화면 계산과 목록/통계 SQL 계산의 회원별 일치 검증(읽기 전용)."""
    _ensure_receivables_schema_ready()
    r = svc.consistency_report(db)
    db.rollback()
    return r
