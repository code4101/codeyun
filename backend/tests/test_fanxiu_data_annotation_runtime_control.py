import json
import time
from copy import deepcopy

from backend.core.fanxiu.data_annotation import runtime_control


def test_read_doctor_watch_latest_prefers_heartbeat_latest_path_when_stale(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    watch_dir.mkdir()
    stable_path = watch_dir / "doctor_watch_latest.json"
    latest_path = watch_dir / "doctor_watch_20260703_113441.latest.json"
    heartbeat_path = watch_dir / "doctor_watch_heartbeat.json"

    stable_path.write_text('{"summary":"stable"}', encoding="utf-8")
    latest_path.write_text('{"summary":"latest"}', encoding="utf-8")
    heartbeat_path.write_text(
        json.dumps({
            "updated_at": time.time() - 3600,
            "latest_path": latest_path.as_posix(),
            "stable_latest_path": stable_path.as_posix(),
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(runtime_control, "doctor_watch_latest_path", lambda: stable_path)
    monkeypatch.setattr(runtime_control, "doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        runtime_control,
        "_doctor_watch_latest_candidates",
        lambda: (_ for _ in ()).throw(AssertionError("should not scan fallback candidates")),
    )

    payload = runtime_control.read_doctor_watch_latest()

    assert payload["exists"] is True
    assert payload["path"] == str(latest_path)
    assert payload["snapshot"]["summary"] == "latest"
    assert payload["heartbeat"]["active"] is False


def test_ensure_doctor_watch_background_uses_repo_root_script(monkeypatch, tmp_path):
    calls = []

    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    class FakeProcess:
        pid = 12345

    def fake_popen(script_path, *args, **kwargs):
        calls.append({"script_path": script_path, "args": args, "kwargs": kwargs})
        return FakeProcess()

    monkeypatch.setattr(runtime_control, "codeyun_temp_root", TempRoot())
    monkeypatch.setattr(runtime_control, "read_doctor_watch_heartbeat", lambda **_kwargs: {"active": False})
    monkeypatch.setattr(runtime_control, "read_doctor_watch_latest", lambda: {})
    monkeypatch.setattr(runtime_control, "popen_python_script_service", fake_popen)

    result = runtime_control.ensure_doctor_watch_background(interval_seconds=30, include_screenshot=False)

    assert result["started"] is True
    assert calls
    script_path = calls[0]["script_path"]
    assert script_path.name == "fanxiu_bt.py"
    assert script_path.parent.name == "scripts"
    assert script_path.is_file()
    assert "backend/core/scripts" not in script_path.as_posix()
    assert calls[0]["kwargs"]["cwd"] == str(script_path.parents[1])
    assert "--auto-run-due" in calls[0]["args"]


def test_scheduler_task_cell_records_terminal_success(monkeypatch):
    state = [{
        "id": "daily-a",
        "task_type": "daily_a",
        "schedule_kind": "daily",
        "schedule": {"time": "12:30"},
        "next_time": "2026-07-13 12:30:00",
        "last_result": "",
    }]
    facts = []

    monkeypatch.setattr(runtime_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(runtime_control, "write_scheduler_tasks", lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)))
    monkeypatch.setattr(runtime_control, "record_scheduler_task_fact", lambda task, result, **_kwargs: facts.append((result, deepcopy(task))))
    monkeypatch.setattr(runtime_control, "task_payload_with_meta", lambda task: {"scheduler_task_id": task["id"]})
    monkeypatch.setattr(
        runtime_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {"status": "success", "message": "done"},
    )
    monkeypatch.setattr(runtime_control, "next_scheduler_time", lambda task, now=None: "2026-07-14 12:30:00")
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 7},
    )

    result = runtime_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
    )

    assert result["status"] == "success"
    assert state[0]["last_result"] == "success"
    assert state[0]["last_message"] == "done"
    assert state[0]["next_time"] == "2026-07-14 12:30:00"
    assert [item[0] for item in facts] == ["running", "success"]


def test_scheduler_task_cell_preserves_business_terminal_result(monkeypatch):
    state = [{
        "id": "daily-a",
        "task_type": "daily_a",
        "schedule_kind": "daily",
        "next_time": "2026-07-13 12:30:00",
        "last_result": "",
        "cooldown_seconds": 60,
    }]
    monkeypatch.setattr(runtime_control, "read_scheduler_tasks", lambda **_kwargs: deepcopy(state))
    monkeypatch.setattr(runtime_control, "write_scheduler_tasks", lambda tasks, **_kwargs: state.__setitem__(slice(None), deepcopy(tasks)))
    monkeypatch.setattr(runtime_control, "record_scheduler_task_fact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(
        runtime_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: {
            "status": "success",
            "message": "Jupyter cell 执行完成",
            "result_text": "{'result': 'skipped', 'message': '稍后重试'}",
        },
    )

    runtime_control._run_scheduler_task_cell_and_record_terminal(
        entry=object(),
        entry_id="entry-a",
        task=deepcopy(state[0]),
    )

    assert state[0]["last_result"] == "skipped"
    assert state[0]["last_message"] == "稍后重试"
    assert state[0]["next_time"] is None
    assert state[0]["retry_after"]
    assert state[0]["attempt_id"] is None


def test_scheduler_invalidates_orphaned_attempt_for_whole_job_retry(monkeypatch):
    tasks = [{
        "id": "daily-a",
        "last_result": "running",
        "attempt_id": "old-attempt",
        "attempt_kernel_generation": 6,
        "cooldown_seconds": 60,
    }]
    written = []
    facts = []
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"execution_state": "busy", "generation": 7},
    )
    monkeypatch.setattr(runtime_control, "write_scheduler_tasks", lambda value, **_kwargs: written.append(deepcopy(value)))
    monkeypatch.setattr(runtime_control, "record_scheduler_task_fact", lambda task, result, **_kwargs: facts.append((task["id"], result)))

    changed = runtime_control.reconcile_stale_scheduler_attempts(tasks)

    assert changed is True
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["attempt_id"] is None
    assert "整单重试" in tasks[0]["last_message"]
    assert written and facts == [("daily-a", "error")]


def test_scheduler_gives_idle_cell_terminal_writer_a_grace_period(monkeypatch):
    tasks = [{
        "id": "daily-a",
        "last_result": "running",
        "attempt_id": "live-attempt",
        "attempt_kernel_generation": 7,
        "started_at": "2026-07-14 16:00:00",
    }]
    written = []
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "generation": 7},
    )
    monkeypatch.setattr(runtime_control, "write_scheduler_tasks", lambda value, **_kwargs: written.append(deepcopy(value)))

    changed = runtime_control.reconcile_stale_scheduler_attempts(tasks)

    assert changed is False
    assert tasks[0]["last_result"] == "running"
    assert tasks[0]["attempt_id"] == "live-attempt"
    assert tasks[0]["attempt_kernel_idle_since"]
    assert written


def test_prepare_scheduler_task_waits_when_kernel_busy(monkeypatch, tmp_path):
    persisted = []
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy"},
    )
    monkeypatch.setattr(runtime_control, "runtime_status", lambda **_kwargs: {"status": "idle"})
    monkeypatch.setattr(runtime_control, "persist_runtime_status", lambda status, **_kwargs: persisted.append(deepcopy(status)))

    blocked = runtime_control.prepare_runtime_for_scheduler_task(
        {"id": "daily-a"},
        [{"id": "daily-a"}],
        runtime_state_path=tmp_path / "runtime.json",
        world_facts_path=tmp_path / "facts.json",
    )

    assert blocked["phase"] == "scheduler_wait_kernel_busy"
    assert "Kernel 正在执行 Cell" in blocked["message"]
    assert persisted[-1]["phase"] == "scheduler_wait_kernel_busy"


def test_scheduler_task_normalization_preserves_terminal_message():
    from backend.core.fanxiu.data_annotation.state import normalize_data_annotation_scheduler_task

    task = normalize_data_annotation_scheduler_task({
        "id": "daily-a",
        "task_type": "daily_a",
        "last_result": "blocked",
        "last_message": "需要业务确认",
    })

    assert task["last_message"] == "需要业务确认"


def test_runtime_reload_preserves_completed_business_result(monkeypatch):
    persisted = {
        "running": False,
        "guard_enabled": True,
        "guard_running": True,
        "status": "success",
        "phase": "done",
        "message": "日常_助手执行完成",
        "logs": [],
    }
    monkeypatch.setattr(runtime_control, "read_runtime_status", lambda _path=None: deepcopy(persisted))
    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(runtime_control, "is_data_annotation_runtime_live_empty", lambda _status: True)
    monkeypatch.setattr(runtime_control, "persist_runtime_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )

    status = runtime_control.runtime_status()

    assert status["guard_running"] is False
    assert status["status"] == "success"
    assert status["phase"] == "done"
    assert status["message"] == "日常_助手执行完成"
