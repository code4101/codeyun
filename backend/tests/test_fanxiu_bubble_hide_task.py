from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.tasks import bubble_hide as bubble_hide_module
from backend.core.fanxiu.data_annotation.tasks.bubble_hide import BubbleHideTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import (
    read_bubble_lifecycle_fact,
    record_bubble_claim_success,
    record_bubble_hidden,
)


def _done(value=None):
    if False:
        yield None
    return value


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


class _Runtime:
    def __init__(self, *, visible: bool = True, overlay_scene: int | None = None) -> None:
        self.visible = visible
        self.overlay_scene = overlay_scene
        self.actions: list[tuple] = []

    def current_scene(self, *_args, **_kwargs):
        raise AssertionError("top-level bubble hiding must not classify the game scene")

    def cur_frame(self, *, update: bool):
        assert update is True
        return "frame"

    def match_view(self, scene_id, **kwargs):
        assert kwargs.get("frame_data_url") == "frame"
        return scene_id == self.overlay_scene, 100.0, "frame"

    def shape_matches(self, _scene_id, shape, **kwargs):
        assert kwargs.get("frame_data_url") == "frame"
        if shape == "奖励浮层":
            return None
        if shape != "气泡":
            raise AssertionError(shape)
        if not self.visible:
            return None
        return {
            "matched": True,
            "unique_match": True,
            "resolved_box": {"x": 17.0, "y": 650.0, "w": 57.0, "h": 67.0},
        }

    def drag_shape_to_shape(self, *args, **kwargs):
        self.actions.append(("drag", args, kwargs))
        self.visible = False

    def wait_action_settle(self, seconds):
        self.actions.append(("settle", seconds))
        return _done()


class _DelayedBubbleRuntime(_Runtime):
    def __init__(self, *, appears_after_checks: int) -> None:
        super().__init__(visible=False)
        self.appears_after_checks = appears_after_checks
        self.bubble_checks = 0
        self.has_appeared = False

    def shape_matches(self, scene_id, shape, **kwargs):
        if shape == "气泡" and not self.has_appeared:
            self.bubble_checks += 1
            if self.bubble_checks > self.appears_after_checks:
                self.visible = True
                self.has_appeared = True
        return super().shape_matches(scene_id, shape, **kwargs)


class _FailedDragRuntime(_Runtime):
    def drag_shape_to_shape(self, *args, **kwargs):
        self.actions.append(("drag", args, kwargs))


class _Runner(BubbleHideTaskMixin):
    def __init__(self, runtime: _Runtime, facts_path: Path) -> None:
        self.runtime = runtime
        self.facts_path = facts_path
        self.next_times: list[tuple[str, str | None]] = []
        self.logs: list[tuple[str, str]] = []

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _bubble_lifecycle_world_facts_path(self):
        return self.facts_path

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _log(self, level, message):
        self.logs.append((level, message))


def test_bubble_hide_drags_once_and_requires_two_absent_world_frames(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    facts_path = tmp_path / "world-facts.json"
    record_bubble_claim_success(facts_path, now=now, claim_count=1)
    runtime = _Runtime()
    runner = _Runner(runtime, facts_path)

    result = _drain(runner._ensure_bubble_hidden(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {"__scheduler_task_id": "bubble-hide"},
    ))

    assert result["result"] == "success"
    assert [action[0] for action in runtime.actions].count("drag") == 1
    drag = next(action for action in runtime.actions if action[0] == "drag")
    assert drag[1] == (421, "气泡", "拖拽隐藏")
    assert runner.next_times == []
    fact = read_bubble_lifecycle_fact(facts_path)
    assert fact["hidden_week"] == "2026-W34"


def test_bubble_hide_accepts_preexisting_absence_only_after_claim(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    facts_path = tmp_path / "world-facts.json"
    record_bubble_claim_success(facts_path, now=now, claim_count=0)
    runner = _Runner(_Runtime(visible=False), facts_path)

    result = _drain(runner._ensure_bubble_hidden(
        {"asset_tree_path": Path("asset-tree.json")}, object(), {},
    ))

    assert result["result"] == "already_hidden"
    assert not any(action[0] == "drag" for action in runner.runtime.actions)


def test_bubble_hide_phase_does_not_duplicate_weekly_claim_policy(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    runner = _Runner(_Runtime(), tmp_path / "world-facts.json")

    result = _drain(runner._ensure_bubble_hidden(
        {"asset_tree_path": Path("asset-tree.json")}, object(), {},
    ))
    assert result["result"] == "success"


def test_bubble_hide_is_idempotent_from_hidden_fact(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    facts_path = tmp_path / "world-facts.json"
    record_bubble_claim_success(facts_path, now=now, claim_count=1)
    record_bubble_hidden(facts_path, now=now)
    runner = _Runner(_Runtime(visible=False), facts_path)

    result = _drain(runner._ensure_bubble_hidden(
        {"asset_tree_path": Path("asset-tree.json")}, object(), {},
    ))

    assert result["result"] == "already_hidden"
    assert runner.next_times == []
    assert not any(action[0] == "drag" for action in runner.runtime.actions)


def test_bubble_hide_rehides_when_same_week_restart_recreates_bubble(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    facts_path = tmp_path / "world-facts.json"
    record_bubble_claim_success(facts_path, now=now, claim_count=1)
    record_bubble_hidden(facts_path, now=now)
    runner = _Runner(_Runtime(visible=True), facts_path)

    result = _drain(runner._ensure_bubble_hidden(
        {"asset_tree_path": Path("asset-tree.json")}, object(), {},
    ))

    assert result["result"] == "success"
    assert [action[0] for action in runner.runtime.actions].count("drag") == 1


def test_bubble_hide_refuses_sdk_modal_without_touching_underlying_page(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    runtime = _Runtime(visible=True, overlay_scene=590)
    runner = _Runner(runtime, tmp_path / "world-facts.json")

    with pytest.raises(RuntimeError, match="SDK 事务仍打开在 #590"):
        _drain(runner._ensure_bubble_hidden(
            {"asset_tree_path": Path("asset-tree.json")}, object(), {},
        ))

    assert not any(action[0] == "drag" for action in runtime.actions)


def test_login_grace_window_hides_a_delayed_bubble(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    facts_path = tmp_path / "world-facts.json"
    record_bubble_claim_success(facts_path, now=now, claim_count=1)
    runtime = _DelayedBubbleRuntime(appears_after_checks=2)
    runner = _Runner(runtime, facts_path)

    result = _drain(runner._ensure_bubble_hidden(
        {"asset_tree_path": Path("asset-tree.json")},
        object(),
        {
            "bubble_appearance_grace_samples": 4,
            "bubble_appearance_poll_seconds": 0.2,
        },
    ))

    assert result["result"] == "success"
    assert [action[0] for action in runtime.actions].count("drag") == 1
    assert read_bubble_lifecycle_fact(facts_path)["hidden_week"] == "2026-W34"


def test_failed_drag_never_records_hidden_success(tmp_path, monkeypatch):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(bubble_hide_module, "job_now", lambda: now)
    facts_path = tmp_path / "world-facts.json"
    record_bubble_claim_success(facts_path, now=now, claim_count=1)
    runner = _Runner(_FailedDragRuntime(visible=True), facts_path)

    with pytest.raises(RuntimeError, match="仍可见"):
        _drain(runner._ensure_bubble_hidden(
            {"asset_tree_path": Path("asset-tree.json")}, object(), {},
        ))

    fact = read_bubble_lifecycle_fact(facts_path)
    assert fact.get("hidden_week") is None
