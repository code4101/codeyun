import time

from backend.core import runtime_management as runtime_core
from backend.models import Task


def _headers(test_device):
    return {"Authorization": f"Bearer {test_device['token']}"}


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
