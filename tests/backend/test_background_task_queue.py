import threading
import time

from backend.core.runtime.background_task_queue import background_task_queue


def test_background_task_queue_runs_tasks_serially():
    background_task_queue.reset_for_tests()
    events: list[str] = []

    def task(name: str, delay: float = 0.0):
        events.append(f"start:{name}")
        if delay:
            time.sleep(delay)
        events.append(f"end:{name}")

    first_id = background_task_queue.enqueue("first", task, "first", 0.05)
    second_id = background_task_queue.enqueue("second", task, "second", 0.0)

    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["is_idle"] and len(snapshot["recent"]) >= 2:
            break
        time.sleep(0.02)

    snapshot = background_task_queue.snapshot()
    assert snapshot["is_idle"] is True
    assert events == ["start:first", "end:first", "start:second", "end:second"]
    recent_ids = {item["id"] for item in snapshot["recent"]}
    assert first_id in recent_ids
    assert second_id in recent_ids

    background_task_queue.reset_for_tests()


def test_background_task_queue_can_delete_pending_and_recent_tasks():
    background_task_queue.reset_for_tests()
    events: list[str] = []
    release_first = threading.Event()

    def blocking_task():
        events.append("start:first")
        release_first.wait(timeout=3)
        events.append("end:first")

    def second_task():
        events.append("second")

    first_id = background_task_queue.enqueue("first", blocking_task)
    second_id = background_task_queue.enqueue("second", second_task)

    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["running"] and snapshot["running"]["id"] == first_id and snapshot["pending"]:
            break
        time.sleep(0.02)

    assert background_task_queue.delete(second_id) == "deleted"
    assert second_id not in {item["id"] for item in background_task_queue.snapshot()["pending"]}

    release_first.set()
    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["is_idle"] and snapshot["recent"]:
            break
        time.sleep(0.02)

    snapshot = background_task_queue.snapshot()
    assert events == ["start:first", "end:first"]
    recent_ids = {item["id"] for item in snapshot["recent"]}
    assert first_id in recent_ids
    assert second_id not in recent_ids

    assert background_task_queue.delete(first_id) == "deleted"
    assert first_id not in {item["id"] for item in background_task_queue.snapshot()["recent"]}

    background_task_queue.reset_for_tests()


def test_background_task_queue_can_delete_pending_tasks_by_name():
    background_task_queue.reset_for_tests()
    release_first = threading.Event()

    def blocking_task():
        release_first.wait(timeout=3)

    first_id = background_task_queue.enqueue("first", blocking_task)
    delete_id = background_task_queue.enqueue("delete-me", lambda: None)
    keep_id = background_task_queue.enqueue("keep-me", lambda: None)

    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["running"] and snapshot["running"]["id"] == first_id and len(snapshot["pending"]) >= 2:
            break
        time.sleep(0.02)

    assert background_task_queue.delete_pending_by_name("delete-me") == 1
    pending_ids = {item["id"] for item in background_task_queue.snapshot()["pending"]}
    assert delete_id not in pending_ids
    assert keep_id in pending_ids

    release_first.set()
    deadline = time.time() + 3
    while time.time() < deadline:
        if background_task_queue.snapshot()["is_idle"]:
            break
        time.sleep(0.02)
    background_task_queue.reset_for_tests()


def test_background_task_queue_enqueue_once_reuses_active_task():
    background_task_queue.reset_for_tests()
    release_first = threading.Event()

    def blocking_task():
        release_first.wait(timeout=3)

    first_id, first_queued = background_task_queue.enqueue_once("same-name", blocking_task)

    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["running"] and snapshot["running"]["id"] == first_id:
            break
        time.sleep(0.02)

    second_id, second_queued = background_task_queue.enqueue_once("same-name", lambda: None)

    assert first_queued is True
    assert second_queued is False
    assert second_id == first_id

    release_first.set()
    deadline = time.time() + 3
    while time.time() < deadline:
        if background_task_queue.snapshot()["is_idle"]:
            break
        time.sleep(0.02)
    background_task_queue.reset_for_tests()


def test_background_task_queue_exposes_resource_lock():
    background_task_queue.reset_for_tests()

    task_id = background_task_queue.enqueue(
        "locked-task",
        lambda: "ok",
        resource_lock="resource:gui-automation",
    )

    deadline = time.time() + 3
    while time.time() < deadline:
        snapshot = background_task_queue.snapshot()
        if snapshot["is_idle"] and snapshot["recent"]:
            break
        time.sleep(0.02)

    recent = background_task_queue.snapshot()["recent"][0]
    assert recent["id"] == task_id
    assert recent["resource_lock"] == "resource:gui-automation"
    assert recent["metadata"]["resource_lock"] == "resource:gui-automation"

    background_task_queue.reset_for_tests()
