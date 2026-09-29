"""Deterministic timing/ownership contracts, without game simulation."""
from contextlib import nullcontext
from datetime import datetime

import pytest

from backend.core.fanxiu.behavior_tree import jupyter_kernel
from backend.core.fanxiu.data_annotation import inspection_service as watchdog


@pytest.fixture
def environment(monkeypatch):
    now = 2_000_000_000.0
    settings = {"job_group_enabled": False, "behavior_tree_enabled": True, "control_changed_at": now - 1800}
    kernel = {"alive": True, "execution_state": "idle", "last_cell_submitted_at": now - 1800}
    tasks = [{"id": "job", "next_time": datetime.fromtimestamp(now - 1800).strftime("%Y-%m-%d %H:%M:%S")}]
    resumed = []
    monkeypatch.setattr(watchdog.time, "time", lambda: now)
    monkeypatch.setattr(watchdog, "assistance_control_lock", nullcontext)
    monkeypatch.setattr(watchdog.control, "read_scheduler_settings", lambda: settings)
    monkeypatch.setattr(jupyter_kernel, "fanxiu_kernel_manager_status", lambda: kernel)
    monkeypatch.setattr(watchdog.control, "read_scheduler_tasks", lambda: tasks)
    monkeypatch.setattr(watchdog.control, "select_due_kernel_scheduler_tasks", lambda rows: rows)
    monkeypatch.setattr(watchdog.control, "resume_engineering_control", lambda: resumed.append(True) or {})
    return now, settings, kernel, tasks, resumed


def test_both_thirty_minute_boundaries_release_control(environment):
    *_, resumed = environment
    assert watchdog.recover_abandoned_ai_control()["recovered"]
    assert resumed == [True]


@pytest.mark.parametrize("condition", ["recent_cell", "recent_takeover", "recent_due", "no_due", "busy", "dead", "unknown", "engineering", "disabled", "no_activity"])
def test_incomplete_conditions_never_release_control(environment, condition):
    now, settings, kernel, tasks, resumed = environment
    if condition == "recent_cell":
        kernel["last_cell_submitted_at"] = now - 1799
    elif condition == "recent_takeover":
        settings["control_changed_at"] = now - 1799
    elif condition == "recent_due":
        tasks[0]["next_time"] = datetime.fromtimestamp(now - 1799).strftime("%Y-%m-%d %H:%M:%S")
    elif condition == "no_due":
        tasks.clear()
    elif condition in {"busy", "unknown"}:
        kernel["execution_state"] = condition
    elif condition == "dead":
        kernel["alive"] = False
    elif condition == "engineering":
        settings["job_group_enabled"] = True
    elif condition == "disabled":
        settings["behavior_tree_enabled"] = False
    else:
        kernel.pop("last_cell_submitted_at")
    assert not watchdog.recover_abandoned_ai_control()["recovered"]
    assert resumed == []


def test_other_settings_writes_do_not_extend_ai_ownership(monkeypatch, tmp_path):
    control = watchdog.control
    path = tmp_path / "settings.json"
    monkeypatch.setattr(control.time, "time", lambda: 100.0)
    ai = control.set_scheduler_job_group_enabled(False, scheduler_settings_path=path)
    assert ai["control_changed_at"] == 100.0
    monkeypatch.setattr(control.time, "time", lambda: 200.0)
    saved = control.write_scheduler_settings({"job_group_enabled": False}, scheduler_settings_path=path)
    assert saved["updated_at"] == 200.0
    assert saved["control_changed_at"] == 100.0
    engineering = control.set_scheduler_job_group_enabled(True, scheduler_settings_path=path)
    assert engineering["control_changed_at"] == 200.0


def test_supervisor_retries_dispatcher_startup_after_failed_handoff(monkeypatch):
    checks = []
    starts = []

    def recover():
        checks.append(True)
        if len(checks) == 1:
            raise RuntimeError("spawn failed after ownership changed")

    class Stop:
        ticks = 0

        def wait(self, seconds):
            assert seconds == 300
            self.ticks += 1
            return self.ticks > 2

    monkeypatch.setattr(watchdog, "recover_abandoned_ai_control", recover)
    monkeypatch.setattr(watchdog.control, "read_scheduler_settings", lambda: {"job_group_enabled": True, "behavior_tree_enabled": True})
    monkeypatch.setattr(watchdog.control, "ensure_doctor_watch_background", lambda: starts.append(True) or {"ok": True})
    watchdog.supervise_scheduler_ownership(Stop())
    assert starts == [True]
    assert len(checks) == 2


def test_runtime_patrol_continues_after_probe_failure_without_dispatch(monkeypatch):
    checks = []

    def inspect():
        checks.append(True)
        if len(checks) == 1:
            raise RuntimeError("transient Runtime read failure")

    class Stop:
        ticks = 0

        def wait(self, seconds):
            assert seconds == 60
            self.ticks += 1
            return self.ticks > 2

    monkeypatch.setattr(watchdog, "inspect_runtime_once", inspect)
    watchdog._inspect_runtime_loop(Stop())
    assert len(checks) == 2
