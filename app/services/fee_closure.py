"""관리비폐지 확인필요 대상 추출 (읽기 전용).

택배회원 중 폐업(=폐지) 처리된 사람을 뽑아 '관리비 중단 여부 확인이 필요한 대상'으로 보여준다.
- 이 시스템에는 부과·수납 데이터가 없으므로 실제 부과 중단 여부는 판단하지 않고 '검증불가'로 표시한다.
- 폐업자를 '관리비가 계속 부과되는 사람'으로 확정하지 않는다. 기존 관리비·미수금은 읽지도, 바꾸지도 않는다.
- 미수금 시스템 연동: register_billing_provider(fn) 로 조회 함수를 등록하면 last_billed_month /
  post_closure_amount / billing_status 가 채워진다. fn(item_dict) -> dict|None
  (예: {"last_billed_month": "2026-05", "post_closure_amount": 30000, "billing_status": "부과중|중단확인|정산필요"})
"""
import re
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app import models
from app.services.change_judgement import parse_ymd

UNVERIFIED = "검증불가"
_billing_provider: Optional[Callable[[dict], Optional[dict]]] = None


def register_billing_provider(fn: Optional[Callable[[dict], Optional[dict]]]) -> None:
    global _billing_provider
    _billing_provider = fn


def _nv(v) -> str:
    return re.sub(r"[\s]|호$", "", str(v or "")).lower()


def fee_closure_candidates(db: Session, year: Optional[int] = None, month: Optional[int] = None) -> list:
    """폐업 처리된 택배회원 목록. year/month 지정 시 해당 월 말일까지 폐업한 건(+ 날짜 불명 건)."""
    members_by_id, members_by_plate = {}, {}
    for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all():
        members_by_id[m.id] = m
        members_by_plate.setdefault(_nv(m.vehicle_number), []).append(m)

    out, seen = [], set()
    q = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None))
    for c in q.all():
        if (c.closure_type or "").replace("폐지", "폐업") != "폐업":
            continue
        m = members_by_id.get(c.member_id) if getattr(c, "member_id", None) else None
        if m is None:
            same = [x for x in members_by_plate.get(_nv(c.vehicle_number), []) if (x.name or "").strip() == (c.name or "").strip()]
            m = same[0] if len(same) == 1 else None
        category = (m.category if m is not None else None) or ("택배" if "배" in (c.vehicle_number or "") else "")
        if category != "택배":
            continue
        ymd = parse_ymd(c.closure_date) or parse_ymd(c.receipt_date)
        if year and month and ymd and (ymd[0], ymd[1]) > (year, month):
            continue
        key = (c.id,)
        if key in seen:
            continue
        seen.add(key)
        item = {
            "closure_id": c.id, "member_id": m.id if m is not None else None,
            "name": c.name or "", "vehicle_number": c.vehicle_number or "", "region": c.region or "",
            "closure_type": "폐업", "closure_date": c.closure_date or "", "receipt_date": c.receipt_date or "",
            "closure_date_valid": bool(ymd),
            "last_billed_month": None, "post_closure_amount": None, "billing_status": UNVERIFIED,
        }
        if _billing_provider:
            try:
                extra = _billing_provider(dict(item))
                if extra:
                    item.update({k: extra[k] for k in ("last_billed_month", "post_closure_amount", "billing_status") if k in extra})
            except Exception:
                pass   # 연동 오류가 있어도 검증불가로 남긴다
        out.append(item)
    out.sort(key=lambda x: (x["closure_date"] or "", x["id"] if "id" in x else x["closure_id"]), reverse=True)
    return out
