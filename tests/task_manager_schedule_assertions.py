def assert_schedule_next_run_synced(task, expected_next_run_at: str | None = None) -> None:
    assert task.next_run_at is not None
    if expected_next_run_at is not None:
        assert task.next_run_at == expected_next_run_at
    assert task.schedule_state["next_trigger_at"] == task.next_run_at
