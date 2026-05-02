import time

from backend.core.background_task_queue import background_task_queue


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
