"""
엑셀 업로드 라우터
- 모든 행 저장 (빈 필드 있어도 절대 누락 금지)
- 개인/택배 카운트 별도 집계
- raw_data에 원본 데이터 전체 보존
- 실패 = DB 오류만 (빈 필드 ≠ 실패)
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user
from app import models, crud
from app.excel_utils import excel_to_records, normalize_membership_status, normalize_closure_type
from app.routers.change_history import normalize_change_type

router = APIRouter()

# 파일 종류 → DB 모델
FILE_MODEL = {
    '면허자현황':     models.LicenseHolder,
    '양도양수대장':   models.TransferLedger,
    '폐지현황':      models.Closure,
    '이전폐지현황':   models.Closure,
    '변경이력대장':   models.ChangeHistory,
    '주소지변경대장': models.ChangeHistory,
    '변경등록대장':   models.ChangeHistory,
}


@router.post('/preview')
async def preview(
    file_type: str = Form(...),
    file: UploadFile = File(...),
    _=Depends(get_current_user),
):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        raise HTTPException(400, '엑셀 파일(.xlsx/.xls/.xlsm)만 가능합니다.')
    content = await file.read()
    try:
        result = excel_to_records(content, file_type, preview=True, preview_n=10)
        records, cmap, unmapped = result[0], result[1], result[2]
        sheet_logs = result[3] if len(result) > 3 else []
    except Exception as e:
        raise HTTPException(400, str(e))
    return {
        'file_type': file_type,
        'filename': file.filename,
        'total_preview': len(records),
        'col_mapping': {k: v for k, v in cmap.items()},
        'unmapped_columns': unmapped,
        'preview_rows': records[:10],
        'sheet_logs': sheet_logs,
    }


@router.post('/upload')
async def upload(
    file_type: str = Form(...),
    duplicate_handling: str = Form('skip'),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not file.filename.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        raise HTTPException(400, '엑셀 파일만 업로드 가능합니다.')

    if file_type == '부과대수':
        return await _upload_allocation(file, db, current_user)

    if file_type not in FILE_MODEL:
        raise HTTPException(400, f'지원하지 않는 파일 종류: {file_type}')

    content = await file.read()
    try:
        result = excel_to_records(content, file_type)
        records, unmapped = result[0], result[2]
        sheet_logs = result[3] if len(result) > 3 else []
    except Exception as e:
        raise HTTPException(400, str(e))

    model = FILE_MODEL[file_type]
    allowed = {c.name for c in model.__table__.columns}

    success = individual = delivery = duplicate = err_cnt = 0
    errors = []

    for i, rec in enumerate(records):
        try:
            # 면허자현황: 차량번호 기반 카테고리 자동 판단
            if model == models.LicenseHolder:
                vn = rec.get('vehicle_number', '')
                cat = '택배' if vn and '배' in vn else '개인'
                rec.setdefault('category', cat)
                rec.setdefault('status', 'active')
                rec.setdefault('registration_type', '엑셀업로드')
                # 가입/미가입 정규화
                ms = rec.get('membership_status', '')
                rec['membership_status'] = normalize_membership_status(ms)

            # 변경이력: change_type 정규화
            if model == models.ChangeHistory:
                if rec.get('change_type'):
                    rec['change_type'] = normalize_change_type(rec['change_type'])
            if file_type == '폐지현황':
                rec.setdefault('data_type', '신규자료')
                if rec.get('closure_type'):
                    rec['closure_type'] = normalize_closure_type(rec['closure_type'])
            elif file_type == '이전폐지현황':
                rec.setdefault('data_type', '이전자료')
                if rec.get('closure_type'):
                    rec['closure_type'] = normalize_closure_type(rec['closure_type'])

            # 중복 체크
            existing = _find_dup(db, model, rec, file_type)

            if existing:
                if duplicate_handling == 'skip':
                    duplicate += 1
                    continue
                elif duplicate_handling == 'overwrite':
                    clean = {k: v for k, v in rec.items() if k in allowed}
                    for k, v in clean.items():
                        setattr(existing, k, v)
                    db.flush()
                    success += 1
                    duplicate += 1
                    if model == models.LicenseHolder:
                        if rec.get('category') == '택배':
                            delivery += 1
                        else:
                            individual += 1
                    continue
                # 'add': 그냥 삽입

            # 저장 (빈 필드도 그대로, DB 오류만 실패)
            clean = {k: v for k, v in rec.items() if k in allowed}
            db.add(model(**clean))
            db.flush()
            success += 1
            if model == models.LicenseHolder:
                if rec.get('category') == '택배':
                    delivery += 1
                else:
                    individual += 1

        except Exception as ex:
            err_cnt += 1
            errors.append({'row': i + 2, 'error': str(ex)[:300]})

    db.commit()
    db.add(models.UploadHistory(
        file_type=file_type, filename=file.filename,
        total_count=len(records), success_count=success,
        duplicate_count=duplicate, error_count=err_cnt,
        uploaded_by=current_user.username, error_details=errors[:50],
    ))
    db.commit()

    return {
        'total': len(records),
        'success': success,
        'individual_count': individual,
        'delivery_count': delivery,
        'duplicates': duplicate,
        'errors': err_cnt,
        'error_details': errors[:30],
        'unmapped_columns': unmapped,
        'sheet_logs': sheet_logs,
        'file_type': file_type,
    }


def _find_dup(db, model, rec, file_type):
    """중복 행 검색"""
    try:
        vn = rec.get('vehicle_number', '')
        if not vn:
            return None
        if model == models.LicenseHolder:
            return db.query(model).filter(
                model.vehicle_number == vn,
                model.deleted_at.is_(None),
            ).first()
        elif model == models.TransferLedger:
            seq = rec.get('seq_number', '')
            if seq:
                return db.query(model).filter(
                    model.seq_number == seq, model.deleted_at.is_(None)
                ).first()
        elif model == models.Closure:
            mgmt = rec.get('management_number', '')
            if mgmt:
                return db.query(model).filter(
                    model.management_number == mgmt, model.deleted_at.is_(None)
                ).first()
    except Exception:
        pass
    return None


async def _upload_allocation(file, db, current_user):
    content = await file.read()
    records, _, unmapped = excel_to_records(content, '부과대수')
    FIELDS = ['association_join', 'transfer_in', 'other_region', 'closed',
              'withdrawn', 'delivery_new', 'mgmt_fee_closed', 'over_70',
              'base_count', 'total_count', 'delivery_mgmt']
    success, errors = 0, []
    for i, rec in enumerate(records):
        try:
            y = int(float(rec.get('year') or 0))
            m = int(float(rec.get('month') or 0))
            if not y or not m:
                continue
            vals = {}
            for f in FIELDS:
                rv = rec.get(f, '')
                try:
                    vals[f] = int(float(rv)) if rv else 0
                except (ValueError, TypeError):
                    vals[f] = 0
            row = db.query(models.AllocationCount).filter(
                models.AllocationCount.year == y,
                models.AllocationCount.month == m,
            ).first()
            if row:
                for f, v in vals.items():
                    setattr(row, f, v)
            else:
                row = models.AllocationCount(year=y, month=m, **vals)
                db.add(row)
            db.flush()
            success += 1
        except Exception as ex:
            errors.append({'row': i + 2, 'error': str(ex)[:200]})
    db.commit()
    db.add(models.UploadHistory(
        file_type='부과대수', filename=file.filename,
        total_count=len(records), success_count=success,
        error_count=len(errors), uploaded_by=current_user.username,
        error_details=errors[:30],
    ))
    db.commit()
    return {
        'total': len(records), 'success': success,
        'individual_count': 0, 'delivery_count': 0,
        'duplicates': 0, 'errors': len(errors),
        'error_details': errors[:20], 'unmapped_columns': unmapped,
        'file_type': '부과대수',
    }
