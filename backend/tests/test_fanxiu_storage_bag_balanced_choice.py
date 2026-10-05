"""库存单位、有限额度和跨来源分配的纯数学契约。"""
import pytest

from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice import (
    BalancedBoxChoice, plan_storage_bag_balanced_choice,
)


def choice(i, count=1, weight=1, cap=None, unit="同级材料"):
    return BalancedBoxChoice(i,count,unit,weight,cap)


def plan(choices, inventory, quantity):
    return plan_storage_bag_balanced_choice(choices,inventory,quantity,non_prayer_verified=True)


def test_equal_start_distributes_boxes_instead_of_opening_all_for_one_target():
    inventory = {1:0,2:0,3:0}
    p = plan([choice(3),choice(2),choice(1)],inventory,10)
    assert p.projected_inventory == {1:4,2:3,3:3}
    assert sum(b.opened_count for b in p.batches) == 10
    assert p.remaining_boxes == 0
    assert inventory == {1:0,2:0,3:0}


def test_low_inventory_filled_before_high_inventory():
    p = plan([choice(1),choice(2)],{1:0,2:6},8)
    assert p.projected_inventory == {1:7,2:7}


def test_explicit_units_and_real_reward_quantities_drive_balance():
    p = plan([choice(1,count=2,weight=3),choice(2,count=3)],{1:1,2:0},4)
    assert p.projected_inventory == {1:3,2:9}
    assert p.projected_balance_units == {1:9,2:9}


def test_limits_leave_unallocated_boxes_without_inventing_availability():
    p = plan([choice(1,cap=1),choice(2,cap=0)],{1:0,2:0},4)
    assert p.remaining_boxes == 3
    assert p.projected_inventory == {1:1,2:0}


def test_next_source_uses_projected_inventory_and_preserves_unrelated_items():
    first = plan([choice(1),choice(2)],{1:0,2:0,99:123},3)
    second = plan([choice(1),choice(2)],first.projected_inventory,3)
    assert second.projected_inventory == {1:3,2:3,99:123}


def test_unknown_inventory_unit_and_prayer_qualification_rejected():
    with pytest.raises(ValueError,match="库存缺失"):
        plan([choice(1),choice(2)],{1:0},1)
    with pytest.raises(ValueError,match="不同资源单位"):
        plan([choice(1),choice(2,unit="稀有资源")],{1:0,2:0},1)
    with pytest.raises(ValueError,match="非祈愿资格"):
        plan_storage_bag_balanced_choice([choice(1)],{1:0},1,non_prayer_verified=False)


@pytest.mark.parametrize("choices,inventory,quantity",[
    ([choice(1),choice(1)],{1:0},1),
    ([choice(1,count=0)],{1:0},1),
    ([choice(1,weight=True)],{1:0},1),
    ([choice(1,cap=-1)],{1:0},1),
    ([choice(1)],{1:-1},1),
    ([choice(1)],{1:0},True),
])
def test_invalid_numeric_or_duplicate_inputs_rejected(choices,inventory,quantity):
    with pytest.raises(ValueError):
        plan(choices,inventory,quantity)
