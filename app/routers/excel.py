"""
엑셀 업로드 라우터
- 행별 savepoint로 PostgreSQL 세션 오염 방지
- raw_data(dict)는 JSON 컬럼에 그대로 유지
- 값 정제: NaN/None/길이초과만 처리, dict는 변환 안 함
- 중복: management_number(있으면) 또는 vehicle_number 기준
- 시트 이름으로 category 강제 설정 (개인/택배 시트)
"""
import math
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db
from app.auth import get_current_user
from app import models, crud
from app.excel_utils import excel_to_records, normalize_membership_status, normalize_closure_type
from app.routers.change_history import normalize_change_type

router = APIRouter()

FILE_MODEL = {
    '면허자현황':       models.LicenseHolder,
    '양도양수대장':     models.TransferLedger,
    '폐지현황':        models.Closure,
    '이전폐지현황':     models.Closure,
    '변경이력대장':     models.ChangeHistory,
    '주소변경등록대장': models.ChangeHistory,
    '주소지변경대장':   models.ChangeHistory,
    '변경등록대장':     models.ChangeHistory,
}

_COL_MAX_LEN = {
    'vehicle_number':50,'name':100,'region':50,'category':20,'phone':50,'mobile':50,
    'management_number':50,'status':20,'membership_status':20,'membership_date':50,
    'approval_date':50,'certificate_issue_date':50,'certificate_number':100,
    'permit_number':100,'driver_license_number':100,'vehicle_type':50,'fuel_type':30,
    'business_number':50,'affiliated_company':200,'resident_number':30,
    'company_name':200,'registration_type':20,'reapproval_date':50,
    'agent_name':100,'agent_resident_number':30,'agent_mobile':50,
    'change_type':50,'change_date':50,'receipt_date':50,
    'closure_type':50,'closure_date':50,'data_type':50,'seq_number':50,
}

_STR_NONE = {'nan','none','nat','#n/a','n/a','null',''}


def _sanitize(rec: dict, allowed: set) -> dict:
    """DB 저장 전 정제. dict/list(raw_data 등)는 그대로 유지."""
    clean = {}
    for k, v in rec.items():
        if k not in allowed:
            continue
        if v is None:
            clean[k] = None
            continue
        # dict/list는 JSON 컬럼용 → 그대로
        if isinstance(v, (dict, list)):
            clean[k] = v
            continue
        # float NaN/inf → None
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            clean[k] = None
            continue
        # 숫자(int/float) → 문자열 변환 후 처리
        if isinstance(v, (int, float)):
            v = str(int(v)) if isinstance(v, float) and v == int(v) else str(v)
        # 문자열 정제
        s = str(v).strip()
        if s.lower() in _STR_NONE:
            clean[k] = None
            continue
        # 길이 초과 자르기
        max_len = _COL_MAX_LEN.get(k)
        if max_len and len(s) > max_len:
            s = s[:max_len]
        clean[k] = s
    return clean


def _row_label(rec: dict, i: int) -> str:
    vn = rec.get('vehicle_number') or ''
    nm = (rec.get('name') or rec.get('transferee') or rec.get('transferor') or '')
    parts = [f"{i+2}행"]
    if vn: parts.append(vn)
    if nm: parts.append(nm)
    return ' / '.join(parts)


def _prep_lh(rec: dict, file_type: str) -> dict:
    """LicenseHolder 전처리: category는 파싱된 값 우선, 없으면 차량번호 기준"""
    vn = rec.get('vehicle_number') or ''
    # category가 이미 파싱됨(엑셀의 '개인/택배' 컬럼 또는 시트 이름에서)
    cat = rec.get('category') or ''
    if cat not in ('개인', '택배'):
        # 차량번호에 '배' 포함이면 택배, 아니면 개인
        cat = '택배' if '배' in vn else '개인'
    rec['category'] = cat
    rec.setdefault('status', 'active')
    rec.setdefault('registration_type', '엑셀업로드')
    rec['membership_status'] = normalize_membership_status(rec.get('membership_status') or '')
    return rec


def _find_dup(db, model, rec, file_type):
    """중복 검색: management_number 우선, 없으면 vehicle_number"""
    try:
        # management_number 기준 (가장 신뢰성 높음)
        mgmt = rec.get('management_number') or ''
        if mgmt and hasattr(model, 'management_number'):
            hit = db.query(model).filter(
                model.management_number == mgmt,
                model.deleted_at.is_(None),
            ).first()
            if hit:
                return hit

        vn = rec.get('vehicle_number') or ''
        if not vn:
            return None

        if model == models.LicenseHolder:
            return db.query(model).filter(
                model.vehicle_number == vn,
                model.deleted_at.is_(None),
            ).first()
        elif model == models.TransferLedger:
            seq = rec.get('seq_number') or ''
            if seq:
                return db.query(model).filter(
                    model.seq_number == seq, model.deleted_at.is_(None)
                ).first()
        elif model == models.Closure:
            if mgmt:
                return db.query(model).filter(
                    model.management_number == mgmt, model.deleted_at.is_(None)
                ).first()
    except Exception:
        pass
    return None


def _save_row(db, model, clean: dict, existing=None, duplicate_handling='skip'):
    """한 행 저장. savepoint로 실패 격리."""
    sp = f"sp_{id(clean)}"
    db.execute(text(f"SAVEPOINT {sp}"))
    try:
        if existing:
            if duplicate_handling == 'skip':
                db.execute(text(f"RELEASE SAVEPOINT {sp}"))
                return 'dup_skip'
            elif duplicate_handling == 'overwrite':
                for k, v in clean.items():
                    if k not in ('id', 'created_at'):
                        setattr(existing, k, v)
                db.flush()
                db.execute(text(f"RELEASE SAVEPOINT {sp}"))
                return 'dup_overwrite'
            else:  # add
                pass  # fall through to insert
        obj = model(**clean)
        db.add(obj)
        db.flush()
        db.execute(text(f"RELEASE SAVEPOINT {sp}"))
        return 'ok'
    except Exception as e:
        db.execute(text(f"ROLLBACK TO SAVEPOINT {sp}"))
        db.execute(text(f"RELEASE SAVEPOINT {sp}"))
        raise


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
        'file_type': file_type, 'filename': file.filename,
        'total_preview': len(records),
        'col_mapping': {k: v for k, v in cmap.items()},
        'unmapped_columns': unmapped, 'preview_rows': records[:10],
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
        raise HTTPException(400, f'파싱 오류: {e}')

    model = FILE_MODEL[file_type]
    allowed = {c.name for c in model.__table__.columns}

    # PostgreSQL이면 savepoint 지원, SQLite면 일반 방식
    from app.database import DATABASE_URL
    use_savepoint = 'postgresql' in DATABASE_URL or 'postgres' in DATABASE_URL

    success = individual = delivery = duplicate = err_cnt = 0
    errors = []

    for i, rec in enumerate(records):
        label = _row_label(rec, i)
        try:
            # 전처리
            if model == models.LicenseHolder:
                rec = _prep_lh(rec, file_type)
            elif model == models.ChangeHistory:
                ct = rec.get('change_type') or ''
                if not ct or ct in ('기타', '기타변경'):
                    probe = [rec.get('memo',''), rec.get('before_value',''), rec.get('after_value','')]
                    if isinstance(rec.get('raw_data'), dict):
                        for k in ('비고','변경내용','변경유형','구분','변경종류','메모'):
                            v = rec['raw_data'].get(k,'')
                            if v: probe.append(str(v))
                    for txt in probe:
                        if txt and txt.strip():
                            d = normalize_change_type(txt)
                            if d and d != '기타': ct = d; break
                if file_type in ('주소변경등록대장','주소지변경대장'):
                    rec['change_type'] = '주소지변경'
                elif ct:
                    rec['change_type'] = normalize_change_type(ct)
                else:
                    rec.setdefault('change_type', '기타')
            elif file_type == '폐지현황':
                rec.setdefault('data_type', '신규자료')
                if rec.get('closure_type'):
                    rec['closure_type'] = normalize_closure_type(rec['closure_type'])
            elif file_type == '이전폐지현황':
                rec.setdefault('data_type', '이전자료')
                if rec.get('closure_type'):
                    rec['closure_type'] = normalize_closure_type(rec['closure_type'])

            # 중복 체크
            existing = _find_dup(db, model, rec, file_type)
            if existing and duplicate_handling == 'skip':
                duplicate += 1
                continue

            # 정제 후 저장
            clean = _sanitize(rec, allowed)

            if use_savepoint:
                result_code = _save_row(db, model, clean, existing, duplicate_handling)
            else:
                # SQLite: 일반 방식
                try:
                    if existing and duplicate_handling == 'overwrite':
                        for k, v in clean.items():
                            if k not in ('id', 'created_at'): setattr(existing, k, v)
                        db.flush()
                        result_code = 'dup_overwrite'
                    elif existing:
                        result_code = 'dup_skip'
                    else:
                        db.add(model(**clean)); db.flush()
                        result_code = 'ok'
                except Exception as ex:
                    try: db.rollback()
                    except: pass
                    raise ex

            if result_code == 'dup_skip':
                duplicate += 1
                continue
            elif result_code == 'dup_overwrite':
                success += 1; duplicate += 1
            else:
                success += 1

            if model == models.LicenseHolder:
                if rec.get('category') == '택배': delivery += 1
                else: individual += 1

        except Exception as ex:
            err_cnt += 1
            short_err = str(ex).split('\n')[0][:300]
            errors.append({
                'row': i + 2, 'label': label,
                'vehicle_number': rec.get('vehicle_number',''),
                'name': rec.get('name',''),
                'error': short_err,
            })

    # 최종 commit
    try:
        db.commit()
    except Exception as e:
        try: db.rollback()
        except: pass
        return {
            'total': len(records), 'success': 0,
            'individual_count': 0, 'delivery_count': 0,
            'duplicates': 0, 'errors': len(records),
            'error_details': [{'row':0,'label':'전체','error':f'최종 저장 실패: {str(e)[:300]}'}],
            'unmapped_columns': unmapped, 'sheet_logs': sheet_logs, 'file_type': file_type,
        }

    # 이력 저장
    try:
        db.add(models.UploadHistory(
            file_type=file_type, filename=file.filename,
            total_count=len(records), success_count=success,
            duplicate_count=duplicate, error_count=err_cnt,
            uploaded_by=current_user.username,
            error_details=[{'row':e['row'],'error':e['error']} for e in errors[:50]],
        ))
        db.commit()
    except Exception:
        pass

    return {
        'total': len(records), 'success': success,
        'individual_count': individual, 'delivery_count': delivery,
        'duplicates': duplicate, 'errors': err_cnt,
        'error_details': errors[:50],
        'unmapped_columns': unmapped, 'sheet_logs': sheet_logs, 'file_type': file_type,
    }


async def _upload_allocation(file, db, current_user):
    content = await file.read()
    records, _, unmapped = excel_to_records(content, '부과대수')
    FIELDS = ['association_join','transfer_in','other_region','closed','withdrawn',
              'delivery_new','mgmt_fee_closed','over_70','base_count','total_count','delivery_mgmt']
    from app.database import DATABASE_URL
    use_sp = 'postgresql' in DATABASE_URL or 'postgres' in DATABASE_URL
    success, errors = 0, []
    for i, rec in enumerate(records):
        try:
            y = int(float(rec.get('year') or 0))
            m = int(float(rec.get('month') or 0))
            if not y or not m: continue
            vals = {}
            for f in FIELDS:
                rv = rec.get(f,'')
                try: vals[f] = int(float(rv)) if rv else 0
                except: vals[f] = 0
            row = db.query(models.AllocationCount).filter(
                models.AllocationCount.year==y, models.AllocationCount.month==m).first()
            sp = f"sp_alloc_{i}"
            if use_sp: db.execute(text(f"SAVEPOINT {sp}"))
            try:
                if row:
                    for f,v in vals.items(): setattr(row, f, v)
                else:
                    row = models.AllocationCount(year=y, month=m, **vals)
                    db.add(row)
                db.flush()
                if use_sp: db.execute(text(f"RELEASE SAVEPOINT {sp}"))
                success += 1
            except Exception as ex:
                if use_sp:
                    db.execute(text(f"ROLLBACK TO SAVEPOINT {sp}"))
                    db.execute(text(f"RELEASE SAVEPOINT {sp}"))
                else:
                    try: db.rollback()
                    except: pass
                raise ex
        except Exception as ex:
            errors.append({'row':i+2,'error':str(ex).split('\n')[0][:200]})
    try: db.commit()
    except:
        try: db.rollback()
        except: pass
    try:
        db.add(models.UploadHistory(
            file_type='부과대수', filename=file.filename,
            total_count=len(records), success_count=success,
            error_count=len(errors), uploaded_by=current_user.username,
            error_details=errors[:30],
        ))
        db.commit()
    except: pass
    return {
        'total':len(records),'success':success,'individual_count':0,'delivery_count':0,
        'duplicates':0,'errors':len(errors),'error_details':errors[:20],
        'unmapped_columns':unmapped,'file_type':'부과대수',
    }
