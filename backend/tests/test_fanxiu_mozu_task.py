from __future__ import annotations

from datetime import datetime as RealDateTime
from types import SimpleNamespace

from backend.core.fanxiu.data_annotation.tasks import mozu
from backend.core.fanxiu.data_annotation.tasks.mozu import MozuTaskMixin
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    ScheduleActivityNotFoundError,
)


class _InMozuWindow(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 7, 22, 12, 31, 0, tzinfo=tz)


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def test_mozu_waits_three_seconds_after_entering_scene_66(monkeypatch):
    calls = []
    snapshots = iter(
        [
            {"complete": True, "left_times": 2},
            {"complete": True, "left_times": 1},
            {"complete": True, "left_times": 1},
        ]
    )

    def select_activity(_runtime, pattern, **options):
        calls.append(("select_schedule_activity", pattern, options))
        if False:
            yield None

    monkeypatch.setattr(mozu, "select_schedule_activity", select_activity)

    class Runtime:
        def set_next_time(self, next_time):
            calls.append(("set_next_time", next_time))

        def go_scene(self, scene_id):
            calls.append(("go_scene", scene_id))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            if False:
                yield None

        def wait_click_then_view(self, scene_id, shape, target):
            calls.append(("wait_click_then_view", scene_id, shape, target))
            if False:
                yield None
            landing = target[0] if isinstance(target, list) else target
            return SimpleNamespace(id=landing)

        def wait_view(self, *scene_ids, **options):
            calls.append(("wait_view", scene_ids, options))
            if False:
                yield None
            return SimpleNamespace(id=336 if scene_ids == (336,) else 34)

        def goto_view(self, scene_id):
            calls.append(("goto_view", scene_id))
            if False:
                yield None
            return SimpleNamespace(id=scene_id)

    monkeypatch.setattr(mozu, "job_now", _InMozuWindow.now)
    monkeypatch.setattr(
        mozu,
        "read_demon_boss_snapshot",
        lambda: next(snapshots),
    )
    result = _drain(MozuTaskMixin().daily_mozu_flow(Runtime()))

    assert result["result"] == "success"
    assert result["runtime_confirmed"] is True
    assert result["left_times"] == 1
    assert result["current_scene"] == 34
    assert calls.index(("wait_action_settle", 3.0)) < calls.index(
        ("select_schedule_activity", "魔祖", {"enter": True})
    )
    assert ("wait_action_settle", 30.0) in calls
    transition_wait = (
        "wait_view",
        (20, 34, 339),
        {
            "timeout": 120.0,
            "label": "日常_魔祖：等待战场结束并落到可返回页面",
        },
    )
    assert transition_wait in calls
    assert calls.index(("wait_action_settle", 30.0)) < calls.index(transition_wait)
    assert calls.index(transition_wait) < calls.index(("goto_view", 34))
    assert result["entry_observed"] is True
    assert result["exit_confirmed"] is True


def test_mozu_runtime_probe_is_advisory_when_unavailable(monkeypatch):
    calls = []

    def select_activity(_runtime, pattern, **options):
        calls.append(("select_schedule_activity", pattern, options))
        if False:
            yield None

    monkeypatch.setattr(mozu, "select_schedule_activity", select_activity)

    class Runtime:
        def set_next_time(self, next_time):
            calls.append(("set_next_time", next_time))

        def go_scene(self, scene_id):
            calls.append(("go_scene", scene_id))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            if False:
                yield None

        def wait_click_then_view(self, scene_id, shape, target):
            calls.append(("wait_click_then_view", scene_id, shape, target))
            if False:
                yield None
            return SimpleNamespace(id=34 if scene_id == 337 else target)

        def goto_view(self, scene_id):
            calls.append(("goto_view", scene_id))
            if False:
                yield None
            return SimpleNamespace(id=scene_id)

        def wait_view(self, *scene_ids, **options):
            calls.append(("wait_view", scene_ids, options))
            if False:
                yield None
            if scene_ids == (336,):
                return SimpleNamespace(id=336)
            raise TimeoutError("no delayed battlefield")

    monkeypatch.setattr(mozu, "job_now", _InMozuWindow.now)
    monkeypatch.setattr(
        mozu,
        "read_demon_boss_snapshot",
        lambda: {"complete": False, "reason": "not loaded"},
    )

    result = _drain(MozuTaskMixin().daily_mozu_flow(Runtime()))

    assert result["result"] == "success"
    assert result["runtime_confirmed"] is False
    assert result["current_scene"] == 34
    assert result["entry_observed"] is False
    assert "未观察到战场" in result["message"]
    assert "未重复进入" in result["message"]
    assert ("wait_action_settle", 30.0) not in calls


def test_mozu_admission_uses_job_effective_time() -> None:
    decisions = []

    class Runner(MozuTaskMixin):
        @staticmethod
        def _persist_admission_decision(_payload, decision):
            decisions.append(decision)
            return decision

    from backend.core.fanxiu.data_annotation.effective_time import job_effective_time

    with job_effective_time({"effective_now": "2026-08-23 12:31:00"}):
        assert Runner().daily_mozu_admission(
            {"__scheduler_task_id": "daily-mozu"}
        ) is None

    with job_effective_time({"effective_now": "2026-08-23 12:41:00"}):
        result = Runner().daily_mozu_admission(
            {"__scheduler_task_id": "daily-mozu"}
        )

    assert result is decisions[-1]
    assert result["next_time"] == "2026-08-24 12:30:00"
    assert "未执行游戏操作" in result["message"]


def test_mozu_skips_only_after_exhaustive_activity_card_scan(monkeypatch):
    calls = []

    def missing_activity(_runtime, _pattern, **_options):
        if False:
            yield None
        raise ScheduleActivityNotFoundError("missing", exhaustive=True)

    monkeypatch.setattr(mozu, "select_schedule_activity", missing_activity)
    monkeypatch.setattr(mozu, "job_now", _InMozuWindow.now)

    class Runtime:
        def go_scene(self, scene_id):
            calls.append(("go_scene", scene_id))
            if False:
                yield None

        def wait_click_then_view(self, scene_id, shape, target):
            calls.append(("wait_click_then_view", scene_id, shape, target))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            calls.append(("wait_action_settle", seconds))
            if False:
                yield None

        def set_next_time(self, next_time):
            calls.append(("set_next_time", next_time))

    result = _drain(MozuTaskMixin().daily_mozu_flow(Runtime()))

    assert result["result"] == "success"
    assert "逐页核对" in result["message"]
    assert ("set_next_time", "2026-07-23 12:30:00") in calls
