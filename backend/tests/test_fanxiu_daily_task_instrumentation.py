from backend.core.fanxiu.instrumentation.daily_task import build_daily_task_snapshot


def test_daily_task_snapshot_marks_claimed_progress_done():
    snapshot = build_daily_task_snapshot(
        task_id=1008,
        task_entries=[{"taskId": 1008, "status": 5, "turn": 3, "targetTurn": 3}],
        finished_task_ids=[],
    )

    assert snapshot["done"] is True
    assert snapshot["status"] == 5
    assert snapshot["turn"] == snapshot["target_turn"] == 3


def test_daily_task_snapshot_does_not_guess_missing_or_partial_task_done():
    partial = build_daily_task_snapshot(
        task_id=1008,
        task_entries=[{"taskId": 1008, "status": 3, "turn": 2, "targetTurn": 3}],
        finished_task_ids=[],
    )
    missing = build_daily_task_snapshot(task_id=1008, task_entries=[], finished_task_ids=[])

    assert partial["done"] is False
    assert missing["done"] is False
