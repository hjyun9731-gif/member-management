from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, List
from datetime import datetime


class UserBase(BaseModel):
    username: str
    role: str = "staff"
    full_name: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    username: str
    role: str
    full_name: Optional[str] = None


# ===== MEMBER COMMON FIELDS =====
class MemberBase(BaseModel):
    region: Optional[str] = None
    vehicle_number: Optional[str] = None
    name: Optional[str] = None
    company_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    category: Optional[str] = None
    membership_status: Optional[str] = None
    membership_date: Optional[str] = None
    approval_date: Optional[str] = None
    certificate_issue_date: Optional[str] = None
    certificate_number: Optional[str] = None
    permit_number: Optional[str] = None
    driver_license_number: Optional[str] = None
    vehicle_type: Optional[str] = None
    fuel_type: Optional[str] = None
    business_number: Optional[str] = None
    affiliated_company: Optional[str] = None
    resident_number: Optional[str] = None
    memo: Optional[str] = None
    management_number: Optional[str] = None


# ===== LICENSE HOLDER =====
class LicenseHolderCreate(MemberBase):
    pass

class LicenseHolderUpdate(MemberBase):
    pass

class LicenseHolderResponse(MemberBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    raw_data: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ===== CANDIDATE =====
class CandidateBase(BaseModel):
    region: Optional[str] = None
    vehicle_number: Optional[str] = None
    name: Optional[str] = None
    resident_number: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    approval_date: Optional[str] = None
    certificate_issue_date: Optional[str] = None
    certificate_number: Optional[str] = None
    driver_license_number: Optional[str] = None
    vehicle_type: Optional[str] = None
    fuel_type: Optional[str] = None
    business_number: Optional[str] = None
    affiliated_company: Optional[str] = None
    memo: Optional[str] = None

class CandidateCreate(CandidateBase):
    pass

class CandidateUpdate(CandidateBase):
    pass

class CandidateResponse(CandidateBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    raw_data: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ===== NEW REGISTRATION =====
class NewRegistrationCreate(MemberBase):
    pass

class NewRegistrationUpdate(MemberBase):
    pass

class NewRegistrationResponse(MemberBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    license_holder_synced: Optional[bool] = False
    raw_data: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ===== TRANSFER =====
class TransferBase(BaseModel):
    management_number: Optional[str] = None
    region: Optional[str] = None
    vehicle_number: Optional[str] = None
    transferor_name: Optional[str] = None
    transferor_phone: Optional[str] = None
    transferor_mobile: Optional[str] = None
    transferee_name: Optional[str] = None
    transferee_phone: Optional[str] = None
    transferee_mobile: Optional[str] = None
    address: Optional[str] = None
    transfer_date: Optional[str] = None
    approval_date: Optional[str] = None
    memo: Optional[str] = None

class TransferCreate(TransferBase):
    pass

class TransferUpdate(TransferBase):
    pass

class TransferResponse(TransferBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    raw_data: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ===== CLOSURE =====
class ClosureBase(BaseModel):
    management_number: Optional[str] = None
    closure_type: Optional[str] = None
    data_type: Optional[str] = "신규자료"
    region: Optional[str] = None
    vehicle_number: Optional[str] = None
    name: Optional[str] = None
    company_name: Optional[str] = None
    closure_date: Optional[str] = None
    approval_date: Optional[str] = None
    reason: Optional[str] = None
    memo: Optional[str] = None

class ClosureCreate(ClosureBase):
    pass

class ClosureUpdate(ClosureBase):
    pass

class ClosureResponse(ClosureBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    raw_data: Optional[Any] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ===== PAGINATED =====
class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    pages: int
    limit: int


# ===== MONTHLY REPORT =====
class MonthlyReportEntryCreate(BaseModel):
    year: int
    month: int
    document_number: Optional[str] = None
    execution_date: Optional[str] = None
    memo: Optional[str] = None
    custom_data: Optional[Any] = None

class MonthlyReportEntryResponse(MonthlyReportEntryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
