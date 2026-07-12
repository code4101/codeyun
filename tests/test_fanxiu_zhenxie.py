from __future__ import annotations

from datetime import datetime as RealDateTime

from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks import zhenxie as zhenxie_module
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin


class _FakeDateTime:
    current = RealDateTime(2026, 7, 12, 21, 1, 0)

    @classmethod
    def now(cls):
        return cls.current


class _FakeRuntime:
    def __init__(self):
        self.calls = []

    def goto_view(self, scene_id):
        self.calls.append(("goto_view", scene_id))
        yield

    def wait_click_then_view(self, scene_id, shape, target):
        self.calls.append(("wait_click_then_view", scene_id, shape, target))
        yield

    def wait_click(self, scene_id, shape):
        self.calls.append(("wait_click", scene_id, shape))
        yield

    def wait_action_settle(self, seconds):
        self.calls.append(("wait_action_settle", seconds))
        yield


def test_daily_zhenxie_flow_runs_exact_closed_loop_inside_window(monkeypatch):
    monkeypatch.setattr(zhenxie_module, "datetime", _FakeDateTime)
    runtime = _FakeRuntime()

    flow = ZhenxieTaskMixin().daily_zhenxie_flow(runtime)
    while True:
        try:
            next(flow)
        except StopIteration as exc:
            result = exc.value
            break

    assert runtime.calls == [
        ("goto_view", 34),
        ("wait_click_then_view", 34, "\u65e5\u7a0b", 66),
        ("wait_click_then_view", 66, "\u524d\u5f80", 63),
        ("wait_click_then_view", 63, "\u524d\u5f80", 271),
        ("wait_click_then_view", 271, "\u53c2\u52a0", 272),
        ("wait_click", 272, "\u524d\u5f80"),
        ("wait_action_settle", 30.0),
    ]
    assert result["result"] == "success"
    assert result["next_time"] == "2026-07-13 21:00:00"


def test_daily_zhenxie_flow_skips_all_actions_outside_window(monkeypatch):
    _FakeDateTime.current = RealDateTime(2026, 7, 12, 21, 5, 1)
    monkeypatch.setattr(zhenxie_module, "datetime", _FakeDateTime)
    runtime = _FakeRuntime()

    flow = ZhenxieTaskMixin().daily_zhenxie_flow(runtime)
    with_result = None
    try:
        next(flow)
    except StopIteration as exc:
        with_result = exc.value

    assert runtime.calls == []
    assert with_result["result"] == "success"
    assert with_result["next_time"] == "2026-07-13 21:00:00"


def test_daily_zhenxie_scheduler_definition_is_runtime_job():
    task = next(item for item in default_data_annotation_scheduler_tasks() if item["id"] == "daily-zhenxie")

    assert task["task_type"] == "daily_zhenxie"
    assert task["source"] == "data_annotation_runtime"
    assert task["enabled"] is True
    assert task["schedule_times"] == ["21:00"]
    assert task["window"] == ["21:00", "21:05"]
