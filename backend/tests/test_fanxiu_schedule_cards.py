"""Pure title identity contracts; GUI acceptance runs against the real game."""
import pytest
from datetime import datetime

from backend.core.fanxiu.data_annotation.schedule_cards import (
    align_schedule_card_title, align_schedule_card_sequence, wait_schedule_card,
    schedule_panel_entity,
)


def test_exact_panel_admission_survives_scoring_end_but_not_panel_close():
    now = datetime.fromisoformat('2026-10-02T04:00:00+08:00')
    row = {'activityId': 16090001, 'id': 16090001400004,
           'prepareEndTime': 1790733600000, 'startTime': 1790820000000,
           'endTime': 1790863200000, 'closePanelTime': 1791129539000}
    schedule = {'available': True, 'complete': True, 'items': [row]}
    assert schedule_panel_entity(schedule, activity_id=16090001,
                                 runtime_id='16090001400004', now=now) is row
    for moment in ('2026-09-30T09:00:00+08:00', '2026-10-04T23:58:59+08:00'):
        with pytest.raises(RuntimeError):
            schedule_panel_entity(schedule, activity_id=16090001,
                                  runtime_id='16090001400004',
                                  now=datetime.fromisoformat(moment))
    with pytest.raises(RuntimeError):
        schedule_panel_entity(schedule, activity_id=16090001,
                              runtime_id='next-edition', now=now)
    with pytest.raises(RuntimeError):
        schedule_panel_entity({**schedule, 'items': [row, row]},
                              activity_id=16090001, runtime_id='16090001400004', now=now)


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


def test_stable_card_accepts_ocr_variants_of_same_resolved_identity(monkeypatch):
    import backend.core.fanxiu.data_annotation.schedule_cards as cards

    observed = iter([
        {'status': 'aligned', 'title': '丹道问鼎跨服[8]', 'pager_index': 5,
         'task': {'key': '8043101:8043101400004'}},
        {'status': 'aligned', 'title': '丹道问鼎跨服[8', 'pager_index': 5,
         'task': {'key': '8043101:8043101400004'}},
    ])
    monkeypatch.setattr(cards, 'read_schedule_card', lambda context, snapshot: next(observed))

    class Context:
        def wait_action_settle(self, seconds):
            if False:
                yield seconds

    iterator = wait_schedule_card(Context(), {}, expected_key='8043101:8043101400004')
    with pytest.raises(StopIteration) as result:
        next(iterator)
    assert result.value.value['title'] == '丹道问鼎跨服[8'


def test_neighbors_resolve_occluded_origin_and_duplicate_names():
    from backend.core.fanxiu.data_annotation.schedule_cards import align_schedule_card_neighbors
    snapshot = inventory('论道', '洞天福地', '论道', '道法争锋')
    assert align_schedule_card_neighbors({0: '论道', -1: '洞天福地'}, snapshot)['task']['key'] == '2'
    assert align_schedule_card_neighbors({0: '', -1: '洞天福地', 1: '道法争锋'}, snapshot)['task']['key'] == '2'
    assert align_schedule_card_neighbors({0: '', 1: '道法争锋'}, snapshot)['status'] == 'insufficient'
    assert align_schedule_card_neighbors({0: '洞天福地', -1: '洞天福地', 1: '道法争锋'}, snapshot)['status'] == 'insufficient'


def test_live_occluded_card_resolves_from_noisy_right_and_left_neighbors():
    from backend.core.fanxiu.data_annotation.schedule_cards import align_schedule_card_neighbors
    snapshot = inventory('论道', '万仙伐劫', '联盟灵脉争夺', '秘境封魔杀')
    result = align_schedule_card_neighbors({0: '', 1: '击+联盟灵脉争夺', -1: '论道'}, snapshot)
    assert result['status'] == 'aligned'
    assert result['task']['key'] == '1'
