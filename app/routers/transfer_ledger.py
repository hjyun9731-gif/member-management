from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel
import io

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, parse_date_sort

router = APIRouter()

SEARCH = ["transferor", "transferee", "vehicle_number", "region",
          "memo", "certificate_number", "seq_number", "management_number"]


def _fmt(t):
    # raw_data 접근 없음 (목록 성능 최적화 - backfill된 process_date 직접 사용)
    return {
        "id": t.id, "seq_number": t.seq_number or "", "receipt_date": t.receipt_date or "",
        "process_date": t.process_date or "",
        "region": t.region or "", "vehicle_number": t.vehicle_number or "",
        "transferor": t.transferor or "", "transferee": t.transferee or "",
        "resident_number": t.resident_number or "", "address": t.address or "",
        "phone": t.phone or "", "mobile": t.mobile or "",
        "approval_date": t.approval_date or "", "membership_date": t.membership_date or "",
        "certificate_issue_date": t.certificate_issue_date or "", "certificate_number": t.certificate_number or "",
        "ledger_update": t.ledger_update or "", "driver_license_number": t.driver_license_number or "",
        "computer_report": t.computer_report or "", "memo": t.memo or "",
        "management_number": t.management_number or "", "member_id": t.member_id,
        "created_at": str(t.created_at)[:16] if t.created_at else None,
    }


@router.get("")
async def list_transfers(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    date_order: Optional[str] = Query("desc"),  # desc=최신순, asc=오래된순
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    # 2쿼리 방식: ①처리일자+id만 가져와 날짜 정렬 → ②50건 full 로딩 (raw_data 제외)
    items, total = crud.get_sorted_page(
        db, models.TransferLedger,
        date_field="process_date", sort_dir=date_order or "desc",
        page=page, limit=limit,
        search=search, search_fields=SEARCH, filters={"region": region},
        nonempty_any=["vehicle_number", "transferee"],
    )
    return {"items": [_fmt(i) for i in items], "total": total,
            "page": page, "pages": max(1, (total + limit - 1) // limit), "limit": limit}


@router.get("/next-number")
async def next_number(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return {"next_number": crud.get_next_transfer_member_number(db)}


@router.get("/export/excel")
async def export(search: Optional[str] = Query(None), region: Optional[str] = Query(None),
                  db: Session = Depends(get_db), _=Depends(get_current_user)):
    items, _ = crud.get_list(db, models.TransferLedger, skip=0, limit=9999,
                              search=search, search_fields=SEARCH, filters={"region": region})
    content = records_to_excel([_fmt(i) for i in items])
    return StreamingResponse(io.BytesIO(content),
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": "attachment; filename=transfer_ledger.xlsx"})


@router.get("/{tid}")
async def get_transfer(tid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = crud.get_by_id(db, models.TransferLedger, tid)
    if not t:
        raise HTTPException(404, "양도양수 기록을 찾을 수 없습니다.")
    return _fmt(t)


@router.post("")
async def create_transfer(data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _fmt(crud.create_item(db, models.TransferLedger, data))


@router.put("/{tid}")
async def update_transfer(tid: int, data: dict, db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = crud.get_by_id(db, models.TransferLedger, tid)
    if not t:
        raise HTTPException(404, "양도양수 기록을 찾을 수 없습니다.")
    return _fmt(crud.update_item(db, t, data))


@router.delete("/{tid}")
async def delete_transfer(tid: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    t = crud.get_by_id(db, models.TransferLedger, tid)
    if not t:
        raise HTTPException(404, "양도양수 기록을 찾을 수 없습니다.")
    crud.soft_delete(db, t)
    return {"ok": True}


class RegisterMemberBody(BaseModel):
    management_number: Optional[str] = None


@router.post("/{tid}/register-member")
async def register_member(tid: int, body: RegisterMemberBody,
                           db: Session = Depends(get_db), _=Depends(get_current_user)):
    t = crud.get_by_id(db, models.TransferLedger, tid)
    if not t:
        raise HTTPException(404, "양도양수 기록을 찾을 수 없습니다.")
    if t.member_id:
        raise HTTPException(400, "이미 회원으로 등록된 기록입니다.")
    mgmt = body.management_number or crud.get_next_transfer_member_number(db)
    if crud.check_mgmt_dup(db, models.LicenseHolder, mgmt):
        raise HTTPException(400, f"관리번호 {mgmt}가 이미 존재합니다.")
    member = crud.register_transfer_as_member(db, tid, mgmt)
    return {"ok": True, "management_number": mgmt, "member_id": member.id, "category": member.category}
