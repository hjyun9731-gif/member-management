from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.sql import func
from datetime import datetime
from app.database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="staff")
    full_name = Column(String(100))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class LicenseHolder(Base):
    """개인회원 / 택배회원 통합 테이블"""
    __tablename__ = "license_holders"
    id = Column(Integer, primary_key=True, index=True)
    management_number = Column(String(50), index=True)   # 신YY-N / 양YY-N
    registration_type = Column(String(20))               # 신규 / 양도양수 / 엑셀업로드
    status = Column(String(20), default="active")        # active / closed
    region = Column(String(50), index=True)
    vehicle_number = Column(String(50), index=True)
    name = Column(String(100), index=True)
    category = Column(String(20), index=True)            # 개인 / 택배 (차량번호 "배"로 자동판단)
    company_name = Column(String(200))
    address = Column(Text)
    phone = Column(String(50))
    mobile = Column(String(50))
    membership_status = Column(String(20))               # 가입 / 미가입
    membership_date = Column(String(50))
    approval_date = Column(String(50))
    certificate_issue_date = Column(String(50))
    certificate_number = Column(String(100))
    permit_number = Column(String(100))
    driver_license_number = Column(String(100))
    vehicle_type = Column(String(50))
    fuel_type = Column(String(30))
    business_number = Column(String(50))
    affiliated_company = Column(String(200))
    resident_number = Column(String(30))
    memo = Column(Text)
    # ── 택배 전용 추가 필드 ──────────────────────────
    reapproval_date = Column(String(50))             # 재허가 일자
    official_address = Column(Text)                  # 공문주소
    # ── 개인 전용 추가 필드 ──────────────────────────
    agent_name = Column(String(100))                 # 대리인 성명
    agent_resident_number = Column(String(30))       # 대리인 주민등록번호
    agent_mobile = Column(String(50))                # 대리인 핸드폰번호
    agent_address = Column(Text)                      # 대리인 주소
    structure_change = Column(Text)                  # 구조변경 내용 (예: 윙바디 변경)
    pinned = Column(Boolean, default=False)          # 목록 상단 고정 표시용 (비고와 무관한 별도 토글)
    upload_id = Column(Integer, nullable=True)        # 업로드 이력 ID (개별 삭제용)
    candidate_id = Column(Integer, nullable=True)        # FK → candidates
    transfer_ledger_id = Column(Integer, nullable=True)  # FK → transfer_ledger
    closure_id = Column(Integer, nullable=True)          # FK → closures (폐업 시)
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class Candidate(Base):
    """예정자 (신규등록 대기자)"""
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True, index=True)
    region = Column(String(50), index=True)
    vehicle_number = Column(String(50), index=True)
    name = Column(String(100), index=True)
    resident_number = Column(String(30))
    address = Column(Text)
    phone = Column(String(50))
    mobile = Column(String(50))
    certificate_issue_date = Column(String(50))
    certificate_number = Column(String(100))
    driver_license_number = Column(String(100))
    vehicle_type = Column(String(50))
    fuel_type = Column(String(30))
    business_number = Column(String(50))
    affiliated_company = Column(String(200))
    membership_date = Column(String(50))          # 가입일자 (있으면 가입, 없으면 미가입)
    memo = Column(Text)
    is_registered = Column(Boolean, default=False)
    member_id = Column(Integer, nullable=True)
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class TransferLedger(Base):
    """양도양수대장 (인허가/변경)"""
    __tablename__ = "transfer_ledger"
    id = Column(Integer, primary_key=True, index=True)
    seq_number = Column(String(50), index=True)          # 번호
    receipt_date = Column(String(50))                    # 접수일자
    region = Column(String(50), index=True)              # 지역별
    vehicle_number = Column(String(50), index=True)      # 차량번호
    transferor = Column(String(100), index=True)         # 양도자
    transferee = Column(String(100), index=True)         # 양수자
    resident_number = Column(String(30))
    address = Column(Text)
    phone = Column(String(50))
    mobile = Column(String(50))
    approval_date = Column(String(50))                   # 인가일자
    membership_date = Column(String(50))                 # 가입일자
    certificate_issue_date = Column(String(50))          # 자격증명발급일자
    certificate_number = Column(String(100))             # 자격증명발급번호
    process_date = Column(String(50))                    # 처리일자 (양도양수 기준날짜)
    ledger_update = Column(String(100))                  # 장부정리
    driver_license_number = Column(String(100))          # 운전면허번호
    computer_report = Column(String(100))                # 전산보고
    memo = Column(Text)                                  # 비고
    vehicle_type = Column(String(100))                  # 차종
    fuel_type = Column(String(30))                      # 유종
    structure_change = Column(Text)                     # 구조변경
    affiliated_company = Column(String(200))            # 소속업체
    management_number = Column(String(50))               # 양YY-N (회원등록 시 부여)
    member_id = Column(Integer, nullable=True)           # 회원등록 완료 시 연결 (양수자, 하위호환용)
    transferor_member_id = Column(Integer, nullable=True) # 양도자 회원 ID (license_holders.id)
    transferee_member_id = Column(Integer, nullable=True) # 양수자 회원 ID (license_holders.id)
    upload_id = Column(Integer, nullable=True)     # 업로드 이력 ID
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class Closure(Base):
    """폐지현황 (폐업/양도/이관)"""
    __tablename__ = "closures"
    id = Column(Integer, primary_key=True, index=True)
    management_number = Column(String(50), index=True)   # 폐-80 / 양-28 / 이-4
    closure_type = Column(String(20), index=True)        # 폐업 / 양도 / 이관
    data_type = Column(String(20), default="신규자료")   # 신규자료 / 이전자료
    region = Column(String(50), index=True)
    vehicle_number = Column(String(50), index=True)
    name = Column(String(100), index=True)
    company_name = Column(String(200))
    closure_date = Column(String(50))
    receipt_date = Column(String(50))                   # 접수일자 (공문 접수)
    approval_date = Column(String(50))
    reason = Column(Text)
    memo = Column(Text)
    vehicle_type = Column(String(100))                  # 차종
    fuel_type    = Column(String(30))                   # 유종
    structure_change = Column(Text)                     # 구조변경
    phone = Column(String(50))                          # 전화번호
    mobile = Column(String(50))                         # 핸드폰
    address = Column(Text)                              # 주소
    official_address = Column(Text)                     # 공문주소
    membership_status = Column(String(20))              # 가입여부
    membership_date = Column(String(50))                # 가입일자
    certificate_issue_date = Column(String(50))         # 자격증명발급일자
    certificate_number = Column(String(100))            # 자격증명발급번호
    driver_license_number = Column(String(100))         # 운전면허번호
    resident_number = Column(String(30))                # 주민등록번호
    affiliated_company = Column(String(200))            # 소속업체
    agent_name = Column(String(100))                    # 대리인
    agent_mobile = Column(String(50))                   # 대리인 핸드폰
    transferee = Column(String(100))                  # 양수인 (양도 시)
    transfer_region = Column(String(50))              # 이관지역 / 양도지역
    transferee_member_id = Column(Integer, nullable=True)  # 양수인 회원 ID (도내 양도양수 연결)
    transfer_ledger_id = Column(Integer, nullable=True)     # 연결된 양도양수대장 기록 ID
    member_id = Column(Integer, nullable=True)
    upload_id = Column(Integer, nullable=True)     # 업로드 이력 ID
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class ChangeHistory(Base):
    """변경이력대장"""
    __tablename__ = "change_history"
    id = Column(Integer, primary_key=True, index=True)
    change_type = Column(String(50), index=True)         # 주소지변경/상호변경/...
    region = Column(String(50), index=True)
    vehicle_number = Column(String(50), index=True)
    name = Column(String(100), index=True)
    before_value = Column(Text)
    after_value = Column(Text)
    change_date = Column(String(50))
    receipt_date = Column(String(50))                    # 접수/신고일자 (change_date 없을 때 표시용)
    memo = Column(Text)
    member_id = Column(Integer, nullable=True)
    upload_id = Column(Integer, nullable=True)     # 업로드 이력 ID
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class AllocationCount(Base):
    """부과대수 (보고/집계용)"""
    __tablename__ = "allocation_counts"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, index=True)
    month = Column(Integer, index=True)
    association_join = Column(Integer, default=0)        # 협회가입
    transfer_in = Column(Integer, default=0)             # 양도
    other_region = Column(Integer, default=0)            # 타도
    closed = Column(Integer, default=0)                  # 폐지
    withdrawn = Column(Integer, default=0)               # 탈퇴
    delivery_new = Column(Integer, default=0)            # 택배신규
    mgmt_fee_closed = Column(Integer, default=0)         # 관리비폐지
    over_70 = Column(Integer, default=0)                 # 70세
    base_count = Column(Integer, default=0)              # 협회기본대수
    total_count = Column(Integer, default=0)             # 총부과대수
    delivery_mgmt = Column(Integer, default=0)           # n월 택배관리
    memo = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class UploadHistory(Base):
    __tablename__ = "upload_histories"
    id = Column(Integer, primary_key=True, index=True)
    file_type = Column(String(100))
    filename = Column(String(255))
    total_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    duplicate_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    uploaded_by = Column(String(50))
    error_details = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MonthlyReportEntry(Base):
    __tablename__ = "monthly_report_entries"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, index=True)
    month = Column(Integer, index=True)
    document_number = Column(String(100))
    execution_date = Column(String(50))
    memo = Column(Text)
    custom_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class DeadlineTask(Base):
    __tablename__ = "deadline_tasks"
    id               = Column(Integer, primary_key=True, index=True)
    member_id        = Column(Integer, nullable=True)
    license_holder_id= Column(Integer, nullable=True)
    vehicle_number   = Column(String(50))
    name             = Column(String(100))
    region           = Column(String(50))
    mobile           = Column(String(50))
    task_type        = Column(String(50))  # 휴업만료/대폐차기한 등
    title            = Column(String(200))
    content          = Column(Text)
    start_date       = Column(String(20))
    due_date         = Column(String(20), index=True)
    reminder_days    = Column(String(20), default="7,3,0")
    status           = Column(String(20), default="예정")  # 예정/진행중/완료/기한초과/연장
    completed_at     = Column(String(20))
    extended_from    = Column(String(20))
    extended_to      = Column(String(20))
    extension_reason = Column(Text)
    memo             = Column(Text)
    manager          = Column(String(100))
    source           = Column(String(100))
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at       = Column(DateTime, nullable=True)


class GlosignDocument(Base):
    __tablename__ = "glosign_documents"
    id                 = Column(Integer, primary_key=True, index=True)
    member_id          = Column(Integer, nullable=True)
    license_holder_id  = Column(Integer, nullable=True)
    vehicle_number     = Column(String(50))
    name               = Column(String(100))
    mobile             = Column(String(50))
    region             = Column(String(50))
    document_title     = Column(String(200))
    glosign_document_id= Column(String(100), index=True)
    glosign_request_id = Column(String(100))
    status             = Column(String(30), default="요청대기")
    requested_at       = Column(String(20))
    due_date           = Column(String(20))
    completed_at       = Column(String(20))
    document_url       = Column(Text)
    completed_file_url = Column(Text)
    memo               = Column(Text)
    contract_method    = Column(String(30), default='비대면')
    raw_response       = Column(JSON)
    created_at         = Column(DateTime, default=datetime.utcnow)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at         = Column(DateTime, nullable=True)


class MemberEditLog(Base):
    __tablename__ = "member_edit_logs"
    id           = Column(Integer, primary_key=True, index=True)
    member_id    = Column(Integer, index=True)
    vehicle_number = Column(String(50))
    name         = Column(String(100))
    field_name   = Column(String(100))   # 수정된 필드명
    old_value    = Column(Text)
    new_value    = Column(Text)
    edit_reason  = Column(String(200))
    record_to_change_history = Column(Boolean, default=False)
    change_type  = Column(String(50))    # 변경등록대장에 기록된 경우 유형
    created_by   = Column(String(100))
    created_at   = Column(DateTime, default=datetime.utcnow)
