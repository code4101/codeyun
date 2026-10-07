"""显式数量的库存边界；不模拟游戏交互。"""
import pytest
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import StorageBagRandomBoxRequest


def test_special_item_detail_mode_cannot_grant_consumption_permission():
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
        StorageBagRandomBoxGuiAdapter, StorageBagRandomBoxBlocked,
    )
    def forbidden(*args, **kwargs):
        raise AssertionError('Detail-only mode reached a side effect')
    adapter = StorageBagRandomBoxGuiAdapter(
        context=None, snapshot_reader=forbidden, catalog_cards_by_id={},
        recorder=forbidden, detail_scene_id=610, operation_template='special_use',
        detail_only=True,
    )
    with pytest.raises(StorageBagRandomBoxBlocked, match='仅详情'):
        next(adapter.execute(StorageBagRandomBoxRequest(20300001, 'owned', '拜师函', 1)))

@pytest.mark.parametrize("count", [1, 50, 150])
def test_explicit_open_count_within_owned_stack(count):
    assert StorageBagRandomBoxRequest(19070152, "owned", "花神宝匣", 150, open_quantity=count).requested_open_count == count

@pytest.mark.parametrize("count", [0, -1, 151, True])
def test_explicit_open_count_rejects_invalid_or_excess(count):
    with pytest.raises(ValueError):
        StorageBagRandomBoxRequest(19070152, "owned", "花神宝匣", 150, open_quantity=count).requested_open_count


def test_large_stack_budget_covers_real_pixel_residual_without_inventory_sized_clicks():
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import storage_bag_quantity_button_budget
    budget = storage_bag_quantity_button_budget(429408, 446)
    assert 77 <= budget < 1100
    assert storage_bag_quantity_button_budget(2, 446) <= 31


@pytest.mark.parametrize("maximum,width", [(0, 446), (True, 446), (100, 0), (100, float("nan"))])
def test_quantity_budget_rejects_unknown_range_or_geometry(maximum, width):
    from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import storage_bag_quantity_button_budget
    with pytest.raises(ValueError):
        storage_bag_quantity_button_budget(maximum, width)
