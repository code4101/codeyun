import json

from backend.core.background_task_runner import BACKGROUND_TASK_SPECS, BackgroundTaskRunner


def _runner_for_test(tmp_path):
    runner = BackgroundTaskRunner()
    runner.state_path = tmp_path / "background_tasks.state.json"
    runner.log_path = tmp_path / "background_tasks.log"
    runner.lock_path = tmp_path / "background_tasks.lock"
    return runner


def _set_enabled(monkeypatch, enabled_by_key):
    monkeypatch.setattr(
        "backend.core.background_task_runner._is_task_enabled",
        lambda task_key: bool(enabled_by_key.get(task_key, False)),
    )


def _write_schedule_state(path, values, *, schedule_version=3):
    blackboard = {} if schedule_version is None else {"schedule_version": schedule_version}
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    f"Root/MemorySelector/{task_key}": {"next_run_at": next_run_at}
                    for task_key, next_run_at in values.items()
                },
                "blackboard": blackboard,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_background_task_runner_next_wake_ignores_disabled_tasks(tmp_path, monkeypatch):
    _set_enabled(
        monkeypatch,
        {
            "attendance_summary_monthly_templates": True,
            "storage_analysis": True,
        },
    )
    runner = _runner_for_test(tmp_path)
    _write_schedule_state(
        runner.state_path,
        {
            "note_metadata_feedback_optimization": "2099-05-09 10:41:49",
            "attendance_summary_monthly_templates": "2099-05-10 00:05:00",
            "storage_analysis": "2099-05-10 03:00:00",
        },
    )

    snapshot = runner.snapshot()

    assert snapshot["next_wake_at"] == "2099-05-10T00:05:00"
    tasks = snapshot["tasks"]
    assert tasks["note_metadata_feedback_optimization"]["enabled"] is False
    assert tasks["note_metadata_feedback_optimization"]["next_run_at"] == "2099-05-09T10:41:49"
    assert tasks["attendance_summary_monthly_templates"]["enabled"] is True


def test_background_task_runner_refresh_updates_existing_tree(tmp_path, monkeypatch):
    task_keys = {spec.key for spec in BACKGROUND_TASK_SPECS}
    enabled_by_key = {key: False for key in task_keys}
    enabled_by_key["note_metadata_feedback_optimization"] = True
    _set_enabled(monkeypatch, enabled_by_key)

    runner = _runner_for_test(tmp_path)
    _write_schedule_state(
        runner.state_path,
        {
            "note_metadata_feedback_optimization": "2099-05-09 10:41:49",
            "attendance_summary_monthly_templates": "2099-05-10 00:05:00",
        },
    )
    tree_runner = runner.build_runner()
    runner._runner = tree_runner
    assert tree_runner.next_wake().isoformat() == "2099-05-09T10:41:49"

    enabled_by_key["note_metadata_feedback_optimization"] = False
    enabled_by_key["attendance_summary_monthly_templates"] = True
    runner.refresh_enabled_states()

    assert tree_runner.next_wake().isoformat() == "2099-05-10T00:05:00"


def test_background_task_runner_resets_versioned_schedule_state(tmp_path, monkeypatch):
    task_keys = {spec.key for spec in BACKGROUND_TASK_SPECS}
    _set_enabled(monkeypatch, {key: False for key in task_keys})

    runner = _runner_for_test(tmp_path)
    _write_schedule_state(
        runner.state_path,
        {
            "auto_git_commit": "2099-05-10 03:20:00",
            "storage_analysis": "2099-05-10 03:00:00",
            "rime_config_sync": "2099-05-10 01:00:00",
        },
        schedule_version=None,
    )

    tree_runner = runner.build_runner()
    nodes = tree_runner.state["nodes"]

    assert nodes["Root/MemorySelector/auto_git_commit"].get("next_run_at") is None
    assert nodes["Root/MemorySelector/storage_analysis"].get("next_run_at") is None
    assert nodes["Root/MemorySelector/rime_config_sync"]["next_run_at"] == "2099-05-10 01:00:00"
    assert tree_runner.state["blackboard"]["schedule_version"] == 3
