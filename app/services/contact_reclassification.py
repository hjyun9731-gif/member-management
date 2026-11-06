"""(대) 표기 연락처의 대리인 이전을 위한 순수 검증 함수.

사용자가 명시적으로 확인하지 않은 상태에서는 절대 대리인 필드에 자동 이전하지 않는다.
현재 DB 스키마는 변경하지 않으며 변경이력은 회원 수정 경로가 생성한다.
"""
import re

from app.services.contact_parser import parse_contact, digits_only

_MARKED_AGENT_NUMBER = re.compile(
    r"(?<!\d)(01[016789][-\.\s]?\d{3,4}[-\.\s]?\d{4})\s*\(대\)(?!\d)"
)


def verified_agent_phone_move(old_mobile: str, new_mobile: str, agent_mobile: str) -> bool:
    """이전이 *정확히* (대) 번호 한 건만 옮기는지 검증한다.

    동일 이름이나 번호만으로 대리인 여부를 추정하지 않는다. 이 함수가 True라고
    실제 대리인이 증명되는 것은 아니며, 관리자 수동 확인을 전제한다.
    """
    raw = str(old_mobile or "")
    matches = list(_MARKED_AGENT_NUMBER.finditer(raw))
    if len(matches) != 1:
        return False
    match = matches[0]
    if digits_only(match.group(1)) != digits_only(agent_mobile):
        return False
    remain = (raw[:match.start()] + ' ' + raw[match.end():]).strip()
    before_rest = parse_contact(remain)
    after_rest = parse_contact(new_mobile)
    if before_rest.needs_review or after_rest.needs_review:
        return False
    return sorted(before_rest.digits_list) == sorted(after_rest.digits_list)
