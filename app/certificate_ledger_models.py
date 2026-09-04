"""자격증명 발급대장 전용 테이블.

기존 테이블은 수정하지 않고 새 테이블 두 개만 추가한다.
"""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class CertificateIssuanceLedger(Base):
    __tablename__ = "certificate_issuance_ledger"
    __table_args__ = (
        CheckConstraint(
            "status IN ('인가대기', '인가완료', '발급완료')",
            name="ck_certificate_issuance_status",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, unique=True, index=True, nullable=True)
    member_id = Column(Integer, index=True, nullable=True)

    region = Column(String(50), index=True)
    vehicle_number = Column(String(50), index=True)
    name = Column(String(100), index=True, nullable=False)
    qualification_number = Column(String(100))
    document_number = Column(String(100), unique=True, index=True, nullable=True)

    approval_date = Column(String(50))
    certificate_issue_date = Column(String(50), index=True)
    status = Column(String(20), default="인가대기", index=True, nullable=False)
    latest_operator = Column(String(100))
    created_by = Column(String(100))

    approved_at = Column(DateTime(timezone=True), nullable=True)
    issued_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class CertificateIssuanceHistory(Base):
    __tablename__ = "certificate_issuance_history"

    id = Column(Integer, primary_key=True, index=True)
    ledger_id = Column(
        Integer,
        ForeignKey("certificate_issuance_ledger.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_type = Column(String(30), index=True, nullable=False)
    from_status = Column(String(20))
    to_status = Column(String(20), nullable=False)
    operator = Column(String(100))
    memo = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
