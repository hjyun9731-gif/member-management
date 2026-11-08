"""관리자 전용 '데이터 정합성 점검' API - 전부 읽기 전용(GET).

이 라우터에는 데이터를 변경하는 엔드포인트가 없다. 정정(쓰기) 기능은 운영 DB 백업과
관리자 승인이 확보되기 전까지 구현/노출하지 않는다.
"""
import csv
import io
import threading
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.services import data_integrity as di
from app.services.contact_parser import parse_contact
from app.services.csv_security import safe_csv_cell as _csv_cell

router = APIRouter(prefix="/api/admin/data-integrity", tags=["데이터 정합성"])




_CACHE = {"at": 0.0, "data": None}
_LOCK = threading.Lock()
_TTL = 120  # 초. 전체 스캔 부하를 줄이기 위해 짧게 캐시한다.


def _scan(db: Session, refresh: bool = False) -> dict:
    with _LOCK:
        if not refresh and _CACHE["data"] is not None and time.time() - _CACHE["at"] < _TTL:
            return _CACHE["data"]
        data = di.run_scan(db)
        _CACHE["data"], _CACHE["at"] = data, time.time()
        return data


def _filter(findings: List[dict], code, severity, category, region, status, mgmt, vehicle, name):
    def ok(f):
        if code and f["code"] != code:
            return False
        if severity and f["severity"] != severity:
            return False
        if category and f["category"] != category:
            return False
        if region and region not in (f["region"] or ""):
            return False
        if status and f["status"] != status:
            return False
        if mgmt and mgmt not in (f["management_number"] or ""):
            return False
        if vehicle and vehicle.replace(" ", "") not in (f["vehicle_number"] or "").replace(" ", ""):
            return False
        if name and name not in (f["name"] or ""):
            return False
        return True
    return [f for f in findings if ok(f)]


@router.get("/summary")
def summary(refresh: bool = False, db: Session = Depends(get_db), _=Depends(require_admin)):
    data = _scan(db, refresh)
    return {**data["summary"], "check_names": di.CHECKS, "read_only": True}


@router.get("/findings")
def findings(
    code: Optional[str] = None, severity: Optional[str] = None, category: Optional[str] = None,
    region: Optional[str] = None, status: Optional[str] = None, management_number: Optional[str] = None,
    vehicle_number: Optional[str] = None, name: Optional[str] = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    refresh: bool = False, db: Session = Depends(get_db), _=Depends(require_admin),
):
    data = _scan(db, refresh)
    rows = _filter(data["findings"], code, severity, category, region, status, management_number, vehicle_number, name)
    start = (page - 1) * page_size
    return {"total": len(rows), "page": page, "page_size": page_size, "items": rows[start:start + page_size]}


@router.get("/mgmt-number")
def mgmt_number(number: str = Query(..., min_length=1, max_length=64), db: Session = Depends(get_db), _=Depends(require_admin)):
    return di.diagnose_mgmt_number(db, number)


@router.get("/parse-contact")
def parse_contact_api(value: str = Query(""), _=Depends(require_admin)):
    p = parse_contact(value)
    return {"raw": p.raw, "status": p.status, "numbers": [n.__dict__ for n in p.numbers],
            "needs_review": p.needs_review, "residual": p.residual}


@router.get("/export")
def export_csv(
    code: Optional[str] = None, severity: Optional[str] = None,
    category: Optional[str] = None, region: Optional[str] = None, status: Optional[str] = None,
    management_number: Optional[str] = None, vehicle_number: Optional[str] = None, name: Optional[str] = None,
    db: Session = Depends(get_db), _=Depends(require_admin),
):
    """관리자 전용 CSV. 주민등록번호는 서비스 단계에서 이미 마스킹되어 있다."""
    data = _scan(db, False)
    rows = _filter(data["findings"], code, severity, category, region, status, management_number, vehicle_number, name)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["오류유형", "판정", "대상자", "관리번호", "차량번호", "테이블", "레코드ID", "필드", "현재값", "문제내용", "권장처리"])
    for f in rows:
        w.writerow([_csv_cell(v) for v in (
            f"{f['code']} {f['type']}", f["severity"], f["name"], f["management_number"], f["vehicle_number"],
            f["table"], f["record_id"], f["field"], f["current_value"], f["message"], f["recommendation"],
        )])
    out = io.BytesIO(("\ufeff" + buf.getvalue()).encode("utf-8"))
    return StreamingResponse(out, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=data_integrity.csv"})
