from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.tasks import storage_bag_random_box
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    StorageBagFixedBoxGuiAdapter,
    StorageBagRandomBoxBlocked,
    StorageBagRandomBoxGuiAdapter,
    StorageBagRandomBoxRequest,
    parse_confirmed_use_quantity,
    require_stable_storage_bag_plan_frame,
    plan_current_random_box_click,
    wallet_reward_targets,
)
from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    StorageBagItemClickPlan,
    StorageBagQuantityObservation,
)


def _snapshot(nums: list[tuple[str, int, int]], fingerprint: str) -> dict:
    return {
        "complete": True,
        "source": "active_backpack_panel_item_info_list",
        "fingerprint": fingerprint,
        "evidence": {"pid": 123, "process_start_ticks": 456},
        "items": [
            {
                "ui_index": index,
                "instance_id": instance_id,
                "base_id": base_id,
                "num": quantity,
                "is_padding": False,
            }
            for index, (instance_id, base_id, quantity) in enumerate(nums)
        ],
    }


class _Runtime:
    def __init__(
        self,
        *,
        quantity_text: str = "10",
        transient: bool = True,
        detail_title: str = "随机宝匣",
    ) -> None:
        self.quantity_text = quantity_text
        self.transient = transient
        self.detail_title = detail_title
        self.events: list[tuple] = []

    def wait_action_settle(self, seconds: float):
        self.events.append(("settle", seconds))
        if False:
            yield None

    def drag_shape_content(self, scene, shape, **options):
        self.events.append(("drag", scene, shape, options["direction"]))

    def click_frame_point(self, scene, x, y):
        self.events.append(("point", scene, x, y))

    def wait_view(self, *scenes, **_options):
        self.events.append(("view", scenes))
        if False:
            yield None
        if scenes == (525, 578):
            return 578 if self.transient else 525
        return scenes[0]

    def cur_frame(self, update=False):
        self.events.append(("frame", update))
        return "frame"

    def ocr_tokens_in_shapes(self, scene, shapes, **_options):
        if scene in {583, 585}:
            return [
                {"text": text, "x": index * 10, "y": 1}
                for index, text in enumerate(self.detail_title)
            ]
        return [{"text": self.quantity_text, "x": 1, "y": 1}]

    def wait_click(self, scene, shape, **_options):
        self.events.append(("click", scene, shape))
        if False:
            yield None

    def wait_click_then_view(self, scene, shape, target, **_options):
        self.events.append(("click_then_view", scene, shape, target))
        if False:
            yield None
        return target


def _consume(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


def _ready(*_args):
    return StorageBagItemClickPlan(
        "ready",
        "unique",
        runtime_index=0,
        runtime_item={"instance_id": "i1", "base_id": 100, "num": 10},
        point=(100.0, 200.0),
        viewport_runtime_start=0,
    )


def test_moving_window_invalidates_the_real_stale_coordinate_before_click() -> None:
    """Regression for 2026-08-19: OCR finished after the list coasted two rows."""

    observations = tuple(
        StorageBagQuantityObservation(index, quantity, None, str(quantity), 0.99)
        for index, quantity in (
            (2, 24),
            (3, 23),
            (12, 100),
            (13, 57_810),
            (14, 1_080),
            (15, 216),
        )
    )
    stale = StorageBagItemClickPlan(
        "ready",
        "Runtime 顺序与旧帧数量序列唯一对齐",
        runtime_index=109,
        runtime_item={"instance_id": "stone", "base_id": 1001, "num": 57_810},
        point=(365.833, 1077.139),
        viewport_runtime_start=96,
        candidate_starts=(96,),
        observations=observations,
    )

    blocked = require_stable_storage_bag_plan_frame(
        stale,
        frame_similarity=82.0,
    )

    assert blocked.status == "insufficient_observations"
    assert blocked.point is None
    assert blocked.runtime_index == 109
    assert blocked.viewport_runtime_start == 96
    assert blocked.observations == observations
    assert "旧帧网格坐标已失效" in blocked.reason


def test_stable_window_preserves_the_ready_click_plan() -> None:
    ready = _ready()

    assert (
        require_stable_storage_bag_plan_frame(ready, frame_similarity=97.5)
        is ready
    )


def test_current_click_planner_resamples_window_after_ocr_and_fails_closed(
    monkeypatch,
) -> None:
    stale = StorageBagItemClickPlan(
        "ready",
        "unique only in captured frame",
        runtime_index=109,
        runtime_item={"instance_id": "stone", "base_id": 1001, "num": 57_810},
        point=(365.833, 1077.139),
        viewport_runtime_start=96,
    )

    class FakeRuntime:
        def __init__(self) -> None:
            self.frames = iter(("captured-before-ocr", "fresh-after-ocr"))
            self.runner = type("Runner", (), {})()

        def view(self, _scene):
            return type("View", (), {"raw": {"dataUrl": "reference"}})()

        def cur_frame(self, update=False):
            assert update is True
            return next(self.frames)

        def observe_scene(self, **_options):
            return 525, 100.0, None

        def shape(self, _scene, _name):
            return type("Shape", (), {"raw": {}})()

        def full_frame_ocr_tokens(self, **_options):
            return []

        def image_signature_bytes_in_shape(
            self, _scene, _shape, *, frame_data_url
        ):
            return frame_data_url.encode()

        def image_signature_similarity(self, _before, _after):
            return 80.0

    class FakeFrame:
        shape = (1600, 900, 3)

    monkeypatch.setattr(storage_bag_random_box, "_decode_frame", lambda _url: FakeFrame())
    monkeypatch.setattr(
        storage_bag_random_box.StorageBagGrid,
        "from_shapes",
        classmethod(lambda cls, *_args, **_kwargs: object()),
    )
    monkeypatch.setattr(
        storage_bag_random_box,
        "register_storage_bag_viewport",
        lambda *_args, **_kwargs: type("Viewport", (), {"aligned": True})(),
    )
    monkeypatch.setattr(
        storage_bag_random_box,
        "visible_storage_bag_cells",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        storage_bag_random_box,
        "quantity_observations_from_ocr",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        storage_bag_random_box,
        "plan_storage_bag_item_click",
        lambda *_args, **_kwargs: stale,
    )

    result = plan_current_random_box_click(
        FakeRuntime(),
        {"complete": True, "items": []},
        StorageBagRandomBoxRequest(1001, "stone", "灵石", 57_810),
    )

    assert result.status == "insufficient_observations"
    assert result.point is None
    assert "旧帧网格坐标已失效" in result.reason


def test_random_box_executes_exact_instance_and_records_verified_runtime_delta() -> None:
    before = _snapshot([("i1", 100, 10), ("r", 200, 3)], "before")
    after = _snapshot([("r", 200, 8)], "after")
    snapshots = iter((before, after))
    recorded = []
    runtime = _Runtime()
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: next(snapshots),
        catalog_cards_by_id={"100": {"name": "随机宝匣"}, "200": {"name": "灵石"}},
        click_planner=_ready,
        recorder=recorded.append,
    )

    result = _consume(adapter.execute(StorageBagRandomBoxRequest(100, "i1", "随机宝匣", 10)))

    assert result.delta.opened_count == 10
    assert result.delta.rewards == ({"item_id": 200, "name": "灵石", "quantity": 5},)
    assert recorded == [result]
    assert ("point", 525, 100.0, 200.0) in runtime.events
    assert ("click", 583, "打开") in runtime.events
    assert ("click", 584, "使用") in runtime.events
    assert ("view", (525,)) in runtime.events


def test_fixed_box_reuses_box_flow_with_fixed_detail_identity() -> None:
    before = _snapshot([("i1", 100, 2), ("r", 200, 3)], "before")
    after = _snapshot([("r", 200, 5)], "after")
    snapshots = iter((before, after))
    runtime = _Runtime(quantity_text="2", transient=False)
    adapter = StorageBagFixedBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: next(snapshots),
        catalog_cards_by_id={"100": {"name": "随机宝匣"}, "200": {"name": "灵石"}},
        click_planner=_ready,
        recorder=lambda _execution: None,
    )

    result = _consume(adapter.execute(StorageBagRandomBoxRequest(100, "i1", "随机宝匣", 2)))

    assert result.delta.opened_count == 2
    assert ("view", (585,)) in runtime.events
    assert ("click", 585, "打开") in runtime.events
    assert ("click", 584, "使用") in runtime.events
    assert ("view", (525, 578)) in runtime.events


def test_quantity_mismatch_fails_before_use_and_before_recording() -> None:
    before = _snapshot([("i1", 100, 10), ("r", 200, 3)], "before")
    runtime = _Runtime(quantity_text="9")
    recorded = []
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: before,
        catalog_cards_by_id={"100": {"name": "随机宝匣"}},
        click_planner=_ready,
        recorder=recorded.append,
    )

    with pytest.raises(StorageBagRandomBoxBlocked, match="当前数量 9"):
        _consume(adapter.execute(StorageBagRandomBoxRequest(100, "i1", "随机宝匣", 10)))

    assert ("click", 584, "使用") not in runtime.events
    assert recorded == []


def test_wrong_detail_title_fails_before_open_or_use() -> None:
    before = _snapshot([("i1", 100, 10), ("other", 38100037, 90)], "before")
    runtime = _Runtime(detail_title="哪吒洗灵随机匣")
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: before,
        catalog_cards_by_id={"100": {"name": "VIP经验"}},
        click_planner=_ready,
        recorder=lambda _execution: None,
    )

    with pytest.raises(StorageBagRandomBoxBlocked, match="详情标题二次核验失败"):
        _consume(adapter.execute(StorageBagRandomBoxRequest(100, "i1", "VIP经验", 10)))

    assert ("click_then_view", 583, "右侧暗幕返回", 525) in runtime.events
    assert ("click", 583, "打开") not in runtime.events
    assert ("click", 584, "使用") not in runtime.events


@pytest.mark.parametrize("status", ["insufficient_observations", "ambiguous_offset"])
def test_insufficient_or_ambiguous_alignment_retries_without_drag(status: str) -> None:
    before = _snapshot([("i1", 100, 10)], "before")
    attempts = 0

    def planner(*_args):
        nonlocal attempts
        attempts += 1
        return StorageBagItemClickPlan(status, "not unique")

    runtime = _Runtime()
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: before,
        catalog_cards_by_id={},
        recorder=lambda _execution: None,
        click_planner=planner,
        alignment_retries=2,
    )

    with pytest.raises(StorageBagRandomBoxBlocked, match="有限重试"):
        _consume(adapter.execute(StorageBagRandomBoxRequest(100, "i1", "随机宝匣", 10)))

    assert attempts == 3
    assert not any(event[0] == "drag" for event in runtime.events)
    assert not any(event[0] == "point" for event in runtime.events)


def test_only_proven_target_not_visible_can_scroll_then_reregisters() -> None:
    before = _snapshot(
        [(f"i{index}", 100 + index, index + 1) for index in range(12)], "before"
    )
    observations = tuple(
        StorageBagQuantityObservation(index, index + 1, None, str(index + 1), 0.99)
        for index in range(4)
    )
    plans = iter(
        (
            StorageBagItemClickPlan(
                "target_not_visible",
                "unique viewport",
                runtime_index=9,
                runtime_item=before["items"][9],
                viewport_runtime_start=0,
                observations=observations,
            ),
            StorageBagItemClickPlan("ambiguous_offset", "new frame ambiguous"),
        )
    )
    runtime = _Runtime()
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=lambda: before,
        catalog_cards_by_id={},
        recorder=lambda _execution: None,
        click_planner=lambda *_args: next(plans),
        alignment_retries=0,
    )

    with pytest.raises(StorageBagRandomBoxBlocked, match="ambiguous_offset"):
        _consume(adapter.execute(StorageBagRandomBoxRequest(109, "i9", "目标箱", 10)))

    assert [event for event in runtime.events if event[0] == "drag"] == [
        ("drag", 525, "窗口", "down")
    ]
    assert not any(event[0] == "point" for event in runtime.events)


def test_exact_instance_id_is_required_even_when_base_id_matches() -> None:
    before = _snapshot([("other", 100, 10)], "before")
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=_Runtime(),
        snapshot_reader=lambda: before,
        catalog_cards_by_id={},
        recorder=lambda _execution: None,
        click_planner=_ready,
    )

    with pytest.raises(StorageBagRandomBoxBlocked, match="instance_id/base_id"):
        _consume(adapter.execute(StorageBagRandomBoxRequest(100, "i1", "随机宝匣", 10)))


def test_current_quantity_parser_requires_one_positive_integer() -> None:
    assert parse_confirmed_use_quantity([{"text": "1", "x": 1, "y": 1}, {"text": "0", "x": 2, "y": 1}]) == 10
    with pytest.raises(StorageBagRandomBoxBlocked):
        parse_confirmed_use_quantity([{"text": "10/20", "x": 1, "y": 1}])


def test_wallet_reward_targets_resolve_resource_and_currency_item_aliases() -> None:
    catalog = {
        "1": {"name": "灵石", "effect_value": "WALLET|1"},
        "57": {"name": "仙币", "effect_value": "WALLET|57"},
        "1001": {"name": "灵石", "type": 9, "effect_value": "1_1"},
    }
    box = {
        "optional_gift_rewards": [
            {"id": 57, "name": "仙币"},
            {"id": 1001, "name": "灵石"},
        ]
    }

    assert wallet_reward_targets(box, catalog) == {1: "灵石", 57: "仙币"}


def test_six_yuan_voucher_effect_opcode_maps_to_voucher_wallet() -> None:
    catalog = {
        "1012": {
            "name": "充值代币(6元)",
            "type": 9,
            "effect_value": "1002_6",
        }
    }
    box = {
        "optional_gift_rewards": [
            {"id": 1012, "name": "充值代币(6元)"},
        ]
    }

    assert wallet_reward_targets(box, catalog) == {1001: "充值代币(6元)"}


def test_random_box_records_wallet_runtime_delta_when_reward_is_not_in_backpack() -> None:
    before = _snapshot([("i1", 100, 10)], "before")
    after = _snapshot([], "after")
    snapshots = iter((before, after))
    wallet_snapshots = iter((
        {
            "source": "runtime_memory",
            "currency_type": 1,
            "exchange_currency": 1000,
            "evidence": {"pid": 123, "process_start_ticks": 456},
        },
        {
            "source": "runtime_memory",
            "currency_type": 1,
            "exchange_currency": 1176,
            "evidence": {"pid": 123, "process_start_ticks": 456},
        },
    ))
    recorded = []
    catalog = {
        "100": {
            "name": "随机宝匣",
            "optional_gift_rewards": [{"id": 1001, "name": "灵石"}],
        },
        "1001": {"name": "灵石", "type": 9, "effect_value": "1_1"},
    }
    adapter = StorageBagRandomBoxGuiAdapter(
        runtime=_Runtime(),
        snapshot_reader=lambda: next(snapshots),
        wallet_snapshot_reader=lambda _currency_type: next(wallet_snapshots),
        catalog_cards_by_id=catalog,
        click_planner=_ready,
        recorder=recorded.append,
    )

    result = _consume(
        adapter.execute(StorageBagRandomBoxRequest(100, "i1", "随机宝匣", 10))
    )

    assert result.delta.rewards == (
        {
            "item_id": 1,
            "name": "灵石",
            "quantity": 176,
            "reward_key": "wallet:1",
        },
    )
    assert result.wallet_before == ((1, 1000),)
    assert result.wallet_after == ((1, 1176),)
    assert recorded == [result]
