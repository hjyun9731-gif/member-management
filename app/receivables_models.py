from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, UniqueConstraint, Index
from sqlalchemy.sql import func
from app.database import Base


class ReceivableProfile(Base):
    __tablename__ = "receivable_profiles"
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, unique=True, index=True, nullable=False)
    account_type = Column(String(20), nullable=False, default="관리비")
    unit_fee = Column(Integer, nullable=False, default=5000)
    vehicle_count = Column(Integer, nullable=False, default=1)
    first_charge_date = Column(String(10), nullable=True)  # YYYY-MM-DD
    legacy_balance = Column(Integer, nullable=False, default=0)
    legacy_months = Column(JSON, nullable=False, default=list)
    legacy_source_row = Column(Integer, nullable=True)
    legacy_note = Column(Text, nullable=True)
    account_manual_override = Column(Integer, nullable=False, default=0)
    # 1=활성 수납/미수금 대상(기본값), 0=활성 목록/합계/자동부과에서 제외.
    # 데이터(legacy_balance, 수납/연락 이력 등)는 그대로 보존하고 화면 노출만 막는 용도.
    receivable_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ReceivableCharge(Base):
    __tablename__ = "receivable_charges"
    __table_args__ = (UniqueConstraint("member_id", "billing_month", name="uq_receivable_charge_member_month"),)
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, index=True, nullable=False)
    billing_month = Column(String(7), index=True, nullable=False)  # YYYY-MM
    amount = Column(Integer, nullable=False, default=0)
    account_type = Column(String(20), nullable=False)
    source = Column(String(20), nullable=False, default="auto")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReceivablePayment(Base):
    __tablename__ = "receivable_payments"
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, index=True, nullable=False)
    payment_date = Column(String(10), index=True, nullable=False)
    amount = Column(Integer, nullable=False)
    method = Column(String(30), nullable=True)
    memo = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by = Column(String(100), nullable=True)


class ReceivableContactLog(Base):
    __tablename__ = "receivable_contact_logs"
    id = Column(Integer, primary_key=True, index=True)
    member_id = Column(Integer, index=True, nullable=False)
    contact_date = Column(String(10), index=True, nullable=False)
    contact_method = Column(String(30), nullable=False, default="전화")
    status = Column(String(30), nullable=False, default="연락완료")
    memo = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ReceivableSystemState(Base):
    """수납/미수금 시스템의 영구 상태값."""
    __tablename__ = "receivable_system_state"
    key = Column(String(120), primary_key=True)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ReceivableImportBatch(Base):
    """통장/결제리스트 일괄수납 업로드 1회 단위."""
    __tablename__ = "receivable_import_batches"
    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(30), nullable=False, default="통장")
    source_name = Column(String(255), nullable=True)
    status = Column(String(30), nullable=False, default="preview")
    total_rows = Column(Integer, nullable=False, default=0)
    matched_rows = Column(Integer, nullable=False, default=0)
    review_rows = Column(Integer, nullable=False, default=0)
    duplicate_rows = Column(Integer, nullable=False, default=0)
    posted_rows = Column(Integer, nullable=False, default=0)
    total_amount = Column(Integer, nullable=False, default=0)
    posted_amount = Column(Integer, nullable=False, default=0)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    posted_at = Column(DateTime(timezone=True), nullable=True)


class ReceivableImportRow(Base):
    """업로드된 거래 1건. 원본을 보존하고 매칭/중복/반영 상태를 추적한다."""
    __tablename__ = "receivable_import_rows"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, index=True, nullable=False)
    source_row = Column(Integer, nullable=True)
    transaction_date = Column(String(10), index=True, nullable=True)
    payer_name = Column(String(200), index=True, nullable=True)
    amount = Column(Integer, nullable=False, default=0)
    vehicle_number = Column(String(80), nullable=True)
    management_number = Column(String(80), nullable=True)
    mobile = Column(String(80), nullable=True)
    external_id = Column(String(160), nullable=True)
    memo = Column(Text, nullable=True)
    fingerprint = Column(String(64), nullable=False, index=True)
    matched_member_id = Column(Integer, index=True, nullable=True)
    match_reason = Column(String(120), nullable=True)
    status = Column(String(30), nullable=False, default="review", index=True)  # matched/review/duplicate/posted/ignored
    payment_id = Column(Integer, nullable=True)
    raw_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ReceivableAdjustmentBatch(Base):
    """미수금 정정(실제 입금이 아님) 일괄작업 1건. 한 번 적용되면 status='applied', 되돌리면 'voided'(기록은 보존)."""
    __tablename__ = "receivable_adjustment_batches"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(40), unique=True, index=True, nullable=False)
    reason_code = Column(String(60), index=True, nullable=False)
    status = Column(String(20), nullable=False, default="applied")
    plan_digest = Column(String(64), nullable=True)
    member_count = Column(Integer, nullable=False, default=0)
    total_amount = Column(Integer, nullable=False, default=0)
    payments_total_before = Column(Integer, nullable=True)   # 실제 수납 합계(정정 전/후 동일해야 함)
    payments_total_after = Column(Integer, nullable=True)
    charges_total_before = Column(Integer, nullable=True)    # 부과 합계(정정 전/후 동일해야 함)
    charges_total_after = Column(Integer, nullable=True)
    report = Column(JSON, nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = Column(String(100), nullable=True)
    void_reason = Column(Text, nullable=True)


class ReceivableAdjustment(Base):
    """회원별 미수금 정정 내역. receivable_payments(실제 수납)와 완전히 분리되어 수납 통계/통장 입금액에 들어가지 않는다."""
    __tablename__ = "receivable_adjustments"
    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(40), index=True, nullable=False)
    member_id = Column(Integer, index=True, nullable=False)
    account_type = Column(String(20), nullable=False)
    balance_before = Column(Integer, nullable=False)
    adjustment_amount = Column(Integer, nullable=False)   # 양수 = 미수금 감소
    balance_after = Column(Integer, nullable=False)
    reason_code = Column(String(60), index=True, nullable=False)
    reason = Column(Text, nullable=True)
    effective_date = Column(String(10), nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    voided_at = Column(DateTime(timezone=True), nullable=True)
    voided_by = Column(String(100), nullable=True)
