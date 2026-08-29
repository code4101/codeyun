from __future__ import annotations

import threading
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import default_jobs
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks import storage_bag_operation
from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import (
    EMPTY_OPERATION_TOAST,
    EXPECTED_QUICK_SETTING_VALUES,
    _finish_reward_chain,
    _observe_known_scene,
    _wait_quick_operation_panel,
    execute_storage_bag_operation_task,
    next_storage_bag_operation_at,
)


def _consume(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


class _Runtime:
    def __init__(self, outcomes):
        self.scene = 34
        self.outcomes = list(outcomes)
        self.active_outcome = None
        self.toast_reads = 0
        self.calls = []

    def goto_view(self, scene):
        self.calls.append(("goto", scene))
        self.scene = scene
        yield None

    def wait_scene(self, scene, **kwargs):
        assert self.scene == scene
        self.calls.append(("wait_scene", scene))
        yield None

    def wait_click(self, scene, shape, **kwargs):
        assert self.scene == scene
        self.calls.append(("click", scene, shape))
        if (scene, shape) == (34, "右侧菜单/储物袋"):
            self.scene = 525
        elif (scene, shape) == (525, "快捷操作"):
            self.scene = 526
            self.active_outcome = None
            self.toast_reads = 0
        elif (scene, shape) == (526, "执行快捷操作（高风险）"):
            self.active_outcome = self.outcomes.pop(0)
            self.toast_reads = 0
            self.scene = (
                526
                if self.active_outcome in {"empty", "stable", "delayed_reward"}
                else 999
                if self.active_outcome == "unknown"
                else int(self.active_outcome)
            )
        elif (scene, shape) == (227, "继续"):
            self.scene = 351
        elif (scene, shape) == (351, "继续"):
            self.scene = 525
        elif (scene, shape) == (526, "外部顶部空白"):
            self.scene = 525
        elif (scene, shape) == (525, "返回"):
            self.scene = 34
        yield None

    def shape(self, scene, title):
        raise AssertionError("快捷设置禁止读取视觉 shape")

    def match_shape(self, shape):
        raise AssertionError("快捷设置禁止调用视觉 match")

    def click_shape_center(self, scene, shape):
        raise AssertionError("快捷设置禁止点击 checkbox")

    def wait_action_settle(self, seconds):
        self.calls.append(("settle", seconds))
        yield None

    def cur_frame(self, *, update):
        assert update is True
        if self.active_outcome == "delayed_reward" and self.scene == 526:
            self.toast_reads += 1
            if self.toast_reads >= 4:
                self.scene = 227
        return f"frame-{self.scene}"

    def ocr_text(self, frame):
        if self.active_outcome == "empty" and self.scene == 526:
            self.toast_reads += 1
            if self.toast_reads <= 2:
                return EMPTY_OPERATION_TOAST
        return ""

    def current_scene(self, scenes, *, frame_data_url):
        assert frame_data_url.startswith("frame-")
        return (self.scene if self.scene in scenes else None), 100, frame_data_url


class _Runner:
    def __init__(self, runtime):
        self.runtime = runtime

    def _fanxiu_runtime(self, ctx, asset_tree_path, *, stop_event):
        return self.runtime


@pytest.fixture(autouse=True)
def _authoritative_quick_settings(monkeypatch):
    monkeypatch.setattr(
        storage_bag_operation.fanxiu_instrumentation_service,
        "backpack_quick_settings_snapshot",
        lambda: {
            "ok": True,
            "complete": True,
            "values": dict(EXPECTED_QUICK_SETTING_VALUES),
            "captured_at_epoch": storage_bag_operation.time.time(),
            "evidence": {"pid": 123, "process_start_ticks": 456, "read_only": True},
        },
    )


class _PostExecuteSequenceRuntime(_Runtime):
    def __init__(self, scenes):
        super().__init__([])
        self.scene = 526
        self.scenes = list(scenes)
        self.observed_scenes = []

    def cur_frame(self, *, update):
        assert update is True
        if self.scenes:
            self.scene = self.scenes.pop(0)
        self.observed_scenes.append(self.scene)
        return f"frame-{self.scene}"


def test_storage_bag_runs_reward_chain_then_stops_only_on_exact_empty_toast():
    runtime = _Runtime([227, "empty"])
    result = _consume(execute_storage_bag_operation_task(
        _Runner(runtime),
        {},
        {"max_rounds": 3},
        threading.Event(),
    ))

    assert result == {
        "ok": True,
        "outcome": "complete",
        "completed_rounds": 1,
        "terminal_evidence": EMPTY_OPERATION_TOAST,
    }
    assert runtime.calls[1] == ("click", 34, "右侧菜单/储物袋")
    assert not [call for call in runtime.calls if call[0] == "ocr_click"]
    assert ("click", 227, "继续") in runtime.calls
    assert ("click", 351, "继续") in runtime.calls
    assert runtime.calls[-2:] == [
        ("click", 525, "返回"),
        ("wait_scene", 34),
    ]


def test_storage_bag_rejects_mismatched_read_only_settings_without_checkbox_click(monkeypatch):
    runtime = _Runtime(["empty"])
    monkeypatch.setattr(
        storage_bag_operation.fanxiu_instrumentation_service,
        "backpack_quick_settings_snapshot",
        lambda: {
            "complete": True,
            "values": {"1": 1, "2": 1, "3": 1, "4": 1},
            "captured_at_epoch": storage_bag_operation.time.time(),
            "evidence": {"pid": 123, "process_start_ticks": 456},
        },
    )

    with pytest.raises(RuntimeError, match="OpenBox=OFF"):
        _consume(execute_storage_bag_operation_task(
            _Runner(runtime), {}, {}, threading.Event()
        ))

    assert ("click", 526, "执行快捷操作（高风险）") not in runtime.calls
    assert not [call for call in runtime.calls if call[0] == "checkbox"]


@pytest.mark.parametrize(
    "snapshot",
    [
        {"complete": False, "reason": "Ambiguous"},
        {
            "complete": True,
            "values": {"1": 0, "2": 1, "3": 1, "4": 1},
            "captured_at_epoch": 0,
            "evidence": {"pid": 123, "process_start_ticks": 456},
        },
        {
            "complete": True,
            "values": {"1": 0, "2": 1, "3": 1, "4": 1},
            "captured_at_epoch": 1,
            "evidence": {"pid": None, "process_start_ticks": None},
        },
    ],
)
def test_storage_bag_rejects_incomplete_stale_or_identityless_settings(
    monkeypatch, snapshot
):
    runtime = _Runtime(["empty"])
    monkeypatch.setattr(
        storage_bag_operation.fanxiu_instrumentation_service,
        "backpack_quick_settings_snapshot",
        lambda: snapshot,
    )
    with pytest.raises(RuntimeError, match="缺失、过期或进程身份不完整"):
        _consume(execute_storage_bag_operation_task(
            _Runner(runtime), {}, {}, threading.Event()
        ))
    assert ("click", 526, "执行快捷操作（高风险）") not in runtime.calls


def test_quick_option_contract_is_three_on_and_open_box_off():
    assert EXPECTED_QUICK_SETTING_VALUES == {"1": 0, "2": 1, "3": 1, "4": 1}


def test_stable_fresh_526_is_no_reward_fixed_point_and_closes_safely(monkeypatch):
    runtime = _Runtime([227, 227, "stable"])
    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 1.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    result = _consume(execute_storage_bag_operation_task(
        _Runner(runtime), {}, {}, threading.Event()
    ))

    assert result == {
        "ok": True,
        "outcome": "complete",
        "completed_rounds": 2,
        "terminal_evidence": "stable_fresh_scene_526",
    }
    assert runtime.calls[-4:] == [
        ("click", 526, "外部顶部空白"),
        ("wait_scene", 525),
        ("click", 525, "返回"),
        ("wait_scene", 34),
    ]


def test_storage_bag_unknown_after_execute_fails_without_followup_click(monkeypatch):
    runtime = _Runtime(["unknown"])
    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 20.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    with pytest.raises(TimeoutError, match="未进入奖励链"):
        _consume(execute_storage_bag_operation_task(
            _Runner(runtime),
            {},
            {"quick_operation_result_timeout_seconds": 30},
            threading.Event(),
        ))
    execute_index = runtime.calls.index(
        ("click", 526, "执行快捷操作（高风险）")
    )
    assert not [
        call for call in runtime.calls[execute_index + 1 :]
        if call[0] in {"click", "checkbox"}
    ]


def test_storage_bag_stops_at_max_three_reward_rounds():
    runtime = _Runtime([227] * 3)
    with pytest.raises(RuntimeError, match="已执行 3 轮快捷操作"):
        _consume(execute_storage_bag_operation_task(
            _Runner(runtime), {}, {"max_rounds": 99}, threading.Event()
        ))
    assert sum(
        call == ("click", 526, "执行快捷操作（高风险）")
        for call in runtime.calls
    ) == 3


def test_delayed_reward_after_short_fresh_526_is_not_closed_as_empty(monkeypatch):
    runtime = _Runtime(["delayed_reward", "stable"])
    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 1.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    result = _consume(execute_storage_bag_operation_task(
        _Runner(runtime), {}, {}, threading.Event()
    ))

    assert result["completed_rounds"] == 1
    assert result["terminal_evidence"] == "stable_fresh_scene_526"
    assert ("click", 227, "继续") in runtime.calls
    assert ("click", 351, "继续") in runtime.calls
    assert runtime.calls.index(("click", 526, "外部顶部空白")) > runtime.calls.index(
        ("click", 351, "继续")
    )


def test_delayed_reward_after_transient_storage_background_is_not_skipped(monkeypatch):
    runtime = _PostExecuteSequenceRuntime([525, 525, 351])
    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 1.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    outcome = _consume(_finish_reward_chain(runtime, deadline=100.0))

    assert outcome == "reward_complete"
    assert ("click", 351, "继续") in runtime.calls


def test_stable_storage_landing_requires_full_post_action_window(monkeypatch):
    runtime = _PostExecuteSequenceRuntime([525])
    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 1.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    outcome = _consume(_finish_reward_chain(runtime, deadline=100.0))

    assert outcome == "storage_fixed_point"
    assert runtime.observed_scenes.count(525) >= 6
    assert not [call for call in runtime.calls if call[0] == "click"]


def test_unknown_between_526_windows_resets_fixed_point_clock(monkeypatch):
    runtime = _PostExecuteSequenceRuntime([526, 526, 999, 526])
    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 1.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    outcome = _consume(_finish_reward_chain(runtime, deadline=100.0))

    assert outcome == "empty_fixed_point"
    unknown_index = runtime.observed_scenes.index(999)
    # With the old accumulated window this would finish after only a few
    # post-unknown frames.  A reset requires a full fresh 10-second window.
    assert runtime.observed_scenes[unknown_index + 1 :].count(526) >= 6
    assert not [call for call in runtime.calls if call[0] == "click"]


def test_unknown_observation_waits_without_clicking_then_times_out(monkeypatch):
    runtime = _Runtime([])
    runtime.scene = 999
    moments = iter((0.0, 2.0))
    monkeypatch.setattr(storage_bag_operation.time, "monotonic", lambda: next(moments))

    with pytest.raises(TimeoutError, match="unknown 期间未执行点击"):
        _consume(_observe_known_scene(runtime, (525,), deadline=1.0))
    assert not [call for call in runtime.calls if call[0] in {"click", "checkbox"}]


def test_quick_panel_accepts_shape_contract_when_title_identity_is_missing():
    class Runtime(_Runtime):
        def __init__(self):
            super().__init__([])
            self.scene = 999

        def current_scene(self, scenes, *, frame_data_url):
            return None, 0, None

        def shape_matches(self, scene, title, *, frame_data_url):
            assert scene == 526
            assert frame_data_url == "frame-999"
            return {"matched": True} if title in {
                "四项快捷标签",
                "执行快捷操作（高风险）",
            } else None

    runtime = Runtime()
    result = _consume(_wait_quick_operation_panel(runtime, timeout=1.0))

    assert result["evidence"] == "panel_shape_contract"
    assert not [call for call in runtime.calls if call[0] in {"click", "checkbox"}]


def test_shape_contract_restores_526_for_observe_and_fixed_point(monkeypatch):
    class Runtime(_Runtime):
        def __init__(self):
            super().__init__([])
            self.scene = 999

        def current_scene(self, scenes, *, frame_data_url):
            return None, 0, None

        def shape_matches(self, scene, title, *, frame_data_url):
            return {"matched": True} if scene == 526 and title in {
                "四项快捷标签",
                "执行快捷操作（高风险）",
            } else None

    observed = Runtime()
    landed, _frame = _consume(
        _observe_known_scene(
            observed,
            (526,),
            deadline=storage_bag_operation.time.monotonic() + 100.0,
        )
    )
    assert landed == 526

    clock = {"value": 0.0}

    def monotonic():
        clock["value"] += 1.0
        return clock["value"]

    monkeypatch.setattr(storage_bag_operation.time, "monotonic", monotonic)
    outcome = _consume(_finish_reward_chain(Runtime(), deadline=100.0))
    assert outcome == "empty_fixed_point"


def test_next_time_is_following_day_at_0100():
    assert next_storage_bag_operation_at(
        datetime(2026, 8, 11, 0, 30)
    ) == datetime(2026, 8, 12, 1, 0)


def test_storage_bag_cell_is_independent_daily_standard_job():
    default_jobs.register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "storage_bag_operation"
    )
    assert definition is not None
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "storage-bag-operation"
    assert definition.standard_job_description == "每日"
    tasks = default_data_annotation_scheduler_tasks(datetime(2026, 8, 11, 0, 0))
    matches = [task for task in tasks if task["id"] == "storage-bag-operation"]
    assert len(matches) == 1
    assert matches[0]["task_type"] == "storage_bag_operation"
    assert matches[0]["trigger_description"] == "每日"
    assert matches[0]["next_time"] == "2026-08-11 01:00:00"
    assert matches[0]["payload"] == {"max_rounds": 3}


def test_storage_bag_success_persists_following_daily_trigger(monkeypatch):
    default_jobs.register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "storage_bag_operation"
    )
    writes = []

    class Runner:
        def _persist_scheduler_task_next_time(self, task_id, next_time):
            writes.append((task_id, next_time))

    def execute(*_args, **_kwargs):
        yield None
        return {"ok": True, "outcome": "complete"}

    monkeypatch.setattr(
        storage_bag_operation,
        "execute_storage_bag_operation_task",
        execute,
    )
    monkeypatch.setattr(default_jobs, "job_now", lambda: datetime(2026, 8, 11, 1, 0))
    result = _consume(definition.handler(
        Runner(),
        {},
        {},
        threading.Event(),
    ))

    assert result["ok"] is True
    assert writes == [("storage-bag-operation", "2026-08-12 01:00:00")]
