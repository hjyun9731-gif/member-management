from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
import re, io

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel

router = APIRouter()

CHANGE_TYPES = ["주소지변경","상호변경","구조변경","전속계약 업체변경","등록이관",
                "이전전출","대표자변경","성명변경","번호변경","변동변경","양도","폐업","기타"]

SEARCH = ["name", "vehicle_number", "region", "before_value", "after_value", "change_type"]


def normalize_change_type(val: str) -> str:
    """변경유형 텍스트 정규화: 공백/특수문자/줄바꿈 완전 제거 후 키워드 매칭"""
    if not val:
        return '기타'
    import re as _re
    def _norm(s):
        return _re.sub(r'[\s\r\n\t\-_·,./()（）\[\]【】]+', '', str(s)).lower()
    s = _norm(val)
    mapping = [
        ('구조변경',          ['구조변경', '구조변경']),
        ('전속계약 업체변경', ['전속계약업체변경', '전속계약업체', '전속업체변경', '전속업체', '전속계약', '소속업체변경', '업체변경', '전속변경']),
        ('주소지변경',        ['주소지변경', '주소변경', '주소이전', '이전주소']),
        ('상호변경',          ['상호변경']),
        ('등록이관',          ['등록이관', '이관등록']),
        ('이전전출',          ['이전전출', '전출']),
        ('대표자변경',        ['대표자변경']),
        ('성명변경',          ['성명변경', '이름변경']),
        ('번호변경',          ['번호변경', '차량번호변경', '번호판변경']),
        ('양도',              ['양도양수', '양도']),
        ('폐업',              ['폐업', '폐지']),
        ('이관',              ['이관']),
    ]
    for ct, kws in mapping:
        if any(_norm(kw) in s for kw in kws):
            return ct
    # 원래 값이 CHANGE_TYPES에 있으면 그대로
    for t in CHANGE_TYPES:
        if _norm(t) == s:
            return t
    return val.strip() if val.strip() else '기타'

def _fmt(c):
    """목록용 - raw_data 접근 없음 (성능 최적화)"""
    return {
        "id": c.id,
        "change_type": c.change_type or "",
        "region": c.region or "",
        "vehicle_number": c.vehicle_number or "",
        "name": c.name or "",
        "before_value": c.before_value or "",
        "after_value": c.after_value or "",
        "change_date": c.change_date or "",
        "receipt_date": c.receipt_date or "",
        "memo": c.memo or "",
        "created_at": str(c.created_at)[:10] if c.created_at else "",
    }


_DATE_KEYS_CH = ['처리일자', '처리일', '변경일자', '접수일자', '신고일자']
_BEFORE_KEYS  = ['변경전', '변경 전', '이전주소', '변경전주소', '이전내용', '변경전내용']
_AFTER_KEYS   = ['변경후', '변경 후', '변경후주소', '현재주소', '변경후내용', '현재내용']


def _extract_raw(raw: dict, keys: list) -> str:
    for k in keys:
        v = raw.get(k, '') or ''
        if str(v).strip() and str(v).strip() not in ('nan','None','NaN','-'):
            return str(v).strip()
    return ''


def _fmt_detail(c):
    """상세보기용 - raw_data fallback 포함"""
    raw = c.raw_data if isinstance(c.raw_data, dict) else {}
    change_date = c.change_date or _extract_raw(raw, _DATE_KEYS_CH)
    before_value = c.before_value or _extract_raw(raw, _BEFORE_KEYS)
    after_value  = c.after_value  or _extract_raw(raw, _AFTER_KEYS)
    return {
        "id": c.id,
        "change_type": c.change_type or "",
        "region": c.region or "",
        "vehicle_number": c.vehicle_number or "",
        "name": c.name or "",
        "before_value": before_value,
        "after_value": after_value,
        "change_date": change_date,
        "receipt_date": c.receipt_date or "",
        "memo": c.memo or "",
        "created_at": str(c.created_at)[:10] if c.created_at else "",
    }


@router.get("")
async def list_changes(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None),
    date_order: Optional[str] = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    items, total = crud.get_sorted_page(
        db, models.ChangeHistory,
        date_field="change_date", sort_dir=date_order or "desc",
        page=page, limit=limit,
        search=search, search_fields=SEARCH,
        filters={"region": region, "change_type": change_type},
        nonempty_any=["vehicle_number", "name", "after_value"],
    )
    pages = max(1, (total + limit - 1) // limit)
    return {"items": [_fmt(i) for i in items], "total": total,
            "page": page, "pages": pages, "limit": limit}


@router.get("/types")
async def get_types(_=Depends(get_current_user)):
    return CHANGE_TYPES


@router.get("/export/excel")
async def export_excel(
    region: Optional[str] = Query(None),
    change_type: Optional[str] = Query(None),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    items, _ = crud.get_list(db, models.ChangeHistory, skip=0, limit=9999,
                              filters={"region": region, "change_type": change_type})
    content = records_to_excel([_fmt(i) for i in items], exclude=["id"])
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=change_history.xlsx"},
    )


@router.get("/{cid}")
async def get_change(cid: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    c = crud.get_by_id(db, models.ChangeHistory, cid)
    if not c:
        raise HTTPException(404)
    return _fmt_detail(c)  # 상세보기에서만 raw_data 포함


@router.post("/renormalize-types")
async def renormalize_change_types(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """DB에 '기타'로 저장된 변경이력의 change_type을 memo/before_value/after_value에서 재탐지해 업데이트"""
    updated_count = 0
    records = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
    ).all()

    from app.excel_utils import _normalize_text
    for rec in records:
        # 현재 타입이 기타거나 없는 경우 재탐지
        cur_type = rec.change_type or ''
        probe_texts = [
            cur_type,
            rec.memo or '',
            rec.before_value or '',
            rec.after_value or '',
        ]
        # raw_data 비고 컬럼도 탐색
        if isinstance(rec.raw_data, dict):
            for k in ('비고', '변경내용', '변경유형', '구분', '변경종류'):
                v = rec.raw_data.get(k, '')
                if v:
                    probe_texts.append(str(v))

        new_type = None
        for txt in probe_texts:
            if txt and txt.strip():
                detected = normalize_change_type(txt)
                if detected and detected not in ('기타', ''):
                    new_type = detected
                    break

        if new_type and new_type != rec.change_type:
            rec.change_type = new_type
            updated_count += 1

    if updated_count:
        db.commit()
    return {"updated": updated_count, "total": len(records)}


@router.post("")
async def create_change(data: dict, db: Session = Depends(get_db),
                         _=Depends(get_current_user)):
    if data.get("change_type"):
        data["change_type"] = normalize_change_type(data["change_type"])
    return _fmt(crud.create_item(db, models.ChangeHistory, data))


@router.put("/{cid}")
async def update_change(cid: int, data: dict, db: Session = Depends(get_db),
                         _=Depends(get_current_user)):
    c = crud.get_by_id(db, models.ChangeHistory, cid)
    if not c:
        raise HTTPException(404)
    if data.get("change_type"):
        data["change_type"] = normalize_change_type(data["change_type"])
    return _fmt(crud.update_item(db, c, data))


@router.delete("/{cid}")
async def delete_change(cid: int, db: Session = Depends(get_db),
                         _=Depends(require_admin)):
    c = crud.get_by_id(db, models.ChangeHistory, cid)
    if not c:
        raise HTTPException(404)
    crud.soft_delete(db, c)
    return {"ok": True}
