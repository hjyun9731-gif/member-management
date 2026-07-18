import re
from typing import Type, List, Optional, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from datetime import datetime, timezone

from app import models
from app.excel_utils import is_association_member, has_value

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


def _apply_common_filters(query, model, search, search_fields, filters, nonempty_any):
    """공통 필터 적용 헬퍼"""
    from sqlalchemy import and_
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
    if nonempty_any:
        pairs = []
        for field in nonempty_any:
            col = getattr(model, field, None)
            if col is not None:
                pairs.append(and_(col.isnot(None), col != ''))
        if pairs:
            query = query.filter(or_(*pairs))
    return query


def get_sorted_page(db: Session, model: Type, *, date_field: str,
                    sort_dir: str = "desc", page: int = 1, limit: int = 50,
                    search=None, search_fields=None, filters=None,
                    nonempty_any=None) -> Tuple[List, int]:
    """날짜 기반 정렬 + 페이지네이션 (PostgreSQL 최적화).
    ① id + 날짜 + 빈행검사 필드를 경량 쿼리로 가져와 Python 정렬
    ② 해당 50건 IDs만 full 로딩 (raw_data 지연)
    nonempty_any는 SQL OR 없이 Python에서 필터 → PostgreSQL seq scan 방지."""
    from app.excel_utils import parse_date_sort
    from sqlalchemy.orm import defer as defer_col

    date_col = getattr(model, date_field, None)

    # ① 경량 SELECT: id + 날짜필드 + nonempty_any 필드 (OR 없는 단순 쿼리)
    select_cols = [model.id, date_col if date_col is not None else model.id]
    nonempty_cols = []
    for field in (nonempty_any or []):
        col = getattr(model, field, None)
        if col is not None:
            select_cols.append(col)
            nonempty_cols.append(field)

    light_q = db.query(*select_cols).filter(model.deleted_at.is_(None))
    # search/filters만 적용 (nonempty OR 조건 제외)
    light_q = _apply_common_filters(light_q, model, search, search_fields, filters, None)
    all_rows = light_q.all()

    # Python에서 빈 행 제거 (nonempty_any 필드 기준)
    if nonempty_cols:
        n_check = len(nonempty_cols)
        # row: (id, date, check1, check2, ...) → check 컬럼은 인덱스 2부터
        all_rows = [r for r in all_rows
                    if any(r[2 + i] and str(r[2 + i]).strip() for i in range(n_check))]

    # Python 날짜 파싱 정렬
    reverse = (sort_dir == "desc")
    all_rows.sort(key=lambda r: parse_date_sort(r[1] or ""), reverse=reverse)

    total = len(all_rows)
    page_ids = [r[0] for r in all_rows[(page - 1) * limit: page * limit]]

    if not page_ids:
        return [], total

    # ② 해당 IDs만 full 로딩 (raw_data 지연)
    items_q = db.query(model).filter(
        model.id.in_(page_ids),
        model.deleted_at.is_(None),
    )
    if hasattr(model, 'raw_data'):
        items_q = items_q.options(defer_col(model.raw_data))

    items = items_q.all()
    items_by_id = {i.id: i for i in items}
    return [items_by_id[pid] for pid in page_ids if pid in items_by_id], total


def get_region_vehicle_page(db: Session, model: Type, *, page: int = 1, limit: int = 50,
                             search=None, search_fields=None, filters=None,
                             nonempty_any=None) -> Tuple[List, int]:
    """지역(가나다) + 차량번호(자연정렬) 기반 페이지네이션.
    ① id + region + vehicle_number 경량 쿼리 → Python 자연정렬
    ② 50건 IDs full 로딩 (raw_data 지연)"""
    from sqlalchemy.orm import defer as defer_col

    def nat_key(s: str):
        return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', s or '')]

    region_col = getattr(model, 'region', None)
    vehicle_col = getattr(model, 'vehicle_number', None)

    if region_col is not None and vehicle_col is not None:
        light_q = db.query(model.id, region_col, vehicle_col).filter(model.deleted_at.is_(None))
    else:
        light_q = db.query(model.id, model.id, model.id).filter(model.deleted_at.is_(None))

    # search/filters 적용 (nonempty OR 없음)
    light_q = _apply_common_filters(light_q, model, search, search_fields, filters, None)
    all_rows = light_q.all()  # (id, region, vehicle_number)

    # Python에서 빈 행 제거
    all_rows = [r for r in all_rows if (r[2] and str(r[2]).strip()) or (r[1] and str(r[1]).strip())]

    # 자연 정렬: 지역(가나다) → 차량번호(자연정렬)
    all_rows.sort(key=lambda r: (r[1] or 'zzz', nat_key(r[2] or '')))

    total = len(all_rows)
    page_ids = [r[0] for r in all_rows[(page - 1) * limit: page * limit]]

    if not page_ids:
        return [], total

    items_q = db.query(model).filter(
        model.id.in_(page_ids),
        model.deleted_at.is_(None),
    )
    if hasattr(model, 'raw_data'):
        items_q = items_q.options(defer_col(model.raw_data))

    items = items_q.all()
    items_by_id = {i.id: i for i in items}
    return [items_by_id[pid] for pid in page_ids if pid in items_by_id], total


def get_sorted_page_mgmt(db: Session, model: Type, *, sort_dir: str = "desc",
                          page: int = 1, limit: int = 50,
                          search=None, search_fields=None, filters=None,
                          nonempty_any=None, fallback_date_field: str = None
                          ) -> Tuple[List, int]:
    """관리번호 기준 자연정렬 (연도+번호 숫자 비교).
    - 연도: 90~99=1990~1999, 00~현재=2000~현재
    - 같은 연도면 뒤 번호를 숫자로 비교 (26-181 > 26-099)
    - 개인/택배 구분 없이 동일 기준
    """
    from app.excel_utils import mgmt_sort_key
    from sqlalchemy.orm import defer as defer_col

    mgmt_col = getattr(model, 'management_number', None)
    select_cols = [model.id, mgmt_col if mgmt_col is not None else model.id]
    nonempty_cols = []
    for field in (nonempty_any or []):
        col = getattr(model, field, None)
        if col is not None:
            select_cols.append(col)
            nonempty_cols.append(field)

    light_q = db.query(*select_cols).filter(model.deleted_at.is_(None))
    light_q = _apply_common_filters(light_q, model, search, search_fields, filters, None)
    all_rows = light_q.all()

    if nonempty_cols:
        n_check = len(nonempty_cols)
        all_rows = [r for r in all_rows
                    if any(r[2 + i] and str(r[2 + i]).strip() for i in range(n_check))]

    reverse = (sort_dir == "desc")
    all_rows.sort(key=lambda r: mgmt_sort_key(str(r[1] or '')), reverse=reverse)

    total = len(all_rows)
    page_ids = [r[0] for r in all_rows[(page - 1) * limit: page * limit]]
    if not page_ids:
        return [], total

    items_q = db.query(model).filter(
        model.id.in_(page_ids),
        model.deleted_at.is_(None),
    )
    if hasattr(model, 'raw_data'):
        items_q = items_q.options(defer_col(model.raw_data))
    items = items_q.all()
    items_by_id = {i.id: i for i in items}
    return [items_by_id[pid] for pid in page_ids if pid in items_by_id], total

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
    db_item.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_item)
    return db_item


def soft_delete(db: Session, db_item):
    db_item.deleted_at = datetime.now(timezone.utc)
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


def lock_transfer_number_sequence(db: Session):
    """관리번호 동시발급 방지용 잠금.
    PostgreSQL: 트랜잭션 범위 advisory lock으로 동시 요청을 직렬화.
    (같은 db 세션의 트랜잭션이 commit/rollback 될 때 자동 해제됨)
    SQLite: 별도 처리 없이 통과 (단일 파일 기반이라 충돌 가능성 낮음, 개발환경 전용)
    """
    try:
        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            from sqlalchemy import text
            # 임의의 고정 키로 advisory lock (양도양수 관리번호 발급 전용)
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('transfer_member_number_seq'))"))
    except Exception:
        # 잠금 실패 시에도 진행 (완전 차단하지 않음) - 아래 중복 체크가 최종 방어선
        pass


def get_next_closure_number(db: Session, closure_type: str) -> str:
    """폐-80, 양-28, 이-4 (연도 없음). '폐지'는 '폐업'과 동일 처리."""
    if closure_type == '폐지':
        closure_type = '폐업'
    prefix_start = {"폐업": ("폐-", 80), "양도": ("양-", 28), "이관": ("이-", 4)}
    prefix, start = prefix_start.get(closure_type, ("폐-", 1))
    # 폐업 조회 시 '폐지'도 포함
    ct_filter = [closure_type]
    if closure_type == '폐업':
        ct_filter.append('폐지')
    items = db.query(models.Closure).filter(
        models.Closure.closure_type.in_(ct_filter),
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
                                  approval_date: str, management_number: str,
                                  membership_date: str = "") -> models.LicenseHolder:
    """예정자 → 회원 등록완료 처리.

    관리번호 접두어에 따라 분기:
    - '신YY-N' : 기존과 동일하게 회원 등록만 처리 (양도양수대장 생성 안 함)
    - '양YY-N' : 회원 등록과 동시에 양도양수대장에도 한 건 자동 생성 (양수자=현재 예정자,
                 양도자 정보는 없으므로 비워둠). 회원 등록 + 대장 등록을 하나의 트랜잭션으로 처리하며
                 실패 시 전체 rollback한다.

    새로운 UI/선택항목/구분필드는 추가하지 않는다 - 관리번호 문자열의 접두어만으로 판단한다.
    """
    try:
        # 동시 중복등록 방지: PostgreSQL에서는 예정자 행을 잠그고 진행 (개발환경 SQLite는 통과)
        q = db.query(models.Candidate).filter(models.Candidate.id == candidate_id)
        try:
            bind = db.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                q = q.with_for_update()
        except Exception:
            pass
        cand = q.first()
        if not cand:
            raise ValueError("예정자를 찾을 수 없습니다.")
        if cand.is_registered:
            raise ValueError("이미 등록 처리된 예정자입니다.")

        cat = detect_category(cand.vehicle_number)
        # 등록완료 모달 입력값 우선, 없으면 예정자 저장 시 입력한 가입일자 이어받기
        final_membership_date = membership_date or getattr(cand, 'membership_date', '') or ''
        # ★ 가입일자 기준으로만 판정: 없으면 무조건 미가입
        from app.excel_utils import normalize_membership_status
        ms = normalize_membership_status(final_membership_date)
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
            membership_date=final_membership_date or None,
            membership_status=ms,       # 가입일자 기준 (없으면 미가입)
            certificate_issue_date=cand.certificate_issue_date,
            certificate_number=cand.certificate_number,
            driver_license_number=cand.driver_license_number,
            vehicle_type=cand.vehicle_type,
            fuel_type=cand.fuel_type,
            business_number=cand.business_number,
            affiliated_company=cand.affiliated_company,
            memo=cand.memo,
            candidate_id=candidate_id,
        )
        db.add(member)
        db.flush()

        # ── 관리번호가 '양'으로 시작하면 양도양수대장에도 자동 생성 ──
        mgmt_clean = (management_number or "").strip()
        if mgmt_clean.startswith("양"):
            dup_ledger = db.query(models.TransferLedger).filter(
                models.TransferLedger.management_number == mgmt_clean,
                models.TransferLedger.deleted_at.is_(None),
            ).first()
            if dup_ledger:
                raise ValueError(f"양도양수대장에 관리번호 {mgmt_clean} 기록이 이미 존재합니다.")
            ledger = models.TransferLedger(
                management_number=mgmt_clean,
                receipt_date="",
                region=cand.region or "",
                vehicle_number=cand.vehicle_number or "",
                transferor="",                      # 양도자 정보 없음 - 빈칸
                transferee=cand.name or "",
                resident_number=cand.resident_number or "",
                address=cand.address or "",
                phone=cand.phone or "",
                mobile=cand.mobile or "",
                approval_date=approval_date or "",
                membership_date=final_membership_date or "",
                certificate_issue_date=cand.certificate_issue_date or "",
                certificate_number=cand.certificate_number or "",
                driver_license_number=cand.driver_license_number or "",
                memo=cand.memo or "",
                vehicle_type=cand.vehicle_type or "",
                fuel_type=cand.fuel_type or "",
                affiliated_company=cand.affiliated_company or "",
                transferor_member_id=None,           # 양도자 정보 없음 - null 허용
                transferee_member_id=member.id,
                member_id=member.id,
            )
            db.add(ledger)
            db.flush()
            member.transfer_ledger_id = ledger.id

            # ── 폐업현황에도 '양도' 기록 생성 (양도자를 내부 회원으로 특정할 수 없으므로
            #    member_id는 null, 양도자 성명 등도 확보된 정보가 없으므로 빈칸으로 둔다.
            #    새로 등록되는 양수자는 절대 폐업/비활성 처리하지 않는다) ──
            dup_closure = db.query(models.Closure).filter(
                models.Closure.transfer_ledger_id == ledger.id,
                models.Closure.deleted_at.is_(None),
            ).first()
            if not dup_closure:
                import datetime as _dt
                closure_mgmt = get_next_closure_number(db, "양도")
                if check_mgmt_dup(db, models.Closure, closure_mgmt):
                    raise ValueError(f"폐업현황 관리번호 {closure_mgmt}가 이미 존재합니다. 다시 시도해주세요.")
                closure = models.Closure(
                    management_number=closure_mgmt,
                    closure_type="양도",
                    data_type="신규자료",
                    region=cand.region or "",
                    vehicle_number=cand.vehicle_number or "",
                    name="",                       # 양도자 성명 정보 없음 - 빈칸 유지
                    closure_date=_dt.date.today().isoformat(),
                    receipt_date="",
                    approval_date=approval_date or "",
                    transferee=cand.name or "",
                    transfer_region=cand.region or "",
                    transferee_member_id=member.id,
                    transfer_ledger_id=ledger.id,
                    member_id=None,                # 내부 양도자 특정 불가 - null 허용
                )
                db.add(closure)
                db.flush()

        cand.is_registered = True
        cand.member_id = member.id
        db.commit()
        db.refresh(member)
        return member
    except Exception:
        db.rollback()
        raise


def register_transfer_as_member(db: Session, transfer_id: int,
                                  management_number: str) -> models.LicenseHolder:
    tr = get_by_id(db, models.TransferLedger, transfer_id)
    if not tr:
        raise ValueError("양도양수 기록을 찾을 수 없습니다.")
    cat = detect_category(tr.vehicle_number)
    # 가입일자(membership_date)가 있으면 가입, 없으면 미가입
    from app.excel_utils import normalize_membership_status
    ms = normalize_membership_status(tr.membership_date or '')
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
        membership_status=ms,   # 가입일자 기준 자동 판정
    )
    db.add(member)
    db.flush()
    tr.management_number = management_number
    tr.member_id = member.id
    db.commit()
    db.refresh(member)
    return member


def close_member_no_commit(db: Session, member_id: int, closure_type: str,
                            closure_date: str, management_number: str, reason: str = "",
                            transferee: str = "", transfer_region: str = "",
                            receipt_date: str = "", transferee_member_id: int = None,
                            transfer_ledger_id: int = None) -> models.Closure:
    """폐업/양도/이관 처리 (커밋하지 않음 - 상위 트랜잭션에서 일괄 커밋).
    실패 시 예외를 던지므로 호출측에서 반드시 try/except로 db.rollback() 처리해야 함."""
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
        company_name=getattr(member, 'company_name', '') or '',
        closure_date=closure_date,
        receipt_date=receipt_date or "",
        approval_date=member.approval_date,
        reason=reason,
        transferee=transferee or "",
        transfer_region=transfer_region or "",
        transferee_member_id=transferee_member_id,
        transfer_ledger_id=transfer_ledger_id,
        # 회원 기존 정보 복사
        vehicle_type=member.vehicle_type or "",
        fuel_type=member.fuel_type or "",
        structure_change=getattr(member, 'structure_change', '') or '',
        phone=member.phone or "",
        mobile=member.mobile or "",
        address=member.address or "",
        official_address=getattr(member, 'official_address', '') or '',
        membership_status=member.membership_status or "",
        membership_date=member.membership_date or "",
        certificate_issue_date=member.certificate_issue_date or "",
        certificate_number=member.certificate_number or "",
        driver_license_number=getattr(member, 'driver_license_number', '') or '',
        resident_number=getattr(member, 'resident_number', '') or '',
        affiliated_company=getattr(member, 'affiliated_company', '') or '',
        agent_name=getattr(member, 'agent_name', '') or '',
        agent_mobile=getattr(member, 'agent_mobile', '') or '',
        memo=getattr(member, 'memo', '') or '',
        member_id=member_id,
    )
    db.add(closure)
    db.flush()
    member.status = "closed"
    member.closure_id = closure.id
    return closure


def close_member(db: Session, member_id: int, closure_type: str,
                  closure_date: str, management_number: str, reason: str = "",
                  transferee: str = "", transfer_region: str = "",
                  receipt_date: str = "") -> models.Closure:
    closure = close_member_no_commit(
        db, member_id, closure_type, closure_date, management_number, reason,
        transferee=transferee, transfer_region=transfer_region, receipt_date=receipt_date,
    )
    db.commit()
    db.refresh(closure)
    return closure


# ===== 도내 양도양수 (거래 단위 일괄 처리) =====

_DUP_STRONG_MSG = ("동일한 주민등록번호 또는 차량번호를 가진 회원/예정자가 이미 존재합니다. "
                    "실수로 중복 등록되지 않도록 주의하세요.")
_DUP_WEAK_MSG = ("동일하거나 유사한 회원정보가 이미 존재합니다. 기존 회원과 연결하시겠습니까?")


def find_duplicate_transferee(db: Session, resident_number: str = "", vehicle_number: str = "",
                               name: str = "", mobile: str = "") -> List[dict]:
    """양수자 중복 확인: 주민등록번호/차량번호 완전일치 → strong,
    성명+핸드폰 조합 일치 → weak. 회원(LicenseHolder)과 예정자(Candidate) 모두 조회."""
    resident_number = (resident_number or "").strip()
    vehicle_number = (vehicle_number or "").strip()
    name = (name or "").strip()
    mobile = (mobile or "").strip()

    matches: List[dict] = []

    def _add(kind, item, strength):
        matches.append({
            "type": kind,  # 'member' | 'candidate'
            "id": item.id,
            "name": getattr(item, "name", "") or "",
            "vehicle_number": getattr(item, "vehicle_number", "") or "",
            "management_number": getattr(item, "management_number", "") or "",
            "region": getattr(item, "region", "") or "",
            "mobile": getattr(item, "mobile", "") or "",
            "strength": strength,
        })

    # 1) 주민등록번호 / 차량번호 완전일치 (strong)
    if resident_number:
        q = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.resident_number == resident_number,
            models.LicenseHolder.deleted_at.is_(None),
        ).all()
        for m in q:
            _add("member", m, "strong")
        q2 = db.query(models.Candidate).filter(
            models.Candidate.resident_number == resident_number,
            models.Candidate.deleted_at.is_(None),
            models.Candidate.is_registered == False,
        ).all()
        for c in q2:
            _add("candidate", c, "strong")

    if vehicle_number:
        q = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.vehicle_number == vehicle_number,
            models.LicenseHolder.deleted_at.is_(None),
            models.LicenseHolder.status == "active",
        ).all()
        for m in q:
            _add("member", m, "strong")
        q2 = db.query(models.Candidate).filter(
            models.Candidate.vehicle_number == vehicle_number,
            models.Candidate.deleted_at.is_(None),
            models.Candidate.is_registered == False,
        ).all()
        for c in q2:
            _add("candidate", c, "strong")

    # 2) 성명 + 핸드폰 조합 (weak)
    if name and mobile:
        q = db.query(models.LicenseHolder).filter(
            models.LicenseHolder.name == name,
            models.LicenseHolder.mobile == mobile,
            models.LicenseHolder.deleted_at.is_(None),
        ).all()
        for m in q:
            _add("member", m, "weak")
        q2 = db.query(models.Candidate).filter(
            models.Candidate.name == name,
            models.Candidate.mobile == mobile,
            models.Candidate.deleted_at.is_(None),
            models.Candidate.is_registered == False,
        ).all()
        for c in q2:
            _add("candidate", c, "weak")

    # id+type 기준 중복 제거 (strong 우선)
    dedup = {}
    for m in matches:
        key = (m["type"], m["id"])
        if key not in dedup or m["strength"] == "strong":
            dedup[key] = m
    return list(dedup.values())


def process_domestic_transfer(db: Session, *, transferor_member_id: int,
                               transfer_fields: dict, transferee_target: str,
                               transferee_fields: dict, closure_date: str,
                               closure_reason: str = "", receipt_date: str = "",
                               management_number: str = None,
                               link_existing_id: int = None,
                               link_existing_type: str = None) -> dict:
    """도내 양도양수 등록 - 하나의 트랜잭션으로 처리.
    성공 시 db.commit(), 실패 시 db.rollback() 후 예외 재발생.

    transferee_target: 'member' (즉시 회원 등록) | 'candidate' (예정자로 등록)
    link_existing_id/type: 중복확인 후 기존 회원/예정자와 연결하는 경우
    """
    try:
        transferor = get_by_id(db, models.LicenseHolder, transferor_member_id)
        if not transferor:
            raise ValueError("양도자 회원을 찾을 수 없습니다.")
        if transferor.status == "closed":
            raise ValueError("이미 폐업 처리된 회원입니다.")

        # ── 관리번호 발급 (동시성 잠금) ──
        lock_transfer_number_sequence(db)
        mgmt = management_number or get_next_transfer_member_number(db)
        if check_mgmt_dup(db, models.LicenseHolder, mgmt):
            raise ValueError(f"관리번호 {mgmt}가 이미 존재합니다. 다시 시도해주세요.")

        closure_mgmt = get_next_closure_number(db, "양도")
        if check_mgmt_dup(db, models.Closure, closure_mgmt):
            raise ValueError(f"폐업현황 관리번호 {closure_mgmt}가 이미 존재합니다. 다시 시도해주세요.")

        # ── 1) 양수자 결정: 기존 회원/예정자 연결 or 신규 생성 ──
        transferee_member = None
        transferee_candidate = None

        if link_existing_id and link_existing_type == "member":
            transferee_member = get_by_id(db, models.LicenseHolder, link_existing_id)
            if not transferee_member:
                raise ValueError("연결할 기존 회원을 찾을 수 없습니다.")
        elif link_existing_id and link_existing_type == "candidate":
            transferee_candidate = get_by_id(db, models.Candidate, link_existing_id)
            if not transferee_candidate:
                raise ValueError("연결할 기존 예정자를 찾을 수 없습니다.")
        else:
            # 신규 생성
            name = (transferee_fields.get("name") or "").strip()
            if not name:
                raise ValueError("양수자 성명을 입력하세요.")
            vehicle_number = transferee_fields.get("vehicle_number") or transferor.vehicle_number
            if transferee_target == "candidate":
                transferee_candidate = models.Candidate(
                    region=transferee_fields.get("region") or transferor.region,
                    vehicle_number=vehicle_number,
                    name=name,
                    resident_number=transferee_fields.get("resident_number") or "",
                    address=transferee_fields.get("address") or "",  # 개인정보(주소) 자동복사 금지: 입력 없으면 빈칸 유지
                    phone=transferee_fields.get("phone") or "",
                    mobile=transferee_fields.get("mobile") or "",
                    certificate_issue_date=transfer_fields.get("certificate_issue_date") or "",
                    certificate_number=transfer_fields.get("certificate_number") or "",
                    driver_license_number=transfer_fields.get("driver_license_number") or "",
                    vehicle_type=transfer_fields.get("vehicle_type") or transferor.vehicle_type or "",
                    fuel_type=transfer_fields.get("fuel_type") or transferor.fuel_type or "",
                    affiliated_company=transfer_fields.get("affiliated_company") or transferor.affiliated_company or "",
                    membership_date=transfer_fields.get("membership_date") or "",
                    memo=transfer_fields.get("memo") or "",
                )
                db.add(transferee_candidate)
                db.flush()
            else:
                cat = detect_category(vehicle_number)
                from app.excel_utils import normalize_membership_status
                ms = normalize_membership_status(transfer_fields.get("membership_date") or "")
                transferee_member = models.LicenseHolder(
                    management_number=mgmt,
                    registration_type="양도양수",
                    status="active",
                    category=cat,
                    region=transferee_fields.get("region") or transferor.region,
                    vehicle_number=vehicle_number,
                    name=name,
                    resident_number=transferee_fields.get("resident_number") or "",
                    address=transferee_fields.get("address") or "",  # 개인정보(주소) 자동복사 금지: 입력 없으면 빈칸 유지
                    phone=transferee_fields.get("phone") or "",
                    mobile=transferee_fields.get("mobile") or "",
                    approval_date=transfer_fields.get("approval_date") or "",
                    membership_date=transfer_fields.get("membership_date") or "",
                    membership_status=ms,
                    certificate_issue_date=transfer_fields.get("certificate_issue_date") or "",
                    certificate_number=transfer_fields.get("certificate_number") or "",
                    driver_license_number=transfer_fields.get("driver_license_number") or "",
                    vehicle_type=transfer_fields.get("vehicle_type") or transferor.vehicle_type or "",
                    fuel_type=transfer_fields.get("fuel_type") or transferor.fuel_type or "",
                    affiliated_company=transfer_fields.get("affiliated_company") or transferor.affiliated_company or "",
                    memo=transfer_fields.get("memo") or "",
                )
                db.add(transferee_member)
                db.flush()

        transferee_name = (transferee_member.name if transferee_member
                            else transferee_candidate.name if transferee_candidate
                            else transferee_fields.get("name", ""))
        transferee_member_id_val = transferee_member.id if transferee_member else None

        # ── 2) 양도양수대장 등록 ──
        ledger = models.TransferLedger(
            management_number=mgmt if transferee_member else "",
            receipt_date=receipt_date or "",
            region=transferee_fields.get("region") or transferor.region,
            vehicle_number=transferor.vehicle_number,
            transferor=transferor.name,
            transferee=transferee_name,
            resident_number=transferee_fields.get("resident_number") or "",
            address=transferee_fields.get("address") or "",
            phone=transferee_fields.get("phone") or "",
            mobile=transferee_fields.get("mobile") or "",
            approval_date=transfer_fields.get("approval_date") or "",
            membership_date=transfer_fields.get("membership_date") or "",
            certificate_issue_date=transfer_fields.get("certificate_issue_date") or "",
            certificate_number=transfer_fields.get("certificate_number") or "",
            driver_license_number=transfer_fields.get("driver_license_number") or "",
            memo=transfer_fields.get("memo") or "",
            vehicle_type=transfer_fields.get("vehicle_type") or transferor.vehicle_type or "",
            fuel_type=transfer_fields.get("fuel_type") or transferor.fuel_type or "",
            structure_change=transfer_fields.get("structure_change") or "",
            affiliated_company=transfer_fields.get("affiliated_company") or transferor.affiliated_company or "",
            transferor_member_id=transferor_member_id,
            transferee_member_id=transferee_member_id_val,
            member_id=transferee_member_id_val,
        )
        db.add(ledger)
        db.flush()

        # 예정자 등록인 경우, 예정자에 transfer 참조 남김 (member_id는 실제 등록 시점에 채움)
        if transferee_candidate:
            transferee_candidate.member_id = transferee_candidate.member_id  # no-op, 명시적 유지

        if transferee_member:
            transferee_member.transfer_ledger_id = ledger.id

        # ── 3) 양도자 폐업 처리 (closure_type='양도') ──
        closure = close_member_no_commit(
            db, transferor_member_id, "양도", closure_date, closure_mgmt,
            reason=closure_reason,
            transferee=transferee_name,
            transfer_region=transferee_fields.get("region") or transferor.region,
            receipt_date=receipt_date or "",
            transferee_member_id=transferee_member_id_val,
            transfer_ledger_id=ledger.id,
        )

        db.commit()
        db.refresh(ledger)
        db.refresh(closure)
        if transferee_member:
            db.refresh(transferee_member)
        if transferee_candidate:
            db.refresh(transferee_candidate)

        return {
            "ok": True,
            "management_number": mgmt if transferee_member else None,
            "closure_management_number": closure_mgmt,
            "transfer_ledger_id": ledger.id,
            "closure_id": closure.id,
            "transferee_member_id": transferee_member.id if transferee_member else None,
            "transferee_candidate_id": transferee_candidate.id if transferee_candidate else None,
            "transferee_type": "member" if transferee_member else "candidate",
        }
    except Exception:
        db.rollback()
        raise


# ===== 양도양수대장 기존자료 연결관계 복구 =====

def _mask_rn(rn: str) -> str:
    """주민등록번호 표시용 마스킹 (앞 6자리만 노출)"""
    rn = (rn or "").strip()
    if len(rn) >= 7:
        return rn[:6] + "-" + "*" * (len(rn) - 7)
    return rn


def _link_candidate_dict(member, matched_by: str) -> dict:
    return {
        "id": member.id,
        "name": member.name or "",
        "vehicle_number": member.vehicle_number or "",
        "management_number": member.management_number or "",
        "region": member.region or "",
        "mobile": member.mobile or "",
        "resident_number_masked": _mask_rn(member.resident_number or ""),
        "matched_by": matched_by,   # resident_number / vehicle_number / name_mobile / name_region_date
    }


def find_link_candidates_for_ledger(db: Session, ledger: "models.TransferLedger", role: str,
                                     exclude_member_id: int = None) -> List[dict]:
    """양도양수대장 한 건에 대해 회원(LicenseHolder) 연결 후보를 찾는다.
    role: 'transferor' | 'transferee'
    우선순위: 주민등록번호 완전일치+성명일치 > 성명+핸드폰 완전일치 > 성명+지역 일치
             > 성명 일치(차량번호는 후보가 여러 명일 때만 보조적으로 좁히는 용도).
    차량번호 단독 일치만으로는 절대 자동 확정하지 않는다 (양도자·양수자가 같은 차량번호를
    공유할 수 있으므로 성명이 다르면 그 차량번호 일치는 무시한다).
    exclude_member_id: 반대쪽 역할에 이미 배정된(또는 배정하려는) 회원 ID - 자기 자신이
    양도자·양수자로 동시에 연결되는 것을 막기 위해 결과에서 제외한다.
    앞 단계에서 후보가 나오면(1명이든 여러 명이든) 그 단계에서 확정하고 다음 단계로 넘어가지 않는다.
    기존 데이터는 조회만 하며 수정하지 않는다."""
    name = ((ledger.transferee if role == "transferee" else ledger.transferor) or "").strip()
    if not name:
        return []

    vehicle_number = (ledger.vehicle_number or "").strip()
    region = (ledger.region or "").strip()
    # transferee 쪽에만 주민등록번호/핸드폰 컬럼이 실질적으로 채워짐 (양도자는 이름 정보만 있는 경우가 많음)
    resident_number = (ledger.resident_number or "").strip() if role == "transferee" else ""
    mobile = (ledger.mobile or "").strip() if role == "transferee" else ""

    def base_q():
        q = db.query(models.LicenseHolder).filter(models.LicenseHolder.deleted_at.is_(None))
        if exclude_member_id:
            q = q.filter(models.LicenseHolder.id != exclude_member_id)
        return q

    # 1) 주민등록번호 완전일치 + 성명 일치
    if resident_number:
        rows = base_q().filter(models.LicenseHolder.resident_number == resident_number,
                                models.LicenseHolder.name == name).all()
        if rows:
            return [_link_candidate_dict(r, "resident_number") for r in rows]

    # 2) 성명 + 핸드폰 완전일치
    if name and mobile:
        rows = base_q().filter(models.LicenseHolder.name == name,
                                models.LicenseHolder.mobile == mobile).all()
        if rows:
            return [_link_candidate_dict(r, "name_mobile") for r in rows]

    # 3) 성명 + 지역 일치 (날짜 근접 확인은 후보가 여러 명일 때 화면에서 사용자가 최종 판단)
    if name and region:
        rows = base_q().filter(models.LicenseHolder.name == name,
                                models.LicenseHolder.region == region).all()
        if rows:
            return [_link_candidate_dict(r, "name_region_date") for r in rows]

    # 4) 성명 일치만 - 차량번호는 여러 명일 때 보조자료로만 사용해 좁힘 (단독 매칭 금지)
    if name:
        rows = base_q().filter(models.LicenseHolder.name == name).all()
        if len(rows) > 1 and vehicle_number:
            narrowed = [r for r in rows if (r.vehicle_number or "").strip() == vehicle_number]
            if len(narrowed) == 1:
                return [_link_candidate_dict(narrowed[0], "name_vehicle_number")]
        if rows:
            return [_link_candidate_dict(r, "name") for r in rows]

    return []


def link_transfer_member(db: Session, ledger_id: int, role: str, member_id: int) -> "models.TransferLedger":
    """사용자가 후보 목록에서 직접 선택한 회원으로 연결 (양도자/양수자 각각 별도 연결 가능)."""
    if role not in ("transferor", "transferee"):
        raise ValueError("role은 transferor 또는 transferee만 가능합니다.")
    ledger = get_by_id(db, models.TransferLedger, ledger_id)
    if not ledger:
        raise ValueError("양도양수 기록을 찾을 수 없습니다.")
    member = get_by_id(db, models.LicenseHolder, member_id)
    if not member:
        raise ValueError("연결할 회원을 찾을 수 없습니다.")
    # self-guard: 같은 회원을 양도자·양수자로 동시에 연결하지 않음
    other_id = ledger.transferee_member_id if role == "transferor" else ledger.transferor_member_id
    if other_id and other_id == member_id:
        raise ValueError("동일한 회원을 양도자와 양수자로 동시에 연결할 수 없습니다.")
    if role == "transferor":
        ledger.transferor_member_id = member_id
    else:
        ledger.transferee_member_id = member_id
    db.commit()
    db.refresh(ledger)
    return ledger


def bulk_relink_transfer_ledger(db: Session) -> dict:
    """기존 양도양수대장 자료 전체를 대상으로 연결 복구를 일괄 시도.
    확실한 후보(1명)만 자동 연결하고, 애매한 자료(후보 여러 명/일치 없음)는 그대로 둔다.
    같은 회원이 양도자·양수자로 동시에 연결되는 경우는 self_conflict로 분류하고 자동 연결하지 않는다.
    기존 원문 데이터는 수정/삭제하지 않으며 *_member_id 필드만 채운다."""
    ledgers = db.query(models.TransferLedger).filter(models.TransferLedger.deleted_at.is_(None)).all()

    def _is_fully_linked(t):
        ok_or = (not (t.transferor or "").strip()) or bool(t.transferor_member_id)
        ok_ee = (not (t.transferee or "").strip()) or bool(t.transferee_member_id)
        return ok_or and ok_ee

    before_linked = sum(1 for t in ledgers if _is_fully_linked(t))

    counts = {"auto_linked": 0, "multiple_candidates": 0, "no_match": 0,
              "already_linked": 0, "self_conflict": 0}

    for t in ledgers:
        need_transferor = bool((t.transferor or "").strip()) and not t.transferor_member_id
        need_transferee = bool((t.transferee or "").strip()) and not t.transferee_member_id

        if not need_transferor and not need_transferee:
            counts["already_linked"] += 1
            continue

        linked_any = False
        saw_multi = False
        saw_none = False
        saw_conflict = False

        transferor_candidate_id = None
        if need_transferor:
            cands = find_link_candidates_for_ledger(db, t, "transferor",
                                                      exclude_member_id=t.transferee_member_id)
            if len(cands) == 1:
                transferor_candidate_id = cands[0]["id"]
            elif len(cands) > 1:
                saw_multi = True
            else:
                saw_none = True

        transferee_candidate_id = None
        if need_transferee:
            cands = find_link_candidates_for_ledger(db, t, "transferee",
                                                      exclude_member_id=t.transferor_member_id)
            if len(cands) == 1:
                transferee_candidate_id = cands[0]["id"]
            elif len(cands) > 1:
                saw_multi = True
            else:
                saw_none = True

        # self-guard: 이번에 확정하려는 양도자 후보와 양수자 후보가 같은 사람이면 둘 다 보류
        if (transferor_candidate_id and transferee_candidate_id
                and transferor_candidate_id == transferee_candidate_id):
            saw_conflict = True
            transferor_candidate_id = None
            transferee_candidate_id = None

        if transferor_candidate_id:
            t.transferor_member_id = transferor_candidate_id
            linked_any = True
        if transferee_candidate_id:
            t.transferee_member_id = transferee_candidate_id
            linked_any = True

        if linked_any:
            counts["auto_linked"] += 1
        elif saw_conflict:
            counts["self_conflict"] += 1
        elif saw_multi:
            counts["multiple_candidates"] += 1
        elif saw_none:
            counts["no_match"] += 1

    db.commit()

    after_linked = sum(1 for t in ledgers if _is_fully_linked(t))

    return {
        "total_records": len(ledgers),
        "before_fully_linked": before_linked,
        "after_fully_linked": after_linked,
        **counts,
    }


# ===== DASHBOARD =====

def get_dashboard_stats(db: Session) -> dict:
    """대시보드 상단 통계 - 항상 DB 현재 상태 기준 (캐시 없음)

    기준:
    - 총 사업자: status=active, deleted_at IS NULL
    - 가입: membership_date 있음 (가입일자 기준)
    - 미가입: membership_date 없음
    - 취업신고: certificate_issue_date 있음 (자격증명발급일자 기준)
    - 미신고: certificate_issue_date 없음
    """
    lh_all = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.status == "active"
    ).all()

    total      = len(lh_all)
    individual = sum(1 for m in lh_all if m.category == "개인")
    delivery   = sum(1 for m in lh_all if m.category == "택배")

    # 가입: membership_date(가입일자) 기준 - 공통 판정 함수 사용 (다른 화면과 동일 기준)
    joined     = sum(1 for m in lh_all if is_association_member(m.membership_date))
    not_joined = total - joined

    # 취업신고: certificate_issue_date(자격증명발급일자) 값 있음
    cert_all   = sum(1 for m in lh_all if has_value(m.certificate_issue_date))
    cert_ind   = sum(1 for m in lh_all if m.category == "개인" and has_value(m.certificate_issue_date))
    cert_del   = sum(1 for m in lh_all if m.category == "택배" and has_value(m.certificate_issue_date))

    candidates = db.query(models.Candidate).filter(
        models.Candidate.deleted_at.is_(None),
        models.Candidate.is_registered == False
    ).count()
    closures  = db.query(models.Closure).filter(models.Closure.deleted_at.is_(None)).count()
    transfers = db.query(models.TransferLedger).filter(models.TransferLedger.deleted_at.is_(None)).count()

    return {
        "total": total, "joined": joined, "not_joined": not_joined,
        "individual": individual, "delivery": delivery,
        # 취업신고/미신고 (자격증명발급일자 기준)
        "employed": cert_all,                       # 전체 취업신고
        "not_employed": total - cert_all,           # 전체 미신고
        "individual_employed": cert_ind,
        "individual_not_employed": individual - cert_ind,
        "delivery_employed": cert_del,
        "delivery_not_employed": delivery - cert_del,
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
        rows = base.all()
        total = len(rows)
        # 가입 판정: 공통 판정 함수 사용 (membership_status 필드는 과거 데이터와 어긋날 수 있어 신뢰하지 않음)
        joined = sum(1 for m in rows if is_association_member(m.membership_date))
        ind = sum(1 for m in rows if m.category == "개인")
        dlv = sum(1 for m in rows if m.category == "택배")
        cl = db.query(models.Closure).filter(
            models.Closure.deleted_at.is_(None), models.Closure.region == region).count()
        result.append({"region": region, "total": total, "joined": joined,
                        "not_joined": total - joined, "individual": ind, "delivery": dlv, "closures": cl})
    return result
