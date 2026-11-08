"""회원·예정자 등록 시 보수적인 중복 검증 (운영 DB 이력은 수정하지 않음).

동명이인/과거 양도 이력 보호: 성명이나 차량번호 단독으로 회원을 합치지 않음.
의심되는 신규 등록은 기존 레코드 ID를 안내하고 사람이 확인하도록 중단함.
"""
import re
import unicodedata
from sqlalchemy import text
from app import models


def mgmt_key(value):
    s = unicodedata.normalize("NFKC", str(value or ""))
    # 오입력된 관리번호의 뒤쪽 마침표·공백과 내부 공백을 비교할 때만 무시.
    s = re.sub(r"\s+", "", s).rstrip(".")
    if re.fullmatch(r"[신양]\d{2}-\d+", s):
        h, n = s.split("-", 1)
        return f"{h}-{int(n)}"
    return s


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _name(value):
    return re.sub(r"\s+", "", str(value or ""))


def _plate(value):
    return re.sub(r"[\s호-]+", "", str(value or ""))


def _cert(value):
    from app import crud
    return crud.normalize_certificate_number(value or "") or ""


def lock_registration(db):
    """PostgreSQL 등록 경로의 조회→저장을 트랜잭션 락으로 직렬화. 실패하면 중단."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('transfer_member_number_seq'))"))


def management_conflicts(db, number, *, member_id=None, candidate_id=None):
    key = mgmt_key(number)
    if not key:
        return []
    conflicts = []
    for model, label, skip in ((models.LicenseHolder, "회원", member_id),
                               (models.Candidate, "예정자", candidate_id)):
        # 관리번호 전수 비교는 읽기 전용. 회원 4천명 규모에서 일회 등록 시 사용.
        rows = db.query(model.id, model.management_number).filter(model.deleted_at.is_(None)).all()
        for rid, value in rows:
            if rid != skip and mgmt_key(value) == key:
                conflicts.append(f"{label} #{rid}")
    return conflicts


def identity_conflicts(db, data, *, member_id=None, candidate_id=None):
    """공통 식별정보 2개 이상이 일치할 때만 충돌로 판정 (차량 단독·성명 단독 금지)."""
    name, cert, resident, plate = (_name(data.get("name")), _cert(data.get("certificate_number")),
                                  _digits(data.get("resident_number")), _plate(data.get("vehicle_number")))
    conflicts = []
    for model, label, skip in ((models.LicenseHolder, "회원", member_id),
                               (models.Candidate, "예정자", candidate_id)):
        for row in db.query(model).filter(model.deleted_at.is_(None)).all():
            if row.id == skip:
                continue
            same_name = bool(name and _name(row.name) == name)
            same_cert = bool(cert and cert == _cert(row.certificate_number))
            # 마스킹된 주민번호는 강한 신원정보로 취급하지 않음.
            same_resident = bool(len(resident) == 13 and resident == _digits(row.resident_number))
            same_plate = bool(plate and plate == _plate(row.vehicle_number))
            # 성명+차량 일치는 양도양수 이력상 정상일 수 있으므로 경고 대상으로 제한.
            if same_cert or same_resident:
                conflicts.append(f"{label} #{row.id} (자격증명/식별정보 일치)")
            elif same_name and same_plate and model is models.Candidate and not row.is_registered:
                conflicts.append(f"{label} #{row.id} (동일 성명·차량의 미전환 예정자)")
            elif same_name and same_plate and model is models.LicenseHolder and row.status in {"active", "pending"}:
                # 이름+차량 2개 근거는 동일인 확정이 아니므로 병합하지 않고 관리자 확인을 요구.
                conflicts.append(f"{label} #{row.id} (동일 성명·차량의 활성 회원, 수동 확인)")
    return conflicts


def assert_registration_safe(db, data, management_number="", *, member_id=None, candidate_id=None):
    conflicts = management_conflicts(db, management_number, member_id=member_id, candidate_id=candidate_id)
    conflicts += identity_conflicts(db, data, member_id=member_id, candidate_id=candidate_id)
    if conflicts:
        names = ", ".join(dict.fromkeys(conflicts) )
        raise ValueError(f"중복등록 위험: {names}. 기존 회원·예정자와 연결 여부를 먼저 확인하세요. 자동 병합하지 않았습니다.")
