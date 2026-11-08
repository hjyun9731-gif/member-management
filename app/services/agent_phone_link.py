"""(대) 표기 연락처의 안전한 기존 대리인 재연결.

동일 회원의 현재 대리인 이력이 단 하나 확인될 때만 누락된 회원 미러 필드를 채움.
기존 전화번호 원문은 변경하지 않으며, 신규 대리인을 임의로 생성하지 않음.
"""
import re
from app import models
from app.services import agent_history

_TAGGED = re.compile(r"(01[016789][-.\s]?\d{3,4}[-.\s]?\d{4})\s*\(\s*대\s*\)")


def tagged_numbers(*values):
    return {re.sub(r"\D", "", m.group(1)) for value in values
            for m in _TAGGED.finditer(str(value or ""))}


def link_known_current_agent(db, member, *contact_values):
    """'linked'/'already_linked'/'review_required'/'not_applicable'."""
    tagged = tagged_numbers(*contact_values)
    if not tagged:
        return "not_applicable"
    if len(tagged) != 1:
        return "review_required"
    number = next(iter(tagged))
    # 전체 현재 대리인 이력을 함께 비교한다. 번호 공유 시 자동 연결 금지.
    current = db.query(models.MemberAgent).filter(
        models.MemberAgent.status == "현재",
        models.MemberAgent.deleted_at.is_(None),
    ).all()
    matches = [r for r in current if re.sub(r"\D", "", r.mobile or "") == number]
    if any(r.member_id != member.id for r in matches):
        return "review_required"
    if len(matches) != 1 or not (matches[0].name or "").strip():
        # 다른 회원의 같은 번호, 과거 대리인, 이름 없는 기록은 연결 근거가 아님.
        return "review_required"
    chosen = matches[0]
    existing_name = re.sub(r"\s+", "", member.agent_name or "")
    target_name = re.sub(r"\s+", "", chosen.name or "")
    existing_number = re.sub(r"\D", "", member.agent_mobile or "")
    if (existing_name and existing_name != target_name) or (existing_number and existing_number != number):
        return "review_required"
    if existing_name == target_name and existing_number == number:
        return "already_linked"
    # 기존 동일 회원·현재 대리인 기록만 회원 칼럼에 반영한다.
    agent_history._mirror_to_member(member, agent_history._row_dict(chosen))
    return "linked"
