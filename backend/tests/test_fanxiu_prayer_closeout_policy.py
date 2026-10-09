"""祈愿收尾30%门槛的纯算法契约，不模拟游戏执行。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import plan_prayer_closeout


def _progress(score):
    return {"complete": True, "current_round": 4, "total_rounds": 5,
            "current_score": score, "round_target_score": 160000,
            "round_score_gap": 160000 - score}


@pytest.mark.parametrize('score', [0, 26674, 47999, 48000])
def test_at_or_below_thirty_percent_is_idempotent_success(score):
    r = plan_prayer_closeout(_progress(score))
    assert (r['action'], r['result'], r['outcome'], r['additional_score']) == (
        'complete', 'success', 'skipped_low_progress', 0)
    assert r['threshold_score'] == 48000
    assert plan_prayer_closeout(_progress(score)) == r


def test_just_over_threshold_only_tops_up_current_round():
    r = plan_prayer_closeout(_progress(48001))
    assert (r['action'], r['additional_score']) == ('top_up', 111999)
    assert r['stop_after_current_round'] is True
    new_round = {**_progress(0), 'current_round': 5}
    assert plan_prayer_closeout(new_round)['action'] == 'complete'


def test_already_full_is_success_without_extra_spending():
    r = plan_prayer_closeout(_progress(160000))
    assert (r['outcome'], r['additional_score']) == ('already_full', 0)


def test_incomplete_observation_is_not_a_skip_success():
    with pytest.raises(ValueError, match='完整'):
        plan_prayer_closeout({**_progress(26674), 'complete': False})


@pytest.mark.parametrize('score', [0, 22172, 48000])
def test_alchemy_fills_all_rounds_even_below_old_threshold(score):
    r = plan_prayer_closeout(_progress(score),prayer_category='炼丹')
    assert r['action']=='top_up'
    assert r['additional_score']==160000-score
    assert r['stop_after_current_round'] is False
    assert r['threshold_percent']==0


def test_other_categories_keep_current_round_threshold():
    r=plan_prayer_closeout(_progress(22172),prayer_category='淬体')
    assert r['outcome']=='skipped_low_progress'
    assert r['stop_after_current_round'] is True


@pytest.mark.parametrize('field,value', [
    ('current_score', 160000.9), ('round_target_score', 160000.9),
    ('round_score_gap', 0.9), ('current_round', 4.9),
    ('total_rounds', 5.9), ('current_round', True),
    ('current_score', '160000'), ('round_score_gap', None),
])
def test_unproven_numeric_values_cannot_complete_or_authorize_consumption(field, value):
    with pytest.raises(ValueError, match='完整整数'):
        plan_prayer_closeout({**_progress(160000), field: value}, prayer_category='炼丹')
