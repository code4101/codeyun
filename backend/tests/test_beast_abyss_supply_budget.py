import pytest
from fractions import Fraction

from backend.core.fanxiu.data_annotation.tasks.beast_abyss_supply import required_beast_explore_stock
from backend.core.fanxiu.activity.exchange_challenge_planning import required_exchange_resource_stock


def test_supply_funds_only_one_whole_batch_after_free_points():
    def stock(points):
        return required_beast_explore_stock(explores=100, points=points,
                                           points_per_item=10, cost_per_explore=3)
    assert stock(300) == 0
    assert stock(299) == 1
    assert stock(19) == 29
    assert stock(0) == 30
    with pytest.raises(ValueError):
        required_beast_explore_stock(explores=100, points=0, points_per_item=0, cost_per_explore=3)


def test_shared_resource_budget_rounds_up_fractional_consumption():
    assert required_exchange_resource_stock(attempts=109, free_points=0,
        points_per_item=10, points_per_attempt=Fraction(1, 80)) == 1
    assert required_exchange_resource_stock(attempts=109, free_points=2,
        points_per_item=10, points_per_attempt=Fraction(1, 80)) == 0
    assert required_exchange_resource_stock(attempts=109, free_points=0,
        points_per_item=10, points_per_attempt=Fraction(3, 10)) == 4
    with pytest.raises(ValueError):
        required_exchange_resource_stock(attempts=109, free_points=0,
            points_per_item=10, points_per_attempt=Fraction(0))
