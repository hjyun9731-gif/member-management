from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, UniqueConstraint
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
