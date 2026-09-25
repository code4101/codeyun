from __future__ import annotations

from datetime import datetime


from backend.core.fanxiu.data_annotation.tasks import bubble_lifecycle as lifecycle
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    consolidate_arena_scheduler_instances,
)


def test_login_always_wakes_the_single_bubble_job():
    calls = []
    task_id = lifecycle.schedule_bubble_reconcile_after_login(
        now=datetime(2026, 8, 18, 9, 4),
        set_next_time=lambda *args: calls.append(args),
    )
    assert task_id == lifecycle.BUBBLE_WEEKLY_TASK_ID
    assert calls == [(lifecycle.BUBBLE_WEEKLY_TASK_ID, "2026-08-18 09:04:00")]


def test_weekly_schedule_is_next_monday_midnight():
    assert lifecycle.next_bubble_weekly_time(datetime(2026, 8, 17, 0, 10)) == "2026-08-24 00:00:00"
    assert lifecycle.next_bubble_weekly_time(datetime(2026, 8, 16, 23, 59)) == "2026-08-17 00:00:00"


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


def test_scheduler_migration_removes_all_three_legacy_bubble_jobs():
    migrated, changed = consolidate_arena_scheduler_instances([
        {"id": "bubble-weekly-restart", "task_type": "bubble_weekly_restart"},
        {"id": "custom-claim", "task_type": "bubble_claim_pills"},
        {"id": "bubble-hide", "task_type": "bubble_hide"},
        {"id": "keep", "task_type": "weekly_hanli"},
    ])
    assert changed is True
    assert migrated == [{"id": "keep", "task_type": "weekly_hanli"}]
