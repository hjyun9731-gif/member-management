"""2026-10-07 수납·미수금 보정 V2.

- 지로희망은 원장 명단을 읽기 전용 표시 데이터로 제공한다.
- 종료자는 성명+차량번호 정확일치만 건별 처리한다.
- 한 건 오류가 전체 패치를 롤백하지 않는다.
- 기존 입금/연락/V4 보정/수동 잔액은 건드리지 않는다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import crud, models
from app.auth import get_current_user, require_admin, admin_for_writes
from app.database import SessionLocal, get_db
from app.receivables_models import ReceivableCharge, ReceivableSystemState

router = APIRouter(prefix="/api/receivables/patch-20261007-v2", tags=["수납미수금-20261007-v2"], dependencies=[Depends(admin_for_writes)])

PATCH_ID = "receivables_patch_20261007_giro_closures_v2"
STATE_KEY = PATCH_ID
DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "receivables_patch_20261007.json"
PATCH_MEMO = "[20261007 미수금원장 종료자정리 V2 자동반영]"


def _norm_name(value: object) -> str:
    s = str(value or "").strip()
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
    # V1 데이터 파일을 그대로 쓰되 내용 건수/키를 검증한다.
    if not isinstance(payload.get("giro_preferred"), list) or not isinstance(payload.get("closures"), list):
        raise RuntimeError("2026-10-07 패치 데이터 형식이 올바르지 않습니다.")
    return payload


def _member_index(db: Session) -> Dict[Tuple[str, str], List[models.LicenseHolder]]:
    rows = db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all()
    out: Dict[Tuple[str, str], List[models.LicenseHolder]] = {}
    for m in rows:
        key = (_norm_name(m.name), _norm_vehicle(m.vehicle_number))
        if key[0] and key[1]:
            out.setdefault(key, []).append(m)
    return out


def _pick_member(candidates: Iterable[models.LicenseHolder]):
    items = list(candidates)
    active = [m for m in items if (m.status or "active") == "active"]
    if len(active) == 1:
        return active[0], "matched_active"
    if len(active) > 1:
        return None, "ambiguous_active"
    if len(items) == 1:
        return items[0], "matched_closed"
    if len(items) > 1:
        return None, "ambiguous"
    return None, "unmatched"


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


def _remove_future_auto_charges(db: Session, member_id: int, closure_date: str) -> int:
    month = str(closure_date or "")[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return 0
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


def _save_state(db: Session, result: dict) -> None:
    row = db.query(ReceivableSystemState).filter(ReceivableSystemState.key == STATE_KEY).first()
    value = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if row:
        row.value = value
    else:
        db.add(ReceivableSystemState(key=STATE_KEY, value=value))


def _read_state(db: Session):
    row = db.query(ReceivableSystemState).filter(ReceivableSystemState.key == STATE_KEY).first()
    if not row or not row.value:
        return None
    try:
        return json.loads(row.value)
    except Exception:
        return {"patch_id": PATCH_ID, "status": "state_parse_error"}


def _existing_closure_for_member(db: Session, member: models.LicenseHolder):
    if getattr(member, "closure_id", None):
        c = db.query(models.Closure).filter(
            models.Closure.id == member.closure_id,
            models.Closure.deleted_at.is_(None),
        ).first()
        if c:
            return c
    # 과거 데이터에서 closure_id 연결만 빠진 경우, 성명+차량번호 정확일치 후보를 찾는다.
    rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
        models.Closure.name == member.name,
    ).all()
    exact = [c for c in rows if _norm_vehicle(c.vehicle_number) == _norm_vehicle(member.vehicle_number)]
    return exact[0] if len(exact) == 1 else None


def apply_once(force: bool = False) -> dict:
    payload = _load_payload()
    db = SessionLocal()
    try:
        previous = _read_state(db)
        if previous and not force and previous.get("status") in {"applied", "applied_with_skips"}:
            return {**previous, "status": "already_applied"}

        members = _member_index(db)
        stats = {
            "matched": 0,
            "created": 0,
            "linked_existing": 0,
            "already_closed": 0,
            "future_auto_charges_removed": 0,
            "unmatched": [],
            "ambiguous": [],
            "errors": [],
        }

        for rec in payload.get("closures", []):
            key = (
                rec.get("match_name") or _norm_name(rec.get("name")),
                rec.get("match_vehicle") or _norm_vehicle(rec.get("vehicle_number")),
            )
            member, match_status = _pick_member(members.get(key, []))
            target = {
                "name": rec.get("name"),
                "vehicle_number": rec.get("vehicle_number"),
                "closure_type": rec.get("closure_type"),
                "closure_date": rec.get("closure_date"),
                "source_row": rec.get("source_row"),
            }
            if member is None:
                (stats["ambiguous"] if match_status.startswith("ambiguous") else stats["unmatched"]).append(target)
                continue

            try:
                stats["matched"] += 1
                closure = _existing_closure_for_member(db, member)
                if closure:
                    if not getattr(member, "closure_id", None):
                        member.closure_id = closure.id
                    member.status = "closed"
                    if not getattr(closure, "member_id", None):
                        closure.member_id = member.id
                    if hasattr(closure, "original_management_number") and not closure.original_management_number:
                        closure.original_management_number = member.management_number or ""
                    if hasattr(closure, "original_mgmt_match_status") and not closure.original_mgmt_match_status:
                        closure.original_mgmt_match_status = "linked"
                    stats["linked_existing"] += 1
                elif (member.status or "active") != "active":
                    # 종료 상태인데 이력이 없으면 폐업관리 이력을 보강한다.
                    ctype = _norm_closure_type(rec.get("closure_type"))
                    closure = crud.close_member_no_commit(
                        db,
                        member.id,
                        ctype,
                        rec.get("closure_date") or "",
                        _new_management_number(db, ctype),
                        rec.get("reason") or PATCH_MEMO,
                    )
                    if hasattr(closure, "memo"):
                        closure.memo = PATCH_MEMO
                    stats["created"] += 1
                    stats["already_closed"] += 1
                else:
                    ctype = _norm_closure_type(rec.get("closure_type"))
                    closure = crud.close_member_no_commit(
                        db,
                        member.id,
                        ctype,
                        rec.get("closure_date") or "",
                        _new_management_number(db, ctype),
                        rec.get("reason") or PATCH_MEMO,
                    )
                    if hasattr(closure, "memo"):
                        closure.memo = PATCH_MEMO
                    stats["created"] += 1

                stats["future_auto_charges_removed"] += _remove_future_auto_charges(
                    db, member.id, rec.get("closure_date") or ""
                )
                db.commit()  # 건별 확정: 다음 건 오류가 앞선 정상 처리까지 되돌리지 않음
            except Exception as exc:
                db.rollback()
                stats["errors"].append({**target, "error": f"{type(exc).__name__}: {exc}"})

        result = {
            "patch_id": PATCH_ID,
            "status": "applied" if not (stats["unmatched"] or stats["ambiguous"] or stats["errors"]) else "applied_with_skips",
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "source_counts": {
                "giro_preferred": len(payload.get("giro_preferred", [])),
                "closures": len(payload.get("closures", [])),
            },
            "closures": stats,
            "safety": {
                "payments_modified": False,
                "contacts_modified": False,
                "v4_adjustments_modified": False,
                "manual_balance_modified": False,
                "matching": "exact_normalized_name_plus_vehicle_only",
                "transaction_scope": "per_closure_record",
            },
        }
        _save_state(db, result)
        db.commit()
        return result
    finally:
        db.close()


@router.get("/giro-targets")
def giro_targets(_=Depends(get_current_user)):
    payload = _load_payload()
    # DB에 플래그 행을 만들지 않아도 프런트에서 성명+차량번호로 바로 표시할 수 있다.
    items = [
        {
            "name": r.get("name") or "",
            "vehicle_number": r.get("vehicle_number") or "",
            "match_name": r.get("match_name") or _norm_name(r.get("name")),
            "match_vehicle": r.get("match_vehicle") or _norm_vehicle(r.get("vehicle_number")),
        }
        for r in payload.get("giro_preferred", [])
    ]
    return {"count": len(items), "items": items}


@router.get("/status")
def status(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return _read_state(db) or {
        "patch_id": PATCH_ID,
        "status": "not_applied",
        "source_counts": {
            "giro_preferred": len(_load_payload().get("giro_preferred", [])),
            "closures": len(_load_payload().get("closures", [])),
        },
    }


@router.post("/apply")
def manual_apply(_=Depends(require_admin)):
    try:
        return apply_once(force=True)
    except Exception as exc:
        raise HTTPException(500, f"2026-10-07 V2 패치 적용 실패: {type(exc).__name__}: {exc}")
