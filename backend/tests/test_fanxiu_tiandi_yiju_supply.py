from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks import tiandi_yiju_supply as supply_module
from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_supply import (
    SACRED_TREE_ITEM_ID,
    TIANDI_YIJU_BOX_ITEM_ID,
    ensure_tiandi_yiju_round_supply,
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


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as done:
        return done.value


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


def test_supply_replay_passes_without_opening_shop_when_boxes_are_sufficient() -> None:
    class Runtime:
        def go_scene(self, _scene):
            if False:
                yield None

        def wait_click(self, *_args, **_kwargs):
            if False:
                yield None

        def wait_scene(self, layer0, **_kwargs):
            if False:
                yield None

        def wait_click_ocr_text(self, *_args, **_kwargs):
            if False:
                yield None
            return object()

        def wait_action_settle(self, _seconds):
            if False:
                yield None

    def forbidden():
        raise AssertionError("库存足够时不得加载商店或 Catalog")

    result = _drain(
        ensure_tiandi_yiju_round_supply(
            Runtime(),
            required_boxes=5,
            snapshot_reader=lambda: _snapshot(trees=0, boxes=5),
            shop_reader=forbidden,
            catalog_reader=forbidden,
        )
    )

    assert result == {"status": "sufficient", "boxes_after": 5}


def test_supply_adapter_delegates_to_shared_sacred_exchange_transaction(
    monkeypatch,
) -> None:
    def done(result=None):
        if False:
            yield None
        return result

    calls = []

    def shared(context, **kwargs):
        calls.append((context, kwargs))
        yield None
        return {
            "status": "supplied",
            "exchange_count": 3,
            "cost_spent": 600,
            "stock_after": 5,
        }

    monkeypatch.setattr(supply_module, "ensure_sacred_exchange_stock", shared)
    context = object()
    snapshot_reader = object()
    shop_reader = object()
    buy_reader = object()
    catalog_reader = object()

    result = _drain(
        ensure_tiandi_yiju_round_supply(
            context,
            required_boxes=5,
            snapshot_reader=snapshot_reader,
            shop_reader=shop_reader,
            buy_reader=buy_reader,
            catalog_reader=catalog_reader,
        )
    )

    assert calls == [(context, {
        "spec": supply_module.TIANDI_YIJU_SUPPLY,
        "required_stock": 5,
        "snapshot_reader": snapshot_reader,
        "shop_reader": shop_reader,
        "buy_reader": buy_reader,
        "catalog_reader": catalog_reader,
    })]
    assert supply_module.TIANDI_YIJU_SUPPLY.allow_partial is True
    assert result == {
        "status": "supplied",
        "exchange_count": 3,
        "tree_spent": 600,
        "boxes_after": 5,
    }
