import json
import datetime

from backend.core.runtime import background_task_runner as background_tasks
from backend.core.runtime.background_task_runner import BACKGROUND_TASK_SPECS, BackgroundTaskRunner, BackgroundTaskSpec
from backend.models import AppSetting
from pyxllib.prog.behavior_tree import BehaviorTreeRunner, Status


class FakeClock:
    def __init__(self, value: str):
        self.value = datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

    def __call__(self):
        return self.value


def _runner_for_test(tmp_path):
    runner = BackgroundTaskRunner()
    runner.state_path = tmp_path / "background_tasks.state.json"
    runner.log_path = tmp_path / "background_tasks.log"
    runner.lock_path = tmp_path / "background_tasks.lock"
    return runner


def _set_enabled(monkeypatch, enabled_by_key):
    monkeypatch.setattr(
        "backend.core.runtime.background_task_runner._is_task_enabled",
        lambda task_key: bool(enabled_by_key.get(task_key, False)),
    )


def _write_schedule_state(path, values, *, schedule_version=background_tasks.BACKGROUND_TASK_SCHEDULE_STATE_VERSION):
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


def test_background_task_runner_builtin_presets_are_disabled_until_user_enables(engine, session, monkeypatch):
    monkeypatch.setattr("backend.db.engine", engine)
    monkeypatch.setattr(background_tasks, "is_fanxiu_slimming_allowed_host", lambda: True)

    assert background_tasks._is_task_enabled(background_tasks.MEDIA_SYNC_HOME_DISCOVERY_TASK_KEY) is False
    assert background_tasks._is_task_enabled(background_tasks.MARKET_QUOTE_REFRESH_TASK_KEY) is False
    assert background_tasks._is_task_enabled(background_tasks.FANXIU_SLIMMING_TASK_KEY) is False
    assert background_tasks._is_task_enabled(background_tasks.RUANYF_WEEKLY_TASK_NAME) is False

    session.add(
        AppSetting(
            key=f"background_task.{background_tasks.MARKET_QUOTE_REFRESH_TASK_KEY}.enabled",
            value={"enabled": True},
        )
    )
    session.add(
        AppSetting(
            key="storage.schedule",
            value={"schedule_enabled": True, "cron_expression": "35 0 * * *"},
        )
    )
    session.commit()

    assert background_tasks._is_task_enabled(background_tasks.MARKET_QUOTE_REFRESH_TASK_KEY) is True
    assert background_tasks._is_task_enabled("storage_analysis") is True


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
    assert tasks["note_metadata_feedback_optimization"]["next_run_at"] is None
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


def test_background_task_runner_attendance_summary_uses_monthly_schedule(tmp_path, monkeypatch):
    task_keys = {spec.key for spec in BACKGROUND_TASK_SPECS}
    enabled_by_key = {key: False for key in task_keys}
    enabled_by_key["attendance_summary_monthly_templates"] = True
    _set_enabled(monkeypatch, enabled_by_key)

    runner = _runner_for_test(tmp_path)
    tree_runner = BehaviorTreeRunner(
        runner.build_tree(),
        runner.state_path,
        now_func=FakeClock("2026-05-19 12:00:00"),
    )
    runner._ensure_schedule_state_version(tree_runner)

    assert tree_runner.next_wake().isoformat() == "2026-05-27T00:00:00"


def test_background_task_runner_persists_next_run_when_queue_job_is_running(tmp_path, monkeypatch):
    spec = BackgroundTaskSpec(
        key="rime_config_sync",
        title="小狼毫自动同步",
        category="输入法",
        description="",
        schedule_label="每小时检查",
        retry_label="失败后 10 分钟重试",
        action=lambda: "queue-1",
    )
    monkeypatch.setattr("backend.core.runtime.background_task_runner.BACKGROUND_TASK_SPECS", (spec,))
    _set_enabled(monkeypatch, {"rime_config_sync": True})
    monkeypatch.setattr(
        "backend.core.runtime.background_task_runner._effective_background_task_schedule_policy",
        lambda task_key, enabled=None: {
            "enabled": True,
            "trigger": {"type": "interval", "minutes": 60, "anchor": "last_finish"},
            "action": {"type": "enqueue"},
        },
    )
    queue_status = {"value": "running"}

    def queue_snapshot():
        if queue_status["value"] == "running":
            return {
                "running": {"id": "queue-1", "status": "running"},
                "pending": [],
                "recent": [],
            }
        return {
            "running": None,
            "pending": [],
            "recent": [{"id": "queue-1", "status": "completed"}],
        }

    monkeypatch.setattr("backend.core.runtime.background_task_runner.background_task_queue.snapshot", queue_snapshot)

    runner = _runner_for_test(tmp_path)
    _write_schedule_state(
        runner.state_path,
        {"rime_config_sync": "2026-05-19 16:50:00"},
    )
    clock = FakeClock("2026-05-19 16:50:00")
    tree_runner = BehaviorTreeRunner(
        runner.build_tree(),
        runner.state_path,
        now_func=clock,
    )
    runner._ensure_schedule_state_version(tree_runner)

    assert tree_runner.run_once() == Status.RUNNING
    assert tree_runner.state["nodes"]["Root/MemorySelector/rime_config_sync"]["next_run_at"] == "2026-05-19 17:50:00"

    queue_status["value"] = "completed"
    clock.value += datetime.timedelta(minutes=2)
    assert tree_runner.run_once() == Status.SUCCESS
    assert tree_runner.state["nodes"]["Root/MemorySelector/rime_config_sync"]["next_run_at"] == "2026-05-19 17:52:00"


def test_background_task_runner_uses_queue_result_next_run_at(tmp_path, monkeypatch):
    spec = BackgroundTaskSpec(
        key="ruanyf_weekly_note",
        title="阮一峰周刊笔记",
        category="笔记",
        description="",
        schedule_label="每周五 06:00",
        retry_label="失败后 10 分钟重试",
        action=lambda: "queue-1",
    )
    monkeypatch.setattr("backend.core.runtime.background_task_runner.BACKGROUND_TASK_SPECS", (spec,))
    _set_enabled(monkeypatch, {"ruanyf_weekly_note": True})
    monkeypatch.setattr(
        "backend.core.runtime.background_task_runner._effective_background_task_schedule_policy",
        lambda task_key, enabled=None: {
            "enabled": True,
            "trigger": {"type": "weekly", "weekdays": [5], "time": "06:00"},
            "action": {"type": "enqueue"},
        },
    )
    monkeypatch.setattr(
        "backend.core.runtime.background_task_runner.background_task_queue.snapshot",
        lambda: {
            "running": None,
            "pending": [],
            "recent": [
                {
                    "id": "queue-1",
                    "status": "completed",
                    "result": {"next_run_at": "2026-05-22T08:00:00"},
                }
            ],
        },
    )

    runner = _runner_for_test(tmp_path)
    _write_schedule_state(
        runner.state_path,
        {"ruanyf_weekly_note": "2026-05-22 06:00:00"},
    )
    tree_runner = BehaviorTreeRunner(
        runner.build_tree(),
        runner.state_path,
        now_func=FakeClock("2026-05-22 06:00:00"),
    )
    runner._ensure_schedule_state_version(tree_runner)

    assert tree_runner.run_once() == Status.SUCCESS
    assert tree_runner.state["nodes"]["Root/MemorySelector/ruanyf_weekly_note"]["next_run_at"] == "2026-05-22 08:00:00"


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
    assert tree_runner.state["blackboard"]["schedule_version"] == background_tasks.BACKGROUND_TASK_SCHEDULE_STATE_VERSION
