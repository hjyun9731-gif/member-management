from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, JSON
from sqlalchemy.sql import func
from datetime import datetime
from app.database import Base


class CertificateNumberLog(Base):
    """자격증명발급번호 발급 이력 - 발급/취소 상태 관리 및 관리자 화면용.
    번호 자체는 절대 재사용하지 않고(카운터는 그대로 증가), 이 로그의 status만 바꿔
    '취소'로 표시한다 (실제 삭제하지 않음).
    """
    __tablename__ = "certificate_number_logs"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, index=True)                 # 26
    number = Column(Integer)                            # 314
    certificate_number = Column(String(20), unique=True, index=True)  # "26-314"
    status = Column(String(20), default="issued")       # issued(발급만 됨) / used(사용중) / cancelled(취소)
    issued_at = Column(DateTime(timezone=True), server_default=func.now())
    issued_by = Column(String(50))
    linked_table = Column(String(50))                    # candidates / transfer_ledger / license_holders 등
    linked_id = Column(Integer)
    target_name = Column(String(100))
    vehicle_number = Column(String(50))
    memo = Column(String(300))
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class CertificateNumberCounter(Base):
    """자격증명발급번호(YY-N) 채번 카운터 - 연도별로 마지막 발급 번호를 영구 보관.
    레코드가 삭제/수정되어도 이 카운터는 그대로 유지되므로 번호가 재사용되지 않는다.
    """
    __tablename__ = "certificate_number_counters"
    id = Column(Integer, primary_key=True, index=True)
    year = Column(Integer, unique=True, index=True)   # 26 (2026년의 YY)
    last_number = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


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
    management_number = Column(String(50), index=True)  # 도내 양도양수로 예정자 등록 시 부여된 관리번호(양YY-N)
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


class ReportFieldDef(Base):
    """월례보고서 항목 정의 - 관리자가 화면에서 항목을 추가/수정할 수 있게 하는 메타데이터.
    field_type='auto'인 경우 auto_path로 /api/dashboard/monthly-report-auto 응답의 값을 참조.
    그 외 타입(number/amount/text/longtext/table)은 MonthlyReportEntry.custom_data에 사용자가 직접 입력한 값을 저장.
    """
    __tablename__ = "report_field_defs"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True)   # custom_data / auto_path에서 사용하는 고유 키
    label = Column(String(200))
    section = Column(String(100))                          # 예: "1. 허가 및 회원 현황"
    field_type = Column(String(20), default="text")        # number/amount/text/longtext/table/auto
    auto_path = Column(String(200))                        # field_type='auto'일 때, 자동집계 JSON 경로 (예: "member_stats.total")
    default_value = Column(String(200))                     # 사용자가 값을 한 번도 저장하지 않았을 때 표시할 기본값 (직접입력 항목용)
    display_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    is_printable = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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


class SmsTemplate(Base):
    """문자 템플릿 - 자주 쓰는 문구를 저장해두고 발송 시 불러와서 사용"""
    __tablename__ = "sms_templates"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), nullable=False)     # 템플릿 이름 (예: 협회비 안내)
    category    = Column(String(50))                       # 분류 (선택)
    subject     = Column(String(200))                       # LMS 제목 (선택)
    content     = Column(Text, nullable=False)               # 본문 (#{성명} 등 변수 포함 가능)
    service     = Column(String(10), default="SMS")          # 기본 SMS/LMS 구분 (내용 길이에 따라 발송 시 재판단)
    created_by  = Column(String(100))
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at  = Column(DateTime, nullable=True)


class SmsJob(Base):
    """문자 발송 작업 1건 - 조건(필터)/문자내용/발송방식을 기록.
    수신자 목록은 license_holders를 복제하지 않고, 발송 시점에 조회한 결과를
    SmsRecipient에 스냅샷으로 남긴다 (발송 당시 무엇을 보냈는지 추적하기 위함이며,
    license_holders 자체를 대체하는 명단이 아니다).
    """
    __tablename__ = "sms_jobs"
    id              = Column(Integer, primary_key=True, index=True)
    filters         = Column(JSON)                 # 대상자 추출에 사용한 조건 스냅샷
    template_id     = Column(Integer, nullable=True)
    service         = Column(String(10))           # SMS / LMS / MMS
    callback        = Column(String(50))            # 발신번호
    subject         = Column(String(200))
    main_text       = Column(Text)                  # 변수 포함 원본 문구
    send_mode       = Column(String(10), default="즉시")   # 즉시 / 예약
    scheduled_at    = Column(String(20), nullable=True)      # "YYYY-MM-DD HH:MM" (예약발송인 경우)
    is_test         = Column(Boolean, default=False)         # 테스트 발송 여부
    status          = Column(String(20), default="대기")     # 대기/예약대기/발송중/완료/실패/취소
    total_count     = Column(Integer, default=0)
    success_count   = Column(Integer, default=0)
    fail_count      = Column(Integer, default=0)
    job_no          = Column(String(50), nullable=True)      # 발송닷컴 Job_No
    cash_after      = Column(Integer, nullable=True)          # 발송 직후 선불잔액(Cash)
    error_message   = Column(Text, nullable=True)
    created_by      = Column(String(100))
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at         = Column(DateTime, nullable=True)
    cancelled_at    = Column(DateTime, nullable=True)


class SmsRecipient(Base):
    """문자 발송 작업의 수신자 1명 - license_holder_id로 원본 회원과 연결(스냅샷 값은 발송 시점 값)"""
    __tablename__ = "sms_recipients"
    id                = Column(Integer, primary_key=True, index=True)
    sms_job_id        = Column(Integer, index=True, nullable=False)
    license_holder_id = Column(Integer, index=True, nullable=True)   # license_holders.id (원본 연결)
    name              = Column(String(100))
    phone             = Column(String(50))
    region            = Column(String(50))
    msg_text          = Column(Text, nullable=True)      # 개인별 치환 완료된 최종 문구
    status            = Column(String(20), default="대기")  # 대기/성공/실패
    status_detail     = Column(String(200), nullable=True)
    done_date         = Column(String(30), nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)
