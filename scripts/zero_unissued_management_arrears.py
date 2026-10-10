"""자격증명 미발급 회원의 관리비 미수금 일괄 0원 정정.

기본 실행은 *조회 전용* 미리보기. 운영에 쓰려면 백업 확보 후
  python -m scripts.zero_unissued_management_arrears --apply --confirm ZERO_UNISSUED_MANAGEMENT
관리비 계정만 대상으로 하며 실제 수납/기타 수수료/기존 기록은 변경하지 않는다.
잔액 정정은 실제 수납이 아닌 method='잔액수정' 감사기록으로 남긴다.
"""
import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func
from app import models
from app.database import SessionLocal
from app.receivables_models import ReceivableCharge, ReceivablePayment, ReceivableProfile

KST = ZoneInfo('Asia/Seoul')
CUTOFF_MONTH = '2026-09'
CUTOFF_DATE = '2026-09-30'
TAG = '[미발급 관리비 미수금 일괄정정 20261010]'


def is_unissued(member):
    return not str(getattr(member, 'certificate_issue_date', None) or '').strip() and not str(getattr(member, 'certificate_number', None) or '').strip()


def calculate_workspace_balance(session, profile):
    """엑셀형 미수금 원장의 계산과 일치: 9월말 잔액 + 10월 이후 부과 - 10월 이후 수납."""
    charges = session.query(func.coalesce(func.sum(ReceivableCharge.amount), 0)).filter(
        ReceivableCharge.member_id == profile.member_id,
        ReceivableCharge.billing_month > CUTOFF_MONTH,
    ).scalar() or 0
    payments = session.query(func.coalesce(func.sum(ReceivablePayment.amount), 0)).filter(
        ReceivablePayment.member_id == profile.member_id,
        ReceivablePayment.cancelled_at.is_(None),
        ReceivablePayment.payment_date > CUTOFF_DATE,
    ).scalar() or 0
    return int(profile.legacy_balance or 0) + int(charges) - int(payments)


def select_candidates(session):
    rows = session.query(models.LicenseHolder, ReceivableProfile).join(
        ReceivableProfile, ReceivableProfile.member_id == models.LicenseHolder.id
    ).filter(
        models.LicenseHolder.deleted_at.is_(None),
        ReceivableProfile.account_type == '관리비',
    ).order_by(models.LicenseHolder.id).all()
    return [(member, profile) for member, profile in rows if is_unissued(member)]


def execute(session, apply=False):
    """양수 관리비 미수금만 0원 정정; 음수 선납금은 그대로 둔다."""
    candidates = select_candidates(session)
    result = {'eligible_members': len(candidates), 'affected_members': 0,
              'arrears_cleared': 0, 'unchanged_or_credit': 0, 'rows': []}
    for member, profile in candidates:
        before = calculate_workspace_balance(session, profile)
        item = {'member_id': member.id, 'name': member.name,
                'vehicle_number': member.vehicle_number, 'account_type': profile.account_type,
                'before': before, 'after': 0 if before > 0 else before}
        result['rows'].append(item)
        if before <= 0:
            result['unchanged_or_credit'] += 1
            continue
        result['affected_members'] += 1
        result['arrears_cleared'] += before
        if apply:
            session.add(ReceivablePayment(
                member_id=member.id,
                payment_date=datetime.now(KST).date().isoformat(),
                amount=before,
                method='잔액수정',
                memo=f'{TAG} 관리비 미수금 {before:,}원 → 0원 (발급번호·발급일 모두 없음; 실제 입금 아님)',
                created_by='bulk_unissued_management_correction',
            ))
            session.flush()
            after = calculate_workspace_balance(session, profile)
            if after != 0:
                raise RuntimeError(f'회원 {member.id}: 미수금 잔액 검증 실패 ({after})')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true', help='실제 잔액수정 기록 생성 (기본은 미리보기)')
    p.add_argument('--confirm', default='', help='실제 정정 시 ZERO_UNISSUED_MANAGEMENT 입력')
    a = p.parse_args()
    if a.apply and a.confirm != 'ZERO_UNISSUED_MANAGEMENT':
        p.error('정정 실행에는 --confirm ZERO_UNISSUED_MANAGEMENT 가 필요합니다.')
    session = SessionLocal()
    try:
        if a.apply:
            # DB에 대한 동시 실행을 막고, 미리보기와 적용 간 race-condition 완화.
            if session.bind.dialect.name == 'postgresql':
                from sqlalchemy import text
                session.execute(text('SELECT pg_advisory_xact_lock(20261010, 301)'))
        data = execute(session, apply=a.apply)
        print(json.dumps({'mode': 'APPLIED' if a.apply else 'PREVIEW', **data}, ensure_ascii=False, indent=2, default=str))
        if a.apply:
            session.commit()
        else:
            session.rollback()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == '__main__':
    main()
