import pytest

from backend.core.fanxiu.runtime_gui.storage_bag_alignment import StorageBagScrollProgress


def test_deep_registered_list_keeps_advancing_past_total_gesture_limit():
    budget = StorageBagScrollProgress(12)
    for count in range(40):
        assert budget.allows(remaining_items=400 - 8 * count, total_scrolls=count)


def test_same_or_worsening_distance_exhausts_one_anchor_budget():
    budget = StorageBagScrollProgress(12)
    for count in range(12):
        assert budget.allows(remaining_items=80 + count % 2, total_scrolls=count)
    assert not budget.allows(remaining_items=80, total_scrolls=12)
    assert not budget.allows(remaining_items=81, total_scrolls=13)


def test_only_new_closest_distance_renews_stalled_budget():
    budget = StorageBagScrollProgress(2)
    assert budget.allows(remaining_items=80, total_scrolls=0)
    assert budget.allows(remaining_items=72, total_scrolls=1)
    assert budget.allows(remaining_items=80, total_scrolls=2)
    assert not budget.allows(remaining_items=72, total_scrolls=3)
    assert budget.allows(remaining_items=64, total_scrolls=4)


def test_zero_allowance_prohibits_scrolling():
    assert not StorageBagScrollProgress(0).allows(remaining_items=80, total_scrolls=0)


@pytest.mark.parametrize('remaining,count', [(-1, 0), (1, -1)])
def test_invalid_registration_distance_or_count_rejected(remaining, count):
    with pytest.raises(ValueError):
        StorageBagScrollProgress(12).allows(remaining_items=remaining, total_scrolls=count)
