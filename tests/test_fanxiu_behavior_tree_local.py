from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from backend.api import fanxiu as fanxiu_api
from backend.core.fanxiu.runtime import behavior_tree as bt
from backend.core.fanxiu.data_annotation import runtime_control as runtime_control
from backend.core.fanxiu.data_annotation import runtime_runner as runtime_runner_core
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_manual_job_definition,
    list_fanxiu_data_annotation_manual_job_definitions,
    normalize_data_annotation_debug_eval_payload,
    normalize_data_annotation_go_scene_payload,
    parse_data_annotation_scene_id,
)
from backend.core.fanxiu.data_annotation.debug_eval import register_fanxiu_data_annotation_debug_eval_job
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation import runner as runner_core
from backend.models import UserDevice


def test_core_behavior_tree_facade_does_not_import_codeyun_db_at_module_top():
    source = Path("backend/core/fanxiu/runtime/behavior_tree.py").read_text(encoding="utf-8")
    header = source.split("DEFAULT_FANXIU_ENTRY_ID", 1)[0]

    assert "from backend.db import" not in header
    assert "from backend.models import" not in header
    assert "from sqlmodel import" not in header
    assert "def resolve_fanxiu_entry" in source


def test_core_runtime_control_does_not_import_codeyun_model_at_module_top():
    source = Path("backend/core/fanxiu/data_annotation/runtime_control.py").read_text(encoding="utf-8")
    header = source.split("def read_world_facts", 1)[0]

    assert "from backend.models import" not in header
    assert "from backend.db import" not in header
    assert "from sqlmodel import" not in header


def test_core_asset_tree_path_sanitizes_entry_id(monkeypatch, tmp_path):
    class Settings:
        data_dir = tmp_path

    monkeypatch.setattr(bt, "get_settings", lambda: Settings())

    path = bt.data_annotation_asset_tree_path(" bad/id ")

    assert path == tmp_path / "fanxiu" / "data-annotation" / "asset-trees" / "bad_id.json"


def test_core_runtime_paths_live_under_data_annotation_runtime(monkeypatch, tmp_path):
    class Settings:
        data_dir = tmp_path

    monkeypatch.setattr(bt, "get_settings", lambda: Settings())

    runtime_dir = tmp_path / "fanxiu" / "data-annotation" / "runtime"

    assert bt.fanxiu_data_annotation_runtime_state_path() == runtime_dir / "runtime_state.json"
    assert bt.fanxiu_data_annotation_world_facts_path() == runtime_dir / "world_facts.json"
    assert bt.fanxiu_data_annotation_scheduler_state_path() == runtime_dir / "scheduler_tasks.json"
    assert bt.fanxiu_data_annotation_manual_job_state_path() == runtime_dir / "manual_jobs.json"
    assert bt.fanxiu_data_annotation_mail_scan_state_path() == runtime_dir / "mail_scan_state.json"
    assert bt.fanxiu_job_group_isolation_path() == runtime_dir / "job_group_isolation.json"
    assert bt.fanxiu_behavior_tree_service_owner_path() == runtime_dir / "behavior_tree_service_owner.json"
    assert bt.fanxiu_behavior_tree_control_path() == runtime_dir / "behavior_tree_control.json"


def test_ensure_behavior_tree_service_launches_external_process_outside_service_host(monkeypatch):
    launched: list[tuple[str, float]] = []

    monkeypatch.setattr(bt, "_current_process_is_fanxiu_service_host", lambda: False)
    monkeypatch.setattr(bt, "read_fanxiu_behavior_tree_service_owner", lambda: {"active": False, "stale": True})
    monkeypatch.setattr(
        bt,
        "_start_external_fanxiu_behavior_tree_service",
        lambda entry_id, *, tick_seconds=1.0, wait_seconds=5.0: launched.append((entry_id, tick_seconds)) or {"started": True},
    )
    monkeypatch.setattr(
        bt,
        "get_fanxiu_runtime_runner",
        lambda: (_ for _ in ()).throw(AssertionError("non-service callers must not host resident threads")),
    )
    monkeypatch.setattr(
        bt,
        "fanxiu_data_annotation_runtime_status",
        lambda: {"service_running": True, "phase": "service_owned_by_other"},
    )

    status = bt.ensure_fanxiu_behavior_tree_service(object(), "entry-1", tick_seconds=0.5)

    assert launched == [("entry-1", 0.5)]
    assert status["service_running"] is True
    assert status["phase"] == "service_owned_by_other"


def test_ensure_behavior_tree_service_hosts_threads_inside_service_process(monkeypatch, tmp_path):
    calls: list[tuple[str, float]] = []

    class FakeRunner:
        def ensure_service(self, *, entry, entry_id, asset_tree_path, tick_seconds):
            calls.append((entry_id, tick_seconds))
            return {"service_running": True, "entry_id": entry_id, "logs": []}

    monkeypatch.setattr(bt, "_current_process_is_fanxiu_service_host", lambda: True)
    monkeypatch.setattr(bt, "get_fanxiu_runtime_runner", lambda: FakeRunner())
    monkeypatch.setattr(bt, "persist_fanxiu_runtime_status", lambda status: None)

    status = bt.ensure_fanxiu_behavior_tree_service(object(), "entry-1", asset_tree_path=tmp_path / "entry.json", tick_seconds=0.5)

    assert calls == [("entry-1", 0.5)]
    assert status["service_running"] is True


def test_external_behavior_tree_service_requests_shutdown_for_stale_foreign_owner(monkeypatch, tmp_path):
    calls: list[dict] = []

    class FakeProcess:
        pid = 4321

    owners = [
        {"exists": True, "active": False, "stale": True, "pid": 1234},
        {"exists": True, "active": False, "stale": True, "pid": 1234},
        {"exists": True, "active": True, "stale": False, "pid": 4321},
    ]

    def fake_owner():
        return owners.pop(0) if owners else {"exists": True, "active": True, "stale": False, "pid": 4321}

    monkeypatch.setattr(bt, "read_fanxiu_behavior_tree_service_owner", fake_owner)
    monkeypatch.setattr(bt, "_fanxiu_service_processes", lambda: [])
    monkeypatch.setattr(bt, "request_fanxiu_behavior_tree_service_shutdown", lambda **kwargs: calls.append({"shutdown": kwargs}))
    monkeypatch.setattr(bt, "popen_python_script_service", lambda *args, **kwargs: calls.append({"popen": args, "kwargs": kwargs}) or FakeProcess())
    monkeypatch.setattr(bt.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bt, "ROOT_DIR", tmp_path)
    script = tmp_path / "scripts" / "fanxiu_bt.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    result = bt._start_external_fanxiu_behavior_tree_service("entry-1", tick_seconds=0.5, wait_seconds=1.0)

    assert calls[0]["shutdown"]["entry_id"] == "entry-1"
    assert calls[0]["shutdown"]["reason"] == "external_service_takeover"
    assert calls[1]["popen"][1:5] == ("--entry-id", "entry-1", "service", "--tick-seconds")
    assert result["started"] is True
    assert result["pid"] == 4321


def test_external_behavior_tree_service_does_not_duplicate_existing_service_process(monkeypatch, tmp_path):
    calls: list[dict] = []

    monkeypatch.setattr(bt, "ROOT_DIR", tmp_path)
    script = tmp_path / "scripts" / "fanxiu_bt.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(bt, "read_fanxiu_behavior_tree_service_owner", lambda: {"exists": True, "active": False, "stale": True, "pid": 1234})
    monkeypatch.setattr(bt, "_fanxiu_service_processes", lambda: [{"pid": 4321, "cmdline": ["fanxiu_bt.py", "service"]}])
    monkeypatch.setattr(bt, "popen_python_script_service", lambda *args, **kwargs: calls.append({"popen": args}) or object())

    result = bt._start_external_fanxiu_behavior_tree_service("entry-1")

    assert result["started"] is False
    assert result["reason"] == "service_process_already_running"
    assert calls == []


def test_local_enqueue_ensures_behavior_tree_service_before_queue(monkeypatch, tmp_path):
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda entry_id: type("Entry", (), {"entry_id": entry_id})())
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(bt, "fanxiu_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(bt, "fanxiu_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(bt, "fanxiu_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(bt, "acquire_fanxiu_job_group_isolation", lambda **_kwargs: "token")
    monkeypatch.setattr(
        bt,
        "ensure_fanxiu_behavior_tree_service",
        lambda _entry, entry_id, **_kwargs: events.append(("ensure", entry_id)) or {"service_running": True},
    )
    monkeypatch.setattr(
        runtime_control,
        "submit_manual_job",
        lambda **kwargs: events.append(("submit", kwargs["entry_id"])) or {"queued_job": {"task_type": kwargs["task_type"]}},
    )

    status = bt.enqueue_fanxiu_local_manual_job(
        bt.FanxiuLocalEnqueueRequest(task_type="manual_tick", entry_id="entry-1")
    )

    assert events == [("ensure", "entry-1"), ("submit", "entry-1")]
    assert status["queued_job"]["task_type"] == "manual_tick"


def test_runtime_control_reads_doctor_watch_latest_snapshot(monkeypatch, tmp_path):
    latest_path = tmp_path / "fanxiu-watch" / "latest.json"
    heartbeat_path = tmp_path / "fanxiu-watch" / "heartbeat.json"
    monkeypatch.setattr(runtime_control, "doctor_watch_heartbeat_path", lambda: heartbeat_path)

    missing = runtime_control.read_doctor_watch_latest(latest_path)

    assert missing["ok"] is False
    assert missing["exists"] is False
    assert missing["snapshot"] == {}
    assert missing["heartbeat"]["active"] is False

    latest_path.parent.mkdir(parents=True)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(runtime_control.doctor_watch_latest_path()),
                "severity": "blocked",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    latest_path.write_text(
        json.dumps(
            {
                "severity": "blocked",
                "summary": "检测到游戏公告遮挡",
                "due_task_count": 13,
                "stale_due_count": 13,
                "stale_due_success_count": 0,
                "blocked_due_count": 13,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = runtime_control.read_doctor_watch_latest(latest_path)

    assert loaded["ok"] is True
    assert loaded["exists"] is True
    assert loaded["path"] == str(latest_path)
    assert loaded["message"] == "检测到游戏公告遮挡"
    assert loaded["snapshot"]["severity"] == "blocked"
    assert loaded["heartbeat"]["pid"] == 123


def test_runtime_control_reads_stable_or_fallback_doctor_watch_latest(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))

    fallback_path = watch_dir / "doctor_watch_20260615_050000.latest.json"
    fallback_path.parent.mkdir(parents=True)
    fallback_path.write_text(json.dumps({"severity": "blocked", "summary": "旧快照"}, ensure_ascii=False), encoding="utf-8")

    loaded_fallback = runtime_control.read_doctor_watch_latest()

    assert loaded_fallback["ok"] is True
    assert loaded_fallback["path"] == str(fallback_path)
    assert loaded_fallback["message"] == "旧快照"

    stable_path = watch_dir / "doctor_watch_latest.json"
    stable_path.write_text(json.dumps({"severity": "ok", "summary": "稳定快照"}, ensure_ascii=False), encoding="utf-8")

    loaded_stable = runtime_control.read_doctor_watch_latest()

    assert loaded_stable["ok"] is True
    assert loaded_stable["path"] == str(stable_path)
    assert loaded_stable["message"] == "稳定快照"


def test_runtime_control_reads_doctor_watch_heartbeat(monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 150.0)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "updated_at_text": "2026-06-15 05:30:00",
                "stable_latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    active = runtime_control.read_doctor_watch_heartbeat(stale_after_seconds=180.0)

    assert active["ok"] is True
    assert active["active"] is True
    assert active["runtime_consistent"] is True
    assert active["age_seconds"] == 50.0

    heartbeat_path.write_text(
        json.dumps({"pid": 123, "updated_at": 100.0, "stable_latest_path": str(tmp_path / "old.json")}, ensure_ascii=False),
        encoding="utf-8",
    )

    inconsistent = runtime_control.read_doctor_watch_heartbeat(stale_after_seconds=180.0)

    assert inconsistent["active"] is False
    assert inconsistent["runtime_consistent"] is False


def test_runtime_control_prefers_active_heartbeat_latest_sidecar(monkeypatch, tmp_path):
    watch_dir = tmp_path / "fanxiu-watch"
    stable_path = watch_dir / "doctor_watch_latest.json"
    active_sidecar_path = watch_dir / "doctor_watch_background.latest.json"
    heartbeat_path = watch_dir / "doctor_watch_heartbeat.json"
    watch_dir.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 150.0)
    stable_path.write_text(json.dumps({"severity": "ok", "summary": "旧稳定快照"}, ensure_ascii=False), encoding="utf-8")
    active_sidecar_path.write_text(json.dumps({"severity": "blocked", "summary": "活跃巡检快照"}, ensure_ascii=False), encoding="utf-8")
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(active_sidecar_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = runtime_control.read_doctor_watch_latest()

    assert loaded["path"] == str(active_sidecar_path)
    assert loaded["snapshot"]["severity"] == "blocked"
    assert loaded["heartbeat"]["active"] is True


def test_doctor_watch_latest_payload_for_frontend_omits_large_auto_run_due(monkeypatch):
    monkeypatch.setattr(
        fanxiu_api._runtime_control,
        "read_doctor_watch_latest",
        lambda: {
            "ok": True,
            "exists": True,
            "path": "doctor-watch.latest.json",
            "message": "巡检摘要",
            "heartbeat": {"active": True},
            "snapshot": {
                "summary": "巡检摘要",
                "severity": "attention",
                "action_required": ["查看人工复核"],
                "auto_run_due": {
                    "runs": [{"message": "x" * 4096}],
                    "blocking_overlays": [{"message": "y" * 4096}],
                },
            },
        },
    )

    payload = fanxiu_api._doctor_watch_latest_payload_for_frontend()

    assert payload["snapshot"]["summary"] == "巡检摘要"
    assert payload["snapshot"]["severity"] == "attention"
    assert payload["snapshot"]["action_required"] == ["查看人工复核"]
    assert payload["snapshot"]["auto_run_due"] is None


def test_runtime_control_ensure_doctor_watch_skips_recent_capable_heartbeat(monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 120.0)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
                "auto_run_due_enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stable_path.write_text(json.dumps({"severity": "blocked", "summary": "活跃"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(runtime_control.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))

    result = runtime_control.ensure_doctor_watch_background(stale_after_seconds=180.0)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"]["active"] is True
    assert result["heartbeat"]["auto_run_due_enabled"] is True
    assert result["latest"]["snapshot"]["severity"] == "blocked"


def test_runtime_control_ensure_doctor_watch_restarts_recent_heartbeat_without_auto_run_due(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 789

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 120.0)
    monkeypatch.setattr(runtime_control.subprocess, "Popen", fake_popen)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stable_path.write_text(json.dumps({"severity": "blocked", "summary": "旧 watcher"}, ensure_ascii=False), encoding="utf-8")

    result = runtime_control.ensure_doctor_watch_background(stale_after_seconds=180.0)

    assert result["started"] is True
    assert result["pid"] == 789
    assert result["previous_heartbeat"]["active"] is True
    assert result["previous_heartbeat"]["auto_run_due_enabled"] is False
    assert "--auto-run-due" in popen_calls[0]["command"]


def test_runtime_control_ensure_doctor_watch_allows_observe_only_recent_heartbeat(monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 120.0)
    monkeypatch.setattr(runtime_control.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stable_path.write_text(json.dumps({"severity": "blocked", "summary": "observe only"}, ensure_ascii=False), encoding="utf-8")

    result = runtime_control.ensure_doctor_watch_background(stale_after_seconds=180.0, auto_run_due=False)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"]["auto_run_due_enabled"] is False


def test_runtime_control_ensure_doctor_watch_starts_when_heartbeat_stale(monkeypatch, tmp_path):
    class FakeProcess:
        pid = 456

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    stable_path = tmp_path / "fanxiu-watch" / "doctor_watch_latest.json"
    heartbeat_path.parent.mkdir(parents=True)
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *parts: tmp_path.joinpath(*parts))
    monkeypatch.setattr(runtime_control.time, "time", lambda: 400.0)
    monkeypatch.setattr(runtime_control.subprocess, "Popen", fake_popen)
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_path),
                "latest_path": str(stable_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = runtime_control.ensure_doctor_watch_background(
        interval_seconds=30,
        duration_seconds=60,
        stale_after_seconds=180.0,
        include_screenshot=False,
    )

    assert result["started"] is True
    assert result["pid"] == 456
    assert result["reason"] == "heartbeat_missing_or_stale"
    assert len(popen_calls) == 1
    command = popen_calls[0]["command"]
    assert "watch-doctor" in command
    assert "--interval-seconds" in command
    assert "30.0" in command
    assert "--duration-seconds" in command
    assert "60.0" in command
    assert "--screenshot" not in command
    assert "--auto-run-due" in command
    assert result["output_path"].endswith(".ndjson")


def test_core_stop_request_writes_control_file(monkeypatch, tmp_path):
    control_path = tmp_path / "behavior_tree_control.json"
    monkeypatch.setattr(bt.uuid, "uuid4", lambda: type("FakeUuid", (), {"hex": "request-id"})())
    monkeypatch.setattr(bt.time, "time", lambda: 123.0)

    request = bt.request_fanxiu_behavior_tree_stop(entry_id="entry", reason="test", path=control_path)

    assert request["path"] == str(control_path)
    assert json.loads(control_path.read_text(encoding="utf-8")) == {
        "id": "request-id",
        "command": "stop_current_task",
        "entry_id": "entry",
        "reason": "test",
        "created_at": 123.0,
    }


def test_core_wake_request_writes_control_file(monkeypatch, tmp_path):
    control_path = tmp_path / "behavior_tree_control.json"
    monkeypatch.setattr(bt.uuid, "uuid4", lambda: type("FakeUuid", (), {"hex": "wake-id"})())
    monkeypatch.setattr(bt.time, "time", lambda: 456.0)

    request = bt.request_fanxiu_behavior_tree_wake(entry_id="entry", reason="test", path=control_path)

    assert request["path"] == str(control_path)
    assert json.loads(control_path.read_text(encoding="utf-8")) == {
        "id": "wake-id",
        "command": "wake_service",
        "entry_id": "entry",
        "reason": "test",
        "created_at": 456.0,
    }


def test_core_shutdown_service_request_writes_control_file(monkeypatch, tmp_path):
    control_path = tmp_path / "behavior_tree_control.json"
    monkeypatch.setattr(bt.uuid, "uuid4", lambda: type("FakeUuid", (), {"hex": "shutdown-id"})())
    monkeypatch.setattr(bt.time, "time", lambda: 789.0)

    request = bt.request_fanxiu_behavior_tree_service_shutdown(entry_id="entry", reason="takeover", path=control_path)

    assert request["path"] == str(control_path)
    assert json.loads(control_path.read_text(encoding="utf-8")) == {
        "id": "shutdown-id",
        "command": "shutdown_service",
        "entry_id": "entry",
        "reason": "takeover",
        "created_at": 789.0,
    }


def test_core_service_owner_diagnostics(monkeypatch, tmp_path):
    owner_path = tmp_path / "behavior_tree_service_owner.json"

    assert bt.read_fanxiu_behavior_tree_service_owner(owner_path)["active"] is False

    monkeypatch.setattr(bt.time, "time", lambda: 100.0)
    owner_path.write_text(
        json.dumps({"pid": 123, "entry_id": "entry", "step": "idle_guard", "updated_at": 95.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bt, "_fanxiu_process_exists", lambda pid: True)
    monkeypatch.setattr(bt, "_fanxiu_process_matches_service_owner", lambda pid: True)

    active = bt.read_fanxiu_behavior_tree_service_owner(owner_path, stale_after_seconds=30.0)
    assert active["active"] is True
    assert active["stale"] is False
    assert active["age_seconds"] == 5.0

    stale = bt.read_fanxiu_behavior_tree_service_owner(owner_path, stale_after_seconds=3.0)
    assert stale["active"] is False
    assert stale["stale"] is True


def test_core_service_owner_marks_missing_pid_stale(monkeypatch, tmp_path):
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    monkeypatch.setattr(bt.time, "time", lambda: 100.0)
    monkeypatch.setattr(bt.os, "getpid", lambda: 456)
    monkeypatch.setattr(bt, "_fanxiu_process_exists", lambda pid: False)
    owner_path.write_text(
        json.dumps({"pid": 123, "entry_id": "entry", "step": "idle_guard", "updated_at": 99.0}),
        encoding="utf-8",
    )

    status = bt.read_fanxiu_behavior_tree_service_owner(owner_path, stale_after_seconds=30.0)

    assert status["active"] is False
    assert status["stale"] is True
    assert "owner 进程不存在" in status["error"]


def test_core_service_owner_marks_reused_non_fanxiu_pid_stale(monkeypatch, tmp_path):
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    monkeypatch.setattr(bt.time, "time", lambda: 100.0)
    monkeypatch.setattr(bt.os, "getpid", lambda: 456)
    monkeypatch.setattr(bt, "_fanxiu_process_exists", lambda pid: True)
    monkeypatch.setattr(bt, "_fanxiu_process_matches_service_owner", lambda pid: False)
    owner_path.write_text(
        json.dumps({"pid": 123, "entry_id": "entry", "step": "scheduler_poll", "updated_at": 99.0}),
        encoding="utf-8",
    )

    status = bt.read_fanxiu_behavior_tree_service_owner(owner_path, stale_after_seconds=30.0)

    assert status["active"] is False
    assert status["stale"] is True
    assert "owner 进程不是凡修常驻服务" in status["error"]


def test_runtime_runner_owner_acquire_ignores_dead_pid_before_ttl(monkeypatch, tmp_path):
    runtime_state_path = tmp_path / "runtime_state.json"
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    owner_path.parent.mkdir(parents=True, exist_ok=True)
    owner_path.write_text(
        json.dumps({"pid": 123, "entry_id": "entry", "step": "idle_guard", "updated_at": 99.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: runtime_state_path)
    monkeypatch.setattr(runtime_runner_core.time, "time", lambda: 100.0)
    monkeypatch.setattr(runtime_runner_core, "_fanxiu_process_matches_service_owner", lambda pid: False)
    runner = bt.create_fanxiu_runtime_runner()

    acquired, message = runner._acquire_service_owner("entry", 1)

    assert acquired is True
    assert message == ""
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    assert owner["resource"] == "fanxiu-behavior-tree-kernel"
    assert owner["owner_kind"] == "process"
    assert owner["owner_id"] == f"pid:{bt.os.getpid()}"
    assert owner["pid"] == bt.os.getpid()
    assert owner["lease_token"] == owner["token"]
    assert owner["heartbeat_at"] == owner["updated_at"]
    runner._release_service_owner()


def test_runtime_runner_default_close_popups_guard_is_on():
    runner = bt.create_fanxiu_runtime_runner()

    status = runner.status()

    assert status["guard_enabled"] is True
    assert status["guard_running"] is False
    assert status["guard_items"]["close_popups"]["enabled"] is True


def test_runtime_status_migrates_legacy_close_popups_guard_off_to_on():
    status = {
        "guard_group_enabled": True,
        "guard_enabled": False,
        "guard_running": False,
        "guard_items": {"close_popups": {"enabled": False}},
    }

    runtime_control.normalize_runtime_guard_items(status)

    assert status["guard_enabled"] is True
    assert status["guard_items"]["close_popups"]["enabled"] is True
    assert status["close_popups_guard_config_version"] >= 2


def test_runtime_status_preserves_versioned_close_popups_guard_off():
    status = {
        "guard_group_enabled": True,
        "guard_enabled": False,
        "close_popups_guard_config_version": 2,
        "guard_running": False,
        "guard_items": {"close_popups": {"enabled": False}},
    }

    runtime_control.normalize_runtime_guard_items(status)

    assert status["guard_enabled"] is False
    assert status["guard_items"]["close_popups"]["enabled"] is False


def test_core_manual_job_payload_normalizers_are_api_free():
    assert normalize_data_annotation_go_scene_payload({"target": "121"})["target_scene_id"] == 121
    assert normalize_data_annotation_go_scene_payload({"target": "#121"})["target_scene_id"] == 121
    assert normalize_data_annotation_go_scene_payload({"target_scene_id": " #121 "})["target_scene_id"] == 121
    assert parse_data_annotation_scene_id("#49") == 49

    payload = normalize_data_annotation_debug_eval_payload(
        {"source": "result = 'ok'", "mode": "ACT", "max_output_chars": 1, "timeout_seconds": 1}
    )

    assert payload["code"] == "result = 'ok'"
    assert payload["mode"] == "act"
    assert payload["max_output_chars"] == 200
    assert payload["timeout_seconds"] == 30


def test_core_debug_eval_payload_requires_code():
    try:
        normalize_data_annotation_debug_eval_payload({})
    except ValueError as exc:
        assert "payload.code" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_core_debug_eval_job_registers_without_api(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})

    register_fanxiu_data_annotation_debug_eval_job()
    register_fanxiu_data_annotation_debug_eval_job()

    definition = get_fanxiu_data_annotation_manual_job_definition("debug_eval")
    assert definition is not None
    assert definition.label == "调试代码"
    assert definition.normalize_payload is normalize_data_annotation_debug_eval_payload


def test_core_default_runtime_jobs_register_without_api(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})

    register_fanxiu_data_annotation_default_runtime_jobs()
    register_fanxiu_data_annotation_default_runtime_jobs()

    for task_type in (
        "detect_scene",
        "manual_tick",
        "gift_code_redeem",
        "go_scene",
        "hide_floating_window",
        "daily_signup",
        "daily_baiye",
        "daily_audit",
        "mail_cleanup",
    ):
        assert get_fanxiu_data_annotation_manual_job_definition(task_type) is not None
    assert get_fanxiu_data_annotation_manual_job_definition("mail_claim_check_v2") is None
    go_scene = get_fanxiu_data_annotation_manual_job_definition("go_scene")
    assert go_scene is not None
    assert go_scene.normalize_payload is normalize_data_annotation_go_scene_payload
    definitions = list_fanxiu_data_annotation_manual_job_definitions()
    assert [definition.task_type for definition in definitions] == sorted(
        definition.task_type for definition in definitions
    )


def test_core_manual_job_catalog_initializes_default_jobs(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})

    catalog = bt.fanxiu_data_annotation_manual_job_catalog()

    by_type = {item["task_type"]: item for item in catalog}
    assert by_type["go_scene"]["label"]
    assert by_type["go_scene"]["has_payload_normalizer"] is True
    assert by_type["mail_cleanup"]["scheduler_supported"] is True
    assert by_type["daily_audit"]["label"] == "日常_复核"
    assert by_type["daily_audit"]["scheduler_supported"] is False
    assert "mail_claim_check_v2" not in by_type


def test_core_mail_cleanup_job_uses_latest_runtime_impl(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu.data_annotation.jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_manual_job_definition("mail_cleanup")
    calls = []

    class FakeRunner:
        def _execute_mail_cleanup_task(self, ctx, stop_event, payload):
            calls.append((ctx, stop_event, payload))
            return "success"

    stop_event = object()
    assert definition is not None
    assert definition.handler(FakeRunner(), {"ctx": True}, {"max_actions": 1}, stop_event) == "success"
    assert calls == [({"ctx": True}, stop_event, {"max_actions": 1})]


def test_local_behavior_tree_entrypoint_initializes_default_jobs(tmp_path, monkeypatch):
    calls = []

    class FakeRunner:
        def start_local_runtime_task(self, **kwargs):
            return {"status": "success", "message": "ok"}

    entry = UserDevice(
        entry_id="entry",
        user_id=1,
        device_id="local",
        name="local",
        mode="local",
        token="",
    )
    (tmp_path / "entry.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", FakeRunner())
    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda _entry_id: entry)
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(bt, "ensure_fanxiu_runtime_jobs_registered", lambda: calls.append("registered"))

    status = bt.run_fanxiu_local_task(bt.FanxiuLocalRunRequest(task_type="go_scene", entry_id="entry"))

    assert status["status"] == "success"
    assert calls == ["registered"]


def test_core_local_python_helpers_wrap_local_task(monkeypatch):
    calls = []

    def fake_run(request):
        calls.append(request)
        return {"status": "success", "task_type": request.task_type}

    monkeypatch.setattr(bt, "run_fanxiu_local_task", fake_run)
    monkeypatch.setattr(bt, "fanxiu_local_task_should_enqueue", lambda _run_mode="auto": False)

    assert bt.go_fanxiu_scene("#121", entry_id="entry", timeout_seconds=3)["task_type"] == "go_scene"
    assert calls[-1].task_type == "go_scene"
    assert calls[-1].payload == {"target_scene_id": 121, "timeout_seconds": 3.0}
    assert calls[-1].entry_id == "entry"
    assert calls[-1].isolate_jobs is True

    assert bt.run_fanxiu_mail_claim_check(
        entry_id="entry",
        observe_only=True,
        scan_mode="full",
        skip_capture=True,
        max_actions=2,
        isolate_jobs=False,
    )["task_type"] == "mail_cleanup"
    assert calls[-1].task_type == "mail_cleanup"
    assert calls[-1].payload == {
        "observe_only": True,
        "scan_mode": "full",
        "skip_capture": True,
        "max_actions": 2,
    }
    assert calls[-1].isolate_jobs is False

    assert bt.run_fanxiu_task("detect_scene", entry_id="entry")["task_type"] == "detect_scene"
    assert calls[-1].task_type == "detect_scene"
    assert calls[-1].payload == {}


def test_core_submit_fanxiu_task_auto_queues_when_other_owner_active(monkeypatch):
    calls = []

    monkeypatch.setattr(bt, "read_fanxiu_behavior_tree_service_owner", lambda: {"active": True, "pid": 11})
    monkeypatch.setattr(bt.os, "getpid", lambda: 10)

    def fake_enqueue(request):
        calls.append(("enqueue", request))
        return {"status": "queued", "phase": "manual_job_queued"}

    def fake_run(request):
        calls.append(("run", request))
        return {"status": "success"}

    monkeypatch.setattr(bt, "enqueue_fanxiu_local_manual_job", fake_enqueue)
    monkeypatch.setattr(bt, "run_fanxiu_local_task", fake_run)

    queued = bt.submit_fanxiu_task("go_scene", {"target_scene_id": 121}, entry_id="entry", run_mode="auto")

    assert queued["phase"] == "manual_job_queued"
    assert calls[-1][0] == "enqueue"
    assert calls[-1][1].task_type == "go_scene"
    assert calls[-1][1].payload == {"target_scene_id": 121}

    direct = bt.submit_fanxiu_task("detect_scene", entry_id="entry", run_mode="direct")

    assert direct["status"] == "success"
    assert calls[-1][0] == "run"
    assert calls[-1][1].task_type == "detect_scene"


def test_core_submit_fanxiu_task_waits_for_queued_job(monkeypatch):
    monkeypatch.setattr(bt, "fanxiu_local_task_should_enqueue", lambda _run_mode: True)
    monkeypatch.setattr(
        bt,
        "enqueue_fanxiu_local_manual_job",
        lambda request: {
            "status": "idle",
            "phase": "manual_job_queued",
            "queued_job": {"id": "manual-1", "task_type": request.task_type},
        },
    )
    monkeypatch.setattr(
        bt,
        "wait_fanxiu_local_manual_job",
        lambda job_id, **kwargs: {
            "done": True,
            "result": "completed",
            "job_id": job_id,
            "runtime_status": {"status": "success"},
            "wait_args": kwargs,
        },
    )

    result = bt.submit_fanxiu_task(
        "go_scene",
        {"target_scene_id": 121},
        run_mode="enqueue",
        wait=True,
        wait_timeout_seconds=9,
        wait_poll_seconds=0.2,
    )

    assert result["done"] is True
    assert result["job_id"] == "manual-1"
    assert result["submitted_status"]["queued_job"]["task_type"] == "go_scene"
    assert result["wait_args"] == {"timeout_seconds": 9, "poll_seconds": 0.2}


def test_core_wait_fanxiu_queued_status_reports_missing_job_id():
    result = bt.wait_fanxiu_queued_status({"status": "idle", "queued_job": {}})

    assert result["done"] is False
    assert result["result"] == "missing_queued_job_id"
    assert result["submitted_status"]["status"] == "idle"


def test_core_runner_facade_operations_use_registered_runner(tmp_path, monkeypatch):
    calls = []

    class FakeWakeEvent:
        def set(self):
            calls.append(("wake",))

    class FakeRunner:
        _service_wake_event = FakeWakeEvent()
        guard_definitions = []

        def status(self):
            calls.append(("status",))
            return {"running": True}

        def _runtime_task_label(self, task_type, payload=None):
            calls.append(("label", task_type, payload))
            return f"label:{task_type}"

        def start_manual_runtime_task(self, **kwargs):
            calls.append(("manual", kwargs))
            return {"status": "manual"}

        def set_guard(self, **kwargs):
            calls.append(("guard", kwargs))
            return {"status": "guard"}

        def replace_logs(self, logs):
            calls.append(("logs", logs))

    entry = UserDevice(
        entry_id="entry",
        user_id=1,
        device_id="local",
        name="local",
        mode="local",
        token="",
    )
    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", FakeRunner())
    monkeypatch.setattr(bt, "ensure_fanxiu_runtime_jobs_registered", lambda: calls.append(("register",)))
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(bt, "request_fanxiu_behavior_tree_wake", lambda **kwargs: calls.append(("wake-request", kwargs)))

    assert bt.fanxiu_runtime_runner_running() is True
    bt.fanxiu_runtime_runner_wake()
    assert bt.fanxiu_runtime_task_label("go_scene", {"target_scene_id": 121}) == "label:go_scene"
    assert bt.start_fanxiu_manual_runtime_task(entry=entry, entry_id="entry", task={"task_type": "go_scene"}) == {"status": "manual"}
    assert bt.set_fanxiu_runtime_guard(entry=entry, entry_id="entry", enabled=True, interval_seconds=3) == {"status": "guard"}
    bt.replace_fanxiu_runtime_logs([])

    assert ("wake",) in calls
    assert any(call[0] == "wake-request" for call in calls)
    assert ("register",) in calls
    assert any(call[0] == "manual" and call[1]["asset_tree_path"] == tmp_path / "entry.json" for call in calls)
    assert any(call[0] == "guard" and call[1]["guard_id"] == "close_popups" for call in calls)
    assert ("logs", []) in calls


def test_core_runtime_log_facade_filters_and_clears(monkeypatch):
    calls = []

    class FakeRunner:
        guard_definitions = {}

        def status(self):
            return {
                "logs": [
                    {"time": "01", "kind": "info", "scope": "guard", "item_id": "close_popups", "message": "guard"},
                    {"time": "02", "kind": "info", "scope": "manual_job", "item_id": "manual_job", "message": "job"},
                ]
            }

        def replace_logs(self, logs):
            calls.append(("replace_logs", logs))

    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", FakeRunner())
    monkeypatch.setattr(bt, "read_fanxiu_runtime_status", lambda: {})
    monkeypatch.setattr(bt, "persist_fanxiu_runtime_status", lambda status: calls.append(("persist", status)))

    entries = bt.fanxiu_data_annotation_runtime_logs(limit=10, scope="guard", item_id="close_popups")

    assert [item["message"] for item in entries] == ["guard"]
    status = bt.clear_fanxiu_data_annotation_runtime_logs()
    assert status["logs"] == []
    assert ("replace_logs", []) in calls
    assert any(call[0] == "persist" and call[1]["logs"] == [] for call in calls)


def test_local_behavior_tree_service_starts_and_stops_runner(tmp_path, monkeypatch):
    calls = []

    class FakeRunner:
        guard_definitions = {}

        def ensure_service(self, **kwargs):
            calls.append(("ensure_service", kwargs))
            return {"status": "idle", "service_running": True}

        def stop_service(self, **kwargs):
            calls.append(("stop_service", kwargs))
            return {"status": "idle", "service_running": False}

        def status(self):
            calls.append(("status",))
            return {"status": "idle", "service_running": True}

    entry = UserDevice(
        entry_id="entry",
        user_id=1,
        device_id="local",
        name="local",
        mode="local",
        token="",
    )
    (tmp_path / "entry.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", FakeRunner())
    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda _entry_id: entry)
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(bt, "ensure_fanxiu_runtime_jobs_registered", lambda: calls.append(("register",)))
    monkeypatch.setattr(bt, "persist_fanxiu_runtime_status", lambda status: calls.append(("persist", status)))
    monkeypatch.setattr(bt, "_current_process_is_fanxiu_service_host", lambda: True)
    monkeypatch.setattr(bt.time, "sleep", lambda _seconds: None)

    status = bt.run_fanxiu_local_service(
        bt.FanxiuLocalServiceRequest(entry_id="entry", tick_seconds=0.2, duration_seconds=0.01)
    )

    assert status == {"status": "idle", "service_running": False}
    assert ("register",) in calls
    assert any(call[0] == "ensure_service" and call[1]["tick_seconds"] == 0.2 for call in calls)
    assert any(call[0] == "stop_service" for call in calls)


def test_local_manual_job_enqueue_uses_core_runtime_control(tmp_path, monkeypatch):
    calls = []

    entry = UserDevice(
        entry_id="entry",
        user_id=1,
        device_id="local",
        name="local",
        mode="local",
        token="",
    )
    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda _entry_id: entry)
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(bt, "fanxiu_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(bt, "fanxiu_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(bt, "fanxiu_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(bt, "ensure_fanxiu_runtime_jobs_registered", lambda: calls.append(("register",)))
    monkeypatch.setattr(
        bt,
        "acquire_fanxiu_job_group_isolation",
        lambda **kwargs: calls.append(("isolate", kwargs)) or "isolate-token",
    )

    def fake_submit_manual_job(**kwargs):
        calls.append(("submit_manual_job", kwargs))
        return {
            "status": "idle",
            "phase": "manual_job_queued",
            "message": "queued",
            "queued_job": {"id": "manual-1", "task_type": kwargs["task_type"]},
        }

    monkeypatch.setattr(runtime_control, "submit_manual_job", fake_submit_manual_job)

    status = bt.enqueue_fanxiu_local_manual_job(
        bt.FanxiuLocalEnqueueRequest(
            entry_id="entry",
            task_type="go_scene",
            payload={"target_scene_id": 121},
            label="到达 #121",
            interruptible=True,
            isolation_ttl_seconds=60,
        )
    )

    assert status["phase"] == "manual_job_queued"
    assert status["queued_job"]["id"] == "manual-1"
    assert ("register",) in calls
    submit_call = next(call for call in calls if call[0] == "submit_manual_job")
    assert submit_call[1]["task_type"] == "go_scene"
    assert submit_call[1]["payload"] == {
        "target_scene_id": 121,
        "__job_group_isolation_token": "isolate-token",
    }
    assert submit_call[1]["manual_job_path"] == tmp_path / "manual_jobs.json"
    isolate_call = next(call for call in calls if call[0] == "isolate")
    assert isolate_call[1]["reason"] == "local_enqueue:go_scene"
    assert isolate_call[1]["ttl_seconds"] == 60


def test_local_manual_job_cancel_and_clear_preserve_running_by_default(tmp_path, monkeypatch):
    path = tmp_path / "manual_jobs.json"
    monkeypatch.setattr(bt, "fanxiu_data_annotation_manual_job_state_path", lambda: path)

    path.write_text(
        json.dumps(
            [
                {"id": "pending-1", "task_type": "detect_scene", "status": "pending", "created_at": 1, "updated_at": 1},
                {"id": "running-1", "task_type": "go_scene", "status": "running", "created_at": 2, "updated_at": 2},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cancelled = bt.cancel_fanxiu_local_manual_job("pending-1")
    assert cancelled["cancelled"] is True
    assert [job["id"] for job in bt.fanxiu_data_annotation_manual_jobs()] == ["running-1"]

    running_cancel = bt.cancel_fanxiu_local_manual_job("running-1")
    assert running_cancel == {"cancelled": False, "reason": "running", "job_id": "running-1", "remaining": 1}

    clear_result = bt.clear_fanxiu_local_manual_jobs()
    assert clear_result == {"removed": 0, "remaining": 1}

    forced = bt.clear_fanxiu_local_manual_jobs(force=True)
    assert forced == {"removed": 1, "remaining": 0}
    assert bt.fanxiu_data_annotation_manual_jobs() == []


def test_local_manual_job_wait_observes_queue_removal(monkeypatch):
    calls = {"jobs": 0}

    def fake_jobs():
        calls["jobs"] += 1
        if calls["jobs"] == 1:
            return [{"id": "manual-1", "status": "pending", "task_type": "go_scene"}]
        return []

    monkeypatch.setattr(bt, "fanxiu_data_annotation_manual_jobs", fake_jobs)
    monkeypatch.setattr(
        bt,
        "fanxiu_data_annotation_runtime_status",
        lambda: {
            "running": False,
            "current_task_id": "",
            "status": "success",
            "logs": [{"kind": "success", "message": "[manual-1] 手动作业完成：到场景 #34"}],
        },
    )
    monkeypatch.setattr(bt.time, "sleep", lambda _seconds: None)

    result = bt.wait_fanxiu_local_manual_job("manual-1", timeout_seconds=5, poll_seconds=0.1)

    assert result["done"] is True
    assert result["result"] == "completed"
    assert calls["jobs"] == 2


def test_local_manual_job_wait_requires_completion_evidence(monkeypatch):
    calls = {"jobs": 0}
    clock = {"now": 100.0}

    def fake_jobs():
        calls["jobs"] += 1
        if calls["jobs"] == 1:
            return [{"id": "manual-1", "status": "pending", "task_type": "go_scene"}]
        return []

    monkeypatch.setattr(bt, "fanxiu_data_annotation_manual_jobs", fake_jobs)
    monkeypatch.setattr(
        bt,
        "fanxiu_data_annotation_runtime_status",
        lambda: {"running": False, "current_task_id": "", "status": "idle", "logs": []},
    )
    monkeypatch.setattr(bt.time, "time", lambda: clock["now"])
    monkeypatch.setattr(bt.time, "sleep", lambda seconds: clock.update(now=clock["now"] + seconds))

    result = bt.wait_fanxiu_local_manual_job("manual-1", timeout_seconds=0.2, poll_seconds=0.1)

    assert result["done"] is False
    assert result["result"] == "missing_completion_evidence"
    assert calls["jobs"] >= 2


def test_local_manual_job_wait_times_out(monkeypatch):
    clock = {"now": 100.0}

    monkeypatch.setattr(
        bt,
        "fanxiu_data_annotation_manual_jobs",
        lambda: [{"id": "manual-1", "status": "running", "task_type": "go_scene"}],
    )
    monkeypatch.setattr(
        bt,
        "fanxiu_data_annotation_runtime_status",
        lambda: {"running": True, "current_task_id": "manual-1", "status": "running"},
    )
    monkeypatch.setattr(bt.time, "time", lambda: clock["now"])
    monkeypatch.setattr(bt.time, "sleep", lambda seconds: clock.update(now=clock["now"] + seconds))

    result = bt.wait_fanxiu_local_manual_job("manual-1", timeout_seconds=0.2, poll_seconds=0.1)

    assert result["done"] is False
    assert result["result"] == "timeout"
    assert result["job"]["id"] == "manual-1"


def test_core_runner_factory_creates_and_registers_runner(monkeypatch):
    class FakeRunner:
        pass

    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", None)
    monkeypatch.setattr(runner_core, "_RUNTIME_RUNNER_CLASS", None)

    bt.register_fanxiu_runtime_runner_class(FakeRunner)
    runner = bt.create_and_register_fanxiu_runtime_runner()

    assert isinstance(runner, FakeRunner)
    assert bt.get_fanxiu_runtime_runner() is runner


def test_core_runner_factory_can_create_unregistered_runner(monkeypatch):
    class FakeRunner:
        pass

    monkeypatch.setattr(runner_core, "_RUNTIME_RUNNER_CLASS", None)
    bt.register_fanxiu_runtime_runner_class(FakeRunner)

    runner = bt.create_fanxiu_runtime_runner()

    assert isinstance(runner, FakeRunner)


def test_core_job_group_isolation_lock_lifecycle(tmp_path):
    path = tmp_path / "job_group_isolation.json"

    token = bt.acquire_fanxiu_job_group_isolation(reason="test", path=path)

    assert bt.fanxiu_job_group_isolated(path) is True
    status = bt.read_fanxiu_job_group_isolation(path)
    assert status["active"] is True
    assert status["reason"] == "test"
    assert status["token"] == token
    bt.release_fanxiu_job_group_isolation(token, path=path)
    assert bt.fanxiu_job_group_isolated(path) is False


def test_core_job_group_isolation_context_releases_on_exit(tmp_path):
    path = tmp_path / "job_group_isolation.json"

    with bt.isolate_fanxiu_job_group(reason="script", path=path) as token:
        assert token
        assert bt.fanxiu_job_group_isolated(path) is True
        assert bt.read_fanxiu_job_group_isolation(path)["reason"] == "script"

    assert bt.fanxiu_job_group_isolated(path) is False


def test_core_job_group_isolation_context_releases_on_error(tmp_path):
    path = tmp_path / "job_group_isolation.json"

    try:
        with bt.isolate_fanxiu_job_group(reason="script", path=path):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert bt.fanxiu_job_group_isolated(path) is False


def test_core_job_group_isolation_expires_stale_lock(tmp_path, monkeypatch):
    path = tmp_path / "job_group_isolation.json"
    monkeypatch.setattr(bt.time, "time", lambda: 1000.0)
    bt.acquire_fanxiu_job_group_isolation(reason="test", ttl_seconds=5.0, path=path)
    monkeypatch.setattr(bt.time, "time", lambda: 1006.0)

    assert bt.fanxiu_job_group_isolated(path) is False
    assert not path.exists()


def test_core_job_group_isolation_clears_dead_owner_before_ttl(tmp_path, monkeypatch):
    path = tmp_path / "job_group_isolation.json"
    monkeypatch.setattr(bt.time, "time", lambda: 1000.0)
    bt.acquire_fanxiu_job_group_isolation(reason="local_run:detect_scene", ttl_seconds=300.0, path=path)
    monkeypatch.setattr(bt, "_fanxiu_process_exists", lambda _pid: False)
    monkeypatch.setattr(bt.time, "time", lambda: 1001.0)

    assert bt.fanxiu_job_group_isolated(path) is False
    assert not path.exists()


def test_core_job_group_isolation_clear_stale_handles_dead_owner(tmp_path, monkeypatch):
    path = tmp_path / "job_group_isolation.json"
    monkeypatch.setattr(bt.time, "time", lambda: 1000.0)
    bt.acquire_fanxiu_job_group_isolation(reason="local_run:detect_scene", ttl_seconds=300.0, path=path)
    monkeypatch.setattr(bt, "_fanxiu_process_exists", lambda _pid: False)
    monkeypatch.setattr(bt.time, "time", lambda: 1001.0)

    result = bt.clear_stale_fanxiu_job_group_isolation(path)

    assert result["cleared"] is True
    assert result["reason"] == "owner_dead"
    assert not path.exists()


def test_core_job_group_isolation_clear_stale(tmp_path, monkeypatch):
    path = tmp_path / "job_group_isolation.json"
    monkeypatch.setattr(bt.time, "time", lambda: 1000.0)
    bt.acquire_fanxiu_job_group_isolation(reason="test", ttl_seconds=5.0, path=path)
    monkeypatch.setattr(bt.time, "time", lambda: 1006.0)

    result = bt.clear_stale_fanxiu_job_group_isolation(path)

    assert result["cleared"] is True
    assert not path.exists()


def test_fanxiu_bt_script_uses_core_entrypoint_only():
    source = Path("scripts/fanxiu_bt.py").read_text(encoding="utf-8")

    assert "from backend.core.fanxiu.runtime.behavior_tree import" in source
    assert "fanxiu_data_annotation_runtime_status" in source
    assert "fanxiu_data_annotation_runtime_logs" in source
    assert "clear_fanxiu_data_annotation_runtime_logs" in source
    assert "read_fanxiu_job_group_isolation" in source
    assert "acquire_fanxiu_job_group_isolation" in source
    assert "release_fanxiu_job_group_isolation" in source
    assert "read_fanxiu_behavior_tree_service_owner" in source
    assert "enqueue_fanxiu_local_manual_job" in source
    assert "fanxiu_data_annotation_manual_jobs" in source
    assert "cancel_fanxiu_local_manual_job" in source
    assert "clear_fanxiu_local_manual_jobs" in source
    assert "request_fanxiu_behavior_tree_stop" in source
    assert "run_fanxiu_local_service" in source
    assert "fanxiu_data_annotation_manual_job_catalog" in source
    assert "wait_fanxiu_local_manual_job" in source
    assert "service" in source
    assert "stop" in source
    assert "enqueue" in source
    assert "tasks" in source
    assert "queue" in source
    assert "cancel" in source
    assert "clear-queue" in source
    assert "isolation" in source
    assert "release-isolation" in source
    assert "--run-mode" in source
    assert "--wait" in source
    assert "_add_task_run_options" in source
    assert "fanxiu_local_task_should_enqueue" in source
    assert "def _resident_owner_active_for_other_process" not in source
    assert "owner" in source
    assert "clear-logs" in source
    assert "doctor" in source
    assert "build_scheduler_plan" in source
    assert "read_scheduler_tasks" in source
    assert "from backend.api import fanxiu" not in source
    assert "backend.api.fanxiu" not in source


def test_fanxiu_bt_auto_run_mode_queues_when_other_owner_active(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    calls = []
    monkeypatch.setattr(fanxiu_bt, "fanxiu_local_task_should_enqueue", lambda mode: calls.append(mode) or mode != "direct")

    assert fanxiu_bt._task_should_enqueue("auto") is True
    assert fanxiu_bt._task_should_enqueue("enqueue") is True
    assert fanxiu_bt._task_should_enqueue("direct") is False
    assert calls == ["auto", "enqueue", "direct"]


def test_fanxiu_bt_go_scene_accepts_hash_scene_id(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    calls = []

    def fake_run(request):
        calls.append(request)
        return {"status": "success", "message": "ok"}

    monkeypatch.setattr(fanxiu_bt, "run_fanxiu_local_task", fake_run)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "--entry-id", "entry", "go-scene", "#121", "--run-mode", "direct"],
    )

    assert fanxiu_bt.main() == 0
    assert calls[0].task_type == "go_scene"
    assert calls[0].payload == {"target_scene_id": 121}
    assert calls[0].entry_id == "entry"


def test_fanxiu_bt_auto_queued_task_waits_when_requested(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    waits = []

    def fake_enqueue(request):
        return {
            "status": "idle",
            "phase": "manual_job_queued",
            "queued_job": {"id": "manual-1", "task_type": request.task_type},
        }

    def fake_wait(status, timeout_seconds):
        waits.append((status, timeout_seconds))
        return 0

    monkeypatch.setattr(fanxiu_bt, "fanxiu_local_task_should_enqueue", lambda mode: True)
    monkeypatch.setattr(fanxiu_bt, "enqueue_fanxiu_local_manual_job", fake_enqueue)
    monkeypatch.setattr(fanxiu_bt, "_wait_and_print_queued_job", fake_wait)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "--entry-id",
            "entry",
            "--wait",
            "--wait-timeout-seconds",
            "42",
            "task",
            "xianfu_visit_partner",
            "--run-mode",
            "auto",
        ],
    )

    assert fanxiu_bt.main() == 0
    assert len(waits) == 1
    assert waits[0][0]["queued_job"]["id"] == "manual-1"
    assert waits[0][1] == 42.0


def test_fanxiu_bt_direct_task_uses_wait_timeout_as_runtime_budget(monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    calls = []

    def fake_run(request):
        calls.append(request)
        return {"status": "success", "message": "ok"}

    monkeypatch.setattr(fanxiu_bt, "fanxiu_local_task_should_enqueue", lambda _mode: False)
    monkeypatch.setattr(fanxiu_bt, "run_fanxiu_local_task", fake_run)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "--entry-id",
            "entry",
            "task",
            "daily_assistant",
            "--run-mode",
            "auto",
            "--wait",
            "--wait-timeout-seconds",
            "900",
        ],
    )

    assert fanxiu_bt.main() == 0
    assert calls[0].payload["timeout_seconds"] == 900.0


def test_fanxiu_bt_doctor_json_reports_runtime_scheduler_and_logs(monkeypatch, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    monkeypatch.setattr(fanxiu_bt, "read_fanxiu_behavior_tree_service_owner", lambda: {"active": True, "pid": 123, "step": "scheduler_poll"})
    monkeypatch.setattr(
        fanxiu_bt,
        "fanxiu_data_annotation_runtime_status",
        lambda: {"service_running": True, "running": False, "status": "idle", "phase": "scheduler_poll", "current_scene": 34},
    )
    monkeypatch.setattr(fanxiu_bt, "fanxiu_data_annotation_manual_jobs", lambda: [])
    monkeypatch.setattr(fanxiu_bt, "read_fanxiu_job_group_isolation", lambda: {"active": False})
    monkeypatch.setattr(
        fanxiu_bt,
        "build_scheduler_plan",
        lambda **kwargs: {"next_action": "run_due", "message": "有到期任务", "job_group_enabled": True, "due_tasks": [{"id": "legacy-daily-youli"}]},
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "read_scheduler_tasks",
        lambda: [
            {"id": "legacy-daily-youli", "task_type": "daily_youli", "label": "日常_游历", "enabled": True, "next_time": "2026-06-15 05:00:00"},
            {"id": "disabled", "enabled": False},
        ],
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "fanxiu_data_annotation_runtime_logs",
        lambda **kwargs: [{"time": "05:00:01", "kind": "action", "scope": "job", "item_id": "legacy-daily-youli", "message": "开始到期任务：日常_游历"}],
    )
    monkeypatch.setattr(fanxiu_bt.sys, "argv", ["fanxiu_bt.py", "doctor", "--json"])

    assert fanxiu_bt.main() == 0
    output = json.loads(capsys.readouterr().out)

    assert output["owner"]["pid"] == 123
    assert output["runtime"]["status"] == "idle"
    assert output["scheduler"]["due_tasks"][0]["id"] == "legacy-daily-youli"
    assert output["scheduler"]["enabled_tasks"][0]["task_type"] == "daily_youli"
    assert output["relevant_logs"][0]["item_id"] == "legacy-daily-youli"
    assert output["maintenance"]["severity"] == "attention"
    assert output["maintenance"]["due_task_ids"] == ["legacy-daily-youli"]


def test_fanxiu_bt_idle_runtime_annotation_error_does_not_block_other_due_tasks():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {
            "service_running": False,
            "running": False,
            "status": "idle",
            "phase": "idle_tick",
            "current_task": "",
            "error": "场景跳转缺少可靠标注，已中断，请人工补标/修标后重试：目标场景=#34；当前/点击前场景=#237；动作 shape=确定",
        },
        "scheduler": {
            "next_action": "job_group_disabled",
            "message": "作业组已关闭，到期作业暂不自动执行",
            "due_tasks": [
                {"id": "legacy-daily-jianling", "label": "日常_剑灵"},
                {"id": "xianfu-visit-partner", "label": "仙府_寻访仙侣"},
            ],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-jianling",
                    "label": "日常_剑灵",
                    "enabled": True,
                    "next_time": "2026-06-21 05:00:00",
                    "last_result": "",
                },
                {
                    "id": "xianfu-visit-partner",
                    "label": "仙府_寻访仙侣",
                    "enabled": True,
                    "next_time": "2026-06-21 06:30:00",
                    "last_result": "",
                },
            ],
        },
    }

    maintenance = fanxiu_bt._build_maintenance_summary(report)

    assert maintenance["severity"] == "attention"
    assert maintenance["automation_safe"] is True
    assert maintenance["needs_human_annotation"] is False
    assert maintenance["blocked_due_count"] == 0
    assert "AI 保底调度应接管" in maintenance["action_required"][0]


def test_fanxiu_bt_doctor_summary_reports_blocked_action_and_exit_code(monkeypatch, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "checked_at": "2026-06-15 05:10:00",
        "owner": {"active": True, "pid": 123, "step": "scheduler_poll"},
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
        "scheduler": {"next_action": "blocked"},
        "screenshot": {"path": "C:/Temp/codeyun/fanxiu-evidence/doctor.png"},
        "maintenance": {
            "severity": "blocked",
            "summary": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏",
            "automation_safe": False,
            "needs_human_annotation": True,
            "blocked_by": [
                {
                    "title": "游戏公告",
                    "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少安全推进动作标注",
                }
            ],
            "due_task_count": 13,
            "stale_due_count": 13,
            "stale_due_success_count": 0,
            "blocked_due_count": 13,
            "action_required": ["在资产树「游戏公告」补充「关闭公告」动作标注"],
            "annotation_targets": [
                {
                    "title": "游戏公告",
                    "url": "/fanxiu/data-annotation?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2&focus_image_title=%E6%B8%B8%E6%88%8F%E5%85%AC%E5%91%8A",
                    "acceptable_shapes": ["关闭公告"],
                    "existing_shapes": [],
                    "all_shapes": ["公告"],
                    "missing_shapes": ["关闭公告"],
                    "required_shapes": ["关闭公告"],
                }
            ],
            "retry_condition": "阻断浮层消失且对应资产树已有安全推进动作标注",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt.sys, "argv", ["fanxiu_bt.py", "doctor", "--summary", "--exit-code"])

    assert fanxiu_bt.main() == 2
    output = capsys.readouterr().out

    assert "severity: blocked" in output
    assert "due_task_count=13" in output
    assert "stale_due_count=13" in output
    assert "blocked_due_count=13" in output
    assert "stale_due_success_count=0" in output
    assert "needs_human_annotation: True" in output
    assert "blocked_by: 游戏公告" in output
    assert "action_required: 在资产树「游戏公告」补充「关闭公告」动作标注" in output
    assert "annotation_target: 游戏公告" in output
    assert "focus_image_title=%E6%B8%B8%E6%88%8F%E5%85%AC%E5%91%8A" in output
    assert "all_shapes=公告" in output
    assert "existing_safe_shapes=" in output
    assert "acceptable_shapes=关闭公告" in output
    assert "missing_shapes=关闭公告" in output
    assert "screenshot: C:/Temp/codeyun/fanxiu-evidence/doctor.png" in output


def test_fanxiu_bt_doctor_summary_attention_exit_code(monkeypatch, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "checked_at": "2026-06-15 05:00:00",
        "owner": {"active": True, "pid": 123, "step": "scheduler_poll"},
        "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
        "scheduler": {"next_action": "run_due"},
        "maintenance": {
            "severity": "attention",
            "summary": "1 个任务已到期，等待自动执行",
            "automation_safe": True,
            "needs_human_annotation": False,
            "due_task_count": 1,
            "stale_due_count": 1,
            "stale_due_success_count": 1,
            "action_required": ["当前有到期任务且未发现阻断，等待 resident service 执行或检查服务调度日志"],
            "retry_condition": "无需特殊条件",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt.sys, "argv", ["fanxiu_bt.py", "doctor", "--summary", "--exit-code"])

    assert fanxiu_bt.main() == 1
    output = capsys.readouterr().out

    assert "severity: attention" in output
    assert "action_required: 当前有到期任务" in output


def test_fanxiu_bt_watch_doctor_writes_ndjson_and_returns_blocked(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch.ndjson"
    latest_path = output_path.with_suffix(".latest.json")
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 05:20:00",
        "owner": {"active": True, "pid": 123, "step": "scheduler_poll"},
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
        "scheduler": {"next_action": "blocked"},
        "maintenance": {
            "severity": "blocked",
            "summary": "检测到游戏公告遮挡",
            "automation_safe": False,
            "needs_human_annotation": True,
            "blocked_by": [{"title": "游戏公告", "blocking": True}],
            "due_task_count": 13,
            "due_task_ids": ["legacy-daily-youli"],
            "stale_due_count": 13,
            "stale_due_success_count": 0,
            "blocked_due_count": 13,
            "blocked_due_ids": ["legacy-daily-youli"],
            "action_required": ["在资产树「游戏公告」补充「关闭公告」动作标注"],
            "annotation_targets": [
                {
                    "title": "游戏公告",
                    "url": "/fanxiu/data-annotation?entry_id=30b82d72-8a76-4a74-be4b-4fc1591c6ce2&focus_image_title=%E6%B8%B8%E6%88%8F%E5%85%AC%E5%91%8A",
                    "acceptable_shapes": ["关闭公告"],
                    "existing_shapes": [],
                    "all_shapes": ["公告"],
                    "missing_shapes": ["关闭公告"],
                    "required_shapes": ["关闭公告"],
                }
            ],
            "retry_condition": "阻断浮层消失且对应资产树已有安全推进动作标注",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "3",
            "--stop-on-blocked",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    lines = output_path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    stable_latest = json.loads(stable_latest_path.read_text(encoding="utf-8"))
    heartbeat = json.loads(heartbeat_path.read_text(encoding="utf-8"))

    assert len(lines) == 1
    assert event["severity"] == "blocked"
    assert latest["severity"] == "blocked"
    assert stable_latest["severity"] == "blocked"
    assert heartbeat["severity"] == "blocked"
    assert heartbeat["output_path"] == str(output_path)
    assert heartbeat["auto_run_due_enabled"] is False
    assert heartbeat["stale_due_count"] == 13
    assert heartbeat["stale_due_success_count"] == 0
    assert heartbeat["blocked_due_count"] == 13
    assert latest["blocked_by"][0]["title"] == "游戏公告"
    assert latest["annotation_targets"][0]["title"] == "游戏公告"
    assert latest["annotation_targets"][0]["url"].startswith("/fanxiu/data-annotation?")
    assert event["due_task_count"] == 13
    assert event["due_task_ids"] == ["legacy-daily-youli"]
    assert event["stale_due_count"] == 13
    assert event["stale_due_success_count"] == 0
    assert event["blocked_due_count"] == 13
    assert event["blocked_due_ids"] == ["legacy-daily-youli"]
    assert event["needs_human_annotation"] is True
    assert event["annotation_targets"][0]["acceptable_shapes"] == ["关闭公告"]
    assert event["annotation_targets"][0]["all_shapes"] == ["公告"]
    assert event["annotation_targets"][0]["missing_shapes"] == ["关闭公告"]
    assert event["annotation_targets"][0]["required_shapes"] == ["关闭公告"]
    output = capsys.readouterr().out
    assert "path=" in output
    assert "latest=" in output
    assert "stable_latest=" in output


def test_fanxiu_bt_watch_doctor_stops_when_ok_no_due(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-ok.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 06:00:00",
        "owner": {"active": True, "pid": 123, "step": "scheduler_poll"},
        "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
        "scheduler": {"next_action": "idle"},
        "maintenance": {
            "severity": "ok",
            "summary": "巡检未发现阻断",
            "automation_safe": True,
            "needs_human_annotation": False,
            "due_task_count": 0,
            "due_task_ids": [],
            "stale_due_count": 0,
            "stale_due_success_count": 0,
            "blocked_due_count": 0,
            "action_required": ["当前没有到期任务"],
            "retry_condition": "无需特殊条件",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--stop-on-ok-no-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert event["severity"] == "ok"
    assert event["due_task_count"] == 0


def test_fanxiu_bt_watch_doctor_auto_runs_due_when_safe(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-auto.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-15 06:20:00",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
            "scheduler": {"next_action": "run_due", "due_tasks": [{"id": "legacy-daily-youli"}]},
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，等待自动执行",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务且未发现阻断"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-15 06:20:01",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
            "scheduler": {"next_action": "idle", "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    run_due_calls = []
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: run_due_calls.append(kwargs) or {"status": "idle", "phase": "scheduler_run_due", "message": "submitted"},
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert len(run_due_calls) == 1
    assert run_due_calls[0]["entry_id"] == "entry"
    assert event["severity"] == "ok"
    assert event["auto_run_due_enabled"] is True
    assert event["auto_run_due"]["triggered"] is True
    assert event["auto_run_due"]["phase"] == "scheduler_run_due"
    assert run_due_calls[0]["ignore_job_group_disabled"] is True


def test_fanxiu_bt_watch_doctor_auto_runs_due_when_job_group_disabled(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-job-group-disabled.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-20 05:01:00",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_job_group_disabled", "current_scene": 34},
            "scheduler": {
                "next_action": "job_group_disabled",
                "job_group_enabled": False,
                "due_tasks": [{"id": "legacy-daily-signup", "label": "日常_报名"}],
            },
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，但被作业组关闭挡住",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-signup"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务但工程作业组已关闭；AI 保底调度应接管并串行执行到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-20 05:01:02",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_due_queued", "current_scene": 34},
            "scheduler": {"next_action": "idle", "job_group_enabled": False, "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    run_due_calls = []
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: run_due_calls.append(kwargs) or {
            "status": "idle",
            "phase": "scheduler_due_queued",
            "message": "AI保底已接管作业组关闭下的到期任务：日常_报名",
            "job_group_override": True,
        },
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert len(run_due_calls) == 1
    assert run_due_calls[0]["entry_id"] == "entry"
    assert run_due_calls[0]["ignore_job_group_disabled"] is True
    assert event["severity"] == "ok"
    assert event["auto_run_due"]["triggered"] is True
    assert event["auto_run_due"]["message"].startswith("AI保底已接管作业组关闭下的到期任务")


def test_fanxiu_bt_watch_doctor_auto_runs_all_due_tasks_when_job_group_disabled(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-job-group-disabled-batch.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-20 05:00:01",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_job_group_disabled", "current_scene": 34},
            "scheduler": {
                "next_action": "job_group_disabled",
                "job_group_enabled": False,
                "due_tasks": [
                    {"id": "legacy-daily-signup", "label": "日常_报名"},
                    {"id": "legacy-daily-offer", "label": "日常_供奉"},
                ],
            },
            "maintenance": {
                "severity": "attention",
                "summary": "2 个任务已到期，但被作业组关闭挡住",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 2,
                "due_task_ids": ["legacy-daily-signup", "legacy-daily-offer"],
                "stale_due_count": 2,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务但工程作业组已关闭；AI 保底调度应接管并串行执行到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-20 05:00:03",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_due_queued", "current_scene": 34},
            "scheduler": {
                "next_action": "job_group_disabled",
                "job_group_enabled": False,
                "due_tasks": [{"id": "legacy-daily-offer", "label": "日常_供奉"}],
            },
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，但被作业组关闭挡住",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-offer"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务但工程作业组已关闭；AI 保底调度应接管并串行执行到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-20 05:00:05",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_due_queued", "current_scene": 34},
            "scheduler": {"next_action": "idle", "job_group_enabled": False, "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    run_due_calls = []

    def fake_run_due_scheduler_tasks(**kwargs):
        run_due_calls.append(kwargs)
        label = "日常_报名" if len(run_due_calls) == 1 else "日常_供奉"
        return {
            "status": "idle",
            "phase": "scheduler_due_queued",
            "message": f"AI保底已接管作业组关闭下的到期任务：{label}",
            "job_group_override": True,
        }

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(fanxiu_bt, "run_due_scheduler_tasks", fake_run_due_scheduler_tasks)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert len(run_due_calls) == 2
    assert all(call["entry_id"] == "entry" for call in run_due_calls)
    assert all(call["ignore_job_group_disabled"] is True for call in run_due_calls)
    assert event["severity"] == "ok"
    assert event["due_task_count"] == 0
    assert event["auto_run_due"]["triggered"] is True
    assert event["auto_run_due"]["run_count"] == 2
    assert len(event["auto_run_due"]["runs"]) == 2
    assert event["auto_run_due"]["runs"][0]["message"].endswith("日常_报名")
    assert event["auto_run_due"]["runs"][1]["message"].endswith("日常_供奉")


def test_fanxiu_bt_watch_doctor_waits_for_queued_auto_run_due_job(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-auto-run-wait.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-20 05:00:00",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "", "current_scene": 34},
            "manual_jobs": [],
            "isolation": {"active": False},
            "scheduler": {
                "next_action": "job_group_disabled",
                "job_group_enabled": False,
                "due_tasks": [{"id": "legacy-daily-assistant", "label": "日常_助手"}],
            },
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-assistant"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": [],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-20 05:00:05",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_due_queued", "current_scene": 34},
            "manual_jobs": [],
            "isolation": {"active": False},
            "scheduler": {"next_action": "idle", "job_group_enabled": False, "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    wait_calls = []

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: {
            "status": "idle",
            "phase": "scheduler_due_queued",
            "message": "AI保底已接管作业组关闭下的到期任务：日常_助手",
            "queued_job": {"id": "manual-1", "task_type": "daily_assistant"},
        },
    )

    def fake_wait(status, *, entry_id, timeout_seconds):
        wait_calls.append((status, entry_id, timeout_seconds))
        return {"waited": True, "job_id": "manual-1", "done": True, "result": "error"}

    monkeypatch.setattr(fanxiu_bt, "_watch_wait_for_queued_job", fake_wait)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--auto-run-due-wait-timeout-seconds",
            "12",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 0
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert len(wait_calls) == 1
    assert wait_calls[0][1] == "entry"
    assert wait_calls[0][2] == 12
    assert event["auto_run_due"]["queued_job"]["id"] == "manual-1"
    assert event["auto_run_due"]["wait_result"] == {
        "waited": True,
        "job_id": "manual-1",
        "done": True,
        "result": "error",
    }


def test_fanxiu_bt_watch_wait_keeps_service_alive_when_job_not_done(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    start_calls = []
    stop_calls = []
    monkeypatch.setattr(
        fanxiu_bt,
        "start_fanxiu_local_service",
        lambda request: start_calls.append(request) or {"service_running": True},
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "wait_fanxiu_local_manual_job",
        lambda job_id, timeout_seconds, poll_seconds: {"done": False, "result": "timeout", "runtime_status": {}},
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "stop_fanxiu_local_service",
        lambda **kwargs: stop_calls.append(kwargs),
    )

    result = fanxiu_bt._watch_wait_for_queued_job(
        {"queued_job": {"id": "manual-1"}},
        entry_id="entry",
        timeout_seconds=1,
    )

    assert result["done"] is False
    assert result["result"] == "timeout"
    assert len(start_calls) == 1
    assert stop_calls == []


def test_fanxiu_bt_watch_doctor_does_not_auto_run_when_manual_job_pending(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-manual-job-pending.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-20 12:00:20",
        "owner": {"active": False, "entry_id": "entry"},
        "runtime": {"status": "idle", "phase": "scheduler_due_queued", "current_scene": 34},
        "manual_jobs": [{"id": "manual-1", "task_type": "daily_assistant", "status": "pending"}],
        "isolation": {"active": False},
        "scheduler": {
            "next_action": "job_group_disabled",
            "job_group_enabled": False,
            "due_tasks": [{"id": "legacy-daily-assistant", "label": "日常_助手"}],
        },
        "maintenance": {
            "severity": "attention",
            "summary": "1 个任务已到期，但被作业组关闭挡住",
            "automation_safe": True,
            "needs_human_annotation": False,
            "due_task_count": 1,
            "due_task_ids": ["legacy-daily-assistant"],
            "stale_due_count": 1,
            "stale_due_success_count": 0,
            "blocked_due_count": 0,
            "action_required": ["等待手动作业队列串行执行"],
            "retry_condition": "无需特殊条件",
        },
    }
    run_due_calls = []
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "run_due_scheduler_tasks", lambda **kwargs: run_due_calls.append(kwargs))
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 1
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert run_due_calls == []
    assert event["due_task_count"] == 1
    assert event["auto_run_due"] == {}


def test_fanxiu_bt_watch_doctor_wakes_early_when_blocked_annotation_changes(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-annotation-change.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-15 06:24:00",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
            "scheduler": {"next_action": "blocked", "due_tasks": [{"id": "legacy-daily-youli"}]},
            "maintenance": {
                "severity": "blocked",
                "summary": "检测到游戏公告遮挡",
                "automation_safe": False,
                "needs_human_annotation": True,
                "blocked_by": [{"title": "游戏公告", "blocking": True, "action_shapes": []}],
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 1,
                "blocked_due_ids": ["legacy-daily-youli"],
                "action_required": ["补标"],
                "annotation_targets": [{"title": "游戏公告"}],
                "retry_condition": "补标后重试",
            },
        },
        {
            "checked_at": "2026-06-15 06:24:02",
            "owner": {"active": True, "pid": 123, "step": "scheduler_poll", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
            "scheduler": {"next_action": "run_due", "due_tasks": [{"id": "legacy-daily-youli"}]},
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，等待自动执行",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前有到期任务且未发现阻断"],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-15 06:24:03",
            "owner": {"active": True, "pid": 123, "step": "scheduler_poll", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
            "scheduler": {"next_action": "idle", "due_tasks": []},
            "maintenance": {
                "severity": "ok",
                "summary": "巡检未发现阻断",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 0,
                "due_task_ids": [],
                "stale_due_count": 0,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": ["当前没有到期任务"],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    signatures = iter([(1, 10), (2, 20)])
    sleep_calls = []
    run_due_calls = []

    class FakeTime:
        current = 100.0

        @staticmethod
        def time():
            return FakeTime.current

        @staticmethod
        def monotonic():
            return FakeTime.current

        @staticmethod
        def sleep(seconds):
            sleep_calls.append(seconds)
            FakeTime.current += float(seconds)

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_asset_tree_signature_for_entry", lambda entry_id: next(signatures))
    monkeypatch.setattr(fanxiu_bt, "time", FakeTime)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: run_due_calls.append(kwargs) or {"status": "idle", "phase": "scheduler_run_due", "message": "submitted"},
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "2",
            "--interval-seconds",
            "60",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 1
    events = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert len(events) == 2
    assert events[0]["severity"] == "blocked"
    assert events[1]["severity"] == "attention"
    assert events[1]["scheduler_next_action"] == "run_due"
    assert events[1]["auto_run_due"] == {}
    assert sleep_calls == [2.0]
    assert run_due_calls == []


def test_fanxiu_bt_watch_doctor_does_not_auto_run_due_when_blocked(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-blocked.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 06:25:00",
        "owner": {"active": True, "pid": 123, "step": "scheduler_poll", "entry_id": "entry"},
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
        "scheduler": {"next_action": "blocked", "due_tasks": [{"id": "legacy-daily-youli"}]},
        "maintenance": {
            "severity": "blocked",
            "summary": "检测到游戏公告遮挡",
            "automation_safe": False,
            "needs_human_annotation": True,
            "blocked_by": [{"title": "游戏公告", "blocking": True}],
            "due_task_count": 1,
            "due_task_ids": ["legacy-daily-youli"],
            "stale_due_count": 1,
            "stale_due_success_count": 0,
            "blocked_due_count": 1,
            "blocked_due_ids": ["legacy-daily-youli"],
            "action_required": ["补标"],
            "retry_condition": "补标后重试",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("blocked watcher must not run due")),
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert event["severity"] == "blocked"
    assert event["auto_run_due_enabled"] is True
    assert event["auto_run_due"] == {}


def test_fanxiu_bt_watch_doctor_promotes_auto_run_blocker(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-auto-run-blocker.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    reports = [
        {
            "checked_at": "2026-06-20 14:00:00",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "", "current_scene": None},
            "manual_jobs": [],
            "isolation": {"active": False},
            "scheduler": {
                "next_action": "job_group_disabled",
                "job_group_enabled": False,
                "due_tasks": [{"id": "legacy-daily-yihuo", "label": "日常_异火"}],
            },
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-yihuo"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 0,
                "action_required": [],
                "retry_condition": "无需特殊条件",
            },
        },
        {
            "checked_at": "2026-06-20 14:00:02",
            "owner": {"active": False, "pid": None, "step": "", "entry_id": "entry"},
            "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
            "manual_jobs": [],
            "isolation": {"active": False},
            "scheduler": {
                "next_action": "job_group_disabled",
                "job_group_enabled": False,
                "due_tasks": [{"id": "legacy-daily-yihuo", "label": "日常_异火"}],
            },
            "maintenance": {
                "severity": "attention",
                "summary": "1 个任务已到期，但被作业组关闭挡住",
                "automation_safe": True,
                "needs_human_annotation": False,
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-yihuo"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 1,
                "blocked_due_ids": ["legacy-daily-yihuo"],
                "action_required": [],
                "retry_condition": "无需特殊条件",
            },
        },
    ]
    blocker = {
        "scene_id": 224,
        "title": "购买破界符",
        "blocking": True,
        "all_shapes": ["购买并使用", "限购次数标识"],
        "message": "检测到 #224「购买破界符」弹窗；资产树缺少 #225「空白」，自动作业无法按 #224 连续购买到 #225 后回退",
    }

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: reports.pop(0))
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda entry_id: {"id": entry_id})
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(
        fanxiu_bt,
        "run_due_scheduler_tasks",
        lambda **kwargs: {
            "status": "idle",
            "phase": "scheduler_blocked",
            "message": blocker["message"],
            "blocking_overlays": [blocker],
        },
    )
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--auto-run-due",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 1
    event = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert event["severity"] == "blocked"
    assert event["needs_human_annotation"] is True
    assert event["blocked_by"] == [blocker]
    assert event["annotation_targets"][0]["title"] == "购买破界符"
    assert event["auto_run_due"]["blocking_overlays"] == [blocker]


def test_fanxiu_bt_watch_doctor_forces_screenshot_when_blocked(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch-blocked-screenshot.ndjson"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    include_screenshot_values = []

    def build_report(*, log_limit, include_screenshot):
        include_screenshot_values.append(bool(include_screenshot))
        report = {
            "checked_at": "2026-06-15 06:35:00",
            "owner": {"active": True, "pid": 123, "step": "scheduler_poll"},
            "runtime": {"status": "idle", "phase": "scheduler_blocked", "current_scene": None},
            "scheduler": {"next_action": "blocked"},
            "maintenance": {
                "severity": "blocked",
                "summary": "检测到游戏公告遮挡",
                "automation_safe": False,
                "needs_human_annotation": True,
                "blocked_by": [{"title": "游戏公告", "blocking": True}],
                "due_task_count": 1,
                "due_task_ids": ["legacy-daily-youli"],
                "stale_due_count": 1,
                "stale_due_success_count": 0,
                "blocked_due_count": 1,
                "blocked_due_ids": ["legacy-daily-youli"],
                "action_required": ["补标"],
                "retry_condition": "补标后重试",
            },
        }
        if include_screenshot:
            report["screenshot"] = {"path": "C:/Temp/codeyun/fanxiu-evidence/blocked.png"}
        return report

    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", build_report)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt, "time", type("FakeTime", (), {
        "time": staticmethod(lambda: 100.0),
        "monotonic": staticmethod(lambda: 100.0),
        "sleep": staticmethod(lambda _seconds: None),
    }))
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "2",
            "--screenshot",
            "--screenshot-every",
            "10",
            "--output",
            str(output_path),
        ],
    )

    assert fanxiu_bt.main() == 2
    lines = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    assert include_screenshot_values == [True, False, True]
    assert lines[0]["screenshot_path"] == "C:/Temp/codeyun/fanxiu-evidence/blocked.png"
    assert lines[1]["screenshot_path"] == "C:/Temp/codeyun/fanxiu-evidence/blocked.png"


def test_fanxiu_bt_watch_doctor_accepts_explicit_latest_json(monkeypatch, tmp_path):
    import scripts.fanxiu_bt as fanxiu_bt

    output_path = tmp_path / "watch.ndjson"
    latest_path = tmp_path / "nested" / "latest.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    report = {
        "checked_at": "2026-06-15 06:10:00",
        "owner": {"active": True, "pid": 123, "step": "scheduler_poll"},
        "runtime": {"status": "idle", "phase": "scheduler_poll", "current_scene": 34},
        "scheduler": {"next_action": "idle"},
        "maintenance": {
            "severity": "ok",
            "summary": "巡检未发现阻断",
            "automation_safe": True,
            "needs_human_annotation": False,
            "due_task_count": 0,
            "due_task_ids": [],
            "stale_due_count": 0,
            "stale_due_success_count": 0,
            "blocked_due_count": 0,
            "action_required": ["当前没有到期任务"],
            "retry_condition": "无需特殊条件",
        },
    }
    monkeypatch.setattr(fanxiu_bt, "_build_doctor_report", lambda **kwargs: report)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "watch-doctor",
            "--max-iterations",
            "1",
            "--output",
            str(output_path),
            "--latest-json",
            str(latest_path),
        ],
    )

    assert fanxiu_bt.main() == 0

    assert json.loads(latest_path.read_text(encoding="utf-8"))["severity"] == "ok"
    assert json.loads(stable_latest_path.read_text(encoding="utf-8"))["severity"] == "ok"
    assert output_path.with_suffix(".latest.json").exists() is False


def test_fanxiu_bt_ensure_watch_doctor_skips_when_heartbeat_recent(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path.write_text(
        json.dumps(
            {
                "pid": 123,
                "updated_at": 100.0,
                "stable_latest_path": str(stable_latest_path),
                "severity": "blocked",
                "auto_run_due_enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt.time, "time", lambda: 120.0)
    monkeypatch.setattr(fanxiu_bt, "read_doctor_watch_latest", lambda: {"ok": True, "snapshot": {"severity": "blocked"}})
    monkeypatch.setattr(fanxiu_bt.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not start")))
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "ensure-watch-doctor", "--stale-after-seconds", "180"],
    )

    assert fanxiu_bt.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["started"] is False
    assert result["reason"] == "heartbeat_recent"
    assert result["heartbeat"]["pid"] == 123
    assert result["heartbeat"]["auto_run_due_enabled"] is True


def test_fanxiu_bt_ensure_watch_doctor_starts_when_recent_heartbeat_lacks_auto_run_due(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeProcess:
        pid = 654

        def poll(self):
            return None

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "doctor_watch_heartbeat.json"
    stable_latest_path = tmp_path / "doctor_watch_latest.json"
    heartbeat_path.write_text(
        json.dumps({"pid": 123, "updated_at": 100.0, "stable_latest_path": str(stable_latest_path), "severity": "blocked"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt, "_stable_doctor_watch_latest_path", lambda: stable_latest_path)
    monkeypatch.setattr(fanxiu_bt.time, "time", lambda: 120.0)
    monkeypatch.setattr(fanxiu_bt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        ["fanxiu_bt.py", "ensure-watch-doctor", "--stale-after-seconds", "180"],
    )

    assert fanxiu_bt.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["started"] is True
    assert result["pid"] == 654
    assert result["previous_heartbeat"].get("auto_run_due_enabled") in {None, False}
    watch_commands = [call["command"] for call in popen_calls if "watch-doctor" in call["command"]]
    assert watch_commands
    assert "--auto-run-due" in watch_commands[-1]


def test_fanxiu_bt_ensure_watch_doctor_starts_when_heartbeat_stale(monkeypatch, tmp_path, capsys):
    import scripts.fanxiu_bt as fanxiu_bt

    class TempRoot:
        def __call__(self, *parts):
            return tmp_path.joinpath(*parts)

    class FakeProcess:
        pid = 456

        def poll(self):
            return None

    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append({"command": command, "kwargs": kwargs})
        return FakeProcess()

    heartbeat_path = tmp_path / "fanxiu-watch" / "doctor_watch_heartbeat.json"
    heartbeat_path.parent.mkdir(parents=True)
    heartbeat_path.write_text(
        json.dumps({"pid": 123, "updated_at": 100.0, "stable_latest_path": str(tmp_path / "old.json"), "severity": "blocked"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_watch_heartbeat_path", lambda: heartbeat_path)
    monkeypatch.setattr(fanxiu_bt.time, "time", lambda: 400.0)
    monkeypatch.setattr(fanxiu_bt.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        fanxiu_bt.sys,
        "argv",
        [
            "fanxiu_bt.py",
            "ensure-watch-doctor",
            "--interval-seconds",
            "30",
            "--duration-seconds",
            "60",
            "--stale-after-seconds",
            "180",
            "--screenshot",
            "--screenshot-every",
            "2",
        ],
    )
    monkeypatch.setattr("backend.core.temp_paths.codeyun_temp_root", TempRoot())

    assert fanxiu_bt.main() == 0
    result = json.loads(capsys.readouterr().out)

    assert result["started"] is True
    assert result["pid"] == 456
    assert result["reason"] == "heartbeat_missing_or_stale"
    watch_commands = [call["command"] for call in popen_calls if "watch-doctor" in call["command"]]
    assert len(watch_commands) == 1
    command = watch_commands[0]
    assert "watch-doctor" in command
    assert "--interval-seconds" in command
    assert "30.0" in command
    assert "--duration-seconds" in command
    assert "60.0" in command
    assert "--screenshot" in command
    assert "--auto-run-due" in command
    assert result["output_path"].endswith(".ndjson")


def test_fanxiu_bt_doctor_maintenance_reports_blocking_annotation_action():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "owner": {"entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2"},
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "message": "检测到游戏公告遮挡"},
        "scheduler": {
            "next_action": "blocked",
            "message": "检测到游戏公告遮挡",
            "due_tasks": [{"id": "legacy-daily-youli"}],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-youli",
                    "label": "日常_游历",
                    "next_time": "2026-06-15 05:00:00",
                    "last_run_at": "2026-06-15 02:51:18",
                    "last_result": "success",
                }
            ],
        },
        "blocking_overlays": [
            {
                "title": "游戏公告",
                "blocking": True,
                "all_shapes": ["公告"],
                "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少安全推进动作标注",
            }
        ],
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "blocked"
    assert summary["needs_human_annotation"] is True
    assert summary["automation_safe"] is False
    assert summary["state_clean"] is True
    assert summary["due_task_ids"] == ["legacy-daily-youli"]
    assert summary["stale_due_count"] == 1
    assert summary["stale_due_success_count"] == 0
    assert summary["blocked_due_count"] == 1
    assert summary["blocked_due_ids"] == ["legacy-daily-youli"]
    assert "资产树「游戏公告」" in summary["action_required"][0]
    assert summary["annotation_targets"][0]["title"] == "游戏公告"
    assert summary["annotation_targets"][0]["query"]["focus_image_title"] == "游戏公告"
    assert summary["annotation_targets"][0]["acceptable_shapes"] == ["关闭公告"]
    assert summary["annotation_targets"][0]["existing_shapes"] == []
    assert summary["annotation_targets"][0]["all_shapes"] == ["公告"]
    assert summary["annotation_targets"][0]["missing_shapes"] == ["关闭公告"]
    assert summary["annotation_targets"][0]["required_shapes"] == ["关闭公告"]
    assert "安全处理动作标注" in summary["retry_condition"]


def test_fanxiu_bt_doctor_maintenance_blocks_runtime_annotation_error():
    import scripts.fanxiu_bt as fanxiu_bt

    error = (
        "日常_灵塔：当前不在可识别的世界或日常页，且无法通过场景图恢复到 #69："
        "场景跳转缺少可靠标注，已中断，请人工补标/修标后重试："
        "目标场景=#69；当前/点击前场景=#237；动作 shape=确定"
    )
    report = {
        "runtime": {"status": "idle", "phase": "idle_guard", "error": error},
        "scheduler": {
            "next_action": "job_group_disabled",
            "due_tasks": [{"id": "legacy-daily-lingta"}],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-lingta",
                    "label": "日常_灵塔",
                    "last_result": "error",
                }
            ],
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "blocked"
    assert summary["automation_safe"] is False
    assert summary["needs_human_annotation"] is True
    assert summary["blocked_by"][0]["title"] == "场景跳转标注缺失"
    assert "请人工补标/修标" in summary["action_required"][0]
    assert summary["blocked_due_ids"] == ["legacy-daily-lingta"]


def test_fanxiu_bt_doctor_runtime_annotation_error_only_blocks_matching_due_task():
    import scripts.fanxiu_bt as fanxiu_bt

    error = (
        "日常_助手：无法通过场景图跳转到 #69；需要补当前场景到日常页的路由/返回/离开标注："
        "场景跳转缺少可靠标注，已中断，请人工补标/修标后重试："
        "目标场景=#69；当前/点击前场景=unknown；动作 shape=unknown"
    )
    report = {
        "runtime": {"status": "idle", "phase": "error", "current_scene": 69, "error": error},
        "scheduler": {
            "next_action": "job_group_disabled",
            "due_tasks": [
                {"id": "daily-boss", "label": "日常_首领"},
                {"id": "legacy-daily-assistant", "label": "日常_助手"},
            ],
            "enabled_tasks": [
                {
                    "id": "daily-boss",
                    "label": "日常_首领",
                    "last_result": "queued",
                },
                {
                    "id": "legacy-daily-assistant",
                    "label": "日常_助手",
                    "last_result": "error",
                },
            ],
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "attention"
    assert summary["automation_safe"] is True
    assert summary["needs_human_annotation"] is False
    assert summary["blocked_by"] == []
    assert summary["blocked_due_ids"] == ["legacy-daily-assistant"]


def test_fanxiu_bt_doctor_maintenance_reports_daily_audit_visual_incomplete():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "scheduler_poll", "message": "idle"},
        "scheduler": {
            "next_action": "idle",
            "message": "当前没有到期任务",
            "due_tasks": [],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-jianling",
                    "task_type": "daily_jianling",
                    "label": "日常_剑灵",
                    "enabled": True,
                    "next_time": "2026-06-21 05:00:00",
                    "last_run_at": "2026-06-20 05:10:00",
                    "last_result": "success",
                }
            ],
            "daily_audit": {
                "mapped_incomplete": [
                    {
                        "task_id": "legacy-daily-jianling",
                        "task_type": "daily_jianling",
                        "title": "淬剑试炼",
                        "progress": {"current": 0, "total": 1},
                    }
                ],
                "unmapped_incomplete": [
                    {
                        "title": "寻道历练1次",
                        "progress": {"current": 0, "total": 4},
                    }
                ],
            },
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "attention"
    assert summary["visual_incomplete_count"] == 1
    assert summary["visual_incomplete_ids"] == ["legacy-daily-jianling"]
    assert summary["visual_unmapped_incomplete_count"] == 1
    assert "日常页复核" in summary["action_required"][0]


def test_fanxiu_bt_doctor_maintenance_ignores_stale_daily_audit_visual_incomplete():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "scheduler_poll", "message": "idle"},
        "scheduler": {
            "next_action": "idle",
            "message": "当前没有到期任务",
            "due_tasks": [],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-lingta",
                    "task_type": "daily_lingta",
                    "label": "日常_灵塔",
                    "enabled": True,
                    "next_time": "2026-06-21 05:00:00",
                    "last_run_at": "2026-06-20 20:08:44",
                    "last_result": "success",
                }
            ],
            "daily_audit": {
                "updated_at": datetime(2026, 6, 20, 15, 7, 28).timestamp(),
                "mapped_incomplete": [
                    {
                        "task_id": "legacy-daily-lingta",
                        "task_type": "daily_lingta",
                        "title": "挑战或扫荡混沌灵塔",
                        "progress": {"current": 0, "total": 1},
                    }
                ],
            },
        },
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["visual_incomplete_count"] == 0
    assert summary["visual_incomplete_ids"] == []


def test_fanxiu_bt_doctor_maintenance_distinguishes_blocked_due_from_old_success():
    import scripts.fanxiu_bt as fanxiu_bt

    report = {
        "runtime": {"status": "idle", "phase": "scheduler_blocked", "message": "检测到游戏公告遮挡"},
        "scheduler": {
            "next_action": "blocked",
            "message": "检测到游戏公告遮挡",
            "due_tasks": [{"id": "legacy-daily-youli"}],
            "enabled_tasks": [
                {
                    "id": "legacy-daily-youli",
                    "label": "日常_游历",
                    "next_time": "2026-06-15 05:00:00",
                    "last_run_at": "2026-06-15 02:51:18",
                    "last_result": "blocked",
                }
            ],
        },
        "blocking_overlays": [
            {
                "title": "游戏公告",
                "blocking": True,
                "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少安全推进动作标注",
            }
        ],
    }

    summary = fanxiu_bt._build_maintenance_summary(report)

    assert summary["severity"] == "blocked"
    assert summary["due_task_ids"] == ["legacy-daily-youli"]
    assert summary["stale_due_count"] == 1
    assert summary["stale_due_success_count"] == 0
    assert summary["blocked_due_count"] == 1
    assert summary["blocked_due_ids"] == ["legacy-daily-youli"]


def test_fanxiu_bt_doctor_ignores_reward_popup_words_without_context(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "百脉宝魄 点击查看"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return []

        def _index_images(self, tree):
            return {186: {"title": "灵祖奖励浮层", "shapes": [{"title": "奖励浮层标识"}]}}

        def _find_shape(self, image, title):
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == []


def test_fanxiu_bt_doctor_reports_game_announcement_blocker(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "游戏公告 更新公告 风险提醒"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return [{"type": "image", "title": "游戏公告", "shapes": [{"title": "公告"}]}]

        def _index_images(self, tree):
            return {}

        def _find_asset_image_by_title(self, ctx, title):
            for item in ctx.get("asset_tree") or []:
                if item.get("title") == title:
                    return item
            return None

        def _find_shape(self, image, title):
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == [{
        "scene_id": None,
        "title": "游戏公告",
        "keywords": ["游戏公告", "更新公告", "风险提醒"],
        "all_shapes": ["公告"],
        "action_shapes": [],
        "blocking": True,
        "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏",
    }]


def test_fanxiu_bt_doctor_reports_dungeon_purchase_blocker(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "破界符 剩余限购次数：1 价格：200 购买并使用"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return []

        def _index_images(self, tree):
            return {
                224: {
                    "title": "购买破界符",
                    "shapes": [
                        {"title": "购买并使用"},
                        {"title": "限购次数标识"},
                    ],
                }
            }

        def _find_asset_image_by_title(self, ctx, title):
            return None

        def _find_shape(self, image, title):
            if not isinstance(image, dict):
                return None
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == [{
        "scene_id": 224,
        "title": "购买破界符",
        "keywords": ["破界符", "购买并使用", "剩余限购次数", "价格"],
        "all_shapes": ["购买并使用", "限购次数标识"],
        "action_shapes": ["购买并使用"],
        "blocking": True,
        "message": "检测到 #224「购买破界符」弹窗；资产树缺少 #225「空白」，自动作业无法按 #224 连续购买到 #225 后回退",
    }]


def test_fanxiu_bt_doctor_does_not_infer_game_announcement_action_from_jump_target(tmp_path, monkeypatch):
    import scripts.fanxiu_bt as fanxiu_bt

    class FakeRunner:
        def _ocr_lines(self, frame):
            return [{"text": "游戏公告 更新公告 风险提醒"}]

        def _ocr_text(self, lines):
            return "".join(item["text"] for item in lines)

        def _load_asset_tree(self, path):
            return [{"type": "image", "title": "游戏公告", "shapes": [{"title": "公告", "sceneJumpTarget": "18"}]}]

        def _index_images(self, tree):
            return {}

        def _find_asset_image_by_title(self, ctx, title):
            for item in ctx.get("asset_tree") or []:
                if item.get("title") == title:
                    return item
            return None

        def _find_shape(self, image, title):
            for shape in image.get("shapes") or []:
                if shape.get("title") == title:
                    return shape
            return None

    screenshot = tmp_path / "frame.png"
    screenshot.write_bytes(b"fake-png")

    monkeypatch.setattr(fanxiu_bt, "create_fanxiu_runtime_runner", lambda: FakeRunner())

    blockers = fanxiu_bt._doctor_blocking_overlays({"path": str(screenshot)})

    assert blockers == [{
        "scene_id": None,
        "title": "游戏公告",
        "keywords": ["游戏公告", "更新公告", "风险提醒"],
        "all_shapes": ["公告"],
        "action_shapes": [],
        "blocking": True,
        "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，自动作业无法安全进入游戏",
    }]


def test_runtime_management_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/runtime/management.py").read_text(encoding="utf-8")

    assert "from backend.api.fanxiu import" not in source
    assert "backend.api.fanxiu" not in source


def test_core_runtime_runner_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/fanxiu/data_annotation/runtime_runner.py").read_text(encoding="utf-8")

    assert "from backend.api import fanxiu" not in source
    assert "backend.api.fanxiu" not in source


def test_core_runtime_runner_db_engine_is_lazy_loaded():
    source = Path("backend/core/fanxiu/data_annotation/runtime_runner.py").read_text(encoding="utf-8")
    header = source.split("FULLWIDTH_DIGIT_TRANSLATION", 1)[0]

    assert "from backend.db import" not in header
    assert "from backend.models import" not in header
    assert "from sqlmodel import" not in header
    assert "from fastapi import" not in header
    assert "UserDevice" not in header
    assert "FanxiuMailRecord" not in header
    assert "_default_engine: Any | None = None" in source
    assert "def _db_engine" in source
    assert "from backend.db import engine" in source


def test_core_runtime_runner_import_does_not_load_codeyun_orm_modules():
    code = "\n".join(
        [
            "import sys",
            "import backend.core.fanxiu.data_annotation.runtime_runner",
            "for name in ('backend.models', 'backend.db', 'sqlmodel', 'fastapi'):",
            "    assert name not in sys.modules, name",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_core_runtime_runner_factory_does_not_import_runner_at_module_top():
    source = Path("backend/core/fanxiu/data_annotation/runner.py").read_text(encoding="utf-8")
    header = source.split("def _default_fanxiu_runtime_runner_class", 1)[0]

    assert "fanxiu_data_annotation_runtime_runner" not in header
    assert "from backend.core.fanxiu.data_annotation.runtime_runner import DataAnnotationRuntimeRunner" in source


def test_core_runtime_control_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/fanxiu/data_annotation/runtime_control.py").read_text(encoding="utf-8")
    api_source = Path("backend/api/fanxiu.py").read_text(encoding="utf-8")
    runtime_runner_source = Path("backend/core/fanxiu/data_annotation/runtime_runner.py").read_text(encoding="utf-8")

    assert "from backend.api import fanxiu" not in source
    assert "backend.api.fanxiu" not in source
    assert "fanxiu.data_annotation import runtime_control as _runtime_control" in api_source
    assert "_runtime_control.prepare_runtime_for_scheduler_task(" in api_source
    assert "_runtime_control.start_runtime_task(" in api_source
    assert "_runtime_framework.interrupt_current_cell(" in api_source
    assert "_runtime_control.read_scheduler_tasks(" in api_source
    assert "_runtime_control.update_scheduler_tasks(" in api_source
    assert "_runtime_control.submit_manual_job(" in api_source
    assert "_runtime_framework.set_guard_item_enabled(" in api_source
    assert "_runtime_framework.set_guard_group_enabled(" in api_source
    assert "_core_data_annotation_runtime_status(" in api_source
    assert "_core_data_annotation_runtime_logs(" in api_source
    assert "_core_clear_data_annotation_runtime_logs(" in api_source
    assert "_runtime_control.runtime_status(" not in api_source
    assert "_runtime_control.runtime_logs(" not in api_source
    assert "_runtime_control.clear_runtime_logs(" not in api_source
    assert "_runtime_control.run_now_scheduler_task(" in api_source
    assert "_runtime_control.run_due_scheduler_tasks(" in api_source
    assert "__job_group_isolation_token" in runtime_runner_source
    assert "self._release_job_group_isolation(isolation_token)" in runtime_runner_source
    assert "_run_service_control_loop" in runtime_runner_source
    assert "_consume_service_control_request" in runtime_runner_source
    assert "stop_current_task" in runtime_runner_source


def test_fanxiu_api_does_not_own_runtime_job_handlers():
    source = Path("backend/api/fanxiu.py").read_text(encoding="utf-8")

    assert "class _DataAnnotationRuntimeRunner" not in source
    assert "DataAnnotationRuntimeRunner as _DataAnnotationRuntimeRunner" not in source
    assert "def __getattr__(name: str)" in source
    assert "if name == \"_DataAnnotationRuntimeRunner\"" in source
    assert "class _DataAnnotationRuntimeDebugContext" not in source
    assert "def _run_data_annotation_debug_eval" not in source
    assert "def _run_data_annotation_go_scene_manual_job" not in source
    assert "register_fanxiu_data_annotation_debug_eval_job()" not in source
    assert "register_fanxiu_data_annotation_default_runtime_jobs()" not in source
    assert "register_fanxiu_runtime_runner_class(_DataAnnotationRuntimeRunner)" not in source
    assert "create_and_register_fanxiu_runtime_runner()" not in source
    assert "class _FanxiuRuntimeRunnerProxy" in source
    assert "_DATA_ANNOTATION_RUNTIME_RUNNER: Any = _FanxiuRuntimeRunnerProxy()" in source
    assert "register_fanxiu_runtime_runner(_DataAnnotationRuntimeRunner())" not in source


def test_fanxiu_api_import_does_not_load_runtime_runner_module():
    code = "\n".join(
        [
            "import sys",
            "import backend.api.fanxiu as fanxiu",
            "assert 'backend.core.fanxiu.data_annotation.runtime_runner' not in sys.modules",
            "assert type(fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER).__name__ == '_FanxiuRuntimeRunnerProxy'",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_fanxiu_api_import_does_not_register_runtime_jobs():
    code = "\n".join(
        [
            "from backend.core.fanxiu.data_annotation.jobs import _DATA_ANNOTATION_MANUAL_JOB_REGISTRY",
            "_DATA_ANNOTATION_MANUAL_JOB_REGISTRY.clear()",
            "import backend.api.fanxiu",
            "assert _DATA_ANNOTATION_MANUAL_JOB_REGISTRY == {}",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_core_runner_start_initializes_runtime_jobs(monkeypatch):
    from backend.core.fanxiu.data_annotation.jobs import _DATA_ANNOTATION_MANUAL_JOB_REGISTRY

    original_registry = dict(_DATA_ANNOTATION_MANUAL_JOB_REGISTRY)
    _DATA_ANNOTATION_MANUAL_JOB_REGISTRY.clear()
    runner = bt.create_fanxiu_runtime_runner()
    monkeypatch.setattr(
        runner,
        "_run_inline_runtime_task",
        lambda **kwargs: {"status": "success", "task_type": kwargs["task_type"]},
    )
    try:
        status = runner.start_runtime_task(
            entry=object(),
            entry_id="entry",
            task_type="go_scene",
            payload={"target_scene_id": 121},
            asset_tree_path=Path("dummy.json"),
        )
        initialized_task_types = set(_DATA_ANNOTATION_MANUAL_JOB_REGISTRY)
    finally:
        _DATA_ANNOTATION_MANUAL_JOB_REGISTRY.clear()
        _DATA_ANNOTATION_MANUAL_JOB_REGISTRY.update(original_registry)

    assert status["task_type"] == "go_scene"
    assert "go_scene" in initialized_task_types


def test_local_behavior_tree_entrypoint_lazily_creates_core_runner(tmp_path, monkeypatch):
    calls = []

    class FakeRunner:
        def start_local_runtime_task(self, **kwargs):
            calls.append(kwargs)
            return {"status": "success", "message": "ok"}

    entry = UserDevice(
        entry_id="entry",
        user_id=1,
        device_id="local",
        name="local",
        mode="local",
        token="",
    )
    (tmp_path / "entry.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", None)
    monkeypatch.setattr(runner_core, "_RUNTIME_RUNNER_CLASS", None)
    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda _entry_id: entry)
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    bt.register_fanxiu_runtime_runner_class(FakeRunner)

    status = bt.run_fanxiu_local_task(
        bt.FanxiuLocalRunRequest(
            task_type="go_scene",
            payload={"target_scene_id": 121},
            entry_id="entry",
            isolate_jobs=True,
        )
    )

    assert status == {"status": "success", "message": "ok"}
    assert calls[0]["entry"] is entry
    assert calls[0]["entry_id"] == "entry"
    assert calls[0]["task_type"] == "go_scene"
    assert calls[0]["payload"] == {"target_scene_id": 121}
    assert calls[0]["asset_tree_path"] == tmp_path / "entry.json"
    assert calls[0]["isolate_jobs"] is True


def test_local_behavior_tree_entrypoint_uses_registered_runner(tmp_path, monkeypatch):
    calls = []

    class FakeRunner:
        guard_definitions = []

        def start_local_runtime_task(self, **kwargs):
            calls.append(kwargs)
            return {"status": "success", "message": "registered"}

    entry = UserDevice(
        entry_id="entry",
        user_id=1,
        device_id="local",
        name="local",
        mode="local",
        token="",
    )
    (tmp_path / "entry.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(bt, "_RUNTIME_RUNNER", None)
    monkeypatch.setattr(bt, "resolve_fanxiu_entry", lambda _entry_id: entry)
    monkeypatch.setattr(bt, "data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    bt.register_fanxiu_runtime_runner(FakeRunner())

    status = bt.run_fanxiu_local_task(
        bt.FanxiuLocalRunRequest(task_type="go_scene", payload={"target_scene_id": 121}, entry_id="entry")
    )

    assert status == {"status": "success", "message": "registered"}
    assert calls[0]["entry"] is entry

