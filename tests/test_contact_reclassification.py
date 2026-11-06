"""회원 연락처 수정 시 (대) 번호를 자의적으로 삭제·전환하지 않게 하는 검증."""
from app.services.contact_reclassification import verified_agent_phone_move


def test_manual_classification_exact_annotated_phone():
    assert verified_agent_phone_move(
        '010-6748-0662 010-5365-4008(대)',
        '010-6748-0662', '010-5365-4008'
    )


def test_no_move_without_marked_number():
    assert not verified_agent_phone_move('010-6748-0662 010-5365-4008', '010-6748-0662', '010-5365-4008')


def test_cannot_move_other_number():
    assert not verified_agent_phone_move('010-6748-0662 010-5365-4008(대)', '010-6748-0662', '010-6748-0662')


def test_cannot_drop_unrelated_phone_on_move():
    assert not verified_agent_phone_move('010-6748-0662 010-5365-4008(대)', '', '010-5365-4008')


def test_cannot_keep_annotated_number_in_member_and_claim_move():
    assert not verified_agent_phone_move('010-6748-0662 010-5365-4008(대)', '010-6748-0662 010-5365-4008(대)', '010-5365-4008')


def test_unknown_annotation_does_not_allow_automatic_classification():
    assert not verified_agent_phone_move('010-6748-0662 010-5365-4008(가족)', '010-6748-0662', '010-5365-4008')
