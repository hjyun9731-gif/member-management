"""
관리자 전용: 테이블 초기화 엔드포인트
- 잘못 업로드된 데이터 전체 삭제
- admin 권한만 사용 가능
"""
from fastapi import APIRouter, Depends, HTTPException, Request
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


@router.get("/routes")
async def list_routes(request: Request, _=Depends(require_admin)):
    routes = []
    for r in request.app.routes:
        if hasattr(r, 'methods'):
            routes.append({"path": r.path, "methods": sorted(list(r.methods))})
    admin = [r for r in routes if '/admin' in r['path']]
    return {"admin_routes": sorted(admin, key=lambda x: x['path'])}


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
    """이전자료 삭제 (폐-*/양-*/이-* 절대 보존).
    삭제 전/후 상세 로그 반환.
    """
    from datetime import datetime, timezone
    import re as _re

    all_rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)
    ).all()

    # 삭제 전 현황
    total = len(all_rows)
    old_data = [r for r in all_rows if r.data_type == "이전자료"]
    new_data = [r for r in all_rows if r.data_type != "이전자료"]
    pye = [r for r in all_rows if (r.management_number or "").startswith("폐-")]
    yang = [r for r in all_rows if (r.management_number or "").startswith("양-")]
    yi   = [r for r in all_rows if (r.management_number or "").startswith("이-")]

    # 삭제 대상: 이전자료 + 관리번호가 폐-/양-/이- 아닌 것
    to_delete = [r for r in old_data
                 if not _re.match(r'^(폐|양|이)-', r.management_number or "")]
    protected = [r for r in old_data
                 if _re.match(r'^(폐|양|이)-', r.management_number or "")]

    # 샘플
    sample = [{"id": r.id, "management_number": r.management_number,
               "data_type": r.data_type, "closure_type": r.closure_type,
               "name": r.name, "receipt_date": r.receipt_date}
              for r in old_data[:20]]

    # 실제 삭제
    now = datetime.now(timezone.utc)
    for r in to_delete:
        r.deleted_at = now
    db.commit()

    return {
        "삭제전": {
            "전체": total,
            "이전자료": len(old_data),
            "신규자료": len(new_data),
            "폐-*": len(pye), "양-*": len(yang), "이-*": len(yi),
            "이전자료샘플20": sample,
        },
        "삭제결과": {
            "삭제됨": len(to_delete),
            "보호됨(폐양이-)": len(protected),
        },
        "삭제후": {
            "남은전체": total - len(to_delete),
            "남은이전자료": len(protected),
        },
    }


@router.post("/cleanup-closures")
async def cleanup_closures(db: Session = Depends(get_db), _=Depends(require_admin)):
    """이전폐지현황 정리: 자료구분 보정 + 이전자료 삭제 (폐-*/양-*/이-* 보호)"""
    from datetime import datetime, timezone
    import re as _re

    all_rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)
    ).all()

    # 삭제 전 현황
    pre = {
        "전체": len(all_rows),
        "이전자료": sum(1 for r in all_rows if r.data_type == "이전자료"),
        "신규자료": sum(1 for r in all_rows if r.data_type != "이전자료"),
        "폐-*": sum(1 for r in all_rows if (r.management_number or "").startswith("폐-")),
        "양-*": sum(1 for r in all_rows if (r.management_number or "").startswith("양-")),
        "이-*": sum(1 for r in all_rows if (r.management_number or "").startswith("이-")),
    }

    now = datetime.now(timezone.utc)

    # 1단계: 폐-*/양-*/이-* → 신규자료 보정
    fixed = 0
    for r in all_rows:
        if _re.match(r"^(폐|양|이)-", r.management_number or ""):
            if r.data_type != "신규자료":
                r.data_type = "신규자료"
                fixed += 1

    # 2단계: 이전자료 + 폐-/양-/이- 아닌 것 삭제
    to_delete = [r for r in all_rows
                 if r.data_type == "이전자료"
                 and not _re.match(r"^(폐|양|이)-", r.management_number or "")]
    for r in to_delete:
        r.deleted_at = now

    db.commit()

    # 삭제 후 현황
    remaining = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None)).all()
    post = {
        "전체": len(remaining),
        "이전자료": sum(1 for r in remaining if r.data_type == "이전자료"),
        "신규자료": sum(1 for r in remaining if r.data_type != "이전자료"),
        "폐-*": sum(1 for r in remaining if (r.management_number or "").startswith("폐-")),
        "양-*": sum(1 for r in remaining if (r.management_number or "").startswith("양-")),
        "이-*": sum(1 for r in remaining if (r.management_number or "").startswith("이-")),
    }

    return {
        "삭제전": pre,
        "보정건수(폐양이→신규자료)": fixed,
        "삭제건수": len(to_delete),
        "삭제후": post,
    }



@router.post("/backfill-closures")
async def backfill_closures(db: Session = Depends(get_db), _=Depends(require_admin)):
    """기존 폐업현황에 회원정보 backfill
    차량번호 기준으로 license_holders와 매칭하여 누락 필드 채움
    """
    import re as _re

    closures = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)
    ).all()

    def _norm_vno(v):
        """차량번호 정규화: 공백/호 제거, 소문자화"""
        import re as _re
        v = (v or "").strip()
        v = _re.sub(r'\s+', '', v)      # 공백 제거
        v = _re.sub(r'호$', '', v)      # 끝 '호' 제거
        return v.lower()

    # 정규화된 차량번호 → 회원 매핑 (active + closed 모두, deleted 포함)
    members = db.query(models.LicenseHolder).all()
    by_vno = {}        # 정확히 일치
    by_vno_norm = {}   # 정규화 일치
    for m in members:
        vno = (m.vehicle_number or "").strip()
        if vno:
            by_vno.setdefault(vno, []).append(m)
            by_vno_norm.setdefault(_norm_vno(vno), []).append(m)

    def _pick_member(c):
        """차량번호 기준으로 최적 회원 찾기 (정규화 포함)"""
        vno = (c.vehicle_number or "").strip()
        name = (c.name or "").strip()
        region = (c.region or "").strip()

        # 1. 정확히 일치
        candidates = by_vno.get(vno, [])
        # 2. 정규화 일치
        if not candidates:
            candidates = by_vno_norm.get(_norm_vno(vno), [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # 성명 일치 우선
        for m in candidates:
            if (m.name or "").strip() == name:
                return m
        # 지역 일치 우선
        for m in candidates:
            if (m.region or "").strip() == region:
                return m
        return candidates[0]

    FILL_FIELDS = [
        'vehicle_type', 'fuel_type', 'structure_change',
        'phone', 'mobile', 'address', 'official_address',
        'membership_status', 'membership_date',
        'certificate_issue_date', 'certificate_number',
        'driver_license_number', 'resident_number',
        'affiliated_company', 'agent_name', 'agent_mobile',
    ]

    total = len(closures)
    matched = 0
    failed = 0
    vt_filled = 0
    ft_filled = 0
    failed_list = []

    for c in closures:
        m = _pick_member(c)
        if not m:
            failed += 1
            failed_list.append({
                "id": c.id,
                "management_number": c.management_number,
                "vehicle_number": c.vehicle_number,
                "name": c.name,
            })
            continue

        matched += 1
        changed = False
        for field in FILL_FIELDS:
            cur = getattr(c, field, None)
            src = getattr(m, field, None)
            if not cur and src:  # 기존값 없을 때만 채움
                setattr(c, field, src)
                changed = True
                if field == 'vehicle_type':
                    vt_filled += 1
                if field == 'fuel_type':
                    ft_filled += 1

        # company_name 보정
        if not c.company_name and m.company_name:
            c.company_name = m.company_name
            changed = True

        # 비고: 기존 보존, 회원 비고가 있으면 별도 추가
        if m.memo and not c.memo:
            c.memo = m.memo
            changed = True
        elif m.memo and c.memo and m.memo not in c.memo:
            c.memo = f"{c.memo}\n[회원비고] {m.memo}"
            changed = True

        # member_id 연결 (없으면)
        if not c.member_id:
            c.member_id = m.id

    db.commit()

    return {
        "전체폐업현황": total,
        "매칭성공": matched,
        "매칭실패": failed,
        "차종채워진건수": vt_filled,
        "유종채워진건수": ft_filled,
        "매칭실패목록(최대20)": failed_list[:20],
    }


@router.post("/backfill-transfers")
async def backfill_transfers(db: Session = Depends(get_db), _=Depends(require_admin)):
    """기존 양도양수대장에 차종/유종/구조변경/소속업체 backfill"""
    transfers = db.query(models.TransferLedger).filter(
        models.TransferLedger.deleted_at.is_(None)
    ).all()

    members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None)
    ).all()
    by_vno = {}
    for m in members:
        vno = (m.vehicle_number or "").strip()
        if vno:
            by_vno.setdefault(vno, []).append(m)

    FILL = ['vehicle_type', 'fuel_type', 'structure_change', 'affiliated_company']
    total = len(transfers)
    matched = vt_filled = 0

    for t in transfers:
        vno = (t.vehicle_number or "").strip()
        candidates = by_vno.get(vno, [])
        if not candidates:
            continue
        m = candidates[0]
        if len(candidates) > 1:
            for c in candidates:
                if (c.name or "") == (t.transferee or ""):
                    m = c; break
        matched += 1
        for field in FILL:
            if not getattr(t, field, None) and getattr(m, field, None):
                setattr(t, field, getattr(m, field))
                if field == 'vehicle_type':
                    vt_filled += 1

    db.commit()
    return {"전체양도양수": total, "매칭성공": matched, "차종채워진건수": vt_filled}




@router.post("/cleanup-bad-vehicle-type")
async def cleanup_bad_vehicle_type(db: Session = Depends(get_db), _=Depends(require_admin)):
    """backfill로 잘못 저장된 날짜/번호 형태의 vehicle_type 정리"""
    import re as _re

    BAD_PATTERNS = [
        _re.compile(r'^\d{2}\.\d{1,2}\.\d{1,2}\.?$'),   # 26.04.06.
        _re.compile(r'^\d{4}[-\.]\d{2}[-\.]\d{2}\.?$'),  # 2026-04-06
        _re.compile(r'^\d{2}[-]\d{2}[-]\d{2}$'),          # 26-04-06
        _re.compile(r'^\d{2}-\d{2}\([가-힣]\)-'),          # 14-17(보)-003182
        _re.compile(r'^\d{6}-\d'),                          # 주민번호 형태
    ]

    def _is_bad(v):
        if not v: return False
        v = str(v).strip()
        return any(p.match(v) for p in BAD_PATTERNS)

    rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
        models.Closure.vehicle_type.isnot(None),
        models.Closure.vehicle_type != '',
    ).all()

    cleaned = 0
    samples = []
    for c in rows:
        vt = (c.vehicle_type or '').strip()
        if _is_bad(vt):
            if len(samples) < 10:
                samples.append({"관리번호": c.management_number, "성명": c.name,
                                 "잘못된vehicle_type": vt})
            c.vehicle_type = ''
            cleaned += 1

    db.commit()
    return {"정리건수": cleaned, "샘플": samples,
            "메시지": f"날짜/번호 형태 vehicle_type {cleaned}건 삭제 완료"}


@router.post("/backfill-closure-vehicle-fields")
async def backfill_closure_vehicle_fields(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """폐업현황 차종/유종/소속업체 backfill.
    - 날짜 패턴 강화: 공백 섞인 04. 4.17. 형태도 제외
    - 차종은 키워드 포함 시만 허용 (숫자+날짜만 있으면 제외)
    dry_run=true: 저장 없이 결과만.
    """
    import re as _re

    VT_KW = ['포터','봉고','스타리아','스타렉스','카니발','리베로','렉스턴','코란도',
             '무쏘','마이티','다마스','라보','st1','t4k','master','마스터','내장',
             '탑차','냉동','냉장','윙','사다리','엘리카','호룡','렉카','렉커','카고',
             '픽업','밴','ev','일렉트릭','파워게이트','하이내장','택배전용','트럭']
    FT_KW = ['경유','전기','lpg','엘피지','가스','휘발유','cng','하이브리드','디젤']

    BAD = [
        _re.compile(r'^\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?\s*$'),  # YY. M.D.
        _re.compile(r'^\d{4}[-\.]\d{1,2}[-\.]\d{1,2}\.?$'),               # YYYY-MM-DD
        _re.compile(r'^\d{2}-\d{2,3}\([가-힣a-zA-Z]+\)-\d+'),              # 자격증명
        _re.compile(r'^\d{3}-\d{2}-\d{5}$'),                               # 사업자번호
        _re.compile(r'^\d{6}-\d{7}$'),                                     # 주민번호
        _re.compile(r'^\d{2,3}-\d{3,4}-\d{4}$'),                          # 전화번호
        _re.compile(r'^\d+$'),                                              # 순수숫자
    ]

    def _bad(v):
        v = str(v or '').strip()
        if not v or v in ('-','x','X','nan','None',''): return True
        return any(p.match(v) for p in BAD)

    def _is_vt(v):
        if _bad(v): return False
        return any(w in v.lower() for w in VT_KW)

    def _is_ft(v):
        if _bad(v): return False
        vl = v.lower().replace(' ','')
        return any(f in vl for f in FT_KW)

    def _is_co(v):
        if _bad(v) or _is_vt(v) or _is_ft(v): return False
        return bool(_re.match(r'^[가-힣a-zA-Z][가-힣a-zA-Z0-9\s&\(\)]{1,}$', v)) and len(v) >= 2

    def _classify(v):
        if _is_vt(v): return 'vt'
        if _is_ft(v): return 'ft'
        if _is_co(v): return 'co'
        return None

    def _why_bad(v):
        v = str(v or '').strip()
        if not v or v in ('-','x','X','nan','None',''): return '빈값'
        for p in BAD:
            if p.match(v):
                s = p.pattern
                if r'\.\s*\d{1,2}\s*\.' in s: return '날짜(YY.M.D)'
                if 'YYYY' in s or r'\d{4}' in s: return '날짜(YYYY)'
                if '자격' in s or '가-힣a-zA-Z' in s: return '자격증명번호'
                if r'\d{3}-\d{2}-\d{5}' in s: return '사업자번호'
                return '제외패턴'
        if not any(w in v.lower() for w in VT_KW): return '차종키워드없음'
        return None

    def _extract_raw(raw):
        vt = ft = co = rv = rf = rc = ''
        if not isinstance(raw, dict): return vt,ft,co,rv,rf,rc
        # 명시적 키
        for k in raw:
            ks = str(k).strip().replace(' ','').lower()
            v  = str(raw[k] or '').strip()
            if not vt and any(x in ks for x in ['차종','차량종류','차명']):
                if _is_vt(v): vt,rv = v, f'키:{k}'
            if not ft and any(x in ks for x in ['유종','연료명','사용연료']):
                if _is_ft(v): ft,rf = v, f'키:{k}'
            if not co and any(x in ks for x in ['업체','소속','company']):
                if _is_co(v): co,rc = v, f'키:{k}'
        # Unnamed:14~17 값 형태로 분류
        for suffix in ['14','15','16','17']:
            for k in raw:
                if str(k).replace(' ','') in [f'Unnamed:{suffix}',f'unnamed:{suffix}']:
                    v   = str(raw[k] or '').strip()
                    cls = _classify(v)
                    if cls == 'vt' and not vt: vt,rv = v, f'Unnamed:{suffix}'
                    elif cls == 'ft' and not ft: ft,rf = v, f'Unnamed:{suffix}'
                    elif cls == 'co' and not co: co,rc = v, f'Unnamed:{suffix}'
        return vt,ft,co,rv,rf,rc

    def _norm(v):
        v = str(v or '').strip()
        v = _re.sub(r'\s+','',v); v = _re.sub(r'호$','',v)
        return v.lower()

    all_lh = db.query(models.LicenseHolder).all()
    by_vno: dict = {}
    for m in all_lh:
        key = _norm(m.vehicle_number)
        if key: by_vno.setdefault(key,[]).append(m)

    def _pick_lh(c):
        cands = by_vno.get(_norm(c.vehicle_number),[])
        if not cands: return None
        name = (c.name or '').strip()
        for m in cands:
            if (m.name or '').strip() == name: return m
        return cands[0]

    closures = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None)).all()

    stats = {
        'dry_run':dry_run, '전체':len(closures),
        'raw추출_차종':0,'raw추출_유종':0,
        'lh매칭_차종':0,'lh매칭_유종':0,
        '차종_이미있음':0,'유종_이미있음':0,
        '차종채움예정':0,'유종채움예정':0,'소속업체채움예정':0,
        '샘플':[],
    }

    target = {'폐-55','폐-56','폐-58','폐-86','폐-91'}
    debug  = {k:None for k in target}
    gen_ok = []; gen_skip = []

    for c in closures:
        cur_vt = (getattr(c,'vehicle_type','') or '').strip()
        cur_ft = (getattr(c,'fuel_type','')    or '').strip()
        cur_co = (getattr(c,'affiliated_company','') or '').strip()
        mgmt   = c.management_number or ''

        if cur_vt: stats['차종_이미있음'] += 1
        if cur_ft: stats['유종_이미있음'] += 1

        raw = c.raw_data if isinstance(c.raw_data, dict) else {}
        raw_vt,raw_ft,raw_co,rv,rf,rc = _extract_raw(raw)

        if raw_vt: stats['raw추출_차종'] += 1
        if raw_ft: stats['raw추출_유종'] += 1

        lh_vt = lh_ft = ''
        if not raw_vt or not raw_ft:
            m = _pick_lh(c)
            if m:
                if not raw_vt: lh_vt = (m.vehicle_type or '').strip()
                if not raw_ft: lh_ft = (m.fuel_type    or '').strip()
                if lh_vt: stats['lh매칭_차종'] += 1
                if lh_ft: stats['lh매칭_유종'] += 1

        final_vt = raw_vt or lh_vt
        final_ft = raw_ft or lh_ft
        final_co = raw_co

        will_vt = not cur_vt and bool(final_vt)
        will_ft = not cur_ft and bool(final_ft)
        will_co = not cur_co and bool(final_co)

        if will_vt: stats['차종채움예정'] += 1
        if will_ft: stats['유종채움예정'] += 1
        if will_co: stats['소속업체채움예정'] += 1

        raw14 = str(raw.get('Unnamed: 14','') or '').strip()
        raw15 = str(raw.get('Unnamed: 15','') or '').strip()
        raw17 = str(raw.get('Unnamed: 17','') or '').strip()

        # 검증 정보
        def _vcheck(v):
            if not v: return '빈값'
            if _is_vt(v): return '차종✅'
            if _is_ft(v): return '유종✅'
            if _is_co(v): return '업체✅'
            return f'제외({_why_bad(v) or "키워드없음"})'

        sample = {
            '관리번호':mgmt,'성명':c.name,'차량번호':c.vehicle_number,
            'raw_Unnamed14':raw14,'raw14판정':_vcheck(raw14),
            'raw_Unnamed15':raw15,'raw15판정':_vcheck(raw15),
            'raw_Unnamed17':raw17,'raw17판정':_vcheck(raw17),
            '기존차종':cur_vt,'기존유종':cur_ft,
            '차종저장예정':final_vt if will_vt else ('(유지:'+cur_vt+')' if cur_vt else '(없음)'),
            '유종저장예정':final_ft if will_ft else ('(유지:'+cur_ft+')' if cur_ft else '(없음)'),
            '소속업체저장예정':final_co if will_co else ('(유지)' if cur_co else ''),
            '추출근거_차종':rv or ('lh매칭' if lh_vt else '없음'),
            '추출근거_유종':rf or ('lh매칭' if lh_ft else '없음'),
        }
        if mgmt in target:
            debug[mgmt] = sample
        elif will_vt and len(gen_ok) < 3:
            gen_ok.append(sample)
        elif not will_vt and not cur_vt and len(gen_skip) < 3:
            gen_skip.append(sample)

        if not dry_run:
            if will_vt: c.vehicle_type = final_vt
            if will_ft: c.fuel_type    = final_ft
            if will_co: c.affiliated_company = final_co

    if not dry_run:
        db.commit()

    stats['샘플'] = [v for v in debug.values() if v] + gen_ok + gen_skip
    return stats


@router.get("/debug-closure-match")
async def debug_closure_match(
    mgmt: str = "",
    vno: str = "",
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """특정 폐업현황 차량번호 매칭 추적"""
    import re as _re

    def _norm(v):
        v = str(v or '').strip()
        v = _re.sub(r'\s+', '', v)
        v = _re.sub(r'호$', '', v)
        return v.lower()

    # 폐업현황 찾기
    c = None
    if mgmt:
        c = db.query(models.Closure).filter(
            models.Closure.management_number == mgmt,
            models.Closure.deleted_at.is_(None),
        ).first()

    search_vno = _norm(vno or (c.vehicle_number if c else ''))
    search_name = (c.name if c else '').strip()

    # license_holders 전체(deleted 포함) 검색
    all_lh = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.vehicle_number.isnot(None),
    ).all()

    exact_match = []
    norm_match = []
    name_match = []

    for m in all_lh:
        orig = (m.vehicle_number or '').strip()
        normed = _norm(orig)
        if orig == (vno or (c.vehicle_number if c else '')).strip():
            exact_match.append({"id": m.id, "name": m.name, "vno": orig,
                                  "vt": m.vehicle_type, "status": m.status, "deleted": str(m.deleted_at)[:10] if m.deleted_at else None})
        elif normed == search_vno:
            norm_match.append({"id": m.id, "name": m.name, "vno": orig, "normed": normed,
                                 "vt": m.vehicle_type, "status": m.status, "deleted": str(m.deleted_at)[:10] if m.deleted_at else None})
        elif search_name and (m.name or '').strip() == search_name:
            name_match.append({"id": m.id, "name": m.name, "vno": orig, "normed": normed,
                                 "vt": m.vehicle_type, "status": m.status})

    return {
        "조회관리번호": mgmt,
        "폐업차량번호_원본": c.vehicle_number if c else vno,
        "폐업차량번호_정규화": search_vno,
        "폐업성명": search_name,
        "정확일치": exact_match[:5],
        "정규화일치": norm_match[:5],
        "성명일치": name_match[:5],
        "총매칭": len(exact_match) + len(norm_match),
    }


@router.get("/search-raw-member")
async def search_raw_member(
    vehicle: str = "",
    name: str = "",
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """차량번호/성명으로 전체 테이블 raw_data 검색"""
    import re as _re

    def _norm(v):
        v = str(v or "").strip()
        v = _re.sub(r"\s+", "", v)
        v = _re.sub(r"호$", "", v)
        return v.lower()

    search_vno = _norm(vehicle)
    search_name = name.strip()
    results = []

    def _match(v, n):
        vno_ok = search_vno and search_vno in _norm(str(v or ""))
        name_ok = search_name and search_name in str(n or "")
        return vno_ok or name_ok

    # 1. license_holders 전체 (deleted 포함)
    for m in db.query(models.LicenseHolder).all():
        if _match(m.vehicle_number, m.name):
            raw = m.raw_data or {}
            results.append({
                "테이블": "license_holders",
                "관리번호": m.management_number, "성명": m.name,
                "차량번호": m.vehicle_number, "차량번호_정규화": _norm(m.vehicle_number),
                "차종": m.vehicle_type or "", "유종": m.fuel_type or "",
                "status": m.status, "deleted_at": str(m.deleted_at)[:10] if m.deleted_at else None,
                "raw_차종": raw.get("차종","") or raw.get("vehicle_type",""),
                "raw_유종": raw.get("유종","") or raw.get("fuel_type",""),
                "raw_keys": list(raw.keys())[:15] if raw else [],
            })

    # 2. candidates
    for m in db.query(models.Candidate).all():
        if _match(m.vehicle_number, m.name):
            results.append({
                "테이블": "candidates", "관리번호": "", "성명": m.name,
                "차량번호": m.vehicle_number, "차종": m.vehicle_type or "",
                "deleted_at": str(m.deleted_at)[:10] if m.deleted_at else None,
            })

    # 3. transfer_ledger
    for t in db.query(models.TransferLedger).all():
        if _match(t.vehicle_number, t.transferee) or _match(t.vehicle_number, t.transferor):
            raw = t.raw_data or {}
            results.append({
                "테이블": "transfer_ledger", "관리번호": t.management_number,
                "성명": f"{t.transferor}→{t.transferee}",
                "차량번호": t.vehicle_number,
                "차종": getattr(t,"vehicle_type","") or "",
                "raw_차종": raw.get("차종","") or raw.get("vehicle_type",""),
                "raw_유종": raw.get("유종","") or raw.get("fuel_type",""),
                "raw_keys": list(raw.keys())[:15] if raw else [],
            })

    # 4. closures raw_data
    for c in db.query(models.Closure).all():
        if _match(c.vehicle_number, c.name):
            raw = c.raw_data or {}
            results.append({
                "테이블": "closures", "관리번호": c.management_number,
                "성명": c.name, "차량번호": c.vehicle_number,
                "차종_DB": getattr(c,"vehicle_type","") or "",
                "유종_DB": getattr(c,"fuel_type","") or "",
                "raw_keys": list(raw.keys())[:20] if raw else [],
                "raw_차종후보": {k: str(raw[k])[:50] for k in raw if "차종" in str(k) or "vehicle" in str(k).lower()},
                "raw_유종후보": {k: str(raw[k])[:30] for k in raw if "유종" in str(k) or "fuel" in str(k).lower() or "연료" in str(k)},
                "raw_all_values_sample": {k: str(raw[k])[:30] for k in list(raw.keys())[:25]},
            })

    return {
        "검색차량번호": vehicle, "검색성명": name,
        "총발견": len(results), "결과": results
    }


@router.get("/verify-change-types")
async def verify_change_types(db: Session = Depends(get_db), _=Depends(require_admin)):
    """전속업체변경으로 분류된 건 중 '전속' 키워드 없는 건 검출"""
    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.change_type == "전속계약 업체변경",
    ).all()

    suspicious = []
    for r in rows:
        combined = " ".join([
            r.change_type or "", r.before_value or "",
            r.after_value or "", r.memo or "",
        ])
        has_jeon = "전속" in combined
        if not has_jeon:
            suspicious.append({
                "id": r.id, "change_type": r.change_type,
                "vehicle_number": r.vehicle_number, "name": r.name,
                "before_value": r.before_value, "after_value": r.after_value,
                "memo": r.memo, "change_date": r.change_date,
                "판정": "상호변경으로 재분류 필요",
            })

    return {
        "전속업체변경_전체": len(rows),
        "전속키워드없음": len(suspicious),
        "의심목록_20건": suspicious[:20],
    }


@router.post("/fix-change-types")
async def fix_change_types(db: Session = Depends(get_db), _=Depends(require_admin)):
    """전속 키워드 없는 전속업체변경 → 상호변경으로 재분류"""
    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.change_type == "전속계약 업체변경",
    ).all()

    fixed = 0
    for r in rows:
        combined = " ".join([r.before_value or "", r.after_value or "", r.memo or ""])
        if "전속" not in combined:
            r.change_type = "상호변경"
            fixed += 1

    db.commit()
    return {"재분류건수": fixed, "message": f"전속키워드없는 전속업체변경 {fixed}건 → 상호변경 재분류"}


@router.post("/backfill-change-before-after")
async def backfill_change_before_after(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """변경등록대장 before/after backfill v6.
    우선순위:
    1. 명시적 변경전/변경후 컬럼
    2. '주 소' 컬럼에 -> 또는 → 있으면 분리
    3. '변 경 내 용' 컬럼이 실제 변경값이면 사용
    4. '내 용'이 업무유형명뿐이면 skip
    """
    from app.excel_utils import _parse_change_text

    BUSINESS_TYPES = {
        '상호변경','주소지변경','주소변경','차량변경','구조변경','대표자변경',
        '전속계약업체변경','전속업체변경','자격증재교부','이전전출','등록이관',
        '변경내용','변경사항','기타','성명변경','번호변경',
    }

    def _nc(s): return ''.join(str(s or '').split()).lower()

    def _is_biz(v): return _nc(v) in {_nc(b) for b in BUSINESS_TYPES}

    def _is_hdr(v):
        HDR = {'차량번호','성명','변경내용','변경내 용','신고일자','시군별','번호',
               'vehicle_number','name','변경사항','내용','번  호'}
        return _nc(v) in {_nc(h) for h in HDR}

    def _find(raw, keys):
        for k in raw:
            kn = _nc(k)
            for c in keys:
                if _nc(c) == kn or _nc(c) in kn:
                    v = str(raw[k] or '').strip()
                    if v and v not in ('-','nan','None',''): return v, k
        return '', ''

    def _split_arrow(v):
        """-> 또는 → 로 분리. 없으면 ('', '') 반환."""
        import re as _re
        m = _re.search(r'(.+?)\s*(?:->|→)\s*(.+)', v)
        if m: return m.group(1).strip(), m.group(2).strip()
        return '', ''

    BEFORE_KEYS  = ['변경전','변경 전','이전주소','변경전주소','변경전내용','이전내용','이전','전주소','종전주소']
    AFTER_KEYS   = ['변경후','변경 후','현재주소','변경후주소','변경후내용','현재내용','현재','변경된내용','신주소','새주소']
    CONTENT_KEYS = ['변경내용','변경사항','내용','변경내 용','변 경 내 용']
    ADDR_KEYS    = ['주소','주      소','주 소','주  소']  # 구버전 변경내용이 여기 들어있음

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)
    ).all()

    TARGET_IDS = {1568, 1639, 3016, 3067}
    stats = {
        '버전': 'change-backfill-v6',
        'dry_run': dry_run, '전체': len(rows),
        'before복구': 0, 'after복구': 0, '샘플': [], 'id_debug': {},
    }

    for r in rows:
        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        if not raw: continue

        cur_bv = (r.before_value or '').strip()
        cur_av = (r.after_value  or '').strip()
        # '-' 또는 업무유형명은 공란 취급 (재복구 허용)
        BAD_AV = {'상호변경','주소지변경','주소변경','차량변경','구조변경','대표자변경',
                  '전속계약업체변경','전속업체변경','자격증재교부','이전전출','등록이관',
                  '성명변경','이름변경','번호변경','연락처변경','대차/대폐차','기타','-'}
        def _nc2(v): return ''.join(v.split()).lower()
        if _nc2(cur_bv) in {_nc2(b) for b in BAD_AV}: cur_bv = ''
        if _nc2(cur_av) in {_nc2(b) for b in BAD_AV}: cur_av = ''

        # 헤더 행 제외
        if _is_hdr(r.vehicle_number) or _is_hdr(r.name): continue

        new_bv = new_av = ''
        source = ''

        # 1순위: 명시적 변경전/변경후 컬럼
        raw_bv, k_bv = _find(raw, BEFORE_KEYS)
        raw_av, k_av = _find(raw, AFTER_KEYS)
        if raw_bv or raw_av:
            new_bv, new_av = raw_bv, raw_av
            source = f'변경전/후컬럼({k_bv}/{k_av})'

        # 2순위: '주 소' 컬럼에 화살표 있으면 분리
        if not new_bv and not new_av:
            addr_val, k_addr = _find(raw, ADDR_KEYS)
            if addr_val:
                bv2, av2 = _split_arrow(addr_val)
                if bv2 and av2:
                    new_bv, new_av = bv2, av2
                    source = f'주소컬럼화살표({k_addr})'

        # 3순위: 변경내용 컬럼 (업무유형명 skip)
        if not new_bv and not new_av:
            content_val, k_ct = _find(raw, CONTENT_KEYS)
            if content_val and not _is_biz(content_val) and not _is_hdr(content_val):
                bv3, av3 = _split_arrow(content_val)
                if bv3 and av3:
                    new_bv, new_av = bv3, av3
                    source = f'변경내용화살표({k_ct})'
                else:
                    # 화살표 없으면 after에만
                    new_bv, new_av = '-', content_val
                    source = f'변경내용단독({k_ct})'

        will_bv = bool(not cur_bv and new_bv)
        will_av = bool(not cur_av and new_av)

        if r.id in TARGET_IDS:
            addr_val2, k2 = _find(raw, ADDR_KEYS)
            stats['id_debug'][r.id] = {
                'id': r.id, 'name': r.name,
                'raw_주소컬럼키': k2, 'raw_주소값': addr_val2,
                '기존_before': cur_bv, '기존_after': cur_av,
                '새_before': new_bv if will_bv else '(유지)',
                '새_after': new_av if will_av else '(유지)',
                '추출근거': source,
            }

        if will_bv: stats['before복구'] += 1
        if will_av: stats['after복구'] += 1

        if (will_bv or will_av) and len(stats['샘플']) < 10:
            stats['샘플'].append({
                'id': r.id, 'change_type': r.change_type,
                'vehicle_number': r.vehicle_number, 'name': r.name,
                '추출근거': source,
                '새_before': new_bv, '새_after': new_av,
            })

        if not dry_run:
            if will_bv: r.before_value = new_bv
            if will_av: r.after_value  = new_av

    if not dry_run: db.commit()
    return stats

@router.get("/debug-closure-raw")
async def debug_closure_raw(
    vehicle: str = "", name: str = "", mgmt: str = "",
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """이전폐지현황 raw_data 구조 + 필드 후보 확인"""
    import re as _re
    from app.routers.dashboard import classify_vt, classify_fuel

    def _norm(v): return _re.sub(r'\s+','', str(v or '')).lower()

    q = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None))
    rows = q.all()
    result = []
    for c in rows:
        if mgmt and c.management_number != mgmt: continue
        if vehicle and _norm(vehicle) not in _norm(c.vehicle_number): continue
        if name and name not in (c.name or ''): continue
        raw = c.raw_data if isinstance(c.raw_data, dict) else {}
        result.append({
            "관리번호": c.management_number, "성명": c.name,
            "차량번호": c.vehicle_number, "처리구분": c.closure_type,
            "처리일자_DB": c.closure_date, "접수일자_DB": c.receipt_date,
            "양수인_DB": c.transferee, "이관지역_DB": c.transfer_region,
            "차종_DB": c.vehicle_type, "유종_DB": c.fuel_type,
            "raw_data_keys": list(raw.keys()),
            "raw_data_all": {k: str(raw[k])[:60] for k in list(raw.keys())[:25]},
        })
        if len(result) >= 5: break
    return {"총발견": len(result), "결과": result}


@router.post("/backfill-closure-transfer-fields")
async def backfill_closure_transfer_fields(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """이전폐지현황 raw_data에서 양수인/이관지역/처리일자 backfill.
    처리구분별 분리:
      양도: 값에 / 있으면 앞=이관양도지역, 뒤=양수인
      이관: 전체=이관지역, 양수인=공란
      폐업: 전체=비고, 양수인/지역=공란
    """
    import re as _re
    from app.excel_utils import _normalize_text

    TRANSFEREE_KEYS = ['양수인','양수자','양수인성명','양수자성명','성명(양수)',
                       '타도전출일자및양수자','양도양수자','이관지역및양수자']
    REGION_KEYS = ['이관지역','이관/양도지역','양도지역','전출지역','이전지역','이전지','이관지']
    DATE_KEYS = ['처리일자','폐지일자','처리일','타도전출일자']

    REGIONS = ['경기','서울','인천','충북','충남','전북','전남','경북','경남','강원',
               '대전','대구','부산','광주','울산','세종','제주','경기도','충청','전라','경상']

    def _is_region(v):
        v = v.strip()
        if any(v.startswith(r) for r in REGIONS): return True
        if _re.search(r'(시|군|구|도)$', v): return True
        return False

    def _is_name(v):
        v = _re.sub(r'\(.*?\)', '', v).strip()
        return bool(_re.match(r'^[가-힣]{2,4}$', v))

    def _parse_val(raw_val, closure_type):
        v = (raw_val or '').strip()
        if not v or v in ('-', '', 'nan', 'None'):
            return '', '', '', ''
        ct = (closure_type or '').replace('폐지','폐업')
        if ct == '폐업':
            return '', '', v, ''   # transferee, region, memo, reason
        if ct == '이관':
            return '', v, '', ''
        # 양도
        if '/' in v:
            parts = v.split('/', 1)
            region = _norm_region(parts[0].strip())
            name_part = parts[1].strip()
            memo_m = _re.search(r'\((.+?)\)', name_part)
            memo = memo_m.group(1) if memo_m else ''
            name = _re.sub(r'\(.*?\)', '', name_part).strip()
            return name, region, memo, ''
        if _is_region(v): return '', _norm_region(v), '', ''
        if _is_name(v): return v, '', '', ''
        return '', '', v, ''   # 불명확하면 비고로

    def _find(raw, keys):
        for k in raw:
            kn = _normalize_text(k)
            for c in keys:
                if _normalize_text(c) in kn or _normalize_text(c) == kn:
                    val = str(raw[k] or '').strip()
                    if val and val not in ('-','nan','None',''): return val
        return ''

    # 날짜 패턴
    _DATE_START = _re.compile(r'^(\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\.?)')
    _DATE_FULL  = [
        _re.compile(r'^\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\.?\s*$'),
        _re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$'),
    ]
    _REGIONS_KW = ['경기','서울','인천','충북','충남','전북','전남','경북','경남','강원',
                   '대전','대구','부산','광주','울산','세종','제주','경기도']
    _NOT_DATE   = ['전출','말소','폐업','양도','신규','기타','취소']

    _SIDO_MAP = {
        '경기도': '경기', '경기': '경기',
        '충청북도': '충북', '충북': '충북',
        '충청남도': '충남', '충남': '충남',
        '전라북도': '전북', '전북': '전북',
        '전라남도': '전남', '전남': '전남',
        '경상북도': '경북', '경북': '경북',
        '경상남도': '경남', '경남': '경남',
        '강원도': '강원', '강원': '강원',
        '서울특별시': '서울', '서울': '서울',
        '인천광역시': '인천', '인천': '인천',
    }

    def _norm_region(v):
        """전북전주 → 전북 전주, 경기도포천 → 경기 포천"""
        v = str(v or '').strip()
        if not v or ' ' in v: return v
        for full, short in sorted(_SIDO_MAP.items(), key=lambda x: -len(x[0])):
            if v.startswith(full) and len(v) > len(full):
                return f"{short} {v[len(full):]}"
        return v

    def _is_valid_date(v):
        v = str(v or '').strip()
        if any(w in v for w in _NOT_DATE): return False
        return any(p.match(v) for p in _DATE_FULL)

    def _parse_date_field(v):
        """날짜+지역+비고 복합 문자열 분리 → (date, region, memo)"""
        v = str(v or '').strip()
        if not v or v in ('-','nan','None',''): return '', '', ''
        # 순수 날짜
        if _is_valid_date(v): return v, '', ''
        # 날짜로 시작하는지
        m = _DATE_START.match(v)
        if m:
            date_part = m.group(1).strip()
            rest = v[m.end():].strip().lstrip('.')  .strip()
            rest_clean = _re.sub(r'이관$|전출$|말소$', '', rest).strip()
            region = ''
            memo = ''
            if rest_clean:
                if any(rest_clean.startswith(r) for r in _REGIONS_KW):
                    region = _norm_region(rest_clean)
                else:
                    memo = rest_clean
            return date_part, region, memo
        # 날짜 없음 → 전체 비고
        return '', '', v

    rows = db.query(models.Closure).filter(
        models.Closure.deleted_at.is_(None),
    ).all()

    target = {'양-18','양-19','양-31','이-2','폐-77'}
    stats = {'dry_run': dry_run, '버전': 'v5-date-split-aa07160', '전체': len(rows), '수정예정': 0, '샘플': []}
    debug = {k: None for k in target}

    for c in rows:
        raw = c.raw_data if isinstance(c.raw_data, dict) else {}
        if not raw: continue
        ct = (c.closure_type or '').replace('폐지','폐업')
        mgmt = c.management_number or ''

        # 원본 raw에서 후보값 찾기
        raw_tee = _find(raw, TRANSFEREE_KEYS)
        raw_reg = _find(raw, REGION_KEYS)
        raw_date = _find(raw, DATE_KEYS)

        # 처리구분별 분리
        tee, reg, memo, reason = _parse_val(raw_tee, ct)
        if not reg and raw_reg: reg = raw_reg
        # 날짜 추출 (날짜+지역+비고 분리)
        date_val, date_region, date_memo = _parse_date_field(raw_date)

        changes = {}
        cur_tee = (c.transferee or '').strip()
        cur_reg = (c.transfer_region or '').strip()
        cur_dt  = (c.closure_date or '').strip()
        cur_memo = (c.memo or '').strip()

        if not cur_tee and tee: changes['transferee'] = tee
        # 지역: raw_region 또는 날짜에서 추출된 지역
        final_reg = reg or (date_region if date_region and not cur_reg else '')
        if not cur_reg and final_reg: changes['transfer_region'] = final_reg
        # 처리일자: 순수 날짜만
        if not cur_dt and date_val: changes['closure_date'] = date_val
        # 비고: date_memo, memo 순
        final_memo = date_memo or memo
        if not cur_memo and final_memo: changes['memo'] = final_memo

        sample = {
            '관리번호': mgmt, '성명': c.name, '처리구분': ct,
            'raw_transferee원본': raw_tee, 'raw_region원본': raw_reg,
            'raw_date원본': raw_date,
            '추출_날짜': date_val, '추출_날짜지역': date_region, '추출_날짜비고': date_memo,
            '날짜판정': bool(date_val),
            '날짜판정사유': '날짜추출성공' if date_val else ('날짜없음_비고처리' if raw_date else '값없음'),
            '기존_처리일자': cur_dt, '추출_처리일자': date_val,
            '기존_양수인': cur_tee, '추출_양수인': tee,
            '기존_이관양도지역': cur_reg, '추출_이관양도지역': reg,
            '기존_비고': cur_memo, '추출_비고': memo,
            '저장예정': changes,
        }
        if mgmt in target:
            debug[mgmt] = sample

        if changes:
            stats['수정예정'] += 1
            if len(stats['샘플']) < 5 and mgmt not in target:
                stats['샘플'].append(sample)
            if not dry_run:
                for k, v in changes.items():
                    setattr(c, k, v)

    if not dry_run: db.commit()
    stats['샘플'] = [v for v in debug.values() if v] + stats['샘플']
    return stats



@router.post("/cleanup-bad-change-before-after")
async def cleanup_bad_change_before_after(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """backfill로 잘못 저장된 change before/after 복구.
    before='-' AND after=업무유형명 인 행을 공란으로 되돌림.
    """
    BAD_AFTER = {
        '상호변경','주소지변경','주소변경','차량변경','구조변경','대표자변경',
        '전속계약 업체변경','전속업체변경','자격증재교부','이전전출','등록이관',
        '변경내용','변 경 내 용','변경사항','기타','성명변경','번호변경',
    }

    def _nc(v): return ''.join(str(v or '').split()).lower()

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.before_value == '-',
    ).all()

    stats = {'dry_run': dry_run, '전체대상': len(rows),
             '잘못저장': 0, '샘플': []}

    for r in rows:
        av = (r.after_value or '').strip()
        av_nc = _nc(av)
        is_bad = any(_nc(b) == av_nc for b in BAD_AFTER)
        if not is_bad: continue

        stats['잘못저장'] += 1
        if len(stats['샘플']) < 20:
            stats['샘플'].append({
                'id': r.id, 'change_type': r.change_type,
                'vehicle_number': r.vehicle_number, 'name': r.name,
                '기존_before': r.before_value, '기존_after': av,
                '복구_before': '', '복구_after': '',
                'memo유지': r.memo or '',
            })
        if not dry_run:
            r.before_value = ''
            r.after_value = ''
            # memo가 비어있으면 유형명 보존
            if not (r.memo or '').strip():
                r.memo = av

    if not dry_run:
        db.commit()
    return stats


@router.get("/debug-change-log")
async def debug_change_log_v2(
    type: str = "", month: str = "", vehicle: str = "", name: str = "",
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """변경등록대장 raw_data 샘플 확인 (복구 가능 여부 포함)"""
    import re as _re
    from app.excel_utils import _normalize_text

    CONTENT_KEYS = ['변경내용','변경사항','내용','변경내 용','변 경 내 용']
    BEFORE_KEYS  = ['변경전','변경 전','이전','전주소','종전주소','이전내용']
    AFTER_KEYS   = ['변경후','변경 후','현재','신주소','변경후내용','현재내용']

    def _find(raw, keys):
        for k in raw:
            kn = _normalize_text(k)
            for c in keys:
                if _normalize_text(c) in kn:
                    v = str(raw[k] or '').strip()
                    if v and v not in ('-','nan','None',''): return v
        return ''

    q = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None))
    if type:
        q = q.filter(models.ChangeHistory.change_type == type)

    rows = q.order_by(models.ChangeHistory.id.desc()).limit(30).all()

    result = []
    for r in rows:
        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        raw_bv  = _find(raw, BEFORE_KEYS)
        raw_av  = _find(raw, AFTER_KEYS)
        raw_ct  = _find(raw, CONTENT_KEYS)
        # 복구 가능 여부
        recoverable = bool(raw_bv or raw_av or (raw_ct and raw_ct not in {'상호변경','주소지변경','주소변경','차량변경','구조변경','대표자변경'}))
        result.append({
            'id': r.id, 'change_type': r.change_type,
            'vehicle_number': r.vehicle_number, 'name': r.name,
            'before_value': r.before_value or '', 'after_value': r.after_value or '',
            'memo': r.memo or '',
            'raw_keys': list(raw.keys()),
            'raw_values': {k: str(raw[k])[:60] for k in list(raw.keys())[:20]},
            'raw_content후보': raw_ct,
            'raw_before후보': raw_bv,
            'raw_after후보': raw_av,
            '복구가능': recoverable,
            '복구불가사유': '' if recoverable else 'raw_data에 변경전/후 값 없음',
        })

    return {'type': type, '총건수': len(result), '결과': result}


@router.post("/cleanup-auto-change-logs")
async def cleanup_auto_change_logs(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """기존 자동기록 after_value에서 [라벨] 제거 + before_value '원본 미기재' 정리"""
    import re as _re
    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.memo == "회원정보 수정 자동기록",
    ).all()

    stats = {'dry_run': dry_run, '전체': len(rows), '수정예정': 0, '샘플': []}
    for r in rows:
        av = r.after_value or ''
        bv = r.before_value or ''
        new_av = av
        new_bv = bv

        # [라벨] 제거: "[주소] 값" → "값"
        new_av = _re.sub(r'^\[[가-힣a-zA-Z0-9_]+\]\s*', '', new_av).strip()
        # 여러 줄인 경우 각 줄에서 라벨 제거
        if '\n' in new_av:
            lines = []
            for line in new_av.split('\n'):
                # "기존값 → 새값" 패턴이면 새값만 추출
                m = _re.search(r'→\s*(.+)$', line)
                lines.append(m.group(1).strip() if m else line.strip())
            new_av = '\n'.join(l for l in lines if l)

        # "원본 미기재" → 공란
        if new_bv == '원본 미기재':
            new_bv = ''

        # raw_data source 추가
        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        need_source = raw.get('source') != 'member_auto_log'

        changed = (new_av != av) or (new_bv != bv) or need_source
        if changed:
            stats['수정예정'] += 1
            if len(stats['샘플']) < 10:
                stats['샘플'].append({
                    'id': r.id, 'change_type': r.change_type,
                    'vehicle_number': r.vehicle_number,
                    '기존_after': av[:80], '새_after': new_av[:80],
                    '기존_before': bv, '새_before': new_bv,
                })
            if not dry_run:
                r.after_value = new_av
                r.before_value = new_bv
                new_raw = dict(raw)
                new_raw['source'] = 'member_auto_log'
                r.raw_data = new_raw
    if not dry_run:
        db.commit()
    return stats


@router.get("/cert-debug-change")
async def cert_debug_change(
    year: int = 2026, month: int = 5,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """변경등록대장 당월 집계 디버그"""
    import re as _re
    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)
    ).all()

    def _ym(s):
        s = str(s or '').strip()
        m = _re.search(r'(19[0-9]{2}|20[0-9]{2})\s*[\.\-/]\s*(\d{1,2})', s)
        if m: return int(m.group(1)), int(m.group(2))
        m = _re.match(r'^(\d{2})\s*[\.\-/]\s*(\d{1,2})', s)
        if m:
            yy = int(m.group(1))
            return (2000+yy if yy<=30 else 1900+yy), int(m.group(2))
        return None, None

    matched = []
    unmatched_samples = []
    for r in rows:
        date_str = r.change_date or r.receipt_date or ''
        y, mo = _ym(date_str)
        if y == year and mo == month:
            matched.append({'id': r.id, 'change_type': r.change_type,
                           'change_date': r.change_date, 'receipt_date': r.receipt_date})
        elif len(unmatched_samples) < 5 and date_str:
            unmatched_samples.append({'date_str': date_str, 'parsed': (y, mo)})

    by_type = {}
    for m in matched:
        ct = m['change_type'] or '기타'
        by_type[ct] = by_type.get(ct, 0) + 1

    return {
        'target': f'{year}년 {month}월',
        '전체': len(rows), '매칭': len(matched),
        '유형별': by_type,
        '매칭샘플': matched[:5],
        '미매칭샘플': unmatched_samples,
    }


# ── 변경등록대장 전체 진단 ──────────────────────────────────────
_AUTO_CT_RULES = [
    # (change_type, keywords_any)
    ('연락처변경', ['핸드폰','휴대폰','전화번호','연락처','번호변경','010','011','016','017','018','019']),
    ('대차/대폐차', ['대차','대폐차','대체등록','차량대체','차량번호변경','차량변경']),
    ('주소지변경',  ['주소','도로명','번지','아파트', '길','로','동','호수']),
    ('전속계약 업체변경', ['전속','전속계약','전속업체']),
    ('상호변경',    ['상호','업체명','사업자명','간판']),
    ('구조변경',    ['구조변경','리프트','탑차','냉동','냉장','윙','파워게이트','장착','탈거','포장탑','내장탑']),
    ('성명변경',    ['개명','성명변경','이름변경']),
]

def _suggest_ct(combined: str) -> str:
    cl = combined.lower().replace(' ','')
    for ct, kws in _AUTO_CT_RULES:
        if any(k.replace(' ','') in cl for k in kws):
            return ct
    return ''

def _is_auto_log(c) -> bool:
    if isinstance(c.raw_data, dict) and c.raw_data.get('source') == 'member_auto_log':
        return True
    return '자동기록' in (c.memo or '')

def _is_header_row(c) -> bool:
    HDR = {'차량번호','성명','변경내용','변 경 내 용','신고일자','시군별','번호'}
    def nc(v): return ''.join(str(v or '').split()).lower()
    return nc(c.vehicle_number) in {nc(h) for h in HDR} or nc(c.name) in {nc(h) for h in HDR}

def _combined(c) -> str:
    raw = c.raw_data if isinstance(c.raw_data, dict) else {}
    parts = [c.change_type or '', c.before_value or '', c.after_value or '',
             c.memo or ''] + [str(v) for v in raw.values()]
    return ' '.join(parts)

def _has_arrow(v: str) -> bool:
    import re as _r
    return bool(_r.search(r'->|→', str(v or '')))

def _raw_find_arrow(raw: dict) -> str:
    """주소/내용 컬럼에서 화살표 포함 값 찾기"""
    ADDR = ['주소','주      소','주 소','변경내용','변 경 내 용','내 용','내용']
    for k in raw:
        kn = ''.join(str(k).split()).lower()
        for a in ADDR:
            if ''.join(a.split()).lower() in kn:
                v = str(raw[k] or '').strip()
                if v and _has_arrow(v):
                    return v
    return ''


@router.get("/audit-change-history")
async def audit_change_history(db: Session = Depends(get_db), _=Depends(require_admin)):
    """변경등록대장 전체 진단 - 오분류/헤더/파싱의심 강화버전"""
    import re as _re

    def _nc(v): return "".join(str(v or "").split()).lower()

    def _combined(c):
        raw = c.raw_data if isinstance(c.raw_data, dict) else {}
        return " ".join([c.change_type or "", c.before_value or "",
                         c.after_value or "", c.memo or ""]
                        + [str(v) for v in raw.values()])

    def _is_hdr(c):
        HDR = {"차량번호","성명","변경내용","변경내 용","신고일자","시군별","번호",
               "지역","처리일자","변경유형","비고","내용","변 경 내 용"}
        vno = _nc(c.vehicle_number)
        nm  = _nc(c.name)
        ct  = _nc(c.change_type)
        return (vno in {_nc(h) for h in HDR} or
                nm  in {_nc(h) for h in HDR} or
                ct  in {_nc(h) for h in HDR})

    def _is_auto(c):
        if isinstance(c.raw_data, dict) and c.raw_data.get("source") == "member_auto_log":
            return True
        return "자동기록" in (c.memo or "")

    def _raw_arrow(raw):
        KEYS = ["주소","주      소","주 소","변경내용","변 경 내 용","내 용","내용"]
        for k in raw:
            kn = _nc(k)
            if any(_nc(a) in kn for a in KEYS):
                v = str(raw[k] or "").strip()
                if v and _re.search(r"->|→", v): return v, k
        return "", ""

    def _fmt(c, reason, sug_ct="", sug_bv="", sug_av=""):
        raw = c.raw_data if isinstance(c.raw_data, dict) else {}
        return {
            "id": c.id, "change_type": c.change_type,
            "추천유형": sug_ct, "vehicle_number": c.vehicle_number,
            "name": c.name, "change_date": c.change_date,
            "before": c.before_value or "", "after": c.after_value or "",
            "memo": c.memo or "",
            "raw_sample": {k: str(raw[k])[:50] for k in list(raw.keys())[:10]},
            "의심사유": reason, "추천_before": sug_bv, "추천_after": sug_av,
        }

    TEL_KW  = ["핸드폰","휴대폰","전화번호","연락처","번호변경","010","011","016","017","018","019"]
    CAR_KW  = ["대차","대폐차","대체등록","차량대체","차량번호변경","대체차","폐차대체"]
    STRUCT_KW = ["리프트","탑차","냉동","냉장","윙","파워게이트","장착","탈거","포장탑","내장탑","수직리프트"]
    NONSTANDARD = {"-","기타","비고","영업소","내 용","변경내용","번  호"}

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()

    summary = {
        "전체": len(rows), "raw있음": 0, "raw없음": 0,
        "전후모두있음": 0, "전후모두없음": 0, "전만있음": 0, "후만있음": 0,
        "헤더의심": 0, "오분류의심": 0, "자동기록": 0, "원본업로드": 0,
    }
    by_type: dict = {}

    # 세부 샘플 버킷
    buckets: dict = {
        "헤더의심": [], "기타유형": [], "대시유형": [],
        "구조변경샘플": [], "번호변경샘플": [], "성명변경샘플": [],
        "연락처의심": [], "대차의심": [], "구조→대차의심": [],
        "파싱가능_비어있음": [], "비표준유형": [],
        "상속/말소/기타비표준": [],
    }

    NON_STD_PATTERNS = ["상속","말소","폐차","운행정지","영업소","비고","내 용"]

    for c in rows:
        raw = c.raw_data if isinstance(c.raw_data, dict) else {}
        bv  = (c.before_value or "").strip()
        av  = (c.after_value  or "").strip()
        ct  = (c.change_type  or "").strip()
        combined = _combined(c)
        cl = combined.lower().replace(" ", "")

        if raw: summary["raw있음"] += 1
        else:   summary["raw없음"] += 1

        if bv and av:   summary["전후모두있음"] += 1
        elif not bv and not av: summary["전후모두없음"] += 1
        elif bv:        summary["전만있음"] += 1
        else:           summary["후만있음"] += 1

        if _is_auto(c): summary["자동기록"] += 1
        else:           summary["원본업로드"] += 1

        by_type[ct] = by_type.get(ct, 0) + 1

        # 헤더 의심
        if _is_hdr(c):
            summary["헤더의심"] += 1
            if len(buckets["헤더의심"]) < 50:
                buckets["헤더의심"].append(_fmt(c, "헤더행의심"))
            continue

        # 비표준 유형
        if any(_nc(p) in _nc(ct) for p in NON_STD_PATTERNS) or _nc(ct) in {_nc(n) for n in NONSTANDARD}:
            summary["오분류의심"] += 1
            if len(buckets["비표준유형"]) < 50:
                buckets["비표준유형"].append(_fmt(c, f"비표준유형:{ct}"))
            if any(k in _nc(ct) for k in ["상속","말소","폐차","운행정지","영업소"]):
                if len(buckets["상속/말소/기타비표준"]) < 50:
                    buckets["상속/말소/기타비표준"].append(_fmt(c, f"비표준:{ct}"))

        # 세부 샘플
        if ct == "기타" and len(buckets["기타유형"]) < 50:
            buckets["기타유형"].append(_fmt(c, "기타유형"))
        if ct in ("-","- ") and len(buckets["대시유형"]) < 50:
            buckets["대시유형"].append(_fmt(c, "대시유형"))
        if ct == "구조변경" and len(buckets["구조변경샘플"]) < 50:
            buckets["구조변경샘플"].append(_fmt(c, "구조변경샘플"))
        if ct in ("번호변경","성명변경") and len(buckets[f"{ct}샘플"]) < 50:
            buckets[f"{ct}샘플"].append(_fmt(c, f"{ct}샘플"))

        # A. 연락처 의심 - 주소 키워드 있으면 제외, 휴대폰 패턴 또는 명시 키워드만
        _ADDR_KW = ['길','로','번길','읍','면','동','리','호','아파트','빌라','주공',
                    '청솔','더샵','롯데캐슬','e편한','대호빌라','타운','오피스텔']
        has_addr_kw = any(k in combined for k in _ADDR_KW) or ct == '주소지변경'
        # 주민번호 패턴 제거 후 휴대폰 검색
        _combined_no_resident = _re.sub(r'\d{6}-\d{7}', '', combined)
        has_mobile  = bool(_re.search(r'\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b', _combined_no_resident))
        has_phone_kw_direct = any(k in combined for k in ['핸드폰','휴대폰','전화번호','연락처','번호변경'])
        is_phone_suspect = has_phone_kw_direct or (has_mobile and not has_addr_kw)
        if is_phone_suspect and ct not in ('연락처변경','번호변경'):
                summary["오분류의심"] += 1
                if len(buckets["연락처의심"]) < 50:
                    buckets["연락처의심"].append(_fmt(c, f"연락처의심(현재:{ct})", "연락처변경"))

        # B. 대차 의심
        if any(k in cl for k in [_nc(t) for t in CAR_KW]):
            summary["오분류의심"] += 1
            if len(buckets["대차의심"]) < 50:
                buckets["대차의심"].append(_fmt(c, f"대차/대폐차의심(현재:{ct})", "대차/대폐차"))
            if ct == "구조변경" and len(buckets["구조→대차의심"]) < 50:
                buckets["구조→대차의심"].append(_fmt(c, "구조변경→대차의심", "대차/대폐차"))

        # F. 파싱 가능한데 비어있음
        if not bv and not av:
            av2, k2 = _raw_arrow(raw)
            if av2:
                m = _re.search(r"(.+?)\s*(?:->|→)\s*(.+)", av2)
                nbv = m.group(1).strip() if m else ""
                nav = m.group(2).strip() if m else ""
                if len(buckets["파싱가능_비어있음"]) < 50:
                    buckets["파싱가능_비어있음"].append(
                        _fmt(c, f"raw화살표파싱가능({k2})", "", nbv, nav))

    return {
        "audit_version": "contact-regex-v4-resident-exclude",
        "요약": summary,
        "유형별건수": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "세부샘플": {k: v for k, v in buckets.items() if v},
    }


@router.post("/fix-change-history-audit")
async def fix_change_history_audit(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """비표준 change_type 표준화 v4 - memo에 after 원문 누락 방지"""
    import re as _re

    def _nc(v): return "".join(str(v or "").split()).lower()

    REGION_KW = ["경남","경북","충남","충북","전남","전북","강원","경기","서울","인천",
                 "대전","대구","부산","광주","울산","세종","제주","춘천","강릉","원주",
                 "포천","포항","안산","수원","성남","용인","화성","고양","부천","영월","포천"]
    ADDR_KW = ["길","로","번길","읍","면","동","리","호","아파트","빌라","주공","타운"]

    def _is_person(v):
        v = str(v or "").strip()
        if not v: return False
        if v in REGION_KW or any(r in v for r in REGION_KW): return False
        return bool(_re.match(r"^[가-힣]{2,4}$", v))

    def _is_addr(v):
        return any(k in str(v or "") for k in ADDR_KW)

    def _merge_memo(*parts):
        """non-empty, non-dash 파트만 / 로 합치기"""
        filtered = [str(p).strip() for p in parts if str(p or "").strip() and str(p or "").strip() != "-"]
        seen = []
        for p in filtered:
            if p not in seen: seen.append(p)
        return " / ".join(seen)

    MALSO_NC = {"말소","폐차","허가취소","허가실효","운전면허취소","영업용말소",
                "차령초과","감차","재허가미신청","직권말소","폐업에의한","폐업"}
    STANDARD_NC = {"주소지변경","상호변경","구조변경","전속계약업체변경","성명변경",
                   "이름변경","번호변경","대표자변경","자격증재교부","이전전출","등록이관",
                   "연락처변경","대차/대폐차","말소/폐차","상속","업종변경","운행정지","영업소변경","기타"}

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()

    stats = {"dry_run": dry_run, "버전": "fix-audit-v4",
             "전체": len(rows), "수정예정": 0, "유형수정": {}, "샘플": []}

    for r in rows:
        ct   = (r.change_type  or "").strip()
        bv   = (r.before_value or "").strip()
        av   = (r.after_value  or "").strip()
        memo = (r.memo         or "").strip()
        ct_nc = _nc(ct); av_nc = _nc(av)

        if ct_nc in STANDARD_NC: continue

        new_ct = new_bv = new_av = new_memo = ""
        reason = ""

        # ── 말소/폐차 ──
        if any(k in ct_nc for k in MALSO_NC) or any(k in av_nc for k in MALSO_NC):
            new_ct = "말소/폐차"
            # memo = 가장 의미있는 원문: av(상세) > ct(유형) > memo
            best = av if (av and _nc(av) != "-" and len(av) > len(ct)) else ct
            if _nc(best) == "-" or not best: best = memo
            new_memo = _merge_memo(best)
            new_bv, new_av = "", ""
            reason = "말소/폐차"

        # ── 상속 ──
        elif "상속" in ct_nc:
            new_ct = "상속"
            new_bv = bv if _is_person(bv) else ""
            new_av = av if _is_person(av) else ""
            # memo = ct 원문 + 이름 아닌 before/after 보존
            extras = []
            if bv and not _is_person(bv) and _nc(bv) != "-": extras.append(bv)
            if av and not _is_person(av) and _nc(av) != "-": extras.append(av)
            new_memo = _merge_memo(ct, *extras)
            reason = "상속"

        # ── 운행정지 ──
        elif "운행정지" in ct_nc:
            new_ct = "운행정지"
            new_bv, new_av = "", ""
            addr_note = f"주소참고: {av}" if av and _nc(av) != "-" else ""
            new_memo = _merge_memo(ct, addr_note)
            reason = "운행정지"

        # ── 주사무소 ──
        elif "주사무소" in ct_nc or "주사무소" in av_nc:
            new_ct = "주소지변경"
            new_bv = "" if not _is_addr(bv) else bv
            new_av = av if _is_addr(av) else ""
            new_memo = "주사무소 변경"
            reason = "주사무소→주소지변경"

        # ── 영업소 ──
        elif "영업소" in ct_nc:
            new_ct = "영업소변경"
            new_bv, new_av, new_memo = bv, av, _merge_memo(ct, memo)
            reason = "영업소"

        # ── 업종변경 ──
        elif any(k in ct_nc for k in ["업종변경","일반화물","용달화물","개인용달"]) or              any(k in av_nc for k in ["업종변경","일반화물","용달화물","개인용달"]):
            new_ct = "업종변경"
            orig = ct if ct not in ("-","") else ""
            new_bv = bv
            if av_nc in {"업종변경"}:
                new_av = ""; new_memo = _merge_memo(orig or av, memo)
            elif av_nc in {"일반화물","용달화물","개인용달"}:
                new_av = av; new_memo = _merge_memo(orig, memo)
            else:
                new_av = ""; new_memo = _merge_memo(orig, memo)
            reason = "업종변경"

        # ── "-" 유형 ──
        elif ct in ("-","","- "):
            if any(k in av_nc for k in MALSO_NC):
                new_ct = "말소/폐차"; new_memo = _merge_memo(av); new_bv = new_av = ""; reason = "-→말소/폐차"
            elif any(k in av_nc for k in ["업종변경","일반화물","용달화물"]):
                new_ct = "업종변경"
                new_bv = bv
                new_av = av if av_nc in {"일반화물","용달화물","개인용달"} else ""
                new_memo = _merge_memo(av); reason = "-→업종변경"
            elif "주사무소" in av_nc:
                new_ct = "주소지변경"; new_memo = av; new_bv = new_av = ""; reason = "-→주소지변경"
            elif "상속" in av_nc:
                new_ct = "상속"; new_memo = _merge_memo(av)
                new_bv = bv if _is_person(bv) else ""
                new_av = av if _is_person(av) else ""; reason = "-→상속"
            else:
                new_ct = "기타"; new_memo = _merge_memo(av, memo); new_bv = bv; new_av = av; reason = "-→기타"
        else:
            continue

        if (new_ct == ct and new_bv == bv and new_av == av and new_memo == memo): continue

        stats["수정예정"] += 1
        key = f"{ct} → {new_ct}"
        stats["유형수정"][key] = stats["유형수정"].get(key, 0) + 1

        if len(stats["샘플"]) < 100:
            stats["샘플"].append({
                "id": r.id, "기존_type": ct, "새_type": new_ct,
                "기존_before": bv, "새_before": new_bv,
                "기존_after": av,  "새_after":  new_av,
                "기존_memo": memo, "새_memo":   new_memo,
                "수정사유": reason, "vehicle_number": r.vehicle_number, "name": r.name,
            })

        if not dry_run:
            r.change_type = new_ct; r.before_value = new_bv
            r.after_value = new_av;  r.memo = new_memo

    if not dry_run: db.commit()
    return stats


@router.get("/nonstandard-change-types")
async def nonstandard_change_types(db: Session = Depends(get_db), _=Depends(require_admin)):
    """비표준 change_type 전체 목록"""

    STANDARD = {
        '주소지변경','상호변경','구조변경','전속계약 업체변경','성명변경','이름변경',
        '번호변경','대표자변경','자격증재교부','이전전출','등록이관','연락처변경',
        '대차/대폐차','자진말소',
    }

    def _suggest(ct, combined):
        ct_nc = ''.join(ct.split()).lower()
        c_nc  = ''.join(combined.split()).lower()
        if '말소' in ct_nc or '폐차' in ct_nc:            return '자진말소'
        if '상속' in ct_nc:                               return '상속(참고)'
        if '운행정지' in ct_nc:                            return '운행정지(참고)'
        if '영업소' in ct_nc or '주사무소' in ct_nc:        return '상호변경'
        if '변경허가' in ct_nc:                            return '기타(참고)'
        if ct_nc in ('-',''):                             return '기타'
        if '폐업' in ct_nc:                               return '자진말소'
        return '기타'

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()

    result = []
    for r in rows:
        ct = (r.change_type or '').strip()
        if ct in STANDARD: continue
        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        combined = ' '.join([ct, r.before_value or '', r.after_value or '', r.memo or '']
                            + [str(v) for v in raw.values()])
        result.append({
            'id': r.id,
            '현재_type': ct,
            '추천_type': _suggest(ct, combined),
            'vehicle_number': r.vehicle_number,
            'name': r.name,
            'change_date': r.change_date,
            'before': r.before_value or '',
            'after':  r.after_value  or '',
            'memo':   r.memo         or '',
            'raw_data': {k: str(raw[k])[:60] for k in list(raw.keys())[:12]},
            '추천사유': _suggest(ct, combined),
        })

    # 유형별 집계
    by_ct: dict = {}
    for r2 in result:
        ct2 = r2['현재_type']
        by_ct[ct2] = by_ct.get(ct2, 0) + 1

    return {
        '비표준총건수': len(result),
        '유형별건수': dict(sorted(by_ct.items(), key=lambda x: -x[1])),
        '전체목록': result,
    }


@router.post("/fix-specific-change-rows")
async def fix_specific_change_rows(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """특정 비정상 변경이력 행 처리.
    id 3636: 안내/메모 행 → soft-delete
    id 2321: 기타→구조변경, raw 주소컬럼 before/after 복구
    """
    from datetime import datetime, timezone
    results = []

    # id 3636 - 안내 행 삭제
    r3636 = db.query(models.ChangeHistory).filter(models.ChangeHistory.id==3636).first()
    if r3636:
        results.append({'id':3636,'action':'soft-delete','current_type':r3636.change_type,
                        'vehicle_number':r3636.vehicle_number,'name':r3636.name})
        if not dry_run:
            r3636.deleted_at = datetime.now(timezone.utc)

    # id 2321 - 기타→구조변경, 카고→내장탑 복구
    r2321 = db.query(models.ChangeHistory).filter(models.ChangeHistory.id==2321).first()
    if r2321:
        results.append({'id':2321,'action':'fix','current_type':r2321.change_type,
                        'current_before':r2321.before_value,'current_after':r2321.after_value,
                        '새_type':'구조변경','새_before':'카고','새_after':'내장탑',
                        '새_memo':'개별화물 (공문 잘못 옴)'})
        if not dry_run:
            r2321.change_type = '구조변경'
            r2321.before_value = '카고'
            r2321.after_value  = '내장탑'
            r2321.memo         = '개별화물 (공문 잘못 옴)'

    if not dry_run: db.commit()
    return {'dry_run': dry_run, '결과': results}


@router.post("/fix-change-number-types")
async def fix_change_number_types(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """번호변경 유형 분리. 버전: number-fix-v4
    우선순위:
    1. raw_data 전체 또는 before/after에 차량번호A->B 패턴 → 차량번호변경
    2. before/after에 010/011 전화번호 패턴 → 연락처변경
    3. 번호변경말소 키워드만 있고 차량번호 없음 → 말소/폐차
       memo = 번호변경말소 / 주소참고: raw_주소컬럼 (주소컬럼이 실제 주소일 때만)
    """
    import re as _re

    # 번호판 패턴 (느슨): 한글+숫자+한글+숫자
    VNO_PAT = _re.compile(r'(?:[가-힣]{2,3}\s*)?\d{2,3}[가-힣]\s*\d{3,4}(?:\s*호)?')
    ARROW   = r'\s*[-\u2013\u2192>]+\s*'
    VNO_ARROW_PAT = _re.compile(
        r'(' + VNO_PAT.pattern + r')' + ARROW + r'(' + VNO_PAT.pattern + r')'
    )
    PHONE_PAT = _re.compile(r'\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b')
    MALSO_KW  = ["번호변경말소", "번호변경 말소"]
    ADDR_KW   = ["길","로","번길","읍","면","동","리","번지","아파트","빌라","주공"]

    def _is_addr(v): return any(k in str(v or "") for k in ADDR_KW)
    def _is_malso_text(v):
        return "".join(str(v or "").split()).lower() in ("번호변경말소","번호변경말소")

    def _find_vno_arrow(text):
        m = VNO_ARROW_PAT.search(str(text or ""))
        if m: return m.group(1).strip(), m.group(2).strip()
        return "", ""

    def _get_raw_content(raw):
        """raw에서 내 용/비고 컬럼 값"""
        for k in raw:
            kn = "".join(str(k).split()).lower()
            if kn in ("내용","내 용","비고"):
                v = str(raw[k] or "").strip()
                if v and v not in ("-","nan"): return v
        return ""

    def _get_raw_addr(raw):
        """raw에서 주소 컬럼 값 (실제 주소인지도 확인)"""
        for k in raw:
            kn = "".join(str(k).split()).lower()
            if kn in ("주소","주      소","주 소"):
                v = str(raw[k] or "").strip()
                if v and v not in ("-","nan"): return v
        return ""

    TARGET_IDS = {1577, 1579, 1614, 3601, 3617}
    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.change_type == "번호변경",
    ).all()

    stats = {"dry_run": dry_run, "버전": "number-fix-v4",
             "전체": len(rows), "수정예정": 0, "유형수정": {}, "샘플": [], "target_debug": {}}

    for r in rows:
        bv   = (r.before_value or "").strip()
        av   = (r.after_value  or "").strip()
        memo = (r.memo         or "").strip()
        raw  = r.raw_data if isinstance(r.raw_data, dict) else {}
        raw_full = " ".join(str(v) for v in raw.values())
        raw_addr = _get_raw_addr(raw)
        raw_content = _get_raw_content(raw)

        ct = new_bv = new_av = new_memo = ""
        reason = ""

        # 1순위: 차량번호A->B 패턴 (before/after, raw 전체 순서로 탐색)
        sources_to_check = [bv + "->" + av, bv + " " + av, raw_full]
        for src in sources_to_check:
            vbv, vav = _find_vno_arrow(src)
            if vbv and vav:
                ct = "차량번호변경"
                new_bv, new_av = vbv, vav
                # memo: raw 내용/비고 컬럼 우선
                new_memo = raw_content or memo
                reason = f"차량번호A->B in {src[:40]!r}"
                break

        # 2순위: 전화번호 패턴
        if not ct:
            if PHONE_PAT.search(bv) or PHONE_PAT.search(av):
                ct = "연락처변경"
                new_bv, new_av, new_memo = bv, av, memo
                reason = "전화번호패턴"

        # 3순위: 번호변경말소 (차량번호 없음)
        if not ct:
            combined = " ".join([bv, av, memo, raw_full])
            has_malso = any("".join(k.split()) in "".join(combined.split()).lower() for k in MALSO_KW)
            has_vno = bool(VNO_ARROW_PAT.search(combined))
            if has_malso and not has_vno:
                ct = "말소/폐차"
                new_bv, new_av = "", ""
                # memo: 주소참고는 raw_addr이 실제 주소일 때만
                parts = ["번호변경말소"]
                if raw_addr and _is_addr(raw_addr) and not _is_malso_text(raw_addr):
                    parts.append(f"주소참고: {raw_addr}")
                new_memo = " / ".join(parts)
                reason = "번호변경말소→말소/폐차"

        sample = {
            "id": r.id, "name": r.name, "vehicle_number": r.vehicle_number,
            "기존_type": "번호변경", "새_type": ct if ct else "분류불가",
            "기존_before": bv, "새_before": new_bv,
            "기존_after": av, "새_after": new_av,
            "기존_memo": memo, "새_memo": new_memo,
            "raw_주소컬럼": raw_addr, "수정사유": reason,
        }
        if r.id in TARGET_IDS:
            stats["target_debug"][r.id] = sample

        if not ct: continue

        stats["수정예정"] += 1
        key = f"번호변경 → {ct}"
        stats["유형수정"][key] = stats["유형수정"].get(key, 0) + 1
        if len(stats["샘플"]) < 30:
            stats["샘플"].append(sample)
        if not dry_run:
            r.change_type = ct; r.before_value = new_bv
            r.after_value = new_av; r.memo = new_memo

    if not dry_run: db.commit()
    return stats

@router.post("/fix-company-name-address-misclassified")
async def fix_company_name_address(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """상호변경인데 before가 주소, after가 주소 조각인 건 → 주소지변경.
    버전: company-fix-v2
    조건: before에 주소 키워드(길/로/읍/면 등)가 반드시 있어야 함.
    업체명/사람이름이면 유지.
    """
    import re as _re
    ADDR_KW   = ["길","로","번길","읍","면","번지","아파트","빌라","주공"]
    # 실제 상호/업체명 키워드 (있으면 상호변경 유지)
    COMPANY_KW = ["운수","택배","화물","물류","용달","이사","로지스","렉카","사다리",
                  "서비스","엔지니어링","㈜","(주)","주식회사"]

    def _is_addr_bv(v):
        """before가 주소인지: 길/로 등 키워드 포함"""
        return any(k in str(v or "") for k in ADDR_KW)
    def _is_company(v):
        return any(k in str(v or "") for k in COMPANY_KW)
    def _is_person_name(v):
        v = str(v or "").strip()
        import re as r
        return bool(r.match(r"^[가-힣]{2,4}$", v))

    TARGET_VNO = {"강원81자339", "강원90아3177"}
    def _nc_vno(v): return "".join(str(v or "").split()).lower().replace("호","")

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.change_type == "상호변경",
    ).all()

    stats = {"dry_run": dry_run, "버전": "company-fix-v2",
             "전체": len(rows), "수정예정": 0, "샘플": [], "target_debug": {}}

    for r in rows:
        bv = (r.before_value or "").strip()
        av = (r.after_value  or "").strip()

        # before에 주소 키워드가 있어야 함 (핵심 조건)
        if not _is_addr_bv(bv): continue
        # 업체명 키워드가 있으면 상호변경 유지
        if _is_company(bv) or _is_company(av): continue
        # before가 사람이름이면 유지
        if _is_person_name(bv): continue

        sample = {
            "id": r.id, "기존_type": "상호변경", "새_type": "주소지변경",
            "before": bv, "after": av, "memo": r.memo or "",
            "vehicle_number": r.vehicle_number, "name": r.name,
            "수정사유": "before에주소키워드있음",
        }
        vno_nc = _nc_vno(r.vehicle_number)
        if any(vno_nc == t for t in {_nc_vno(t) for t in TARGET_VNO}):
            stats["target_debug"][r.vehicle_number] = sample

        stats["수정예정"] += 1
        if len(stats["샘플"]) < 30:
            stats["샘플"].append(sample)
        if not dry_run: r.change_type = "주소지변경"

    if not dry_run: db.commit()
    return stats

@router.post("/fix-structure-address-misclassified")
async def fix_structure_address(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """구조변경인데 before/after가 주소인 건 → 주소지변경"""
    ADDR_KW  = ["길","로","번길","읍","면","동","리","호","아파트","빌라","주공"]
    STRUCT_KW = ["탑","윙","카고","봉고","포터","리프트","냉동","냉장","파워게이트",
                 "픽업","덮개","호로","가축","수직","구조변경","장착","탈거"]

    def _is_addr(v): return any(k in str(v or "") for k in ADDR_KW)
    def _is_struct(v): return any(k in str(v or "") for k in STRUCT_KW)

    TARGET_VNO2 = {"강원80자5162", "강원82자8563"}
    def _nc_vno2(v): return "".join(str(v or "").split()).lower()

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.change_type == "구조변경",
    ).all()

    stats = {"dry_run": dry_run, "전체": len(rows), "수정예정": 0, "샘플": [], "target_debug": {}}

    for r in rows:
        bv = (r.before_value or "").strip()
        av = (r.after_value  or "").strip()
        if not bv and not av: continue
        if _is_struct(bv) or _is_struct(av): continue  # 구조변경 키워드 있으면 유지
        if _is_addr(bv) or _is_addr(av):
            sample = {"id": r.id, "기존_type": "구조변경", "새_type": "주소지변경",
                      "before": bv, "after": av, "memo": r.memo or "",
                      "vehicle_number": r.vehicle_number, "name": r.name,
                      "수정사유": "구조변경인데before/after주소형태"}
            if _nc_vno2(r.vehicle_number) in TARGET_VNO2:
                stats["target_debug"][r.vehicle_number] = sample
            stats["수정예정"] += 1
            if len(stats["샘플"]) < 50:
                stats["샘플"].append(sample)
            if not dry_run: r.change_type = "주소지변경"

    if not dry_run: db.commit()
    return stats


@router.get("/change-history-types")
async def change_history_types(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """변경등록대장의 distinct change_type 목록 반환 (건수 내림차순)"""
    from sqlalchemy import func as _func
    rows = db.query(
        models.ChangeHistory.change_type,
        _func.count(models.ChangeHistory.id).label("cnt")
    ).filter(
        models.ChangeHistory.deleted_at.is_(None),
        models.ChangeHistory.change_type.isnot(None),
        models.ChangeHistory.change_type != "",
    ).group_by(models.ChangeHistory.change_type).order_by(_func.count(models.ChangeHistory.id).desc()).all()
    return {"types": [r.change_type for r in rows if r.change_type]}
