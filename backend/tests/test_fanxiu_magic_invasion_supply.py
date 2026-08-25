from threading import Event

import pytest

from backend.core.fanxiu.data_annotation.tasks.magic_invasion_supply import (
    MAGIC_RANKING_CHOICE_BOX_ID,
    TIANYAN_ITEM_ID,
    build_magic_supply_plan,
    ensure_magic_tianyan_supply,
)


CATALOG = {
    str(MAGIC_RANKING_CHOICE_BOX_ID): {
        "name": "玩法榜甄选·魔道",
        "optional_gift_rewards": [
            {"id": 1010007, "name": "追踪令", "count": 100},
            {"id": 1010005, "name": "除魔令", "count": 120},
            {"id": TIANYAN_ITEM_ID, "name": "天眼符", "count": 120},
        ],
    },
    str(TIANYAN_ITEM_ID): {"name": "天眼符"},
}


def _snapshot(*, tianyan: int, boxes: tuple[int, ...]) -> dict:
    items = [
        {
            "ui_index": 0,
            "instance_id": "tianyan",
            "base_id": TIANYAN_ITEM_ID,
            "num": tianyan,
            "is_padding": False,
        }
    ]
    items.extend(
        {
            "ui_index": index + 1,
            "instance_id": f"box-{index}",
            "base_id": MAGIC_RANKING_CHOICE_BOX_ID,
            "num": count,
            "is_padding": False,
        }
        for index, count in enumerate(boxes)
    )
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "items": items,
    }


def test_plan_opens_minimum_boxes_for_exact_shortfall() -> None:
    plan = build_magic_supply_plan(
        _snapshot(tianyan=868, boxes=(20,)),
        CATALOG,
        required_tianyan=1500,
    )

    assert plan.shortfall == 632
    assert plan.required_box_count == 6
    assert plan.requests[0].owned_box_count == 20
    assert plan.requests[0].open_box_count == 6
    assert plan.tianyan_before + plan.required_box_count * plan.per_box == 1588


def test_plan_needs_no_choice_box_catalog_when_tianyan_is_already_sufficient() -> None:
    plan = build_magic_supply_plan(
        _snapshot(tianyan=1500, boxes=()),
        {},
        required_tianyan=1500,
    )

    assert plan.needed is False
    assert plan.shortfall == 0
    assert plan.requests == ()


def test_plan_spans_instances_but_never_uses_random_boxes() -> None:
    plan = build_magic_supply_plan(
        _snapshot(tianyan=100, boxes=(4, 10)),
        CATALOG,
        required_tianyan=1500,
    )

    assert [(item.instance_id, item.open_box_count) for item in plan.requests] == [
        ("box-0", 4),
        ("box-1", 8),
    ]


def test_plan_fails_before_action_when_deterministic_boxes_are_insufficient() -> None:
    with pytest.raises(RuntimeError, match="仅有 1 个"):
        build_magic_supply_plan(
            _snapshot(tianyan=0, boxes=(1,)),
            CATALOG,
            required_tianyan=1500,
        )


def test_sufficient_runtime_supply_does_not_load_choice_box_catalog() -> None:
    class _Runtime:
        def goto_view(self, _scene):
            yield None

        def wait_click(self, *_args, **_kwargs):
            yield None

        def wait_scene(self, *_args, **_kwargs):
            yield None

    class _Runner:
        def _fanxiu_runtime(self, *_args, **_kwargs):
            return _Runtime()

    def unavailable_catalog():
        raise AssertionError("天眼符足够时不得加载选择箱 Catalog")

    generator = ensure_magic_tianyan_supply(
        _Runner(),
        {},
        Event(),
        required_tianyan=1500,
        snapshot_reader=lambda: _snapshot(tianyan=1500, boxes=()),
        catalog_reader=unavailable_catalog,
    )
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            result = exc.value
            break

    assert result["status"] == "sufficient"
    assert result["tianyan_after"] == 1500
