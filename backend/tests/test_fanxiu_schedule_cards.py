"""Pure title identity contracts; GUI acceptance runs against the real game."""
import pytest

from backend.core.fanxiu.data_annotation.schedule_cards import (
    align_schedule_card_title, align_schedule_card_sequence,
)


def inventory(*titles):
    return {'complete': True, 'items': [
        {'key': str(index), 'title': title} for index, title in enumerate(titles)
    ]}


@pytest.mark.parametrize('observed', ['社团灵庞跨服[4]', '社 团 灵 宠 跨服【4】', '社团灵宠跨服4'])
def test_noisy_title_aligns_without_exact_match(observed):
    result = align_schedule_card_title(observed, inventory('洞天福地', '社团灵宠跨服[4]', '道法争锋'))
    assert result['status'] == 'aligned'
    assert result['task']['key'] == '1'


def test_explicit_cross_conflict_cannot_pass_on_shared_name():
    result = align_schedule_card_title('魔道入侵跨服[16]', inventory('魔道入侵跨服[4]'))
    assert result['status'] == 'insufficient'


def test_equal_names_preserve_ambiguity_instead_of_first_wins():
    result = align_schedule_card_title('魔道入侵', inventory('魔道入侵', '魔道入侵'))
    assert result['status'] == 'ambiguous'
    assert result['task'] is None


@pytest.mark.parametrize('title', ['', '奖励预览', '前往参与'])
def test_absent_title_or_button_is_not_identity(title):
    assert align_schedule_card_title(title, inventory('论道', '仙缘大比跨服[16]'))['status'] == 'insufficient'


def test_partial_runtime_cannot_authorize_unique_match():
    snapshot = inventory('论道')
    snapshot['complete'] = False
    assert align_schedule_card_title('论道', snapshot)['status'] == 'incomplete_runtime'


def test_adjacent_sequence_resolves_both_a_positions():
    snapshot = inventory('a', 'b', 'a', 'c')
    assert align_schedule_card_sequence(['a'], snapshot)['status'] == 'ambiguous'
    assert align_schedule_card_sequence(['a', 'b'], snapshot)['task']['key'] == '0'
    assert align_schedule_card_sequence(['a', 'c'], snapshot)['task']['key'] == '2'


def test_lookahead_can_cross_end_and_tolerate_noisy_next_title():
    snapshot = inventory('洞天福地', '论道', '道法争锋', '论道')
    result = align_schedule_card_sequence(['论道', '洞天福池'], snapshot)
    assert result['status'] == 'aligned'
    assert result['task']['key'] == '3'


def test_periodic_sequence_remains_ambiguous_after_complete_cycle():
    result = align_schedule_card_sequence(['a', 'b', 'a', 'b'], inventory('a', 'b', 'a', 'b'))
    assert result['status'] == 'ambiguous'
    assert result['task'] is None


def test_missing_qualifier_needs_sequence_not_local_version_guess():
    snapshot = inventory('魔道入侵', '论道', '魔道入侵跨服[4]', '洞天福地')
    assert align_schedule_card_title('魔道入侵', snapshot)['status'] == 'ambiguous'
    assert align_schedule_card_sequence(['魔道入侵', '洞天福地'], snapshot)['task']['key'] == '2'
    assert align_schedule_card_title('魔道入侵跨服[4]', snapshot)['task']['key'] == '2'
