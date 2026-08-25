from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition,
)
from backend.core.fanxiu.behavior_tree.runtime import create_behavior_tree_runtime_runner
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.daozu_challenge import (
    next_daozu_challenge_time,
)


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


def _state(*, pass_count: int) -> dict:
    return {
        "ok": True,
        "current": 262,
        "currentMax": 261,
        "passCount": pass_count,
        "daoLevel": 5,
        "remaining": 20 - pass_count,
        "limit": 20,
    }


def _capture_next_time(runner):
    updates = []
    runner._persist_scheduler_task_next_time = lambda task_id, next_time: updates.append((task_id, next_time))
    return updates


class _FakeRuntime:
    def __init__(self, scenes=(251,), *, payload=None, ocr_texts=(), shape_scores=None, panel_open=False, panel_collapsed=False):
        self.completion_message = ""
        self.stop_event = threading.Event()
        self.payload = payload or {"monitor_poll_interval": 0.1}
        self._scenes = iter(scenes)
        self._last_scene = 251
        self.actions = []
        self._ocr_texts = iter(ocr_texts)
        self._shape_scores = dict(shape_scores or {})
        self._panel_open = bool(panel_open)
        self._panel_collapsed = bool(panel_collapsed)

    def set_completion_message(self, message: str) -> None:
        self.completion_message = message

    def current_scene(self, _candidates, **_kwargs):
        self._last_scene = next(self._scenes, self._last_scene)
        return self._last_scene, 100.0, "frame"

    def cur_frame(self, **_kwargs):
        return "frame"

    def ocr_fragments_in_shapes(self, _scene_id, _shapes, **_kwargs):
        if self._panel_collapsed:
            return []
        return [{"text": "创建队伍"}] if self._panel_open else [{"text": "任务"}]

    def click_ocr_text(self, scene_id, title, **kwargs):
        self.actions.append(("click_ocr", scene_id, title, kwargs.get("match_mode")))

    def goto_view(self, scene_id):
        self.actions.append(("goto", scene_id))
        if False:
            yield None

    def wait_click(self, scene_id, title):
        self.actions.append(("wait_click", scene_id, title))
        if False:
            yield None

    def wait_view(self, scene_id, **_kwargs):
        self.actions.append(("wait_view", scene_id))
        if False:
            yield None
        return scene_id

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        if False:
            yield None

    def ocr_text(self, _frame):
        return next(self._ocr_texts, "下一层(8秒)")

    def view(self, scene_id):
        assert scene_id == 189

        class View:
            raw = {"shapes": [{"title": "点击退出"}]}

        return View()

    def click_shape_center(self, scene_id, title):
        self.actions.append(("click_shape", scene_id, title))

    def shape_score(self, scene_id, title, **_kwargs):
        return self._shape_scores.get((scene_id, title), 0.0)


@pytest.mark.parametrize(
    ("pass_count", "remaining"),
    ((0, 20), (20, 0)),
)
def test_daozu_state_fuses_fresh_runtime_pass_count_with_configured_limit(
    monkeypatch, pass_count, remaining
):
    from backend.core.fanxiu.instrumentation import daozu_road

    monkeypatch.setattr(
        daozu_road,
        "read_daozu_road_snapshot",
        lambda: {
            "available": True,
            "complete": False,
            "daily_pass_count": pass_count,
            "daily_limit": None,
            "daily_remaining": None,
        },
    )
    state = create_behavior_tree_runtime_runner()._read_daozu_challenge_state()

    assert state["ok"] is True
    assert state["passCount"] == pass_count
    assert state["limit"] == 20
    assert state["remaining"] == remaining
    assert state["source"] == "runtime_memory_with_configured_limit"
    assert state["runtime"]["complete"] is False


@pytest.mark.parametrize("pass_count", (None, -1, 21, True))
def test_daozu_state_refuses_packet_fallback_for_unusable_runtime_pass_count(
    monkeypatch, pass_count
):
    from backend.core.fanxiu.instrumentation import daozu_road

    monkeypatch.setattr(
        daozu_road,
        "read_daozu_road_snapshot",
        lambda: {
            "available": True,
            "complete": False,
            "daily_pass_count": pass_count,
        },
    )
    state = create_behavior_tree_runtime_runner()._read_daozu_challenge_state()

    assert state["ok"] is False
    assert state["source"] == "runtime_memory"
    assert state["reason"] == "runtime_incomplete_daily_pass_count_invalid_or_missing"
    assert state["runtime"]["daily_pass_count"] is pass_count


def test_daozu_state_reports_runtime_failure_without_packet_fallback(monkeypatch):
    from backend.core.fanxiu.instrumentation import daozu_road

    monkeypatch.setattr(
        daozu_road,
        "read_daozu_road_snapshot",
        lambda: {
            "available": True,
            "complete": False,
            "daily_pass_count": None,
        },
    )
    state = create_behavior_tree_runtime_runner()._read_daozu_challenge_state()

    assert state["ok"] is False
    assert state["source"] == "runtime_memory"
    assert state["reason"] == "runtime_incomplete_daily_pass_count_invalid_or_missing"


def test_daozu_flow_refuses_action_without_fresh_state():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeRuntime()
    runner._read_daozu_challenge_state = lambda: {"ok": False}

    with pytest.raises(RuntimeError, match="缺少短时新鲜"):
        _drain(runner.道祖挑战流程(runtime))

    assert runtime.completion_message == ""


def test_daozu_flow_reconciles_start_mark_on_route_before_reporting_missing_fact():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeRuntime(
        (34, 251),
        payload={"daozu_auto_chain_started_at": "2026-08-15T07:00:00"},
        panel_open=True,
    )
    runner._read_daozu_challenge_state = lambda: {
        "ok": False,
        "source": "runtime_memory",
        "reason": "runtime_unavailable",
    }

    with pytest.raises(RuntimeError, match="保留未收口防重复标记") as exc_info:
        _drain(runner.道祖挑战流程(runtime))

    assert "重复点击" in str(exc_info.value)
    assert runtime.actions == [
        ("wait_click", 34, "任务"),
        ("settle", 0.8),
        ("wait_click", 34, "主线"),
        ("wait_view", 251),
    ]


def test_daozu_flow_expands_collapsed_task_panel_before_opening_route():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeRuntime(
        (34, 251),
        payload={"daozu_auto_chain_started_at": "2026-08-15T07:00:00"},
        panel_collapsed=True,
    )
    runner._read_daozu_challenge_state = lambda: {
        "ok": False,
        "source": "runtime_memory",
        "reason": "runtime_unavailable",
    }

    with pytest.raises(RuntimeError, match="保留未收口防重复标记"):
        _drain(runner.道祖挑战流程(runtime))

    assert runtime.actions[:6] == [
        ("wait_click", 34, "展开任务组队面板"),
        ("settle", 0.8),
        ("wait_click", 34, "任务"),
        ("settle", 0.8),
        ("wait_click", 34, "主线"),
        ("wait_view", 251),
    ]


def test_daozu_flow_treats_realm_locked_as_idempotent_success_and_clears_bad_mark(monkeypatch):
    from backend.core.fanxiu.data_annotation import behavior_tree_runtime

    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 8, 15, 7, 1),
    )
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeRuntime(
        (34, 251),
        payload={"daozu_auto_chain_started_at": "2026-08-15T08:11:56"},
        shape_scores={(251, "境界未解锁"): 100.0},
    )
    runner._read_daozu_challenge_state = lambda: _state(pass_count=0)
    cleared = []
    runner._clear_scheduler_task_payload_flag = lambda *args: cleared.append(args)

    result = _drain(runner.道祖挑战流程(runtime))

    assert result is None
    assert updates[0] == ("daozu-challenge", "2026-08-16 07:00:00")
    assert cleared == [("daozu-challenge", "daozu_auto_chain_started_at")]
    assert runtime.payload.get("daozu_auto_chain_started_at") is None
    assert runtime.actions == [
        ("wait_click", 34, "主线"),
        ("wait_view", 251),
        ("goto", 34),
    ]
    assert "境界未达到解锁要求" in runtime.completion_message


def test_daozu_flow_missing_fact_error_includes_source_and_reason():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeRuntime((34,))
    runner._read_daozu_challenge_state = lambda: {
        "ok": False,
        "source": "packet_capture",
        "reason": "no_recent_daozu_realm_sync_fact",
    }

    with pytest.raises(RuntimeError, match="source=packet_capture") as exc_info:
        _drain(runner.道祖挑战流程(runtime))

    assert "reason=no_recent_daozu_realm_sync_fact" in str(exc_info.value)
    assert "防重复标记" not in str(exc_info.value)
    assert runtime.actions == []


def test_daozu_flow_finishes_without_action_when_daily_limit_reached():
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeRuntime()
    runner._read_daozu_challenge_state = lambda: _state(pass_count=20)

    assert _drain(runner.道祖挑战流程(runtime)) is None
    assert updates[0][1].endswith("07:00:00")
    assert runtime.completion_message == "道祖_挑战结束，运行态显示今日剩余 0/20，已回到世界"
    assert ("goto", 34) in runtime.actions


def test_daozu_flow_does_not_let_unscoped_full_frame_text_override_runtime_state():
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeRuntime((251,), ocr_texts=("已达今日层数挑战上限 (20/20)",))
    runner._read_daozu_challenge_state = lambda: _state(pass_count=3)
    runner._set_scheduler_task_payload_flag = lambda *_args: True

    runtime._scenes = iter((251, 251, 533))
    states = iter((_state(pass_count=3), _state(pass_count=20)))
    runner._read_daozu_challenge_state = lambda: next(states)
    runner._clear_scheduler_task_payload_flag = lambda *_args: None
    result = _drain(runner.道祖挑战流程(runtime))

    assert result is None
    assert updates[0][1].endswith("07:00:00")

    assert runtime.actions.count(("wait_click", 251, "挑战")) == 1
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_daozu_flow_start_mark_attaches_from_unknown_battle_without_reclick():
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeRuntime(
        (None, None, 548, None, 533),
        payload={"daozu_auto_chain_started_at": "2026-08-14T07:01:00"},
    )
    runner._read_daozu_challenge_state = lambda: _state(pass_count=20)
    runner._clear_scheduler_task_payload_flag = lambda *_args: None

    result = _drain(runner.道祖挑战流程(runtime))

    assert result is None
    assert updates[0][1].endswith("07:00:00")
    assert ("wait_click", 548, "下一层") in runtime.actions
    assert ("wait_click", 533, "点击退出") in runtime.actions
    assert ("wait_click", 251, "挑战") not in runtime.actions


def test_daozu_flow_start_mark_blocks_second_start_click():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeRuntime(
        (251, 251),
        payload={"daozu_auto_chain_started_at": "2026-08-11T03:50:00"},
        ocr_texts=("挑战", "挑战"),
    )
    runner._read_daozu_challenge_state = lambda: _state(pass_count=3)

    with pytest.raises(RuntimeError, match="防重复标记"):
        _drain(runner.道祖挑战流程(runtime))

    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_daozu_flow_clicks_once_and_accelerates_only_countdown_next_layer():
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeRuntime(
        (251, 251, None, 548, None, 533),
    )
    states = iter((_state(pass_count=1), _state(pass_count=20)))
    runner._read_daozu_challenge_state = lambda: next(states)
    start_marks = []
    def persist_start_mark(*args):
        start_marks.append(("set", args))
        return True

    runner._set_scheduler_task_payload_flag = persist_start_mark
    runner._clear_scheduler_task_payload_flag = lambda *args: start_marks.append(("clear", args))

    assert _drain(runner.道祖挑战流程(runtime)) is None
    assert updates[0][1].endswith("07:00:00")

    assert runtime.actions.count(("wait_click", 251, "挑战")) == 1
    assert not [
        action
        for action in runtime.actions
        if action[:3] == ("click_ocr", 251, "挑战")
    ]
    assert runtime.actions.count(("wait_click", 548, "下一层")) == 1
    assert runtime.actions.count(("wait_click", 533, "点击退出")) == 1
    assert runtime.actions.count(("wait_view", 251)) == 1
    assert [action for action in runtime.actions if action[0] == "goto"] == [("goto", 34)]
    assert [item[0] for item in start_marks] == ["set", "clear"]
    assert "每日20层" in runtime.completion_message


def test_daozu_flow_attaches_to_ordinary_result_then_closes_daily_limit_result():
    runner = create_behavior_tree_runtime_runner()
    updates = _capture_next_time(runner)
    runtime = _FakeRuntime(
        (548, 548, 533),
    )
    runner._read_daozu_challenge_state = lambda: _state(pass_count=20)
    runner._clear_scheduler_task_payload_flag = lambda *_args: None

    assert _drain(runner.道祖挑战流程(runtime)) is None
    assert updates[0][1].endswith("07:00:00")

    assert ("wait_click", 548, "下一层") in runtime.actions
    assert ("wait_click", 533, "点击退出") in runtime.actions
    assert not [action for action in runtime.actions if action[0] == "click_ocr"]
    assert "每日20层" in runtime.completion_message


def test_daozu_flow_refuses_click_when_start_mark_persistence_fails():
    runner = create_behavior_tree_runtime_runner()
    runtime = _FakeRuntime((251, 251), ocr_texts=("挑战", "挑战"))
    runner._read_daozu_challenge_state = lambda: _state(pass_count=3)
    runner._set_scheduler_task_payload_flag = lambda *_args: False

    with pytest.raises(RuntimeError, match="防重复标记未确认持久化"):
        _drain(runner.道祖挑战流程(runtime))

    assert not [action for action in runtime.actions if action[0] == "click_ocr"]


def test_scheduler_payload_flag_reports_missing_task_and_write_failure(monkeypatch):
    from backend.core.fanxiu.data_annotation import behavior_tree_control

    runner = create_behavior_tree_runtime_runner()
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [])
    assert runner._set_scheduler_task_payload_flag("missing", "start_mark", "value") is False

    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_tasks",
        lambda **_kwargs: [{"id": "daozu-challenge", "payload": {}}],
    )

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(behavior_tree_control, "update_scheduler_tasks", fail_write)
    assert (
        runner._set_scheduler_task_payload_flag("daozu-challenge", "start_mark", "value")
        is False
    )


def test_daozu_job_is_single_daily_standard_scheduler_instance():
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daozu_challenge")
    assert definition is not None
    assert definition.label == "道祖_挑战"
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    assert definition.standard_job_id == "daozu-challenge"
    assert definition.standard_job_description == "每日"
    assert definition.standard_job_payload == {"max_runtime_seconds": 1800}

    tasks = default_data_annotation_scheduler_tasks(now=None)
    matches = [task for task in tasks if task.get("task_type") == "daozu_challenge"]
    assert len(matches) == 1
    assert matches[0]["id"] == "daozu-challenge"
    assert matches[0]["trigger_description"] == "每日"
    assert str(matches[0]["next_time"]).endswith("07:00:00")
    assert matches[0]["payload"] == {"max_runtime_seconds": 1800}


def test_daozu_handler_delegates_next_time_ownership_to_business():
    calls: list[tuple] = []

    class FakeRunner:
        def _execute_daozu_challenge_task(self, ctx, stop_event, payload):
            calls.append(("execute", ctx, stop_event, payload))
            yield "running"
            return "success"

        def _persist_scheduler_task_next_time(self, task_id, next_time):
            calls.append(("next_time", task_id, next_time))

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition("daozu_challenge")
    assert definition is not None
    stop_event = threading.Event()
    operation = definition.handler(FakeRunner(), {"ctx": 1}, {"max_rounds": 20}, stop_event)

    assert next(operation) == "running"
    with pytest.raises(StopIteration) as stopped:
        next(operation)
    assert stopped.value.value == "success"
    assert calls == [("execute", {"ctx": 1}, stop_event, {"max_rounds": 20})]


def test_daozu_executor_does_not_delegate_scheduling_to_runtime_wrapper():
    runner = create_behavior_tree_runtime_runner()
    captured = {}

    def fake_execute(*_args, **kwargs):
        captured.update(kwargs)
        return "operation"

    runner._execute_daily_runtime_task = fake_execute

    assert runner._execute_daozu_challenge_task({}, threading.Event(), {}) == "operation"
    assert "schedule_next" not in captured


def test_next_daozu_challenge_time_is_absolute_next_0700() -> None:
    assert next_daozu_challenge_time(datetime(2026, 8, 13, 6, 59, 59)) == datetime(2026, 8, 13, 7)
    assert next_daozu_challenge_time(datetime(2026, 8, 13, 7)) == datetime(2026, 8, 14, 7)


def test_daily_runtime_finish_only_records_status_and_never_schedules():
    runner = create_behavior_tree_runtime_runner()
    updates = []
    runner._persist_scheduler_task_next_time = lambda task_id, next_time: updates.append(
        (task_id, next_time)
    )

    runner._finish_daily_runtime_task(
        task_type="daozu_challenge",
        label="道祖_挑战",
        message="道祖_挑战结束，今日已完成",
    )

    assert updates == []
    assert runner._status["message"] == "道祖_挑战结束，今日已完成"


def test_daily_runtime_wrapper_does_not_interpret_business_result_as_run_error():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        attrs = {}

    runtime = Runtime()
    runner._fanxiu_runtime = lambda *_args, **_kwargs: runtime
    runner._wait_runtime_action_settle = lambda *_args, **_kwargs: iter(())
    updates = []
    runner._persist_scheduler_task_next_time = lambda *args: updates.append(args)

    result = _drain(runner._execute_daily_runtime_task(
        {"asset_tree_path": Path("asset-tree.json")},
        threading.Event(),
        {"__scheduler_task_id": "probe"},
        task_type="probe",
        label="探针",
        flow=lambda _runtime: {"result": "failed", "message": "业务判断失败"},
    ))

    assert result == "success"
    assert updates == []
    assert runner._status["message"] == "业务判断失败"


def test_daily_runtime_wrapper_rejects_returned_next_time():
    runner = create_behavior_tree_runtime_runner()

    class Runtime:
        attrs = {}

    runner._fanxiu_runtime = lambda *_args, **_kwargs: Runtime()
    runner._wait_runtime_action_settle = lambda *_args, **_kwargs: iter(())

    with pytest.raises(RuntimeError, match="正式 flow 不得返回 next_time"):
        _drain(runner._execute_daily_runtime_task(
            {"asset_tree_path": Path("asset-tree.json")},
            threading.Event(),
            {"__scheduler_task_id": "probe"},
            task_type="probe",
            label="探针",
            flow=lambda _runtime: {"result": "success", "next_time": "2099-01-01 00:00:00"},
        ))
    assert "下次" not in runner._status["message"]
