import time
from types import SimpleNamespace

from backend.api import device_entries as device_entries_api
from backend.api import runtime_management as runtime_management_api
from backend.core import runtime_management as runtime_core
from backend.core import system_metrics as system_metrics_core
from backend.models import Task, UserDevice


def _headers(test_device):
    return {"Authorization": f"Bearer {test_device['token']}"}


def test_codeyun_watchdog_description_is_fallback_not_dev_owner():
    item = runtime_core._serialize_codeyun_watchdog_service_item(
        {
            "running": True,
            "state": "running",
            "interval_seconds": 60,
            "startup": {"enabled": False},
        }
    )

    assert "无命令行主控时兜底恢复" in item["description"]
    assert "异常时重启 dev.py" not in item["description"]


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


def test_remote_entry_with_local_device_id_runtime_status_uses_local_engine(
    client,
    session,
    auth_user,
    test_device,
    monkeypatch,
):
    entry = UserDevice(
        user_id=auth_user.id,
        device_id=test_device["id"],
        mode="remote",
        name="Current Device Via Localhost",
        server_url="http://localhost:8000",
        token="local-device-token",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    captured = {}

    def fake_build_runtime_status(runtime_session, device_id):
        captured["device_id"] = device_id
        captured["same_session"] = runtime_session is session
        return {
            "device_id": device_id,
            "device": {"id": device_id},
            "groups": [{"id": "service:test", "kind": "service", "title": "测试服务"}],
            "items": [
                {
                    "id": "command:server",
                    "key": "server",
                    "kind": "service",
                    "source": "command",
                    "group_id": "service:test",
                    "group_title": "测试服务",
                    "title": "server",
                    "active": False,
                    "status": {"running": False},
                    "actions": [],
                    "raw": {},
                }
            ],
            "queue": None,
            "runner_running": False,
            "next_wake_at": None,
            "runner_error": None,
        }

    def fail_proxy_request(*args, **kwargs):
        raise AssertionError("local device runtime entry should not proxy to itself")

    monkeypatch.setattr(device_entries_api, "build_runtime_status", fake_build_runtime_status)
    monkeypatch.setattr(device_entries_api.requests, "request", fail_proxy_request)

    response = client.get(f"/api/device-entries/{entry.entry_id}/runtime/status")

    assert response.status_code == 200
    assert captured == {"device_id": test_device["id"], "same_session": True}
    payload = response.json()
    assert payload["device_id"] == test_device["id"]
    assert payload["items"][0]["title"] == "server"


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


def test_legacy_codeyun_command_runtime_item_cannot_be_triggered(client, session, test_device, monkeypatch):
    task = Task(
        id="legacy-codeyun",
        name="codeyun",
        command="uv run dev.py",
        device_id=test_device["id"],
        created_at=time.time(),
    )
    session.add(task)
    session.commit()

    monkeypatch.setattr(
        runtime_core.task_manager,
        "start_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy codeyun task must stay disabled")),
    )

    response = client.post(
        "/api/runtime/items/command/legacy-codeyun/trigger",
        headers=_headers(test_device),
    )

    assert response.status_code == 404
    assert "旧 CodeYun 命令任务已停用" in response.json()["detail"]


def test_legacy_codeyun_command_runtime_item_cannot_be_stopped(client, session, test_device, monkeypatch):
    task = Task(
        id="legacy-codeyun-stop",
        name="codeyun",
        command="uv run dev.py",
        device_id=test_device["id"],
        created_at=time.time(),
    )
    session.add(task)
    session.commit()

    monkeypatch.setattr(
        runtime_core.task_manager,
        "stop_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy codeyun task must stay disabled")),
    )

    response = client.post(
        "/api/runtime/items/command/legacy-codeyun-stop/stop",
        headers=_headers(test_device),
    )

    assert response.status_code == 404
    assert "旧 CodeYun 命令任务已停用" in response.json()["detail"]


def test_local_device_entry_runtime_item_trigger_uses_same_runtime_kernel(
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


def test_local_device_entry_runtime_item_action_uses_same_runtime_kernel(
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
            "token": "local-entry-token-2",
            "alias": "当前机器2",
        },
    )
    assert entry_response.status_code == 200
    entry_id = entry_response.json()["id"]

    monkeypatch.setattr(
        device_entries_api,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {
            "item_key": item_key,
            "action_key": action_key,
            "status": "ok",
        },
    )

    response = client.post(
        f"/api/device-entries/{entry_id}/runtime/items/builtin/attendance-behavior-tree/actions/inspect"
    )

    assert response.status_code == 200
    assert response.json() == {
        "item_key": "attendance-behavior-tree",
        "action_key": "inspect",
        "status": "ok",
    }


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


def test_fanxiu_capture_runtime_item_trims_packet_worker_raw_payload():
    item = runtime_core._serialize_fanxiu_capture_runtime_service_item({
        "running": True,
        "state": "running",
        "state_label": "运行中",
        "module": "backend.services.fanxiu_packet_daemon",
        "cwd": "D:/home/chenkunze/slns/codeyun",
        "log_path": "D:/tmp/fanxiu.log",
        "state_path": "D:/tmp/fanxiu.json",
        "process_count": 1,
        "pids": [1234],
        "updated_at": "2026-06-29 14:00:00",
        "capture_runtime": {
            "running": True,
            "game_running": True,
            "adb_connected": True,
            "root_ready": True,
            "tcpdump_ready": True,
            "active_reasons": ["watchdog"],
            "current_pcap_path": "D:/tmp/demo.pcap",
            "current_pcap_size": 2048,
            "started_at": "2026-06-29 13:58:00",
        },
        "packet_worker": {
            "updated_at": "2026-06-29 14:00:00",
            "realtime_running": True,
            "maintenance_running": False,
            "skip_reason": "",
            "huge_rows": [{"id": index, "payload": "x" * 128} for index in range(32)],
        },
    })

    assert item["status"]["realtime_running"] is True
    assert item["status"]["maintenance_running"] is False
    assert item["raw"]["packet_worker"] == {
        "updated_at": "2026-06-29 14:00:00",
        "realtime_running": True,
        "maintenance_running": False,
        "skipped": False,
        "skip_reason": "",
    }
    assert "huge_rows" not in item["raw"]["packet_worker"]


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
    assert item["actions"] == ["trigger", "stop", "logs", "configure", "inspect", "restart", "reset"]
    assert item["action_labels"]["trigger"] == "启动调度器"
    assert "唯一考勤调度器" in item["action_descriptions"]["trigger"]
    assert item["action_success_messages"]["restart"] == "已重启考勤调度器"
    assert item["action_error_messages"]["reset"] == "重置行为树状态失败"
    assert "PID 2233" in item["description"]
    assert "root 1" in item["description"]
    assert "descendant 2" in item["description"]


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


def test_run_builtin_attendance_behavior_tree_inspect_action(monkeypatch):
    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_core,
        "show_attendance_behavior_tree_schedule",
        lambda limit=20: {"status": "ok", "limit": limit},
    )

    result = runtime_core.run_builtin_runtime_item_action("attendance-behavior-tree", "inspect")

    assert result == {"status": "ok", "limit": 20}


def test_run_builtin_attendance_behavior_tree_restart_action(monkeypatch):
    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_core,
        "restart_attendance_behavior_tree_service",
        lambda: {"status": "started", "service": {"running": True}},
    )

    result = runtime_core.run_builtin_runtime_item_action("attendance-behavior-tree", "restart")

    assert result == {"status": "started", "service": {"running": True}}


def test_run_builtin_attendance_behavior_tree_reset_action(monkeypatch):
    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_core,
        "reset_attendance_behavior_tree_state",
        lambda: {"status": "ok", "service": {"running": False}},
    )

    result = runtime_core.run_builtin_runtime_item_action("attendance-behavior-tree", "reset")

    assert result == {"status": "ok", "service": {"running": False}}


def test_runtime_action_endpoint_runs_builtin_attendance_action(client, test_device, monkeypatch):
    monkeypatch.setattr(
        runtime_management_api,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )
    monkeypatch.setattr(
        runtime_core,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )

    response = client.post(
        "/api/runtime/items/builtin/attendance-behavior-tree/actions/inspect",
        headers=_headers(test_device),
    )

    assert response.status_code == 200
    assert response.json() == {
        "item_key": "attendance-behavior-tree",
        "action_key": "inspect",
        "status": "ok",
    }


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
    assert payload["action_labels"]["trigger"] == "启动调度器"
    assert "唯一考勤调度器" in payload["action_descriptions"]["trigger"]
    assert payload["action_success_messages"]["inspect"] == "已刷新调度摘要"
    assert payload["action_error_messages"]["restart"] == "重启考勤调度器失败"
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
    keys = [item["key"] for item in runtime_core._collect_builtin_services()["items"]]
    assert keys[:3] == ["ocr", "codeyun-watchdog", "proxy-traffic-audit"]
    assert "attendance-behavior-tree" not in keys
    assert "fanxiu-behavior-tree" in keys

    monkeypatch.setattr(runtime_core, "is_attendance_behavior_tree_service_enabled", lambda: True)
    keys = [item["key"] for item in runtime_core._collect_builtin_services()["items"]]
    assert keys[:3] == ["ocr", "codeyun-watchdog", "proxy-traffic-audit"]
    assert keys.index("attendance-behavior-tree") < keys.index("fanxiu-behavior-tree")


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
        assert "codepc_mf" in exc.detail
        assert "未在当前机器启用" in exc.detail
    else:
        raise AssertionError("expected HTTPException")
    assert captured == {}


def test_fanxiu_behavior_tree_serializes_inspect_action():
    item = runtime_core._serialize_fanxiu_behavior_tree_service_item({
        "running": True,
        "state": "running",
        "state_label": "运行中",
        "current_scene": 121,
        "current_task": "go_scene",
        "phase": "manual_job",
        "guard_enabled": True,
        "service_running": True,
        "task_running": True,
        "updated_at": "2026-07-01 13:00:00",
        "runtime_state_path": "D:/tmp/runtime_state.json",
        "world_facts_path": "D:/tmp/world_facts.json",
        "route_path": "/fanxiu/data-annotation/runtime",
        "last_error": "",
    })

    assert item["key"] == "fanxiu-behavior-tree"
    assert item["kind"] == "service"
    assert item["actions"] == ["trigger", "stop", "logs", "configure", "inspect", "restart", "wake"]
    assert item["action_labels"]["trigger"] == "确保行为树"
    assert "resident service" in item["action_descriptions"]["trigger"]
    assert "只停止当前业务任务" in item["action_descriptions"]["stop"]
    assert item["action_success_messages"]["wake"] == "已发送行为树唤醒请求"
    assert item["action_error_messages"]["inspect"] == "刷新运行诊断失败"
    assert item["status"]["current_scene"] == 121


def test_run_builtin_fanxiu_behavior_tree_inspect_action(monkeypatch):
    monkeypatch.setattr(runtime_core, "is_fanxiu_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_core,
        "inspect_fanxiu_behavior_tree_service",
        lambda: {"status": "ok", "owner": {"active": True}},
    )

    result = runtime_core.run_builtin_runtime_item_action("fanxiu-behavior-tree", "inspect")

    assert result == {"status": "ok", "owner": {"active": True}}


def test_run_builtin_fanxiu_behavior_tree_wake_action(monkeypatch):
    monkeypatch.setattr(runtime_core, "is_fanxiu_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_core,
        "wake_fanxiu_behavior_tree_service",
        lambda: {"status": "ok", "action": "wake"},
    )

    result = runtime_core.run_builtin_runtime_item_action("fanxiu-behavior-tree", "wake")

    assert result == {"status": "ok", "action": "wake"}


def test_run_builtin_fanxiu_behavior_tree_restart_action(monkeypatch):
    monkeypatch.setattr(runtime_core, "is_fanxiu_behavior_tree_service_enabled", lambda: True)
    monkeypatch.setattr(
        runtime_core,
        "restart_fanxiu_behavior_tree_service",
        lambda: {"status": "ok", "action": "restart"},
    )

    result = runtime_core.run_builtin_runtime_item_action("fanxiu-behavior-tree", "restart")

    assert result == {"status": "ok", "action": "restart"}


def test_restart_attendance_behavior_tree_service_replaces_existing(monkeypatch):
    captured = {}

    def fake_start(*, replace_existing: bool = True):
        captured["replace_existing"] = replace_existing
        return {"status": "started"}

    monkeypatch.setattr(runtime_core, "start_attendance_behavior_tree_service", fake_start)

    result = runtime_core.restart_attendance_behavior_tree_service()

    assert result == {"status": "started"}
    assert captured == {"replace_existing": True}


def test_restart_fanxiu_behavior_tree_service_requests_shutdown_then_ensure(monkeypatch):
    owner_states = iter([
        {"active": True, "pid": 1001},
        {"active": False, "pid": 1001},
    ])
    captured = {}

    monkeypatch.setattr(
        runtime_core,
        "_get_data_annotation_behavior_tree_status",
        lambda: {"entry_id": "entry-1", "guard_entry_id": "", "service_running": True},
    )
    monkeypatch.setattr(
        runtime_core,
        "request_fanxiu_behavior_tree_service_shutdown",
        lambda *, entry_id="", reason="": {"entry_id": entry_id, "reason": reason, "command": "shutdown_service"},
    )
    monkeypatch.setattr(runtime_core, "read_fanxiu_behavior_tree_service_owner", lambda: next(owner_states))
    monkeypatch.setattr(runtime_core, "resolve_fanxiu_entry", lambda entry_id: {"entry_id": entry_id})

    def fake_ensure(entry, entry_id=None, **kwargs):
        captured["entry"] = entry
        captured["entry_id"] = entry_id
        captured["kwargs"] = kwargs
        return {"service_running": True, "entry_id": entry_id}

    monkeypatch.setattr(runtime_core, "ensure_fanxiu_behavior_tree_service", fake_ensure)

    result = runtime_core.restart_fanxiu_behavior_tree_service(timeout_seconds=2.0, poll_seconds=0.01)

    assert result["action"] == "restart"
    assert result["shutdown_request"]["command"] == "shutdown_service"
    assert result["shutdown_request"]["reason"] == "runtime_management_restart"
    assert captured["entry"] == {"entry_id": "entry-1"}
    assert captured["entry_id"] == "entry-1"
    assert captured["kwargs"] == {}
    assert result["service"] == {"service_running": True, "entry_id": "entry-1"}


def test_runtime_action_endpoint_runs_builtin_fanxiu_action(client, test_device, monkeypatch):
    monkeypatch.setattr(
        runtime_management_api,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )
    monkeypatch.setattr(
        runtime_core,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )

    response = client.post(
        "/api/runtime/items/builtin/fanxiu-behavior-tree/actions/inspect",
        headers=_headers(test_device),
    )

    assert response.status_code == 200
    assert response.json() == {
        "item_key": "fanxiu-behavior-tree",
        "action_key": "inspect",
        "status": "ok",
    }


def test_runtime_action_endpoint_runs_builtin_fanxiu_wake_action(client, test_device, monkeypatch):
    monkeypatch.setattr(
        runtime_management_api,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )
    monkeypatch.setattr(
        runtime_core,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )

    response = client.post(
        "/api/runtime/items/builtin/fanxiu-behavior-tree/actions/wake",
        headers=_headers(test_device),
    )

    assert response.status_code == 200
    assert response.json() == {
        "item_key": "fanxiu-behavior-tree",
        "action_key": "wake",
        "status": "ok",
    }


def test_runtime_action_endpoint_runs_builtin_fanxiu_restart_action(client, test_device, monkeypatch):
    monkeypatch.setattr(
        runtime_management_api,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )
    monkeypatch.setattr(
        runtime_core,
        "run_builtin_runtime_item_action",
        lambda item_key, action_key: {"item_key": item_key, "action_key": action_key, "status": "ok"},
    )

    response = client.post(
        "/api/runtime/items/builtin/fanxiu-behavior-tree/actions/restart",
        headers=_headers(test_device),
    )

    assert response.status_code == 200
    assert response.json() == {
        "item_key": "fanxiu-behavior-tree",
        "action_key": "restart",
        "status": "ok",
    }


def test_builtin_fanxiu_behavior_tree_logs_include_owner_queue_and_doctor(session, monkeypatch):
    item = runtime_core._serialize_fanxiu_behavior_tree_service_item({
        "running": True,
        "state": "idle",
        "state_label": "常驻",
        "route_path": "/fanxiu/data-annotation/runtime",
        "runtime_state_path": "D:/tmp/runtime_state.json",
        "world_facts_path": "D:/tmp/world_facts.json",
        "logs": [{"time": "2026-07-01 13:10:00", "kind": "info", "message": "service ready"}],
    }, include_logs=True)
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
    monkeypatch.setattr(
        runtime_core,
        "read_fanxiu_behavior_tree_service_owner",
        lambda: {"active": True, "pid": 4321, "step": "scheduler_poll"},
    )
    monkeypatch.setattr(
        runtime_core,
        "read_fanxiu_job_group_isolation",
        lambda: {"active": True, "reason": "local_enqueue"},
    )
    monkeypatch.setattr(
        runtime_core,
        "fanxiu_data_annotation_manual_jobs",
        lambda: [{"id": "job-1", "status": "queued", "task_type": "go_scene", "label": "回世界"}],
    )
    monkeypatch.setattr(
        runtime_core,
        "read_doctor_watch_latest",
        lambda: {
            "ok": True,
            "exists": True,
            "path": "D:/tmp/doctor.json",
            "message": "attention: due tasks pending",
            "heartbeat": {"active": True, "updated_at": "2026-07-01 13:12:00", "pid": 5566},
            "snapshot": {"maintenance": {"severity": "attention", "summary": "due tasks pending"}},
        },
    )

    payload = runtime_core.get_runtime_item_logs("builtin", "fanxiu-behavior-tree", session)

    assert payload["kind"] == "service"
    assert any("Owner：active=True pid=4321 step=scheduler_poll" in line for line in payload["logs"])
    assert any("普通作业隔离：active=True reason=local_enqueue" in line for line in payload["logs"])
    assert any("手动作业队列：1" in line for line in payload["logs"])
    assert any("Doctor：attention" in line for line in payload["logs"])
    assert payload["action_labels"]["trigger"] == "确保行为树"
    assert "resident service" in payload["action_descriptions"]["trigger"]
    assert payload["action_success_messages"]["restart"] == "已重启凡修行为树"
    assert payload["action_error_messages"]["wake"] == "唤醒凡修行为树失败"
    assert any(
        "动作语义：trigger=ensure resident service；stop=停止当前任务；restart=shutdown_service 后重新 ensure 常驻服务；wake=唤醒 resident loop 立即重轮询"
        in line
        for line in payload["logs"]
    )


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
