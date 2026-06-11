import time
from types import SimpleNamespace

from backend.core import runtime_management as runtime_core
from backend.core import system_metrics as system_metrics_core
from backend.models import Task


def _headers(test_device):
    return {"Authorization": f"Bearer {test_device['token']}"}


def test_runtime_system_metrics_endpoint_collects_recent_sample(client, session, test_device, monkeypatch):
    calls = {"count": 0}

    def fake_sample():
        calls["count"] += 1
        return {
            "sampled_at": time.time(),
            "cpu_percent": 12.5,
            "memory_percent": 34.5,
            "memory_used": 3 * 1024 * 1024 * 1024,
            "memory_available": 5 * 1024 * 1024 * 1024,
            "memory_total": 8 * 1024 * 1024 * 1024,
        }

    monkeypatch.setattr(system_metrics_core, "_read_current_system_metric_sample", fake_sample)

    response = client.get("/api/runtime/system-metrics?hours=1", headers=_headers(test_device))

    assert response.status_code == 200
    payload = response.json()
    assert payload["device_id"] == test_device["id"]
    assert payload["interval_seconds"] == 60
    assert payload["latest"]["cpu_percent"] == 12.5
    assert payload["latest"]["memory_percent"] == 34.5
    assert payload["samples"][-1]["memory_total"] == 8 * 1024 * 1024 * 1024
    assert calls["count"] == 1

    second_response = client.get("/api/runtime/system-metrics?hours=1", headers=_headers(test_device))

    assert second_response.status_code == 200
    assert len(second_response.json()["samples"]) == 1
    assert calls["count"] == 1


def test_local_device_entry_system_metrics_uses_entry_device_id(
    client,
    session,
    auth_user,
    test_device,
    monkeypatch,
):
    monkeypatch.setattr(
        system_metrics_core,
        "_read_current_system_metric_sample",
        lambda: {
            "sampled_at": time.time(),
            "cpu_percent": 22.0,
            "memory_percent": 44.0,
            "memory_used": 1024,
            "memory_available": 2048,
            "memory_total": 3072,
        },
    )
    entry_response = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_response.status_code == 200
    entry_id = entry_response.json()["id"]

    response = client.get(f"/api/device-entries/{entry_id}/runtime/system-metrics?hours=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["device_id"] == test_device["id"]
    assert payload["latest"]["cpu_percent"] == 22.0


def test_trigger_command_job_runtime_item_queues(client, session, test_device, monkeypatch):
    task = Task(
        id="job-command",
        name="weekly",
        command="python weekly.py",
        device_id=test_device["id"],
        created_at=time.time(),
    )
    session.add(task)
    session.commit()

    captured = {}

    def fake_enqueue(task_id: str, *, trigger_reason: str):
        captured["task_id"] = task_id
        captured["trigger_reason"] = trigger_reason
        return {"task_key": task_id, "queued": True, "queue_task_id": "queue-1"}

    monkeypatch.setattr(runtime_core.task_manager, "enqueue_task_run", fake_enqueue)

    response = client.post(
        "/api/runtime/items/command/job-command/trigger",
        headers=_headers(test_device),
    )

    assert response.status_code == 200
    assert response.json()["queue_task_id"] == "queue-1"
    assert captured == {"task_id": "job-command", "trigger_reason": "manual_runtime"}


def test_trigger_command_service_runtime_item_starts_service(client, session, test_device, monkeypatch):
    task = Task(
        id="service-command",
        name="capture",
        command="python capture.py",
        device_id=test_device["id"],
        schedule="0 * * * *",
        created_at=time.time(),
    )
    session.add(task)
    session.commit()

    captured = {}

    def fake_start(task_id: str, *, replace_running: bool = False, trigger_reason: str = "manual"):
        captured["task_id"] = task_id
        captured["replace_running"] = replace_running
        captured["trigger_reason"] = trigger_reason
        return {"status": "started"}

    monkeypatch.setattr(runtime_core.task_manager, "start_task", fake_start)

    response = client.post(
        "/api/runtime/items/command/service-command/trigger",
        headers=_headers(test_device),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "started"
    assert captured == {
        "task_id": "service-command",
        "replace_running": True,
        "trigger_reason": "manual_runtime",
    }


def test_local_device_entry_runtime_item_trigger_uses_same_runtime_engine(
    client,
    session,
    auth_user,
    test_device,
    monkeypatch,
):
    entry_response = client.post(
        "/api/devices/add",
        json={
            "mode": "local",
            "token": "local-entry-token",
            "alias": "当前机器",
        },
    )
    assert entry_response.status_code == 200
    entry_id = entry_response.json()["id"]

    task = Task(
        id="entry-job-command",
        name="weekly",
        command="python weekly.py",
        device_id=test_device["id"],
        created_at=time.time(),
    )
    session.add(task)
    session.commit()

    monkeypatch.setattr(
        runtime_core.task_manager,
        "enqueue_task_run",
        lambda task_id, *, trigger_reason: {
            "task_key": task_id,
            "queued": True,
            "queue_task_id": "entry-queue-1",
        },
    )

    response = client.post(
        f"/api/device-entries/{entry_id}/runtime/items/command/entry-job-command/trigger"
    )

    assert response.status_code == 200
    assert response.json()["queue_task_id"] == "entry-queue-1"


def test_runtime_queue_uses_runtime_titles_and_preserves_duplicate_records(session, test_device, monkeypatch):
    task = Task(
        id="rime-command",
        name="小狼毫配置同步",
        command=(
            'python sync_rime_config.py --target-name codepc_mi15 '
            '--target-rime-dir "C:\\Users\\chen\\AppData\\Roaming\\Rime"'
        ),
        device_id=test_device["id"],
        runtime_kind="job",
        created_at=time.time(),
    )
    session.add(task)
    session.commit()

    queue = {
        "running": None,
        "pending": [],
        "recent": [
            {
                "id": "q-command",
                "name": "command:rime-command",
                "status": "completed",
                "finished_at": 1,
                "metadata": {"title": "小狼毫配置同步", "task_id": "rime-command"},
            },
            {
                "id": "q-rime-22",
                "name": "rime_config_sync",
                "status": "completed",
                "finished_at": 2,
                "metadata": {},
            },
            {
                "id": "q-rime-21",
                "name": "rime_config_sync",
                "status": "completed",
                "finished_at": 3,
                "metadata": {},
            },
        ],
    }
    builtin_item = runtime_core._serialize_builtin_job_item({
        "key": "rime_config_sync",
        "title": "小狼毫自动同步",
        "category": "输入法",
        "description": "",
        "enabled": True,
        "active": False,
    })

    monkeypatch.setattr(runtime_core.task_manager, "scan_running_tasks", lambda: None)
    monkeypatch.setattr(runtime_core.task_manager, "get_task_status", lambda task_id: {"running": False})
    monkeypatch.setattr(
        runtime_core,
        "_collect_builtin_jobs",
        lambda session: {
            "items": [builtin_item],
            "queue": queue,
            "runner_running": True,
            "next_wake_at": None,
            "runner_error": None,
        },
    )

    payload = runtime_core.build_runtime_status(session, test_device["id"])

    recent = payload["queue"]["recent"]
    assert [item["id"] for item in recent] == ["q-command", "q-rime-22", "q-rime-21"]
    assert [item["metadata"]["title"] for item in recent] == [
        "小狼毫到mi15",
        "小狼毫自动同步",
        "小狼毫自动同步",
    ]


def test_builtin_runtime_logs_use_runtime_title_and_queue_records(session, monkeypatch):
    queue = {
        "running": None,
        "pending": [],
        "recent": [
            {
                "id": "q-diary",
                "name": "codex_diary_yesterday_import",
                "status": "completed",
                "queued_at": 1,
                "started_at": 2,
                "finished_at": 5,
                "metadata": {},
            }
        ],
    }
    builtin_item = runtime_core._serialize_builtin_job_item({
        "key": "codex_diary_yesterday_import",
        "title": "Codex 星图日记",
        "category": "AI",
        "description": "每天凌晨读取昨日 Codex 会话，复用现有日记导入流程写入星图笔记。",
        "schedule_label": "每天 00:05",
        "enabled": True,
        "active": False,
        "latest_run": {
            "status": "completed",
            "stage_label": "写入星图笔记",
            "created_at": 1,
            "finished_at": 5,
            "created_note_count": 2,
        },
    })
    monkeypatch.setattr(
        runtime_core,
        "_collect_builtin_jobs",
        lambda session: {
            "items": [builtin_item],
            "queue": queue,
            "runner_running": True,
            "next_wake_at": None,
            "runner_error": None,
        },
    )

    payload = runtime_core.get_runtime_item_logs("builtin", "codex_diary_yesterday_import", session)

    assert payload["title"] == "Codex 星图日记"
    assert payload["records"][0]["display_name"] == "Codex 星图日记"
    assert any("最近运行" in line for line in payload["logs"])
    assert any("写入星图笔记" in line for line in payload["logs"])


def test_ocr_service_serializes_as_builtin_runtime_service():
    item = runtime_core._serialize_ocr_service_item({
        "key": "ocr",
        "title": "OCR",
        "engine": "paddleocr",
        "device": "cpu",
        "lang": "ch",
        "running": True,
        "loaded": True,
        "state": "idle",
        "instance_count": 1,
        "idle_instance_count": 1,
        "active_instance_count": 0,
        "idle_timeout_seconds": 600,
        "acquire_timeout_seconds": 30,
        "call_count": 3,
        "error_count": 0,
        "url": "http://127.0.0.1:8765",
    })

    assert item["key"] == "ocr"
    assert item["kind"] == "service"
    assert item["source"] == "builtin"
    assert item["active"] is True
    assert item["status"]["running"] is True
    assert item["actions"] == ["trigger", "stop", "logs", "configure"]
    assert "空闲10分释放" in item["description"]
    assert "独立进程" in item["description"]


def test_trigger_builtin_ocr_runtime_item_starts_external_service(session, monkeypatch):
    captured = {}

    def fake_start_ocr_service(*, replace_existing: bool = False):
        captured["replace_existing"] = replace_existing
        return {"status": "started", "service": {"key": "ocr", "running": True}}

    monkeypatch.setattr(runtime_core, "start_ocr_service", fake_start_ocr_service)

    result = runtime_core.trigger_builtin_runtime_item("ocr", session)

    assert captured == {"replace_existing": False}
    assert result == {"status": "started", "service": {"key": "ocr", "running": True}}


def test_stop_builtin_ocr_runtime_item_stops_external_service(monkeypatch):
    captured = {}

    def fake_stop_ocr_service():
        captured["called"] = True
        return {"status": "stopped", "service": {"key": "ocr", "running": False}}

    monkeypatch.setattr(runtime_core, "stop_ocr_service", fake_stop_ocr_service)

    result = runtime_core.stop_builtin_runtime_item("ocr")

    assert captured == {"called": True}
    assert result == {"status": "stopped", "service": {"key": "ocr", "running": False}}


def test_attendance_behavior_tree_serializes_as_builtin_runtime_service():
    item = runtime_core._serialize_attendance_behavior_tree_service_item({
        "key": "attendance-behavior-tree",
        "title": "考勤行为树",
        "running": True,
        "state": "running",
        "state_label": "运行中",
        "pid": 2233,
        "process_count": 1,
        "child_process_count": 2,
        "total_process_count": 3,
        "started_at": "2026-05-20 07:10:00",
        "next_run_at": "2026-05-20 21:00:00",
        "script_path": r"C:\home\chenkunze\slns\xlproject\src\xlsln\kq5034\kqmain.py",
        "cwd": r"C:\home\chenkunze\slns\xlproject\src\xlsln",
        "last_error": "",
    })

    assert item["key"] == "attendance-behavior-tree"
    assert item["kind"] == "service"
    assert item["source"] == "builtin"
    assert item["group_id"] == "service:attendance"
    assert item["active"] is True
    assert item["status"]["running"] is True
    assert item["status"]["next_run_at"] == "2026-05-20 21:00:00"
    assert item["status"]["process_count"] == 1
    assert item["status"]["child_process_count"] == 2
    assert item["status"]["total_process_count"] == 3
    assert item["next_run_at"] == "2026-05-20 21:00:00"
    assert item["actions"] == ["trigger", "stop", "logs", "configure"]
    assert "PID 2233" in item["description"]
    assert "子进程 2" in item["description"]


def test_trigger_builtin_attendance_behavior_tree_runtime_item_starts_service(session, monkeypatch):
    captured = {}

    def fake_start_attendance_behavior_tree_service(*, replace_existing: bool = True):
        captured["replace_existing"] = replace_existing
        return {"status": "started", "service": {"key": "attendance-behavior-tree", "running": True}}

    monkeypatch.setattr(
        runtime_core,
        "start_attendance_behavior_tree_service",
        fake_start_attendance_behavior_tree_service,
    )
    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)

    result = runtime_core.trigger_builtin_runtime_item("attendance-behavior-tree", session)

    assert captured == {"replace_existing": True}
    assert result == {"status": "started", "service": {"key": "attendance-behavior-tree", "running": True}}


def test_stop_builtin_attendance_behavior_tree_runtime_item_stops_service(monkeypatch):
    captured = {}

    def fake_stop_attendance_behavior_tree_service():
        captured["called"] = True
        return {"status": "stopped", "service": {"key": "attendance-behavior-tree", "running": False}}

    monkeypatch.setattr(
        runtime_core,
        "stop_attendance_behavior_tree_service",
        fake_stop_attendance_behavior_tree_service,
    )
    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)

    result = runtime_core.stop_builtin_runtime_item("attendance-behavior-tree")

    assert captured == {"called": True}
    assert result == {"status": "stopped", "service": {"key": "attendance-behavior-tree", "running": False}}


def test_builtin_attendance_behavior_tree_logs_use_service_log_builder(session, monkeypatch):
    item = runtime_core._serialize_attendance_behavior_tree_service_item({
        "key": "attendance-behavior-tree",
        "title": "考勤行为树",
        "running": False,
        "state": "stopped",
        "state_label": "已停止",
        "process_count": 0,
        "next_run_at": "2026-05-20 21:00:00",
        "script_path": r"C:\home\chenkunze\slns\xlproject\src\xlsln\kq5034\kqmain.py",
        "cwd": r"C:\home\chenkunze\slns\xlproject\src\xlsln",
        "last_error": "",
    })
    monkeypatch.setattr(
        runtime_core,
        "_collect_builtin_jobs",
        lambda session: {
            "items": [],
            "queue": None,
            "runner_running": False,
            "next_wake_at": None,
            "runner_error": None,
        },
    )
    monkeypatch.setattr(runtime_core, "_collect_builtin_services", lambda: {"items": [item]})
    monkeypatch.setattr(runtime_core, "build_attendance_behavior_tree_log_lines", lambda: ["考勤行为树日志"])

    payload = runtime_core.get_runtime_item_logs("builtin", "attendance-behavior-tree", session)

    assert payload["kind"] == "service"
    assert payload["title"] == "考勤行为树"
    assert payload["next_run_at"] == "2026-05-20 21:00:00"
    assert payload["logs"] == ["考勤行为树日志"]


def test_attendance_behavior_tree_builtin_service_is_mi15_scoped(monkeypatch):
    monkeypatch.setattr(runtime_core, "get_ocr_service_status", lambda: {"title": "OCR"})
    monkeypatch.setattr(runtime_core, "_serialize_ocr_service_item", lambda _status: {"key": "ocr"})
    monkeypatch.setattr(runtime_core, "_serialize_codeyun_watchdog_service_item", lambda: {"key": "codeyun-watchdog"})
    monkeypatch.setattr(runtime_core, "_serialize_proxy_traffic_audit_service_item", lambda: {"key": "proxy-traffic-audit"})
    monkeypatch.setattr(
        runtime_core,
        "_serialize_fanxiu_behavior_tree_service_item",
        lambda: {"key": "fanxiu-behavior-tree"},
    )
    monkeypatch.setattr(
        runtime_core,
        "_serialize_attendance_behavior_tree_service_item",
        lambda: {"key": "attendance-behavior-tree"},
    )
    monkeypatch.setattr(runtime_core, "is_fanxiu_behavior_tree_service_enabled", lambda: True)

    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: False)
    assert [item["key"] for item in runtime_core._collect_builtin_services()["items"]] == [
        "ocr",
        "codeyun-watchdog",
        "proxy-traffic-audit",
        "fanxiu-behavior-tree",
    ]

    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)
    assert [item["key"] for item in runtime_core._collect_builtin_services()["items"]] == [
        "ocr",
        "codeyun-watchdog",
        "proxy-traffic-audit",
        "attendance-behavior-tree",
        "fanxiu-behavior-tree",
    ]


def test_disabled_attendance_behavior_tree_runtime_item_cannot_start_on_non_execution_host(session, monkeypatch):
    captured = {}

    def fake_start_attendance_behavior_tree_service(*, replace_existing: bool = True):
        captured["called"] = True
        return {"status": "started"}

    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: False)
    monkeypatch.setattr(
        runtime_core,
        "start_attendance_behavior_tree_service",
        fake_start_attendance_behavior_tree_service,
    )

    try:
        runtime_core.trigger_builtin_runtime_item("attendance-behavior-tree", session)
    except runtime_core.HTTPException as exc:
        assert exc.status_code == 404
        assert "mi15" in exc.detail
    else:
        raise AssertionError("expected HTTPException")
    assert captured == {}


def test_disabled_fanxiu_behavior_tree_runtime_item_cannot_start_on_non_execution_host(session, monkeypatch):
    captured = {}

    def fake_start_behavior_tree_service(*, replace_existing: bool = True):
        captured["called"] = True
        return {"status": "started"}

    monkeypatch.setattr(runtime_core, "is_fanxiu_behavior_tree_service_enabled", lambda: False)
    monkeypatch.setattr(runtime_core, "start_behavior_tree_service", fake_start_behavior_tree_service)

    try:
        runtime_core.trigger_builtin_runtime_item("fanxiu-behavior-tree", session)
    except runtime_core.HTTPException as exc:
        assert exc.status_code == 404
        assert "mi15" in exc.detail
    else:
        raise AssertionError("expected HTTPException")
    assert captured == {}


def test_fanxiu_behavior_tree_ocr_host_prefers_explicit_env(monkeypatch):
    from backend.core import fanxiu_behavior_tree_service as fanxiu_service

    monkeypatch.setenv("FX_CODEYUN_OCR_HOST", "http://192.168.31.15:8000")

    result = fanxiu_service._resolve_codeyun_ocr_host(
        SimpleNamespace(backend_host="127.0.0.1", backend_port=8000)
    )

    assert result == "http://192.168.31.15:8000"


def test_fanxiu_behavior_tree_ocr_host_uses_loopback_for_local_child_process(monkeypatch):
    from backend.core import fanxiu_behavior_tree_service as fanxiu_service

    monkeypatch.delenv("FX_CODEYUN_OCR_HOST", raising=False)
    monkeypatch.delenv("CODEYUN_OCR_SERVICE_URL", raising=False)
    monkeypatch.delenv("CODEYUN_OCR_SERVICE_HOST", raising=False)
    monkeypatch.delenv("CODEYUN_OCR_SERVICE_PORT", raising=False)

    result = fanxiu_service._resolve_codeyun_ocr_host(
        SimpleNamespace(backend_host="192.168.31.15", backend_port=8000)
    )

    assert result == "http://127.0.0.1:8765"


def test_fanxiu_behavior_tree_ocr_host_uses_loopback_for_wildcard_bind(monkeypatch):
    from backend.core import fanxiu_behavior_tree_service as fanxiu_service

    monkeypatch.delenv("FX_CODEYUN_OCR_HOST", raising=False)
    monkeypatch.delenv("CODEYUN_OCR_SERVICE_URL", raising=False)
    monkeypatch.delenv("CODEYUN_OCR_SERVICE_HOST", raising=False)
    monkeypatch.delenv("CODEYUN_OCR_SERVICE_PORT", raising=False)
    monkeypatch.setattr(fanxiu_service, "_get_primary_lan_address", lambda: "192.168.31.15")

    result = fanxiu_service._resolve_codeyun_ocr_host(
        SimpleNamespace(backend_host="0.0.0.0", backend_port=8000)
    )

    assert result == "http://127.0.0.1:8765"


def test_fanxiu_behavior_tree_ocr_device_follows_global_setting(monkeypatch):
    from backend.core import fanxiu_behavior_tree_service as fanxiu_service

    monkeypatch.delenv("CODEYUN_OCR_DEVICE", raising=False)
    monkeypatch.delenv("FX_CODEYUN_OCR_DEVICE", raising=False)

    assert fanxiu_service._resolve_fanxiu_ocr_device(SimpleNamespace(ocr_device="gpu")) == "gpu"

    monkeypatch.setenv("FX_CODEYUN_OCR_DEVICE", "cpu")

    assert fanxiu_service._resolve_fanxiu_ocr_device(SimpleNamespace(ocr_device="gpu")) == "cpu"

    monkeypatch.setenv("CODEYUN_OCR_DEVICE", "gpu")

    assert fanxiu_service._resolve_fanxiu_ocr_device(SimpleNamespace(ocr_device="cpu")) == "gpu"


def test_fanxiu_behavior_tree_lan_address_filters_reserved_virtual_networks(monkeypatch):
    from backend.core import fanxiu_behavior_tree_service as fanxiu_service

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def connect(self, _target):
            return None

        def getsockname(self):
            return ("198.18.0.1", 53210)

    monkeypatch.setattr(fanxiu_service.socket, "socket", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(fanxiu_service.socket, "gethostname", lambda: "codepc-mi15")
    monkeypatch.setattr(
        fanxiu_service.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (fanxiu_service.socket.AF_INET, 0, 0, "", ("198.18.0.2", 0)),
            (fanxiu_service.socket.AF_INET, 0, 0, "", ("192.168.31.15", 0)),
        ],
    )

    assert fanxiu_service._get_primary_lan_address() == "192.168.31.15"
