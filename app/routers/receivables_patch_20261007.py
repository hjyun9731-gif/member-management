"""2026-10-07 수납·미수금 보정 패치.

목적
- 2026 미수금 원장에서 '지로희망' 대상자를 표시용 플래그로만 관리한다.
- 2026년 종료자 시트의 폐업/폐지/양도/이관/탈퇴자를 정확 일치(성명+차량번호)로만
  기존 회원 DB와 연결하여 활성 미수/향후 자동부과에서 제외하고 폐업관리 이력을 만든다.
- 기존 수납, 연락, V4 보정, 수동 잔액 조정은 덮어쓰거나 삭제하지 않는다.

안전장치
- 성명+차량번호 정규화 후 정확 일치만 허용한다. 모호하거나 미매칭이면 건너뛴다.
- 종료월 이후의 source='auto' 자동부과만 제거한다. 종료월 부과, 수동조정, 입금은 보존한다.
- 동일 패치 재실행은 멱등적으로 동작한다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app import crud, models
from app.auth import get_current_user, require_admin
from app.database import Base, SessionLocal, engine, get_db
from app.receivables_models import ReceivableCharge, ReceivableSystemState

router = APIRouter(prefix="/api/receivables/patch-20261007", tags=["수납미수금-20261007"])

PATCH_ID = "receivables_patch_20261007_giro_closures_v1"
STATE_KEY = PATCH_ID
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "receivables_patch_20261007.json"
FLAG_KEY = "giro_preferred"
FLAG_VALUE = "1"
PATCH_MEMO = "[20261007 미수금원장 종료자정리 자동반영]"


class ReceivableMemberFlag(Base):
    __tablename__ = "receivable_member_flags"
    __table_args__ = (
        UniqueConstraint("member_id", "flag_key", name="uq_receivable_member_flag_member_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, nullable=False, index=True)
    flag_key = Column(String(80), nullable=False, index=True)
    flag_value = Column(String(120), nullable=False, default="1")
    source = Column(String(120), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


def _ensure_schema() -> None:
    # 신규 플래그 테이블만 checkfirst로 추가한다. 기존 테이블/컬럼은 변경하지 않는다.
    ReceivableMemberFlag.__table__.create(bind=engine, checkfirst=True)
    ReceivableSystemState.__table__.create(bind=engine, checkfirst=True)


def _norm_name(value: object) -> str:
    s = str(value or "").strip()
    # 예: LISANGUN (이상건) → 이상건
    m = re.search(r"\(([가-힣]+)\)", s)
    if m:
        s = m.group(1)
    return re.sub(r"\s+", "", s)


def _norm_vehicle(value: object) -> str:
    s = str(value or "").strip().lower()
    s = s.replace("강원", "")
    s = re.sub(r"\s+", "", s)
    s = s.replace("-", "")
    s = re.sub(r"호$", "", s)
    return s


def _norm_closure_type(value: object) -> str:
    s = str(value or "")
    if "탈퇴" in s:
        return "탈퇴"
    if "양도" in s:
        return "양도"
    if "이관" in s:
        return "이관"
    if any(x in s for x in ("폐업", "폐지", "페지")):
        return "폐업"
    return s.strip()


def _load_payload() -> dict:
    with DATA_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("patch_id") != PATCH_ID:
        raise RuntimeError("2026-10-07 패치 데이터 ID가 일치하지 않습니다.")
    return payload


def _member_index(db: Session) -> Dict[Tuple[str, str], List[models.LicenseHolder]]:
    rows = db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all()
    out: Dict[Tuple[str, str], List[models.LicenseHolder]] = {}
    for m in rows:
        key = (_norm_name(m.name), _norm_vehicle(m.vehicle_number))
        if key[0] and key[1]:
            out.setdefault(key, []).append(m)
    return out


def _pick_member(candidates: Iterable[models.LicenseHolder], prefer_active: bool = True):
    items = list(candidates)
    if not items:
        return None, "unmatched"
    if prefer_active:
        active = [m for m in items if (m.status or "active") == "active"]
        if len(active) == 1:
            return active[0], "matched_active"
        if len(active) > 1:
            return None, "ambiguous_active"
    if len(items) == 1:
        return items[0], "matched"
    return None, "ambiguous"


def _closure_index(db: Session):
    rows = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None)).all()
    out: Dict[Tuple[str, str, str, str], List[models.Closure]] = {}
    for c in rows:
        key = (
            _norm_name(c.name),
            _norm_vehicle(c.vehicle_number),
            _norm_closure_type(c.closure_type),
            str(c.closure_date or "")[:10],
        )
        out.setdefault(key, []).append(c)
    return out


def _next_withdrawal_number(db: Session) -> str:
    items = db.query(models.Closure).filter(
        models.Closure.management_number.like("탈-%"),
        models.Closure.deleted_at.is_(None),
    ).all()
    max_n = 0
    for item in items:
        try:
            max_n = max(max_n, int(str(item.management_number).split("-", 1)[1]))
        except Exception:
            pass
    return f"탈-{max_n + 1}"


def _new_management_number(db: Session, closure_type: str) -> str:
    if closure_type == "탈퇴":
        return _next_withdrawal_number(db)
    return crud.get_next_closure_number(db, closure_type)


def _create_closure(db: Session, member: models.LicenseHolder, rec: dict) -> models.Closure:
    closure_type = _norm_closure_type(rec.get("closure_type"))
    closure = models.Closure(
        management_number=_new_management_number(db, closure_type),
        closure_type=closure_type,
        data_type="이전자료",
        region=member.region or rec.get("region") or "",
        vehicle_number=member.vehicle_number or rec.get("vehicle_number") or "",
        name=member.name or rec.get("name") or "",
        company_name=getattr(member, "company_name", "") or "",
        closure_date=rec.get("closure_date") or "",
        receipt_date="",
        approval_date=member.approval_date or "",
        reason=rec.get("reason") or "",
        memo=PATCH_MEMO,
        vehicle_type=member.vehicle_type or "",
        fuel_type=member.fuel_type or "",
        structure_change=getattr(member, "structure_change", "") or "",
        phone=member.phone or "",
        mobile=member.mobile or "",
        address=member.address or "",
        official_address=getattr(member, "official_address", "") or "",
        membership_status=member.membership_status or "",
        membership_date=member.membership_date or "",
        certificate_issue_date=member.certificate_issue_date or "",
        certificate_number=member.certificate_number or "",
        driver_license_number=getattr(member, "driver_license_number", "") or "",
        resident_number=getattr(member, "resident_number", "") or "",
        affiliated_company=getattr(member, "affiliated_company", "") or "",
        agent_name=getattr(member, "agent_name", "") or "",
        agent_mobile=getattr(member, "agent_mobile", "") or "",
        transferee="",
        transfer_region="",
        member_id=member.id,
        original_management_number=member.management_number or "",
        original_mgmt_match_status="linked",
        raw_data={
            "source": PATCH_ID,
            "source_row": rec.get("source_row"),
            "source_region": rec.get("region"),
            "source_account_type": rec.get("account_type"),
        },
    )
    db.add(closure)
    db.flush()
    return closure


def _remove_future_auto_charges(db: Session, member_id: int, closure_date: str) -> int:
    month = str(closure_date or "")[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return 0
    # 사용자 기준: 종료월까지만 부과. 종료월 다음 달 이후 자동부과만 제거한다.
    return int(
        db.query(ReceivableCharge)
        .filter(
            ReceivableCharge.member_id == member_id,
            ReceivableCharge.billing_month > month,
            ReceivableCharge.source == "auto",
        )
        .delete(synchronize_session=False)
        or 0
    )


def _upsert_flag(db: Session, member_id: int, note: str = "") -> bool:
    row = db.query(ReceivableMemberFlag).filter(
        ReceivableMemberFlag.member_id == member_id,
        ReceivableMemberFlag.flag_key == FLAG_KEY,
    ).first()
    if row:
        changed = row.flag_value != FLAG_VALUE or row.source != PATCH_ID
        row.flag_value = FLAG_VALUE
        row.source = PATCH_ID
        row.note = note or row.note
        return changed
    db.add(ReceivableMemberFlag(
        member_id=member_id,
        flag_key=FLAG_KEY,
        flag_value=FLAG_VALUE,
        source=PATCH_ID,
        note=note or "26년 지로희망",
    ))
    return True


def _save_state(db: Session, result: dict) -> None:
    state = db.query(ReceivableSystemState).filter(ReceivableSystemState.key == STATE_KEY).first()
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if state:
        state.value = payload
    else:
        db.add(ReceivableSystemState(key=STATE_KEY, value=payload))


def _read_state(db: Session) -> Optional[dict]:
    state = db.query(ReceivableSystemState).filter(ReceivableSystemState.key == STATE_KEY).first()
    if not state or not state.value:
        return None
    try:
        return json.loads(state.value)
    except Exception:
        return {"status": "state_parse_error", "raw": state.value}


def apply_once(force: bool = False) -> dict:
    """패치를 1회 적용한다. force=True면 미매칭 재확인을 위해 다시 대조한다."""
    _ensure_schema()
    db = SessionLocal()
    try:
        previous = _read_state(db)
        if previous and not force and previous.get("status") in {"applied", "applied_with_skips"}:
            return {**previous, "status": "already_applied"}

        payload = _load_payload()
        members = _member_index(db)
        closures_idx = _closure_index(db)

        giro_matched = 0
        giro_created_or_updated = 0
        giro_unmatched = []
        giro_ambiguous = []
        for rec in payload.get("giro_preferred", []):
            key = (rec.get("match_name") or _norm_name(rec.get("name")), rec.get("match_vehicle") or _norm_vehicle(rec.get("vehicle_number")))
            member, match_status = _pick_member(members.get(key, []), prefer_active=True)
            if not member:
                target = {"name": rec.get("name"), "vehicle_number": rec.get("vehicle_number"), "source_row": rec.get("source_row")}
                (giro_ambiguous if match_status.startswith("ambiguous") else giro_unmatched).append(target)
                continue
            giro_matched += 1
            if _upsert_flag(db, member.id, "26년 지로희망"):
                giro_created_or_updated += 1

        closure_matched = 0
        closure_created = 0
        closure_linked_existing = 0
        closure_already_closed = 0
        closure_unmatched = []
        closure_ambiguous = []
        auto_charges_removed = 0

        for rec in payload.get("closures", []):
            key2 = (rec.get("match_name") or _norm_name(rec.get("name")), rec.get("match_vehicle") or _norm_vehicle(rec.get("vehicle_number")))
            member, match_status = _pick_member(members.get(key2, []), prefer_active=True)
            if not member:
                target = {"name": rec.get("name"), "vehicle_number": rec.get("vehicle_number"), "closure_type": rec.get("closure_type"), "closure_date": rec.get("closure_date"), "source_row": rec.get("source_row")}
                (closure_ambiguous if match_status.startswith("ambiguous") else closure_unmatched).append(target)
                continue

            closure_matched += 1
            ckey = (key2[0], key2[1], _norm_closure_type(rec.get("closure_type")), str(rec.get("closure_date") or "")[:10])
            existing = closures_idx.get(ckey, [])
            closure = existing[0] if len(existing) == 1 else None

            if closure is None and member.closure_id:
                linked = db.query(models.Closure).filter(
                    models.Closure.id == member.closure_id,
                    models.Closure.deleted_at.is_(None),
                ).first()
                # 이미 다른 폐업이력과 연결된 회원이면 중복 이력을 만들지 않는다.
                if linked:
                    closure = linked
                    closure_already_closed += 1

            if closure is None:
                if (member.status or "active") != "active":
                    # 상태는 이미 종료인데 정확한 이력 연결을 확인할 수 없으면 보수적으로 건너뜀.
                    closure_unmatched.append({
                        "name": rec.get("name"), "vehicle_number": rec.get("vehicle_number"),
                        "closure_type": rec.get("closure_type"), "closure_date": rec.get("closure_date"),
                        "source_row": rec.get("source_row"), "reason": "member_already_closed_without_exact_closure",
                    })
                    continue
                closure = _create_closure(db, member, rec)
                closures_idx.setdefault(ckey, []).append(closure)
                closure_created += 1
            else:
                closure_linked_existing += 1
                if not closure.member_id:
                    closure.member_id = member.id
                if not closure.original_management_number:
                    closure.original_management_number = member.management_number or ""
                if not closure.original_mgmt_match_status:
                    closure.original_mgmt_match_status = "linked"

            member.status = "closed"
            member.closure_id = closure.id
            auto_charges_removed += _remove_future_auto_charges(db, member.id, rec.get("closure_date") or closure.closure_date or "")

        result = {
            "patch_id": PATCH_ID,
            "status": "applied" if not (giro_unmatched or giro_ambiguous or closure_unmatched or closure_ambiguous) else "applied_with_skips",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": {
                "giro_preferred": len(payload.get("giro_preferred", [])),
                "closures": len(payload.get("closures", [])),
            },
            "giro": {
                "matched": giro_matched,
                "created_or_updated": giro_created_or_updated,
                "unmatched": giro_unmatched,
                "ambiguous": giro_ambiguous,
            },
            "closures": {
                "matched": closure_matched,
                "created": closure_created,
                "linked_existing": closure_linked_existing,
                "already_closed": closure_already_closed,
                "future_auto_charges_removed": auto_charges_removed,
                "unmatched": closure_unmatched,
                "ambiguous": closure_ambiguous,
            },
            "safety": {
                "balance_overwrite": False,
                "payments_modified": False,
                "manual_adjustments_modified": False,
                "matching": "exact_normalized_name_plus_vehicle_only",
                "future_charge_delete_scope": "source=auto and billing_month>closure_month only",
            },
        }
        _save_state(db, result)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/flags")
def get_giro_flags(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _ensure_schema()
    rows = db.query(ReceivableMemberFlag).filter(
        ReceivableMemberFlag.flag_key == FLAG_KEY,
        ReceivableMemberFlag.flag_value == FLAG_VALUE,
    ).all()
    return {
        "flag": FLAG_KEY,
        "count": len(rows),
        "member_ids": [int(r.member_id) for r in rows],
    }


@router.get("/status")
def get_patch_status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    _ensure_schema()
    state = _read_state(db)
    return state or {"patch_id": PATCH_ID, "status": "not_applied"}


@router.post("/apply")
def force_apply(_=Depends(require_admin)):
    try:
        return apply_once(force=True)
    except Exception as exc:
        raise HTTPException(500, f"2026-10-07 수납·미수금 패치 적용 실패: {type(exc).__name__}: {exc}")
