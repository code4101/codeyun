"""Scheduler ownership/time contracts; no game Runtime or GUI simulation."""

from copy import deepcopy
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control


@pytest.mark.parametrize("engineering", [False, True])
@pytest.mark.parametrize("terminal_path", ["caller", "detached", "orphan"])
def test_failed_attempt_time_follows_current_owner(monkeypatch, tmp_path, engineering, terminal_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 6, 16, 50, 3, tzinfo=tz)

    original = "2026-09-06 16:43:26"
    state = [{
        "id": "daily-lundao-seat", "task_type": "daily_lundao",
        "next_time": original, "error_retry_delay_seconds": 60,
    }]
    writes = []
    settings_path = tmp_path / "settings.json"
    control.write_scheduler_settings(
        {"job_group_enabled": engineering}, scheduler_settings_path=settings_path,
    )

    def write(tasks, **kwargs):
        state[:] = deepcopy(tasks)
        writes.append(kwargs)
        return True

    monkeypatch.setattr(control, "datetime", Clock)
    monkeypatch.setattr(control, "read_scheduler_tasks", lambda **kw: deepcopy(state))
    monkeypatch.setattr(control, "write_scheduler_tasks", write)
    monkeypatch.setattr(control, "record_scheduler_task_fact", lambda *a, **kw: None)
    monkeypatch.setattr(control, "record_scheduler_incident", lambda **kw: None)
    monkeypatch.setattr(control, "read_kernel_scheduler_status", lambda *a, **kw: {})
    monkeypatch.setattr(control, "_higher_level_due_task_for_attempt", lambda *a, **kw: None)
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": terminal_path != "orphan", "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(control, "behavior_tree_executor_status", lambda: {
        "scheduler_task_id": state[0]["id"],
        "scheduler_attempt_id": state[0].get("attempt_id"),
        "scheduler_terminal_result": "error" if terminal_path == "detached" else "",
        "scheduler_terminal_message": "exit failed",
    })
    if terminal_path == "caller":
        def submit(**kwargs):
            # A business write followed by an exception cannot defer AI diagnosis.
            state[0]["next_time"] = "2026-09-07 05:00:00"
            return {"status": "error", "message": "exit failed"}

        monkeypatch.setattr(control, "submit_task_cell", submit)
        result = control._run_scheduler_task_cell_and_record_terminal(
            entry=object(), entry_id="test", task=deepcopy(state[0]),
            scheduler_state_path=tmp_path / "scheduler_tasks.json",
            scheduler_settings_path=settings_path, scheduled_attempt=True,
        )
        assert result["status"] == "error"
    else:
        state[0].update(
            last_result="running", attempt_id="owned-attempt",
            attempt_original_trigger=original, attempt_kernel_generation=7,
            next_time="2026-09-07 05:00:00",
        )
        assert control.reconcile_stale_scheduler_attempts(
            state, scheduler_settings_path=settings_path,
        )
    assert state[0]["last_result"] == "error"
    assert state[0]["next_time"] == ("2026-09-06 16:51:03" if engineering else original)
    assert state[0]["attempt_id"] is None
    assert writes[-1]["expected_execution_attempt_ids"]["daily-lundao-seat"]


def test_ai_failure_preserves_manual_sleep_and_legacy_trigger():
    for task in (
        {"attempt_original_trigger": None, "next_time": "2026-09-07 05:00:00"},
        {"next_time": "2026-09-06 16:43:26"},
    ):
        original = task.get("attempt_original_trigger", task.get("next_time"))
        control.schedule_failed_task_retry(task, datetime(2026, 9, 6, 17), job_group_enabled=False)
        assert task["next_time"] == original
