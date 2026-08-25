from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation.tasks import bubble_lifecycle as lifecycle
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    consolidate_arena_scheduler_instances,
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


def test_login_always_wakes_the_single_bubble_job():
    calls = []
    task_id = lifecycle.schedule_bubble_reconcile_after_login(
        now=datetime(2026, 8, 18, 9, 4),
        set_next_time=lambda *args: calls.append(args),
    )
    assert task_id == lifecycle.BUBBLE_WEEKLY_TASK_ID
    assert calls == [(lifecycle.BUBBLE_WEEKLY_TASK_ID, "2026-08-18 09:04:00")]


def test_weekly_schedule_is_next_monday_0010():
    assert lifecycle.next_bubble_weekly_time(datetime(2026, 8, 17, 0, 10)) == "2026-08-24 00:10:00"
    assert lifecycle.next_bubble_weekly_time(datetime(2026, 8, 16, 23, 59)) == "2026-08-17 00:10:00"


def test_claim_and_hidden_facts_are_separate_observations(tmp_path):
    path = tmp_path / "world-facts.json"
    now = datetime(2026, 8, 19, 1, 5)
    lifecycle.record_bubble_claim_success(path, now=now, claim_count=3)
    lifecycle.record_bubble_hidden(path, now=now)
    fact = lifecycle.read_bubble_lifecycle_fact(path)
    assert fact["claimed_week"] == "2026-W34"
    assert fact["claim_count"] == 3
    assert fact["hidden_week"] == "2026-W34"
    assert "restart_week" not in fact
    assert "hide_pending_week" not in fact


class _Runtime:
    def __init__(self, visible=False, overlay_scene=None):
        self.visible = visible
        self.overlay_scene = overlay_scene
        self.waits = 0
        self.go_scene_calls = []

    def current_scene(self, _views, **_kwargs):
        raise AssertionError("top-level bubble flow must not classify the game scene")

    def cur_frame(self, *, update):
        assert update is True
        return "frame"

    def go_scene(self, scene_id):
        self.go_scene_calls.append(scene_id)
        return _done(scene_id)

    def match_view(self, scene_id, *, frame_data_url):
        assert frame_data_url == "frame"
        return scene_id == self.overlay_scene, 100.0, frame_data_url

    def shape_matches(self, _scene, shape, **kwargs):
        assert shape == "气泡"
        assert kwargs.get("frame_data_url") == "frame"
        if not self.visible:
            return None
        return {"unique_match": True, "resolved_box": {"x": 17, "y": 650, "w": 57, "h": 67}}

    def wait_action_settle(self, _seconds):
        self.waits += 1
        return _done()


class _Runner(lifecycle.BubbleLifecycleTaskMixin):
    def __init__(self, facts_path: Path, runtime=None):
        self.facts_path = facts_path
        self.runtime = runtime or _Runtime(visible=True)
        self.next_times = []
        self.claim_calls = 0
        self.hide_calls = 0
        self.logs = []

    def _bubble_lifecycle_world_facts_path(self):
        return self.facts_path

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _execute_bubble_claim_pills_task(self, _ctx, _stop, _payload):
        self.claim_calls += 1
        lifecycle.record_bubble_claim_success(
            self.facts_path, now=datetime(2026, 8, 19, 1, 5), claim_count=3
        )
        return _done({"result": "success"})

    def _ensure_bubble_hidden(self, _ctx, _stop, _payload):
        self.hide_calls += 1
        return _done({"result": "already_hidden"})

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _log(self, level, message):
        self.logs.append((level, message))


def test_unclaimed_run_restores_claims_hides_then_schedules(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    shake_calls = []
    runtime = _Runtime(visible=False)

    def shake(**kwargs):
        shake_calls.append(kwargs)
        runtime.visible = True
        return {"status": "sent"}

    monkeypatch.setattr(lifecycle, "shake_mumu_device", shake)
    runner = _Runner(tmp_path / "facts.json", runtime)
    result = _drain(runner._execute_bubble_weekly_task(
        {"asset_tree_path": Path("tree.json")}, object(), {}
    ))
    assert result["claimed_this_run"] is True
    assert runner.claim_calls == runner.hide_calls == 1
    assert len(shake_calls) == 1
    assert runner.next_times == [("bubble-weekly-pills", "2026-08-24 00:10:00")]


def test_claimed_run_never_shakes_or_claims_and_still_hides(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    path = tmp_path / "facts.json"
    lifecycle.record_bubble_claim_success(path, now=now, claim_count=3)
    monkeypatch.setattr(lifecycle, "shake_mumu_device", lambda **_kwargs: pytest.fail("must not shake"))
    runner = _Runner(path)
    result = _drain(runner._execute_bubble_weekly_task(
        {"asset_tree_path": Path("tree.json")}, object(), {}
    ))
    assert result["claimed_this_run"] is False
    assert runner.claim_calls == 0
    assert runner.hide_calls == 1


def test_login_reconcile_hides_inline_when_current_week_is_claimed(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    path = tmp_path / "facts.json"
    lifecycle.record_bubble_claim_success(path, now=now, claim_count=3)
    runner = _Runner(path)

    result = _drain(runner._reconcile_bubble_after_login(
        {"asset_tree_path": Path("tree.json")}, object(), {}
    ))

    assert result["mode"] == "hidden_inline"
    assert runner.hide_calls == 1
    assert runner.next_times == []


def test_login_reconcile_passes_a_bounded_delayed_appearance_window(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    path = tmp_path / "facts.json"
    lifecycle.record_bubble_claim_success(path, now=now, claim_count=3)
    runner = _Runner(path)
    payloads = []

    def hide(_ctx, _stop, payload):
        payloads.append(dict(payload))
        return _done({"result": "already_hidden"})

    runner._ensure_bubble_hidden = hide
    result = _drain(runner._reconcile_bubble_after_login(
        {"asset_tree_path": Path("tree.json")}, object(), {},
    ))

    assert result["mode"] == "hidden_inline"
    assert payloads == [{
        "bubble_appearance_grace_samples": 12,
        "bubble_appearance_poll_seconds": 1.0,
    }]


def test_login_reconcile_schedules_weekly_transaction_when_unclaimed(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    runner = _Runner(tmp_path / "facts.json")

    result = _drain(runner._reconcile_bubble_after_login(
        {"asset_tree_path": Path("tree.json")}, object(), {}
    ))

    assert result == {"mode": "scheduled_weekly", "task_id": "bubble-weekly-pills"}
    assert runner.hide_calls == 0
    assert runner.next_times == [("bubble-weekly-pills", "2026-08-19 01:05:00")]


def test_unclaimed_run_establishes_world_before_using_top_level_bubble(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    monkeypatch.setattr(
        lifecycle,
        "shake_mumu_device",
        lambda **_kwargs: pytest.fail("visible top-level bubble must not shake"),
    )
    runner = _Runner(tmp_path / "facts.json", _Runtime(visible=True))

    result = _drain(runner._execute_bubble_weekly_task(
        {"asset_tree_path": Path("tree.json")}, object(), {}
    ))

    assert result["claimed_this_run"] is True
    assert runner.claim_calls == 1
    assert runner.hide_calls == 1
    assert runner.runtime.go_scene_calls == [34]


def test_unclaimed_run_reuses_open_sdk_menu_without_shake(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    monkeypatch.setattr(
        lifecycle,
        "shake_mumu_device",
        lambda **_kwargs: pytest.fail("open SDK menu must not shake"),
    )
    runner = _Runner(tmp_path / "facts.json", _Runtime(overlay_scene=590))

    result = _drain(runner._execute_bubble_weekly_task(
        {"asset_tree_path": Path("tree.json")}, object(), {}
    ))

    assert result["claimed_this_run"] is True
    assert runner.claim_calls == 1
    assert runner.hide_calls == 1
    assert runner.runtime.go_scene_calls == []


def test_dynamic_591_items_are_sdk_overlay_evidence_when_full_view_misses():
    marker = object()

    class _Dynamic591Runtime(_Runtime):
        def find_floating_items_by_anchor_text(self, *_args, **kwargs):
            assert kwargs.get("frame_data_url") == "frame"
            return [marker] if _args[3] == "领取" else []

        def floating_item_field_is_fully_inside(self, item, field, container):
            assert item is marker
            assert (field, container) == ("领取", "窗口")
            return True

    assert lifecycle.bubble_sdk_overlay_scene(
        _Dynamic591Runtime(), frame="frame"
    ) == 591


def test_hide_failure_does_not_advance_next_time(monkeypatch, tmp_path):
    now = datetime(2026, 8, 19, 1, 5)
    monkeypatch.setattr(lifecycle, "job_now", lambda: now)
    path = tmp_path / "facts.json"
    lifecycle.record_bubble_claim_success(path, now=now, claim_count=3)
    runner = _Runner(path)

    def fail_hide(*_args):
        raise RuntimeError("still visible")
        yield

    runner._ensure_bubble_hidden = fail_hide
    with pytest.raises(RuntimeError, match="still visible"):
        _drain(runner._execute_bubble_weekly_task(
            {"asset_tree_path": Path("tree.json")}, object(), {}
        ))
    assert runner.next_times == []


def test_scheduler_migration_removes_all_three_legacy_bubble_jobs():
    migrated, changed = consolidate_arena_scheduler_instances([
        {"id": "bubble-weekly-restart", "task_type": "bubble_weekly_restart"},
        {"id": "custom-claim", "task_type": "bubble_claim_pills"},
        {"id": "bubble-hide", "task_type": "bubble_hide"},
        {"id": "keep", "task_type": "weekly_hanli"},
    ])
    assert changed is True
    assert migrated == [{"id": "keep", "task_type": "weekly_hanli"}]
