from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.core import fanxiu_behavior_tree as bt
from backend.core import fanxiu_data_annotation_runtime_control as runtime_control
from backend.core.fanxiu_data_annotation_jobs import (
    get_fanxiu_data_annotation_manual_job_definition,
    list_fanxiu_data_annotation_manual_job_definitions,
    normalize_data_annotation_debug_eval_payload,
    normalize_data_annotation_go_scene_payload,
    parse_data_annotation_scene_id,
)
from backend.core.fanxiu_data_annotation_debug_eval import register_fanxiu_data_annotation_debug_eval_job
from backend.core.fanxiu_data_annotation_default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core import fanxiu_data_annotation_runner as runner_core
from backend.models import UserDevice


def test_core_behavior_tree_facade_does_not_import_codeyun_db_at_module_top():
    source = Path("backend/core/fanxiu_behavior_tree.py").read_text(encoding="utf-8")
    header = source.split("DEFAULT_FANXIU_ENTRY_ID", 1)[0]

    assert "from backend.db import" not in header
    assert "from backend.models import" not in header
    assert "from sqlmodel import" not in header
    assert "def resolve_fanxiu_entry" in source


def test_core_runtime_control_does_not_import_codeyun_model_at_module_top():
    source = Path("backend/core/fanxiu_data_annotation_runtime_control.py").read_text(encoding="utf-8")
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


def test_core_service_owner_diagnostics(monkeypatch, tmp_path):
    owner_path = tmp_path / "behavior_tree_service_owner.json"

    assert bt.read_fanxiu_behavior_tree_service_owner(owner_path)["active"] is False

    monkeypatch.setattr(bt.time, "time", lambda: 100.0)
    owner_path.write_text(
        json.dumps({"pid": 123, "entry_id": "entry", "step": "idle_guard", "updated_at": 95.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bt, "_fanxiu_process_exists", lambda pid: True)

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
    monkeypatch.setattr("backend.core.fanxiu_data_annotation_jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})

    register_fanxiu_data_annotation_debug_eval_job()
    register_fanxiu_data_annotation_debug_eval_job()

    definition = get_fanxiu_data_annotation_manual_job_definition("debug_eval")
    assert definition is not None
    assert definition.label == "调试代码"
    assert definition.normalize_payload is normalize_data_annotation_debug_eval_payload


def test_core_default_runtime_jobs_register_without_api(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu_data_annotation_jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})

    register_fanxiu_data_annotation_default_runtime_jobs()
    register_fanxiu_data_annotation_default_runtime_jobs()

    for task_type in (
        "detect_scene",
        "manual_tick",
        "gift_code_redeem",
        "go_scene",
        "hide_floating_window",
        "daily_signup",
        "mail_claim_check",
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
    monkeypatch.setattr("backend.core.fanxiu_data_annotation_jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})

    catalog = bt.fanxiu_data_annotation_manual_job_catalog()

    by_type = {item["task_type"]: item for item in catalog}
    assert by_type["go_scene"]["label"]
    assert by_type["go_scene"]["has_payload_normalizer"] is True
    assert by_type["mail_claim_check"]["scheduler_supported"] is True
    assert "mail_claim_check_v2" not in by_type


def test_core_mail_claim_check_job_uses_latest_runtime_impl(monkeypatch):
    monkeypatch.setattr("backend.core.fanxiu_data_annotation_jobs._DATA_ANNOTATION_MANUAL_JOB_REGISTRY", {})
    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_manual_job_definition("mail_claim_check")
    calls = []

    class FakeRunner:
        def _execute_mail_claim_check_v2_task(self, ctx, stop_event, payload):
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
    )["task_type"] == "mail_claim_check"
    assert calls[-1].task_type == "mail_claim_check"
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
        def ensure_service(self, **kwargs):
            calls.append(("ensure_service", kwargs))
            return {"status": "idle", "service_running": True}

        def stop_service(self, **kwargs):
            calls.append(("stop_service", kwargs))
            return {"status": "idle", "service_running": False}

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

    assert "from backend.core.fanxiu_behavior_tree import" in source
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


def test_runtime_management_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/runtime_management.py").read_text(encoding="utf-8")

    assert "from backend.api.fanxiu import" not in source
    assert "backend.api.fanxiu" not in source


def test_core_runtime_runner_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/fanxiu_data_annotation_runtime_runner.py").read_text(encoding="utf-8")

    assert "from backend.api import fanxiu" not in source
    assert "backend.api.fanxiu" not in source


def test_core_runtime_runner_db_engine_is_lazy_loaded():
    source = Path("backend/core/fanxiu_data_annotation_runtime_runner.py").read_text(encoding="utf-8")
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
            "import backend.core.fanxiu_data_annotation_runtime_runner",
            "for name in ('backend.models', 'backend.db', 'sqlmodel', 'fastapi'):",
            "    assert name not in sys.modules, name",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_core_runtime_runner_factory_does_not_import_runner_at_module_top():
    source = Path("backend/core/fanxiu_data_annotation_runner.py").read_text(encoding="utf-8")
    header = source.split("def _default_fanxiu_runtime_runner_class", 1)[0]

    assert "fanxiu_data_annotation_runtime_runner" not in header
    assert "from backend.core.fanxiu_data_annotation_runtime_runner import DataAnnotationRuntimeRunner" in source


def test_core_runtime_control_does_not_import_fanxiu_api_directly():
    source = Path("backend/core/fanxiu_data_annotation_runtime_control.py").read_text(encoding="utf-8")
    api_source = Path("backend/api/fanxiu.py").read_text(encoding="utf-8")
    runtime_runner_source = Path("backend/core/fanxiu_data_annotation_runtime_runner.py").read_text(encoding="utf-8")

    assert "from backend.api import fanxiu" not in source
    assert "backend.api.fanxiu" not in source
    assert "fanxiu_data_annotation_runtime_control as _runtime_control" in api_source
    assert "_runtime_control.ensure_runtime_service(" in api_source
    assert "_runtime_control.start_runtime_task(" in api_source
    assert "_runtime_control.stop_current_task(" in api_source
    assert "_runtime_control.read_scheduler_tasks(" in api_source
    assert "_runtime_control.update_scheduler_tasks(" in api_source
    assert "_runtime_control.submit_manual_job(" in api_source
    assert "_runtime_control.set_runtime_guard(" in api_source
    assert "_runtime_control.submit_tick_task(" in api_source
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
            "assert 'backend.core.fanxiu_data_annotation_runtime_runner' not in sys.modules",
            "assert type(fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER).__name__ == '_FanxiuRuntimeRunnerProxy'",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_fanxiu_api_import_does_not_register_runtime_jobs():
    code = "\n".join(
        [
            "from backend.core.fanxiu_data_annotation_jobs import _DATA_ANNOTATION_MANUAL_JOB_REGISTRY",
            "_DATA_ANNOTATION_MANUAL_JOB_REGISTRY.clear()",
            "import backend.api.fanxiu",
            "assert _DATA_ANNOTATION_MANUAL_JOB_REGISTRY == {}",
        ]
    )
    subprocess.run([sys.executable, "-c", code], cwd=Path.cwd(), check=True)


def test_core_runner_start_initializes_runtime_jobs(monkeypatch):
    from backend.core.fanxiu_data_annotation_jobs import _DATA_ANNOTATION_MANUAL_JOB_REGISTRY

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
