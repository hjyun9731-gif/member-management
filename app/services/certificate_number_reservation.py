"""자격증명발급번호 예약/재호출 안전장치.

같은 대상에서 발급번호 부여 버튼을 반복해서 눌러도 새 번호를 계속 소비하지 않도록
서버에서 최종 방어한다. 기존 채번 함수/카운터는 그대로 사용하고, 예약 대상 정보만
certificate_number_logs와 발급대장에 연결한다.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app import certificate_ledger_models as ledger_models
from app import crud, models


def _norm_vehicle(value: str) -> str:
    return re.sub(r"[\s-]+", "", str(value or "")).strip()


def _same_subject(name_a: str, vehicle_a: str, name_b: str, vehicle_b: str) -> bool:
    na, nb = (name_a or "").strip(), (name_b or "").strip()
    va, vb = _norm_vehicle(vehicle_a), _norm_vehicle(vehicle_b)
    return bool(na and nb and va and vb and na == nb and va == vb)


def _ensure_waiting_ledger_for_reservation(
    db: Session, certificate_number: str, name: str, vehicle_number: str, actor: str
) -> None:
    """저장 전 예약도 발급대장에서 바로 보이도록 최소 행을 보장한다.

    후보자 저장 시 ensure_candidate_ledger가 이 행을 candidate_id에 재연결한다.
    """
    cert = crud.normalize_certificate_number(certificate_number)
    if not cert or not (name or "").strip() or not _norm_vehicle(vehicle_number):
        return
    yy, no = cert.split("-", 1)
    try:
        n = int(no)
        variants = list(dict.fromkeys([f"{yy}-{n}", f"{yy}-{n:02d}", f"{yy}-{n:03d}", f"{yy}-{n:04d}"]))
    except Exception:
        variants = [cert]

    row = (
        db.query(ledger_models.CertificateIssuanceLedger)
        .filter(ledger_models.CertificateIssuanceLedger.document_number.in_(variants))
        .order_by(ledger_models.CertificateIssuanceLedger.id.desc())
        .first()
    )
    if row:
        # 이미 실제 회원/예정자에 연결된 행이면 대상정보를 덮어쓰지 않는다.
        if row.candidate_id or row.member_id:
            return
        if not _same_subject(row.name or "", row.vehicle_number or "", name, vehicle_number):
            return
        row.deleted_at = None
        row.document_number = cert
        row.name = (name or "").strip()
        row.vehicle_number = vehicle_number or ""
        row.status = "인가대기"
        row.latest_operator = actor or row.latest_operator
        return

    db.add(
        ledger_models.CertificateIssuanceLedger(
            candidate_id=None,
            member_id=None,
            region="",
            vehicle_number=vehicle_number or "",
            name=(name or "").strip(),
            qualification_number="",
            document_number=cert,
            approval_date="",
            certificate_issue_date="",
            status="인가대기",
            latest_operator=actor or "시스템",
            created_by=actor or "시스템",
            approved_at=None,
            issued_at=None,
        )
    )
    try:
        db.flush()
    except Exception:
        # 번호 채번 자체는 advisory lock으로 이미 안전하게 하나만 나갔지만,
        # 그 다음 단계인 이 발급대장 placeholder 행 생성은 잠금 밖에서 이뤄지므로
        # 두 요청이 "같은 번호를 막 새로 받은" 순간에는 서로 아직 상대방의 행을
        # 보지 못한 채 동시에 INSERT를 시도할 수 있다. UNIQUE 제약 충돌이면
        # 상대방이 이미 만든 것이므로 롤백 후 그 행을 찾아 갱신한다(새로 만들지 않음).
        db.rollback()
        row = (
            db.query(ledger_models.CertificateIssuanceLedger)
            .filter(ledger_models.CertificateIssuanceLedger.document_number.in_(variants))
            .order_by(ledger_models.CertificateIssuanceLedger.id.desc())
            .first()
        )
        if row and not row.candidate_id and not row.member_id:
            row.deleted_at = None
            row.document_number = cert
            row.name = (name or "").strip()
            row.vehicle_number = vehicle_number or ""
            row.status = "인가대기"
            row.latest_operator = actor or row.latest_operator


def get_or_reserve_certificate_number(
    db: Session,
    *,
    issued_by: str = "",
    current_number: str = "",
    candidate_id: Optional[int] = None,
    name: str = "",
    vehicle_number: str = "",
) -> str:
    """동일 대상의 재호출이면 기존 번호를 반환하고, 최초 호출에서만 새 번호를 만든다."""
    actor = (issued_by or "").strip()
    name = (name or "").strip()
    vehicle_number = (vehicle_number or "").strip()

    # 새 번호는 반드시 실제 대상을 식별할 수 있을 때만 예약한다.
    # 과거에는 빈 입력폼에서도 버튼만 누르면 26-374, 26-375처럼 대상 없는 번호가
    # 계속 소비되었다. 서버에서 막아 브라우저 우회/중복 호출에도 번호가 나가지 않게 한다.
    if not candidate_id and not crud.normalize_certificate_number(current_number or ""):
        if not name or not _norm_vehicle(vehicle_number):
            raise ValueError("성명과 차량번호를 먼저 입력한 뒤 발급번호를 부여하세요.")

    # "이미 예약된 번호가 있는지 확인 -> 없으면 새로 채번"을 하나의 잠금 범위로 묶는다.
    # advisory lock을 여기서 먼저 잡지 않으면, 완전히 새로운 대상에 대해 두 요청이
    # 동시에 들어왔을 때 둘 다 "기존 예약 없음"을 보고 각자 새 번호를 채번해버리는
    # 경쟁이 있었다(예: 동시 클릭 시 26-375/26-376처럼 서로 다른 번호 두 개 발급).
    # get_next_certificate_number 내부에서도 같은 잠금을 다시 거는데, Postgres
    # advisory xact lock은 같은 세션(트랜잭션) 안에서는 재진입 가능하므로 안전하다.
    crud.lock_certificate_number_sequence(db)

    # 수정 화면은 DB에 이미 저장된 예정자의 번호를 최우선으로 재사용한다.
    if candidate_id:
        cand = (
            db.query(models.Candidate)
            .filter(models.Candidate.id == candidate_id, models.Candidate.deleted_at.is_(None))
            .first()
        )
        if cand:
            name = name or (cand.name or "")
            vehicle_number = vehicle_number or (cand.vehicle_number or "")
            existing = crud.normalize_certificate_number(cand.certificate_number or "")
            if existing:
                return existing

    # 브라우저 입력칸에 이미 번호가 있으면 서버에서도 새 채번을 절대 하지 않는다.
    current = crud.normalize_certificate_number(current_number or "")
    if current:
        log = crud.get_certificate_number_log(db, current)
        usage = crud._scan_certificate_number_usage(db, current)
        if log and log.status == "cancelled":
            raise ValueError(f"자격증명발급번호 {current}는 취소된 번호입니다.")
        if usage:
            tname, lid, uname, uvehicle = usage
            same_id = bool(candidate_id and tname == "candidates" and int(lid) == int(candidate_id))
            if not same_id and name and vehicle_number and not _same_subject(uname, uvehicle, name, vehicle_number):
                raise ValueError(f"자격증명발급번호 {current}는 다른 대상자가 이미 사용 중입니다.")
        if log:
            # 이전 버전에서 대상 없이 발급만 된 번호라면 현재 폼의 대상정보를 보강한다.
            if name and vehicle_number and log.status == "issued":
                if not log.target_name and not log.vehicle_number:
                    log.target_name = name
                    log.vehicle_number = vehicle_number
                    db.commit()  # 아래 ledger insert 충돌로 롤백해도 이 변경은 보존되게 먼저 커밋
                elif not _same_subject(log.target_name or "", log.vehicle_number or "", name, vehicle_number):
                    raise ValueError(f"자격증명발급번호 {current}는 다른 대상자에게 예약되어 있습니다.")
                _ensure_waiting_ledger_for_reservation(db, current, name, vehicle_number, actor)
                db.commit()
            return current
        if usage:
            return current
        raise ValueError(f"자격증명발급번호 {current}의 발급 이력을 찾을 수 없습니다.")

    # 저장 전 동일 성명+차량번호에 이미 '미사용 발급' 번호가 있으면 그 번호를 재사용한다.
    if name and vehicle_number:
        recent = (
            db.query(models.CertificateNumberLog)
            .filter(
                models.CertificateNumberLog.status == "issued",
                models.CertificateNumberLog.target_name == name,
            )
            .order_by(models.CertificateNumberLog.id.desc())
            .limit(20)
            .all()
        )
        for log in recent:
            if _same_subject(log.target_name or "", log.vehicle_number or "", name, vehicle_number):
                cert = crud.normalize_certificate_number(log.certificate_number or "")
                if cert:
                    _ensure_waiting_ledger_for_reservation(db, cert, name, vehicle_number, actor)
                    db.commit()
                    return cert

    # 진짜 최초 요청일 때만 기존의 동시성 안전 채번 함수를 호출한다.
    # target_name/vehicle_number를 채번과 같은 트랜잭션에 함께 저장해야, 바로 위
    # "이미 예약된 번호가 있는지" 조회가 그 사이(커밋~커밋)의 좁은 틈에서 아직 비어있는
    # target_name을 보고 놓치는 일 없이, 동시 요청이 같은 대상에게 서로 다른 번호를
    # 발급하는 경쟁을 확실히 막는다.
    cert = crud.get_next_certificate_number(
        db, issued_by=actor or None, target_name=name or "", vehicle_number=vehicle_number or "",
    )
    if name and vehicle_number:
        _ensure_waiting_ledger_for_reservation(db, cert, name, vehicle_number, actor)
        db.commit()
    return cert
