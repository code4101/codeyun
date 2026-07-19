from __future__ import annotations

from datetime import datetime as RealDateTime
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.scheduler import repair_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.tasks import mozu as mozu_module
from backend.core.fanxiu.data_annotation.tasks.mozu import MozuTaskMixin


class _FakeDateTime:
    current = RealDateTime(2026, 7, 13, 12, 31, 0)

    @classmethod
    def now(cls):
        return cls.current


class _FakeRuntime:
    def __init__(self, completed_scene_id=34):
        self.calls = []
        self.completed_scene_id = completed_scene_id

    def go_scene(self, scene_id):
        self.calls.append(("go_scene", scene_id))
        yield

    def wait_click_then_view(self, scene_id, shape, target):
        self.calls.append(("wait_click_then_view", scene_id, shape, target))
        yield
        if scene_id == 337:
            return SimpleNamespace(id=self.completed_scene_id)

    def wait_action_settle(self, seconds):
        self.calls.append(("wait_action_settle", seconds))
        yield


def _finish(flow):
    while True:
        try:
            next(flow)
        except StopIteration as exc:
            return exc.value


@pytest.mark.parametrize("completed_scene_id", [34, 339])
def test_daily_mozu_flow_completes_immediately_on_world_or_auction(monkeypatch, completed_scene_id):
    _FakeDateTime.current = RealDateTime(2026, 7, 13, 12, 31, 0)
    monkeypatch.setattr(mozu_module, "datetime", _FakeDateTime)
    runtime = _FakeRuntime(completed_scene_id)

    result = _finish(MozuTaskMixin().daily_mozu_flow(runtime))

    assert runtime.calls == [
        ("go_scene", 34),
        ("wait_click_then_view", 34, "日程", 66),
        ("wait_click_then_view", 66, "前往", 336),
        ("wait_click_then_view", 336, "前往", 337),
        ("wait_click_then_view", 337, "前往", [338, 34, 339]),
    ]
    assert result == {
        "result": "success",
        "message": f"日常_魔祖：已参与，完成落点 #{completed_scene_id}",
        "next_time": "2026-07-14 12:30:00",
        "current_scene": completed_scene_id,
    }


def test_daily_mozu_flow_waits_30_seconds_after_battle_scene(monkeypatch):
    _FakeDateTime.current = RealDateTime(2026, 7, 13, 12, 31, 0)
    monkeypatch.setattr(mozu_module, "datetime", _FakeDateTime)
    runtime = _FakeRuntime(338)

    result = _finish(MozuTaskMixin().daily_mozu_flow(runtime))

    assert runtime.calls[-2:] == [
        ("wait_click_then_view", 337, "前往", [338, 34, 339]),
        ("wait_action_settle", 30.0),
    ]
    assert result["result"] == "success"
    assert result["message"] == "日常_魔祖：已参与，完成落点 #338"
    assert result["current_scene"] == 338


def test_daily_mozu_flow_skips_all_actions_after_window(monkeypatch):
    _FakeDateTime.current = RealDateTime(2026, 7, 13, 12, 35, 1)
    monkeypatch.setattr(mozu_module, "datetime", _FakeDateTime)
    runtime = _FakeRuntime()

    result = _finish(MozuTaskMixin().daily_mozu_flow(runtime))

    assert runtime.calls == []
    assert result["result"] == "success"
    assert result["next_time"] == "2026-07-14 12:30:00"


def test_daily_mozu_flow_skips_before_window_and_keeps_today_trigger(monkeypatch):
    _FakeDateTime.current = RealDateTime(2026, 7, 13, 12, 29, 59)
    monkeypatch.setattr(mozu_module, "datetime", _FakeDateTime)
    runtime = _FakeRuntime()

    result = _finish(MozuTaskMixin().daily_mozu_flow(runtime))

    assert runtime.calls == []
    assert result["next_time"] == "2026-07-13 12:30:00"


def test_daily_mozu_scheduler_definition_is_enabled_runtime_job():
    task = next(item for item in default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-mozu")

    assert task["task_type"] == "daily_mozu"
    assert task["source"] == "data_annotation_runtime"
    assert task["enabled"] is True
    assert task["schedule_times"] == ["12:30"]
    assert task["window"] == ["12:30", "12:35"]


def test_daily_mozu_scheduler_migrates_legacy_placeholder_enabled():
    tasks, changed = repair_data_annotation_scheduler_tasks(
        [
            {
                "id": "legacy-daily-mozu",
                "task_type": "legacy_daily_task",
                "label": "日常 魔祖",
                "source": "legacy_behavior_tree",
                "schedule_kind": "daily",
                "legacy_name": "日常_魔祖",
                "enabled": False,
                "schedule_times": ["12:29"],
                "window": ["12:29", "12:35"],
                "payload": {"legacy_name": "日常_魔祖"},
            }
        ],
        default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: task.get("task_type") == "daily_mozu",
        now=RealDateTime(2026, 7, 13, 12, 48, 0),
    )
    task = next(item for item in tasks if item["id"] == "legacy-daily-mozu")

    assert changed is True
    assert task["task_type"] == "daily_mozu"
    assert task["source"] == "data_annotation_runtime"
    assert task["enabled"] is True
    assert task["schedule_times"] == ["12:30"]
    assert task["window"] == ["12:30", "12:35"]
