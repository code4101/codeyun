from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from threading import Event

import pytest

from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
    current_magic_invasion_occurrence,
    execute_magic_invasion_explore_job,
    magic_invasion_occurrences,
    next_magic_invasion_probe_time,
    parse_available_explore_count,
    parse_owned_item_count,
    parse_selected_item_count,
    slider_fraction,
)
from backend.core.fanxiu.data_annotation.tasks import magic_invasion as magic


def _finish(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _schedule() -> dict:
    return {
        "available": True,
        "complete": True,
        "items": [
            {
                "id": 1070011400004,
                "activityId": 1070011,
                "activityType": 7,
                "startTime": _ms("2026-08-21T10:00:00+08:00"),
                "endTime": _ms("2026-08-21T22:00:00+08:00"),
                "serverCount": None,
            },
            {
                "id": 8070001400004,
                "activityId": 8070001,
                "activityType": 7,
                "startTime": _ms("2026-08-22T10:00:00+08:00"),
                "endTime": _ms("2026-08-22T22:00:00+08:00"),
                "serverCount": 8,
            },
            {
                "id": 1,
                "activityId": 1,
                "activityType": 12,
                "startTime": _ms("2026-08-22T10:00:00+08:00"),
                "endTime": _ms("2026-08-22T22:00:00+08:00"),
            },
        ],
    }


def test_server_and_cross_are_distinct_occurrences_with_shared_type() -> None:
    server, cross = magic_invasion_occurrences(_schedule())

    assert server.mode == "server"
    assert server.occurrence_id == "1070011400004"
    assert cross.mode == "cross"
    assert cross.server_count == 8
    assert cross.occurrence_id == "8070001400004"


def test_current_occurrence_selects_tomorrow_cross_by_exact_runtime_period() -> None:
    occurrence = current_magic_invasion_occurrence(
        _schedule(), now=datetime.fromisoformat("2026-08-22T10:01:00+08:00")
    )

    assert occurrence is not None
    assert occurrence.mode == "cross"
    assert occurrence.activity_id == 8070001


def test_daily_probe_rolls_after_ten_oh_one() -> None:
    assert next_magic_invasion_probe_time(
        datetime.fromisoformat("2026-08-21T22:00:00+08:00")
    ) == datetime.fromisoformat("2026-08-22T10:01:00+08:00")


def test_quantity_parsers_and_slider_fraction() -> None:
    assert parse_available_explore_count("可用探查次数:35/120") == 35
    with pytest.raises(RuntimeError, match="挑战事件"):
        parse_available_explore_count("挑战事件 24/100")
    assert parse_owned_item_count("天眼符 持有数量：2368") == 2368
    assert parse_selected_item_count("465") == 465
    assert slider_fraction(quantity=1, owned_count=2368) == 0
    assert slider_fraction(quantity=2368, owned_count=2368) == 1


def test_quantity_parsers_fail_closed_on_ambiguity() -> None:
    with pytest.raises(RuntimeError, match="不唯一"):
        parse_selected_item_count("465 / 2368")


def test_configure_quantity_reuses_verified_slider_and_separates_owned_from_max(monkeypatch) -> None:
    calls = []

    def _set_count(_runtime, assets, desired, **options):
        calls.append((assets, desired, options))
        if False:
            yield None
        return {"before": 250, "after": 469, "maximum": 1200}

    monkeypatch.setattr(magic, "_shape_text", lambda *_args, **_kwargs: "持有数量：1533")
    monkeypatch.setattr(magic, "_set_verified_slider_count", _set_count)

    result = _finish(magic._configure_use_quantity(object(), quantity=469))

    assets, desired, options = calls[0]
    assert assets.settings_scene_id == magic.MAGIC_INVASION_USE_SCENE_ID
    assert assets.count_slider_thumb == "数量滑块游标"
    assert assets.count_minimum_marker == "使用数量为1"
    assert assets.count_slider_left_anchor == "数量滑轨左端"
    assert assets.count_slider_right_anchor == "数量滑轨右端"
    assert desired == 469
    assert options["force_bound_probe"] is True
    assert options["max_adjustments"] == 10
    assert result == {
        "owned_count": 1533,
        "single_use_maximum": 1200,
        "selected_count": 469,
        "slider_calibration": {"before": 250, "after": 469, "maximum": 1200},
    }


def test_configure_quantity_uses_stable_slider_readback_when_extra_single_frame_would_be_empty(
    monkeypatch,
) -> None:
    shape_reads = []

    def _shape_text(_runtime, _scene_id, shape_title):
        shape_reads.append(shape_title)
        if shape_title == "持有数量":
            return "持有数量：1533"
        return ""

    def _set_count(_runtime, _assets, _desired, **_options):
        if False:
            yield None
        return {"before": 250, "after": 469, "maximum": 1200}

    monkeypatch.setattr(magic, "_shape_text", _shape_text)
    monkeypatch.setattr(magic, "_set_verified_slider_count", _set_count)

    result = _finish(magic._configure_use_quantity(object(), quantity=469))

    assert result["selected_count"] == 469
    assert shape_reads == ["持有数量"]


@pytest.mark.parametrize("calibration", [None, {}, {"after": ""}, {"after": "unknown"}])
def test_configure_quantity_fails_closed_without_stable_slider_readback(
    monkeypatch,
    calibration,
) -> None:
    def _set_count(_runtime, _assets, _desired, **_options):
        if False:
            yield None
        return calibration

    monkeypatch.setattr(magic, "_shape_text", lambda *_args, **_kwargs: "持有数量：1533")
    monkeypatch.setattr(magic, "_set_verified_slider_count", _set_count)

    with pytest.raises(RuntimeError, match="缺少稳定读回证据"):
        _finish(magic._configure_use_quantity(object(), quantity=469))


def test_configure_quantity_fails_closed_when_slider_cannot_stabilize(monkeypatch) -> None:
    def _set_count(_runtime, _assets, _desired, **_options):
        if False:
            yield None
        raise RuntimeError("整数滑块动作后读值方向异常")

    monkeypatch.setattr(magic, "_shape_text", lambda *_args, **_kwargs: "持有数量：1533")
    monkeypatch.setattr(magic, "_set_verified_slider_count", _set_count)

    with pytest.raises(RuntimeError, match="读值方向异常"):
        _finish(magic._configure_use_quantity(object(), quantity=469))


def test_top_up_never_clicks_use_when_quantity_readback_is_not_stable(monkeypatch) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.clicks = []

        def click_shape_center(self, scene_id, title):
            self.clicks.append((scene_id, title))

    def _configure(_runtime, *, quantity):
        assert quantity == 469
        if False:
            yield None
        raise RuntimeError("天眼符使用数量缺少稳定读回证据")

    runtime = _Runtime()
    monkeypatch.setattr(magic, "_wait_scene", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(magic, "_configure_use_quantity", _configure)

    with pytest.raises(RuntimeError, match="缺少稳定读回证据"):
        _finish(magic._top_up_to_batch(runtime, available_count=31))

    assert (magic.MAGIC_INVASION_USE_SCENE_ID, "使用") not in runtime.clicks


def test_white_dragon_result_requires_explicit_result_evidence() -> None:
    assert magic._white_dragon_result("快速探索854次")["observed"] is False
    result = magic._white_dragon_result("快速探索854次 御灵·白龙马触发")
    assert result["observed"] is True
    assert result["matched_alias"] == "御灵·白龙马"


def test_map_entry_explicitly_consumes_magic_confirmation_layer() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.scenes = [magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, magic.MAGIC_INVASION_MAP_SCENE_ID]
            self.clicks = []

        def click_shape(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def current_scene(self, targets, update=True):
            scene = self.scenes.pop(0)
            assert scene in targets
            return scene, 100.0, "frame"

    runtime = _Runtime()
    magic._enter_magic_invasion_map(runtime)

    assert runtime.clicks == [
        (magic.MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图"),
        (magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, "确认"),
    ]


def test_map_entry_waits_out_read_only_transition_without_clicking_it() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.scenes = [
                magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID,
                magic.MAGIC_INVASION_ENTRY_TRANSITION_SCENE_ID,
                magic.MAGIC_INVASION_MAP_SCENE_ID,
            ]
            self.clicks = []

        def click_shape(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def current_scene(self, targets, update=True):
            scene = self.scenes.pop(0)
            assert scene in targets
            return scene, 100.0, "frame"

    runtime = _Runtime()
    magic._enter_magic_invasion_map(runtime)

    assert runtime.clicks == [
        (magic.MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图"),
        (magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, "确认"),
    ]


def test_map_entry_accepts_map_landing_after_more_than_old_thirty_seconds(
    monkeypatch,
) -> None:
    clock = [0.0]

    class _Runtime:
        def __init__(self) -> None:
            self.polls_after_confirm = 0
            self.clicks = []

        def click_shape(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def current_scene(self, targets, update=True):
            if self.polls_after_confirm == 0:
                self.polls_after_confirm += 1
                return magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, 100.0, "confirm"
            self.polls_after_confirm += 1
            if clock[0] <= 31.0:
                return None, 0.0, "unknown"
            return magic.MAGIC_INVASION_MAP_SCENE_ID, 100.0, "map"

    monkeypatch.setattr(magic.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(magic.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    runtime = _Runtime()

    magic._enter_magic_invasion_map(runtime)

    assert clock[0] > 30.0
    assert runtime.clicks == [
        (magic.MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图"),
        (magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, "确认"),
    ]


@pytest.mark.parametrize(
    "task_scene",
    [
        magic.MAGIC_INVASION_TASK_DEMON_SCENE_ID,
        magic.MAGIC_INVASION_TASK_CULTIVATION_SCENE_ID,
    ],
)
def test_map_entry_fails_closed_when_confirmation_lands_on_task_page(task_scene) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.scenes = [
                magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID,
                task_scene,
            ]
            self.clicks = []

        def click_shape(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def current_scene(self, targets, update=True):
            scene = self.scenes.pop(0)
            assert scene in targets
            return scene, 100.0, "frame"

    runtime = _Runtime()

    with pytest.raises(RuntimeError, match="入口误落任务页"):
        magic._enter_magic_invasion_map(runtime)

    assert runtime.clicks == [
        (magic.MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图"),
        (magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, "确认"),
    ]


def test_map_entry_persistent_unknown_still_fails_closed(monkeypatch) -> None:
    clock = [0.0]

    class _Runtime:
        def __init__(self) -> None:
            self.first = True

        def click_shape(self, _scene_id, _title):
            return None

        def current_scene(self, _targets, update=True):
            if self.first:
                self.first = False
                return magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID, 100.0, "confirm"
            return None, 0.0, "unknown"

    monkeypatch.setattr(magic.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(magic.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    with pytest.raises(RuntimeError, match=r"稳定落到 #512 超时.*elapsed=90\.0s.*last_state=unknown"):
        magic._enter_magic_invasion_map(_Runtime())

    assert clock[0] >= magic.MAGIC_INVASION_MAP_ENTRY_SETTLE_TIMEOUT_SECONDS


def test_map_entry_accepts_direct_map_landing_without_confirmation() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.clicks = []

        def click_shape(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def current_scene(self, targets, update=True):
            return magic.MAGIC_INVASION_MAP_SCENE_ID, 100.0, "frame"

    runtime = _Runtime()
    magic._enter_magic_invasion_map(runtime)

    assert runtime.clicks == [(magic.MAGIC_INVASION_MAIN_SCENE_ID, "前往大地图")]


@pytest.mark.parametrize("landings", ([34], [magic.MAGIC_INVASION_MAIN_SCENE_ID, 34]))
def test_map_exit_never_consumes_the_map_entry_confirmation(landings) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.scenes = list(landings)
            self.clicks = []

        def click_shape_center(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def current_scene(self, targets, update=True):
            scene = self.scenes.pop(0)
            assert scene in targets
            return scene, 100.0, "frame"

    runtime = _Runtime()
    magic._leave_magic_invasion_map(runtime)

    assert runtime.clicks[0] == (magic.MAGIC_INVASION_MAP_SCENE_ID, "地图返回")
    assert all(scene_id != magic.MAGIC_INVASION_MAP_ENTRY_CONFIRM_SCENE_ID for scene_id, _ in runtime.clicks)
    if landings[0] == magic.MAGIC_INVASION_MAIN_SCENE_ID:
        assert runtime.clicks[-1] == (magic.MAGIC_INVASION_MAIN_SCENE_ID, "返回")


def test_task_progress_evidence_fails_closed_when_runtime_snapshot_incomplete(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        magic,
        "read_magic_invasion_task_reward_snapshot",
        lambda _activity_id: {
            "ok": True,
            "available": True,
            "complete": False,
            "state": "partial",
        },
    )

    with pytest.raises(RuntimeError, match="不可用或不完整"):
        magic._compact_task_snapshot(8070001)


def _run_single_batch_settlement(
    monkeypatch,
    *,
    result_explore_count: int,
    available_count_after: int,
):
    class _DuringWindowDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat("2026-08-22T19:00:00+08:00")
            return value.astimezone(tz) if tz is not None else value

    class _Runtime:
        def __init__(self) -> None:
            self.clicks = []

        def shape_matches(self, *_args, **_kwargs):
            return object()

        def click_shape_center(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def click_shape(self, scene_id, title):
            self.clicks.append((scene_id, title))

        def cur_frame(self, update=True):
            return "result-frame"

        def ocr_text(self, _frame):
            return ""

    class _Runner:
        def __init__(self) -> None:
            self.progress_writes = []

        def _set_scheduler_task_payload_flag(self, _task_id, _key, value):
            self.progress_writes.append(deepcopy(value))
            return True

        def _log(self, *_args, **_kwargs):
            return None

    available_reads = iter((500, available_count_after))

    def _shape_text(_runtime, _scene_id, shape_title):
        if shape_title == "可用探查次数":
            return f"{next(available_reads)}/120"
        if shape_title == "探索次数结果":
            return f"快速探索{result_explore_count}次"
        raise AssertionError(f"未预期的 shape 读取：{shape_title}")

    def _top_up(_runtime, *, available_count):
        assert available_count == 500
        if False:
            yield None
        return {"requested_topup": 0, "available_explore_count_after_topup": 500}

    monkeypatch.setattr(magic, "datetime", _DuringWindowDatetime)
    monkeypatch.setattr(magic, "_wait_scene", lambda _runtime, targets, **_kwargs: (targets[0], 100.0, "frame"))
    monkeypatch.setattr(magic, "_enter_magic_invasion_map", lambda _runtime: None)
    monkeypatch.setattr(magic, "_leave_magic_invasion_map", lambda _runtime: None)
    monkeypatch.setattr(magic, "_shape_text", _shape_text)
    monkeypatch.setattr(magic, "_top_up_to_batch", _top_up)
    monkeypatch.setattr(
        magic,
        "_read_tianyan_inventory",
        lambda: {"count": 1000, "evidence": {"pid": 1, "process_start_ticks": 2}},
    )
    monkeypatch.setattr(magic, "_compact_task_snapshot", lambda _activity_id: {"state": "ok"})

    runner = _Runner()
    runtime = _Runtime()
    payload = {
        "expected_occurrence_id": "8070001400004",
        "magic_invasion_progress": {
            "occurrence_id": "8070001400004",
            "state": "confirmed",
            "base_explore_count": 1000,
            "confirmed_batches": [{"batch_index": 1}, {"batch_index": 2}],
        },
    }
    operation = execute_magic_invasion_explore_job(
        runner,
        {"scheduler_task_id": "ranking-lifecycle"},
        payload,
        Event(),
        manage_schedule=False,
        prepared_runtime=runtime,
        prepared_schedule=_schedule(),
        already_on_main_scene=True,
    )
    return runner, runtime, payload, operation


def test_batch_result_must_be_exactly_500_before_confirming(monkeypatch) -> None:
    runner, _runtime, payload, operation = _run_single_batch_settlement(
        monkeypatch,
        result_explore_count=499,
        available_count_after=0,
    )

    with pytest.raises(RuntimeError, match="结果不是精确 500 次"):
        _finish(operation)

    assert [item["state"] for item in runner.progress_writes] == [
        "topup_confirmed",
        "explore_armed",
    ]
    assert len(payload["magic_invasion_progress"]["confirmed_batches"]) == 2
    assert payload["magic_invasion_progress"]["base_explore_count"] == 1000


def test_batch_is_not_confirmed_until_available_count_is_zero(monkeypatch) -> None:
    runner, _runtime, payload, operation = _run_single_batch_settlement(
        monkeypatch,
        result_explore_count=500,
        available_count_after=1,
    )

    with pytest.raises(RuntimeError, match="可用探查次数未归零"):
        _finish(operation)

    assert [item["state"] for item in runner.progress_writes] == [
        "topup_confirmed",
        "explore_armed",
        "result_observed",
    ]
    assert len(payload["magic_invasion_progress"]["confirmed_batches"]) == 2
    assert payload["magic_invasion_progress"]["base_explore_count"] == 1000


def test_batch_is_durably_confirmed_after_exact_500_and_zero_available(monkeypatch) -> None:
    runner, _runtime, payload, operation = _run_single_batch_settlement(
        monkeypatch,
        result_explore_count=500,
        available_count_after=0,
    )

    result = _finish(operation)

    confirmed_write = next(item for item in runner.progress_writes if item["state"] == "confirmed")
    assert [item["state"] for item in runner.progress_writes[:4]] == [
        "topup_confirmed",
        "explore_armed",
        "result_observed",
        "confirmed",
    ]
    assert len(confirmed_write["confirmed_batches"]) == 3
    assert confirmed_write["base_explore_count"] == 1500
    assert confirmed_write["confirmed_batches"][-1]["result_explore_count"] == 500
    assert confirmed_write["confirmed_batches"][-1]["available_explore_count_after_result"] == 0
    assert payload["magic_invasion_progress"]["state"] == "complete"
    assert result["result"] == "success"


class _CrashRecoveryRunner:
    def __init__(self, *, crash_after_state: str | None = None) -> None:
        self.crash_after_state = crash_after_state
        self.progress_writes = []

    def _set_scheduler_task_payload_flag(self, _task_id, _key, value):
        saved = deepcopy(value)
        self.progress_writes.append(saved)
        if saved.get("state") == self.crash_after_state:
            raise RuntimeError(f"crash-after-{self.crash_after_state}")
        return True

    def _log(self, *_args, **_kwargs):
        return None


class _CrashRecoveryRuntime:
    def __init__(self, scenes=()) -> None:
        self.scenes = list(scenes)
        self.clicks = []

    def current_scene(self, targets, update=True):
        scene = self.scenes.pop(0)
        assert scene in targets
        return scene, 100.0, "frame"

    def click_shape_center(self, scene_id, title):
        self.clicks.append((scene_id, title))

    def click_shape(self, scene_id, title):
        self.clicks.append((scene_id, title))

    def cur_frame(self, update=True):
        return "result-frame"

    def ocr_text(self, _frame):
        return ""


def _recovery_payload(state: str) -> dict:
    return {
        "expected_occurrence_id": "8070001400004",
        "magic_invasion_progress": {
            "occurrence_id": "8070001400004",
            "state": state,
            "base_explore_count": 1000,
            "confirmed_batches": [{"batch_index": 1}, {"batch_index": 2}],
            "batch_index": 3,
            "transaction_evidence": {
                "base_explore_before": 1000,
                "available_explore_count_before": 0,
                "requested_topup": 500,
                "tianyan_before": {
                    "count": 1000,
                    "evidence": {"pid": 1, "process_start_ticks": 2},
                },
                "task_progress_before": {"tasks": []},
            },
        },
    }


def _prepare_recovery_test(monkeypatch, *, inventory_count: int, map_count: int = 0) -> None:
    class _DuringWindowDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat("2026-08-22T19:00:00+08:00")
            return value.astimezone(tz) if tz is not None else value

    monkeypatch.setattr(magic, "datetime", _DuringWindowDatetime)
    monkeypatch.setattr(
        magic,
        "_read_tianyan_inventory",
        lambda: {
            "count": inventory_count,
            "evidence": {"pid": 1, "process_start_ticks": 2},
        },
    )
    monkeypatch.setattr(magic, "_compact_task_snapshot", lambda _activity_id: {"tasks": []})

    def shape_text(_runtime, scene_id, title):
        if scene_id == magic.MAGIC_INVASION_RESULT_SCENE_ID:
            assert title == "探索次数结果"
            return "快速探索500次"
        assert title == "可用探查次数"
        return f"{map_count}/120"

    monkeypatch.setattr(magic, "_shape_text", shape_text)


def _run_recovery(runner, runtime, payload):
    return execute_magic_invasion_explore_job(
        runner,
        {"scheduler_task_id": "ranking-lifecycle"},
        payload,
        Event(),
        manage_schedule=False,
        prepared_runtime=runtime,
        prepared_schedule=_schedule(),
        already_on_main_scene=True,
    )


def test_crash_after_use_armed_before_click_fails_stop_without_replaying_use(
    monkeypatch,
) -> None:
    _prepare_recovery_test(monkeypatch, inventory_count=1000, map_count=0)
    runtime = _CrashRecoveryRuntime()

    with pytest.raises(RuntimeError, match="禁止重放使用"):
        _finish(_run_recovery(_CrashRecoveryRunner(), runtime, _recovery_payload("use_armed")))

    assert runtime.clicks == []


def test_crash_after_use_click_recovers_from_exact_inventory_and_map_pair_only(
    monkeypatch,
) -> None:
    _prepare_recovery_test(monkeypatch, inventory_count=500, map_count=500)
    runtime = _CrashRecoveryRuntime(
        (magic.MAGIC_INVASION_ITEM_SCENE_ID, magic.MAGIC_INVASION_MAP_SCENE_ID)
    )
    runner = _CrashRecoveryRunner(crash_after_state="explore_armed")

    with pytest.raises(RuntimeError, match="crash-after-explore_armed"):
        _finish(_run_recovery(runner, runtime, _recovery_payload("use_armed")))

    assert [item["state"] for item in runner.progress_writes] == [
        "topup_confirmed",
        "explore_armed",
    ]
    assert (magic.MAGIC_INVASION_USE_SCENE_ID, "使用") not in runtime.clicks
    assert (magic.MAGIC_INVASION_MAP_SCENE_ID, "探查") not in runtime.clicks


@pytest.mark.parametrize("stored_state", ["explore_armed", "armed"])
def test_crash_after_explore_arming_before_click_never_replays_explore(
    monkeypatch,
    stored_state,
) -> None:
    _prepare_recovery_test(monkeypatch, inventory_count=500, map_count=500)
    runtime = _CrashRecoveryRuntime((magic.MAGIC_INVASION_MAP_SCENE_ID,))

    with pytest.raises(RuntimeError, match="禁止重放探查"):
        _finish(_run_recovery(_CrashRecoveryRunner(), runtime, _recovery_payload(stored_state)))

    assert (magic.MAGIC_INVASION_MAP_SCENE_ID, "探查") not in runtime.clicks


def test_crash_after_explore_click_observes_result_before_any_settlement_click(
    monkeypatch,
) -> None:
    _prepare_recovery_test(monkeypatch, inventory_count=500, map_count=0)
    runtime = _CrashRecoveryRuntime((magic.MAGIC_INVASION_RESULT_SCENE_ID,))
    runner = _CrashRecoveryRunner(crash_after_state="result_observed")

    with pytest.raises(RuntimeError, match="crash-after-result_observed"):
        _finish(_run_recovery(runner, runtime, _recovery_payload("explore_armed")))

    assert [item["state"] for item in runner.progress_writes] == ["result_observed"]
    assert runtime.clicks == []


def test_crash_after_result_observed_commits_without_replaying_explore(
    monkeypatch,
) -> None:
    _prepare_recovery_test(monkeypatch, inventory_count=500, map_count=0)
    payload = _recovery_payload("result_observed")
    payload["magic_invasion_progress"]["transaction_evidence"].update({
        "result_explore_count": 500,
        "result_source": "result_page",
        "task_progress_after": {"tasks": []},
    })
    runtime = _CrashRecoveryRuntime(
        (magic.MAGIC_INVASION_RESULT_SCENE_ID, magic.MAGIC_INVASION_MAP_SCENE_ID)
    )

    result = _finish(_run_recovery(_CrashRecoveryRunner(), runtime, payload))

    assert result["result"] == "success"
    assert (magic.MAGIC_INVASION_MAP_SCENE_ID, "探查") not in runtime.clicks
    assert (magic.MAGIC_INVASION_USE_SCENE_ID, "使用") not in runtime.clicks
    assert runtime.clicks[0] == (magic.MAGIC_INVASION_RESULT_SCENE_ID, "确定")


def test_expected_occurrence_cannot_succeed_after_activity_window(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion as magic

    class _AfterWindowDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat("2026-08-22T22:00:01+08:00")
            return value.astimezone(tz) if tz is not None else value

    monkeypatch.setattr(magic, "datetime", _AfterWindowDatetime)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: _schedule(),
    )

    with pytest.raises(RuntimeError, match="零动作记为完成"):
        _finish(
            execute_magic_invasion_explore_job(
                object(),
                {"scheduler_task_id": "ranking-lifecycle"},
                {"expected_occurrence_id": "8070001400004"},
                Event(),
                manage_schedule=False,
            )
        )


def test_shared_job_keeps_progress_persistence_when_legacy_schedule_management_is_off(
    monkeypatch,
) -> None:
    from backend.core.fanxiu.data_annotation.tasks import magic_invasion as magic

    class _DuringWindowDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls.fromisoformat("2026-08-22T19:00:00+08:00")
            return value.astimezone(tz) if tz is not None else value

    class _Runner:
        def __init__(self) -> None:
            self.progress_writes = []

        def _set_scheduler_task_payload_flag(self, task_id, key, value):
            self.progress_writes.append((task_id, key, value))
            return True

        def _persist_scheduler_task_next_time(self, *_args, **_kwargs):
            raise AssertionError("统一 Job 不得让旧魔道内核改写 next_time")

    monkeypatch.setattr(magic, "datetime", _DuringWindowDatetime)
    monkeypatch.setattr(
        "backend.core.fanxiu.activity.runtime_schedule.read_fanxiu_activity_runtime_schedule",
        lambda **_kwargs: _schedule(),
    )
    runner = _Runner()
    payload = {
        "expected_occurrence_id": "8070001400004",
        "magic_invasion_progress": {
            "occurrence_id": "8070001400004",
            "state": "complete",
            "base_explore_count": 1500,
            "confirmed_batches": [{}, {}, {}],
        },
    }

    result = _finish(
        execute_magic_invasion_explore_job(
            runner,
            {"scheduler_task_id": "ranking-lifecycle"},
            payload,
            Event(),
            manage_schedule=False,
        )
    )

    assert result["result"] == "success"
    assert result["performed_actions"] is False
    assert len(runner.progress_writes) == 1
    task_id, key, progress = runner.progress_writes[0]
    assert task_id == "ranking-lifecycle"
    assert key == "magic_invasion_progress"
    assert progress["state"] == "complete"
    assert progress["base_explore_count"] == 1500


def test_magic_invasion_is_owned_by_the_one_visible_ranking_lifecycle_job() -> None:
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        get_fanxiu_data_annotation_task_cell_definition,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    legacy = get_fanxiu_data_annotation_task_cell_definition(
        "magic_invasion_explore"
    )
    definition = get_fanxiu_data_annotation_task_cell_definition("ranking_lifecycle")

    assert legacy is not None
    assert legacy.scheduler_supported is False
    assert legacy.standard_job is False
    assert definition is not None
    assert definition.standard_job is True
    assert definition.standard_job_id == "ranking-lifecycle"
    assert definition.standard_job_description == "动态"
    assert definition.standard_job_payload == {
        "max_runtime_seconds": 10800,
    }
