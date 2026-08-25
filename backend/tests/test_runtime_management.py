from sqlmodel import Session, SQLModel, create_engine

from backend.api import task_manager as task_manager_module
from backend.core.devices.device import TaskStatus
from backend.core.runtime import management
from backend.models import Task


def test_fanxiu_startup_ensures_the_single_external_scheduler(monkeypatch):
    entry = type("Entry", (), {"entry_id": "entry-a"})()
    calls = []
    monkeypatch.setattr(management, "_resolve_behavior_tree_runtime_entry", lambda _session: entry)
    monkeypatch.setattr(management, "ensure_fanxiu_behavior_tree_service", lambda **_kwargs: {})
    monkeypatch.setattr(management, "_get_data_annotation_behavior_tree_status", lambda: {})
    monkeypatch.setattr(management, "_fanxiu_doctor_watch_autostart_enabled", lambda: True)
    monkeypatch.setattr(
        management,
        "ensure_doctor_watch_background",
        lambda **kwargs: calls.append(kwargs) or {"started": True},
    )

    result = management.ensure_data_annotation_behavior_tree_service(object())

    assert result["doctor_watch"]["started"] is True
    assert calls == [{}]


def test_build_runtime_status_reuses_runtime_device_for_command_status(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[Task.__table__])
    local_device_id = "device-local"

    with Session(engine) as session:
        session.add(
            Task(
                id="service-1",
                name="Service 1",
                command="python service.py",
                device_id=local_device_id,
                runtime_kind="service",
                order=0,
            )
        )
        session.add(
            Task(
                id="job-1",
                name="Job 1",
                command="python job.py",
                device_id=local_device_id,
                runtime_kind="job",
                order=1,
            )
        )
        session.commit()

        class FakeDevice:
            def __init__(self):
                self.status_calls: list[str] = []

            def get_task_status(self, task_id: str) -> TaskStatus:
                self.status_calls.append(task_id)
                return TaskStatus(id=task_id, running=task_id == "service-1", pid=123 if task_id == "service-1" else None)

            def to_dict(self):
                return {"id": local_device_id, "name": "Local Device", "type": "FakeDevice"}

        fake_device = FakeDevice()

        monkeypatch.setattr(management, "engine", engine)
        monkeypatch.setattr(management, "get_device_id", lambda: local_device_id)
        monkeypatch.setattr(management.task_manager, "scan_running_tasks", lambda restore_timeouts=False: None)
        monkeypatch.setattr(management.task_manager, "get_task_status", lambda task_id: (_ for _ in ()).throw(AssertionError("unexpected fallback status lookup")))
        monkeypatch.setattr(management, "_collect_builtin_jobs", lambda _session: {"items": [], "queue": None, "runner_running": False, "next_wake_at": None, "runner_error": None})
        monkeypatch.setattr(management, "_collect_builtin_services", lambda: {"items": []})
        monkeypatch.setattr(management.device_manager, "get_device", lambda device_id: fake_device if device_id == local_device_id else None)

        payload = management.build_runtime_status(session, local_device_id)

    assert [item["key"] for item in payload["items"]] == ["service-1", "job-1"]
    assert fake_device.status_calls == ["service-1", "job-1"]
    assert payload["device"]["name"] == "Local Device"


def test_scan_running_tasks_skips_recent_repeat_and_rescans_after_ttl(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[Task.__table__])
    local_device_id = "device-local"

    with Session(engine) as session:
        session.add(
            Task(
                id="service-1",
                name="Service 1",
                command="python service.py",
                device_id=local_device_id,
                runtime_kind="service",
                order=0,
            )
        )
        session.commit()

    class FakeDevice:
        def __init__(self):
            self.scan_calls: list[tuple[bool, list[str]]] = []

        def scan_running_tasks(self, tasks_to_check, *, deep_scan=False):
            self.scan_calls.append((deep_scan, [str(task.id) for task in tasks_to_check]))

        def get_task_status(self, task_id: str) -> TaskStatus:
            return TaskStatus(id=task_id, running=False)

    fake_device = FakeDevice()
    monotonic_values = iter([100.0, 100.5, 101.2, 111.5])
    manager = task_manager_module.TaskManager()

    try:
        monkeypatch.setattr(task_manager_module, "engine", engine)
        monkeypatch.setattr(task_manager_module.time, "monotonic", lambda: next(monotonic_values))
        monkeypatch.setattr(manager, "_get_local_device_id", lambda: local_device_id)
        monkeypatch.setattr(task_manager_module.device_manager, "get_device", lambda device_id: fake_device if device_id == local_device_id else None)

        manager.scan_running_tasks()
        manager.scan_running_tasks()
        manager.scan_running_tasks()
        manager.scan_running_tasks()
    finally:
        manager.scheduler.shutdown(wait=False)

    assert fake_device.scan_calls == [
        (False, ["service-1"]),
        (True, ["service-1"]),
        (False, ["service-1"]),
        (False, ["service-1"]),
        (True, ["service-1"]),
    ]


def test_warm_runtime_status_caches_on_startup_continues_after_errors(monkeypatch):
    calls: list[str] = []

    class DummySessionContext:
        def __enter__(self):
            calls.append("session_enter")
            return object()

        def __exit__(self, exc_type, exc, tb):
            calls.append("session_exit")
            return False

    monkeypatch.setattr(management.task_manager, "scan_running_tasks", lambda restore_timeouts=False: calls.append("scan"))
    monkeypatch.setattr(management, "Session", lambda _engine: DummySessionContext())
    monkeypatch.setattr(management, "_collect_builtin_jobs", lambda _session: (_ for _ in ()).throw(RuntimeError("jobs failed")))
    monkeypatch.setattr(management, "_collect_builtin_services", lambda: calls.append("services"))

    result = management.warm_runtime_status_caches_on_startup()

    assert calls == ["scan", "session_enter", "session_exit", "services"]
    assert result == {
        "scan_running_tasks": {"status": "ok"},
        "builtin_jobs": {"status": "error", "error": "jobs failed"},
        "builtin_services": {"status": "ok"},
    }


def test_collect_builtin_jobs_returns_stale_payload_while_refreshing(monkeypatch):
    calls: list[str] = []
    refresh_threads = []

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            refresh_threads.append(self)

    def fake_build(_session):
        calls.append("build")
        return {
            "items": [
                {
                    "key": "job-a",
                    "title": "Job A",
                }
            ],
            "queue": None,
            "runner_running": True,
            "next_wake_at": None,
            "runner_error": None,
        }

    monkeypatch.setattr(management.time, "monotonic", lambda: 131.0)
    monkeypatch.setattr(
        management,
        "_builtin_jobs_status_cache",
        (
            100.0,
            {
                "items": [{"key": "job-a", "title": "Cached Job"}],
                "queue": None,
                "runner_running": True,
                "next_wake_at": None,
                "runner_error": None,
            },
        ),
    )
    monkeypatch.setattr(management, "_builtin_jobs_status_refreshing", False)
    monkeypatch.setattr(management, "_build_builtin_jobs_status", fake_build)
    monkeypatch.setattr(management.threading, "Thread", DeferredThread)

    first = management._collect_builtin_jobs(object())
    second = management._collect_builtin_jobs(object())

    assert first["items"][0]["title"] == "Cached Job"
    assert second["items"][0]["title"] == "Cached Job"
    assert calls == []
    assert len(refresh_threads) == 1

    refresh_threads[0].target(*refresh_threads[0].args)

    assert calls == ["build"]
    assert management._builtin_jobs_status_cache[1]["items"][0]["title"] == "Job A"


def test_collect_builtin_services_returns_stale_payload_while_refreshing(monkeypatch):
    calls: list[str] = []
    refresh_threads = []
    enabled_signature = (False, False, False)

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            refresh_threads.append(self)

    def fake_build(signature):
        calls.append("build")
        assert signature == enabled_signature
        return {"items": [{"key": "service-a", "title": "Fresh Service"}]}

    monkeypatch.setattr(management.time, "monotonic", lambda: 131.0)
    monkeypatch.setattr(management, "is_attendance_behavior_tree_service_enabled", lambda: False)
    monkeypatch.setattr(management, "_fanxiu_behavior_tree_service_enabled", lambda: False)
    monkeypatch.setattr(management, "_fanxiu_game_window_service_enabled", lambda: False)
    monkeypatch.setattr(
        management,
        "_builtin_services_status_cache",
        (
            100.0,
            enabled_signature,
            {"items": [{"key": "service-a", "title": "Cached Service"}]},
        ),
    )
    monkeypatch.setattr(management, "_builtin_services_status_refreshing", False)
    monkeypatch.setattr(management, "_build_builtin_services_status", fake_build)
    monkeypatch.setattr(management.threading, "Thread", DeferredThread)

    first = management._collect_builtin_services()
    second = management._collect_builtin_services()

    assert first["items"][0]["title"] == "Cached Service"
    assert second["items"][0]["title"] == "Cached Service"
    assert calls == []
    assert len(refresh_threads) == 1

    refresh_threads[0].target(*refresh_threads[0].args)

    assert calls == ["build"]
    assert management._builtin_services_status_cache[2]["items"][0]["title"] == "Fresh Service"


def test_runtime_status_cache_ttls_cover_ten_poll_intervals():
    poll_window_seconds = 10 * 3.0

    assert management._BUILTIN_JOBS_STATUS_CACHE_TTL_SECONDS >= poll_window_seconds
    assert management._BUILTIN_SERVICES_STATUS_CACHE_TTL_SECONDS >= poll_window_seconds


def test_build_runtime_status_compacts_builtin_list_payload(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[Task.__table__])
    local_device_id = "device-local"

    with Session(engine) as session:
        session.add(
            Task(
                id="service-1",
                name="Service 1",
                command="python service.py",
                device_id=local_device_id,
                runtime_kind="service",
                order=0,
            )
        )
        session.commit()

        class FakeDevice:
            def get_task_status(self, task_id: str) -> TaskStatus:
                return TaskStatus(id=task_id, running=False)

            def to_dict(self):
                return {"id": local_device_id, "name": "Local Device", "type": "FakeDevice"}

        monkeypatch.setattr(management, "engine", engine)
        monkeypatch.setattr(management, "get_device_id", lambda: local_device_id)
        monkeypatch.setattr(management.task_manager, "scan_running_tasks", lambda restore_timeouts=False: None)
        monkeypatch.setattr(management.device_manager, "get_device", lambda device_id: FakeDevice() if device_id == local_device_id else None)
        monkeypatch.setattr(
            management,
            "_collect_builtin_jobs",
            lambda _session: {
                "items": [
                    {
                        "id": "builtin:job-a",
                        "key": "job-a",
                        "kind": "job",
                        "source": "builtin",
                        "group_id": "job:默认",
                        "group_title": "默认",
                        "title": "Job A",
                        "description": "",
                        "command": "",
                        "cwd": "",
                        "schedule": "",
                        "schedule_policy": None,
                        "schedule_label": "",
                        "next_run_at": "2026-07-08T00:00:00",
                        "timeout": None,
                        "order": 0,
                        "enabled": True,
                        "active": False,
                        "status": {
                            "running": False,
                            "enabled": True,
                            "runner_running": False,
                            "next_run_at": "2026-07-08T00:00:00",
                            "latest_run": {"huge": "payload"},
                            "retry_policy": "retry",
                            "trigger_warning": "warning",
                        },
                        "actions": ["trigger"],
                        "raw": {"latest_run": {"huge": "payload"}},
                        "policy": {"duplicated": True},
                    }
                ],
                "queue": None,
                "runner_running": False,
                "next_wake_at": None,
                "runner_error": None,
            },
        )
        monkeypatch.setattr(
            management,
            "_collect_builtin_services",
            lambda: {
                "items": [
                    {
                        "id": "builtin:service-a",
                        "key": "service-a",
                        "kind": "service",
                        "source": "builtin",
                        "group_id": "service:default",
                        "group_title": "默认服务",
                        "title": "Service A",
                        "description": "",
                        "command": "",
                        "cwd": "",
                        "schedule": "",
                        "schedule_policy": None,
                        "schedule_label": "",
                        "next_run_at": None,
                        "timeout": None,
                        "order": 0,
                        "enabled": True,
                        "active": True,
                        "status": {"running": True},
                        "actions": ["stop"],
                        "raw": {"details": "keep out of list"},
                        "policy": {"duplicated": True},
                    }
                ]
            },
        )

        payload = management.build_runtime_status(session, local_device_id)

    command_item = next(item for item in payload["items"] if item["source"] == "command")
    builtin_job = next(item for item in payload["items"] if item["key"] == "job-a")
    builtin_service = next(item for item in payload["items"] if item["key"] == "service-a")

    assert command_item["raw"]["name"] == "Service 1"
    assert "policy" not in command_item
    assert builtin_job["raw"] == {}
    assert builtin_job["status"] == {
        "running": False,
        "enabled": True,
        "runner_running": False,
        "next_run_at": "2026-07-08T00:00:00",
    }
    assert "policy" not in builtin_job
    assert builtin_service["raw"] == {}
    assert "policy" not in builtin_service


def test_serialize_codeyun_watchdog_service_item_uses_lightweight_process_status(monkeypatch):
    calls: list[dict[str, bool]] = []

    def fake_status(*, full_scan=False, include_startup=True, include_process_details=True):
        calls.append({
            "full_scan": full_scan,
            "include_startup": include_startup,
            "include_process_details": include_process_details,
        })
        return {
            "key": "codeyun-watchdog",
            "title": "CodeYun 本机守护",
            "running": True,
            "state": "running",
            "state_label": "运行中",
            "interval_seconds": 60,
            "backend_url": "http://127.0.0.1:8000/api/health",
            "frontend_url": "http://127.0.0.1:5173/",
            "script_path": "scripts/codeyun_watchdog.py",
            "cwd": "",
            "log_path": "",
            "process_count": 1,
            "pids": [123],
            "startup": {"enabled": True},
        }

    monkeypatch.setattr(management, "get_codeyun_watchdog_status", fake_status)

    item = management._serialize_codeyun_watchdog_service_item()

    assert calls == [{
        "full_scan": False,
        "include_startup": True,
        "include_process_details": False,
    }]
    assert item["title"] == "CodeYun 本机守护"
    assert item["status"]["pids"] == [123]
