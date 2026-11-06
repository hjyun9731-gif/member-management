"""관리번호 정정과 자격증명 재연결의 검증/승인 실행 공용 로직.

미리보기는 절대 DB에 쓰지 않으며, 실행은 별도 관리설정(백업 확인 후 활성화) 필요.
폐업·중복 회원을 '삭제'해서 관리번호를 빼앗는 기능은 제공하지 않는다.
"""
import os
import re
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, text
from app import models, crud, certificate_ledger_models as clm

MGMT_RE = re.compile(r"^양\d{2}-[1-9]\d*$")


def writes_enabled():
    # 설정만으로 백업 여부가 증명되지는 않는다. 시스템 관리자가 백업/복원 검증 후 설정해야 한다.
    return os.environ.get("ENABLE_VERIFIED_DATA_CORRECTIONS", "").strip() == "I_VERIFIED_BACKUP"


def _brief_member(m):
    return {"id": m.id, "name": m.name or "", "management_number": m.management_number or "",
            "status": m.status or "", "deleted": bool(m.deleted_at),
            "vehicle_number": m.vehicle_number or ""}


def preview_transfer_correction(db: Session, member_id: int, desired_number: str) -> dict:
    """정정 시 바뀔 대상과 충돌을 보여준다. 폐업회원도 번호 점유자로 취급한다."""
    target = (desired_number or "").strip()
    blocked, warnings = [], []
    m = db.get(models.LicenseHolder, member_id)
    if not m or m.deleted_at is not None:
        return {"allowed": False, "blocked": ["대상 회원이 없거나 삭제 상태입니다."], "member_id": member_id,
                "desired_number": target, "related": {}, "writes_enabled": writes_enabled()}
    if not MGMT_RE.fullmatch(target):
        blocked.append("목표 관리번호는 '양YY-N' 형식이어야 합니다.")
    if m.status not in {"active", "pending"}:
        blocked.append("폐업·비활성 회원은 이 정정 기능으로 변경할 수 없습니다.")
    if not m.transfer_ledger_id:
        blocked.append("회원에 연결된 양도양수대장이 없습니다. 먼저 실제 업무이력을 확인하세요.")
    t = db.get(models.TransferLedger, m.transfer_ledger_id) if m.transfer_ledger_id else None
    if t is None or t.deleted_at is not None:
        blocked.append("연결된 양도양수대장이 없거나 삭제 상태입니다.")
    elif (t.transferee_member_id not in (None, m.id) or t.member_id not in (None, m.id)):
        blocked.append(f"양도양수대장 #{t.id}이 다른 회원을 참조합니다.")
    c = db.get(models.Candidate, m.candidate_id) if m.candidate_id else None
    if m.candidate_id and (not c or c.deleted_at is not None or c.member_id not in (None, m.id)):
        blocked.append("연결된 예정자가 삭제됐거나 다른 회원을 참조합니다.")
    # 보존할 회원 외에 같은 번호 점유자가 있으면 closed라도 절대 덮어쓰지 않는다.
    owner_members = db.query(models.LicenseHolder).filter(
        models.LicenseHolder.management_number == target,
        models.LicenseHolder.deleted_at.is_(None),
        models.LicenseHolder.id != m.id).all()
    if owner_members:
        blocked.append("다른 회원이 관리번호를 점유하고 있습니다(폐업 포함). 수동 확인 전 변경 불가.")
    owner_candidates = db.query(models.Candidate).filter(
        models.Candidate.management_number == target,
        models.Candidate.deleted_at.is_(None)).all()
    foreign_candidates = [r for r in owner_candidates if r.id != m.candidate_id]
    if foreign_candidates:
        blocked.append("다른 예정자가 목표 관리번호를 점유하고 있습니다.")
    owner_transfers = db.query(models.TransferLedger).filter(
        models.TransferLedger.management_number == target,
        models.TransferLedger.deleted_at.is_(None)).all()
    if any(r.id != getattr(t, 'id', None) for r in owner_transfers):
        blocked.append("다른 활성 양도양수대장이 목표 관리번호를 사용합니다.")
    if c and c.management_number and c.management_number not in (m.management_number, target):
        blocked.append("연결 예정자의 관리번호가 회원번호와 다릅니다. 수동 확인이 필요합니다.")
    if t and t.management_number and t.management_number not in (m.management_number, target):
        blocked.append("연결 양도양수대장의 관리번호가 회원번호와 다릅니다.")
    if m.management_number == target and m.registration_type == "양도양수" and (
        not t or t.management_number == target) and (not c or c.management_number == target):
        warnings.append("이미 정정된 상태입니다. 추가 변경이 필요하지 않습니다.")
    history = db.query(models.TransferLedger).filter(
        models.TransferLedger.management_number == target,
        models.TransferLedger.deleted_at.isnot(None)).all()
    if history:
        warnings.append(f"삭제된 양도양수대장 {len(history)}건이 같은 번호로 남아 있습니다. 이력은 보존됩니다.")
    return {
        "allowed": not blocked, "writes_enabled": writes_enabled(), "blocked": blocked, "warnings": warnings,
        "member": _brief_member(m), "member_id": m.id, "desired_number": target,
        "proposed": {"registration_type": "양도양수", "management_number": target},
        "related": {"candidate_id": getattr(c, "id", None), "transfer_ledger_id": getattr(t, "id", None),
                    "conflicting_members": [_brief_member(x) for x in owner_members],
                    "conflicting_candidates": [x.id for x in foreign_candidates],
                    "conflicting_transfer_ledgers": [x.id for x in owner_transfers if x.id != getattr(t, 'id', None)]},
    }


def apply_transfer_correction(db: Session, member_id: int, desired_number: str, expected_old: str,
                              reason: str, actor: str) -> dict:
    if not writes_enabled():
        raise ValueError("쓰기 기능이 비활성화되어 있습니다. 백업·복원 검증 후 관리자가 설정해야 합니다.")
    if len((reason or "").strip()) < 10:
        raise ValueError("정정 사유를 10자 이상 입력하세요.")
    # PostgreSQL에서 사용 중인 관리번호 발급과 동일한 advisory lock.
    if db.get_bind().dialect.name == "postgresql":
        # 잠금 실패를 무시하면 중복 관리번호를 발급할 수 있으므로 예외를 그대로 전파한다.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('transfer_member_number_seq'))"))
    try:
        m = db.query(models.LicenseHolder).filter(models.LicenseHolder.id == member_id).with_for_update().first()
        if not m or (m.management_number or "") != expected_old:
            raise ValueError("회원 정보가 미리보기 이후 변경됐습니다. 다시 검사하세요.")
        p = preview_transfer_correction(db, member_id, desired_number)
        if not p["allowed"]:
            raise ValueError("관리번호 정정 차단: " + " / ".join(p["blocked"]))
        old = m.management_number or ""
        t = db.get(models.TransferLedger, m.transfer_ledger_id)
        c = db.get(models.Candidate, m.candidate_id) if m.candidate_id else None
        updates = []
        for obj, field, val, kind in [(m, "management_number", desired_number, "회원"),
                                      (m, "registration_type", "양도양수", "회원"),
                                      (t, "management_number", desired_number, "양도양수대장"),
                                      (c, "management_number", desired_number, "예정자")]:
            if obj is None:
                continue
            prev = getattr(obj, field) or ""
            if prev == val:
                continue
            setattr(obj, field, val)
            updates.append((kind, obj.id, field, prev, val))
            db.add(models.MemberEditLog(member_id=m.id, name=m.name or "", vehicle_number=m.vehicle_number or "",
                                        field_name=f"{kind}.{field}", old_value=prev, new_value=val,
                                        edit_reason=reason, created_by=actor))
        if t.transferee_member_id is None:
            t.transferee_member_id = m.id
            db.add(models.MemberEditLog(member_id=m.id, name=m.name or "", vehicle_number=m.vehicle_number or "",
                                        field_name="양도양수대장.transferee_member_id", old_value="", new_value=str(m.id),
                                        edit_reason=reason, created_by=actor))
        if t.member_id is None:
            t.member_id = m.id
        if c and c.member_id is None:
            c.member_id = m.id
        db.flush()
        db.commit()
        return {"changed": len(updates), "member_id": m.id, "old_number": old, "desired_number": desired_number,
                "changed_fields": [f"{kind}#{id_}.{field}" for kind, id_, field, _, _ in updates]}
    except Exception:
        db.rollback()
        raise


def _cert(v):
    return crud.normalize_certificate_number(v or "") or ""


def preview_certificate_relink(db: Session, ledger_id: int, target_type: str, target_id: int) -> dict:
    """지정 발급대장과 예정자/회원의 번호·신원·중복 연결을 검증."""
    reasons = []
    row = db.get(clm.CertificateIssuanceLedger, ledger_id)
    model = {"candidate": models.Candidate, "member": models.LicenseHolder}.get(target_type)
    target = db.get(model, target_id) if model else None
    if row is None or row.deleted_at is not None:
        reasons.append("발급대장이 없거나 삭제됐습니다.")
    if target is None or target.deleted_at is not None:
        reasons.append("연결 대상이 없거나 삭제됐습니다.")
    if not reasons:
        rn, tn = _cert(row.document_number), _cert(target.certificate_number)
        if not rn or not tn or rn != tn:
            reasons.append("발급대장과 대상의 자격증명번호가 없거나 서로 다릅니다.")
        if row.name and target.name and row.name.strip() != target.name.strip():
            reasons.append("성명이 다릅니다. 오연결 방지를 위해 자동 연결을 차단합니다.")
        if row.vehicle_number and target.vehicle_number:
            if re.sub(r"[\s-]", "", row.vehicle_number) != re.sub(r"[\s-]", "", target.vehicle_number):
                reasons.append("차량번호가 다릅니다. 수동 조사 대상입니다.")
        if target_type == "member":
            if row.member_id and row.member_id != target.id:
                reasons.append("기존에 다른 회원 ID가 연결돼 있습니다.")
            candidate_id = getattr(target, "candidate_id", None)
            if row.candidate_id and candidate_id and row.candidate_id != candidate_id:
                reasons.append("예정자 ID가 다릅니다.")
            if row.candidate_id and not candidate_id:
                existing = db.get(models.Candidate, row.candidate_id)
                if existing and existing.deleted_at is None and existing.member_id != target.id:
                    reasons.append("기존 예정자가 다른 회원을 참조합니다.")
        else:
            if row.candidate_id and row.candidate_id != target.id:
                reasons.append("기존에 다른 예정자 ID가 연결돼 있습니다.")
            if row.member_id and row.member_id != target.member_id:
                reasons.append("기존 회원 연결과 대상 예정자의 회원 연결이 충돌합니다.")
        yy, nval = rn.split("-", 1)
        nint = int(nval)
        variants = list(dict.fromkeys([f"{yy}-{nint}", f"{yy}-{nint:02d}",
                                       f"{yy}-{nint:03d}", f"{yy}-{nint:04d}"]))
        other = db.query(clm.CertificateIssuanceLedger).filter(
            clm.CertificateIssuanceLedger.id != row.id,
            clm.CertificateIssuanceLedger.deleted_at.is_(None),
            or_(*(clm.CertificateIssuanceLedger.document_number.like(f"{v}%") for v in variants))).all()
        for x in other:
            if _cert(x.document_number) and _cert(x.document_number) == rn:
                reasons.append(f"같은 표준 발급번호가 다른 발급대장 #{x.id}에도 있습니다.")
                break
        # 대상자에게 다른 번호의 발급대장이 이미 있는지 전체에서 별도 점검.
        # 단순 동일 번호 검색만으로는 과거 재발급/복수대장을 놓칠 수 있다.
        if target_type == "candidate":
            linked_other = db.query(clm.CertificateIssuanceLedger.id).filter(
                clm.CertificateIssuanceLedger.id != row.id,
                clm.CertificateIssuanceLedger.deleted_at.is_(None),
                clm.CertificateIssuanceLedger.candidate_id == target.id).first()
            if linked_other:
                reasons.append(f"예정자가 이미 발급대장 #{linked_other[0]}에 연결돼 있습니다.")
        else:
            linked_other = db.query(clm.CertificateIssuanceLedger.id).filter(
                clm.CertificateIssuanceLedger.id != row.id,
                clm.CertificateIssuanceLedger.deleted_at.is_(None),
                clm.CertificateIssuanceLedger.member_id == target.id).first()
            if linked_other:
                reasons.append(f"회원이 이미 발급대장 #{linked_other[0]}에 연결돼 있습니다.")
        logs = [x for x in db.query(models.CertificateNumberLog).filter(
            or_(*(models.CertificateNumberLog.certificate_number.like(f"{v}%") for v in variants))).all()
            if _cert(x.certificate_number) == rn]
        if len(logs) != 1:
            reasons.append("발급번호 이력 원장이 없거나 같은 표준 번호로 중복돼 있습니다.")
        else:
            log = logs[0]
            if log.status == "cancelled":
                reasons.append("취소된 발급번호입니다.")
            if log.status == "used" and log.linked_table and log.linked_id:
                allowed_ids = {("candidates", target.id)} if target_type == "candidate" else {("license_holders", target.id)}
                if target_type == "candidate" and target.member_id:
                    allowed_ids.add(("license_holders", target.member_id))
                if target_type == "member" and target.candidate_id:
                    allowed_ids.add(("candidates", target.candidate_id))
                if (log.linked_table, log.linked_id) not in allowed_ids:
                    reasons.append("발급번호 이력이 다른 대상자와 연결돼 있습니다.")
    return {"allowed": not reasons, "reasons": reasons, "ledger_id": ledger_id,
            "target_type": target_type, "target_id": target_id,
            "document_number": getattr(row, "document_number", None), "writes_enabled": writes_enabled()}


def apply_certificate_relink(db: Session, ledger_id: int, target_type: str, target_id: int,
                             reason: str, actor: str) -> dict:
    if not writes_enabled():
        raise ValueError("쓰기 기능이 비활성화되어 있습니다. 백업·복원 검증 후 관리자가 설정해야 합니다.")
    if len((reason or "").strip()) < 10:
        raise ValueError("재연결 사유를 10자 이상 입력하세요.")
    try:
        row = db.query(clm.CertificateIssuanceLedger).filter(clm.CertificateIssuanceLedger.id == ledger_id).with_for_update().first()
        preview = preview_certificate_relink(db, ledger_id, target_type, target_id)
        if not preview["allowed"]:
            raise ValueError("자격증명 재연결 차단: " + " / ".join(preview["reasons"]))
        target = db.get(models.Candidate if target_type == "candidate" else models.LicenseHolder, target_id)
        old = (row.candidate_id, row.member_id)
        row.candidate_id = target.id if target_type == "candidate" else getattr(target, "candidate_id", None)
        row.member_id = (target.member_id if target_type == "candidate" else target.id)
        row.latest_operator = actor
        from app.services.certificate_ledger_service import add_history
        add_history(db, row.id, "연결대상변경", row.status, row.status, actor,
                    f"수동 확인: {reason}; ID {old} -> {(row.candidate_id, row.member_id)}")
        # 발급번호 원장은 기존 소유권을 훼손하지 않고, '예약' 상태인 경우에만 사용중으로 전환한다.
        number = _cert(row.document_number)
        log = db.query(models.CertificateNumberLog).filter(
            models.CertificateNumberLog.certificate_number == number).first()
        if log is None:
            # '26-085' 같은 기존 표기 차이 허용
            yy, nval = number.split("-", 1)
            nint = int(nval)
            variants = list(dict.fromkeys([f"{yy}-{nint}", f"{yy}-{nint:02d}",
                                           f"{yy}-{nint:03d}", f"{yy}-{nint:04d}"]))
            log = next(x for x in db.query(models.CertificateNumberLog).filter(
                or_(*(models.CertificateNumberLog.certificate_number.like(f"{v}%") for v in variants))).all()
                if _cert(x.certificate_number) == number)
        if log.status == "issued":
            log.status = "used"
            log.linked_table = "license_holders" if target_type == "member" else "candidates"
            log.linked_id = target.id
            log.target_name = target.name or ""
            log.vehicle_number = target.vehicle_number or ""
        db.flush()
        db.commit()
        return {"ledger_id": row.id, "old_candidate_id": old[0], "old_member_id": old[1],
                "candidate_id": row.candidate_id, "member_id": row.member_id, "status": row.status}
    except Exception:
        db.rollback()
        raise


def certificate_relink_candidates(db: Session, limit: int = 100, offset: int = 0) -> dict:
    """미연결 발급대장을 페이지 단위로 읽고 단독 후보를 추린다. 어떠한 쓰기도 하지 않는다."""
    from sqlalchemy import or_
    # 미등록 예정자의 member_id=NULL은 정상. 이미 인가된 예정자만 회원 미연결 점검 대상.
    q = db.query(clm.CertificateIssuanceLedger).outerjoin(
        models.Candidate, models.Candidate.id == clm.CertificateIssuanceLedger.candidate_id).filter(
        clm.CertificateIssuanceLedger.deleted_at.is_(None),
        or_(clm.CertificateIssuanceLedger.candidate_id.is_(None),
            and_(clm.CertificateIssuanceLedger.member_id.is_(None), models.Candidate.is_registered.is_(True))))
    count = q.count()
    items = []
    for row in q.order_by(clm.CertificateIssuanceLedger.id).offset(offset).limit(limit):
        n = _cert(row.document_number)
        if not n:
            items.append({"ledger_id": row.id, "document_number": row.document_number or "", "result": "보류", "reason": "발급번호 없음"})
            continue
        matches = []
        for typ, model in [("candidate", models.Candidate), ("member", models.LicenseHolder)]:
            # 표준형의 대시/0채움 표기 차이를 고려하되, 번호가 있는 건만 비교한다.
            # 전체 DB 대신 같은 연도/번호 접두어만 좁혀서 검사한다.
            yy = n.split("-", 1)[0]
            yy, nval = n.split("-", 1)
            nint = int(nval)
            variants = list(dict.fromkeys([f"{yy}-{nint}", f"{yy}-{nint:02d}",
                                           f"{yy}-{nint:03d}", f"{yy}-{nint:04d}"]))
            query = db.query(model).filter(model.deleted_at.is_(None),
                or_(*(model.certificate_number.like(f"{v}%") for v in variants)))
            for x in query.all():
                if _cert(x.certificate_number) == n:
                    p = preview_certificate_relink(db, row.id, typ, x.id)
                    if p["allowed"]:
                        matches.append({"type": typ, "id": x.id})
        # 회원-예정자가 연결된 동일인인 경우를 중복 후보로 세지 않는다.
        if len(matches) == 2 and {x['type'] for x in matches} == {"candidate", "member"}:
            c = db.get(models.Candidate, next(x['id'] for x in matches if x['type'] == 'candidate'))
            m = db.get(models.LicenseHolder, next(x['id'] for x in matches if x['type'] == 'member'))
            if c and m and (c.member_id == m.id or m.candidate_id == c.id):
                matches = [next(x for x in matches if x['type'] == 'member')]
        items.append({"ledger_id": row.id, "document_number": row.document_number or "",
                      "result": "연결 가능" if len(matches) == 1 else "보류",
                      "target": matches[0] if len(matches) == 1 else None,
                      "reason": "" if len(matches) == 1 else ("검증 가능한 대상 없음" if not matches else "후보자 중복")})
    return {"total": count, "offset": offset, "limit": limit, "items": items, "read_only": True}
