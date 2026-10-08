"""2026-10-08 monthly-ledger V2 reconciliation.

Fixes the V1 defect where only legacy_balance was adjusted, which could make the
member list balance disagree with the monthly ledger.  The corrected workbook is
now the authoritative Jan-Sep 2026 ledger for uniquely matched members.
"""
from __future__ import annotations
import json,re
from datetime import datetime,timezone
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import crud,models
from app.auth import get_current_user,require_admin
from app.database import SessionLocal,get_db
from app.receivables_models import ReceivableProfile,ReceivableCharge,ReceivablePayment,ReceivableSystemState

router=APIRouter(prefix='/api/receivables/reconcile-20261008',tags=['수납미수금-20261008-monthly-v2'])
PATCH_ID='receivables_reconcile_20261008_monthly_v2'
STATE_KEY=PATCH_ID
DATA_PATH=Path(__file__).resolve().parents[1]/'data'/'receivables_reconcile_20261008_final.json'
MEMO='[20261008 월별장부 전수정정 V2]'

def _n(v):return re.sub(r'\s+','',str(v or '')).strip()
def _v(v):
 s=str(v or '').strip().lower().replace('강원','');s=re.sub(r'호\s*$','',s);return re.sub(r'[\s\-]+','',s)
def _load():
 p=json.loads(DATA_PATH.read_text(encoding='utf-8'))
 if p.get('patch_id')!=PATCH_ID:raise RuntimeError('patch data mismatch')
 return p
def _index(db):
 out={}
 for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all():
  k=(_n(m.name),_v(m.vehicle_number))
  if k[0] and k[1]:out.setdefault(k,[]).append(m)
 return out
def _pick(items):
 items=list(items or [])
 if len(items)==1:return items[0],'matched'
 active=[x for x in items if (x.status or 'active')=='active']
 if len(active)==1:return active[0],'matched_active_among_duplicates'
 return None,'ambiguous' if items else 'unmatched'
def _profile(db,mid):return db.query(ReceivableProfile).filter(ReceivableProfile.member_id==mid).first()
def _append_note(p,t):
 old=(p.legacy_note or '').strip()
 if t not in old:p.legacy_note=(old+' / ' if old else '')+t

def _legacy_months(rec):
 out=[]
 for x in rec.get('months',[]):
  # Include historical and compatibility key names. Existing receivables API
  # implementations have used monthly_charge/payment/payment_date/arrears.
  out.append({
   'month':int(x['month']),
   'monthly_charge':int(x.get('monthly_charge') or 0),
   'charge':int(x.get('monthly_charge') or 0),
   'billed_total':x.get('billed_total'),
   'payment':x.get('payment'),
   'payment_date':x.get('payment_date'),
   'arrears':x.get('arrears'),
   'current_arrears':x.get('arrears'),
   'balance_adjustment':int(x.get('balance_adjustment') or 0),
  })
 return out

def _archive_through_sep(db,mid):
 # Final workbook now owns Jan-Sep. Keep rows for audit but neutralize them so
 # current balance is not double-counted. October+ operational activity remains untouched.
 charges=0;payments=0
 for c in db.query(ReceivableCharge).filter(ReceivableCharge.member_id==mid,ReceivableCharge.billing_month<='2026-09').all():
  if int(c.amount or 0)!=0:
   c.amount=0;charges+=1
  c.source='legacy_reconciled'
 now=datetime.now(timezone.utc)
 for p in db.query(ReceivablePayment).filter(ReceivablePayment.member_id==mid,ReceivablePayment.payment_date<='2026-09-30',ReceivablePayment.cancelled_at.is_(None)).all():
  p.cancelled_at=now;p.cancelled_by='20261008-monthly-v2'
  if MEMO not in (p.memo or ''):p.memo=((p.memo or '')+' / ' if p.memo else '')+MEMO+' 최종원장 baseline 편입'
  payments+=1
 return charges,payments

def _apply_baseline(db,m,p,rec):
 p.account_type=rec.get('account_type') or p.account_type
 p.unit_fee=10000 if p.account_type=='협회비' else 5000
 p.vehicle_count=int(rec.get('vehicle_count') or 1)
 p.account_manual_override=1
 p.legacy_months=_legacy_months(rec)
 p.legacy_balance=int(rec.get('target_sep_balance') or 0)
 _append_note(p,MEMO)
 c,pay=_archive_through_sep(db,m.id)
 # Existing October auto charge must follow corrected account/vehicle count.
 oc=db.query(ReceivableCharge).filter(ReceivableCharge.member_id==m.id,ReceivableCharge.billing_month=='2026-10').first()
 if oc and not rec.get('certificate_hold'):
  oc.amount=int(p.unit_fee or 0)*int(p.vehicle_count or 1);oc.account_type=p.account_type
 return c,pay

def _current_balance(db,p):
 charges=int(db.query(func.coalesce(func.sum(ReceivableCharge.amount),0)).filter(ReceivableCharge.member_id==p.member_id,ReceivableCharge.billing_month>'2026-09').scalar() or 0)
 pays=int(db.query(func.coalesce(func.sum(ReceivablePayment.amount),0)).filter(ReceivablePayment.member_id==p.member_id,ReceivablePayment.payment_date>'2026-09-30',ReceivablePayment.cancelled_at.is_(None)).scalar() or 0)
 return int(p.legacy_balance or 0)+charges-pays

def _next_num(db,ctype):
 if ctype=='탈퇴':
  rows=db.query(models.Closure).filter(models.Closure.management_number.like('탈-%'),models.Closure.deleted_at.is_(None)).all();mx=0
  for x in rows:
   try:mx=max(mx,int(str(x.management_number).split('-',1)[1]))
   except:pass
  return f'탈-{mx+1}'
 base='폐업' if ctype in ('폐업','폐지') else ctype
 return crud.get_next_closure_number(db,base)
def _existing_closure(db,m):
 if getattr(m,'closure_id',None):
  c=db.query(models.Closure).filter(models.Closure.id==m.closure_id,models.Closure.deleted_at.is_(None)).first()
  if c:return c
 rows=db.query(models.Closure).filter(models.Closure.deleted_at.is_(None),models.Closure.name==m.name).all();exact=[c for c in rows if _v(c.vehicle_number)==_v(m.vehicle_number)]
 return exact[0] if len(exact)==1 else None
def _remove_post_closure(db,mid,date):
 mon=str(date or '')[:7]
 if not re.fullmatch(r'\d{4}-\d{2}',mon):return 0
 return int(db.query(ReceivableCharge).filter(ReceivableCharge.member_id==mid,ReceivableCharge.billing_month>mon).delete(synchronize_session=False) or 0)
def _remove_future(db,mid):return int(db.query(ReceivableCharge).filter(ReceivableCharge.member_id==mid,ReceivableCharge.billing_month>='2026-10').delete(synchronize_session=False) or 0)
def _state(db):
 r=db.query(ReceivableSystemState).filter(ReceivableSystemState.key==STATE_KEY).first()
 if not r or not r.value:return None
 try:return json.loads(r.value)
 except:return {'patch_id':PATCH_ID,'status':'state_parse_error'}
def _save_state(db,obj):
 r=db.query(ReceivableSystemState).filter(ReceivableSystemState.key==STATE_KEY).first();s=json.dumps(obj,ensure_ascii=False,separators=(',',':'))
 if r:r.value=s
 else:db.add(ReceivableSystemState(key=STATE_KEY,value=s))

def _rec_for_member(payload,m):
 k=(_n(m.name),_v(m.vehicle_number))
 for r in payload['active']:
  if (r['match_name'],r['match_vehicle'])==k:return r
 for r in payload['closures']:
  if (r['match_name'],r['match_vehicle'])==k:return r
 return None

def _monthly_for(rec):
 rows=[]
 for x in rec.get('months',[]):
  rows.append({'month':int(x['month']),'legacy_monthly_charge':int(x.get('monthly_charge') or 0),'auto_charge':0,'legacy_payment':x.get('payment'),'legacy_payment_date':x.get('payment_date'),'additional_payment':0,'balance_adjustment':int(x.get('balance_adjustment') or 0),'current_arrears':x.get('arrears')})
 return rows

def dry_run():
 payload=_load();db=SessionLocal()
 try:
  idx=_index(db);st={'matched':0,'unmatched':[],'ambiguous':[],'profiles_missing':[],'pre_oct_charges_to_archive':0,'pre_oct_payments_to_archive':0}
  for r in payload['active']:
   m,why=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
   if not m:st['ambiguous' if why=='ambiguous' else 'unmatched'].append({'name':r['name'],'vehicle_number':r['vehicle_number']});continue
   p=_profile(db,m.id)
   if not p:st['profiles_missing'].append({'name':r['name'],'vehicle_number':r['vehicle_number']});continue
   st['matched']+=1
   st['pre_oct_charges_to_archive']+=db.query(ReceivableCharge).filter(ReceivableCharge.member_id==m.id,ReceivableCharge.billing_month<='2026-09',ReceivableCharge.amount!=0).count()
   st['pre_oct_payments_to_archive']+=db.query(ReceivablePayment).filter(ReceivablePayment.member_id==m.id,ReceivablePayment.payment_date<='2026-09-30',ReceivablePayment.cancelled_at.is_(None)).count()
  return {'patch_id':PATCH_ID,'status':'dry_run','source_counts':payload['source_counts'],'stats':st}
 finally:db.close()

def apply_once(force=False):
 payload=_load();db=SessionLocal()
 try:
  prev=_state(db)
  if prev and not force and prev.get('status') in ('applied','applied_with_skips'):return {**prev,'status':'already_applied'}
  preview=dry_run()
  if preview['stats']['matched']<2500:raise RuntimeError(f"safety stop: only {preview['stats']['matched']} active rows matched")
  idx=_index(db);out={'active':{'matched':0,'charges_archived':0,'payments_archived':0,'skipped':[],'errors':[]},'closures':{'matched':0,'created':0,'linked':0,'charges_archived':0,'payments_archived':0,'future_charges_removed':0,'skipped':[],'errors':[]},'holds':{'matched':0,'future_charges_removed':0},'exclusions':{'matched':0,'future_charges_removed':0}}
  for r in payload['active']:
   m,why=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
   if not m or (m.status or 'active')!='active':out['active']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':why if not m else 'closed_in_db'});continue
   p=_profile(db,m.id)
   if not p:out['active']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':'profile_missing'});continue
   try:
    with db.begin_nested():
     c,pay=_apply_baseline(db,m,p,r);out['active']['matched']+=1;out['active']['charges_archived']+=c;out['active']['payments_archived']+=pay
     if r.get('note'):_append_note(p,'[원장비고] '+str(r['note'])[:500])
   except Exception as exc:out['active']['errors'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'error':f'{type(exc).__name__}: {exc}'})
  for r in payload['certificate_holds']:
   m,_=_pick(idx.get((r['match_name'],r['match_vehicle']),[]));p=_profile(db,m.id) if m else None
   if m and p:p.first_charge_date=None;_append_note(p,MEMO+' 자격증명 미발급·부과 제외');out['holds']['matched']+=1;out['holds']['future_charges_removed']+=_remove_future(db,m.id)
  for r in payload['exclusions']:
   m,_=_pick(idx.get((r['match_name'],r['match_vehicle']),[]));p=_profile(db,m.id) if m else None
   if m and p:p.first_charge_date=None;p.legacy_balance=0;_append_note(p,MEMO+' '+r['reason']);out['exclusions']['matched']+=1;out['exclusions']['future_charges_removed']+=_remove_future(db,m.id)
  for r in payload['closures']:
   m,why=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
   if not m:out['closures']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':why});continue
   p=_profile(db,m.id)
   if not p:out['closures']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':'profile_missing'});continue
   try:
    with db.begin_nested():
     cnum,pay=_apply_baseline(db,m,p,r);out['closures']['charges_archived']+=cnum;out['closures']['payments_archived']+=pay;out['closures']['matched']+=1
     c=_existing_closure(db,m)
     if c:m.status='closed';m.closure_id=c.id;c.member_id=m.id;out['closures']['linked']+=1
     else:
      bt='폐업' if r['closure_type'] in ('폐업','폐지') else r['closure_type'];c=crud.close_member_no_commit(db,m.id,bt,r.get('closure_date') or '',_next_num(db,bt),r.get('reason') or MEMO);out['closures']['created']+=1
      if r['closure_type']=='폐지':c.closure_type='폐지'
     if r.get('reason'):c.reason=r['reason']
     out['closures']['future_charges_removed']+=_remove_post_closure(db,m.id,r.get('closure_date') or '')
   except Exception as exc:out['closures']['errors'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'error':f'{type(exc).__name__}: {exc}'})
  result={'patch_id':PATCH_ID,'status':'applied_with_skips' if (out['active']['skipped'] or out['active']['errors'] or out['closures']['skipped'] or out['closures']['errors']) else 'applied','applied_at':datetime.now(timezone.utc).isoformat(),'source_counts':payload['source_counts'],'result':out,'safety':{'jan_sep_source':'corrected workbook','october_plus_preserved':True,'contacts_modified':False,'member_identity_modified':False,'pre_oct_payments_deleted':False,'pre_oct_payments_archived_by_cancel':True}}
  _save_state(db,result);db.commit();return result
 except Exception:db.rollback();raise
 finally:db.close()

@router.get('/giro-targets')
def giro_targets(_=Depends(get_current_user)):
 p=_load();return {'count':len(p['giro_preferred']),'items':p['giro_preferred']}
@router.get('/ledger/{member_id}')
def ledger(member_id:int,db:Session=Depends(get_db),_=Depends(get_current_user)):
 m=db.query(models.LicenseHolder).filter(models.LicenseHolder.id==member_id).first()
 if not m:raise HTTPException(404,'member not found')
 rec=_rec_for_member(_load(),m)
 if not rec:raise HTTPException(404,'authoritative ledger not found')
 p=_profile(db,m.id)
 return {'member_id':m.id,'name':m.name,'vehicle_number':m.vehicle_number,'monthly':_monthly_for(rec),'current_balance':_current_balance(db,p) if p else None,'target_sep_balance':rec.get('target_sep_balance')}
@router.get('/dry-run')
def dry_run_api(_=Depends(require_admin)):return dry_run()
@router.get('/status')
def status(db:Session=Depends(get_db),_=Depends(get_current_user)):return _state(db) or {'patch_id':PATCH_ID,'status':'not_applied','source_counts':_load()['source_counts']}
@router.post('/apply')
def apply_api(_=Depends(require_admin)):
 try:return apply_once(force=True)
 except Exception as exc:raise HTTPException(500,f'2026-10-08 monthly V2 reconcile failed: {type(exc).__name__}: {exc}')
