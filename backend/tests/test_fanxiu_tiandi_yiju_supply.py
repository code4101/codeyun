from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_supply as supply_module
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_supply import (
    SACRED_TREE_ITEM_ID,
    TIANDI_YIJU_BOX_ITEM_ID,
    plan_tiandi_yiju_supply,
    verify_tiandi_yiju_supply_delta,
)


def _snapshot(*, trees: int, boxes: int, fingerprint: str = "a") -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": 11, "process_start_ticks": 22},
        "items": [
            {"base_id": SACRED_TREE_ITEM_ID, "num": trees, "instance_id": "tree"},
            {"base_id": TIANDI_YIJU_BOX_ITEM_ID, "num": boxes, "instance_id": "box"},
        ],
    }


def _shop() -> dict:
    return {
        "complete": True,
        "rows": [{
            "entries": [{
                "item_id": TIANDI_YIJU_BOX_ITEM_ID,
                "name": "弈技·仙弈盒",
                "goods_num": 1,
                "cost_item_id": SACRED_TREE_ITEM_ID,
                "cost_num": 200,
                "limit_times": -1,
                "bought": 0,
            }],
        }],
    }


def test_supply_plan_buys_only_the_box_shortfall() -> None:
    plan = plan_tiandi_yiju_supply(
        _snapshot(trees=1_000, boxes=2),
        _shop(),
        required_boxes=5,
    )

    assert plan.exchange_count == 3
    assert plan.total_cost == 600
    assert plan.projected_stock == 5


def test_supply_plan_uses_all_affordable_trees_toward_large_target() -> None:
    plan = plan_tiandi_yiju_supply(
        _snapshot(trees=1_000, boxes=2),
        _shop(),
        required_boxes=20,
    )

    assert plan.exchange_count == 5
    assert plan.total_cost == 1_000
    assert plan.projected_stock == 7
    assert plan.target_stock == 7
    assert plan.ready is True


def test_supply_delta_requires_exact_tree_cost_and_box_gain() -> None:
    before = _snapshot(trees=1_000, boxes=2)
    plan = plan_tiandi_yiju_supply(before, _shop(), required_boxes=5)

    verify_tiandi_yiju_supply_delta(
        before,
        _snapshot(trees=400, boxes=5, fingerprint="b"),
        plan,
    )
    with pytest.raises(RuntimeError, match="Runtime 双差值"):
        verify_tiandi_yiju_supply_delta(
            before,
            _snapshot(trees=401, boxes=5, fingerprint="c"),
            plan,
        )


def test_supply_admission_accepts_existing_boxes_without_source():
    result = supply_module.plan_tiandi_yiju_supply_admission(
        {supply_module.TIANDI_YIJU_BOX_ITEM_ID: 5}, required_boxes=5,
    )
    assert result == {"status": "sufficient", "boxes_after": 5}


def test_supply_admission_preserves_shortage_as_business_outcome():
    result = supply_module.plan_tiandi_yiju_supply_admission(
        {supply_module.TIANDI_YIJU_BOX_ITEM_ID: 5}, required_boxes=6,
    )
    assert result == {"status": "unavailable", "reason": "source_item_absent", "boxes_after": 5}
    assert supply_module.plan_tiandi_yiju_supply_admission(
        {supply_module.TIANDI_YIJU_BOX_ITEM_ID: 5, supply_module.SACRED_TREE_ITEM_ID: 1},
        required_boxes=6,
    ) is None
