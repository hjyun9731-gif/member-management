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


@router.get("/debug-change-log")
async def debug_change_log(
    vehicle: str = "", name: str = "", month: str = "",
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """변경등록대장 파싱/분류 디버그
    month 형식: 2026-05
    """
    import re as _re
    from app.routers.dashboard import _ext_year

    q = db.query(models.ChangeHistory).filter(models.ChangeHistory.deleted_at.is_(None))

    def _norm(v): return _re.sub(r'\s+','',str(v or '')).lower()

    rows = q.all()
    result = []
    for r in rows:
        if vehicle and _norm(vehicle) not in _norm(r.vehicle_number): continue
        if name and name not in (r.name or ''): continue
        if month:
            y, mo = month.split('-')
            date_str = r.change_date or r.receipt_date or ''
            yr = _ext_year(date_str)
            # 월 판단
            m2 = _re.search(r'(\d{1,2})\.\s*(\d{1,2})', date_str)
            if not m2: continue
            if int(y) != yr or int(m2.group(1)) != int(mo): continue

        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        result.append({
            "id": r.id, "change_type": r.change_type,
            "change_date": r.change_date, "receipt_date": r.receipt_date,
            "region": r.region, "vehicle_number": r.vehicle_number, "name": r.name,
            "before_value": r.before_value, "after_value": r.after_value, "memo": r.memo,
            "raw_data_keys": list(raw.keys())[:20],
            "raw_data": {k: str(raw[k])[:50] for k in list(raw.keys())[:15]},
        })
        if len(result) >= 20: break

    # 월별 집계 요약
    summary = {}
    if month:
        summary["월"] = month
        for r2 in rows:
            date_str = r2.change_date or r2.receipt_date or ''
            yr = _ext_year(date_str)
            m2 = _re.search(r'(\d{1,2})\.\s*(\d{1,2})', date_str)
            if not m2: continue
            y2, mo2 = month.split('-')
            if int(y2) != yr or int(m2.group(1)) != int(mo2): continue
            ct = r2.change_type or '기타'
            summary[ct] = summary.get(ct, 0) + 1

    return {"총건수": len(result), "월별집계": summary, "결과": result}


@router.get("/debug-change-log")
async def debug_change_log(
    vehicle: str = "", name: str = "", month: str = "",
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """변경등록대장 파싱 디버그. month=2026-05 형식"""
    import re as _re
    from app.routers.dashboard import _ext_year

    BEFORE_KEYS = ['변경전','변경 전','이전주소','변경전주소','변경전내용','이전내용','종전주소','전주소','이전']
    AFTER_KEYS  = ['변경후','변경 후','현재주소','변경후주소','변경후내용','현재내용','신주소','새주소','변경된내용']

    def _norm(v): return _re.sub(r'\s+','',str(v or '')).lower()
    def _find_raw(raw, keys):
        for k in raw:
            if _norm(k) in [_norm(x) for x in keys]:
                v = str(raw[k] or '').strip()
                if v and v not in ('-','nan'): return v, k
        return '', ''

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()

    result = []
    for r in rows:
        if vehicle and _norm(vehicle) not in _norm(r.vehicle_number): continue
        if name and name not in (r.name or ''): continue
        if month:
            y2, mo2 = month.split('-')
            date_str = r.change_date or r.receipt_date or ''
            yr = _ext_year(date_str)
            m2 = _re.search(r'(\d{1,2})\.\s*(\d{1,2})', date_str)
            if not m2 or int(y2) != yr or int(m2.group(1)) != int(mo2): continue

        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        raw_bv, raw_bk = _find_raw(raw, BEFORE_KEYS)
        raw_av, raw_ak = _find_raw(raw, AFTER_KEYS)

        result.append({
            "id": r.id, "change_type": r.change_type,
            "vehicle_number": r.vehicle_number, "name": r.name,
            "change_date": r.change_date,
            "DB_before_value": r.before_value,
            "DB_after_value": r.after_value,
            "DB_memo": r.memo,
            "raw_before_후보": raw_bv, "raw_before_키": raw_bk,
            "raw_after_후보": raw_av, "raw_after_키": raw_ak,
            "raw_keys": list(raw.keys())[:20],
            "불일치": (r.before_value or '') != raw_bv or (r.after_value or '') != raw_av,
        })
        if len(result) >= 20: break

    # 월별 집계
    summary = {}
    if month:
        y2, mo2 = month.split('-')
        for r2 in rows:
            date_str = r2.change_date or r2.receipt_date or ''
            yr = _ext_year(date_str)
            m2 = _re.search(r'(\d{1,2})\.\s*(\d{1,2})', date_str)
            if not m2 or int(y2) != yr or int(m2.group(1)) != int(mo2): continue
            ct = r2.change_type or '기타'
            summary[ct] = summary.get(ct, 0) + 1

    return {"총건수": len(result), "월별집계": summary, "결과": result}


@router.post("/backfill-change-before-after")
async def backfill_change_before_after(
    dry_run: bool = True,
    db: Session = Depends(get_db), _=Depends(require_admin)
):
    """변경등록대장 raw_data에서 변경전/변경후 재추출하여 DB 복구
    dry_run=true: 저장 없이 결과만
    """
    import re as _re

    BEFORE_KEYS = ['변경전','변경 전','이전주소','변경전주소','변경전내용','이전내용','종전주소','전주소']
    AFTER_KEYS  = ['변경후','변경 후','현재주소','변경후주소','변경후내용','현재내용','신주소','새주소']

    def _norm(v): return _re.sub(r'\s+','',str(v or '')).lower()
    def _find(raw, keys):
        for k in raw:
            if _norm(k) in [_norm(x) for x in keys]:
                v = str(raw[k] or '').strip()
                if v and v not in ('-','nan','None'): return v
        return ''

    rows = db.query(models.ChangeHistory).filter(
        models.ChangeHistory.deleted_at.is_(None)).all()

    stats = {'dry_run': dry_run, '전체': len(rows),
             'raw있음': 0, 'before복구': 0, 'after복구': 0, '샘플': []}

    for r in rows:
        raw = r.raw_data if isinstance(r.raw_data, dict) else {}
        if not raw: continue
        stats['raw있음'] += 1

        raw_bv = _find(raw, BEFORE_KEYS)
        raw_av = _find(raw, AFTER_KEYS)

        will_bv = bool(raw_bv and not r.before_value)
        will_av = bool(raw_av and not r.after_value)

        if will_bv: stats['before복구'] += 1
        if will_av: stats['after복구'] += 1

        if len(stats['샘플']) < 10 and (will_bv or will_av):
            stats['샘플'].append({
                '관리번호': r.vehicle_number, '성명': r.name,
                '변경유형': r.change_type,
                '현재before': r.before_value, '복구before': raw_bv if will_bv else '(유지)',
                '현재after': r.after_value, '복구after': raw_av if will_av else '(유지)',
            })

        if not dry_run:
            if will_bv: r.before_value = raw_bv
            if will_av: r.after_value = raw_av

    if not dry_run:
        db.commit()
    return stats
