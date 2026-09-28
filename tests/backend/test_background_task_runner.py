import json
import datetime
import pytest
from types import SimpleNamespace

from sqlmodel import SQLModel, Session, create_engine

from backend.core import fanxiu_wechat_reminder
from backend.core.jobs import scheduler as background_tasks
from backend.core.jobs.scheduler import BACKGROUND_TASK_SPECS, BackgroundTaskRunner, BackgroundTaskSpec
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
        "backend.core.jobs.scheduler._is_task_enabled",
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

    assert background_tasks._is_task_enabled(background_tasks.MARKET_QUOTE_REFRESH_TASK_KEY) is False
    assert background_tasks._is_task_enabled(background_tasks.RUANYF_WEEKLY_TASK_NAME) is False
    assert background_tasks._is_task_enabled(background_tasks.NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY) is False

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


def test_background_task_runner_note_sheet_snapshot_backfill_is_optional():
    spec = background_tasks.get_background_task_spec(
        background_tasks.NOTE_SHEET_PAGE_SNAPSHOT_BACKFILL_TASK_KEY
    )

    assert spec is not None
    assert spec.title == "星云表格快照补齐"
    assert spec.category == "表格"
    assert spec.schedule_label == "未配置自动触发"
    assert spec.default_visible is False
    assert background_tasks._default_background_task_schedule_policy(spec.key) is None


def test_background_task_runner_public_frontend_deploy_keeps_interval_schedule():
    spec = background_tasks.get_background_task_spec(
        background_tasks.PUBLIC_FRONTEND_DEPLOY_TASK_KEY
    )

    assert spec is not None
    assert spec.title == "公网前端发布"
    assert spec.category == "部署"
    assert spec.default_visible is True
    policy = background_tasks._default_background_task_schedule_policy(spec.key)
    assert policy is not None
    assert policy["trigger"] == {
        "type": "interval",
        "minutes": 30,
        "anchor": "last_finish",
    }
    assert policy["outcome"]["on_failure"] == {
        "type": "retry_after",
        "minutes": 10,
    }


def test_background_task_runner_fanxiu_wechat_reminders_are_registered_optional_jobs():
    boss_spec = background_tasks.get_background_task_spec(
        background_tasks.FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY
    )
    shengzu_spec = background_tasks.get_background_task_spec(
        background_tasks.FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY
    )

    assert boss_spec is not None
    assert boss_spec.title == "凡修魔狱封阵微信群提醒"
    assert boss_spec.category == "凡修"
    assert "微信 iLink" in boss_spec.description
    assert "xlproject" not in boss_spec.description
    assert "xlproject" not in boss_spec.manual_warning
    assert boss_spec.schedule_label == "每天 17:57"
    assert boss_spec.default_visible is False
    boss_policy = background_tasks._default_background_task_schedule_policy(boss_spec.key)
    assert boss_policy is not None
    assert boss_policy["trigger"] == {"type": "daily", "time": "17:57"}

    assert shengzu_spec is not None
    assert shengzu_spec.title == "凡修圣祖微信群提醒"
    assert shengzu_spec.category == "凡修"
    assert "考勤微信账号 code4102" in shengzu_spec.description
    assert "xlproject" not in shengzu_spec.description
    assert "xlproject" not in shengzu_spec.manual_warning
    assert shengzu_spec.schedule_label == "每周日 19:57"
    assert shengzu_spec.default_visible is False
    shengzu_policy = background_tasks._default_background_task_schedule_policy(shengzu_spec.key)
    assert shengzu_policy is not None
    assert shengzu_policy["trigger"] == {"type": "weekly", "weekdays": [7], "time": "19:57"}


def test_fanxiu_wechat_reminder_worker_sends_via_wechat_ilink(monkeypatch):
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    sent_messages = []

    def fake_send_text_message(account_id, *, to_user_id, text, context_token=None, timeout_seconds=120):
        sent_messages.append(
            {
                "account_id": account_id,
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {"message_id": "msg-1", "to_user_id": to_user_id, "used_context_token": bool(context_token)}

    monkeypatch.setattr(fanxiu_wechat_reminder, "send_text_message", fake_send_text_message)

    result = fanxiu_wechat_reminder.run_fanxiu_wechat_reminder_worker(
        fanxiu_wechat_reminder.FANXIU_WECHAT_BOSS_REMINDER_TASK_KEY,
        db_bind=engine,
        account_id="bot@example",
        to_user_id="group-1",
        context_token="ctx-1",
        timeout_seconds=3,
        require_allowed_host=False,
    )

    assert result["status"] == "completed"
    assert result["target"] == "wechat_ilink:send_text_message"
    assert result["sent"]["message_id"] == "msg-1"
    assert sent_messages == [
        {
            "account_id": "bot@example",
            "to_user_id": "group-1",
            "text": "@所有人 准备打魔狱封阵",
            "context_token": "ctx-1",
            "timeout_seconds": 3,
        }
    ]
    assert "xlproject_root" not in result
    assert "python_executable" not in result


def test_shengzu_reminder_uses_attendance_account_without_ilink_fallback(monkeypatch):
    from pyxllib.autogui import weixin4_instrumentation as wechat

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    calls = []

    def send(recipient, text, *, sender_account_id):
        calls.append((recipient, text, sender_account_id))
        return {"status": "sent"}

    monkeypatch.setattr(wechat, "send_text", send)
    monkeypatch.setattr(fanxiu_wechat_reminder, "send_text_message", lambda *a, **kw: pytest.fail("不得回退 iLink"))
    result = fanxiu_wechat_reminder.run_fanxiu_wechat_reminder_worker(
        fanxiu_wechat_reminder.FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
        db_bind=engine, require_allowed_host=False,
    )
    assert result["status"] == "completed"
    assert calls == [("53176639124@chatroom", "打圣祖", "wxid_gxgjjgft1oj722")]

    def unavailable(*args, **kwargs):
        raise wechat.WeixinInstrumentationUnavailable("指定账号不可用")

    monkeypatch.setattr(wechat, "send_text", unavailable)
    with pytest.raises(wechat.WeixinInstrumentationUnavailable):
        fanxiu_wechat_reminder.run_fanxiu_wechat_reminder_worker(
            fanxiu_wechat_reminder.FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY,
            db_bind=engine, require_allowed_host=False,
        )
    with Session(engine) as session:
        status = fanxiu_wechat_reminder.get_fanxiu_wechat_reminder_status(
            fanxiu_wechat_reminder.FANXIU_WECHAT_SHENGZU_REMINDER_TASK_KEY, session,
        )["latest_run"]
    assert status["status"] == "failed"
    assert status["stage"] == "send_failed"


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
            "codex_diary_yesterday_import": "2099-05-09 10:41:49",
            "attendance_summary_monthly_templates": "2099-05-10 00:05:00",
            "storage_analysis": "2099-05-10 03:00:00",
        },
    )

    snapshot = runner.snapshot()

    assert snapshot["next_wake_at"] == "2099-05-10T00:05:00"
    tasks = snapshot["tasks"]
    assert tasks["codex_diary_yesterday_import"]["enabled"] is False
    assert tasks["codex_diary_yesterday_import"]["next_run_at"] is None
    assert tasks["attendance_summary_monthly_templates"]["enabled"] is True


def test_background_task_runner_refresh_updates_existing_tree(tmp_path, monkeypatch):
    task_keys = {spec.key for spec in BACKGROUND_TASK_SPECS}
    enabled_by_key = {key: False for key in task_keys}
    enabled_by_key["codex_diary_yesterday_import"] = True
    _set_enabled(monkeypatch, enabled_by_key)

    runner = _runner_for_test(tmp_path)
    _write_schedule_state(
        runner.state_path,
        {
            "codex_diary_yesterday_import": "2099-05-09 10:41:49",
            "attendance_summary_monthly_templates": "2099-05-10 00:05:00",
        },
    )
    tree_runner = runner.build_runner()
    runner._runner = tree_runner
    assert tree_runner.next_wake().isoformat() == "2099-05-09T10:41:49"

    enabled_by_key["codex_diary_yesterday_import"] = False
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


def test_background_task_runner_commits_next_run_only_after_queue_job_succeeds(tmp_path, monkeypatch):
    spec = BackgroundTaskSpec(
        key="rime_config_sync",
        title="小狼毫自动同步",
        category="输入法",
        description="",
        schedule_label="每小时检查",
        retry_label="失败后 10 分钟重试",
        action=lambda: "queue-1",
    )
    monkeypatch.setattr("backend.core.jobs.scheduler.BACKGROUND_TASK_SPECS", (spec,))
    _set_enabled(monkeypatch, {"rime_config_sync": True})
    monkeypatch.setattr(
        "backend.core.jobs.scheduler._effective_background_task_schedule_policy",
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

    monkeypatch.setattr("backend.core.jobs.scheduler.background_task_queue.snapshot", queue_snapshot)

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
    assert tree_runner.state["nodes"]["Root/MemorySelector/rime_config_sync"]["next_run_at"] == "2026-05-19 16:50:00"

    queue_status["value"] = "completed"
    clock.value += datetime.timedelta(minutes=2)
    tree_runner = BehaviorTreeRunner(
        runner.build_tree(),
        runner.state_path,
        now_func=clock,
    )
    runner._ensure_schedule_state_version(tree_runner)
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
    monkeypatch.setattr("backend.core.jobs.scheduler.BACKGROUND_TASK_SPECS", (spec,))
    _set_enabled(monkeypatch, {"ruanyf_weekly_note": True})
    monkeypatch.setattr(
        "backend.core.jobs.scheduler._effective_background_task_schedule_policy",
        lambda task_key, enabled=None: {
            "enabled": True,
            "trigger": {"type": "weekly", "weekdays": [5], "time": "06:00"},
            "action": {"type": "enqueue"},
        },
    )
    monkeypatch.setattr(
        "backend.core.jobs.scheduler.background_task_queue.snapshot",
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


def test_failed_archive_waits_ten_minutes_across_runner_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(BehaviorTreeRunner, "sleep", lambda self, seconds: None)
    calls = []
    spec = BackgroundTaskSpec(key="tibo_x_archive", title="Tibo", category="library",
        description="", schedule_label="hourly", retry_label="10m",
        action=lambda: calls.append("run") or "queue-1")
    monkeypatch.setattr(background_tasks, "BACKGROUND_TASK_SPECS", (spec,))
    _set_enabled(monkeypatch, {spec.key: True})
    monkeypatch.setattr(background_tasks, "_effective_background_task_schedule_policy",
        lambda *a, **k: {"enabled": True, "trigger": {"type": "interval", "minutes": 60},
                        "outcome": {"on_failure": {"type": "retry_after", "minutes": 10}}})
    monkeypatch.setattr(background_tasks.background_task_queue, "snapshot",
        lambda: {"recent": [{"id": "queue-1", "status": "failed", "error_message": "HTTP 451"}]})
    runner = _runner_for_test(tmp_path)
    _write_schedule_state(runner.state_path, {spec.key: "2026-09-16 10:00:00"})
    clock = FakeClock("2026-09-16 10:00:00")
    tree = BehaviorTreeRunner(runner.build_tree(), runner.state_path, now_func=clock)
    assert tree.run_once() == Status.SUCCESS
    assert tree.state["nodes"]["Root/MemorySelector/tibo_x_archive"]["next_run_at"] == "2026-09-16 10:10:00"
    clock.value += datetime.timedelta(minutes=9)
    tree = BehaviorTreeRunner(runner.build_tree(), runner.state_path, now_func=clock)
    tree.run_once()
    assert calls == ["run"]
    clock.value += datetime.timedelta(minutes=1)
    tree.run_once()
    tree.run_once()  # Complete the idle branch, then re-evaluate due tasks.
    assert calls == ["run", "run"]


def test_background_task_runner_escalates_after_three_consecutive_failures_and_recovers(
    tmp_path,
    monkeypatch,
):
    should_fail = {"value": True}
    action_calls = []
    dispatched_requests = []
    agent_status = {"value": "running"}

    def action():
        action_calls.append("run")
        if should_fail["value"]:
            raise RuntimeError("usage page is blank")
        return {"ok": True}

    spec = BackgroundTaskSpec(
        key="quota_probe",
        title="余额查询",
        category="AI",
        description="",
        schedule_label="每天 00:00",
        retry_label="失败后 10 分钟重试",
        action=action,
    )
    monkeypatch.setattr("backend.core.jobs.scheduler.BACKGROUND_TASK_SPECS", (spec,))
    _set_enabled(monkeypatch, {"quota_probe": True})
    monkeypatch.setattr(
        "backend.core.jobs.scheduler._effective_background_task_schedule_policy",
        lambda task_key, enabled=None: {
            "enabled": True,
            "trigger": {"type": "daily", "time": "00:00"},
            "action": {"type": "enqueue"},
            "outcome": {"on_failure": {"type": "retry_after", "minutes": 10}},
            "escalation": {"type": "codex", "after_consecutive_failures": 3},
        },
    )

    def dispatch(request):
        dispatched_requests.append(request)
        return background_tasks.CodexDispatch(
            dispatch_id="dispatch-1",
            pid=4101,
            workspace_dir=str(tmp_path),
            request_path=str(tmp_path / "request.json"),
            prompt_path=str(tmp_path / "prompt.md"),
            stdout_path=str(tmp_path / "stdout.jsonl"),
            stderr_path=str(tmp_path / "stderr.log"),
            created_at="2026-05-19T10:20:00+00:00",
        )

    def inspect(_dispatch_id):
        return SimpleNamespace(
            status=agent_status["value"],
            thread_id="thread-1",
            codex_url="codex://threads/thread-1",
            last_message="fixed" if agent_status["value"] == "completed" else None,
            error=None,
        )

    monkeypatch.setattr("backend.core.jobs.scheduler.escalate_to_codex", dispatch)
    monkeypatch.setattr("backend.core.jobs.scheduler.inspect_codex_dispatch", inspect)

    runner = _runner_for_test(tmp_path)
    _write_schedule_state(runner.state_path, {"quota_probe": "2026-05-19 10:00:00"})
    clock = FakeClock("2026-05-19 10:00:00")
    tree_runner = BehaviorTreeRunner(runner.build_tree(), runner.state_path, now_func=clock)
    runner._ensure_schedule_state_version(tree_runner)

    for expected_count in (1, 2):
        assert tree_runner.run_once() == Status.SUCCESS
        failure = tree_runner.state["blackboard"][background_tasks.BACKGROUND_TASK_FAILURE_STATE_KEY]["quota_probe"]
        assert failure["consecutive_failures"] == expected_count
        assert dispatched_requests == []
        clock.value += datetime.timedelta(minutes=10)
        tree_runner = BehaviorTreeRunner(runner.build_tree(), runner.state_path, now_func=clock)
        runner._ensure_schedule_state_version(tree_runner)

    assert tree_runner.run_once() == Status.SUCCESS
    failure = tree_runner.state["blackboard"][background_tasks.BACKGROUND_TASK_FAILURE_STATE_KEY]["quota_probe"]
    assert failure["consecutive_failures"] == 3
    assert failure["status"] == "agent_running"
    assert failure["dispatch"]["dispatch_id"] == "dispatch-1"
    assert len(dispatched_requests) == 1
    assert "连续失败 3 次" in dispatched_requests[0].problem

    clock.value += datetime.timedelta(seconds=30)
    assert tree_runner.run_once() == Status.SUCCESS
    assert action_calls == ["run", "run", "run"]

    agent_status["value"] = "completed"
    should_fail["value"] = False
    clock.value += datetime.timedelta(seconds=30)
    assert tree_runner.run_once() == Status.SUCCESS
    assert action_calls == ["run", "run", "run", "run"]
    assert "quota_probe" not in tree_runner.state["blackboard"][background_tasks.BACKGROUND_TASK_FAILURE_STATE_KEY]


def test_background_task_runner_continues_after_run_once_exception(tmp_path, monkeypatch):
    runner = _runner_for_test(tmp_path)
    calls = []
    waits = []

    class FakeRunner:
        def run_once(self):
            calls.append("run")
            if len(calls) == 1:
                raise RuntimeError("boom")
            runner._stop_event.set()
            return Status.SUCCESS

    monkeypatch.setattr(runner, "build_runner", lambda: FakeRunner())
    monkeypatch.setattr(runner._stop_event, "wait", lambda seconds: waits.append(seconds) or False)

    runner._run_thread()

    assert calls == ["run", "run"]
    assert waits == [5, 1]
    assert runner._last_error == "boom"
    assert runner._runner is None


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

    assert "Root/MemorySelector/auto_git_commit" not in nodes
    assert nodes["Root/MemorySelector/storage_analysis"].get("next_run_at") is None
    assert "Root/MemorySelector/rime_config_sync" not in nodes
    assert tree_runner.state["blackboard"]["schedule_version"] == background_tasks.BACKGROUND_TASK_SCHEDULE_STATE_VERSION
