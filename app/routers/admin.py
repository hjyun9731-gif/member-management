"""
관리자 전용: 테이블 초기화 엔드포인트
- 잘못 업로드된 데이터 전체 삭제
- admin 권한만 사용 가능
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.database import get_db
from app.auth import require_admin
from app import models

router = APIRouter()

TABLE_MAP = {
    "license_holders":  models.LicenseHolder,
    "transfer_ledger":  models.TransferLedger,
    "closures":         models.Closure,
    "change_history":   models.ChangeHistory,
    "candidates":       models.Candidate,
    "upload_histories": models.UploadHistory,
}


@router.delete("/reset/{table_name}")
async def reset_table(
    table_name: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """특정 테이블의 모든 데이터를 soft-delete로 초기화"""
    model = TABLE_MAP.get(table_name)
    if not model:
        raise HTTPException(400, f"알 수 없는 테이블: {table_name}. 가능: {list(TABLE_MAP.keys())}")

    now = datetime.now(timezone.utc)
    if hasattr(model, "deleted_at"):
        updated = db.query(model).filter(model.deleted_at.is_(None)).update(
            {"deleted_at": now}, synchronize_session=False
        )
    else:
        # UploadHistory는 deleted_at 없으므로 실제 삭제
        updated = db.query(model).delete(synchronize_session=False)

    db.commit()
    return {"ok": True, "table": table_name, "deleted_count": updated}


@router.delete("/reset-all")
async def reset_all(
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """모든 업로드 데이터 초기화 (사용자/설정 제외)"""
    now = datetime.now(timezone.utc)
    counts = {}
    for name, model in TABLE_MAP.items():
        if hasattr(model, "deleted_at"):
            cnt = db.query(model).filter(model.deleted_at.is_(None)).update(
                {"deleted_at": now}, synchronize_session=False
            )
        else:
            cnt = db.query(model).delete(synchronize_session=False)
        counts[name] = cnt
    db.commit()
    return {"ok": True, "deleted": counts}


@router.get("/db-status")
async def db_status(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Railway DB 실제 상태 확인용"""
    from sqlalchemy import text, func

    # TransferLedger 상태
    tl_total = db.query(models.TransferLedger).filter(models.TransferLedger.deleted_at.is_(None)).count()
    tl_has_mgmt = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.management_number.isnot(None),
        models.TransferLedger.management_number != ''
    ).count()
    tl_has_receipt = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.receipt_date.isnot(None),
        models.TransferLedger.receipt_date != ''
    ).count()

    # 샘플 10건
    samples = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)
    ).limit(10).all()

    return {
        "transfer_ledger": {
            "total": tl_total,
            "has_management_number": tl_has_mgmt,
            "no_management_number": tl_total - tl_has_mgmt,
            "has_receipt_date": tl_has_receipt,
            "samples": [
                {
                    "id": r.id,
                    "management_number": r.management_number,
                    "receipt_date": r.receipt_date,
                                        "approval_date": r.approval_date,
                    "transferee": r.transferee,
                }
                for r in samples
            ]
        },
        "license_holders": {
            "total": db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).count(),
            "individual": db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.category=='개인').count(),
            "delivery": db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None), models.LicenseHolder.category=='택배').count(),
        }
    }


@router.post("/backfill-transfer-mgmt")
async def backfill_transfer_management_numbers(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """기존 TransferLedger에 management_number가 없는 행을 seq_number+data_year로 채움
    형식: 양YY-NN (예: 양26-28, 양00-15)
    """
    import re
    rows = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None),
        models.TransferLedger.management_number.is_(None) |
        (models.TransferLedger.management_number == '')
    ).all()

    updated = skipped = 0
    for r in rows:
        seq = str(r.seq_number or '').strip()
        try:
            seq_int = int(float(seq)) if seq else 0
        except (ValueError, TypeError):
            seq_int = 0

        if seq_int <= 0:
            skipped += 1
            continue

        # data_year 또는 receipt_date/approval_date에서 연도 추출
        year = r.data_year if hasattr(r, 'data_year') and r.data_year else None
        if not year:
            for date_field in [r.receipt_date, r.approval_date]:
                if date_field:
                    m = re.search(r'(\d{2,4})[.\-/]', str(date_field))
                    if m:
                        y = int(m.group(1))
                        year = (2000 + y) if y <= 99 and y >= 0 else y
                        break

        if not year:
            skipped += 1
            continue

        yy = str(year % 100).zfill(2)
        r.management_number = f"양{yy}-{seq_int}"
        updated += 1

    db.commit()
    return {
        "updated": updated,
        "skipped": skipped,
        "total": len(rows),
        "message": f"management_number 채우기 완료: {updated}건 업데이트"
    }


@router.post("/fix-transfer-dates")
async def fix_transfer_dates(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """기존 TransferLedger 데이터의 날짜 보정:
    raw_data의 원본 엑셀 값(접수일자, 인가일자)을 읽어서
    receipt_date / approval_date로 정확히 저장.
    처리일자(process_date) 개념 제거.
    """
    import re

    def _clean(v):
        if not v: return None
        s = str(v).strip().rstrip('.')
        return s if s and s.lower() not in ('nan','none','nat','') else None

    rows = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)
    ).all()

    fixed = skipped = 0
    for r in rows:
        if not isinstance(r.raw_data, dict):
            skipped += 1
            continue

        rd = r.raw_data
        # 접수일자: raw_data에서 정규화된 키 탐색
        receipt = _clean(rd.get('접수일자') or rd.get('접수\n일자') or rd.get('접수 일자'))
        approval = _clean(rd.get('인가일자') or rd.get('인가\n일자') or rd.get('인가 일자'))

        changed = False
        if receipt and r.receipt_date != receipt:
            r.receipt_date = receipt
            changed = True
        if approval and r.approval_date != approval:
            r.approval_date = approval
            changed = True
        # process_date는 clear (양도양수에 처리일자 없음)
        if r.process_date:
            r.process_date = None
            changed = True

        if changed:
            fixed += 1

    db.commit()
    return {
        "fixed": fixed,
        "skipped": skipped,
        "total": len(rows),
        "message": f"날짜 보정 완료: {fixed}건 수정 (접수일자→receipt_date, 인가일자→approval_date)"
    }


@router.delete("/upload/{history_id}")
async def delete_upload(
    history_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """특정 업로드 이력의 데이터 삭제."""
    from datetime import datetime, timezone
    hist = db.query(models.UploadHistory).filter(
        models.UploadHistory.id == history_id
    ).first()
    if not hist:
        raise HTTPException(status_code=404, detail="업로드 이력을 찾을 수 없습니다.")

    now = datetime.now(timezone.utc)
    deleted = {}

    # upload_id가 설정된 행 삭제
    for model, label in [
        (models.LicenseHolder, "면허자현황"),
        (models.TransferLedger, "양도양수대장"),
        (models.Closure, "폐지/폐업현황"),
        (models.ChangeHistory, "변경이력대장"),
    ]:
        rows = db.query(model).filter(
            model.upload_id == history_id,
            model.deleted_at.is_(None),
        ).all()
        for row in rows:
            row.deleted_at = now
        deleted[label] = deleted.get(label, 0) + len(rows)

    # upload_id가 NULL인 경우: file_type 기준으로 created_at 범위 삭제
    # (이전에 upload_id 없이 저장된 데이터)
    file_type = hist.file_type or ""
    if sum(deleted.values()) == 0:
        # upload_id 방식으로 삭제된 게 없으면 file_type + 업로드 시각 기준 삭제
        upload_time = hist.created_at
        if upload_time:
            from sqlalchemy import and_
            import datetime as dt_mod
            # 해당 업로드 직후 1분 이내에 생성된 데이터
            t_from = upload_time - dt_mod.timedelta(seconds=5)
            t_to   = upload_time + dt_mod.timedelta(minutes=2)

            model_map = {
                "이전폐지현황": models.Closure,
                "폐지현황":    models.Closure,
                "양도양수대장": models.TransferLedger,
                "면허자현황":  models.LicenseHolder,
                "변경이력대장": models.ChangeHistory,
                "주소변경등록대장": models.ChangeHistory,
            }
            model = model_map.get(file_type)
            if model:
                rows = db.query(model).filter(
                    model.deleted_at.is_(None),
                    model.created_at >= t_from,
                    model.created_at <= t_to,
                ).all()
                for row in rows:
                    row.deleted_at = now
                deleted[file_type] = len(rows)

    db.delete(hist)
    db.commit()

    total = sum(deleted.values())
    return {
        "deleted_total": total,
        "deleted_by_type": deleted,
        "message": f"업로드 이력 #{history_id} ({file_type}) 삭제 완료: 총 {total}건",
    }


@router.delete("/upload-by-filetype/{file_type}")
async def delete_upload_by_filetype(
    file_type: str,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """파일 종류 전체 삭제 (재업로드 전 정리용).
    예: DELETE /api/admin/upload-by-filetype/이전폐지현황
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    model_map = {
        "이전폐지현황": models.Closure,
        "폐지현황":    models.Closure,
        "양도양수대장": models.TransferLedger,
        "면허자현황":  models.LicenseHolder,
        "변경이력대장": models.ChangeHistory,
        "주소변경등록대장": models.ChangeHistory,
    }

    model = model_map.get(file_type)
    if not model:
        raise HTTPException(400, f"지원하지 않는 파일 종류: {file_type}. 가능한 값: {list(model_map.keys())}")

    # data_type으로 추가 필터 (폐지현황 구분)
    q = db.query(model).filter(model.deleted_at.is_(None))
    if file_type == "이전폐지현황":
        q = q.filter(model.data_type == "이전자료")
    elif file_type == "폐지현황":
        q = q.filter(model.data_type == "신규자료")

    rows = q.all()
    for row in rows:
        row.deleted_at = now

    # 관련 업로드 이력도 삭제
    hists = db.query(models.UploadHistory).filter(
        models.UploadHistory.file_type == file_type
    ).all()
    for h in hists:
        db.delete(h)

    db.commit()
    return {
        "deleted_total": len(rows),
        "deleted_histories": len(hists),
        "message": f"{file_type} 전체 삭제 완료: {len(rows)}건",
    }

@router.get("/check-closed-new-members")
async def check_closed_new_members(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """신규등록대장에서 폐업 처리로 숨겨진 회원 현황 확인"""
    closed = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
        models.LicenseHolder.status == "closed",
    ).all()

    return {
        "숨겨진_신규회원수": len(closed),
        "목록": [
            {
                "id": m.id,
                "management_number": m.management_number,
                "name": m.name,
                "vehicle_number": m.vehicle_number,
                "approval_date": m.approval_date,
                "status": m.status,
            }
            for m in closed
        ]
    }


@router.get("/dashboard-verify")
async def dashboard_verify(
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """대시보드 집계 실제 검증"""
    from datetime import datetime
    from app.routers.dashboard import _ext_year

    cur_year = datetime.now().year

    # 1. 신규 집계: 관리번호 신26-* 전체 vs 인가일자 2026년
    shin26_total = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
    ).count()

    shin26_active = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
        models.LicenseHolder.status == "active",
    ).count()

    shin26_closed = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신26%"),
        models.LicenseHolder.status == "closed",
    ).count()

    # 관리번호 기준 연도별 집계 (신YY-* → YY년도, 접수일자/인가일자/status 무관)
    import re as _re
    shin_all = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.management_number.like("신%"),
    ).all()

    by_mgmt_year = {}
    bad_format = []
    for m in shin_all:
        mgmt = (m.management_number or "").strip()
        m2 = _re.match(r'^신(\d{2})[-]', mgmt)
        if m2:
            yy = int(m2.group(1))
            cur_yy = datetime.now().year % 100
            y = 2000 + yy if yy <= cur_yy else 1900 + yy
            by_mgmt_year[y] = by_mgmt_year.get(y, 0) + 1
        else:
            bad_format.append({"id": m.id, "management_number": mgmt, "status": m.status})

    # 2. 폐업현황 집계
    total_closures = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)
    ).count()

    # 3. 전체 회원
    total_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    ).count()

    return {
        "신26_관리번호_전체": shin26_total,
        "신26_active": shin26_active,
        "신26_closed(신규등록대장에서_숨겨진수)": shin26_closed,
        "관리번호기준_연도별_신규수": dict(sorted(by_mgmt_year.items(), reverse=True)),
        "관리번호_형식_불일치": bad_format[:10],
        "폐업현황_전체": total_closures,
        "현재_active_회원수": total_members,
    }


@router.get("/cert-debug")
async def cert_debug(db: Session = Depends(get_db), _=Depends(require_admin)):
    """택배 취업신고/미신고 디버그: 정확한 계산 근거 확인"""
    def _has_val(v):
        return bool(v and str(v).strip() and str(v).strip().lower() not in ('-','x','none','nan'))

    # 전체 택배 원본 (삭제된 것 포함)
    all_delivery_raw = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.category == "택배"
    ).count()

    # 삭제된 택배
    deleted = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.category == "택배",
        models.LicenseHolder.deleted_at.isnot(None)
    ).count()

    # 폐업 처리된 택배 (status=closed)
    closed = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.category == "택배",
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "closed"
    ).count()

    # 현재 유효한 택배 (status=active, deleted_at=NULL)
    valid = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.category == "택배",
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active"
    ).all()

    employed   = [m for m in valid if _has_val(m.certificate_issue_date)]
    unemployed = [m for m in valid if not _has_val(m.certificate_issue_date)]

    return {
        "택배_전체원본": all_delivery_raw,
        "deleted_at있음": deleted,
        "status_closed(폐업)": closed,
        "현재유효택배": len(valid),
        "취업신고(자격증발급일자있음)": len(employed),
        "미신고(자격증발급일자없음)": len(unemployed),
        "검증": f"취업신고{len(employed)} + 미신고{len(unemployed)} = {len(employed)+len(unemployed)} (유효택배{len(valid)}와 {'일치✅' if len(employed)+len(unemployed)==len(valid) else '불일치❌'})",
        "미신고_샘플10": [
            {"관리번호": m.management_number, "지역": m.region,
             "차량번호": m.vehicle_number, "성명": m.name,
             "certificate_issue_date": m.certificate_issue_date or "",
             "status": m.status}
            for m in unemployed[:10]
        ]
    }


@router.get("/vtype-debug")
async def vtype_debug(db: Session = Depends(get_db), _=Depends(require_admin)):
    """미분류 차종 원본값 분석"""
    from app.routers.dashboard import classify_vt
    from collections import Counter

    rows = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active",
    ).all()

    unclassified = []
    fuel_raw = Counter()

    for m in rows:
        cat = classify_vt(m.vehicle_type or "")
        if cat == "미분류":
            vt = (m.vehicle_type or "").strip()
            unclassified.append(vt if vt else "(빈값)")
        fuel_raw[m.fuel_type or "(빈값)"] += 1

    vt_counter = Counter(unclassified)
    return {
        "미분류_총건수": len(unclassified),
        "빈값건수": sum(1 for v in unclassified if v == "(빈값)"),
        "값있는미분류": len([v for v in unclassified if v != "(빈값)"]),
        "원본차종_빈도순_상위50": vt_counter.most_common(50),
        "유종_원본값_빈도순": fuel_raw.most_common(20),
    }


@router.post("/fix-closure-data-type")
async def fix_closure_data_type(db: Session = Depends(get_db), _=Depends(require_admin)):
    """폐-*/양-*/이-* 관리번호는 신규자료로 보정"""
    from datetime import datetime, timezone
    import re as _re

    rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
    ).all()

    fixed = 0
    for r in rows:
        mgmt = (r.management_number or "").strip()
        if _re.match(r'^(폐|양|이)-', mgmt):
            if r.data_type != "신규자료":
                r.data_type = "신규자료"
                fixed += 1
    db.commit()
    return {"보정건수": fixed, "message": f"폐-*/양-*/이-* → 신규자료 보정: {fixed}건"}


@router.delete("/old-closure-data")
async def delete_old_closure_data(db: Session = Depends(get_db), _=Depends(require_admin)):
    """이전자료 삭제 (폐-*/양-*/이-* 제외)"""
    from datetime import datetime, timezone
    import re as _re

    all_rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)
    ).all()

    total = len(all_rows)
    old_data = [r for r in all_rows if r.data_type == "이전자료"]
    protected = [r for r in old_data if _re.match(r'^(폐|양|이)-', r.management_number or "")]
    to_delete = [r for r in old_data if not _re.match(r'^(폐|양|이)-', r.management_number or "")]

    now = datetime.now(timezone.utc)
    for r in to_delete:
        r.deleted_at = now
    db.commit()

    return {
        "삭제전_전체": total,
        "이전자료": len(old_data),
        "보호됨(폐양이-)": len(protected),
        "삭제됨": len(to_delete),
        "삭제후_남은건수": total - len(to_delete),
    }
