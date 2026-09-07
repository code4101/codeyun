"""库存/奖励目录投影的纯数据契约，不模拟游戏操作。"""

import pytest

from backend.core.fanxiu.instrumentation.spirit_artifact_storage_bag import (
    build_spirit_artifact_storage_bag_snapshot,
)


def test_projection_aggregates_stacks_resets_absent_boxes_and_maps_reward_description():
    artifacts = [{"name": "弥罗宝光幢", "rows": [{"part_name": "座"}]}]
    cards = {
        "10": {"id": 10, "name": "弥罗升品宝匣·贰", "optional_gift_rewards": [
            {"id": 20, "name": "宝光幢座曜仙镜", "count": 1,
             "description": "可助【弥罗宝光幢·座】升品。"},
        ]},
        "11": {"id": 11, "name": "弥罗自选宝匣·贰", "optional_gift_rewards": [
            {"id": 21, "name": "弥罗宝光幢·座", "count": 1},
        ]},
    }
    runtime = {"complete": True, "source": "active_backpack_panel_item_info_list",
               "tab": {"number": 1}, "items": [
                   {"ui_index": 0, "base_id": 10, "instance_id": "100", "num": 2},
                   {"ui_index": 1, "base_id": 10, "instance_id": "101", "num": 3},
               ]}
    previous = [{"title": "弥罗自选宝匣·贰", "quantity": 9, "choices": []}]
    result = build_spirit_artifact_storage_bag_snapshot(
        runtime, cards, artifacts, previous, captured_at="2026-09-07T16:33:09+08:00",
    )
    old, new = result["storage_bag_items"]
    assert old["quantity"] == 0
    assert new["quantity"] == 5
    assert new["choices"][0]["part_name"] == "座"
    assert new["choices"][0]["reward_quantity"] == 1
    assert previous[0]["quantity"] == 9
    repeated = build_spirit_artifact_storage_bag_snapshot(
        runtime, cards, artifacts, result["storage_bag_items"], captured_at=result["captured_at"],
    )
    assert repeated == result


@pytest.mark.parametrize("complete,tab", [(False, 1), (True, 2)])
def test_projection_rejects_incomplete_or_filtered_inventory(complete, tab):
    with pytest.raises(ValueError):
        build_spirit_artifact_storage_bag_snapshot(
            {"complete": complete, "source": "active_backpack_panel_item_info_list",
             "tab": {"number": tab}, "items": []},
            {}, [], [], captured_at="2026-09-07T16:33:09+08:00",
        )
