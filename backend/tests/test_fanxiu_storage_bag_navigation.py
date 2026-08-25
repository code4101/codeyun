from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.storage_bag_navigation import (
    select_storage_bag_category,
    select_storage_bag_category_target,
)


class _Runtime:
    def __init__(self) -> None:
        self.calls = []

    def wait_click_ocr_text(self, view, target, **kwargs):
        self.calls.append(("ocr", view, target, kwargs))
        yield "clicked"
        return "match"

    def wait_action_settle(self, seconds):
        self.calls.append(("settle", seconds))
        yield "settled"


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_select_storage_bag_daily_category_uses_formal_shape() -> None:
    runtime = _Runtime()

    result = _drain(select_storage_bag_category(runtime, "日程"))

    assert result == "match"
    kind, view, target, kwargs = runtime.calls[0]
    assert (kind, view, target) == ("ocr", 525, "日程")
    assert kwargs["in_shapes"] == ("分类页签",)
    assert kwargs["match_mode"] == "exact"


def test_select_category_target_requires_fresh_complete_runtime_identity() -> None:
    runtime = _Runtime()
    snapshot = {
        "complete": True,
        "items": [{"instance_id": "x", "base_id": 4_000_001, "num": 116906}],
    }

    target = _drain(select_storage_bag_category_target(
        runtime,
        category="日程",
        target_name="天雷竹",
        snapshot_reader=lambda: snapshot,
        catalog_cards_by_id={"4000001": {"name": "天雷竹", "type_name": "神物"}},
    ))

    assert (target.base_id, target.name, target.runtime_index) == (4_000_001, "天雷竹", 0)
