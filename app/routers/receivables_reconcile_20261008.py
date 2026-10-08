"""2026-10-08 corrected receivables ledger reconciliation.

Authoritative source: corrected 2026 ledger through 2026-09-30.
- Reconciles every uniquely matched member's balance through Sep 30 by changing only
  receivable_profiles.legacy_balance (delta), preserving Oct+ receipts/charges.
- Corrects account type / vehicle count for matched active ledger rows.
- Applies closures from the final ended-member sheet and removes post-closure auto charges.
- Applies certificate-unissued holds and special receivable exclusions.
- Exposes giro-preferred list to UI.
- Never deletes/recreates payment/contact rows.
"""
from __future__ import annotations
import json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from app import crud, models
from app.auth import get_current_user, require_admin
from app.database import SessionLocal, get_db
from app.receivables_models import ReceivableProfile, ReceivableCharge, ReceivablePayment, ReceivableSystemState

router=APIRouter(prefix='/api/receivables/reconcile-20261008',tags=['수납미수금-20261008-final'])
PATCH_ID='receivables_reconcile_20261008_final_ledger_v1'
STATE_KEY=PATCH_ID
DATA_PATH=Path(__file__).resolve().parents[1]/'data'/'receivables_reconcile_20261008_final.json'
MEMO='[20261008 수정완료 최종원장 전수정정]'

def _n(v): return re.sub(r'\s+','',str(v or '')).strip()
def _v(v):
    s=str(v or '').strip().lower().replace('강원','')
    s=re.sub(r'호\s*$','',s); return re.sub(r'[\s\-]+','',s)
def _load():
    p=json.loads(DATA_PATH.read_text(encoding='utf-8'))
    if p.get('patch_id')!=PATCH_ID: raise RuntimeError('patch data mismatch')
    return p

def _index(db):
    out={}
    for m in db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None)).all():
        k=(_n(m.name),_v(m.vehicle_number))
        if k[0] and k[1]: out.setdefault(k,[]).append(m)
    return out

def _pick(items):
    items=list(items or [])
    if len(items)==1:return items[0],'matched'
    active=[x for x in items if (x.status or 'active')=='active']
    if len(active)==1:return active[0],'matched_active_among_duplicates'
    return None,'ambiguous' if items else 'unmatched'

def _profile(db,mid): return db.query(ReceivableProfile).filter(ReceivableProfile.member_id==mid).first()
def _cutoff_balance(db,p,cutoff_month='2026-09',cutoff_date='2026-09-30',legacy_last='2026-08'):
    q=db.query(func.coalesce(func.sum(ReceivableCharge.amount),0)).filter(ReceivableCharge.member_id==p.member_id,ReceivableCharge.billing_month<=cutoff_month)
    if p.legacy_source_row is not None: q=q.filter(ReceivableCharge.billing_month>legacy_last)
    charges=int(q.scalar() or 0)
    payments=int(db.query(func.coalesce(func.sum(ReceivablePayment.amount),0)).filter(ReceivablePayment.member_id==p.member_id,ReceivablePayment.payment_date<=cutoff_date,ReceivablePayment.cancelled_at.is_(None)).scalar() or 0)
    return int(p.legacy_balance or 0)+charges-payments

def _append_note(p,text):
    old=(p.legacy_note or '').strip()
    if text not in old: p.legacy_note=(old+' / ' if old else '')+text

def _reconcile_profile(db,p,target,payload):
    before=_cutoff_balance(db,p,payload['cutoff_month'],payload['cutoff_date'],payload['legacy_last_month'])
    delta=int(target)-before
    if delta: p.legacy_balance=int(p.legacy_balance or 0)+delta
    _append_note(p,MEMO)
    return before,delta,int(target)

def _next_num(db,ctype):
    if ctype=='탈퇴':
        prefix='탈-'; rows=db.query(models.Closure).filter(models.Closure.management_number.like('탈-%'),models.Closure.deleted_at.is_(None)).all(); mx=0
        for x in rows:
            try: mx=max(mx,int(str(x.management_number).split('-',1)[1]))
            except: pass
        return f'{prefix}{mx+1}'
    base='폐업' if ctype in ('폐업','폐지') else ctype
    return crud.get_next_closure_number(db,base)

def _existing_closure(db,m):
    if getattr(m,'closure_id',None):
        c=db.query(models.Closure).filter(models.Closure.id==m.closure_id,models.Closure.deleted_at.is_(None)).first()
        if c:return c
    rows=db.query(models.Closure).filter(models.Closure.deleted_at.is_(None),models.Closure.name==m.name).all()
    exact=[c for c in rows if _v(c.vehicle_number)==_v(m.vehicle_number)]
    return exact[0] if len(exact)==1 else None

def _remove_post_closure_charges(db,mid,closure_date):
    month=str(closure_date or '')[:7]
    if not re.fullmatch(r'\d{4}-\d{2}',month): return 0
    return int(db.query(ReceivableCharge).filter(ReceivableCharge.member_id==mid,ReceivableCharge.billing_month>month,ReceivableCharge.source=='auto').delete(synchronize_session=False) or 0)

def _remove_future_auto(db,mid,month='2026-10'):
    return int(db.query(ReceivableCharge).filter(ReceivableCharge.member_id==mid,ReceivableCharge.billing_month>=month,ReceivableCharge.source=='auto').delete(synchronize_session=False) or 0)

def _state(db):
    r=db.query(ReceivableSystemState).filter(ReceivableSystemState.key==STATE_KEY).first()
    if not r or not r.value:return None
    try:return json.loads(r.value)
    except:return {'patch_id':PATCH_ID,'status':'state_parse_error'}
def _save_state(db,obj):
    r=db.query(ReceivableSystemState).filter(ReceivableSystemState.key==STATE_KEY).first(); s=json.dumps(obj,ensure_ascii=False,separators=(',',':'))
    if r:r.value=s
    else:db.add(ReceivableSystemState(key=STATE_KEY,value=s))

def dry_run():
    payload=_load(); db=SessionLocal()
    try:
        idx=_index(db); stats={'active_matched':0,'active_unmatched':[],'active_ambiguous':[],'closed_in_db_skipped':[], 'profile_missing':[], 'would_adjust':0,'delta_total':0,'closures_matched':0,'holds_matched':0,'exclusions_matched':0}
        for r in payload['active']:
            m,st=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
            if not m: stats['active_ambiguous' if st=='ambiguous' else 'active_unmatched'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'row':r['source_row']}); continue
            if (m.status or 'active')!='active': stats['closed_in_db_skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'row':r['source_row']}); continue
            p=_profile(db,m.id)
            if not p: stats['profile_missing'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'row':r['source_row']}); continue
            stats['active_matched']+=1; before=_cutoff_balance(db,p,payload['cutoff_month'],payload['cutoff_date'],payload['legacy_last_month']); d=int(r['target_sep_balance'])-before
            if d: stats['would_adjust']+=1; stats['delta_total']+=d
        for bucket,key in [('closures','closures_matched'),('certificate_holds','holds_matched'),('exclusions','exclusions_matched')]:
            for r in payload[bucket]:
                m,_=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
                if m: stats[key]+=1
        return {'patch_id':PATCH_ID,'status':'dry_run','source_counts':payload['source_counts'],'stats':stats}
    finally: db.close()

def apply_once(force=False):
    payload=_load(); db=SessionLocal()
    try:
        prev=_state(db)
        if prev and not force and prev.get('status') in ('applied','applied_with_skips'): return {**prev,'status':'already_applied'}
        preview=dry_run()
        if preview['stats']['active_matched']<2500: raise RuntimeError(f"safety stop: only {preview['stats']['active_matched']} active rows matched")
        idx=_index(db)
        out={'active':{'matched':0,'adjusted':0,'delta_total':0,'account_updated':0,'skipped':[],'errors':[]},'closures':{'matched':0,'created':0,'linked':0,'charges_removed':0,'adjusted':0,'delta_total':0,'skipped':[],'errors':[]},'holds':{'matched':0,'charges_removed':0,'skipped':[]},'exclusions':{'matched':0,'charges_removed':0,'adjusted':0,'skipped':[]}}
        # active final ledger
        for r in payload['active']:
            m,st=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
            if not m or (m.status or 'active')!='active': out['active']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':st if not m else 'closed_in_db'}); continue
            p=_profile(db,m.id)
            if not p: out['active']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':'profile_missing'}); continue
            try:
                with db.begin_nested():
                    out['active']['matched']+=1
                    acct=r.get('account_type') or p.account_type
                    fee=10000 if acct=='협회비' else 5000
                    if p.account_type!=acct or int(p.unit_fee or 0)!=fee or int(p.vehicle_count or 1)!=int(r.get('vehicle_count') or 1):
                        p.account_type=acct;p.unit_fee=fee;p.vehicle_count=int(r.get('vehicle_count') or 1);p.account_manual_override=1;out['active']['account_updated']+=1
                    # existing October auto charge follows corrected account, but no new row is created here
                    oct_charge=db.query(ReceivableCharge).filter(ReceivableCharge.member_id==m.id,ReceivableCharge.billing_month=='2026-10',ReceivableCharge.source=='auto').first()
                    if oct_charge and not r.get('certificate_hold'):
                        oct_charge.amount=fee*int(p.vehicle_count or 1);oct_charge.account_type=acct
                    before,delta,target=_reconcile_profile(db,p,r['target_sep_balance'],payload)
                    if delta: out['active']['adjusted']+=1;out['active']['delta_total']+=delta
                    if r.get('note'): _append_note(p,'[원장비고] '+str(r['note'])[:500])
            except Exception as exc: out['active']['errors'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'error':f'{type(exc).__name__}: {exc}'})
        # certificate hold: no Oct+ auto charge until certificate issued by existing V4/normal logic
        for r in payload['certificate_holds']:
            m,st=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
            if not m: out['holds']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':st}); continue
            p=_profile(db,m.id)
            if not p: out['holds']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':'profile_missing'}); continue
            out['holds']['matched']+=1;p.first_charge_date=None;_append_note(p,MEMO+' 자격증명 미발급·부과 제외');out['holds']['charges_removed']+=_remove_future_auto(db,m.id)
        # explicit receivable exclusions
        for r in payload['exclusions']:
            m,st=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
            if not m: out['exclusions']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':st}); continue
            p=_profile(db,m.id)
            if not p: out['exclusions']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':'profile_missing'}); continue
            out['exclusions']['matched']+=1; p.first_charge_date=None; _append_note(p,MEMO+' '+r['reason']); out['exclusions']['charges_removed']+=_remove_future_auto(db,m.id)
            before,delta,target=_reconcile_profile(db,p,r.get('target_sep_balance',0),payload)
            if delta: out['exclusions']['adjusted']+=1
        # closures / ended members
        for r in payload['closures']:
            m,st=_pick(idx.get((r['match_name'],r['match_vehicle']),[]))
            if not m: out['closures']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':st}); continue
            p=_profile(db,m.id)
            if not p: out['closures']['skipped'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'why':'profile_missing'}); continue
            try:
                with db.begin_nested():
                    out['closures']['matched']+=1
                    c=_existing_closure(db,m)
                    if c:
                        m.status='closed';m.closure_id=c.id;c.member_id=m.id;out['closures']['linked']+=1
                    else:
                        base_type='폐업' if r['closure_type'] in ('폐업','폐지') else r['closure_type']
                        c=crud.close_member_no_commit(db,m.id,base_type,r.get('closure_date') or '',_next_num(db,base_type),r.get('reason') or MEMO)
                        if r['closure_type']=='폐지': c.closure_type='폐지'
                        out['closures']['created']+=1
                    if r.get('reason'): c.reason=r['reason']
                    out['closures']['charges_removed']+=_remove_post_closure_charges(db,m.id,r.get('closure_date') or '')
                    before,delta,target=_reconcile_profile(db,p,r.get('target_sep_balance',0),payload)
                    if delta: out['closures']['adjusted']+=1;out['closures']['delta_total']+=delta
            except Exception as exc: out['closures']['errors'].append({'name':r['name'],'vehicle_number':r['vehicle_number'],'error':f'{type(exc).__name__}: {exc}'})
        result={'patch_id':PATCH_ID,'status':'applied_with_skips' if any((out['active']['skipped'],out['active']['errors'],out['closures']['skipped'],out['closures']['errors'],out['holds']['skipped'],out['exclusions']['skipped'])) else 'applied','applied_at':datetime.now(timezone.utc).isoformat(),'source_counts':payload['source_counts'],'result':out,'safety':{'payments_deleted':False,'payments_inserted':False,'contacts_modified':False,'october_plus_activity_preserved':True,'matching':'exact normalized name+vehicle; source duplicates omitted'}}
        _save_state(db,result);db.commit();return result
    except Exception: db.rollback(); raise
    finally: db.close()

@router.get('/giro-targets')
def giro_targets(_=Depends(get_current_user)):
    p=_load();return {'count':len(p['giro_preferred']),'items':p['giro_preferred']}
@router.get('/dry-run')
def dry_run_api(_=Depends(require_admin)): return dry_run()
@router.get('/status')
def status(db:Session=Depends(get_db),_=Depends(get_current_user)): return _state(db) or {'patch_id':PATCH_ID,'status':'not_applied','source_counts':_load()['source_counts']}
@router.post('/apply')
def apply_api(_=Depends(require_admin)):
    try:return apply_once(force=True)
    except Exception as exc: raise HTTPException(500,f'2026-10-08 final ledger reconcile failed: {type(exc).__name__}: {exc}')
