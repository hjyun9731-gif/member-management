import re
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel
import io
import logging

from app.database import get_db
from app.auth import get_current_user, require_admin
from app import models, crud
from app.excel_utils import records_to_excel, parse_date_sort

logger = logging.getLogger(__name__)
router = APIRouter()

SEARCH = ["transferor", "transferee", "vehicle_number", "region",
          "memo", "certificate_number", "seq_number", "management_number"]

# 양도자/양수자 raw_data 후보 컬럼명
_TRANSFEROR_KEYS = ['양도자', '양도인', '양도자성명', '양도인성명', '양도자명',
                    '성명(양도)', '양도(자)성명', '양도자 성명', '양도인 성명']
_TRANSFEREE_KEYS = ['양수자', '양수인', '양수자성명', '양수인성명', '양수자명',
                    '성명(양수)', '양수(자)성명', '양수자 성명', '양수인 성명']


# ─────────────────────────────────────────────
# 날짜 정제 함수
# ─────────────────────────────────────────────
_DATE_RE = re.compile(
    r'^(\d{4}[\.\-/]\d{1,2}[\.\-/]\d{1,2})'       # 4자리 연도
    r'|'
    r'^(\d{2}\s*[\.\-/]\s*\d{1,2}\s*[\.\-/]\s*\d{1,2})'  # 2자리 연도
)


def _clean_date(s: str) -> str:
    """날짜+텍스트 혼합에서 날짜만 추출 (표시용).
    예: '25.09.03. 경기여주->강릉' → '25.09.03'"""
    if not s:
        return ''
    s = str(s).strip()
    m = _DATE_RE.match(s)
    if m:
        date_part = (m.group(1) or m.group(2) or '').replace(' ', '').rstrip('.')
        return date_part
    return s


def _extract_memo(s: str) -> str:
    """날짜+텍스트 혼합에서 메모 부분만 추출.
    예: '25.09.03. 경기여주->강릉' → '경기여주->강릉'"""
    if not s:
        return ''
    s = str(s).strip()
    cleaned = _DATE_RE.sub('', s).lstrip('.').strip()
    return cleaned if cleaned != s else ''


def _raw_get(raw: dict, keys: list) -> str:
    """raw_data에서 후보 컬럼명으로 값 검색"""
    for k in keys:
        v = raw.get(k, '') or ''
        if str(v).strip() and str(v).strip() not in ('nan', 'None', 'NaN', '-'):
            return str(v).strip()
    return ''


# ─────────────────────────────────────────────
# 포맷 함수
# ─────────────────────────────────────────────
def _fmt(t):
    """양도양수대장 날짜: 접수일자(receipt_date=B열), 인가일자(approval_date=K열).
    처리일자(process_date) 개념 없음 - 엑셀에 해당 컬럼 존재하지 않음.
    """
    return {
        "id": t.id,
        "seq_number":            t.seq_number or "",
        "management_number":     t.management_number or "",
        "region":                t.region or "",
        "vehicle_number":        t.vehicle_number or "",
        "transferor":            t.transferor or "",
        "transferee":            t.transferee or "",
        "resident_number":       t.resident_number or "",
        "address":               t.address or "",
        "phone":                 t.phone or "",
        "mobile":                t.mobile or "",
        "receipt_date":          _clean_date(t.receipt_date or ""),    # 접수일자 (B열)
        "approval_date":         _clean_date(t.approval_date or ""),   # 인가일자 (K열)
        "membership_date":       _clean_date(t.membership_date or ""),
        "certificate_issue_date": t.certificate_issue_date or "",
        "certificate_number":    t.certificate_number or "",
        "ledger_update":         t.ledger_update or "",
        "driver_license_number": t.driver_license_number or "",
        "computer_report":       t.computer_report or "",
        "memo":                  t.memo or "",
        "member_id":             t.member_id,
        "created_at":            str(t.created_at)[:16] if t.created_at else None,
    }


def _fmt_detail(t):
    """상세보기용 - raw_data 포함"""
    d = _fmt(t)
    raw = t.raw_data if isinstance(t.raw_data, dict) else {}
    # raw_data에서 transferor/transferee fallback
    if not d["transferor"]:
        d["transferor"] = _raw_get(raw, _TRANSFEROR_KEYS)
    if not d["transferee"]:
        d["transferee"] = _raw_get(raw, _TRANSFEREE_KEYS)
    # raw_data 정제 (Unnamed, 허가번호 제외)
    d["raw_data"] = {k: v for k, v in raw.items()
                     if k and not str(k).startswith('Unnamed') and k not in ('허가번호',)}
    return d


# ─────────────────────────────────────────────
# 시작 시 양도자/양수자 역추출 마이그레이션
# ─────────────────────────────────────────────
def backfill_transfer_names(db: Session):
    """raw_data에서 비어있는 양도자/양수자 채우기 (일회성, 서버 시작 시 실행)"""
    from sqlalchemy import or_
    empty_count = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.raw_data.isnot(None),
        or_(
            models.TransferLedger.transferor == None,
            models.TransferLedger.transferor == '',
            models.TransferLedger.transferee == None,
            models.TransferLedger.transferee == '',
        ),
    ).count()

    if empty_count == 0:
        return

    logger.info(f"양도자/양수자 역추출 마이그레이션 시작: {empty_count}건")
    records = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.raw_data.isnot(None),
    ).all()

    updated = 0
    for t in records:
        if (t.transferor and t.transferee):
            continue
        raw = t.raw_data if isinstance(t.raw_data, dict) else {}
        if not raw:
            continue
        changed = False
        if not t.transferor:
            v = _raw_get(raw, _TRANSFEROR_KEYS)
            if v:
                t.transferor = v
                changed = True
        if not t.transferee:
            v = _raw_get(raw, _TRANSFEREE_KEYS)
            if v:
                t.transferee = v
                changed = True
        if changed:
            updated += 1

    if updated:
        db.commit()
    logger.info(f"양도자/양수자 역추출 완료: {updated}건 업데이트")


# ─────────────────────────────────────────────
# API 엔드포인트
# ─────────────────────────────────────────────
@router.get("")
async def list_transfers(
    search: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    date_order: Optional[str] = Query("desc"),    # 날짜 정렬: desc/asc
    member_sort: Optional[str] = Query(None),      # 관리번호 정렬: mgmt_desc/mgmt_asc
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _=Depends(get_current_user),
):
    from app.excel_utils import mgmt_sort_key, parse_date_sort
    from sqlalchemy.orm import defer

    # member_sort가 있으면 우선 적용, 없으면 date_order 사용
    # (하위호환: date_order=mgmt_desc 형태도 처리)
    effective_sort = member_sort or date_order or "mgmt_desc"

    base_q = (db.query(models.TransferLedger)
              .filter(models.TransferLedger.deleted_at.is_(None))
              .options(defer(models.TransferLedger.raw_data)))
    if region:
        base_q = base_q.filter(models.TransferLedger.region == region)
    if search:
        from sqlalchemy import or_
        conds = [getattr(models.TransferLedger, f).ilike(f"%{search}%")
                 for f in SEARCH if hasattr(models.TransferLedger, f)]
        if conds:
            base_q = base_q.filter(or_(*conds))

    from sqlalchemy import or_ as _or
    base_q = base_q.filter(_or(
        models.TransferLedger.vehicle_number.isnot(None),
        models.TransferLedger.transferee.isnot(None),
    ))

    # 정렬 분기
    if effective_sort in ("mgmt_desc", "mgmt_asc"):
        all_rows = base_q.with_entities(
            models.TransferLedger.id,
            models.TransferLedger.management_number,
            models.TransferLedger.seq_number,
            models.TransferLedger.receipt_date).all()
        reverse = effective_sort == "mgmt_desc"

        def mgmt_sort_fn(r):
            from datetime import datetime as _dt
            mgmt   = str(r[1] or '').strip()
            seq    = str(r[2] or '').strip()
            mk = mgmt_sort_key(mgmt)
            if mk[0] > 0:
                # 관리번호가 있으면 (year, num) 기준 최상단
                return (3, mk[0], mk[1], 0)
            # 관리번호 없음: seq_number를 숫자로 파싱해서 차선 정렬
            try:
                seq_int = int(float(seq)) if seq else 0
            except (ValueError, TypeError):
                seq_int = 0
            if seq_int > 0:
                return (2, 0, seq_int, 0)
            # seq도 없으면 날짜
            d = parse_date_sort(r[3] or '')
            if d != _dt.min:
                return (1, d.year, d.month, d.day)
            return (0, 0, 0, 0)

        all_rows.sort(key=mgmt_sort_fn, reverse=reverse)
    else:
        # 날짜 정렬: 접수일자 1순위, 없으면 처리일자
        all_rows = base_q.with_entities(
            models.TransferLedger.id,
            models.TransferLedger.receipt_date).all()
        reverse = (effective_sort or "desc") == "desc"

        def sort_key(r):
            from datetime import datetime
            d = parse_date_sort(r[1] or '')
            if d == datetime.min:
                d = parse_date_sort(r[2] or '')
            return d

        all_rows.sort(key=sort_key, reverse=reverse)

    total = len(all_rows)
    page_ids = [r[0] for r in all_rows[(page-1)*limit: page*limit]]
    if page_ids:
        items = db.query(models.TransferLedger).filter(
            models.TransferLedger.id.in_(page_ids),
            models.TransferLedger.deleted_at.is_(None),
        ).options(defer(models.TransferLedger.raw_data)).all()
        items_by_id = {i.id: i for i in items}
        items = [items_by_id[pid] for pid in page_ids if pid in items_by_id]
    else:
        items = []

    pages = max(1, (total + limit - 1) // limit)
    return {"items": [_fmt(i) for i in items], "total": total,
            "page": page, "pages": pages, "limit": limit}


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
    return _fmt_detail(t)  # 상세보기: raw_data 포함


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
