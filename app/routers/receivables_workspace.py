"""수납·미수금 통합 업무화면(엑셀형) 데이터 API — 전부 읽기 전용(GET).

원칙
  · 이 모듈은 DB를 수정하지 않는다(INSERT/UPDATE/DELETE 없음). 점검(audit)은 Postgres READ ONLY 트랜잭션에서만 실행한다.
  · 인증은 기존 로그인(get_current_user)을 그대로 쓴다. 점검 화면은 관리자 전용(require_admin).
  · 사용자 입력은 SQL에 문자열로 합치지 않는다(ORM 바인딩만 사용). 점검 SQL은 코드에 고정되어 있다.
"""
from __future__ import annotations

import csv
import io
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session

from app import models
from app.auth import get_current_user, require_admin
from app.database import get_db
from app.receivables_models import (
    ReceivableCharge,
    ReceivableContactLog,
    ReceivableImportBatch,
    ReceivableImportRow,
    ReceivablePayment,
    ReceivableProfile,
)

router = APIRouter()
_STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
LEGACY_CUTOFF_MONTH = "2026-09"      # 9월말 확정잔액 이후 부과만 더한다(V4 기준)
LEGACY_CUTOFF_DATE = "2026-09-30"    # 9/30 이후 수납만 뺀다(V4 기준)

TABS = [
    ("bank", "통장 원장"), ("ledger", "미수금 원장"), ("monthly", "월별 부과·수납"),
    ("match", "자동매칭 결과"), ("review", "확인 필요 거래"), ("closed", "폐업·탈퇴"),
    ("suspense", "가수금(미확인 입금)"), ("history", "수정 이력"), ("audit", "점검(관리자)"),
]


def _mask(name: str | None) -> str:
    n = (name or "").strip()
    return (n[:1] + "*" * (len(n) - 1)) if n else ""


@router.get("/receivables/workspace")
def workspace_page():
    return FileResponse(os.path.join(_STATIC, "receivables_workspace.html"),
                        headers={"Cache-Control": "no-store"})


def _col(key, label, kind="text", width=120, **kw):
    return {"key": key, "label": label, "type": kind, "width": width, **kw}


def _finish(tab, columns, rows, total, page, size, summary=None, fmt="json", note=""):
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c["label"] for c in columns])
        for r in rows:
            w.writerow([r.get(c["key"], "") for c in columns])
        data = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return StreamingResponse(io.BytesIO(data), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="receivables_{tab}.csv"'})
    return {"tab": tab, "columns": columns, "rows": rows, "total": total, "page": page,
            "size": size, "summary": summary or {}, "note": note}


def _paged(query, page, size, fmt):
    total = query.count()
    if fmt == "csv":
        return total, query.limit(20000).all()
    return total, query.offset((page - 1) * size).limit(size).all()


def _row_time(raw: dict | None) -> str:
    raw = raw or {}
    return raw.get("_trade_time") or raw.get("거래시간") or ""


def _row_balance(raw: dict | None):
    raw = raw or {}
    if raw.get("_balance") is not None:
        return raw.get("_balance")
    for k, v in raw.items():
        if "잔액" in str(k):
            try:
                return int(str(v).replace(",", ""))
            except Exception:
                return v
    return None


STATUS_LABEL = {"matched": "자동확정", "review": "확인필요", "duplicate": "중복제외", "posted": "수납반영"}


def _import_rows(db, tab, q, status, page, size, sort, direction, fmt, base_filter=None):
    M = models.LicenseHolder
    query = (db.query(ReceivableImportRow, M.name, M.vehicle_number)
             .outerjoin(M, M.id == ReceivableImportRow.matched_member_id))
    if base_filter is not None:
        query = query.filter(base_filter)
    if status:
        query = query.filter(ReceivableImportRow.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(ReceivableImportRow.payer_name.ilike(like), ReceivableImportRow.memo.ilike(like),
                                 ReceivableImportRow.vehicle_number.ilike(like), M.name.ilike(like),
                                 M.vehicle_number.ilike(like)))
    sort_map = {"transaction_date": ReceivableImportRow.transaction_date, "amount": ReceivableImportRow.amount,
                "payer_name": ReceivableImportRow.payer_name, "status": ReceivableImportRow.status,
                "id": ReceivableImportRow.id}
    col = sort_map.get(sort, ReceivableImportRow.transaction_date)
    order = col.asc() if direction == "asc" else col.desc()
    query = query.order_by(order, ReceivableImportRow.id.desc())
    total, items = _paged(query, page, size, fmt)
    rows = []
    for r, mname, mveh in items:
        raw = r.raw_data or {}
        rows.append({
            "id": r.id, "batch": r.batch_id, "transaction_date": r.transaction_date, "time": _row_time(raw),
            "payer_name": r.payer_name or "", "payer_raw": raw.get("_payer_raw", ""),
            "channel": raw.get("거래내용", ""), "amount": r.amount, "balance": _row_balance(raw),
            "status": STATUS_LABEL.get(r.status, r.status), "status_code": r.status,
            "member_id": r.matched_member_id, "member": mname or "", "vehicle": mveh or r.vehicle_number or "",
            "reason": r.match_reason or "",
        })
    s = db.query(func.count(ReceivableImportRow.id), func.coalesce(func.sum(ReceivableImportRow.amount), 0))
    if base_filter is not None:
        s = s.filter(base_filter)
    cnt, amt = s.first()
    cols = [
        _col("transaction_date", "거래일", "date", 92, sortable=True), _col("time", "시각", "text", 74),
        _col("payer_name", "입금자", "text", 110, sortable=True), _col("payer_raw", "거래기록사항(원문)", "text", 160),
        _col("channel", "거래내용", "text", 96), _col("amount", "입금액", "money", 96, sortable=True),
        _col("balance", "거래 후 잔액", "money", 110), _col("status", "상태", "badge", 82, sortable=True),
        _col("member", "연결 회원", "text", 96), _col("vehicle", "차량번호", "text", 110),
        _col("reason", "매칭 근거 / 사유", "text", 300), _col("batch", "배치", "num", 56),
    ]
    return _finish(tab, cols, rows, total, page, size, {"건수": cnt, "금액합계": int(amt)}, fmt)


def _ledger(db, q, status, page, size, sort, direction, fmt):
    """미수금 원장 — 회원 상세·목록·통계와 같은 잔액식(receivables._balance_sql_core)을 쓴다."""
    from app.routers import receivables as R
    M, P = models.LicenseHolder, ReceivableProfile
    charges_sq, payments_sq = R._charge_payment_subqueries(db)
    charges = func.coalesce(charges_sq.c.charge_total, 0)
    pays = func.coalesce(payments_sq.c.payment_total, 0)
    adjs = func.coalesce(payments_sq.c.adjustment_total, 0)
    balance = R._balance_sql_core(charges_sq, payments_sq)
    last_pay = (db.query(func.max(ReceivablePayment.payment_date))
                .filter(ReceivablePayment.member_id == P.member_id, ReceivablePayment.cancelled_at.is_(None))
                .correlate(P).scalar_subquery())
    base = (db.query(P.member_id, M.name, M.vehicle_number, M.category, M.status, P.account_type, P.unit_fee,
                     P.legacy_balance, charges, pays, adjs, balance, last_pay, P.legacy_note)
            .join(M, M.id == P.member_id)
            .outerjoin(charges_sq, charges_sq.c.member_id == P.member_id)
            .outerjoin(payments_sq, payments_sq.c.member_id == P.member_id)
            .filter(M.deleted_at.is_(None)))
    query = base
    if status == "active":
        query = query.filter(or_(M.status.is_(None), M.status == "", M.status == "active"))
    elif status == "closed":
        query = query.filter(M.status == "closed")
    elif status == "unpaid":
        query = query.filter(balance > 0)
    elif status == "credit":
        query = query.filter(balance < 0)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(M.name.ilike(like), M.vehicle_number.ilike(like)))
    sort_map = {"name": M.name, "balance": balance, "legacy_balance": P.legacy_balance, "last": last_pay,
                "account_type": P.account_type}
    col = sort_map.get(sort, balance)
    query = query.order_by(col.asc() if direction == "asc" else col.desc(), P.member_id)
    total, items = _paged(query, page, size, fmt)
    rows = [{
        "member_id": r[0], "name": r[1] or "", "vehicle": r[2] or "", "category": r[3] or "", "status": r[4] or "active",
        "account_type": r[5] or "", "unit_fee": r[6], "legacy_balance": r[7], "charges": int(r[8] or 0),
        "payments": int(r[9] or 0), "adjustments": int(r[10] or 0), "balance": int(r[11] or 0),
        "last_payment": r[12] or "", "note": (r[13] or "")[:120],
    } for r in items]
    act = [x for x in base.filter(or_(M.status.is_(None), M.status == "", M.status == "active")).all()]
    bals = [int(x[11] or 0) for x in act]
    summary = {"활성 회원": len(act), "미수 합계": sum(b for b in bals if b > 0), "초과납(음수) 합계": sum(b for b in bals if b < 0)}
    cols = [
        _col("name", "성명", "text", 90, sortable=True, sticky=True), _col("vehicle", "차량번호", "text", 110, sticky=True),
        _col("category", "구분", "text", 56), _col("status", "상태", "text", 62),
        _col("account_type", "계정", "text", 70, sortable=True), _col("unit_fee", "월부과", "money", 72),
        _col("legacy_balance", "기준잔액", "money", 96, sortable=True), _col("charges", "유효 부과", "money", 92),
        _col("payments", "실제 수납", "money", 92), _col("adjustments", "정정(입금 아님)", "money", 108),
        _col("balance", "현재 미수금", "money", 110, sortable=True),
        _col("last_payment", "마지막 입금일", "date", 100, sortable=True), _col("note", "비고(미수금)", "text", 280),
    ]
    return _finish("ledger", cols, rows, total, page, size, summary, fmt,
                   note="현재 미수금 = 기준잔액 + 유효 부과 − 실제 수납 − 정정. 회원 상세·목록·통계와 같은 계산식입니다(정정은 수납이 아닙니다).")


def case_pos(expr):
    from sqlalchemy import case
    return case((expr > 0, expr), else_=0)


def case_neg(expr):
    from sqlalchemy import case
    return case((expr < 0, expr), else_=0)


def _monthly(db, page, size, fmt):
    chg = dict(db.query(ReceivableCharge.billing_month, func.sum(ReceivableCharge.amount)).group_by(ReceivableCharge.billing_month).all())
    cnt = dict(db.query(ReceivableCharge.billing_month, func.count(func.distinct(ReceivableCharge.member_id)))
               .filter(ReceivableCharge.amount > 0).group_by(ReceivableCharge.billing_month).all())
    mon = func.substr(ReceivablePayment.payment_date, 1, 7)
    pay = dict(db.query(mon, func.sum(ReceivablePayment.amount)).filter(ReceivablePayment.cancelled_at.is_(None)).group_by(mon).all())
    canc = dict(db.query(mon, func.sum(ReceivablePayment.amount)).filter(ReceivablePayment.cancelled_at.isnot(None)).group_by(mon).all())
    months = sorted(set(chg) | set(pay) | set(canc), reverse=True)
    rows = [{"month": m, "members": cnt.get(m, 0), "charged": int(chg.get(m) or 0), "paid": int(pay.get(m) or 0),
             "diff": int(chg.get(m) or 0) - int(pay.get(m) or 0), "cancelled": int(canc.get(m) or 0)} for m in months if m]
    total = len(rows)
    rows = rows if fmt == "csv" else rows[(page - 1) * size: page * size]
    cols = [_col("month", "월", "text", 80), _col("members", "부과 회원수", "num", 100), _col("charged", "부과액", "money", 110),
            _col("paid", "유효 수납액", "money", 110), _col("diff", "부과−수납", "money", 110),
            _col("cancelled", "취소된 수납", "money", 110)]
    return _finish("monthly", cols, rows, total, page, size, {}, fmt,
                   note="월별 합계입니다. 회원별 월별 미납은 '미수금 원장'에서 회원을 선택해 기존 상세화면으로 확인하세요(이월 상계 반영).")


def _closed(db, q, page, size, sort, direction, fmt):
    M, C, P = models.LicenseHolder, models.Closure, ReceivableProfile
    query = (db.query(M.id, M.name, M.vehicle_number, C.closure_type, C.closure_date, C.management_number, P.legacy_balance, P.legacy_note, M.memo)
             .outerjoin(C, and_(C.id == M.closure_id, C.deleted_at.is_(None))).outerjoin(P, P.member_id == M.id)
             .filter(M.deleted_at.is_(None), M.status == "closed"))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(M.name.ilike(like), M.vehicle_number.ilike(like)))
    query = query.order_by(C.closure_date.desc().nullslast() if hasattr(C.closure_date, "desc") else C.closure_date, M.id.desc())
    total, items = _paged(query, page, size, fmt)
    rows = [{"member_id": r[0], "name": r[1] or "", "vehicle": r[2] or "", "type": r[3] or "", "date": r[4] or "",
             "mgmt": r[5] or "", "legacy_balance": r[6], "note": (r[7] or "")[:100], "memo": (r[8] or "")[:100]} for r in items]
    cols = [_col("name", "성명", "text", 90, sticky=True), _col("vehicle", "차량번호", "text", 110), _col("type", "구분", "text", 70),
            _col("date", "폐업일", "date", 92), _col("mgmt", "관리번호", "text", 90), _col("legacy_balance", "확정잔액", "money", 100),
            _col("note", "비고(미수금)", "text", 240), _col("memo", "비고(회원)", "text", 240)]
    return _finish("closed", cols, rows, total, page, size, {}, fmt)


def _history(db, q, page, size, fmt):
    M = models.LicenseHolder
    ev: list[dict[str, Any]] = []
    for p, name in (db.query(ReceivablePayment, M.name).outerjoin(M, M.id == ReceivablePayment.member_id)
                    .filter(ReceivablePayment.cancelled_at.isnot(None)).order_by(ReceivablePayment.cancelled_at.desc()).limit(500)):
        ev.append({"at": str(p.cancelled_at)[:19], "kind": "수납 취소", "member": name or "", "amount": p.amount,
                   "actor": p.cancelled_by or "", "detail": f"입금일 {p.payment_date} · {p.method or ''}"})
    for p, name in (db.query(ReceivablePayment, M.name).outerjoin(M, M.id == ReceivablePayment.member_id)
                    .order_by(ReceivablePayment.created_at.desc()).limit(500)):
        ev.append({"at": str(p.created_at)[:19], "kind": "수납 입력", "member": name or "", "amount": p.amount,
                   "actor": p.created_by or "", "detail": f"입금일 {p.payment_date} · {p.method or ''}"})
    for b in db.query(ReceivableImportBatch).filter(ReceivableImportBatch.posted_at.isnot(None)).order_by(ReceivableImportBatch.posted_at.desc()).limit(200):
        ev.append({"at": str(b.posted_at)[:19], "kind": "일괄 반영", "member": "", "amount": b.posted_amount,
                   "actor": b.created_by or "", "detail": f"{b.source_type} {b.source_name or ''} · {b.posted_rows}건 반영(배치 {b.id})"})
    for c, name in (db.query(ReceivableContactLog, M.name).outerjoin(M, M.id == ReceivableContactLog.member_id)
                    .order_by(ReceivableContactLog.created_at.desc()).limit(200)):
        ev.append({"at": str(c.created_at)[:19], "kind": "연락 기록", "member": name or "", "amount": None,
                   "actor": c.created_by or "", "detail": f"{c.contact_method or ''} {c.status or ''} {(c.memo or '')[:60]}"})
    if q:
        ql = q.strip().lower()
        ev = [e for e in ev if ql in (e["member"] + e["detail"] + e["kind"] + e["actor"]).lower()]
    ev.sort(key=lambda e: e["at"], reverse=True)
    total = len(ev)
    ev = ev[:20000] if fmt == "csv" else ev[(page - 1) * size: page * size]
    cols = [_col("at", "일시", "text", 150), _col("kind", "구분", "text", 90), _col("member", "회원", "text", 90),
            _col("amount", "금액", "money", 96), _col("actor", "처리자", "text", 90), _col("detail", "내용", "text", 360)]
    return _finish("history", cols, ev, total, page, size, {}, fmt,
                   note="수납 입력·취소, 일괄 반영, 연락 기록을 시간순으로 모았습니다(각 최근 200~500건).")


@router.get("/api/receivables/workspace/data/{tab}")
def workspace_data(
    tab: str,
    q: str = Query("", max_length=60),
    status: str = Query("", max_length=20),
    page: int = Query(1, ge=1, le=100000),
    size: int = Query(100, ge=10, le=500),
    sort: str = Query("", max_length=30),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    fmt: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if tab == "bank":
        return _import_rows(db, tab, q, status, page, size, sort, dir, fmt)
    if tab == "match":
        return _import_rows(db, tab, q, status, page, size, sort, dir, fmt,
                            base_filter=and_(ReceivableImportRow.matched_member_id.isnot(None),
                                             ReceivableImportRow.status.in_(["matched", "posted"])))
    if tab == "review":
        return _import_rows(db, tab, q, "", page, size, sort, dir, fmt, base_filter=ReceivableImportRow.status == "review")
    if tab == "suspense":
        return _import_rows(db, tab, q, "", page, size, sort, dir, fmt,
                            base_filter=and_(ReceivableImportRow.matched_member_id.is_(None), ReceivableImportRow.status != "duplicate"))
    if tab == "ledger":
        return _ledger(db, q, status, page, size, sort, dir, fmt)
    if tab == "monthly":
        return _monthly(db, page, size, fmt)
    if tab == "closed":
        return _closed(db, q, page, size, sort, dir, fmt)
    if tab == "history":
        return _history(db, q, page, size, fmt)
    raise HTTPException(404, "알 수 없는 탭입니다.")


# ───────────────────────────── 읽기 전용 점검(관리자) ─────────────────────────────
_AUDIT = [
    ("v4_state", "V4 적용 상태", """
        SELECT key, substring(value from '"status":"([^"]*)"') AS status,
               substring(value from '"applied_at":"([^"]*)"') AS applied_at, updated_at::text AS updated_at
        FROM receivable_system_state WHERE key LIKE 'receivables_reconcile_20261008%' ORDER BY key"""),
    ("cancelled", "V4가 취소 처리한 수납(취소자별)", """
        SELECT COALESCE(cancelled_by,'(없음)') AS cancelled_by, COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS amount,
               MIN(payment_date) AS first_date, MAX(payment_date) AS last_date
        FROM receivable_payments WHERE cancelled_at IS NOT NULL GROUP BY cancelled_by ORDER BY 2 DESC"""),
    ("stale_payments", "9/30 이전인데 취소되지 않은 수납(이중 차감 후보, 정상이면 0)", """
        SELECT p.member_id, l.name AS name, '…'||right(l.vehicle_number,4) AS vehicle, COUNT(*) AS cnt, SUM(p.amount) AS amount,
               MIN(p.payment_date) AS first_date, MAX(p.payment_date) AS last_date, r.legacy_balance AS legacy_balance
        FROM receivable_payments p JOIN receivable_profiles r ON r.member_id=p.member_id JOIN license_holders l ON l.id=p.member_id
        WHERE p.cancelled_at IS NULL AND p.payment_date <= '2026-09-30'
          AND r.legacy_note LIKE '%[20261008 월별장부 전수정정 V4]%'
        GROUP BY p.member_id, l.name, l.vehicle_number, r.legacy_balance ORDER BY SUM(p.amount) DESC LIMIT 100"""),
    ("unissued_charged", "자격증명 미발급 택배회원에게 부과된 관리비(월별)", """
        SELECT c.billing_month, COUNT(DISTINCT c.member_id) AS members, SUM(c.amount) AS amount
        FROM receivable_charges c JOIN license_holders l ON l.id=c.member_id
        WHERE l.deleted_at IS NULL AND l.status='active' AND (l.category='택배' OR l.vehicle_number LIKE '%배%')
          AND (NULLIF(btrim(COALESCE(l.certificate_issue_date,'')),'') IS NULL AND NULLIF(btrim(COALESCE(l.certificate_number,'')),'') IS NULL) AND c.amount>0
        GROUP BY c.billing_month ORDER BY 1"""),
    ("unissued_not_held", "미발급 택배 활성회원 중 부과 보류 목록에 없는데 부과가 있는 회원(진짜 오부과 후보)", """
        SELECT l.id AS member_id, l.name AS name, '…'||right(l.vehicle_number,4) AS vehicle,
               (SELECT SUM(amount) FROM receivable_charges c WHERE c.member_id=l.id AND c.amount>0) AS charged,
               (SELECT SUM(amount) FROM receivable_payments p WHERE p.member_id=l.id AND p.cancelled_at IS NULL) AS paid
        FROM license_holders l
        WHERE l.deleted_at IS NULL AND l.status='active' AND (l.category='택배' OR l.vehicle_number LIKE '%배%')
          AND (NULLIF(btrim(COALESCE(l.certificate_issue_date,'')),'') IS NULL AND NULLIF(btrim(COALESCE(l.certificate_number,'')),'') IS NULL)
          AND EXISTS (SELECT 1 FROM receivable_charges c WHERE c.member_id=l.id AND c.amount>0)
          AND NOT EXISTS (SELECT 1 FROM receivable_billing_exclusions e WHERE e.member_id=l.id)
        ORDER BY 4 DESC NULLS LAST LIMIT 100"""),
    ("closed_charged", "폐업·양도 회원인데 폐업월 이후 부과가 남은 경우", """
        SELECT l.id AS member_id, l.name AS name, c.closure_type, c.closure_date,
               MIN(ch.billing_month) AS first_month, MAX(ch.billing_month) AS last_month, COUNT(*) AS cnt, SUM(ch.amount) AS amount
        FROM license_holders l JOIN closures c ON c.id=l.closure_id AND c.deleted_at IS NULL
        JOIN receivable_charges ch ON ch.member_id=l.id
        WHERE c.closure_date ~ '^[0-9]{4}-[0-9]{2}' AND ch.billing_month > substring(c.closure_date,1,7) AND ch.amount>0
        GROUP BY l.id, l.name, c.closure_type, c.closure_date ORDER BY SUM(ch.amount) DESC LIMIT 100"""),
    ("dup_loss", "과거 '중복 제외'된 거래 중 반영 건과 내용이 다른 것(정상 입금 누락 의심)", """
        WITH d AS (SELECT r.transaction_date, r.amount, r.raw_data::text AS raw FROM receivable_import_rows r
                   WHERE r.status='duplicate' AND r.match_reason LIKE '기존/파일내 중복%'),
             p AS (SELECT transaction_date, amount, raw_data::text AS raw FROM receivable_import_rows WHERE status='posted')
        SELECT COUNT(*) AS excluded_cnt,
               COUNT(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM p WHERE p.transaction_date=d.transaction_date AND p.amount=d.amount AND p.raw=d.raw)) AS suspect_cnt,
               COALESCE(SUM(amount) FILTER (WHERE NOT EXISTS (SELECT 1 FROM p WHERE p.transaction_date=d.transaction_date AND p.amount=d.amount AND p.raw=d.raw)),0) AS suspect_amount
        FROM d"""),
    ("dup_payments", "동일 회원·일자·금액·방법의 유효 수납 중복", """
        SELECT COUNT(*) AS groups, COALESCE(SUM(c-1),0) AS extra FROM (
          SELECT COUNT(*) c FROM receivable_payments WHERE cancelled_at IS NULL
          GROUP BY member_id, payment_date, amount, COALESCE(method,'') HAVING COUNT(*)>1) t"""),
    ("triggers", "수납·미수금 테이블의 DB 트리거", """
        SELECT tgrelid::regclass::text AS table_name, tgname AS trigger_name, tgenabled AS enabled
        FROM pg_trigger WHERE NOT tgisinternal AND tgrelid::regclass::text LIKE 'receivable%' ORDER BY 1,2"""),
]


@router.get("/api/receivables/workspace/audit")
def workspace_audit(reveal: bool = Query(False), db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """기본은 이름을 가려서(캡처·공유 안전) 보여 준다. reveal=true 는 관리자 본인 화면용."""
    conn = db.connection()
    if conn.dialect.name != "postgresql":
        return {"ok": False, "error": "점검 화면은 운영 DB(Postgres)에서만 동작합니다.", "sections": []}
    conn.execute(text("SET TRANSACTION READ ONLY"))   # 이 요청 안에서는 어떤 쓰기도 DB가 거부한다
    sections = []
    try:
        for key, title, sql in _AUDIT:
            try:
                res = conn.execute(text(sql))
                cols = list(res.keys())
                rows = [{c: (v if isinstance(v, (int, float, str, type(None))) else str(v)) for c, v in zip(cols, r)} for r in res.fetchall()]
                if not reveal:
                    for r_ in rows:
                        if "name" in r_:
                            r_["name"] = _mask(r_["name"])
                sections.append({"key": key, "title": title, "columns": cols, "rows": rows})
            except Exception as exc:  # 한 항목이 실패해도 나머지는 계속(예: 보류 테이블이 아직 없는 경우)
                db.rollback()
                conn = db.connection()
                conn.execute(text("SET TRANSACTION READ ONLY"))
                sections.append({"key": key, "title": title, "columns": [], "rows": [], "error": f"{type(exc).__name__}"})
    finally:
        db.rollback()
    return {"ok": True, "readonly": True, "sections": sections}
