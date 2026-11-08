"""Read-only four-field duplicate candidate search.

The public notice typically provides only management number, name, approval date and
region. This search deliberately never reads/returns RRN, phone or certificate data.
It suggests records, never asserts identity or mutates an existing member.
"""
from datetime import datetime
import re
from sqlalchemy.orm import Session
from app import models
from app.services.member_integrity import mgmt_key, _name


def date_key(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Avoid interpreting mixed date+notes as an exact date.
    m = re.fullmatch(r"\s*(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})\.?\s*", raw)
    if not m:
        return ""
    year, month, day = (int(v) for v in m.groups())
    if year < 100:
        year += 2000 if year <= 50 else 1900
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def region_key(value):
    region = re.sub(r"\s+", "", str(value or "")).replace("강원특별자치도", "").replace("강원도", "")
    return region[:-1] if len(region) > 1 and region.endswith(("시", "군")) else region


def candidate_approval_date(c):
    raw = c.raw_data if isinstance(c.raw_data, dict) else {}
    return raw.get("approval_date", "") or ""


def set_candidate_approval_date(data, original_raw=None):
    """Keep approval date in Candidate.raw_data without a production DB migration."""
    if "approval_date" not in data:
        return data
    date = data.pop("approval_date")
    raw = (dict(original_raw) if isinstance(original_raw, dict) else
           {"_legacy_raw_data": original_raw} if original_raw is not None else {})
    raw.update(data.get("raw_data") if isinstance(data.get("raw_data"), dict) else {})
    if date:
        raw["approval_date"] = date
    else:
        raw.pop("approval_date", None)
    data["raw_data"] = raw
    return data


def find_similar(db: Session, *, management_number="", name="", approval_date="", region="", limit=12):
    mgmt = mgmt_key(management_number)
    person = _name(name)
    when = date_key(approval_date)
    place = region_key(region)
    if not mgmt and not person:
        return {"matches": [], "has_management_conflict": False}

    result = []
    # Pick only the four public-notice fields; no sensitive fields are selected.
    members = db.query(models.LicenseHolder.id, models.LicenseHolder.name,
                       models.LicenseHolder.management_number, models.LicenseHolder.approval_date,
                       models.LicenseHolder.region, models.LicenseHolder.category,
                       models.LicenseHolder.status).filter(models.LicenseHolder.deleted_at.is_(None)).all()
    for mid, mname, mnum, mdate, mregion, category, status in members:
        result.append(("member", mid, category or "회원", mname, mnum, mdate, mregion, status or ""))

    # Converted candidates already have a member: show the member once, not two suggestions.
    from sqlalchemy import or_
    candidates = db.query(models.Candidate.id, models.Candidate.name,
                          models.Candidate.management_number, models.Candidate.region,
                          models.Candidate.raw_data, models.Candidate.member_id).filter(
        models.Candidate.deleted_at.is_(None),
        or_(models.Candidate.is_registered.is_(False), models.Candidate.is_registered.is_(None))).all()
    for cid, cname, cnum, cregion, craw, linked_member_id in candidates:
        if linked_member_id:
            continue
        raw = craw if isinstance(craw, dict) else {}
        result.append(("candidate", cid, "예정자", cname, cnum,
                       raw.get("approval_date", ""), cregion, "예정"))

    # Unsigned/unfinished transfer entries can exist without a member or a candidate.
    transfers = db.query(models.TransferLedger.id, models.TransferLedger.transferee,
                         models.TransferLedger.management_number, models.TransferLedger.approval_date,
                         models.TransferLedger.region, models.TransferLedger.member_id,
                         models.TransferLedger.transferee_member_id).filter(
                             models.TransferLedger.deleted_at.is_(None)).all()
    for tid, tname, tnum, tdate, tregion, member_id, transferee_id in transfers:
        if member_id or transferee_id:
            continue
        result.append(("transfer", tid, "양도양수대장", tname, tnum, tdate, tregion, "미연결"))

    output = []
    for kind, rid, category, row_name, row_mgmt, row_date, row_region, status in result:
        exact_mgmt = bool(mgmt and mgmt == mgmt_key(row_mgmt))
        exact_name = bool(person and person == _name(row_name))
        exact_date = bool(when and when == date_key(row_date))
        exact_region = bool(place and place == region_key(row_region))
        if not (exact_mgmt or exact_name):
            continue
        matches = []
        if exact_mgmt: matches.append("관리번호")
        if exact_name: matches.append("이름")
        if exact_date: matches.append("인가일자")
        if exact_region: matches.append("지역")
        # A one-field name match is a warning, not evidence of identity.
        score = 100 * exact_mgmt + 30 * exact_name + 15 * exact_date + 10 * exact_region
        output.append({"type": kind, "id": rid, "category": category,
                       "name": row_name or "", "management_number": row_mgmt or "",
                       "approval_date": row_date or "", "region": row_region or "",
                       "status": status, "matched_fields": matches,
                       "management_conflict": exact_mgmt, "score": score})
    output.sort(key=lambda r: (-r["score"], {"member":0,"candidate":1,"transfer":2}[r["type"]], r["id"]))
    return {"matches": output[:limit],
            "has_management_conflict": any(r["management_conflict"] for r in output),
            "total": len(output)}
