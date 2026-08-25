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


def test_daily_zhenxie_admission_allows_inside_window(monkeypatch):
    monkeypatch.setattr(zhenxie_module, "datetime", _FakeDateTime)

    assert ZhenxieTaskMixin().daily_zhenxie_admission() is None


def test_daily_zhenxie_admission_skips_all_actions_outside_window(monkeypatch):
    _FakeDateTime.current = RealDateTime(2026, 7, 12, 21, 5, 1)
    monkeypatch.setattr(zhenxie_module, "datetime", _FakeDateTime)

    with_result = ZhenxieTaskMixin().daily_zhenxie_admission()

    assert with_result["result"] == "success"
    assert with_result["next_time"] == "2026-07-13 21:00:00"


def test_daily_zhenxie_scheduler_definition_is_runtime_job():
    task = next(item for item in default_data_annotation_scheduler_tasks() if item["id"] == "daily-zhenxie")

    assert task["task_type"] == "daily_zhenxie"
    assert task["source"] == "data_annotation_runtime"
    assert task["trigger_description"] == "每日"
    assert task["next_time"]
    assert task["dispatch_level"] == 1
    assert "window" not in task
