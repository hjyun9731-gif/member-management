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
    account_manual_override = Column(Integer, nullable=False, default=0)  # 0/1 (PATCH /account 로 수동 지정된 경우 1 — 자동 재판정 대상에서 제외)
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
