from sqlmodel import Session, SQLModel, create_engine

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
