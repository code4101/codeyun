from sqlmodel import Session, SQLModel, create_engine

from backend.api import admin as admin_module
from backend.api import task_manager as task_manager_module
from backend.core.devices.device import TaskStatus
from backend.core.runtime import management
from backend.models import Task


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
    monotonic_values = iter([100.0, 100.5, 101.2])
    manager = task_manager_module.TaskManager()

    try:
        monkeypatch.setattr(task_manager_module, "engine", engine)
        monkeypatch.setattr(task_manager_module.time, "monotonic", lambda: next(monotonic_values))
        monkeypatch.setattr(manager, "_get_local_device_id", lambda: local_device_id)
        monkeypatch.setattr(task_manager_module.device_manager, "get_device", lambda device_id: fake_device if device_id == local_device_id else None)

        manager.scan_running_tasks()
        manager.scan_running_tasks()
        manager.scan_running_tasks()
    finally:
        manager.scheduler.shutdown(wait=False)

    assert fake_device.scan_calls == [
        (False, ["service-1"]),
        (True, ["service-1"]),
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


def test_collect_builtin_jobs_uses_short_cache(monkeypatch):
    calls: list[str] = []
    monotonic_values = iter([100.0, 100.1, 106.0])

    monkeypatch.setattr(management.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(management, "_builtin_jobs_status_cache", None)

    def fake_status(_session):
        calls.append("status")
        return {
            "tasks": [
                {
                    "key": "job-a",
                    "title": "Job A",
                    "category": "默认",
                    "enabled": True,
                    "active": False,
                    "runner_running": True,
                }
            ],
            "queue": None,
            "runner_running": True,
            "next_wake_at": None,
            "runner_error": None,
        }

    monkeypatch.setattr(admin_module, "get_background_task_status", fake_status)

    first = management._collect_builtin_jobs(object())
    first["items"][0]["title"] = "mutated"
    second = management._collect_builtin_jobs(object())
    third = management._collect_builtin_jobs(object())

    assert calls == ["status", "status"]
    assert second["items"][0]["title"] == "Job A"
    assert third["items"][0]["title"] == "Job A"
