"""대리인 변경이력 관리 (member_agents).

원칙
- 새 대리인을 입력해도 기존 대리인 행을 덮어쓰거나 삭제하지 않는다. 이전 대리인은 '종료'로 남긴다.
- license_holders.agent_* 컬럼은 '현재 대리인' 미러로 계속 유지한다(기존 조회·검색·수정과 호환).
- 기존 회원정보에 저장된 대리인은 이력 1건(source=legacy)으로 전환하되, 시작일을 모르면 비워 둔다(임의 생성 금지).
- 대리인만 바뀐 것은 ChangeHistory(월례보고서 실적)에 기록하지 않는다. 이 모듈은 ChangeHistory 를 건드리지 않는다.
- 이 모듈은 commit 하지 않는다. 호출한 쪽의 트랜잭션에 함께 묶인다(부분 저장 방지).
"""
import datetime as _dt
import re

from app import models
from app.services.change_judgement import mask_rrn  # noqa: F401  (재사용 가능하도록 노출)

AGENT_FIELDS = ("name", "resident_number", "mobile", "address",
                "driver_license_number", "freight_qualification_number", "start_date")
# license_holders / transfer_ledger 의 컬럼명 접두 매핑
_MEMBER_COL = {
    "name": "agent_name", "resident_number": "agent_resident_number", "mobile": "agent_mobile",
    "address": "agent_address", "driver_license_number": "agent_driver_license_number",
    "freight_qualification_number": "agent_freight_qualification_number", "start_date": "agent_start_date",
}


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def _digits(v) -> str:
    return re.sub(r"\D", "", _s(v))


def today_str() -> str:
    return _dt.date.today().strftime("%Y-%m-%d")


def from_prefixed(d: dict) -> dict:
    """{'agent_name':..., 'agent_mobile':...} 형태 dict → 이력 필드 dict"""
    return {f: _s(d.get(_MEMBER_COL[f])) for f in AGENT_FIELDS}


def mask_agent_rrn(v) -> str:
    """주민등록번호 앞 6자리만 남기고 가린다(예: 800101-*******). 형식이 아니면 전부 가린다."""
    t = _s(v)
    if not t:
        return ""
    ds = _digits(t)
    if len(ds) >= 7:
        return f"{ds[:6]}-*******"
    return "(표시 제한)"


def mask_license(v) -> str:
    """면허/자격증 번호는 끝 4자리만 보인다."""
    t = _s(v)
    if len(t) <= 4:
        return "*" * len(t)
    return "*" * (len(t) - 4) + t[-4:]


def mask_row_rrn(d: dict, keys=("resident_number", "agent_resident_number")) -> dict:
    """목록/엑셀 응답용: 주민등록번호 계열 키만 가린다(원본 DB·상세 조회·수정 폼은 그대로)."""
    for k in keys:
        if k in d and d[k]:
            d[k] = mask_agent_rrn(d[k])
    return d


def _same_person(a: dict, b: dict) -> bool:
    """같은 대리인 판정: 성명(공백 제거)이 같아야 하고, 주민번호가 둘 다 있으면 그것도 같아야 한다.
    성명이 다르면 주민번호가 같아 보여도(이전 대리인 값이 남은 경우 등) 다른 사람으로 본다."""
    na, nb = re.sub(r"\s+", "", _s(a.get("name"))), re.sub(r"\s+", "", _s(b.get("name")))
    if not na or na != nb:
        return False
    ra, rb = _digits(a.get("resident_number")), _digits(b.get("resident_number"))
    return not (ra and rb and ra != rb)


def current_row(db, member_id: int):
    return (db.query(models.MemberAgent)
            .filter(models.MemberAgent.member_id == member_id,
                    models.MemberAgent.status == "현재",
                    models.MemberAgent.deleted_at.is_(None))
            .order_by(models.MemberAgent.id.desc()).first())


def _has_rows(db, member_id: int) -> bool:
    return db.query(models.MemberAgent.id).filter(
        models.MemberAgent.member_id == member_id, models.MemberAgent.deleted_at.is_(None)).first() is not None


def _mirror_to_member(member, d: dict):
    """회원 테이블의 '현재 대리인' 컬럼을 이력의 현재 행과 맞춘다."""
    for f, col in _MEMBER_COL.items():
        if hasattr(member, col):
            setattr(member, col, _s(d.get(f)) or None)


def _clear_member_mirror(member):
    for col in _MEMBER_COL.values():
        if hasattr(member, col):
            setattr(member, col, None)


def ensure_legacy_row(db, member_id: int, legacy: dict, created_by: str = "") -> bool:
    """이력이 아직 없는 회원의 기존 대리인 정보를 이력 1건으로 전환. 시작일은 있는 값만 사용(없으면 None).
    이미 이력이 있으면 아무것도 하지 않는다(멱등)."""
    if not _s(legacy.get("name")) or _has_rows(db, member_id):
        return False
    row = models.MemberAgent(member_id=member_id, status="현재", source="legacy", created_by=created_by or "system")
    for f in AGENT_FIELDS:
        setattr(row, f, _s(legacy.get(f)) or None)
    db.add(row)
    return True


def set_current_agent(db, member, new: dict, *, source: str, created_by: str = "",
                      previous_end_date: str = "", transfer_ledger_id=None, old: dict = None) -> str:
    """회원의 현재 대리인을 설정한다. 반환: 'none'|'unchanged'|'updated'|'replaced'|'ended'.
    - old: 수정 직전 값(회원 컬럼이 이미 덮어써진 뒤 호출될 때 레거시 전환에 사용)."""
    mid = member.id
    new = {f: _s(new.get(f)) for f in AGENT_FIELDS}
    old = {f: _s((old or {}).get(f)) for f in AGENT_FIELDS} if old is not None else from_prefixed(
        {c: getattr(member, c, None) for c in _MEMBER_COL.values()})

    ensure_legacy_row(db, mid, old, created_by)
    db.flush()
    cur = current_row(db, mid)

    if not new["name"]:
        # 대리인 입력을 비운 경우: 삭제하지 않고 현재 대리인을 종료 처리한다.
        if cur:
            cur.status = "종료"
            cur.end_date = _s(previous_end_date) or today_str()
            _clear_member_mirror(member)
            return "ended"
        return "none"

    if cur and _same_person(_row_dict(cur), new):
        changed = False
        for f in AGENT_FIELDS:
            nv = new[f]
            if nv and nv != _s(getattr(cur, f)):
                setattr(cur, f, nv)
                changed = True
        if previous_end_date:                          # 같은 대리인에 종료일 입력 = 대리 종료
            cur.status, cur.end_date = "종료", _s(previous_end_date)
            _clear_member_mirror(member)
            return "ended"
        _mirror_to_member(member, _row_dict(cur))
        return "updated" if changed else "unchanged"

    replaced = False
    if cur:
        cur.status = "종료"
        cur.end_date = _s(previous_end_date) or new["start_date"] or today_str()
        replaced = True
    row = models.MemberAgent(member_id=mid, status="현재", source=source, created_by=created_by,
                             transfer_ledger_id=transfer_ledger_id)
    for f in AGENT_FIELDS:
        setattr(row, f, new[f] or None)
    db.add(row)
    _mirror_to_member(member, new)
    return "replaced" if replaced else "updated"


def _row_dict(r) -> dict:
    return {f: _s(getattr(r, f, "")) for f in AGENT_FIELDS}


def list_history(db, member_id: int, member=None, created_by: str = "") -> list:
    """대리인 이력(현재 → 과거 순). 마스킹된 값만 반환한다. 이력이 없고 회원에 기존 대리인이 있으면
    조회 시점에 레거시 1건을 만들어 보존한다(시작일 없음)."""
    if member is not None and not _has_rows(db, member_id):
        legacy = from_prefixed({c: getattr(member, c, None) for c in _MEMBER_COL.values()})
        if ensure_legacy_row(db, member_id, legacy, created_by):
            db.flush()
    rows = (db.query(models.MemberAgent)
            .filter(models.MemberAgent.member_id == member_id, models.MemberAgent.deleted_at.is_(None))
            .order_by(models.MemberAgent.status.desc(), models.MemberAgent.id.desc()).all())
    out = []
    for r in rows:
        out.append({
            "id": r.id, "name": r.name or "", "mobile": r.mobile or "", "address": r.address or "",
            "resident_number": r.resident_number,
            "driver_license_number": mask_license(r.driver_license_number),
            "freight_qualification_number": mask_license(r.freight_qualification_number),
            "start_date": r.start_date or "", "end_date": r.end_date or "",
            "status": r.status or "", "source": r.source or "",
            "start_date_known": bool(r.start_date),
            "transfer_ledger_id": r.transfer_ledger_id,
        })
    out.sort(key=lambda x: (0 if x["status"] == "현재" else 1, -x["id"]))
    return out


def backfill_legacy(db, created_by: str = "system") -> dict:
    """관리자용 일괄 전환: 대리인 이름이 있는데 이력이 없는 회원만 1건씩 추가. 멱등·비파괴."""
    created = 0
    q = db.query(models.LicenseHolder).filter(models.LicenseHolder.agent_name.isnot(None),
                                              models.LicenseHolder.agent_name != "",
                                              models.LicenseHolder.deleted_at.is_(None))
    for m in q.all():
        legacy = from_prefixed({c: getattr(m, c, None) for c in _MEMBER_COL.values()})
        if ensure_legacy_row(db, m.id, legacy, created_by):
            created += 1
    return {"created": created}
