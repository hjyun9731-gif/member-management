from typing import Type, List, Optional, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime

from app import models

REGIONS = [
    "춘천시","원주시","강릉시","동해시","태백시","속초시","삼척시",
    "홍천군","횡성군","영월군","평창군","정선군","철원군","화천군",
    "양구군","인제군","고성군","양양군",
]

# 시/군 없는 형태 → 정식명칭 매핑
_REGION_NORM = {
    "춘천":"춘천시","원주":"원주시","강릉":"강릉시","동해":"동해시",
    "태백":"태백시","속초":"속초시","삼척":"삼척시","홍천":"홍천군",
    "횡성":"횡성군","영월":"영월군","평창":"평창군","정선":"정선군",
    "철원":"철원군","화천":"화천군","양구":"양구군","인제":"인제군",
    "고성":"고성군","양양":"양양군",
}


def normalize_region(val: str) -> str:
    """'춘천' → '춘천시', '춘천시' → '춘천시' (이미 정규화되어 있으면 그대로)"""
    if not val:
        return val
    s = val.strip()
    if s in REGIONS:
        return s
    if s in _REGION_NORM:
        return _REGION_NORM[s]
    # 앞부분 매칭 (예: '춘 천 시' → 공백 제거 후)
    cleaned = s.replace(" ", "")
    for r in REGIONS:
        if cleaned == r or cleaned == r[:-1]:  # '춘천시' or '춘천'
            return r
    return s  # 알 수 없는 지역은 그대로


def detect_category(vehicle_number: str) -> str:
    """차량번호에 '배' 포함 → 택배, 아니면 → 개인"""
    return "택배" if vehicle_number and "배" in vehicle_number else "개인"


def get_list(db: Session, model: Type, *, skip=0, limit=50,
             search=None, search_fields=None, filters=None,
             sort_by: str = None, sort_dir: str = "asc",
             nonempty_any: list = None) -> Tuple[List, int]:
    query = db.query(model).filter(model.deleted_at.is_(None))
    if search and search_fields:
        conds = [getattr(model, f).ilike(f"%{search}%") for f in search_fields if hasattr(model, f)]
        if conds:
            query = query.filter(or_(*conds))
    if filters:
        for k, v in filters.items():
            if v is None or v == "":
                continue
            if k == "management_number_prefix":
                col = getattr(model, "management_number", None)
                if col is not None:
                    query = query.filter(col.like(f"{v}%"))
                continue
            col = getattr(model, k, None)
            if col is not None:
                query = query.filter(col == v)
    # 하나 이상의 필드가 비어 있지 않은 행만 (빈 행 제거)
    if nonempty_any:
        from sqlalchemy import and_
        pairs = []
        for field in nonempty_any:
            col = getattr(model, field, None)
            if col is not None:
                pairs.append(and_(col.isnot(None), col != ''))
        if pairs:
            query = query.filter(or_(*pairs))
    total = query.count()
    # 정렬: sort_by가 지정되면 해당 컬럼 사용, 아니면 지역 ASC + id ASC
    if sort_by and hasattr(model, sort_by):
        col = getattr(model, sort_by)
        order = col.desc() if sort_dir == "desc" else col.asc()
        query = query.order_by(order, model.id.asc())
    else:
        query = query.order_by(model.id.asc())
    return query.offset(skip).limit(limit).all(), total


def get_by_id(db: Session, model: Type, item_id: int):
    return db.query(model).filter(model.id == item_id, model.deleted_at.is_(None)).first()


def create_item(db: Session, model: Type, data: dict):
    allowed = {c.name for c in model.__table__.columns}
    db_item = model(**{k: v for k, v in data.items() if k in allowed})
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


def update_item(db: Session, db_item, data: dict):
    for k, v in data.items():
        if hasattr(db_item, k):
            setattr(db_item, k, v)
    db_item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_item)
    return db_item


def soft_delete(db: Session, db_item):
    db_item.deleted_at = datetime.utcnow()
    db.commit()


# ===== MANAGEMENT NUMBER GENERATORS =====

def _get_yy() -> str:
    return str(datetime.now().year)[2:]


def _max_suffix(items, prefix: str) -> int:
    max_n = 0
    for item in items:
        try:
            n = int(item.management_number.split("-")[1])
            if n > max_n:
                max_n = n
        except Exception:
            pass
    return max_n


def get_next_new_member_number(db: Session) -> str:
    """신YY-N (신규등록 회원)"""
    yy = _get_yy()
    prefix = f"신{yy}-"
    items = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.management_number.like(f"{prefix}%"),
        models.LicenseHolder.deleted_at.is_(None)
    ).all()
    return f"{prefix}{_max_suffix(items, prefix) + 1}"


def get_next_transfer_member_number(db: Session) -> str:
    """양YY-N (양도양수 회원)"""
    yy = _get_yy()
    prefix = f"양{yy}-"
    items = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.management_number.like(f"{prefix}%"),
        models.LicenseHolder.deleted_at.is_(None)
    ).all()
    return f"{prefix}{_max_suffix(items, prefix) + 1}"


def get_next_closure_number(db: Session, closure_type: str) -> str:
    """폐-80, 양-28, 이-4 (연도 없음)"""
    prefix_start = {"폐업": ("폐-", 80), "양도": ("양-", 28), "이관": ("이-", 4)}
    prefix, start = prefix_start.get(closure_type, ("폐-", 1))
    items = db.query(models.Closure).filter(
        models.Closure.closure_type == closure_type,
        models.Closure.management_number.like(f"{prefix}%"),
        models.Closure.deleted_at.is_(None)
    ).all()
    max_n = start - 1
    for item in items:
        try:
            n = int(item.management_number.split("-")[1])
            if n > max_n:
                max_n = n
        except Exception:
            pass
    return f"{prefix}{max_n + 1}"


def check_mgmt_dup(db: Session, model: Type, mgmt_num: str, exclude_id: int = None) -> bool:
    if not mgmt_num:
        return False
    q = db.query(model).filter(model.management_number == mgmt_num, model.deleted_at.is_(None))
    if exclude_id:
        q = q.filter(model.id != exclude_id)
    return q.first() is not None


# ===== CANDIDATE → MEMBER REGISTRATION =====

def register_candidate_as_member(db: Session, candidate_id: int,
                                  approval_date: str, management_number: str) -> models.LicenseHolder:
    cand = get_by_id(db, models.Candidate, candidate_id)
    if not cand:
        raise ValueError("예정자를 찾을 수 없습니다.")
    cat = detect_category(cand.vehicle_number)
    member = models.LicenseHolder(
        management_number=management_number,
        registration_type="신규",
        status="active",
        category=cat,
        region=cand.region,
        vehicle_number=cand.vehicle_number,
        name=cand.name,
        resident_number=cand.resident_number,
        address=cand.address,
        phone=cand.phone,
        mobile=cand.mobile,
        approval_date=approval_date,
        certificate_issue_date=cand.certificate_issue_date,
        certificate_number=cand.certificate_number,
        driver_license_number=cand.driver_license_number,
        vehicle_type=cand.vehicle_type,
        fuel_type=cand.fuel_type,
        business_number=cand.business_number,
        affiliated_company=cand.affiliated_company,
        memo=cand.memo,
        candidate_id=candidate_id,
        membership_status="가입",
    )
    db.add(member)
    db.flush()
    cand.is_registered = True
    cand.member_id = member.id
    db.commit()
    db.refresh(member)
    return member


def register_transfer_as_member(db: Session, transfer_id: int,
                                  management_number: str) -> models.LicenseHolder:
    tr = get_by_id(db, models.TransferLedger, transfer_id)
    if not tr:
        raise ValueError("양도양수 기록을 찾을 수 없습니다.")
    cat = detect_category(tr.vehicle_number)
    member = models.LicenseHolder(
        management_number=management_number,
        registration_type="양도양수",
        status="active",
        category=cat,
        region=tr.region,
        vehicle_number=tr.vehicle_number,
        name=tr.transferee,
        resident_number=tr.resident_number,
        address=tr.address,
        phone=tr.phone,
        mobile=tr.mobile,
        approval_date=tr.approval_date,
        membership_date=tr.membership_date,
        certificate_issue_date=tr.certificate_issue_date,
        certificate_number=tr.certificate_number,
        driver_license_number=tr.driver_license_number,
        memo=tr.memo,
        transfer_ledger_id=transfer_id,
        membership_status="가입",
    )
    db.add(member)
    db.flush()
    tr.management_number = management_number
    tr.member_id = member.id
    db.commit()
    db.refresh(member)
    return member


def close_member(db: Session, member_id: int, closure_type: str,
                  closure_date: str, management_number: str, reason: str = "") -> models.Closure:
    member = get_by_id(db, models.LicenseHolder, member_id)
    if not member:
        raise ValueError("회원을 찾을 수 없습니다.")
    closure = models.Closure(
        management_number=management_number,
        closure_type=closure_type,
        data_type="신규자료",
        region=member.region,
        vehicle_number=member.vehicle_number,
        name=member.name,
        company_name=member.company_name,
        closure_date=closure_date,
        approval_date=member.approval_date,
        reason=reason,
        member_id=member_id,
    )
    db.add(closure)
    db.flush()
    member.status = "closed"
    member.closure_id = closure.id
    db.commit()
    db.refresh(closure)
    return closure


# ===== DASHBOARD =====

def get_dashboard_stats(db: Session) -> dict:
    lh = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active"
    )
    total = lh.count()
    joined = lh.filter(models.LicenseHolder.membership_status == "가입").count()
    individual = lh.filter(models.LicenseHolder.category == "개인").count()
    delivery = lh.filter(models.LicenseHolder.category == "택배").count()
    candidates = db.query(models.Candidate).filter(
        models.Candidate.deleted_at.is_(None),
        models.Candidate.is_registered == False
    ).count()
    closures = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None)).count()
    transfers = db.query(models.TransferLedger).filter(models.TransferLedger.deleted_at.is_(None)).count()
    return {
        "total": total, "joined": joined, "not_joined": total - joined,
        "individual": individual, "delivery": delivery,
        "candidates": candidates, "closures": closures, "transfers": transfers,
        "next_new_number": get_next_new_member_number(db),
        "next_transfer_number": get_next_transfer_member_number(db),
    }


def get_regional_stats(db: Session) -> List[dict]:
    result = []
    for region in REGIONS:
        base = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.deleted_at.is_(None),
            models.LicenseHolder.status == "active",
            models.LicenseHolder.region == region,
        )
        total = base.count()
        joined = base.filter(models.LicenseHolder.membership_status == "가입").count()
        ind = base.filter(models.LicenseHolder.category == "개인").count()
        dlv = base.filter(models.LicenseHolder.category == "택배").count()
        cl = db.query(models.Closure).filter(
            models.Closure.deleted_at.is_(None), models.Closure.region == region).count()
        result.append({"region": region, "total": total, "joined": joined,
                        "not_joined": total - joined, "individual": ind, "delivery": dlv, "closures": cl})
    return result
