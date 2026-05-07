"""
엑셀 업로드 라우터
- upload_id: 업로드 이력 ID를 각 데이터 행에 저장 → 개별 삭제 가능
- 행별 savepoint로 PostgreSQL 세션 오염 방지
- 값 정제: NaN/None/빈값/길이초과만 처리, dict는 변환 안 함
- 중복: management_number 우선, 없으면 vehicle_number
"""
import math
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.database import get_db, DATABASE_URL
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
_USE_SP   = 'postgresql' in DATABASE_URL or 'postgres' in DATABASE_URL


def _sanitize(rec: dict, allowed: set) -> dict:
    clean = {}
    for k, v in rec.items():
        if k not in allowed:
            continue
        if v is None:
            clean[k] = None; continue
        if isinstance(v, (dict, list)):
            clean[k] = v; continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            clean[k] = None; continue
        if isinstance(v, (int, float)):
            v = str(int(v)) if isinstance(v, float) and v == int(v) else str(v)
        s = str(v).strip()
        if s.lower() in _STR_NONE:
            clean[k] = None; continue
        max_len = _COL_MAX_LEN.get(k)
        if max_len and len(s) > max_len:
            s = s[:max_len]
        clean[k] = s
    return clean


def _row_label(rec: dict, i: int) -> str:
    vn = rec.get('vehicle_number') or ''
    nm = rec.get('name') or rec.get('transferee') or rec.get('transferor') or ''
    parts = [f"{i+2}행"]
    if vn: parts.append(vn)
    if nm: parts.append(nm)
    return ' / '.join(parts)


def _prep_lh(rec: dict, file_type: str) -> dict:
    vn = rec.get('vehicle_number') or ''
    cat = rec.get('category') or ''
    if cat not in ('개인', '택배'):
        cat = '택배' if '배' in vn else '개인'
    rec['category'] = cat
    rec.setdefault('status', 'active')
    rec.setdefault('registration_type', '엑셀업로드')
    rec['membership_status'] = normalize_membership_status(rec.get('membership_status') or '')
    return rec


def _find_dup(db, model, rec, file_type):
    try:
        mgmt = (rec.get('management_number') or '').strip()
        vn   = (rec.get('vehicle_number') or '').strip()
        if model == models.LicenseHolder:
            if mgmt:
                hit = db.query(model).filter(model.management_number==mgmt, model.deleted_at.is_(None)).first()
                if hit: return hit
            if vn:
                return db.query(model).filter(model.vehicle_number==vn, model.deleted_at.is_(None)).first()
        elif model == models.TransferLedger:
            if mgmt:
                hit = db.query(model).filter(model.management_number==mgmt, model.deleted_at.is_(None)).first()
                if hit: return hit
            seq = (rec.get('seq_number') or '').strip()
            if seq and vn:
                return db.query(model).filter(model.seq_number==seq, model.vehicle_number==vn, model.deleted_at.is_(None)).first()
        elif model == models.Closure:
            if mgmt:
                return db.query(model).filter(model.management_number==mgmt, model.deleted_at.is_(None)).first()
        elif model == models.ChangeHistory:
            return None
    except Exception:
        pass
    return None


def _save_row(db, model, clean: dict, existing=None, dup='skip'):
    sp = f"sp_{abs(hash(str(clean)))}"
    if _USE_SP:
        db.execute(text(f"SAVEPOINT {sp}"))
    try:
        if existing:
            if dup == 'skip':
                if _USE_SP: db.execute(text(f"RELEASE SAVEPOINT {sp}"))
                return 'dup_skip'
            for k, v in clean.items():
                if k not in ('id','created_at'): setattr(existing, k, v)
            db.flush()
            if _USE_SP: db.execute(text(f"RELEASE SAVEPOINT {sp}"))
            return 'dup_overwrite'
        obj = model(**clean)
        db.add(obj)
        db.flush()
        if _USE_SP: db.execute(text(f"RELEASE SAVEPOINT {sp}"))
        return 'ok'
    except Exception as e:
        if _USE_SP:
            db.execute(text(f"ROLLBACK TO SAVEPOINT {sp}"))
            db.execute(text(f"RELEASE SAVEPOINT {sp}"))
        else:
            try: db.rollback()
            except: pass
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

    model   = FILE_MODEL[file_type]
    allowed = {c.name for c in model.__table__.columns}

    # ── 1. 업로드 이력을 먼저 저장해서 upload_id 확보 ──
    upload_id = None
    try:
        hist = models.UploadHistory(
            file_type=file_type, filename=file.filename,
            total_count=len(records), success_count=0,
            duplicate_count=0, error_count=0,
            uploaded_by=current_user.username,
            error_details=[],
        )
        db.add(hist)
        db.flush()
        db.refresh(hist)
        upload_id = hist.id
    except Exception:
        try: db.rollback()
        except: pass

    # ── 2. 행별 저장 (upload_id 포함) ──
    success = individual = delivery = duplicate = err_cnt = 0
    errors  = []

    for i, rec in enumerate(records):
        label = _row_label(rec, i)
        try:
            # 전처리
            if model == models.LicenseHolder:
                rec = _prep_lh(rec, file_type)
            elif model == models.ChangeHistory:
                ct = rec.get('change_type') or ''
                if not ct or ct in ('기타','기타변경'):
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
                if rec.get('closure_type'): rec['closure_type'] = normalize_closure_type(rec['closure_type'])
            elif file_type == '이전폐지현황':
                rec.setdefault('data_type', '이전자료')
                if rec.get('closure_type'): rec['closure_type'] = normalize_closure_type(rec['closure_type'])

            # upload_id 설정 (이력별 삭제를 위해)
            if upload_id and 'upload_id' in allowed:
                rec['upload_id'] = upload_id

            existing = _find_dup(db, model, rec, file_type)
            if existing and duplicate_handling == 'skip':
                duplicate += 1; continue

            clean = _sanitize(rec, allowed)
            result_code = _save_row(db, model, clean, existing, duplicate_handling)

            if result_code == 'dup_skip':
                duplicate += 1; continue
            elif result_code == 'dup_overwrite':
                success += 1; duplicate += 1
            else:
                success += 1

            if model == models.LicenseHolder:
                if rec.get('category') == '택배': delivery += 1
                else: individual += 1

        except Exception as ex:
            err_cnt += 1
            errors.append({
                'row': i+2, 'label': label,
                'vehicle_number': rec.get('vehicle_number',''),
                'name': rec.get('name',''),
                'error': str(ex).split('\n')[0][:300],
            })

    # ── 3. 최종 commit + 이력 업데이트 ──
    try:
        # 이력의 실제 결과값 업데이트
        if hist and upload_id:
            hist.success_count  = success
            hist.duplicate_count = duplicate
            hist.error_count    = err_cnt
            hist.error_details  = [{'row':e['row'],'error':e['error']} for e in errors[:50]]
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

    return {
        'total': len(records), 'success': success,
        'individual_count': individual, 'delivery_count': delivery,
        'duplicates': duplicate, 'errors': err_cnt,
        'error_details': errors[:50],
        'unmapped_columns': unmapped, 'sheet_logs': sheet_logs,
        'file_type': file_type, 'upload_id': upload_id,
    }


async def _upload_allocation(file, db, current_user):
    content = await file.read()
    records, _, unmapped = excel_to_records(content, '부과대수')
    FIELDS = ['association_join','transfer_in','other_region','closed','withdrawn',
              'delivery_new','mgmt_fee_closed','over_70','base_count','total_count','delivery_mgmt']
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
            if _USE_SP: db.execute(text(f"SAVEPOINT {sp}"))
            try:
                if row:
                    for f,v in vals.items(): setattr(row, f, v)
                else:
                    row = models.AllocationCount(year=y, month=m, **vals)
                    db.add(row)
                db.flush()
                if _USE_SP: db.execute(text(f"RELEASE SAVEPOINT {sp}"))
                success += 1
            except Exception as ex:
                if _USE_SP:
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
