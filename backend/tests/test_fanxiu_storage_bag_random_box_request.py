"""显式数量的库存边界；不模拟游戏交互。"""
import pytest
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import StorageBagRandomBoxRequest

@pytest.mark.parametrize("count", [1, 50, 150])
def test_explicit_open_count_within_owned_stack(count):
    assert StorageBagRandomBoxRequest(19070152, "owned", "花神宝匣", 150, open_quantity=count).requested_open_count == count

@pytest.mark.parametrize("count", [0, -1, 151, True])
def test_explicit_open_count_rejects_invalid_or_excess(count):
    with pytest.raises(ValueError):
        StorageBagRandomBoxRequest(19070152, "owned", "花神宝匣", 150, open_quantity=count).requested_open_count
