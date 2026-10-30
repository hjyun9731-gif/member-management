from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import io

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.schemas import LicenseHolderCreate, LicenseHolderUpdate, LicenseHolderResponse, PaginatedResponse
from app.services.certificate_number_guard import guard_certificate_number
from app.services.certificate_number_rules import canonical_number
from app.excel_utils import records_to_excel, COLUMN_LABELS
from app.services.freight_qualification import normalize_in_dict_or_400

router = APIRouter()

SEARCH_FIELDS = ["name", "vehicle_number", "phone", "mobile", "company_name",
                 "certificate_number", "permit_number", "address", "management_number",
                 "affiliated_company", "driver_license_number"]


@router.get("", response_model=PaginatedResponse)
async def list_license_holders(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    filters = {"region": region, "category": category, "membership_status": membership_status}
    skip = (page - 1) * limit
    items, total = crud.get_list(db, models.LicenseHolder, skip=skip, limit=limit,
                                  search=search, search_fields=SEARCH_FIELDS, filters=filters)
    pages = max(1, (total + limit - 1) // limit)
    return PaginatedResponse(items=[LicenseHolderResponse.model_validate(i) for i in items],
                              total=total, page=page, pages=pages, limit=limit)


@router.get("/export/excel")
async def export_excel(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    membership_status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    filters = {"region": region, "category": category, "membership_status": membership_status}
    items, _ = crud.get_list(db, models.LicenseHolder, skip=0, limit=9999,
                              search=search, search_fields=SEARCH_FIELDS, filters=filters)
    records = [{COLUMN_LABELS.get(k, k): v for k, v in LicenseHolderResponse.model_validate(i).model_dump().items()
                if k != "raw_data"} for i in items]
    return StreamingResponse(io.BytesIO(records_to_excel(records)),
                              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": "attachment; filename=license_holders.xlsx"})


@router.get("/{item_id}", response_model=LicenseHolderResponse)
async def get_license_holder(item_id: int, db: Session = Depends(get_db),
                              current_user: models.User = Depends(get_current_user)):
    item = crud.get_by_id(db, models.LicenseHolder, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="데이터를 찾을 수 없습니다.")
    return item


@router.post("", response_model=LicenseHolderResponse)
async def create_license_holder(data: LicenseHolderCreate, db: Session = Depends(get_db),
                                 current_user: models.User = Depends(get_current_user)):
    payload = data.model_dump()
    normalize_in_dict_or_400(payload)  # 화물운송종사자격증 번호 형식 검증/정규화
    guard_certificate_number(db, payload.get("certificate_number"), candidate_id=payload.get("candidate_id"),
                             name=str(payload.get("name") or ""), vehicle_number=str(payload.get("vehicle_number") or ""))
    return crud.create_item(db, models.LicenseHolder, payload)


@router.put("/{item_id}", response_model=LicenseHolderResponse)
async def update_license_holder(item_id: int, data: LicenseHolderUpdate,
                                 db: Session = Depends(get_db),
                                 current_user: models.User = Depends(get_current_user)):
    item = crud.get_by_id(db, models.LicenseHolder, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="데이터를 찾을 수 없습니다.")
    payload = data.model_dump(exclude_none=True)
    normalize_in_dict_or_400(payload)  # 화물운송종사자격증 번호 형식 검증/정규화
    if "certificate_number" in payload and canonical_number(payload.get("certificate_number")) != canonical_number(item.certificate_number):
        guard_certificate_number(db, payload.get("certificate_number"), member_id=item.id,
                                 candidate_id=getattr(item, "candidate_id", None),
                                 name=str(payload.get("name") or item.name or ""),
                                 vehicle_number=str(payload.get("vehicle_number") or item.vehicle_number or ""))
    return crud.update_item(db, item, payload)


@router.delete("/{item_id}")
async def delete_license_holder(item_id: int, db: Session = Depends(get_db),
                                 current_user: models.User = Depends(require_admin)):
    item = crud.get_by_id(db, models.LicenseHolder, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="데이터를 찾을 수 없습니다.")
    crud.soft_delete(db, item)
    return {"message": "삭제되었습니다."}
