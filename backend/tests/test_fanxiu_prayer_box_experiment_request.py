"""研究请求的纯输入契约；不模拟游戏、识别或点击。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_box_experiment import validate_prayer_box_experiment_request


@pytest.mark.parametrize(('key', 'value'), [
    ('base_id', True), ('base_id', 0), ('base_id', '39032101'),
    ('open_quantity', False), ('open_quantity', -1), ('open_quantity', 1.5),
    ('prayer_round_targets', {}), ('prayer_round_targets', None),
    ('minimum_score_gap', True), ('minimum_score_gap', 0), ('minimum_score_gap', 1.5),
    ('allow_outer_container', 1),
])
def test_ambiguous_request_is_rejected_before_game_access(key, value):
    payload = {'base_id': 39032101, 'open_quantity': 3, 'prayer_round_targets': {'炼丹': {'4': 100000}}}
    payload[key] = value
    with pytest.raises(ValueError):
        validate_prayer_box_experiment_request(payload)


def test_explicit_research_request():
    validate_prayer_box_experiment_request({
        'base_id': 39032101, 'open_quantity': 3, 'prayer_round_targets': {'炼丹': {'4': 100000}}})
