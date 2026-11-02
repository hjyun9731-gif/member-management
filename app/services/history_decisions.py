"""원본을 보존하는 판정 추가 및 회원 수정 사유 검증."""
from fastapi import HTTPException
from app import models
from app.services.change_judgement import (
    NORMAL, REVIEW, FIRST, NOTATION, ERROR_FIX, DUPLICATE, JUDGEMENTS,
    RULE_VERSION, source_hash, classify_for_type, classify_plate, pick_report_date,
)

REASONS = {"actual", "correction", "supplement"}


def member_change_reasons(data, before):
    reasons = data.get("change_reasons") or {}
    if not isinstance(reasons, dict) or any(v not in REASONS for v in reasons.values()):
        raise HTTPException(422, "변경 사유는 actual/correction/supplement 중 선택하세요.")
    if "vehicle_number" in data:
        old, new = before.get("vehicle_number", ""), data["vehicle_number"]
        j, _ = classify_plate(old, new)
        if str(old or "").strip() != str(new or "").strip() and j == REVIEW:
            if reasons.get("vehicle_number") not in ("actual", "correction"):
                raise HTTPException(409, detail={"code": "PLATE_CHANGE_REASON_REQUIRED",
                    "message": "입력 오류정정 / 실제 번호변경 중 사유를 선택하세요. 대폐차는 차종 변경으로 판단합니다."})
    return reasons


def chosen_judgement(change_type, before, after, reason=None):
    j, why = classify_for_type(change_type, before, after)
    if j in (FIRST, NOTATION):
        return j, why
    if not str(after or "").strip() or str(after).strip() in ("-", "미상"):
        return REVIEW, "변경 후 값이 불완전하므로 실적 확인 필요"
    if reason == "correction":
        return ERROR_FIX, "사용자 선택: 입력 오류정정"
    if reason == "supplement":
        return FIRST, "사용자 선택: 정보보완"
    if reason == "actual":
        return NORMAL, "사용자 선택: 실제 변경"
    return j, why


def append_decision(db, row, *, judgement, status, report_decision, reason,
                    created_by, evidence=None, duplicate_of_id=None):
    if judgement not in JUDGEMENTS or status not in ("confirmed", "automatic", "pending"):
        raise HTTPException(422, "잘못된 판정 또는 판정상태입니다.")
    if report_decision not in ("include", "exclude", "hold") or not isinstance(reason, str) or not reason.strip():
        raise HTTPException(422, "실적 처리와 판정근거가 필요합니다.")
    if status == "pending" and report_decision != "hold":
        raise HTTPException(422, "보류 판정은 실적에 반영할 수 없습니다.")
    if judgement == REVIEW and report_decision != "hold":
        raise HTTPException(422, "확인필요 기록은 보류로 저장하세요.")
    if report_decision == "include":
        if judgement != NORMAL or not pick_report_date(row.change_date, row.receipt_date)[0]:
            raise HTTPException(422, "실적 포함은 유효한 업무일자의 정상변경만 가능합니다.")
        if row.change_type in ("대폐차", "차종변경"):
            if classify_for_type(row.change_type, row.before_value, row.after_value)[0] != NORMAL:
                raise HTTPException(422, "대폐차 실적에는 실질적인 차종 변경 근거가 필요합니다.")
    if judgement == DUPLICATE:
        if not duplicate_of_id or duplicate_of_id == row.id or report_decision != "exclude":
            raise HTTPException(422, "중복 제외에는 별도의 기준 이력 ID가 필요합니다.")
        target = db.get(models.ChangeHistory, duplicate_of_id)
        if not target or target.deleted_at is not None:
            raise HTTPException(422, "중복 기준 이력이 존재하지 않습니다.")
        if target.decisions and target.decisions[0].judgement == DUPLICATE:
            raise HTTPException(422, "중복 기준은 다른 중복 기록이 아닌 대표 원본이어야 합니다.")
    elif duplicate_of_id is not None:
        raise HTTPException(422, "중복 판정에만 기준 이력 ID를 지정할 수 있습니다.")
    db.flush()
    d = models.ChangeHistoryDecision(history_id=row.id, judgement=judgement, status=status,
        report_decision=report_decision, reason=reason.strip(), evidence=evidence or {},
        source_hash=source_hash(row), rule_version=RULE_VERSION, duplicate_of_id=duplicate_of_id,
        created_by=created_by or "unknown")
    db.add(d)
    return d
