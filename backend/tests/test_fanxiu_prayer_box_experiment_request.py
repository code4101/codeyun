"""研究请求的纯输入契约；不模拟游戏、识别或点击。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_box_experiment import (
    validate_prayer_box_experiment_request, validate_choice_research_selection,
)


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


@pytest.mark.parametrize('choice_key', [None, True, 100, '', '0', '-1', '01', '１'])
def test_choice_reward_identity_is_explicit(choice_key):
    with pytest.raises(ValueError):
        validate_prayer_box_experiment_request(dict(base_id=1, open_quantity=1,
            prayer_round_targets={'淬体': {'1': 100000}}, choice_key=choice_key))


def test_choice_research_is_single_box():
    payload = dict(base_id=1, open_quantity=1, prayer_round_targets={'淬体': {'1': 100000}}, choice_key='100')
    validate_prayer_box_experiment_request(payload)
    with pytest.raises(ValueError, match='一箱'):
        validate_prayer_box_experiment_request({**payload, 'open_quantity': 2})


def test_choice_plan_requires_current_version_and_matching_category():
    rule = dict(base_id=1, catalog_fingerprint='current')
    option = dict(choice_key='100', prayer_category='淬体', experiment_open_count=1, count_per_box=5)
    plan = dict(base_id=1, catalog_fingerprint='current', options=[option])
    child = dict(base_id=100, classification_complete=True, allocation_categories=['淬体'], resource_categories=['淬体'])
    assert validate_choice_research_selection(rule, plan, choice_key='100', prayer_category='淬体',
        selected_reward_rule=child, count_per_box=5) == option
    for rejected_plan, key, category in [
        (None, '100', '淬体'),
        ({**plan, 'catalog_fingerprint': 'old'}, '100', '淬体'),
        ({**plan, 'base_id': 2}, '100', '淬体'),
        (plan, '101', '淬体'),
        (plan, '100', '仙花'),
        ({**plan, 'options': [{**option, 'prayer_category': '炼丹'}]}, '100', '炼丹'),
        ({**plan, 'options': [{**option, 'experiment_open_count': 2}]}, '100', '淬体'),
    ]:
        with pytest.raises(ValueError):
            validate_choice_research_selection(rule, rejected_plan, choice_key=key, prayer_category=category,
                selected_reward_rule=child, count_per_box=5)
    # 根卡指纹相同不能授予变化后的奖励子卡或数量消费资格。
    for rejected_child, count in [
        ({**child, 'base_id': 101}, 5),
        ({**child, 'classification_complete': False}, 5),
        ({**child, 'allocation_categories': ['仙花']}, 5),
        ({**child, 'allocation_categories': ['淬体', '仙花']}, 5),
        (child, 6), (child, True),
    ]:
        with pytest.raises(ValueError, match='子卡或数量'):
            validate_choice_research_selection(rule, plan, choice_key='100', prayer_category='淬体',
                selected_reward_rule=rejected_child, count_per_box=count)
