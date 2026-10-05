"""显式批量的数学和消耗边界；不模拟游戏完成。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import plan_prayer_closeout_batch


def plan(**changes):
    values=dict(additional_score=6000,expected_score_per_box=60,
        available_count=1000,remaining_budget=500)
    return plan_prayer_closeout_batch(**(values | changes))


def test_default_one_and_explicit_batch_are_estimates_with_hard_consumption_caps():
    assert plan()==1
    assert plan(maximum_batch=100)==100
    assert plan(maximum_batch=100,available_count=3)==3
    assert plan(maximum_batch=100,remaining_budget=2)==2
    assert plan(maximum_batch=100,additional_score=1)==1
    assert plan(maximum_batch=100,additional_score=119)==1
    assert plan(maximum_batch=100,expected_score_per_box=1e-320)==100


@pytest.mark.parametrize('changes',[
    {'maximum_batch':True},{'maximum_batch':0},{'remaining_budget':-1},
    {'available_count':0},{'additional_score':0},{'expected_score_per_box':float('nan')},
    {'expected_score_per_box':float('inf')},{'expected_score_per_box':0},
])
def test_missing_or_invalid_limits_do_not_authorize_consumption(changes):
    with pytest.raises(ValueError):plan(**changes)
