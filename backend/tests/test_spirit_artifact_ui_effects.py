import pytest

from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
from backend.core.fanxiu.instrumentation.spirit_artifact_ui import bind_spirit_artifact_ui_effects, locate_spirit_artifact_name


def test_vertical_name_uses_detector_order_and_excludes_following_badge():
    tokens = [{'parent_line_id': 'name', 'order': i, 'text': char,
               'x': 100, 'y': 200 + i * 40, 'w': 30, 'h': 40}
              for i, char in enumerate('弥罗宝光幢闪避')]
    assert locate_spirit_artifact_name(list(reversed(tokens)), ('弥罗宝光幢',)) == (115, 300)
    assert locate_spirit_artifact_name(tokens, ('血晶摩河剑',)) is None


def test_vertical_name_rejects_two_matching_cards():
    tokens = [{'parent_line_id': str(i), 'order': 0, 'text': '弥罗宝光幢',
               'x': i * 100, 'y': 200, 'w': 30, 'h': 200} for i in range(2)]
    with pytest.raises(FanxiuRuntimeMemoryError):
        locate_spirit_artifact_name(tokens, ('弥罗宝光幢',))


def test_vertical_name_edit_distance_excludes_following_badge():
    tokens = [{'parent_line_id': 'name', 'order': i, 'text': char,
               'x': 100, 'y': 200 + i * 40, 'w': 30, 'h': 40}
              for i, char in enumerate('青瞑岁月灯减伤')]
    assert locate_spirit_artifact_name(tokens, ('青暝岁月灯',)) == (115, 300)


def test_fuzzy_vertical_name_does_not_choose_tied_cards():
    tokens = [{'parent_line_id': str(i), 'order': 0, 'text': '青瞑岁月灯',
               'x': i * 100, 'y': 200, 'w': 30, 'h': 200} for i in range(2)]
    assert locate_spirit_artifact_name(tokens, ('青暝岁月灯',)) is None


def test_lock_reply_updates_state_without_reordering_ui_rows():
    rows = [{'row': 0, 'cleanse_id': 2, 'locked': True}, {'row': 1, 'cleanse_id': 1, 'locked': False}]
    committed = [{'cleanse_id': 1, 'locked': False}, {'cleanse_id': 2, 'locked': False}]
    assert bind_spirit_artifact_ui_effects(rows, committed) == [
        {'row': 0, 'cleanse_id': 2, 'locked': False}, {'row': 1, 'cleanse_id': 1, 'locked': False}]
    assert rows[0]['locked'] is True


@pytest.mark.parametrize('rows,committed', [
    ([{'row': 0, 'cleanse_id': 1}], [{'cleanse_id': 2}]),
    ([{'row': 0, 'cleanse_id': 1}, {'row': 1, 'cleanse_id': 1}], [{'cleanse_id': 1}]),
    ([{'row': 0, 'cleanse_id': 1}], [{'cleanse_id': 1}, {'cleanse_id': 1}]),
])
def test_rejects_stale_or_ambiguous_attribute_binding(rows, committed):
    with pytest.raises(FanxiuRuntimeMemoryError):
        bind_spirit_artifact_ui_effects(rows, committed)
