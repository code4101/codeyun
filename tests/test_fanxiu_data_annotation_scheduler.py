import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.api import fanxiu
from backend.core.fanxiu.runtime import behavior_tree as fanxiu_behavior_tree
from backend.core.fanxiu.data_annotation import default_jobs as data_annotation_default_jobs
from backend.core.fanxiu.data_annotation import runtime_control as runtime_control
from backend.core.fanxiu.data_annotation import runtime_runner as runtime_runner_core
from backend.core.fanxiu.runtime.behavior_tree import create_fanxiu_runtime_runner, get_fanxiu_runtime_runner_class
from backend.core.fanxiu.runtime.errors import FanxiuRuntimeError


def _scheduler_state_path(tmp_path):
    return tmp_path / "fanxiu" / "data-annotation" / "runtime" / "scheduler_tasks.json"


def _scheduler_settings_path(tmp_path):
    return tmp_path / "fanxiu" / "data-annotation" / "runtime" / "scheduler_settings.json"


def _no_blocking_overlay_generator(*args, **kwargs):
    if False:
        yield None
    return False


def _patch_data_annotation_api_common(monkeypatch, tmp_path):
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", create_fanxiu_runtime_runner())
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_settings_path", lambda: _scheduler_settings_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_job_group_isolation_path", lambda: tmp_path / "job_group_isolation.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_settings_path", lambda: _scheduler_settings_path(tmp_path))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_job_group_isolation_path", lambda: tmp_path / "job_group_isolation.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [])
    monkeypatch.setattr(runtime_runner_core, "_behavior_tree_control_path", lambda: tmp_path / "behavior_tree_control.json")
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_behavior_tree_service_owner_path", lambda: tmp_path / "behavior_tree_service_owner.json")
    monkeypatch.setattr(fanxiu, "ensure_feature_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(fanxiu, "_get_user_device_or_404", lambda *args, **kwargs: object())


def test_data_annotation_json_write_retries_windows_permission_error(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    original_replace = fanxiu.Path.replace
    calls = {"count": 0}

    def flaky_replace(self, target):
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("locked")
        return original_replace(self, target)

    monkeypatch.setattr(fanxiu.Path, "replace", flaky_replace)
    monkeypatch.setattr(fanxiu.time, "sleep", lambda _seconds: None)

    fanxiu._write_data_annotation_json(path, {"ok": True})

    assert calls["count"] == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp")) == []


def test_data_annotation_default_scheduler_imports_legacy_behavior_tree_tasks():
    tasks = fanxiu._default_data_annotation_scheduler_tasks()

    legacy_tasks = [item for item in tasks if item["source"] == "legacy_behavior_tree"]
    daily_tasks = [item for item in legacy_tasks if item["schedule_kind"] == "daily"]
    dynamic_tasks = [item for item in legacy_tasks if item["schedule_kind"] == "dynamic"]
    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")
    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")
    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    gift = next(item for item in tasks if item["id"] == "gift-code-weekly")

    assert len(tasks) == 32
    assert len(legacy_tasks) == 11
    assert len(daily_tasks) == 10
    assert len(dynamic_tasks) == 1
    assert youli["task_type"] == "daily_youli"
    assert youli["source"] == "data_annotation_runtime"
    assert youli["enabled"] is False
    assert youli["interruptible"] is True
    assert signup["task_type"] == "daily_signup"
    assert signup["source"] == "data_annotation_runtime"
    assert signup["enabled"] is True
    assert signup["legacy_name"] == "日常_报名"
    assert assistant["task_type"] == "daily_assistant"
    assert assistant["source"] == "data_annotation_runtime"
    assert assistant["enabled"] is True
    assert assistant["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    yihuo = next(item for item in tasks if item["id"] == "legacy-daily-yihuo")
    assert yihuo["task_type"] == "daily_yihuo"
    assert yihuo["source"] == "data_annotation_runtime"
    assert yihuo["enabled"] is False
    assert yihuo["schedule_times"] == ["05:00"]
    assert gift["schedule_kind"] == "manual"
    assert gift["payload"] == {"codes": []}
    assert not any(item["id"] == "daily-locate" for item in tasks)


def test_data_annotation_scheduler_read_repairs_structural_fields(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-youli",
            "task_type": "legacy_daily_task",
            "label": "stale label",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": False,
            "priority": 123,
            "interruptible": True,
            "payload": {"custom": "kept"},
        },
        {
            "id": "legacy-daily-assistant",
            "task_type": "daily_assistant",
            "label": "日常_助手",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": False,
            "schedule_times": ["05:00", "12:00", "18:00", "00:00"],
            "interruptible": True,
            "payload": {},
        },
        {
            "id": "gift-code-real-test",
            "task_type": "gift_code_redeem",
            "label": "真实测试礼包码",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": False,
            "priority": 40,
            "interruptible": True,
            "payload": {"codes": []},
        },
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")
    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")

    assert not any(item["label"] == "真实测试礼包码" for item in tasks)
    assert youli["task_type"] == "daily_youli"
    assert youli["source"] == "data_annotation_runtime"
    assert youli["schedule_kind"] == "daily"
    assert youli["legacy_name"] == "日常_游历"
    assert youli["schedule_times"] == ["00:00", "05:00"]
    assert youli["payload"] == {"__scheduler_definition_task_type": "daily_youli"}
    assert youli["enabled"] is False
    assert "priority" not in youli
    assert youli["interruptible"] is True
    assert assistant["enabled"] is True
    assert assistant["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    assert any(item["id"] == "gift-code-weekly" for item in tasks)


def test_data_annotation_scheduler_response_marks_supported_tasks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)

    response = fanxiu.get_fanxiu_data_annotation_scheduler_tasks(
        current_user=object(),
        session=object(),
    )
    by_id = {item.id: item for item in response.tasks}

    assert by_id["gift-code-weekly"].supported is True
    assert by_id["go-settings"].supported is True
    assert by_id["hide-floating-window"].supported is True
    assert by_id["legacy-daily-assistant"].supported is True
    assert by_id["legacy-daily-youli"].supported is True


def test_data_annotation_scheduler_put_does_not_persist_supported_view_field(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    task = fanxiu.FanxiuDataAnnotationSchedulerTaskItem.model_validate({
        "id": "gift-code-weekly",
        "task_type": "gift_code_redeem",
        "label": "每周礼包码",
        "supported": False,
        "source": "manual",
        "schedule_kind": "manual",
        "enabled": False,
        "priority": 40,
        "interruptible": True,
        "next_time": None,
        "schedule_times": [],
        "window": None,
        "last_run_at": None,
        "last_result": "",
        "retry_after": None,
        "cooldown_seconds": 0,
        "payload": {"codes": []},
        "checkpoint": None,
    })

    response = fanxiu.put_fanxiu_data_annotation_scheduler_tasks(
        [task],
        current_user=object(),
        session=object(),
    )
    persisted = json.loads(_scheduler_state_path(tmp_path).read_text(encoding="utf-8"))

    assert response.tasks[0].supported is True
    assert "supported" not in persisted[0]


def test_data_annotation_scheduler_put_preserves_runtime_fields(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    current = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    current.update({
        "enabled": True,
        "last_run_at": "2026-06-06 18:51:41",
        "last_result": "success",
        "next_time": "2026-06-07 05:00:00",
        "retry_after": None,
    })
    fanxiu._write_data_annotation_scheduler_tasks([current])
    incoming = dict(current)
    incoming.update({
        "last_run_at": None,
        "last_result": "",
        "next_time": None,
        "retry_after": "2026-06-06 15:59:04",
    })

    response = fanxiu.put_fanxiu_data_annotation_scheduler_tasks(
        [fanxiu.FanxiuDataAnnotationSchedulerTaskItem.model_validate(incoming)],
        current_user=object(),
        session=object(),
    )
    signup = next(item for item in response.tasks if item.id == "legacy-daily-signup")
    persisted = json.loads(_scheduler_state_path(tmp_path).read_text(encoding="utf-8"))
    persisted_signup = next(item for item in persisted if item["id"] == "legacy-daily-signup")

    assert signup.enabled is True
    assert signup.last_run_at == "2026-06-06 18:51:41"
    assert signup.last_result == "success"
    assert signup.next_time == "2026-06-07 05:00:00"
    assert signup.retry_after is None
    assert persisted_signup["next_time"] == "2026-06-07 05:00:00"


def test_data_annotation_scheduler_partial_update_preserves_other_enabled_tasks(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    tasks = []
    for task_id in ("xianfu-learn-skill", "xianfu-visit-partner"):
        item = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == task_id).copy()
        item["enabled"] = True
        tasks.append(item)
    fanxiu._write_data_annotation_scheduler_tasks(tasks)

    update = dict(tasks[0])
    update["enabled"] = False
    result = runtime_control.update_scheduler_tasks(
        [update],
        scheduler_state_path=path,
        world_facts_path=tmp_path / "world_facts.json",
        now=datetime(2026, 6, 18, 15, 20, 0),
    )

    by_id = {item["id"]: item for item in result}
    assert by_id["xianfu-learn-skill"]["enabled"] is False
    assert by_id["xianfu-visit-partner"]["enabled"] is True


def test_data_annotation_scheduler_read_migrates_supported_legacy_tasks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-youli",
            "task_type": "legacy_daily_task",
            "label": "日常 游历",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "enabled": True,
            "priority": 10,
            "interruptible": True,
            "payload": {"legacy_name": "日常_游历"},
        }
    ])

    response = fanxiu.get_fanxiu_data_annotation_scheduler_tasks(
        current_user=object(),
        session=object(),
    )
    by_id = {item.id: item for item in response.tasks}
    persisted = json.loads(_scheduler_state_path(tmp_path).read_text(encoding="utf-8"))
    persisted_by_id = {item["id"]: item for item in persisted}

    assert by_id["legacy-daily-youli"].supported is True
    assert by_id["legacy-daily-youli"].enabled is False
    assert by_id["legacy-daily-youli"].last_result == ""
    assert persisted_by_id["legacy-daily-youli"]["enabled"] is False
    assert persisted_by_id["legacy-daily-youli"]["last_result"] == ""


def test_data_annotation_runtime_scheduler_routes_replace_stepper_routes():
    paths = {route.path for route in fanxiu.status_router.routes}

    required_paths = {
        "/data-annotation/runtime/status",
        "/data-annotation/runtime/task/start",
        "/data-annotation/runtime/task/stop",
        "/data-annotation/runtime/task/tick",
        "/data-annotation/runtime/logs",
        "/data-annotation/scheduler/tasks",
        "/data-annotation/scheduler/settings",
        "/data-annotation/scheduler/run-due",
        "/data-annotation/scheduler/task/run-now",
    }

    assert required_paths <= paths
    assert not any(path.startswith("/game-window3/") for path in paths)
    assert "/data-annotation/stepper/logs" not in paths
    assert not any("gift-code-task" in path for path in paths)


def test_data_annotation_scheduler_daily_next_time_uses_next_clock():
    task = {
        "schedule_kind": "daily",
        "schedule_times": ["05:00", "00:00"],
    }

    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 2, 4, 0)) == "2026-06-02 05:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 2, 6, 0)) == "2026-06-03 00:00:00"


def test_data_annotation_scheduler_read_initializes_enabled_daily_next_time(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 2, 6, 0, 0)

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")

    assert signup["enabled"] is True
    assert signup["next_time"] == "2026-06-02 05:00:00"
    assert assistant["enabled"] is True
    assert assistant["next_time"] == "2026-06-02 00:00:00"


def test_data_annotation_scheduler_read_repairs_enabled_failed_task_retry_time(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-youli",
            "task_type": "daily_youli",
            "label": "日常_游历",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:00", "05:00"],
            "last_result": "stopped",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": None,
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_youli"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")

    assert youli["enabled"] is True
    assert youli["last_result"] == "stopped"
    assert youli["next_time"] is None
    assert youli["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_scheduler_read_clears_failed_task_stale_next_time_when_retrying(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingta",
            "task_type": "daily_lingta",
            "label": "日常_灵塔",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "error",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": "2026-06-03 05:00:00",
            "retry_after": "2026-06-02 06:10:00",
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_lingta"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    lingta = next(item for item in tasks if item["id"] == "legacy-daily-lingta")

    assert lingta["last_result"] == "error"
    assert lingta["next_time"] is None
    assert lingta["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_scheduler_read_retries_stopped_task_with_stale_next_time(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "mail-cleanup",
            "task_type": "mail_cleanup",
            "label": "邮件_清理",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:05"],
            "last_result": "stopped",
            "last_run_at": "2026-06-02 00:16:35",
            "next_time": "2026-06-03 00:05:00",
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "mail_cleanup"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    mail = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert mail["last_result"] == "stopped"
    assert mail["next_time"] is None
    assert mail["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_scheduler_read_keeps_manual_check_pending_unscheduled(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "manual_check_pending",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": "2026-06-03 05:00:00",
            "retry_after": "2026-06-02 06:10:00",
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_boss"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    boss = next(item for item in tasks if item["id"] == "daily-boss")

    assert boss["last_result"] == "manual_check_pending"
    assert boss["next_time"] is None
    assert boss["retry_after"] is None


def test_data_annotation_scheduler_sync_ignores_failed_fact_stale_next_time_when_retrying(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingta",
            "task_type": "daily_lingta",
            "label": "日常_灵塔",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "error",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": None,
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_lingta"},
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "legacy-daily-lingta": {
                    "id": "legacy-daily-lingta",
                    "task_type": "daily_lingta",
                    "last_result": "error",
                    "last_run_at": "2026-06-02 05:58:00",
                    "discovered_next_time": "2026-06-03 05:00:00",
                    "discovered_retry_after": "2026-06-02 06:10:00",
                    "updated_at": fixed_now.timestamp(),
                }
            }
        }
    })

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    lingta = next(item for item in tasks if item["id"] == "legacy-daily-lingta")

    assert lingta["last_result"] == "error"
    assert lingta["next_time"] is None
    assert lingta["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_scheduler_sync_ignores_manual_pending_fact_next_time(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "",
            "last_run_at": None,
            "next_time": None,
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_boss"},
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "daily-boss": {
                    "id": "daily-boss",
                    "task_type": "daily_boss",
                    "last_result": "manual_check_pending",
                    "last_run_at": "2026-06-02 05:58:00",
                    "discovered_next_time": "2026-06-03 05:00:00",
                    "discovered_retry_after": "2026-06-02 06:10:00",
                    "updated_at": fixed_now.timestamp(),
                }
            }
        }
    })

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    boss = next(item for item in tasks if item["id"] == "daily-boss")

    assert boss["last_result"] == "manual_check_pending"
    assert boss["next_time"] is None
    assert boss["retry_after"] is None


def test_data_annotation_scheduler_forces_manual_tasks_disabled(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    task = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "gift-code-weekly").copy()
    task["enabled"] = True

    fanxiu._write_data_annotation_scheduler_tasks([task])
    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    gift = next(item for item in tasks if item["id"] == "gift-code-weekly")

    assert gift["schedule_kind"] == "manual"
    assert gift["enabled"] is False


def test_data_annotation_scheduler_order_uses_group_then_trigger_time():
    tasks = [
        {"id": "late-daily-high-priority", "schedule_kind": "daily", "priority": 1, "next_time": "2026-06-02 20:00:00"},
        {"id": "manual-low-priority", "schedule_kind": "manual", "priority": 999, "next_time": None},
        {"id": "early-daily-low-priority", "schedule_kind": "daily", "priority": 999, "next_time": "2026-06-02 05:00:00"},
        {"id": "dynamic", "schedule_kind": "dynamic", "priority": 1, "next_time": "2026-06-02 04:00:00"},
    ]

    ordered = sorted(tasks, key=fanxiu.data_annotation_scheduler_order_key)

    assert [item["id"] for item in ordered] == [
        "early-daily-low-priority",
        "late-daily-high-priority",
        "dynamic",
        "manual-low-priority",
    ]


def test_data_annotation_scheduler_restores_daily_runtime_fields_from_world_facts(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    facts_path = tmp_path / "world_facts.json"
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: facts_path)
    task = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    task.update({"enabled": False, "last_run_at": None, "last_result": "", "next_time": None})
    fanxiu._write_data_annotation_scheduler_tasks([task])
    fanxiu._write_data_annotation_json(
        facts_path,
        {
            "discoveries": {
                "task": {
                    "legacy-daily-signup": {
                        "last_result": "success",
                        "last_run_at": "2026-06-06 18:51:41",
                        "next_time": "2026-06-07 05:00:00",
                    }
                }
            }
        },
    )

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")

    assert signup["last_result"] == "success"
    assert signup["last_run_at"] == "2026-06-06 18:51:41"
    assert signup["next_time"] == "2026-06-07 05:00:00"
    assert signup["checkpoint"]["world_fact_synced_at"]


def test_data_annotation_task_due_respects_enabled_next_time_and_retry(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0).timestamp()
    monkeypatch.setattr(fanxiu.time, "time", lambda: now)

    assert fanxiu._data_annotation_task_due({"enabled": False, "next_time": None}) is False
    assert fanxiu._data_annotation_task_due({"enabled": True, "next_time": None}) is True
    assert fanxiu._data_annotation_task_due({"enabled": True, "next_time": "2026-06-02 12:01:00"}) is False
    assert fanxiu._data_annotation_task_due({"enabled": True, "next_time": "2026-06-02 11:59:00"}) is True
    assert fanxiu._data_annotation_task_due({
        "enabled": True,
        "next_time": "2026-06-02 11:59:00",
        "retry_after": "2026-06-02 12:01:00",
    }) is False


class _FakeRuntimeRunner:
    def __init__(self, status, can_preempt):
        self._status = status
        self._can_preempt = can_preempt
        self.stopped_entry_id = None
        self.waited = False

    def status(self):
        return dict(self._status)

    def can_preempt(self, priority):
        return self._can_preempt

    def stop_current_task(self, entry_id):
        self.stopped_entry_id = entry_id

    def wait_until_idle(self, timeout_seconds):
        self.waited = True
        return True


def test_data_annotation_prepare_scheduler_task_waits_when_runtime_is_busy(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = _FakeRuntimeRunner(
        {
            "running": True,
            "entry_id": "entry-a",
            "current_task_id": "slow-task",
            "status": "running",
        },
        can_preempt=True,
    )
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", runner)
    tasks = [
        {"id": "slow-task", "last_result": "running"},
        {"id": "fast-task", "priority": 10, "last_result": ""},
    ]

    blocked = fanxiu._prepare_data_annotation_runtime_for_scheduler_task(tasks[1], tasks)

    assert blocked is not None
    assert "当前有任务运行" in blocked["message"]
    assert "暂不触发" in blocked["message"]
    assert runner.stopped_entry_id is None
    assert runner.waited is False
    assert tasks[0]["last_result"] == "running"
    assert tasks[1]["last_result"] == ""
    assert runner.stopped_entry_id is None


def test_data_annotation_prepare_scheduler_task_interrupts_same_group_runtime(tmp_path, monkeypatch):
    statuses = [
        {
            "running": True,
            "entry_id": "entry-a",
            "task_type": "scheduler_run_due",
            "phase": "scheduler_task",
            "current_task_id": "slow-task",
            "status": "running",
            "interruptible": True,
        },
        {
            "running": False,
            "entry_id": "entry-a",
            "status": "idle",
        },
    ]
    stop_calls = []

    def fake_status():
        return dict(statuses[0])

    def fake_stop(entry_id):
        stop_calls.append(entry_id)
        statuses[0] = statuses[1]
        return dict(statuses[0])

    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_status", fake_status)
    monkeypatch.setattr(runtime_control, "stop_fanxiu_behavior_tree_current_task", fake_stop)

    blocked = runtime_control.prepare_runtime_for_scheduler_task(
        {"id": "fast-task", "last_result": ""},
        [{"id": "slow-task", "last_result": "running"}, {"id": "fast-task", "last_result": ""}],
        entry_id="entry-a",
        interrupt_same_group=True,
        wait_timeout_seconds=0.1,
        runtime_state_path=tmp_path / "runtime_state.json",
        world_facts_path=tmp_path / "world_facts.json",
    )

    assert blocked is None
    assert stop_calls == ["entry-a"]


def test_data_annotation_prepare_scheduler_task_does_not_interrupt_other_group_runtime(tmp_path, monkeypatch):
    stop_calls = []
    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_status", lambda: {
        "running": True,
        "entry_id": "entry-a",
        "task_type": "debug_eval",
        "phase": "manual_job",
        "current_task_id": "manual-1",
        "status": "running",
        "interruptible": True,
    })
    monkeypatch.setattr(runtime_control, "stop_fanxiu_behavior_tree_current_task", lambda entry_id: stop_calls.append(entry_id))

    blocked = runtime_control.prepare_runtime_for_scheduler_task(
        {"id": "fast-task", "last_result": ""},
        [{"id": "fast-task", "last_result": ""}],
        entry_id="entry-a",
        interrupt_same_group=True,
        wait_timeout_seconds=0.1,
        runtime_state_path=tmp_path / "runtime_state.json",
        world_facts_path=tmp_path / "world_facts.json",
    )

    assert blocked is not None
    assert "当前有任务运行" in blocked["message"]
    assert stop_calls == []


def test_data_annotation_world_facts_merges_runtime_guard_and_keeps_events(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")

    fanxiu._persist_data_annotation_runtime_status({
        "entry_id": "entry-a",
        "running": True,
        "status": "running",
        "task_type": "gift_code_redeem",
        "current_task": "兑换礼包码",
        "current_task_id": "gift-code-weekly",
        "phase": "process_code",
        "current_scene": 78,
        "message": "处理兑换码",
        "guard_enabled": True,
        "guard_running": True,
        "guard_entry_id": "entry-a",
        "last_guard_event": {
            "time": 100,
            "kind": "popup",
            "image": "#82",
            "title": "已被领取",
            "folder_path": "弹窗",
            "score": 94,
            "action": "observe",
        },
    })
    fanxiu._persist_data_annotation_runtime_status({
        "entry_id": "entry-a",
        "running": False,
        "status": "success",
        "task_type": "gift_code_redeem",
        "current_task": "兑换礼包码",
        "current_task_id": "gift-code-weekly",
        "phase": "done",
        "current_scene": 49,
        "message": "完成",
        "guard_enabled": True,
        "guard_running": False,
        "guard_entry_id": "entry-a",
        "last_guard_event": {},
    })

    facts = json.loads((tmp_path / "world_facts.json").read_text(encoding="utf-8"))

    assert facts["version"] == 1
    assert facts["runtime"]["current_scene"] == 49
    assert facts["runtime"]["current_task_id"] == "gift-code-weekly"
    assert facts["guard"]["enabled"] is True
    assert facts["discoveries"]["scene"]["78"]["phase"] == "process_code"
    assert facts["discoveries"]["scene"]["49"]["phase"] == "done"
    assert facts["discoveries"]["popup"]["popup:#82:已被领取:弹窗"]["score"] == 94
    assert any(event["kind"] == "guard_popup" and event["image"] == "#82" for event in facts["events"])


def test_data_annotation_scheduler_task_result_writes_world_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "manual-gift",
        "task_type": "gift_code_redeem",
        "label": "兑换礼包码",
        "source": "manual",
        "schedule_kind": "manual",
        "last_result": "",
        "last_run_at": None,
        "next_time": None,
        "retry_after": None,
    }

    runner._mark_scheduler_task([task], "manual-gift", "running")
    runner._mark_scheduler_task([task], "manual-gift", "success")
    facts = json.loads((tmp_path / "world_facts.json").read_text(encoding="utf-8"))

    assert facts["discoveries"]["task"]["manual-gift"]["last_result"] == "success"
    assert facts["discoveries"]["task"]["manual-gift"]["task_type"] == "gift_code_redeem"
    assert [event["result"] for event in facts["events"] if event["kind"] == "scheduler_task"] == ["running", "success"]


def test_data_annotation_runtime_indexes_nested_frame_tree_images_and_guard_candidates():
    runner = create_fanxiu_runtime_runner()
    tree = [
        {
            "type": "folder",
            "title": "日常",
            "children": [
                {
                    "type": "image",
                    "id": "img-69",
                    "title": "#69 日常",
                    "filename": "0069.png",
                    "shapes": [],
                    "children": [
                        {
                            "type": "image",
                            "id": "img-75",
                            "title": "#75 活动报名",
                            "filename": "0075.png",
                            "shapes": [
                                {
                                    "id": "shape-close",
                                    "title": "关闭",
                                    "sceneJumpTarget": "-1",
                                    "x": 10,
                                    "y": 10,
                                    "w": 20,
                                    "h": 20,
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]

    images = runner._index_images(tree)
    candidates = runner._index_guard_candidates(tree)

    assert set(images) == {69, 75}
    assert images[75]["title"] == "#75 活动报名"
    assert len(candidates) == 1
    assert candidates[0]["image"]["id"] == "img-75"
    assert candidates[0]["folder_path"] == "日常/#69 日常"
    assert candidates[0]["action_shape"]["title"] == "关闭"


def test_data_annotation_scheduler_plan_uses_world_facts_and_due_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_settings_path", lambda: _scheduler_settings_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [])
    runner = _FakeRuntimeRunner({"running": False, "status": "idle"}, can_preempt=True)
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", runner)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "priority": 40,
            "interruptible": True,
            "next_time": "2026-06-02 04:00:00",
            "schedule_times": [],
            "window": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": []},
            "checkpoint": None,
        }
    ])
    fanxiu._record_data_annotation_scheduler_task_fact({"id": "due-gift", "task_type": "gift_code_redeem", "label": "礼包"}, "success")

    plan = fanxiu._build_data_annotation_scheduler_plan()

    assert plan["next_action"] == "run_due"
    assert plan["due_tasks"][0]["id"] == "due-gift"
    assert plan["due_tasks"][0]["supported"] is True
    assert plan["due_tasks"][0]["runnable"] is True
    assert plan["due_tasks"][0]["fact"]["last_result"] == "success"
    assert plan["facts_summary"]["task_fact_count"] == 1
    legacy_item = next(item for item in plan["tasks"] if item["id"] == "legacy-daily-youli")
    assert legacy_item["supported"] is True


def test_scheduler_job_group_settings_default_enabled_and_persisted(tmp_path):
    path = _scheduler_settings_path(tmp_path)

    assert runtime_control.read_scheduler_settings(scheduler_settings_path=path)["job_group_enabled"] is True
    assert runtime_control.read_scheduler_settings(scheduler_settings_path=path)["behavior_tree_enabled"] is True

    saved = runtime_control.set_scheduler_job_group_enabled(False, scheduler_settings_path=path)

    assert saved["job_group_enabled"] is False
    assert saved["behavior_tree_enabled"] is True
    assert runtime_control.read_scheduler_settings(scheduler_settings_path=path)["job_group_enabled"] is False
    assert json.loads(path.read_text(encoding="utf-8"))["job_group_enabled"] is False


def test_run_due_scheduler_tasks_skips_when_behavior_tree_disabled(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runtime_control.write_scheduler_settings(
        {"job_group_enabled": True, "behavior_tree_enabled": False},
        scheduler_settings_path=_scheduler_settings_path(tmp_path),
    )
    monkeypatch.setattr(
        runtime_control,
        "ensure_fanxiu_behavior_tree_service",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("disabled behavior tree must not be ensured")),
    )
    monkeypatch.setattr(
        runtime_control,
        "fanxiu_runtime_runner_wake",
        lambda: (_ for _ in ()).throw(AssertionError("disabled behavior tree must not wake")),
    )
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "interruptible": True,
            "next_time": "2000-01-01 00:00:00",
            "payload": {"codes": []},
        }
    ])

    status = runtime_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        scheduler_state_path=_scheduler_state_path(tmp_path),
        scheduler_settings_path=_scheduler_settings_path(tmp_path),
        runtime_state_path=tmp_path / "runtime_state.json",
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
        asset_tree_path=tmp_path / "entry.json",
    )

    assert status["behavior_tree_enabled"] is False
    assert status["service_running"] is False
    assert status["phase"] == "behavior_tree_disabled"
    assert "行为树已关闭" in status["message"]


def test_scheduler_plan_keeps_due_tasks_but_marks_job_group_disabled(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(fanxiu.time, "time", lambda: datetime(2026, 6, 2, 12, 0).timestamp())
    runtime_control.set_scheduler_job_group_enabled(False, scheduler_settings_path=_scheduler_settings_path(tmp_path))
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "interruptible": True,
            "next_time": "2026-06-02 04:00:00",
            "payload": {"codes": []},
        }
    ])

    plan = fanxiu._build_data_annotation_scheduler_plan()

    assert plan["job_group_enabled"] is False
    assert plan["next_action"] == "job_group_disabled"
    assert plan["due_tasks"][0]["id"] == "due-gift"


def test_scheduler_plan_reports_blocking_overlays(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_status", lambda: {"running": False, "status": "idle"})
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [
        {
            "scene_id": 186,
            "title": "灵祖奖励浮层",
            "blocking": True,
            "message": "检测到灵祖奖励浮层；缺少关闭动作标注",
        }
    ])

    plan = runtime_control.build_scheduler_plan(
        entry_id="entry",
        asset_tree_path=tmp_path / "asset-tree.json",
        scheduler_state_path=_scheduler_state_path(tmp_path),
        scheduler_settings_path=_scheduler_settings_path(tmp_path),
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )

    assert plan["blocking_overlays"][0]["scene_id"] == 186
    assert plan["blocking_overlays"][0]["blocking"] is True
    assert plan["message"] == "检测到灵祖奖励浮层；缺少关闭动作标注"


def test_scheduler_plan_marks_due_tasks_blocked_by_overlay(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_status", lambda: {"running": False, "status": "idle"})
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [
        {
            "scene_id": 186,
            "title": "灵祖奖励浮层",
            "blocking": True,
            "message": "检测到灵祖奖励浮层；缺少关闭动作标注",
        }
    ])
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "interruptible": True,
            "next_time": "2000-01-01 00:00:00",
            "payload": {"codes": []},
        }
    ])

    plan = runtime_control.build_scheduler_plan(
        entry_id="entry",
        asset_tree_path=tmp_path / "asset-tree.json",
        scheduler_state_path=_scheduler_state_path(tmp_path),
        scheduler_settings_path=_scheduler_settings_path(tmp_path),
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )

    assert plan["next_action"] == "blocked"
    assert plan["due_tasks"][0]["id"] == "due-gift"
    assert plan["message"] == "检测到灵祖奖励浮层；缺少关闭动作标注"


def test_run_due_scheduler_tasks_stops_before_submit_when_overlay_blocks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_control, "ensure_fanxiu_behavior_tree_service", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_wake", lambda: (_ for _ in ()).throw(AssertionError("blocked scheduler must not wake service")))
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [
        {
            "scene_id": 186,
            "title": "灵祖奖励浮层",
            "blocking": True,
            "message": "检测到灵祖奖励浮层；缺少关闭动作标注",
        }
    ])
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "interruptible": True,
            "next_time": "2000-01-01 00:00:00",
            "last_result": "",
            "payload": {"codes": []},
            "checkpoint": {"manual_inspection_note": "旧人工备注：今日按成功处理"},
        }
    ])

    status = runtime_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        scheduler_state_path=_scheduler_state_path(tmp_path),
        scheduler_settings_path=_scheduler_settings_path(tmp_path),
        runtime_state_path=tmp_path / "runtime_state.json",
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
        asset_tree_path=tmp_path / "entry.json",
    )
    tasks = runtime_control.read_scheduler_tasks(
        scheduler_state_path=_scheduler_state_path(tmp_path),
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )
    due_task = next(item for item in tasks if item["id"] == "due-gift")

    assert status["phase"] == "scheduler_blocked"
    assert status["blocking_overlays"][0]["scene_id"] == 186
    assert due_task["last_result"] == "blocked"
    assert due_task["checkpoint"]["blocked_message"] == "检测到灵祖奖励浮层；缺少关闭动作标注"
    assert "manual_inspection_note" not in due_task["checkpoint"]
    assert due_task["checkpoint"]["previous_manual_inspection_note"] == "旧人工备注：今日按成功处理"
    assert due_task["next_time"] == "2000-01-01 00:00:00"


def test_run_due_scheduler_tasks_skips_when_job_group_disabled(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runtime_control.set_scheduler_job_group_enabled(False, scheduler_settings_path=_scheduler_settings_path(tmp_path))
    monkeypatch.setattr(runtime_control, "ensure_fanxiu_behavior_tree_service", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(runtime_control, "submit_manual_job", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("disabled job group must not submit jobs")))
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "interruptible": True,
            "next_time": "2000-01-01 00:00:00",
            "payload": {"codes": []},
        }
    ])

    status = runtime_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        scheduler_state_path=_scheduler_state_path(tmp_path),
        scheduler_settings_path=_scheduler_settings_path(tmp_path),
        runtime_state_path=tmp_path / "runtime_state.json",
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
        asset_tree_path=tmp_path / "entry.json",
    )

    assert status["phase"] == "scheduler_job_group_disabled"
    assert "作业组已关闭" in status["message"]


def test_data_annotation_scheduler_plan_waits_for_non_interruptible_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_settings_path", lambda: _scheduler_settings_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [])
    runner = _FakeRuntimeRunner(
        {
            "running": True,
            "status": "running",
            "current_task": "日常游历",
            "priority": 90,
            "interruptible": False,
        },
        can_preempt=False,
    )
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", runner)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "due-gift",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": True,
            "priority": 40,
            "interruptible": True,
            "next_time": "2026-06-02 04:00:00",
            "schedule_times": [],
            "window": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": []},
            "checkpoint": None,
        }
    ])

    plan = fanxiu._build_data_annotation_scheduler_plan()

    assert plan["next_action"] == "wait"
    assert plan["runtime"]["current_task"] == "日常游历"
    assert plan["due_tasks"][0]["id"] == "due-gift"
    assert plan["due_tasks"][0]["runnable"] is False


def test_data_annotation_scheduler_syncs_dynamic_next_time_from_world_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu.time, "time", lambda: datetime(2026, 6, 2, 12, 0, 0).timestamp())
    runner = _FakeRuntimeRunner({"running": False, "status": "idle"}, can_preempt=True)
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", runner)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "首领",
            "source": "legacy_behavior_tree",
            "schedule_kind": "dynamic",
            "legacy_name": "日常_首领",
            "enabled": True,
            "priority": 110,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "window": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_首领"},
            "checkpoint": None,
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        **fanxiu._initial_data_annotation_world_facts(),
        "discoveries": {
            "scene": {},
            "popup": {},
            "occlusion": {},
            "task": {
                "daily-boss": {
                    "id": "daily-boss",
                    "discovered_next_time": "2026-06-02 13:00:00",
                    "updated_at": 123,
                }
            },
        },
    })

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    target = next(item for item in tasks if item["id"] == "daily-boss")
    plan = fanxiu._build_data_annotation_scheduler_plan()
    plan_item = next(item for item in plan["tasks"] if item["id"] == "daily-boss")

    assert target["next_time"] == "2026-06-02 13:00:00"
    assert target["checkpoint"]["world_fact_updated_at"] == 123
    assert target["enabled"] is True
    assert target["last_result"] == ""
    assert plan_item["supported"] is True
    assert plan_item["due"] is False
    assert "未到时间" in plan_item["reason"]


def test_data_annotation_scheduler_syncs_retry_after_from_world_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "礼包",
            "source": "manual",
            "schedule_kind": "manual",
            "enabled": True,
            "priority": 40,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "window": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": []},
            "checkpoint": None,
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        **fanxiu._initial_data_annotation_world_facts(),
        "discoveries": {
            "scene": {},
            "popup": {},
            "occlusion": {},
            "task": {
                "gift-code-weekly": {
                    "id": "gift-code-weekly",
                    "discovered_retry_after": "2026-06-02 13:00:00",
                    "updated_at": 456,
                }
            },
        },
    })

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    target = next(item for item in tasks if item["id"] == "gift-code-weekly")

    assert target["retry_after"] == "2026-06-02 13:00:00"
    assert target["checkpoint"]["world_fact_updated_at"] == 456


def test_data_annotation_run_now_payload_override_does_not_mutate_scheduler_task():
    tasks = [
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "每周礼包码",
            "payload": {"codes": []},
        }
    ]

    run_task = fanxiu._data_annotation_scheduler_run_now_task(
        tasks,
        "gift-code-weekly",
        {"codes": ["煮梅消夏"]},
    )

    assert run_task is not None
    assert run_task["payload"]["codes"] == ["煮梅消夏"]
    assert tasks[0]["payload"]["codes"] == []
    assert run_task is not tasks[0]


def test_data_annotation_run_now_endpoint_uses_payload_override_without_persisting_codes(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "每周礼包码",
            "source": "manual",
            "schedule_kind": "manual",
            "legacy_name": "",
            "enabled": False,
            "priority": 40,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": []},
            "checkpoint": None,
        }
    ])
    runner = fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER

    def fake_ensure_service(**kwargs):
        with runner._lock:
            runner._status["service_running"] = True
            runner._status["entry_id"] = kwargs["entry_id"]
        return runner.status()

    monkeypatch.setattr(runner, "ensure_service", fake_ensure_service)

    response = fanxiu.run_now_fanxiu_data_annotation_scheduler_task(
        fanxiu.FanxiuDataAnnotationSchedulerRunNowRequest(
            entry_id="entry",
            task_id="gift-code-weekly",
            payload={"codes": ["煮梅消夏"]},
        ),
        current_user=object(),
        session=object(),
    )
    persisted = fanxiu._read_data_annotation_scheduler_tasks()
    persisted_task = next(item for item in persisted if item["id"] == "gift-code-weekly")
    queued_jobs = fanxiu._read_data_annotation_manual_jobs()
    run_job = queued_jobs[0]

    assert response.running is False
    assert run_job["task_type"] == "gift_code_redeem"
    assert run_job["payload"]["codes"] == ["煮梅消夏"]
    assert run_job["payload"]["__scheduler_task_id"] == "gift-code-weekly"
    assert persisted_task["payload"]["codes"] == []
    assert persisted_task["last_result"] == "queued"
    assert persisted_task["last_run_at"]


def test_data_annotation_run_now_does_not_directly_drain_pending_manual_job(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "每周礼包码",
            "source": "manual",
            "schedule_kind": "manual",
            "legacy_name": "",
            "enabled": False,
            "priority": 40,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": []},
            "checkpoint": None,
        }
    ])
    runner = fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER
    first_job = fanxiu._enqueue_data_annotation_manual_job("detect_scene", {}, label="旧手动作业")

    def fake_ensure_service(**kwargs):
        with runner._lock:
            runner._status["service_running"] = True
            runner._status["entry_id"] = kwargs["entry_id"]
        return runner.status()

    def fail_start_manual_runtime_task(**_kwargs):
        raise AssertionError("run-now must not directly consume pending manual jobs")

    monkeypatch.setattr(runner, "ensure_service", fake_ensure_service)
    monkeypatch.setattr(runner, "start_manual_runtime_task", fail_start_manual_runtime_task)

    response = fanxiu.run_now_fanxiu_data_annotation_scheduler_task(
        fanxiu.FanxiuDataAnnotationSchedulerRunNowRequest(
            entry_id="entry",
            task_id="gift-code-weekly",
            payload={"codes": ["煮梅消夏"]},
        ),
        current_user=object(),
        session=object(),
    )

    queued_jobs = fanxiu._read_data_annotation_manual_jobs()
    assert response.running is False
    assert [job["id"] for job in queued_jobs][0] == first_job["id"]
    assert [job["task_type"] for job in queued_jobs] == ["detect_scene", "gift_code_redeem"]
    assert "priority" not in queued_jobs[-1]


def test_data_annotation_manual_job_registry_dispatches_custom_backend_logic(monkeypatch):
    calls = []
    task_type = "codex_debug_probe"

    @fanxiu.register_fanxiu_data_annotation_manual_job(
        task_type,
        "Codex 调试探针",
        scheduler_supported=True,
        normalize_payload=lambda payload: {**payload, "normalized": True},
    )
    def debug_probe(runner, ctx, payload, stop_event):
        calls.append((ctx["marker"], payload["value"], payload["normalized"], stop_event.is_set()))
        return "success"

    try:
        runner = create_fanxiu_runtime_runner()
        ctx = {"marker": "ctx"}
        stop_event = fanxiu.threading.Event()

        assert runner._runtime_task_label(task_type, {}) == "Codex 调试探针"
        assert fanxiu._data_annotation_task_supported({"task_type": task_type}) is True
        assert runner._execute_runtime_task(ctx, task_type, {"value": 3}, stop_event) == "success"
        assert calls == [("ctx", 3, True, False)]
    finally:
        fanxiu._DATA_ANNOTATION_MANUAL_JOB_REGISTRY.pop(task_type, None)


def test_data_annotation_runner_registers_default_scheduler_jobs_when_checking_support():
    for task_type in ("daily_assistant", "daily_youli", "daily_yihuo", "daily_gongfeng", "daily_xianshi"):
        fanxiu._DATA_ANNOTATION_MANUAL_JOB_REGISTRY.pop(task_type, None)

    assert runtime_runner_core._data_annotation_task_supported({"task_type": "daily_assistant"}) is True
    assert runtime_runner_core._data_annotation_task_supported({"task_type": "daily_youli"}) is True
    assert runtime_runner_core._data_annotation_task_supported({"task_type": "daily_yihuo"}) is True
    assert runtime_runner_core._data_annotation_manual_job_definition("daily_yihuo") is not None
    assert runtime_runner_core._data_annotation_task_supported({"task_type": "daily_gongfeng"}) is True
    assert runtime_runner_core._data_annotation_task_supported({"task_type": "daily_xianshi"}) is True


def test_daily_gongfeng_law_progress_parser_uses_last_fraction_suffix():
    runner = create_fanxiu_runtime_runner()

    assert runner._parse_daily_gongfeng_law_progress("40001400/4000") == (1400, 4000)
    assert runner._parse_daily_gongfeng_law_progress("4000 4000/4000") == (4000, 4000)
    assert runner._parse_daily_gongfeng_law_progress("1400/4000") == (1400, 4000)


def test_data_annotation_runner_repairs_scheduler_tasks_before_selecting_due(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)

    tasks = runtime_runner_core._read_data_annotation_scheduler_tasks()
    by_id = {str(item.get("id") or ""): item for item in tasks}

    assert by_id["legacy-daily-assistant"]["task_type"] == "daily_assistant"
    assert by_id["legacy-daily-assistant"]["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    assert by_id["legacy-daily-youli"]["task_type"] == "daily_youli"
    assert by_id["legacy-daily-yihuo"]["task_type"] == "daily_yihuo"
    assert by_id["legacy-daily-gongfeng"]["task_type"] == "daily_gongfeng"
    assert by_id["legacy-daily-xianshi"]["task_type"] == "daily_xianshi"


def test_daily_xianshi_missing_free_box_records_retry_not_next_day(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 14, 11, 30, 0))
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    images = {scene_id: {"id": scene_id, "title": str(scene_id), "width": 900, "height": 1600, "shapes": []} for scene_id in (34, 247, 248, 249, 250)}
    ctx = {"asset_tree_path": asset_tree, "images": images}

    class FakeStopEvent:
        def is_set(self):
            return False

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return False

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (None, 0.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘藏阁 天衍灵石 仙币"}])
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {"coin_box_retry_seconds": 600}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "skipped"
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-xianshi"]
    assert fact["last_result"] == "skipped"
    assert fact["discovered_retry_after"] == "2026-06-14 11:40:00"
    assert fact["discovered_next_time"] is None


def test_daily_xianshi_no_free_box_records_next_day(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 14, 11, 30, 0))
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    images = {scene_id: {"id": scene_id, "title": str(scene_id), "width": 900, "height": 1600, "shapes": []} for scene_id in (34, 247, 248, 249, 250)}
    ctx = {"asset_tree_path": asset_tree, "images": images}

    class FakeStopEvent:
        def is_set(self):
            return False

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (None, 0.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘藏阁 天衍灵石 仙币"}])
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-xianshi"]
    assert fact["discovered_next_time"] == "2026-06-15 05:00:00"
    assert fact.get("discovered_retry_after") is None


def test_daily_xianshi_uses_runtime_observation_at_entry(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 14, 11, 30, 0))
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    images = {scene_id: {"id": scene_id, "title": str(scene_id), "width": 900, "height": 1600, "shapes": []} for scene_id in (34, 247, 248, 249, 250)}
    ctx = {"asset_tree_path": asset_tree, "images": images}
    observed: list[str] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    real_factory = runner._fanxiu_runtime

    def wrapped_runtime(*args, **kwargs):
        runtime = real_factory(*args, **kwargs)
        real_current_scene = runtime.current_scene
        real_ocr_text = runtime.ocr_text

        def current_scene(*scene_args, **scene_kwargs):
            observed.append("current_scene")
            return real_current_scene(*scene_args, **scene_kwargs)

        def ocr_text(*ocr_args, **ocr_kwargs):
            observed.append("ocr_text")
            return real_ocr_text(*ocr_args, **ocr_kwargs)

        runtime.current_scene = current_scene  # type: ignore[method-assign]
        runtime.ocr_text = ocr_text  # type: ignore[method-assign]
        return runtime

    monkeypatch.setattr(runner, "_fanxiu_runtime", wrapped_runtime)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (None, 0.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘藏阁 天衍灵石 仙币"}])
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert observed[:2] == ["current_scene", "ocr_text"]


def test_daily_xianshi_open_coin_list_reads_as_runtime_steps(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click_then_shape(self, view_id, shape, target_view_id, target_shape, **kwargs):
            actions.append(("wait_click_then_shape", view_id, shape, target_view_id, target_shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view_id(self, view_id, **kwargs):
            actions.append(("wait_view_id", view_id, kwargs))
            if False:
                yield None
            return view_id, 100.0

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)

    result = _drain_generator(
        runner._open_daily_xianshi_coin_list(
            ctx,
            fanxiu.threading.Event(),
            {"xianshi_entry_timeout": 1, "secret_tab_timeout": 1, "coin_tab_timeout": 1},
            {"id": 34},
            {"id": 247},
            {"id": 248},
            task_label="日常_仙市",
        )
    )

    assert result is None
    assert actions == [
        ("wait_click_then_shape", 34, "仙市", 247, "秘藏阁", {"settle_seconds": 2.0, "label": "日常_仙市：等待仙市入口页"}),
        ("wait_click_then_shape", 247, "秘藏阁", 248, "仙币", {"settle_seconds": 1.5, "label": "日常_仙市：等待秘藏阁仙币页"}),
        ("wait_click", 248, "仙币", {}),
        ("settle", 1.5),
    ]


def test_daily_xianshi_return_to_world_uses_runtime_click(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        default_wait_click_timeout = 10.0

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs.get("timeout")))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs.get("timeout"), kwargs.get("label")))
            if False:
                yield None
            return "success"

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)

    result = _drain_generator(
        runner._return_daily_xianshi_to_world(
            ctx,
            fanxiu.threading.Event(),
            {"return_timeout": 7, "return_world_timeout": 11},
            {"id": 249},
            task_label="日常_仙市",
        )
    )

    assert result is None
    assert actions == [
        ("wait_click", 249, "返回", None),
        ("wait_view", (34,), None, "日常_仙市：等待世界 #34"),
    ]


def test_daily_xianshi_claim_coin_box_uses_runtime_click_and_ocr(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        default_wait_click_timeout = 10.0

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs.get("timeout")))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

        def ocr_text(self, **kwargs):
            actions.append(("ocr_text", kwargs.get("update")))
            return "领取成功"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._claim_daily_xianshi_coin_box(
            ctx,
            fanxiu.threading.Event(),
            {"claim_timeout": 8, "claim_settle_seconds": 1.25},
            {"id": 250},
            task_label="日常_仙市",
        )
    )

    assert result is True
    assert actions == [
        ("wait_click", 250, "领取", None),
        ("settle", 1.25),
        ("ocr_text", True),
    ]


def test_daily_xianshi_free_coin_box_uses_runtime_click_and_ocr(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        default_wait_click_timeout = 10.0

        def wait_click_and_ocr(self, view_id, shape, **kwargs):
            actions.append((
                "wait_click_and_ocr",
                view_id,
                shape,
                kwargs.get("timeout"),
                kwargs.get("settle_seconds"),
            ))
            if False:
                yield None
            return "灵石仙币宝匣 免费 领取"

    def fake_claim(_ctx, _stop_event, _payload, _image250, *, task_label):
        actions.append(("claim", task_label))
        if False:
            yield None
        return True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", fake_claim)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._click_daily_xianshi_free_coin_box(
            ctx,
            fanxiu.threading.Event(),
            {"coin_box_settle_seconds": 1.25},
            {"id": 249},
            {"id": 250},
            task_label="日常_仙市",
        )
    )

    assert result is True
    assert actions == [
        ("wait_click_and_ocr", 249, "灵石仙币宝匣", None, 1.25),
        ("claim", "日常_仙市"),
    ]


def _drain_generator(gen):
    while True:
        try:
            next(gen)
        except StopIteration as exc:
            return exc.value


def _run_registered_daily_yihuo(runner, ctx, stop_event, payload=None):
    data_annotation_default_jobs.register_fanxiu_data_annotation_default_runtime_jobs()
    definition = runtime_runner_core._data_annotation_manual_job_definition("daily_yihuo")
    assert definition is not None
    return definition.handler(runner, ctx, payload or {}, stop_event)


def _wait_click_runtime(image):
    runner = create_fanxiu_runtime_runner()
    ctx = {"images": {int(image["id"]): image}, "entry": type("Entry", (), {"mode": "local"})()}
    return runner, runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=fanxiu.threading.Event())


def _sample_wait_click_flow(runtime):
    yield from runtime.wait_click(247, "秘藏阁")


def test_fanxiu_runtime_wait_click_fixed_shape(monkeypatch):
    image = {
        "id": 247,
        "title": "仙市",
        "width": 1000,
        "height": 2000,
        "shapes": [{"title": "秘藏阁", "x": 0.5, "y": 0.8, "w": 0.1, "h": 0.1}],
    }
    runner, runtime = _wait_click_runtime(image)
    clicks = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, img, x, y: clicks.append((img["id"], round(x), round(y))))

    _drain_generator(runtime.wait_click("#247", "[秘藏阁]"))

    assert clicks == [(247, 550, 1700)]


def test_fanxiu_runtime_wait_click_log_records_source_location(monkeypatch):
    image = {
        "id": 247,
        "title": "仙市",
        "width": 1000,
        "height": 2000,
        "shapes": [{"title": "秘藏阁", "x": 0.5, "y": 0.8, "w": 0.1, "h": 0.1}],
    }
    runner, runtime = _wait_click_runtime(image)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: None)

    _drain_generator(_sample_wait_click_flow(runtime))

    action_log = next(item for item in runner.status()["logs"] if item["kind"] == "waitClick")
    assert action_log["source_file"] == "test_fanxiu_data_annotation_scheduler.py"
    assert isinstance(action_log["source_line"], int)
    assert action_log["source_expr"] == "wait_click(247, '秘藏阁')"
    assert action_log["action"] == "wait_click"


def test_fanxiu_runtime_wait_click_duplicate_shape_requires_path(monkeypatch):
    image = {
        "id": 47,
        "title": "提示",
        "width": 1000,
        "height": 2000,
        "shapes": [
            {"title": "确认", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
            {"title": "提示", "kind": "group", "children": [{"title": "确认", "x": 0.2, "y": 0.2, "w": 0.1, "h": 0.1}]},
        ],
    }
    _runner, runtime = _wait_click_runtime(image)

    with pytest.raises(RuntimeError, match="命中多个目标"):
        _drain_generator(runtime.wait_click(47, "[确认]"))


def test_fanxiu_runtime_wait_click_path_selector(monkeypatch):
    image = {
        "id": 247,
        "title": "仙市",
        "width": 1000,
        "height": 2000,
        "shapes": [
            {"title": "下方菜单", "kind": "group", "children": [{"title": "秘藏阁", "x": 0.8, "y": 0.8, "w": 0.1, "h": 0.1}]},
            {"title": "秘藏阁", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1},
        ],
    }
    runner, runtime = _wait_click_runtime(image)
    clicks = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, img, x, y: clicks.append((round(x), round(y))))

    _drain_generator(runtime.wait_click("#247", "[下方菜单/秘藏阁]"))

    assert clicks == [(850, 1700)]


def test_fanxiu_runtime_wait_click_none_frame_uses_current_scene(monkeypatch):
    image = {
        "id": 247,
        "title": "仙市",
        "width": 1000,
        "height": 2000,
        "shapes": [{"title": "秘藏阁", "x": 0.8, "y": 0.8, "w": 0.1, "h": 0.1}],
    }
    runner, runtime = _wait_click_runtime(image)
    clicks = []
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (247, 100.0))
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, img, x, y: clicks.append((img["id"], round(x), round(y))))

    _drain_generator(runtime.wait_click(None, "[秘藏阁]"))

    assert clicks == [(247, 850, 1700)]


def test_fanxiu_runtime_wait_click_floating_without_condition_uses_fixed_click(monkeypatch):
    image = {
        "id": 216,
        "title": "详情",
        "width": 1000,
        "height": 2000,
        "shapes": [{"title": "邀请道友", "floating": True, "x": 0.4, "y": 0.8, "w": 0.2, "h": 0.1}],
    }
    runner, runtime = _wait_click_runtime(image)
    clicks = []
    logs = []
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, img, x, y: clicks.append((round(x), round(y))))
    monkeypatch.setattr(runner, "_log", lambda kind, message: logs.append((kind, message)))

    _drain_generator(runtime.wait_click(216, "[邀请道友]"))

    assert clicks == [(500, 1700)]
    assert any(kind == "warning" and "退化为固定坐标点击" in message for kind, message in logs)


def test_fanxiu_runtime_wait_click_nested_shape_uses_parent_match_region(monkeypatch):
    image = {
        "id": 249,
        "title": "秘藏阁",
        "width": 1000,
        "height": 2000,
        "shapes": [{
            "title": "窗口",
            "kind": "group",
            "x": 0.1,
            "y": 0.2,
            "w": 0.8,
            "h": 0.6,
            "children": [{
                "title": "灵石仙币宝匣",
                "x": 0.2,
                "y": 0.3,
                "w": 0.3,
                "h": 0.1,
                "ocrText": "灵石.*免费",
                "ocrMatchRole": "required",
                "ocrMatchMode": "regex",
                "imageMatchRole": "off",
            }],
        }],
    }
    runner, runtime = _wait_click_runtime(image)
    captured = {}

    def fake_wait(ctx, stop_event, img, shape, **kwargs):
        captured["match_shape"] = shape
        if False:
            yield None
        return "frame", {"matched": True, "resolved_box": {"x": 300, "y": 500, "w": 120, "h": 50}}

    monkeypatch.setattr(runner, "_wait_shape_match", fake_wait)
    monkeypatch.setattr(runner, "_click_shape", lambda ctx, img, shape, frame, match_result=None: captured.update(action_shape=shape, match_result=match_result))

    _drain_generator(runtime.wait_click(249, "[窗口/灵石仙币宝匣]"))

    assert captured["match_shape"]["title"] == "灵石仙币宝匣"
    assert captured["match_shape"]["x"] == 0.1
    assert captured["match_shape"]["y"] == 0.2
    assert captured["match_shape"]["w"] == 0.8
    assert captured["match_shape"]["h"] == 0.6
    assert captured["action_shape"]["title"] == "灵石仙币宝匣"
    assert captured["match_result"]["resolved_box"]["x"] == 300


def test_fanxiu_runtime_wait_click_ocr_floating_child_uses_shape_center(monkeypatch):
    image = {
        "id": 228,
        "title": "修仙传游历",
        "width": 900,
        "height": 1600,
        "shapes": [{
            "title": "菜单",
            "kind": "group",
            "x": 0.34,
            "y": 0.88,
            "w": 0.6,
            "h": 0.1,
            "children": [{
                "title": "游历",
                "floating": True,
                "x": 0.54,
                "y": 0.89,
                "w": 0.1,
                "h": 0.08,
                "ocrText": "游历",
                "ocrMatchRole": "required",
                "ocrMatchMode": "contains",
                "imageMatchRole": "off",
            }],
        }],
    }
    runner, runtime = _wait_click_runtime(image)
    captured: dict[str, object] = {}
    clicks: list[dict[str, object]] = []

    def fake_match(ctx, img, shape, frame, **kwargs):
        captured["match_shape"] = dict(shape)
        return {
            "similarity": 0,
            "matches": [{"text": "游历道祖逸闻", "x": 506, "y": 1444, "w": 324, "h": 97}],
        }

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_run_match", fake_match)
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda payload: clicks.append(dict(payload)))

    _drain_generator(runtime.wait_click(228, "[菜单/游历]"))

    assert captured["match_shape"]["title"] == "游历"
    assert captured["match_shape"]["x"] == pytest.approx(0.34)
    assert captured["match_shape"]["w"] == pytest.approx(0.6)
    assert clicks
    assert clicks[-1]["x"] == pytest.approx(531.0)
    assert clicks[-1]["y"] == pytest.approx(1488.0)


def test_goto_view_route_candidate_ranking_prefers_score_clarity_then_shortest(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    tree = [
        {"type": "image", "id": 1, "title": "高可信长路径", "shapes": [{"title": "下一步", "sceneJumpTarget": "10"}]},
        {"type": "image", "id": 2, "title": "同分歧义短路径", "shapes": [{"title": "歧义", "sceneJumpTarget": "98,99"}]},
        {"type": "image", "id": 3, "title": "低可信短路径", "shapes": [{"title": "直达", "sceneJumpTarget": "99"}]},
        {"type": "image", "id": 10, "title": "中转", "shapes": [{"title": "直达", "sceneJumpTarget": "99"}]},
        {"type": "image", "id": 98, "title": "旁路", "shapes": []},
        {"type": "image", "id": 99, "title": "目标", "shapes": []},
    ]
    ctx = {"images": {scene_id: {"id": scene_id, "title": str(scene_id), "shapes": []} for scene_id in [1, 2, 3, 99]}}
    scores = {1: 90.0, 2: 90.0, 3: 89.0, 99: 0.0}
    monkeypatch.setattr(runner, "_scene_score", lambda _ctx, image, _frame: scores[int(image["id"])])

    scene_id, score = runner._identify_scene_number_for_route(ctx, "frame", tree, 99, [99, 1, 2, 3])

    assert (scene_id, score) == (1, 90.0)

    scores[3] = 91.0
    scene_id, score = runner._identify_scene_number_for_route(ctx, "frame", tree, 99, [99, 1, 2, 3])

    assert (scene_id, score) == (3, 91.0)


def test_data_annotation_daily_schedule_uses_nearest_future_time_independent_of_order():
    task_a = {"schedule_kind": "daily", "schedule_times": ["05:00", "00:00"]}
    task_b = {"schedule_kind": "daily", "schedule_times": ["00:00", "05:00"]}

    assert fanxiu._next_data_annotation_scheduler_time(task_a, datetime(2026, 6, 13, 23, 0, 0)) == "2026-06-14 00:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task_b, datetime(2026, 6, 13, 23, 0, 0)) == "2026-06-14 00:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task_a, datetime(2026, 6, 14, 0, 4, 0)) == "2026-06-14 05:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task_b, datetime(2026, 6, 14, 6, 0, 0)) == "2026-06-15 00:00:00"


def test_data_annotation_scheduler_repair_corrects_wrong_multi_clock_future_next_time():
    raw = [{
        "id": "legacy-daily-youli",
        "task_type": "daily_youli",
        "label": "日常_游历",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-14 05:00:00",
        "schedule_times": ["05:00", "00:00"],
        "last_run_at": "2026-06-13 18:00:00",
        "last_result": "success",
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_youli"},
    }]

    tasks, changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 13, 18, 16, 0),
    )

    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")
    assert changed is True
    assert youli["next_time"] == "2026-06-14 00:00:00"


def test_data_annotation_scheduler_repair_keeps_unfinished_daily_task_due_today():
    raw = [{
        "id": "legacy-daily-lingta",
        "task_type": "daily_lingta",
        "label": "日常_灵塔",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-19 05:00:00",
        "schedule_times": ["05:00"],
        "last_run_at": None,
        "last_result": "",
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_lingta"},
    }]

    tasks, changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 18, 15, 0, 0),
    )

    lingta = next(item for item in tasks if item["id"] == "legacy-daily-lingta")
    assert changed is True
    assert lingta["next_time"] == "2026-06-18 05:00:00"


def test_data_annotation_scheduler_repair_keeps_successful_daily_task_on_next_day():
    raw = [{
        "id": "legacy-daily-signup",
        "task_type": "daily_signup",
        "label": "日常_报名",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-19 05:00:00",
        "schedule_times": ["05:00"],
        "last_run_at": "2026-06-18 14:32:15",
        "last_result": "success",
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_signup"},
    }]

    tasks, changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 18, 15, 0, 0),
    )

    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    assert signup["next_time"] == "2026-06-19 05:00:00"


def test_data_annotation_scheduler_repair_keeps_due_multi_clock_task_due():
    raw = [{
        "id": "legacy-daily-youli",
        "task_type": "daily_youli",
        "label": "日常_游历",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-14 00:00:00",
        "schedule_times": ["05:00", "00:00"],
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_youli"},
    }]

    tasks, _changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 14, 0, 0, 1),
    )

    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")
    assert youli["next_time"] == "2026-06-14 00:00:00"


def test_xianfu_visit_partner_returns_world_when_waiting_cd(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        171: {"id": 171, "title": "仙府主页", "shapes": [{"title": "离开", "x": 0.8, "y": 0.3, "w": 0.1, "h": 0.1}]},
        174: {
            "id": 174,
            "title": "绝品仙侣",
            "shapes": [
                {"title": "状态", "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.03},
                {"title": "免费提示", "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.03},
                {"title": "退出", "x": 0.05, "y": 0.9, "w": 0.08, "h": 0.05},
            ],
        },
    }

    class FakeRuntime:
        def __init__(self):
            self.goto_targets = []
            self.clicked = []
            self.scene_id = 174
            self.ctx = {"images": images}

        def cur_frame(self, update=False):
            return object()

        def current_scene(self, view_ids=None, **kwargs):
            return self.scene_id, 100.0, self.cur_frame(update=bool(kwargs.get("update")))

        def get_view(self, view_id):
            image = images.get(int(view_id))
            return runtime_runner_core.View(image) if image else None

        def click_shape(self, view, shape):
            self.clicked.append((view.id, shape.title))
            if view.id == 174 and shape.title == "退出":
                self.scene_id = 171
            elif view.id == 171 and shape.title == "离开":
                self.scene_id = 34

        def wait_view(self, *view_ids, **kwargs):
            if False:
                yield None
            return self.get_view(self.scene_id) if self.scene_id in view_ids else self.scene_id

        def goto_view(self, target_id):
            self.goto_targets.append(target_id)
            if False:
                yield None
            return "success"

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_screencap", lambda ctx: object())
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred: (fake_runtime.scene_id, 100.0))
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", lambda *args, **kwargs: [{"text": "11:58:07后可免费抽取"}])
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_next_time", lambda *args, **kwargs: None)
    ctx = {"asset_tree_path": tmp_path / "asset-tree.json", "images": images}

    gen = runner._execute_xianfu_visit_partner_task(
        ctx,
        fanxiu.threading.Event(),
        {"__scheduler_task_id": "xianfu-visit-partner"},
    )
    while True:
        try:
            next(gen)
        except StopIteration as exc:
            result = exc.value
            break

    assert result == "success"
    assert fake_runtime.clicked == [(174, "退出"), (171, "离开")]
    assert fake_runtime.goto_targets == []
    assert fake_runtime.scene_id == 34


def test_daily_jianling_confirm_does_not_treat_early_main_frame_as_done(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"images": {}}
    frames = iter(["early-main", "result"])
    actions: list[tuple] = []

    class FakeRuntime:
        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, frame))
            if frame == "early-main":
                return 190, 100.0, frame
            return 192, 100.0, frame

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "early-main":
                return "淬剑试炼 通关进度 剩余次数"
            return "扫荡奖励 点击屏幕继续"

    monkeypatch.setattr(runtime_runner_core.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    gen = runner._confirm_daily_jianling_sweep(ctx, fanxiu.threading.Event())
    next(gen)
    # The old behavior returned "main" here and let cleanup click back before the reward popup appeared.
    next(gen)
    with pytest.raises(StopIteration) as exc_info:
        next(gen)

    assert exc_info.value.value == "result"
    assert actions == [
        ("wait_click", 191, "进行扫荡", {}),
        ("current_scene", (190, 192), {"update": True}, "early-main"),
        ("ocr_text", "early-main"),
        ("current_scene", (190, 192), {"update": True}, "result"),
        ("ocr_text", "result"),
    ]


def test_daily_jianling_finish_result_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    frames = iter(["result", "main"])

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, frame))
            if frame == "result":
                return 192, 100.0, frame
            return 190, 100.0, frame

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "result":
                return "扫荡奖励 点击屏幕继续"
            return "淬剑试炼 通关进度 剩余次数"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(runner._finish_daily_jianling_result(ctx, fanxiu.threading.Event()))

    assert result == "success"
    assert actions == [
        ("current_scene", (190, 192), {"update": True}, "result"),
        ("ocr_text", "result"),
        ("wait_click", 192, "点击继续", {}),
        ("current_scene", (190, 192), {"update": True}, "main"),
        ("ocr_text", "main"),
    ]


def test_daily_lingta_finish_result_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    frames = iter(["result", "main"])

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, frame))
            if frame == "result":
                return 196, 100.0, frame
            return 194, 100.0, frame

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "result":
                return "扫荡奖励 点击屏幕继续"
            return "混沌灵塔 剩余次数 扫荡"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(runner._finish_daily_lingta_result(ctx, fanxiu.threading.Event()))

    assert result == "success"
    assert actions == [
        ("current_scene", (194, 196), {"update": True}, "result"),
        ("ocr_text", "result"),
        ("wait_click", 196, "点击继续", {}),
        ("current_scene", (194, 196), {"update": True}, "main"),
        ("ocr_text", "main"),
    ]


def test_daily_jianling_sweep_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

    def fake_confirm(*_args, **_kwargs):
        actions.append(("confirm",))
        if False:
            yield None
        return "main"

    def fake_cleanup(callback, *, label, repeat_risk):
        actions.append(("cleanup", label, repeat_risk))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_confirm_daily_jianling_sweep", fake_confirm)
    monkeypatch.setattr(runner, "_record_daily_jianling_done", lambda payload, *, message: actions.append(("record_done", message)))
    monkeypatch.setattr(runner, "_safe_daily_done_cleanup", fake_cleanup)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._run_daily_jianling_sweep(
            ctx,
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result is None
    assert actions == [
        ("wait_click", 190, "扫荡", {}),
        ("wait_view", (191,), {"label": "日常_剑灵：等待扫荡确认 #191"}),
        ("confirm",),
        ("record_done", "淬剑试炼扫荡完成"),
        ("cleanup", "日常_剑灵", "重复扫荡"),
    ]


def test_daily_lingta_sweep_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

    def fake_confirm(*_args, **_kwargs):
        actions.append(("confirm",))
        if False:
            yield None
        return "success"

    def fake_finish(*_args, **_kwargs):
        actions.append(("finish",))
        if False:
            yield None
        return "success"

    def fake_cleanup(callback, *, label, repeat_risk):
        actions.append(("cleanup", label, repeat_risk))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_confirm_daily_lingta_sweep", fake_confirm)
    monkeypatch.setattr(runner, "_finish_daily_lingta_result", fake_finish)
    monkeypatch.setattr(runner, "_record_daily_lingta_done", lambda payload, *, message: actions.append(("record_done", message)))
    monkeypatch.setattr(runner, "_safe_daily_done_cleanup", fake_cleanup)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._run_daily_lingta_sweep(
            ctx,
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result is None
    assert actions == [
        ("wait_click", 194, "扫荡", {}),
        ("wait_view", (195,), {"label": "日常_灵塔：等待扫荡确认 #195"}),
        ("confirm",),
        ("finish",),
        ("record_done", "混沌灵塔扫荡完成"),
        ("cleanup", "日常_灵塔", "重复扫荡"),
    ]


def test_daily_lingta_confirm_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._confirm_daily_lingta_sweep(
            ctx,
            fanxiu.threading.Event(),
        )
    )

    assert result is None
    assert actions == [
        ("wait_click", 195, "进行扫荡", {}),
        ("wait_view", (196,), {"label": "日常_灵塔：等待扫荡结果 #196"}),
    ]


def test_daily_lingta_entry_opens_main_with_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    frames = iter(["entry", "main"])

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, frame))
            if frame == "entry":
                return 193, 100.0, frame
            return 194, 100.0, frame

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "entry":
                return "灵塔区域 进入"
            return "混沌灵塔 剩余次数 扫荡"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(runner._open_daily_lingta_main_from_entry(ctx, fanxiu.threading.Event()))

    assert result == "success"
    assert actions == [
        ("current_scene", (194, 193), {"update": True}, "entry"),
        ("ocr_text", "entry"),
        ("wait_click", 193, "进入", {}),
        ("current_scene", (194, 193), {"update": True}, "main"),
        ("ocr_text", "main"),
    ]


def test_daily_lingta_entry_keeps_jianling_misroute_guard(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return None, 0.0, "jianling"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "淬剑试炼 通关进度 剩余次数"

        def wait_click(self, *_args, **_kwargs):
            raise AssertionError("must not click lingta entry after jianling text")

    def fake_return(_ctx, _stop_event):
        actions.append(("return_jianling",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_return_daily_jianling_to_world", fake_return)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    with pytest.raises(RuntimeError, match="不是混沌灵塔"):
        _drain_generator(runner._open_daily_lingta_main_from_entry(ctx, fanxiu.threading.Event()))

    assert actions == [
        ("current_scene", (194, 193), {"update": True}),
        ("ocr_text", "jianling"),
        ("return_jianling",),
    ]


def test_daily_jianling_return_to_world_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return 190, 100.0, "frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "淬剑试炼 通关进度 剩余次数"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_view_id(self, view_id, **kwargs):
            actions.append(("wait_view_id", view_id, kwargs))
            if False:
                yield None
            return view_id, 100.0

    return_scenes = iter([(69, 100.0)])

    def fake_wait_return(_ctx, _stop_event, scene_ids, **kwargs):
        actions.append(("wait_return", tuple(scene_ids), kwargs))
        if False:
            yield None
        return next(return_scenes)

    def fake_ensure(_ctx, _stop_event):
        actions.append(("ensure_outer",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_lingzu_return_scene", fake_wait_return)
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_ensure_daily_lingzu_outer_world", fake_ensure)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime for initial state")))

    result = _drain_generator(runner._return_daily_jianling_to_world(ctx, fanxiu.threading.Event()))

    assert result == "success"
    assert actions == [
        ("current_scene", (190, 69, 34), {"update": True}),
        ("ocr_text", "frame"),
        ("wait_click", 190, "返回", {}),
        ("wait_return", (69, 34), {"timeout": 18.0, "label": "日常_剑灵：等待日常 #69 或世界 #34"}),
        ("wait_click", 69, "退出", {}),
        ("wait_view_id", 34, {"timeout": 18.0, "label": "日常_剑灵：等待世界 #34"}),
        ("ensure_outer",),
    ]


def test_daily_lingta_return_to_world_uses_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return 194, 100.0, "frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "混沌灵塔 剩余次数 扫荡"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

    return_scenes = iter([(69, 100.0), (34, 100.0)])

    def fake_wait_return(_ctx, _stop_event, scene_ids, **kwargs):
        actions.append(("wait_return", tuple(scene_ids), kwargs))
        if False:
            yield None
        return next(return_scenes)

    def fake_ensure(_ctx, _stop_event):
        actions.append(("ensure_outer",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_lingzu_return_scene", fake_wait_return)
    monkeypatch.setattr(runner, "_ensure_daily_lingzu_outer_world", fake_ensure)
    monkeypatch.setattr(runner, "_leave_daily_lingta_green_bottle", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not enter green bottle")))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime for initial state")))

    result = _drain_generator(runner._return_daily_lingta_to_world(ctx, fanxiu.threading.Event()))

    assert result == "success"
    assert actions == [
        ("current_scene", (194, 69, 20, 34), {"update": True}),
        ("ocr_text", "frame"),
        ("wait_click", 194, "返回", {}),
        (
            "wait_return",
            (69, 20, 34),
            {"timeout": 18.0, "label": "日常_灵塔：等待日常 #69、绿瓶 #20 或世界 #34"},
        ),
        ("wait_click", 69, "退出", {}),
        ("wait_return", (20, 34), {"timeout": 18.0, "label": "日常_灵塔：等待绿瓶 #20 或世界 #34"}),
        ("ensure_outer",),
    ]


def test_daily_lingta_daily_list_requires_progress_on_lingta_row(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    lines = [
        {"text": "日常", "x": 80, "y": 220, "w": 100, "h": 40},
        {"text": "活跃度 修为 修为", "x": 220, "y": 280, "w": 260, "h": 40},
        {"text": "参与击败圣祖", "x": 120, "y": 480, "w": 300, "h": 40},
        {"text": "1/1", "x": 760, "y": 480, "w": 80, "h": 40},
        {"text": "活动报名小助手奖励找回新", "x": 160, "y": 1580, "w": 420, "h": 40},
        {"text": "日常周常", "x": 420, "y": 1800, "w": 180, "h": 40},
    ]

    def no_scroll(*args, **kwargs):
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))
    monkeypatch.setattr(runner, "_scroll_daily_xianyuan_list", no_scroll)

    gen = runner._open_daily_lingta_from_daily(ctx, fanxiu.threading.Event(), {"max_scrolls": 0})
    with pytest.raises(RuntimeError, match="未找到"):
        next(gen)


def test_daily_lingta_daily_list_refuses_false_scene_69_world_frame(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    scrolled = []

    def no_scroll(*args, **kwargs):
        scrolled.append(True)
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "world-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "世界 储物袋 角色 装备 功法书 日程"}])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))
    monkeypatch.setattr(runner, "_scroll_daily_xianyuan_list", no_scroll)

    gen = runner._open_daily_lingta_from_daily(ctx, fanxiu.threading.Event(), {"max_scrolls": 3})
    with pytest.raises(RuntimeError, match="未确认当前在 #69 日常列表"):
        next(gen)

    assert scrolled == []


def test_daily_boss_daily_list_refuses_false_scene_69_world_frame(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    scrolled = []

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "world-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "世界 储物袋 角色 装备 功法书 日程"}])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))
    monkeypatch.setattr(runner, "_scroll_shape_content_changed", lambda *args, **kwargs: scrolled.append(True))

    gen = runner._open_daily_boss_list_from_daily(ctx, fanxiu.threading.Event())
    with pytest.raises(RuntimeError, match="未确认当前在 #69 日常列表"):
        next(gen)

    assert scrolled == []


def test_daily_lingta_daily_list_marks_done_only_on_lingta_row(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    lines = [
        {"text": "日常", "x": 80, "y": 220, "w": 100, "h": 40},
        {"text": "活跃度 修为 修为", "x": 220, "y": 280, "w": 260, "h": 40},
        {"text": "挑战或扫荡混沌灵塔", "x": 120, "y": 520, "w": 360, "h": 40},
        {"text": "1/1", "x": 760, "y": 520, "w": 80, "h": 40},
        {"text": "活动报名小助手奖励找回新", "x": 160, "y": 1580, "w": 420, "h": 40},
        {"text": "日常周常", "x": 420, "y": 1800, "w": 180, "h": 40},
    ]

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))

    gen = runner._open_daily_lingta_from_daily(ctx, fanxiu.threading.Event(), {"max_scrolls": 0})
    with pytest.raises(StopIteration) as exc_info:
        next(gen)

    assert exc_info.value.value == "done"


def test_daily_jianling_daily_list_requires_progress_on_jianling_row(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    lines = [
        {"text": "日常", "x": 80, "y": 220, "w": 100, "h": 40},
        {"text": "活跃度 修为 修为", "x": 220, "y": 280, "w": 260, "h": 40},
        {"text": "参与击败圣祖", "x": 120, "y": 480, "w": 300, "h": 40},
        {"text": "1/1", "x": 760, "y": 480, "w": 80, "h": 40},
        {"text": "活动报名小助手奖励找回新", "x": 160, "y": 1580, "w": 420, "h": 40},
        {"text": "日常周常", "x": 420, "y": 1800, "w": 180, "h": 40},
    ]

    def no_scroll(*args, **kwargs):
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))
    monkeypatch.setattr(runner, "_scroll_daily_xianyuan_list", no_scroll)

    gen = runner._open_daily_jianling_from_daily(ctx, fanxiu.threading.Event(), {"max_scrolls": 0})
    with pytest.raises(RuntimeError, match="未找到"):
        next(gen)


def test_daily_jianling_daily_list_refuses_false_scene_69_world_frame(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    scrolled = []

    def no_scroll(*args, **kwargs):
        scrolled.append(True)
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "world-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "世界 储物袋 角色 装备 功法书 日程"}])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))
    monkeypatch.setattr(runner, "_scroll_daily_xianyuan_list", no_scroll)

    gen = runner._open_daily_jianling_from_daily(ctx, fanxiu.threading.Event(), {"max_scrolls": 3})
    with pytest.raises(RuntimeError, match="未确认当前在 #69 日常列表"):
        next(gen)

    assert scrolled == []


def test_daily_jianling_daily_list_marks_done_only_on_jianling_row(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "滚动窗口", "x": 0.0, "y": 0.15, "w": 1.0, "h": 0.7}],
    }
    ctx = {"images": {69: image69}}
    lines = [
        {"text": "日常", "x": 80, "y": 220, "w": 100, "h": 40},
        {"text": "活跃度 修为 修为", "x": 220, "y": 280, "w": 260, "h": 40},
        {"text": "挑战或扫荡淬剑试炼", "x": 120, "y": 520, "w": 360, "h": 40},
        {"text": "1/1", "x": 760, "y": 520, "w": 80, "h": 40},
        {"text": "活动报名小助手奖励找回新", "x": 160, "y": 1580, "w": 420, "h": 40},
        {"text": "日常周常", "x": 420, "y": 1800, "w": 180, "h": 40},
    ]

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: lines)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (69, 100.0))

    gen = runner._open_daily_jianling_from_daily(ctx, fanxiu.threading.Event(), {"max_scrolls": 0})
    with pytest.raises(StopIteration) as exc_info:
        next(gen)

    assert exc_info.value.value == "done"


def test_daily_youli_purchase_reads_remaining_from_shape_and_closes(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    image229 = {
        "id": 229,
        "title": "游历购买体力",
        "w": 1080,
        "h": 1920,
        "shapes": [
            {"title": "剩余限购次数", "x": 0.32, "y": 0.24, "w": 0.3, "h": 0.04},
            {"title": "购买并使用", "x": 0.37, "y": 0.75, "w": 0.28, "h": 0.05},
            {"title": "空白", "x": 0.05, "y": 0.92, "w": 0.1, "h": 0.05},
        ],
    }
    image233 = {"id": 233, "title": "游历购买次数不足", "shapes": [{"title": "空白", "x": 0.05, "y": 0.92, "w": 0.1, "h": 0.05}]}

    class FakeRuntime:
        def wait_view_or_ocr(self, view_id, predicate, **kwargs):
            actions.append(("wait_view_or_ocr", view_id, predicate("游历符 购买并使用"), kwargs))
            if False:
                yield None
            return "scene", 229, 100.0

        def ocr_numbers_in_shapes(self, view_id, shape_titles, **kwargs):
            actions.append(("ocr_numbers_in_shapes", view_id, tuple(shape_titles), kwargs))
            return [3], "剩余限购次数：3"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            yield None
            return "success"

        def current_scene(self, views, **kwargs):
            actions.append(("current_scene", tuple(views), kwargs))
            return 229, 100.0, "purchase-frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "游历符 购买并使用"

    def close_dialog(_ctx, _stop_event, _image229, *, task_label):
        actions.append(("close_dialog", _image229["id"], task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_close_daily_youli_purchase_dialog", close_dialog)
    monkeypatch.setattr(runner, "_screencap", lambda ctx: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_click_shape_respecting_conditions", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    gen = runner._click_daily_youli_purchase_uses(
        {},
        fanxiu.threading.Event(),
        {"purchase_uses": 3},
        image229,
        image233,
        task_label="日常_游历",
    )
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert [item for item in actions if item[0] == "wait_click"] == [
        ("wait_click", 229, "购买并使用", {}),
        ("wait_click", 229, "购买并使用", {}),
        ("wait_click", 229, "购买并使用", {}),
    ]
    assert actions[-1] == ("close_dialog", 229, "日常_游历")


def test_daily_youli_open_purchase_uses_runtime_branch_wait(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    image228 = {"id": 228, "title": "修仙传游历"}
    image229 = {"id": 229, "title": "游历购买体力"}
    image233 = {"id": 233, "title": "游历购买次数不足"}

    class FakeRuntime:
        def shape_visible(self, view_id, shape, **kwargs):
            actions.append(("shape_visible", view_id, shape, kwargs))
            return ("shape_visible", view_id, shape)

        def view_visible(self, view_id, **kwargs):
            actions.append(("view_visible", view_id, kwargs))
            return ("view_visible", view_id)

        def wait_click_then_any(self, view_id, shape, conditions, **kwargs):
            actions.append(("wait_click_then_any", view_id, shape, conditions, kwargs))
            if False:
                yield None
            return "purchase"

    def fake_wait_home(*_args, **_kwargs):
        actions.append(("wait_home",))
        if False:
            yield None
        return "success"

    def fake_purchase_uses(_ctx, _stop_event, _payload, _image229, _image233, *, task_label):
        actions.append(("purchase_uses", _image229["id"], _image233["id"], task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_home)
    monkeypatch.setattr(runner, "_click_daily_youli_purchase_uses", fake_purchase_uses)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._open_daily_youli_purchase(
            ctx,
            fanxiu.threading.Event(),
            {"purchase_click_settle_seconds": 1.5},
            image228,
            image229,
            image233,
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert actions == [
        ("wait_home",),
        ("shape_visible", 229, "购买并使用", {}),
        ("shape_visible", 233, "空白", {}),
        ("view_visible", 228, {"threshold": 95.0}),
        (
            "wait_click_then_any",
            228,
            "购买",
            {
                "purchase": ("shape_visible", 229, "购买并使用"),
                "empty": ("shape_visible", 233, "空白"),
                "home": ("view_visible", 228),
            },
            {"settle_seconds": 1.5, "label": "日常_游历：等待购买体力结果"},
        ),
        ("purchase_uses", 229, 233, "日常_游历"),
    ]


def test_daily_youli_wait_home_uses_runtime_scene_or_ocr_conditions(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_view_or_ocr(self, view_id, predicate, **kwargs):
            actions.append(("wait_view_or_ocr", view_id, predicate("修仙传 游历 人界"), kwargs))
            if False:
                yield None
            return "text", 228, 0.0

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._wait_daily_youli_home(
            ctx,
            fanxiu.threading.Event(),
            label="日常_游历：等待修仙传游历 #228",
        )
    )

    assert result == (228, 0.0)
    assert actions == [
        (
            "wait_view_or_ocr",
            228,
            True,
            {
                "view_threshold": 95.0,
                "timeout": 12.0,
                "label": "日常_游历：等待修仙传游历 #228",
            }
        ),
    ]


def test_daily_youli_last_region_uses_runtime_ocr_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    image228 = {"id": 228, "title": "修仙传游历", "shapes": [{"title": "检索区域"}]}
    image236 = {"id": 236, "title": "游历区域详情"}
    image237 = {"id": 237, "title": "游历结果"}

    class FakeRuntime:
        def ocr_row_clicks_in_shape(self, view_id, shape_title, **kwargs):
            actions.append(("ocr_rows", view_id, shape_title, kwargs))
            return [(100.0, 200.0, "人界"), (120.0, 300.0, "仙界")]

        def click_frame_point(self, view_id, x, y):
            actions.append(("click_point", view_id, x, y))

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

    def fake_wait_home(*_args, **_kwargs):
        actions.append(("wait_home",))
        if False:
            yield None
        return "success"

    def fake_wait_region(*_args, **_kwargs):
        actions.append(("wait_region",))
        if False:
            yield None
        return "success"

    def fake_quick(_ctx, _stop_event, _payload, _image236, _image237, *, task_label):
        actions.append(("quick", _image236["id"], _image237["id"], task_label))
        if False:
            yield None
        return "success"

    def fake_return(_ctx, _stop_event, _image228, _image236, *, task_label):
        actions.append(("return", _image228["id"], _image236["id"], task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_home)
    monkeypatch.setattr(runner, "_wait_daily_youli_region_detail", fake_wait_region)
    monkeypatch.setattr(runner, "_click_daily_youli_quick_travel", fake_quick)
    monkeypatch.setattr(runner, "_return_daily_youli_to_world", fake_return)
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    result = _drain_generator(
        runner._click_daily_youli_last_region(
            ctx,
            fanxiu.threading.Event(),
            {"region_click_settle_seconds": 1.5},
            image228,
            image236,
            image237,
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert actions == [
        ("wait_home",),
        ("ocr_rows", 228, "检索区域", {"include": ()}),
        ("click_point", 228, 120.0, 300.0),
        ("settle", 1.5),
        ("wait_region",),
        ("quick", 236, 237, "日常_游历"),
        ("return", 228, 236, "日常_游历"),
    ]


def test_daily_youli_purchase_closers_use_runtime_clicks(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_shape_respecting_conditions", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    empty_result = _drain_generator(
        runner._close_daily_youli_purchase_empty(
            ctx,
            fanxiu.threading.Event(),
            {"id": 233},
            task_label="日常_游历",
        )
    )
    dialog_result = _drain_generator(
        runner._close_daily_youli_purchase_dialog(
            ctx,
            fanxiu.threading.Event(),
            {"id": 229},
            task_label="日常_游历",
        )
    )

    assert empty_result == "success"
    assert dialog_result == "success"
    assert actions == [
        ("wait_click", 233, "空白", {"label": "日常_游历：关闭购买次数不足提示"}),
        ("settle", 1.0),
        ("wait_click", 229, "空白", {"label": "日常_游历：关闭购买体力弹窗"}),
        ("settle", 1.0),
    ]


def test_daily_youli_mainline_shortcut_enters_youli_home(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "id": 34,
        "title": "世界",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "主线", "x": 0.59, "y": 0.08, "w": 0.31, "h": 0.03}],
    }
    image228 = {
        "id": 228,
        "title": "修仙传游历",
        "w": 1080,
        "h": 1920,
        "shapes": [{"title": "菜单", "x": 0.34, "y": 0.88, "w": 0.6, "h": 0.1}],
    }
    clicked: list[str] = []
    waited: list[tuple[int, ...]] = []

    def click_shape(ctx, stop_event, image, shape, payload, **kwargs):
        clicked.append(shape["title"])
        if False:
            yield None
        return "success"

    def wait_settle(*args, **kwargs):
        if False:
            yield None
        return "success"

    class FakeView:
        id = 228

    class FakeRuntime:
        def wait_view(self, *view_ids, **kwargs):
            waited.append(tuple(view_ids))
            if False:
                yield None
            return FakeView()

    monkeypatch.setattr(runner, "_click_shape_respecting_conditions", click_shape)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", wait_settle)
    monkeypatch.setattr(runner, "_screencap", lambda ctx: "youli-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "修仙传 游历 人界"}])
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (228, 100.0))

    result = _drain_generator(
        runner._try_enter_daily_youli_from_world_mainline(
            {},
            FakeRuntime(),
            fanxiu.threading.Event(),
            {},
            image34,
            image228,
            task_label="日常_游历",
        )
    )

    assert result is True
    assert clicked == ["主线"]
    assert waited == [(228, 71)]


def test_daily_youli_selects_youli_from_xiuxianzhuan_menu(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image71 = {"id": 71, "title": "修仙传", "w": 1080, "h": 1920, "shapes": []}
    clicked: list[tuple[float, float]] = []

    def wait_settle(*args, **kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "xiuxianzhuan-frame")
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: [
            {"text": "修仙传", "x": 70, "y": 106, "w": 207, "h": 108},
            {"text": "游历道祖逸闻", "x": 507, "y": 1443, "w": 323, "h": 98},
        ],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, image, x, y: clicked.append((x, y)))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", wait_settle)

    result = _drain_generator(
        runner._select_daily_youli_from_xiuxianzhuan_menu(
            {},
            fanxiu.threading.Event(),
            {},
            image71,
            task_label="日常_游历",
        )
    )

    assert result is True
    assert clicked == [(pytest.approx(560.8333333333), 1492.0)]


def test_ocr_substring_center_targets_text_fragment_not_whole_line():
    runner = create_fanxiu_runtime_runner()
    line = {"text": "游历道祖逸闻", "x": 507, "y": 1443, "w": 323, "h": 98}

    youli = runner._ocr_substring_center(line, "游历")
    daozu = runner._ocr_substring_center(line, "道祖")

    assert youli == (pytest.approx(560.8333333333), 1492.0)
    assert daozu == (pytest.approx(668.5), 1492.0)
    assert youli != daozu


def test_daily_youli_home_text_rejects_xiuxianzhuan_story_menu():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_youli_text_is_home("修仙传 游历 人界 探索完成") is True
    assert runner._daily_youli_text_is_home("修仙传 道祖鸿蒙 幻境 机缘 游历道祖逸闻") is False


def test_daily_youli_return_to_world_uses_runtime_clicks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    state = {"scene": 236}
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, view_ids=None, **_kwargs):
            actions.append(("current_scene", tuple(view_ids or ())))
            return state["scene"], 100.0, "frame"

        def ocr_text(self, _frame=None):
            actions.append(("ocr_text",))
            return "游历区域详情 快速游历 返回"

        def wait_click(self, view_id, shape, **_kwargs):
            actions.append(("wait_click", view_id, shape))
            if view_id == 236 and shape == "返回":
                state["scene"] = 228
            elif view_id == 228 and shape == "返回":
                state["scene"] = 34
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **_kwargs):
            actions.append(("wait_view", view_ids))
            if int(state["scene"]) not in {int(view_id) for view_id in view_ids}:
                raise RuntimeError("unexpected scene")
            if False:
                yield None
            return state["scene"]

    def fake_wait_daily_youli_home(*_args, **_kwargs):
        actions.append(("wait_home",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_daily_youli_home)

    result = _drain_generator(
        runner._return_daily_youli_to_world(
            ctx,
            fanxiu.threading.Event(),
            {"id": 228},
            {"id": 236},
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert actions == [
        ("current_scene", (236, 228, 34)),
        ("ocr_text",),
        ("wait_click", 236, "返回"),
        ("wait_home",),
        ("wait_click", 228, "返回"),
        ("wait_view", (34,)),
    ]


def test_daily_youli_reward_recovery_return_uses_runtime_clicks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    state = {"scene": 69, "after_first_exit": True}
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click(self, view_id, shape, **_kwargs):
            actions.append(("wait_click", view_id, shape))
            if view_id == 69 and shape == "退出":
                if state["after_first_exit"]:
                    state["after_first_exit"] = False
                    state["scene"] = 69
                else:
                    state["scene"] = 34
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

        def current_scene(self, view_ids=None, **_kwargs):
            actions.append(("current_scene", tuple(view_ids or ())))
            return state["scene"], 100.0, "frame"

        def ocr_text(self, _frame=None):
            actions.append(("ocr_text",))
            return "日常 活跃度 活动报名 小助手 奖励找回"

        def wait_view(self, *view_ids, **_kwargs):
            actions.append(("wait_view", view_ids))
            if int(state["scene"]) not in {int(view_id) for view_id in view_ids}:
                raise RuntimeError("unexpected scene")
            if False:
                yield None
            return state["scene"]

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._return_daily_youli_reward_recovery_to_world(
            ctx,
            fanxiu.threading.Event(),
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert actions == [
        ("wait_click", 69, "退出"),
        ("settle", 1.5),
        ("current_scene", (34, 69)),
        ("ocr_text",),
        ("wait_click", 69, "退出"),
        ("settle", 1.5),
        ("wait_view", (34,)),
    ]


def test_daily_gongfeng_runs_marked_closed_loop(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        34: {"id": 34, "title": "世界", "width": 900, "height": 1600, "shapes": [{"title": "主线", "x": 0.6, "y": 0.08, "w": 0.2, "h": 0.04}]},
        251: {"id": 251, "title": "0251.png", "width": 900, "height": 1600, "shapes": [{"title": "供奉", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.14, "y": 0.32, "w": 0.08, "h": 0.04}]},
        252: {
            "id": 252,
            "title": "0252.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "次数", "x": 0.5, "y": 0.75, "w": 0.2, "h": 0.04},
                {"title": "接受供奉", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.38, "y": 0.81, "w": 0.25, "h": 0.04},
                {"title": "额外奖励", "x": 0.76, "y": 0.2, "w": 0.12, "h": 0.07},
                {"title": "升级法则", "ocrMatchRole": "required", "ocrText": "升级", "x": 0.76, "y": 0.81, "w": 0.19, "h": 0.04},
            ],
        },
        254: {
            "id": 254,
            "title": "0254.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "数值", "x": 0.54, "y": 0.69, "w": 0.18, "h": 0.03},
                {"title": "升级", "imageMatchRole": "required", "x": 0.55, "y": 0.74, "w": 0.15, "h": 0.03},
                {"title": "空白", "x": 0.05, "y": 0.94, "w": 0.08, "h": 0.04},
            ],
        },
        255: {"id": 255, "title": "0255.png", "width": 900, "height": 1600, "shapes": [{"title": "供奉", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.3, "y": 0.46, "w": 0.37, "h": 0.07}, {"title": "空白", "x": 0.05, "y": 0.93, "w": 0.08, "h": 0.04}]},
        256: {"id": 256, "title": "0256.png", "width": 900, "height": 1600, "shapes": [{"title": "返回", "x": 0.04, "y": 0.93, "w": 0.08, "h": 0.04}, {"title": "法则", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.14, "y": 0.07, "w": 0.14, "h": 0.06}]},
        257: {"id": 257, "title": "0257.png", "width": 900, "height": 1600, "shapes": [{"title": "空白", "x": 0.1, "y": 0.91, "w": 0.09, "h": 0.04}, {"title": "物品", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.13, "y": 0.36, "w": 0.4, "h": 0.03}]},
    }
    state = {"page": "34", "accept_remaining": 2, "law_current": 5000, "law_required": 2000}
    actions: list[str] = []

    class FakeRuntime:
        def __init__(self, ctx):
            self.ctx = ctx
            self.stop_event = fanxiu.threading.Event()
            self.attrs: dict[str, object] = {}

        @property
        def payload(self):
            payload = self.attrs.get("payload")
            return payload if isinstance(payload, dict) else {}

        def set_completion_message(self, message):
            self.attrs["completion_message"] = message

        def cur_frame(self, update=False):
            return state["page"]

        def current_scene(self, view_ids=None, **kwargs):
            frame = state["page"]
            preferred = [int(view.id) if isinstance(view, runtime_runner_core.View) else int(view) for view in view_ids] if view_ids is not None else None
            scene_id, score = identify_scene(self.ctx, frame, preferred)
            return scene_id, score, frame

        def view(self, view_id):
            return runtime_runner_core.View(images[int(view_id)])

        def goto_view(self, view_id):
            actions.append(f"goto:{view_id}")
            state["page"] = str(view_id)
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(f"wait:{view_ids}")
            if False:
                yield None
            current = int(state["page"])
            if current not in view_ids:
                raise RuntimeError(f"not on expected view: {view_ids}, current={current}")
            return runtime_runner_core.View(images[current])

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(f"wait_click:{view_id}:{shape}")
            if view_id == 34 and shape == "主线":
                state["page"] = "251"
            elif view_id == 251 and shape == "供奉":
                state["page"] = "252"
            elif view_id == 252 and shape == "接受供奉":
                state["accept_remaining"] = max(0, int(state["accept_remaining"]) - 1)
            elif view_id == 252 and shape == "额外奖励":
                state["page"] = "257"
            elif view_id == 252 and shape == "升级法则":
                state["page"] = "254"
            elif view_id == 254 and shape == "升级":
                state["law_current"] = max(0, int(state["law_current"]) - int(state["law_required"]))
                state["page"] = "255"
            elif view_id == 254 and shape == "空白":
                state["page"] = "256"
            elif view_id == 257 and shape == "空白":
                state["page"] = "252"
            elif view_id == 255 and shape == "空白":
                state["page"] = "254"
            elif view_id == 47 and shape == "空白":
                state["page"] = "252"
            elif view_id == 256 and shape == "返回":
                state["page"] = "252"
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(f"settle:{seconds}")
            if False:
                yield None
            return None

        def wait_shape(self, view_id, shape, **kwargs):
            actions.append(f"wait_shape:{view_id}:{shape}")
            if False:
                yield None
            if int(state["page"]) != int(view_id):
                raise RuntimeError(f"not on expected shape view: {view_id}, current={state['page']}")
            return state["page"]

        def click_shape_center(self, view_id, shape, **kwargs):
            actions.append(f"click_shape_center:{view_id}:{shape}")
            if int(view_id) == 254 and shape == "升级":
                state["law_current"] = max(0, int(state["law_current"]) - int(state["law_required"]))
                state["page"] = "255"
            return "success"

        def view_visible(self, view_id, **kwargs):
            return ("view_visible", int(view_id))

        def ocr_contains(self, **kwargs):
            return ("ocr_contains", kwargs)

        def wait_any(self, conditions, **kwargs):
            actions.append(f"wait_any:{'/'.join(str(key) for key in conditions)}")
            if False:
                yield None
            current = int(state["page"])
            for key, condition in conditions.items():
                if isinstance(condition, tuple) and condition == ("view_visible", current):
                    return key
                if isinstance(condition, tuple) and condition[0] == "ocr_contains" and current == 254:
                    return key
            raise AssertionError(f"no fake wait_any condition matched for page={current}: {conditions}")

        def ocr_numbers_in_shapes(self, view_id, shape_titles, **kwargs):
            lines = ocr_lines_in_shapes(state["page"], images[int(view_id)], tuple(shape_titles), padding=kwargs.get("padding", 16))
            text = " ".join(str(line.get("text") or "") for line in lines)
            return [int(match) for match in re.findall(r"\d+", text)], text

    def click_shape(ctx, image, shape, frame=None, match_result=None):
        actions.append(f"click:{image['id']}:{shape['title']}")
        if image["id"] == 252 and shape["title"] == "额外奖励":
            state["page"] = "257"
        elif image["id"] == 257 and shape["title"] == "空白":
            state["page"] = "252"
        elif image["id"] == 254 and shape["title"] == "升级":
            state["law_current"] = max(0, int(state["law_current"]) - int(state["law_required"]))
            state["page"] = "255"
        elif image["id"] == 255 and shape["title"] == "空白":
            state["page"] = "254"
        elif image["id"] == 254 and shape["title"] == "空白":
            state["page"] = "256"

    def ocr_lines_in_shapes(frame, image, shape_titles, padding=16):
        if image["id"] == 252 and "次数" in shape_titles:
            return [{"text": f"{state['accept_remaining']}/1+", "x": 0, "y": 0, "w": 10, "h": 10}]
        if image["id"] == 254 and "数值" in shape_titles:
            return [{"text": f"{state['law_current']}/{state['law_required']}", "x": 0, "y": 0, "w": 10, "h": 10}]
        return []

    def identify_scene(ctx, frame, preferred=None):
        page = int(state["page"])
        if preferred is None or page in preferred:
            return page, 100.0
        return None, 0.0

    def shape_score(ctx, image, shape, frame, *args, **kwargs):
        if image["id"] == int(state["page"]):
            return 100.0
        return 0.0

    runtime_ctx = {"asset_tree_path": tmp_path / "asset-tree.json", "images": images}
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime(runtime_ctx))
    monkeypatch.setattr(runner, "_click_shape", click_shape)
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, image, x, y: actions.append(f"point:{image['id']}:{round(x, 1)}:{round(y, 1)}"))
    monkeypatch.setattr(runner, "_screencap", lambda ctx: state["page"])
    monkeypatch.setattr(runner, "_ocr_lines_in_shapes", ocr_lines_in_shapes)
    monkeypatch.setattr(runner, "_identify_scene_number", identify_scene)
    monkeypatch.setattr(runner, "_shape_score", shape_score)
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_next_time", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *args, **kwargs: iter(()))

    result = _drain_generator(
        runner._execute_daily_gongfeng_task(
            runtime_ctx,
            fanxiu.threading.Event(),
            {"max_accept": 5},
        )
    )

    assert result == "success"
    assert state["accept_remaining"] == 0
    assert state["law_current"] == 1000
    assert state["page"] == "34"
    assert "wait_click:34:主线" in actions
    assert actions.count("wait_click:252:接受供奉") == 2
    assert "wait_click:252:额外奖励" in actions
    assert actions.count("wait_click:255:空白") == 2
    assert actions.count("click_shape_center:254:升级") == 2
    assert "wait_click:257:空白" in actions
    assert "wait_click:254:空白" in actions
    assert "wait_click:256:返回" in actions
    assert "goto:34" in actions


def test_daily_yihuo_opens_xinghai_from_world_menu(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        34: {
            "id": 34,
            "title": "世界",
            "width": 900,
            "height": 1600,
            "shapes": [
                {
                    "title": "下方菜单",
                    "x": 0.38,
                    "y": 0.70,
                    "w": 0.5,
                    "h": 0.28,
                    "children": [
                        {"title": "星海", "x": 0.63, "y": 0.71, "w": 0.1, "h": 0.06},
                    ],
                },
            ],
        },
        259: {
            "id": 259,
            "title": "0259.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "异火", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.84, "y": 0.81, "w": 0.09, "h": 0.04},
                {"title": "返回", "x": 0.05, "y": 0.91, "w": 0.07, "h": 0.04},
            ],
        },
        260: {
            "id": 260,
            "title": "0260.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "净莲", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.78, "y": 0.16, "w": 0.14, "h": 0.09},
                {"title": "返回", "imageMatchRole": "required", "x": 0.05, "y": 0.91, "w": 0.07, "h": 0.04},
            ],
        },
        261: {
            "id": 261,
            "title": "0261.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "箱子", "imageMatchRole": "required", "x": 0.83, "y": 0.05, "w": 0.08, "h": 0.04},
                {"title": "返回", "x": 0.05, "y": 0.90, "w": 0.08, "h": 0.04},
            ],
        },
    }
    state = {"page": 34}
    actions: list[str] = []

    class FakeRuntime:
        ctx = {"images": images}

        def get_view(self, view_id):
            return runtime_runner_core.View(images[int(view_id)])

        def goto_view(self, view_id):
            actions.append(f"goto:{view_id}")
            state["page"] = int(view_id)
            if False:
                yield None
            return "success"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(f"wait_click:{view_id}:{shape}")
            if view_id == 34 and shape == "下方菜单/星海":
                state["page"] = 259
            elif view_id == 259 and shape == "异火":
                state["page"] = 260
            elif view_id == 260 and shape == "净莲":
                state["page"] = 261
            elif view_id == 261 and shape == "返回":
                state["page"] = 260
            elif view_id == 260 and shape == "返回":
                state["page"] = 259
            elif view_id == 259 and shape == "返回":
                state["page"] = 34
            if False:
                yield None
            return "success"

        def wait_clicks(self, steps):
            for view_id, shape in steps:
                yield from self.wait_click(view_id, shape)

        def wait_view(self, *view_ids, **kwargs):
            actions.append(f"wait_view:{view_ids}")
            if False:
                yield None
            return runtime_runner_core.View(images[int(view_ids[0])])

        def shape_visible(self, view_id, shape, **kwargs):
            return ("shape", int(view_id), str(shape))

        def view_visible(self, view_id, **kwargs):
            return ("view", int(view_id))

        def ocr_contains(self, **kwargs):
            return ("ocr", kwargs)

        def all_of(self, *conditions, **kwargs):
            return ("all", conditions)

        def wait_any(self, conditions, **kwargs):
            def matched(condition):
                kind = condition[0]
                if kind == "shape":
                    return state["page"] == condition[1]
                if kind == "view":
                    return state["page"] == condition[1]
                if kind == "ocr":
                    return True
                if kind == "all":
                    return all(matched(item) for item in condition[1])
                return False

            for key, condition in conditions.items():
                if matched(condition):
                    if False:
                        yield None
                    return key
            raise AssertionError(f"no fake wait_any condition matched: {conditions}")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(runner, "_screencap", lambda ctx: str(state["page"]))
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame: 97.0 if int(frame) == image["id"] and shape["title"] in {"异火", "净莲", "箱子", "返回"} else 0.0)

    def click_shape(ctx, image, shape, frame=None, match_result=None):
        actions.append(f"click:{image['id']}:{shape['title']}")
        if image["id"] == 259 and shape["title"] == "异火":
            state["page"] = 260
        elif image["id"] == 260 and shape["title"] == "净莲":
            state["page"] = 261
        elif image["id"] == 260 and shape["title"] == "返回":
            state["page"] = 259
        elif image["id"] == 261 and shape["title"] == "返回":
            state["page"] = 260
        elif image["id"] == 259 and shape["title"] == "返回":
            state["page"] = 34

    monkeypatch.setattr(runner, "_click_shape", click_shape)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (34, 100.0) if str(frame) == "34" else (None, 0.0))
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_next_time", lambda *args, **kwargs: None)

    result = _drain_generator(
        _run_registered_daily_yihuo(
            runner,
            {"asset_tree_path": tmp_path / "asset-tree.json", "images": images},
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result == "success"
    assert actions == [
        "goto:34",
        "wait_click:34:下方菜单/星海",
        "wait_click:259:异火",
        "wait_click:260:净莲",
        "wait_click:261:箱子",
        "wait_click:261:返回",
        "wait_click:260:返回",
        "wait_click:259:返回",
        "wait_view:(34,)",
    ]


def test_daily_yihuo_return_wait_accepts_direct_world(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "id": 34,
        "title": "世界",
        "width": 900,
        "height": 1600,
        "shapes": [],
    }
    image259 = {
        "id": 259,
        "title": "0259.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "返回", "x": 0.05, "y": 0.91, "w": 0.07, "h": 0.04},
        ],
    }

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "world-frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (34, 100.0))
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame: 0.0)

    runtime = runtime_runner_core.FanxiuRuntime(
        runner,
        {"images": {34: image34, 259: image259}},
        stop_event=fanxiu.threading.Event(),
    )

    result = _drain_generator(
        runtime.wait_any(
            {
                "world": runtime.view_visible(34),
                "yihuo_back": runtime.shape_visible(259, "返回"),
            },
            timeout=1.0,
        )
    )

    assert result == "world"


def test_daily_yihuo_aligns_to_world_from_local_jinglian_page(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        34: {
            "id": 34,
            "title": "世界",
            "width": 900,
            "height": 1600,
            "shapes": [
                {
                    "title": "下方菜单",
                    "x": 0.38,
                    "y": 0.70,
                    "w": 0.5,
                    "h": 0.28,
                    "children": [
                        {"title": "星海", "x": 0.63, "y": 0.71, "w": 0.1, "h": 0.06},
                    ],
                },
            ],
        },
        259: {
            "id": 259,
            "title": "0259.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "异火", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.84, "y": 0.81, "w": 0.09, "h": 0.04},
                {"title": "返回", "x": 0.05, "y": 0.91, "w": 0.07, "h": 0.04},
            ],
        },
        260: {
            "id": 260,
            "title": "0260.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "净莲", "isSceneIdentity": True, "imageMatchRole": "required", "x": 0.78, "y": 0.16, "w": 0.14, "h": 0.09},
                {"title": "返回", "imageMatchRole": "required", "x": 0.05, "y": 0.91, "w": 0.07, "h": 0.04},
            ],
        },
        261: {
            "id": 261,
            "title": "0261.png",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "箱子", "imageMatchRole": "required", "x": 0.83, "y": 0.05, "w": 0.08, "h": 0.04},
                {"title": "返回", "x": 0.05, "y": 0.90, "w": 0.08, "h": 0.04},
            ],
        },
    }
    state = {"page": 260}
    actions: list[str] = []

    class FakeRuntime:
        ctx = {"images": images}

        def get_view(self, view_id):
            return runtime_runner_core.View(images[int(view_id)])

        def goto_view(self, view_id):
            actions.append(f"goto:{view_id}")
            state["page"] = int(view_id)
            if False:
                yield None
            return "success"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(f"wait_click:{view_id}:{shape}")
            if view_id == 34 and shape == "下方菜单/星海":
                state["page"] = 259
            elif view_id == 259 and shape == "异火":
                state["page"] = 260
            elif view_id == 260 and shape == "净莲":
                state["page"] = 261
            elif view_id == 261 and shape == "返回":
                state["page"] = 260
            elif view_id == 260 and shape == "返回":
                state["page"] = 259
            elif view_id == 259 and shape == "返回":
                state["page"] = 34
            if False:
                yield None
            return "success"

        def wait_clicks(self, steps):
            for view_id, shape in steps:
                yield from self.wait_click(view_id, shape)

        def wait_view(self, *view_ids, **kwargs):
            actions.append(f"wait_view:{view_ids}")
            if False:
                yield None
            return runtime_runner_core.View(images[int(view_ids[0])])

        def shape_visible(self, view_id, shape, **kwargs):
            return ("shape", int(view_id), str(shape))

        def view_visible(self, view_id, **kwargs):
            return ("view", int(view_id))

        def ocr_contains(self, **kwargs):
            return ("ocr", kwargs)

        def all_of(self, *conditions, **kwargs):
            return ("all", conditions)

        def wait_any(self, conditions, **kwargs):
            def matched(condition):
                kind = condition[0]
                if kind == "shape":
                    return state["page"] == condition[1]
                if kind == "view":
                    return state["page"] == condition[1]
                if kind == "ocr":
                    return True
                if kind == "all":
                    return all(matched(item) for item in condition[1])
                return False

            for key, condition in conditions.items():
                if matched(condition):
                    if False:
                        yield None
                    return key
            raise AssertionError(f"no fake wait_any condition matched: {conditions}")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(runner, "_screencap", lambda ctx: str(state["page"]))
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame: 97.0 if int(frame) == image["id"] and shape["title"] in {"异火", "净莲", "箱子", "返回"} else 0.0)

    def click_shape(ctx, image, shape, frame=None, match_result=None):
        actions.append(f"click:{image['id']}:{shape['title']}")
        if image["id"] == 259 and shape["title"] == "异火":
            state["page"] = 260
        elif image["id"] == 260 and shape["title"] == "净莲":
            state["page"] = 261
        elif image["id"] == 260 and shape["title"] == "返回":
            state["page"] = 259
        elif image["id"] == 261 and shape["title"] == "返回":
            state["page"] = 260
        elif image["id"] == 259 and shape["title"] == "返回":
            state["page"] = 34

    monkeypatch.setattr(runner, "_click_shape", click_shape)
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_next_time", lambda *args, **kwargs: None)

    result = _drain_generator(
        _run_registered_daily_yihuo(
            runner,
            {"asset_tree_path": tmp_path / "asset-tree.json", "images": images},
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result == "success"
    assert actions == [
        "goto:34",
        "wait_click:34:下方菜单/星海",
        "wait_click:259:异火",
        "wait_click:260:净莲",
        "wait_click:261:箱子",
        "wait_click:261:返回",
        "wait_click:260:返回",
        "wait_click:259:返回",
        "wait_view:(34,)",
    ]


def test_daily_yihuo_box_wait_accepts_already_claimed_detail(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image261 = {
        "id": 261,
        "title": "0261.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "箱子", "imageMatchRole": "required", "x": 0.83, "y": 0.05, "w": 0.08, "h": 0.04},
            {"title": "返回", "imageMatchRole": "required", "x": 0.05, "y": 0.90, "w": 0.08, "h": 0.04},
        ],
    }
    monkeypatch.setattr(runner, "_screencap", lambda ctx: "claimed-frame")
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(
        runner,
        "_shape_score",
        lambda ctx, image, shape, frame: 37.0 if shape["title"] == "箱子" else 90.0,
    )
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: [{"text": "异火 已领取 次日5点刷新 净莲妖火"}],
    )
    runtime = runtime_runner_core.FanxiuRuntime(
        runner,
        {"images": {261: image261}},
        stop_event=fanxiu.threading.Event(),
    )

    result = _drain_generator(
        runtime.wait_any(
            {
                "claimable": runtime.shape_visible(261, "箱子"),
                "claimed": runtime.all_of(
                    runtime.shape_visible(261, "返回"),
                    runtime.ocr_contains(all_of=("已领取",), any_of=("次日5点刷新", "净莲妖火")),
                ),
            },
            timeout=1.0,
        )
    )

    assert result == "claimed"


def test_guard_enabled_service_restarts_on_stale_heartbeat_without_pending_jobs(monkeypatch):
    runner = create_fanxiu_runtime_runner()

    class DeadThread:
        def is_alive(self):
            return True

    runner._service_thread = DeadThread()
    runner._service_heartbeat_at = time.time() - 60
    runner._guard_enabled = True
    runner._guard_group_enabled = True
    runner._guard_interval_seconds = 2
    monkeypatch.setattr(runner, "_pending_manual_job_count", lambda: 0)

    assert runner._service_should_restart_for_pending_jobs_locked() is True


def test_daily_lingzu_go_elder_uses_longer_scene_wait(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            184: {"title": "灵祖挑战详情", "shapes": [{"title": "前往", "x": 0.3, "y": 0.7, "w": 0.2, "h": 0.1}]},
            185: {"title": "灵祖挑战过场", "shapes": [{"title": "跳过", "x": 0.8, "y": 0.04, "w": 0.1, "h": 0.05}]},
            187: {"title": "战灵长老", "shapes": [{"title": "灵祖挑战", "x": 0.4, "y": 0.6, "w": 0.2, "h": 0.1}]},
            188: {"title": "圣雷龙妖祖", "shapes": [{"title": "前往", "x": 0.4, "y": 0.7, "w": 0.2, "h": 0.1}]},
            189: {"title": "灵祖挑战结算", "shapes": [{"title": "点击退出", "x": 0.3, "y": 0.8, "w": 0.3, "h": 0.1}]},
        }
    }
    waits = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 184, 100.0, "frame"

        def ocr_text(self, *_args, **_kwargs):
            return "灵祖挑战 今日剩余次数 1/1"

        def wait_click(self, *_args, **_kwargs):
            if False:
                yield None
            return "success"

        def wait_view_id(self, scene_id, **kwargs):
            waits.append((scene_id, kwargs.get("timeout")))
            if False:
                yield None
            return scene_id, 100.0

        def cur_frame(self, *_args, **_kwargs):
            return "frame"

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_record_daily_lingzu_done", lambda *args, **kwargs: "2026-06-15 05:00:00")
    monkeypatch.setattr(runner, "_safe_return_daily_lingzu_to_world_after_done", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(runner, "_daily_lingzu_remaining_zero", lambda text: True)

    gen = runner._run_daily_lingzu_challenge(ctx, fake_runtime, fanxiu.threading.Event(), {})
    with pytest.raises(StopIteration):
        while True:
            next(gen)

    assert (187, 45.0) in waits
    assert (188, 30.0) in waits


def test_daily_lingzu_can_resume_from_elder_when_global_scene_unknown(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            69: {"title": "日常"},
            183: {"title": "灵祖活动列表"},
            184: {"title": "灵祖挑战详情"},
            185: {"title": "灵祖挑战过场"},
            186: {"title": "灵祖奖励浮层"},
            187: {"title": "战灵长老"},
            188: {"title": "圣雷龙妖祖"},
            189: {"title": "灵祖挑战结算"},
        },
    }
    called = []

    class FakeRuntime:
        def ocr_text(self, frame=None, **kwargs):
            return "灵祖挑战 每日能够挑战一次妖灵之祖 灵祖魂息"

        def current_scene(self, views=None, **kwargs):
            return 187, 100.0, "frame"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_daily_lingzu_discovered_next_time_is_future", lambda payload: None)
    monkeypatch.setattr(runner, "_current_scene_number", lambda ctx: (None, 0.0, "frame"))

    def run_challenge(ctx, runtime, stop_event, payload):
        called.append(True)
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_run_daily_lingzu_challenge", run_challenge)

    gen = runner._execute_daily_lingzu_task(ctx, fanxiu.threading.Event(), {})
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert called == [True]


def test_daily_lingzu_return_uses_lingzu_scene_fallback_from_boss(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            69: {"title": "日常", "shapes": [{"title": "退出", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            183: {"title": "灵祖活动列表", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            184: {"title": "灵祖挑战详情", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            187: {"title": "战灵长老", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            188: {"title": "圣雷龙妖祖", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    clicked = []
    current_scenes = iter([(None, 0.0, "boss-frame"), (34, 100.0, "world-frame")])

    def wait_return_scene(ctx, stop_event, scene_ids, **kwargs):
        if False:
            yield None
        return (183, 100.0)

    def wait_settle(*args, **kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_current_scene_number", lambda ctx: next(current_scenes))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number",
        lambda ctx, frame, preferred=None: (preferred[0], 100.0) if preferred else (34, 100.0),
    )
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "圣雷龙妖祖 剩余奖励次数：0/1 前往 快速挑战"}])
    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, image, *args, **kwargs: clicked.append(image["title"]))
    monkeypatch.setattr(runner, "_wait_scene_id", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_wait_daily_lingzu_return_scene", wait_return_scene)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", wait_settle)
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_ensure_daily_lingzu_outer_world", lambda *args, **kwargs: iter(()))

    gen = runner._return_daily_lingzu_to_world(ctx, fanxiu.threading.Event())
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert clicked[:3] == ["圣雷龙妖祖", "战灵长老", "灵祖活动列表"]


def test_daily_lingzu_return_fails_when_reward_popup_has_no_close_shape(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            69: {"title": "日常", "shapes": [{"title": "退出", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            183: {"title": "灵祖活动列表", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            184: {"title": "灵祖挑战详情", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            186: {"title": "灵祖奖励浮层", "shapes": [{"title": "奖励浮层标识", "x": 0.49, "y": 0.58, "w": 0.31, "h": 0.22}]},
            187: {"title": "战灵长老", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            188: {"title": "圣雷龙妖祖", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
        },
    }

    monkeypatch.setattr(runner, "_current_scene_number", lambda ctx: (34, 100.0, "reward-frame"))
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "百脉宝魄 点击查看"}])

    gen = runner._return_daily_lingzu_to_world(ctx, fanxiu.threading.Event())
    with pytest.raises(RuntimeError, match="#186 奖励浮层缺少"):
        while True:
            next(gen)


def test_scheduler_preflight_ignores_reward_popup_words_without_context(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            186: {"title": "灵祖奖励浮层", "shapes": [{"title": "奖励浮层标识"}]},
        },
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "reward-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "百脉宝魄 点击查看"}])

    assert runner._known_blocking_overlay_message(ctx) is None


def test_scheduler_preflight_does_not_promote_reward_popup_to_global_blocker(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            186: {"title": "灵祖奖励浮层", "shapes": [{"title": "关闭"}]},
        },
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "reward-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "百脉宝魄 点击查看"}])

    assert runner._known_blocking_overlay_message(ctx) is None


def test_scheduler_preflight_ignores_world_activity_reward_words(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            186: {"title": "灵祖奖励浮层", "shapes": [{"title": "奖励浮层标识"}]},
        },
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "world-frame")
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: [
            {"text": "百脉宝魄O", "x": 530, "y": 990, "w": 331, "h": 34},
            {"text": "点击查看", "x": 519, "y": 1200, "w": 134, "h": 39},
            {"text": "角色 装备 星海 功法书", "x": 400, "y": 1526, "w": 300, "h": 45},
        ],
    )

    assert runner._known_blocking_overlay_message(ctx) is None


def test_scheduler_preflight_reports_game_announcement_without_close_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree": [
            {
                "type": "folder",
                "title": "登录",
                "children": [
                    {
                        "type": "image",
                        "title": "游戏公告",
                        "shapes": [{"title": "公告"}],
                    }
                ],
            }
        ],
        "images": {},
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "announcement-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "游戏公告 更新公告 风险提醒"}])

    info = runner._known_blocking_overlay_info(ctx)

    assert info == {
        "scene_id": None,
        "title": "游戏公告",
        "blocking": True,
        "all_shapes": ["公告"],
        "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，无法安全进入游戏",
    }


def test_scheduler_preflight_allows_game_announcement_with_close_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree": [
            {
                "type": "folder",
                "title": "登录",
                "children": [
                    {
                        "type": "image",
                        "title": "游戏公告",
                        "shapes": [{"title": "关闭公告", "x": 0.9, "y": 0.1, "w": 0.05, "h": 0.05}],
                    }
                ],
            }
        ],
        "images": {},
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "announcement-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "游戏公告 更新公告 风险提醒"}])

    info = runner._known_blocking_overlay_info(ctx)

    assert info == {
        "scene_id": None,
        "title": "游戏公告",
        "blocking": False,
        "all_shapes": ["关闭公告"],
        "action_shapes": ["关闭公告"],
        "message": "检测到游戏公告遮挡，已有安全关闭动作标注",
    }
    assert runner._known_blocking_overlay_message(ctx) is None


def test_scheduler_preflight_does_not_infer_game_announcement_action_from_jump_target(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree": [
            {
                "type": "folder",
                "title": "登录",
                "children": [
                    {
                        "type": "image",
                        "title": "游戏公告",
                        "shapes": [{"title": "公告", "sceneJumpTarget": "18", "x": 0.2, "y": 0.1, "w": 0.1, "h": 0.05}],
                    }
                ],
            }
        ],
        "images": {},
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "announcement-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "游戏公告 更新公告 风险提醒"}])

    info = runner._known_blocking_overlay_info(ctx)

    assert info == {
        "scene_id": None,
        "title": "游戏公告",
        "blocking": True,
        "all_shapes": ["公告"],
        "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，无法安全进入游戏",
    }
    assert runner._known_blocking_overlay_message(ctx) == "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，无法安全进入游戏"


def test_runtime_clears_known_game_announcement_with_safe_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree": [
            {
                "type": "folder",
                "title": "登录",
                "children": [
                    {
                        "type": "image",
                        "title": "游戏公告",
                        "shapes": [{"title": "关闭公告", "x": 0.9, "y": 0.1, "w": 0.05, "h": 0.05}],
                    }
                ],
            }
        ],
        "images": {},
    }
    frames = iter(["announcement-frame", "click-frame", "clean-frame"])
    clicked = []

    def wait_settle(*args, **kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: next(frames))
    monkeypatch.setattr(
        runner,
        "_ocr_lines",
        lambda frame: [{"text": "游戏公告 更新公告 风险提醒"}] if frame != "clean-frame" else [{"text": "世界 储物袋 角色"}],
    )
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, image, *args, **kwargs: clicked.append(image["title"]))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", wait_settle)

    gen = runner._clear_known_blocking_overlay_if_possible(ctx, fanxiu.threading.Event(), label="Scheduler")
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value is True
    assert clicked == [("游戏公告", "关闭公告")]


def test_daily_lingzu_reward_popup_cleanup_failure_is_not_marked_done(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            69: {"title": "日常", "shapes": [{"title": "退出", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            183: {"title": "灵祖活动列表", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            184: {"title": "灵祖挑战详情", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            185: {"title": "灵祖挑战过场"},
            186: {"title": "灵祖奖励浮层", "shapes": [{"title": "奖励浮层标识", "x": 0.49, "y": 0.58, "w": 0.31, "h": 0.22}]},
            187: {"title": "战灵长老", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            188: {"title": "圣雷龙妖祖", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            189: {"title": "灵祖挑战结算"},
        },
    }
    recorded = []

    class FakeRuntime:
        def ocr_text(self, frame=None, **kwargs):
            return "百脉宝魄 点击查看"

        def current_scene(self, views=None, **kwargs):
            return 34, 100.0, "reward-frame"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_daily_lingzu_discovered_next_time_is_future", lambda payload: None)
    monkeypatch.setattr(runner, "_current_scene_number", lambda ctx: (34, 100.0, "reward-frame"))
    monkeypatch.setattr(runner, "_record_daily_lingzu_done", lambda *args, **kwargs: recorded.append(kwargs))

    gen = runner._execute_daily_lingzu_task(ctx, fanxiu.threading.Event(), {"__scheduler_task_id": "legacy-daily-lingzu"})
    with pytest.raises(RuntimeError, match="#186 奖励浮层缺少"):
        while True:
            next(gen)

    assert recorded == []


def test_daily_lingzu_return_closes_reward_popup_before_success(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            69: {"title": "日常", "shapes": [{"title": "退出", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            183: {"title": "灵祖活动列表", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            184: {"title": "灵祖挑战详情", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            186: {"title": "灵祖奖励浮层", "shapes": [{"title": "关闭", "x": 0.72, "y": 0.58, "w": 0.08, "h": 0.08}]},
            187: {"title": "战灵长老", "shapes": [{"title": "空白", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.05}]},
            188: {"title": "圣雷龙妖祖", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
        },
    }
    current_scenes = iter([(34, 100.0, "reward-frame"), (34, 100.0, "clean-frame")])
    frames = iter(["reward-frame", "clean-frame", "clean-frame"])
    clicked = []

    def wait_settle(*args, **kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_current_scene_number", lambda ctx: next(current_scenes))
    monkeypatch.setattr(runner, "_screencap", lambda ctx: next(frames))
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "百脉宝魄 点击查看"}] if frame == "reward-frame" else [{"text": "世界 储物袋 角色"}])
    monkeypatch.setattr(runner, "_click_frame_point", lambda ctx, image, *args, **kwargs: clicked.append(image["title"]))
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (34, 100.0))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", wait_settle)
    monkeypatch.setattr(runner, "_ensure_daily_lingzu_outer_world", lambda *args, **kwargs: iter(()))

    gen = runner._return_daily_lingzu_to_world(ctx, fanxiu.threading.Event())
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert clicked == ["灵祖奖励浮层"]


def test_daily_lingzu_outer_world_confirms_leave_dialog(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            34: {"title": "世界"},
            85: {"title": "某区域内部", "shapes": [{"title": "离开", "x": 0.8, "y": 0.7, "w": 0.1, "h": 0.05}]},
            86: {"title": "离开场景", "shapes": [{"title": "确认", "x": 0.6, "y": 0.7, "w": 0.1, "h": 0.05}]},
        }
    }
    frames = iter(["confirm-frame", "world-frame"])
    actions: list[tuple] = []

    class FakeRuntime:
        def ocr_text(self, frame=None, **kwargs):
            actions.append(("ocr_text", frame, kwargs))
            if kwargs.get("update"):
                return "社团管事 创建队伍 加入队伍 离开"
            if frame == "confirm-frame":
                return "提示 是否离开当前场景 取消 确认"
            return "世界 储物袋 角色 装备 功法书"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, frame))
            if frame == "confirm-frame":
                return 86, 100.0, frame
            return 34, 100.0, frame

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    gen = runner._ensure_daily_lingzu_outer_world(ctx, fanxiu.threading.Event())
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert actions == [
        ("ocr_text", None, {"update": True}),
        ("wait_click", 85, "离开", {}),
        ("current_scene", (34, 86, 204, 69), {"update": True}, "confirm-frame"),
        ("ocr_text", "confirm-frame", {}),
        ("wait_click", 86, "确认", {}),
        ("settle", 2.0),
        ("current_scene", (34, 86, 204, 69), {"update": True}, "world-frame"),
        ("ocr_text", "world-frame", {}),
    ]


def test_daily_lingzu_outer_world_unwinds_assistant_after_leave_confirm(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            34: {"title": "世界"},
            69: {"title": "日常", "shapes": [{"title": "退出", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
            85: {"title": "某区域内部", "shapes": [{"title": "离开", "x": 0.8, "y": 0.7, "w": 0.1, "h": 0.05}]},
            86: {"title": "离开场景", "shapes": [{"title": "确认", "x": 0.6, "y": 0.7, "w": 0.1, "h": 0.05}]},
            204: {"title": "小助手清单", "shapes": [{"title": "返回", "x": 0.1, "y": 0.9, "w": 0.1, "h": 0.05}]},
        }
    }
    frames = iter(["confirm-frame", "assistant-frame", "daily-frame", "world-frame"])
    actions: list[tuple] = []

    class FakeRuntime:
        def ocr_text(self, frame=None, **kwargs):
            actions.append(("ocr_text", frame, kwargs))
            if kwargs.get("update"):
                return "社团管事 创建队伍 加入队伍 离开"
            return "提示 是否离开当前场景" if frame == "confirm-frame" else "世界"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, frame))
            return {
                "confirm-frame": (86, 100.0, frame),
                "assistant-frame": (204, 100.0, frame),
                "daily-frame": (69, 100.0, frame),
                "world-frame": (34, 100.0, frame),
            }.get(frame, (None, 0.0, frame))

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_screencap", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))
    monkeypatch.setattr(runner, "_click_shape", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should use runtime")))

    gen = runner._ensure_daily_lingzu_outer_world(ctx, fanxiu.threading.Event())
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert actions == [
        ("ocr_text", None, {"update": True}),
        ("wait_click", 85, "离开", {}),
        ("current_scene", (34, 86, 204, 69), {"update": True}, "confirm-frame"),
        ("ocr_text", "confirm-frame", {}),
        ("wait_click", 86, "确认", {}),
        ("settle", 2.0),
        ("current_scene", (34, 86, 204, 69), {"update": True}, "assistant-frame"),
        ("ocr_text", "assistant-frame", {}),
        ("wait_click", 204, "返回", {}),
        ("settle", 2.0),
        ("current_scene", (34, 86, 204, 69), {"update": True}, "daily-frame"),
        ("ocr_text", "daily-frame", {}),
        ("wait_click", 69, "退出", {}),
        ("settle", 2.0),
        ("current_scene", (34, 86, 204, 69), {"update": True}, "world-frame"),
        ("ocr_text", "world-frame", {}),
    ]


def test_data_annotation_runtime_status_overlays_active_resident_owner(tmp_path, monkeypatch):
    runtime_state_path = tmp_path / "runtime_state.json"
    world_facts_path = tmp_path / "world_facts.json"
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    fanxiu_behavior_tree.persist_fanxiu_runtime_status(
        {
            "status": "idle",
            "phase": "service_owned_by_other",
            "running": False,
            "service_running": False,
            "message": "行为树执行器已由后端进程 36500 持有：scheduler_poll",
            "logs": [],
        },
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    owner_path.write_text(
        json.dumps(
            {
                "pid": 53420,
                "token": "token",
                "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
                "step": "scheduler_poll",
                "updated_at": 1781374327.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_state_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: world_facts_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_behavior_tree_service_owner_path", lambda: owner_path)
    monkeypatch.setattr(fanxiu_behavior_tree.time, "time", lambda: 1781374330.0)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_exists", lambda pid: True)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_matches_service_owner", lambda pid: True)

    status = fanxiu_behavior_tree.fanxiu_data_annotation_runtime_status()

    assert status["service_running"] is True
    assert status["phase"] == "scheduler_poll"
    assert "53420" in status["message"]


def test_data_annotation_runtime_status_preserves_scheduler_blocked_phase(tmp_path, monkeypatch):
    runtime_state_path = tmp_path / "runtime_state.json"
    world_facts_path = tmp_path / "world_facts.json"
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    blocker_message = "检测到游戏公告遮挡"
    fanxiu_behavior_tree.persist_fanxiu_runtime_status(
        {
            "status": "idle",
            "phase": "scheduler_blocked",
            "running": False,
            "service_running": True,
            "message": blocker_message,
            "blocking_overlays": [{"title": "游戏公告", "blocking": True, "message": blocker_message}],
            "logs": [],
        },
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    owner_path.write_text(
        json.dumps(
            {
                "pid": 53420,
                "token": "token",
                "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
                "step": "scheduler_poll",
                "updated_at": 1781374327.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_state_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: world_facts_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_behavior_tree_service_owner_path", lambda: owner_path)
    monkeypatch.setattr(fanxiu_behavior_tree.time, "time", lambda: 1781374330.0)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_exists", lambda pid: True)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_matches_service_owner", lambda pid: True)

    status = fanxiu_behavior_tree.fanxiu_data_annotation_runtime_status()

    assert status["service_running"] is True
    assert status["phase"] == "scheduler_blocked"
    assert status["message"] == blocker_message


def test_data_annotation_runtime_status_uses_persisted_scheduler_block_when_live_poll_overwrites(tmp_path, monkeypatch):
    runtime_state_path = tmp_path / "runtime_state.json"
    world_facts_path = tmp_path / "world_facts.json"
    blocker_message = "检测到游戏公告遮挡"
    fanxiu_behavior_tree.persist_fanxiu_runtime_status(
        {
            "status": "idle",
            "phase": "scheduler_blocked",
            "running": False,
            "service_running": True,
            "message": blocker_message,
            "blocking_overlays": [{"title": "游戏公告", "blocking": True, "message": blocker_message}],
            "last_scheduler_block_message": blocker_message,
            "logs": [],
        },
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )

    class PollingRunner:
        guard_definitions = {}

        def status(self):
            return {
                "status": "idle",
                "phase": "scheduler_poll",
                "running": False,
                "service_running": True,
                "message": "行为树常驻服务运行中",
                "logs": [],
            }

    monkeypatch.setattr(fanxiu_behavior_tree, "get_fanxiu_runtime_runner", lambda: PollingRunner())
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_state_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: world_facts_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "read_fanxiu_behavior_tree_service_owner", lambda: {
        "active": True,
        "stale": False,
        "pid": os.getpid(),
        "step": "scheduler_poll",
    })
    monkeypatch.setattr(fanxiu_behavior_tree.time, "time", lambda: 1781374330.0)

    status = fanxiu_behavior_tree.fanxiu_data_annotation_runtime_status()

    assert status["service_running"] is True
    assert status["phase"] == "scheduler_blocked"
    assert status["message"] == blocker_message
    assert status["blocking_overlays"][0]["title"] == "游戏公告"


def test_data_annotation_runtime_status_preserves_persisted_logs_from_active_owner(tmp_path, monkeypatch):
    runtime_state_path = tmp_path / "runtime_state.json"
    world_facts_path = tmp_path / "world_facts.json"
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    fanxiu_behavior_tree.persist_fanxiu_runtime_status(
        {
            "status": "success",
            "phase": "done",
            "running": False,
            "service_running": True,
            "message": "手动作业完成：单步识别",
            "current_task_id": "",
            "logs": [
                {
                    "time": "03:15:51",
                    "kind": "success",
                    "scope": "manual_job",
                    "item_id": "manual_job",
                    "message": "[manual-1] 手动作业完成：单步识别",
                }
            ],
        },
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    owner_path.write_text(
        json.dumps(
            {
                "pid": os.getpid() + 1000,
                "token": "token",
                "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
                "step": "scheduler_poll",
                "updated_at": 1781374330.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_state_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: world_facts_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_behavior_tree_service_owner_path", lambda: owner_path)
    monkeypatch.setattr(fanxiu_behavior_tree.time, "time", lambda: 1781374331.0)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_exists", lambda pid: True)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_matches_service_owner", lambda pid: True)

    status = fanxiu_behavior_tree.fanxiu_data_annotation_runtime_status()

    assert status["service_running"] is True
    assert status["logs"][-1]["message"] == "[manual-1] 手动作业完成：单步识别"


def test_data_annotation_runtime_status_clears_missing_owner_overlay(tmp_path, monkeypatch):
    runtime_state_path = tmp_path / "runtime_state.json"
    world_facts_path = tmp_path / "world_facts.json"
    owner_path = tmp_path / "behavior_tree_service_owner.json"
    fanxiu_behavior_tree.persist_fanxiu_runtime_status(
        {
            "status": "idle",
            "phase": "service_owned_by_other",
            "running": False,
            "service_running": False,
            "message": "行为树执行器已由后端进程 11336 持有：scheduler_poll",
            "logs": [],
        },
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
    owner_path.write_text(
        json.dumps(
            {
                "pid": 11336,
                "token": "token",
                "entry_id": "30b82d72-8a76-4a74-be4b-4fc1591c6ce2",
                "step": "scheduler_poll",
                "updated_at": 1781374330.0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: runtime_state_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: world_facts_path)
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_behavior_tree_service_owner_path", lambda: owner_path)
    monkeypatch.setattr(fanxiu_behavior_tree.time, "time", lambda: 1781374331.0)
    monkeypatch.setattr(fanxiu_behavior_tree, "_fanxiu_process_exists", lambda pid: False)

    status = fanxiu_behavior_tree.fanxiu_data_annotation_runtime_status()

    assert status["service_running"] is False
    assert status["phase"] == "idle"
    assert "常驻服务未运行" in status["message"]


def test_data_annotation_manual_job_submit_is_queue_mediator(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER
    task_type = "codex_queue_probe"

    @fanxiu.register_fanxiu_data_annotation_manual_job(
        task_type,
        "队列探针",
        interruptible=False,
        normalize_payload=lambda payload: {**payload, "queued_by": "registry"},
    )
    def queue_probe(runner, ctx, payload, stop_event):
        return "success"

    def fake_ensure_service(**kwargs):
        with runner._lock:
            runner._status["service_running"] = True
            runner._status["entry_id"] = kwargs["entry_id"]
        return runner.status()

    def fail_start_manual_runtime_task(**_kwargs):
        raise AssertionError("submit must only enqueue; resident loop consumes later")

    monkeypatch.setattr(runner, "ensure_service", fake_ensure_service)
    monkeypatch.setattr(runner, "start_manual_runtime_task", fail_start_manual_runtime_task)

    try:
        status = fanxiu._submit_data_annotation_manual_job(
            entry=object(),
            entry_id="entry",
            task_type=task_type,
            payload={"value": 1},
        )
        jobs = fanxiu._read_data_annotation_manual_jobs()

        assert status["running"] is False
        assert status["phase"] == "manual_job_queued"
        assert status["queued_job"]["id"] == jobs[0]["id"]
        assert status["queued_job"]["task_type"] == task_type
        assert jobs == [
            {
                **jobs[0],
                "task_type": task_type,
                "label": "队列探针",
                "interruptible": False,
                "payload": {"value": 1, "queued_by": "registry"},
            }
        ]
    finally:
        fanxiu._DATA_ANNOTATION_MANUAL_JOB_REGISTRY.pop(task_type, None)


def test_data_annotation_run_now_gift_code_executes_through_runtime_thread(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runtime_control.set_scheduler_job_group_enabled(False, scheduler_settings_path=_scheduler_settings_path(tmp_path))
    asset_tree_path = tmp_path / "entry.json"
    asset_tree_path.write_text(json.dumps([
        {"type": "image", "id": "49", "title": "#49 设置页", "filename": "0049.png", "shapes": []},
        {"type": "image", "id": "78", "title": "#78 兑换礼包", "filename": "0078.png", "shapes": []},
    ], ensure_ascii=False), encoding="utf-8")
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "每周礼包码",
            "source": "manual",
            "schedule_kind": "manual",
            "legacy_name": "",
            "enabled": False,
            "priority": 40,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": []},
            "checkpoint": None,
        }
    ])
    runner = create_fanxiu_runtime_runner()
    executed: list[list[str]] = []

    def fake_require_assets(ctx):
        return None

    def fake_execute_gift_code_task(ctx, codes, stop_event):
        executed.append(list(codes))

    monkeypatch.setattr(runner, "_require_assets", fake_require_assets)
    monkeypatch.setattr(runner, "_execute_gift_code_task", fake_execute_gift_code_task)
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", runner)

    response = fanxiu.run_now_fanxiu_data_annotation_scheduler_task(
        fanxiu.FanxiuDataAnnotationSchedulerRunNowRequest(
            entry_id="entry",
            task_id="gift-code-weekly",
            payload={"codes": [" 煮梅消夏 ", ""]},
        ),
        current_user=object(),
        session=object(),
    )

    deadline = fanxiu.time.time() + 2.0
    while fanxiu.time.time() < deadline and not executed:
        fanxiu.time.sleep(0.05)
    assert runner.wait_until_idle(2.0) is True
    status = runner.status()
    persisted_status = {}
    deadline = fanxiu.time.time() + 2.0
    while fanxiu.time.time() < deadline:
        try:
            persisted_status = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            fanxiu.time.sleep(0.05)
            continue
        if persisted_status.get("status") == "success":
            break
        fanxiu.time.sleep(0.05)
    persisted_tasks = fanxiu._read_data_annotation_scheduler_tasks()
    persisted_task = next(item for item in persisted_tasks if item["id"] == "gift-code-weekly")

    assert executed == [["煮梅消夏"]]
    assert status["running"] is False
    assert status["status"] == "success"
    assert status["task_type"] == ""
    assert persisted_status["status"] == "success"
    assert persisted_task["last_result"] == "success"
    assert persisted_task["payload"]["codes"] == []


def test_data_annotation_run_now_rejects_unverified_task_type(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-mozu",
            "task_type": "legacy_daily_task",
            "label": "日常 游历",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
                "legacy_name": "日常_魔祖",
            "enabled": True,
            "priority": 120,
            "interruptible": True,
            "next_time": None,
            "schedule_times": ["05:00"],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
                "payload": {"legacy_name": "日常_魔祖"},
            "checkpoint": None,
        }
    ])

    with pytest.raises(fanxiu.HTTPException) as exc_info:
        fanxiu.run_now_fanxiu_data_annotation_scheduler_task(
            fanxiu.FanxiuDataAnnotationSchedulerRunNowRequest(
                entry_id="entry",
                task_id="legacy-daily-mozu",
                payload={},
            ),
            current_user=object(),
            session=object(),
        )

    assert exc_info.value.status_code == 400
    assert "尚未纳入当前框架验收" in str(exc_info.value.detail)


def test_data_annotation_run_due_endpoint_skips_legacy_placeholders(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    disabled_signup = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    disabled_signup["enabled"] = False
    disabled_mail = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "mail-cleanup").copy()
    disabled_mail["enabled"] = False
    disabled_assistant = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-assistant").copy()
    disabled_assistant["enabled"] = False
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-mozu",
            "task_type": "legacy_daily_task",
            "label": "日常 魔祖",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_魔祖",
            "enabled": True,
            "priority": 10,
            "interruptible": True,
            "next_time": None,
            "schedule_times": ["12:29"],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_魔祖"},
            "checkpoint": None,
        },
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "每周礼包码",
            "source": "manual",
            "schedule_kind": "manual",
            "legacy_name": "",
            "enabled": True,
            "priority": 40,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"codes": ["煮梅消夏"]},
            "checkpoint": None,
        },
        disabled_signup,
        disabled_mail,
        disabled_assistant,
    ])
    response = fanxiu.run_due_fanxiu_data_annotation_scheduler_tasks(
        fanxiu.FanxiuDataAnnotationSchedulerRunDueRequest(entry_id="entry"),
        current_user=object(),
        session=object(),
    )

    assert response.service_running is True
    assert response.running is False
    assert response.message == "没有可执行的到期任务"


def test_data_annotation_run_due_endpoint_reports_no_executable_due_tasks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    disabled_signup = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    disabled_signup["enabled"] = False
    disabled_mail = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "mail-cleanup").copy()
    disabled_mail["enabled"] = False
    disabled_assistant = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-assistant").copy()
    disabled_assistant["enabled"] = False
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-mozu",
            "task_type": "legacy_daily_task",
            "label": "日常 魔祖",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_魔祖",
            "enabled": True,
            "priority": 10,
            "interruptible": True,
            "next_time": None,
            "schedule_times": ["12:29"],
            "window": None,
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 0,
            "payload": {"legacy_name": "日常_魔祖"},
            "checkpoint": None,
        },
        disabled_signup,
        disabled_mail,
        disabled_assistant,
    ])

    response = fanxiu.run_due_fanxiu_data_annotation_scheduler_tasks(
        fanxiu.FanxiuDataAnnotationSchedulerRunDueRequest(entry_id="entry"),
        current_user=object(),
        session=object(),
    )

    assert response.running is False
    assert response.message == "没有可执行的到期任务"


def test_data_annotation_guard_endpoint_persists_switch_state(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)

    def fake_set_guard(**kwargs):
        return {
            "ok": True,
            "running": False,
            "guard_enabled": kwargs["enabled"],
            "guard_running": kwargs["enabled"],
            "guard_entry_id": kwargs["entry_id"] if kwargs["enabled"] else "",
            "guard_interval_seconds": kwargs["interval_seconds"],
            "status": "idle",
            "entry_id": kwargs["entry_id"],
            "message": "guard set",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._DATA_ANNOTATION_RUNTIME_RUNNER, "set_guard", fake_set_guard)

    response = fanxiu.set_fanxiu_data_annotation_runtime_guard(
        fanxiu.FanxiuDataAnnotationRuntimeGuardRequest(entry_id="entry", enabled=True, interval_seconds=3),
        current_user=object(),
        session=object(),
    )
    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))

    assert response.guard_enabled is True
    assert response.guard_running is True
    assert response.guard_entry_id == "entry"
    assert persisted["guard_enabled"] is True
    assert persisted["guard_interval_seconds"] == 3


def test_data_annotation_guard_group_endpoint_persists_switch_state(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)

    def fake_set_guard_group_enabled(**kwargs):
        return {
            "ok": True,
            "running": False,
            "guard_group_enabled": kwargs["enabled"],
            "guard_group_running": False,
            "guard_enabled": True,
            "guard_running": False,
            "guard_entry_id": kwargs["entry_id"],
            "guard_interval_seconds": 2,
            "status": "idle",
            "entry_id": kwargs["entry_id"],
            "message": "guard group set",
            "guard_items": {"close_popups": {"id": "close_popups", "enabled": True}},
            "logs": [],
        }

    monkeypatch.setattr(runtime_control, "set_fanxiu_runtime_guard_group_enabled", fake_set_guard_group_enabled)

    response = fanxiu.set_fanxiu_data_annotation_runtime_guard_group(
        fanxiu.FanxiuDataAnnotationRuntimeGuardGroupRequest(entry_id="entry", enabled=False),
        current_user=object(),
        session=object(),
    )
    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))

    assert response.guard_group_enabled is False
    assert response.guard_enabled is True
    assert persisted["guard_group_enabled"] is False
    assert persisted["guard_items"]["close_popups"]["enabled"] is True


def test_data_annotation_runtime_status_corrects_stale_running_after_backend_reload(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    stale_status = {
        "ok": True,
        "running": True,
        "guard_enabled": True,
        "guard_running": True,
        "guard_entry_id": "entry",
        "status": "running",
        "entry_id": "entry",
        "task_type": "gift_code_redeem",
        "current_task": "兑换礼包码",
        "phase": "process_code",
        "message": "处理中",
        "logs": [{"time": "00:00:01", "kind": "info", "message": "旧日志"}],
        "started_at": 1,
        "updated_at": 1,
    }
    fanxiu._write_data_annotation_json(tmp_path / "runtime_state.json", stale_status)
    monkeypatch.setattr(fanxiu, "_DATA_ANNOTATION_RUNTIME_RUNNER", create_fanxiu_runtime_runner())

    status = fanxiu._data_annotation_runtime_status()
    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))

    assert status["running"] is False
    assert status["guard_enabled"] is True
    assert status["guard_running"] is False
    assert status["service_running"] is False
    assert status["status"] == "stopped"
    assert status["message"] == "后端已重载，运行状态已结束"
    assert any(item["message"] == "旧日志" for item in status["logs"])
    assert persisted["running"] is False
    assert persisted["guard_enabled"] is True


def test_data_annotation_runtime_stop_only_targets_current_task_not_resident_service(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    stop_event = fanxiu.threading.Event()
    fake_thread = type("AliveThread", (), {"is_alive": lambda self: True})()
    runner._service_thread = fake_thread
    runner._stop_event = stop_event
    with runner._lock:
        runner._status.update({
            "entry_id": "entry",
            "running": True,
            "status": "running",
            "message": "任务执行中",
        })

    status = runner.stop_current_task("entry")

    assert stop_event.is_set()
    assert status["running"] is True
    assert status["status"] == "stopping"
    assert status["service_running"] is True
    assert status["message"] == "当前任务停止请求已发送"

    with runner._lock:
        runner._status.update({"running": False, "status": "success"})

    idle_status = runner.stop_current_task("entry")

    assert idle_status["running"] is False
    assert idle_status["status"] == "idle"
    assert idle_status["service_running"] is True
    assert idle_status["message"] == "当前没有正在运行的任务"


def test_data_annotation_runtime_control_wake_service_sets_wake_event(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    control_path = tmp_path / "behavior_tree_control.json"
    control_path.write_text(
        json.dumps({
            "id": "wake-1",
            "command": "wake_service",
            "entry_id": "entry",
            "reason": "test",
            "created_at": 123.0,
        }),
        encoding="utf-8",
    )

    runner._consume_service_control_request()

    assert runner._service_wake_event.is_set()
    assert not control_path.exists()
    assert any("wake_service" in str(item.get("message") or "") for item in runner.status().get("logs") or [])


def test_data_annotation_direct_runtime_task_runs_inline_and_persists_status(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_runtime_guard_service_tick", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_clear_known_blocking_overlay_if_possible", _no_blocking_overlay_generator)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda *_args, **_kwargs: "success")
    monkeypatch.setattr(runner, "_run_runtime_behavior_tree", lambda *args, **kwargs: kwargs["action"]())

    status = runner.start_runtime_task(
        entry=object(),
        entry_id="entry",
        task_type="hide_floating_window",
        payload={},
        asset_tree_path=tmp_path / "entry.json",
    )

    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))
    facts = json.loads((tmp_path / "world_facts.json").read_text(encoding="utf-8"))

    assert status["running"] is False
    assert not hasattr(runner, "_thread")
    assert persisted["running"] is False
    assert persisted["status"] == "success"
    assert persisted["task_type"] == ""
    assert facts["runtime"]["running"] is False
    assert facts["runtime"]["task_type"] == ""


def test_local_runtime_task_isolates_job_group_and_releases_lock(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_runtime_guard_service_tick", lambda *_args, **_kwargs: fanxiu.BehaviorTreeStatus.SKIP)

    def fake_execute(_ctx, task_type, payload, _stop_event):
        assert task_type == "hide_floating_window"
        assert payload["__local_run"] is True
        assert runner._job_group_isolated() is True
        return "success"

    monkeypatch.setattr(runner, "_execute_runtime_task", fake_execute)

    status = runner.start_local_runtime_task(
        entry=object(),
        entry_id="entry",
        task_type="hide_floating_window",
        payload={},
        asset_tree_path=tmp_path / "entry.json",
    )

    assert status["running"] is False
    assert status["status"] == "success"
    assert not (tmp_path / "job_group_isolation.json").exists()


def test_local_runtime_task_phase_skips_close_popup_guard(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    with runner._lock:
        runner._status["phase"] = "local_run"
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: (_ for _ in ()).throw(AssertionError("local_run should not run guard screencap")))

    result = runner._runtime_guard_service_tick(
        "close_popups",
        {"images": {}},
        tmp_path / "entry.json",
        fanxiu.threading.Event(),
    )

    assert result == fanxiu.BehaviorTreeStatus.SKIP


def test_due_scheduler_is_skipped_when_job_group_isolated(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "mail-cleanup",
        "task_type": "mail_cleanup",
        "label": "邮件_清理",
        "schedule_kind": "daily",
        "enabled": True,
        "interruptible": True,
        "payload": {},
        "schedule_times": ["00:00"],
        "next_time": "2000-01-01 00:00:00",
        "last_result": "",
        "checkpoint": {"manual_inspection_note": "旧人工备注：今日按成功处理"},
    }
    fanxiu._write_data_annotation_scheduler_tasks([task])
    runner._acquire_job_group_isolation(reason="test")

    started = runner._start_due_scheduler_tasks_if_idle(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "entry.json",
    )

    assert started is False


def test_due_scheduler_is_skipped_when_job_group_disabled(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    runtime_control.set_scheduler_job_group_enabled(False, scheduler_settings_path=_scheduler_settings_path(tmp_path))
    task = {
        "id": "mail-cleanup",
        "task_type": "mail_cleanup",
        "label": "邮件_清理",
        "schedule_kind": "daily",
        "enabled": True,
        "interruptible": True,
        "payload": {},
        "schedule_times": ["00:00"],
        "next_time": "2000-01-01 00:00:00",
        "last_result": "",
        "checkpoint": {"manual_inspection_note": "旧人工备注：今日按成功处理"},
    }
    fanxiu._write_data_annotation_scheduler_tasks([task])
    monkeypatch.setattr(
        runner,
        "start_scheduler_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("disabled job group must not start due tasks")),
    )

    started = runner._start_due_scheduler_tasks_if_idle(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "entry.json",
    )

    assert started is False


def test_due_scheduler_is_blocked_by_unclearable_overlay_before_start(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "mail-cleanup",
        "task_type": "mail_cleanup",
        "label": "邮件_清理",
        "schedule_kind": "daily",
        "enabled": True,
        "interruptible": True,
        "payload": {},
        "schedule_times": ["00:00"],
        "next_time": "2000-01-01 00:00:00",
        "last_result": "",
        "checkpoint": {"manual_inspection_note": "旧人工备注：今日按成功处理"},
    }
    fanxiu._write_data_annotation_scheduler_tasks([task])
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(
        runner,
        "_known_blocking_overlay_info",
        lambda _ctx: {
            "scene_id": 186,
            "title": "灵祖奖励浮层",
            "blocking": True,
            "message": "检测到灵祖奖励浮层；缺少关闭动作标注",
        },
    )
    monkeypatch.setattr(
        runner,
        "start_scheduler_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("blocked overlay must not start due tasks")),
    )

    started = runner._start_due_scheduler_tasks_if_idle(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "entry.json",
    )
    status = runner.status()
    tasks = fanxiu._read_data_annotation_scheduler_tasks()

    assert started is False
    assert status["phase"] == "scheduler_blocked"
    assert status["blocking_overlays"][0]["scene_id"] == 186
    assert tasks[0]["last_result"] == "blocked"
    assert tasks[0]["checkpoint"]["blocked_message"] == "检测到灵祖奖励浮层；缺少关闭动作标注"
    assert "manual_inspection_note" not in tasks[0]["checkpoint"]
    assert tasks[0]["checkpoint"]["previous_manual_inspection_note"] == "旧人工备注：今日按成功处理"


def test_due_scheduler_starts_only_first_due_task_per_idle_poll(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    tasks = [
        {
            "id": "legacy-daily-youli",
            "task_type": "daily_youli",
            "label": "第一个到期",
            "schedule_kind": "daily",
            "enabled": True,
            "interruptible": True,
            "payload": {},
            "schedule_times": ["00:00"],
            "next_time": "2000-01-01 00:00:00",
            "last_result": "",
        },
        {
            "id": "mail-cleanup",
            "task_type": "mail_cleanup",
            "label": "第二个到期",
            "schedule_kind": "daily",
            "enabled": True,
            "interruptible": True,
            "payload": {},
            "schedule_times": ["00:00"],
            "next_time": "2000-01-01 00:00:01",
            "last_result": "",
        },
    ]
    fanxiu._write_data_annotation_scheduler_tasks(tasks)
    started_batches: list[list[str]] = []

    def fake_start_scheduler_tasks(*_args, **kwargs):
        started_batches.append([str(item.get("id") or "") for item in kwargs["tasks"]])

    monkeypatch.setattr(runner, "start_scheduler_tasks", fake_start_scheduler_tasks)
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_known_blocking_overlay_message", lambda _ctx: None)

    started = runner._start_due_scheduler_tasks_if_idle(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "entry.json",
    )

    assert started is True
    assert started_batches == [["legacy-daily-youli"]]


def test_data_annotation_scheduler_tasks_run_inside_resident_service_without_worker_thread(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "hide-floating",
        "task_type": "hide_floating_window",
        "label": "隐藏浮窗",
        "schedule_kind": "daily",
        "enabled": True,
        "priority": 30,
        "interruptible": True,
        "payload": {},
        "schedule_times": ["00:00"],
        "last_result": "",
    }
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_runtime_guard_service_tick", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_clear_known_blocking_overlay_if_possible", _no_blocking_overlay_generator)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda *_args, **_kwargs: "success")

    status = runner.start_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        tasks=[task],
        all_tasks=[task],
        asset_tree_path=tmp_path / "entry.json",
    )

    assert status["running"] is False
    assert status["status"] == "success"
    assert status["task_type"] == ""
    assert not hasattr(runner, "_thread")


class _DispatchRunner(get_fanxiu_runtime_runner_class()):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _log(self, kind, message):
        self.calls.append(("log", kind, message))

    def _align_settings(self, ctx, stop_event):
        self.calls.append(("align_settings",))

    def _go_scene_task(self, ctx, asset_tree_path, target_scene_id, stop_event):
        self.calls.append(("go_scene", target_scene_id))
        return "success"

    def _execute_hide_floating_window(self, ctx, stop_event):
        self.calls.append(("hide_floating_window",))

    def _execute_gift_code_task(self, ctx, codes, stop_event):
        self.calls.append(("gift_code_redeem", tuple(codes)))


def test_data_annotation_runtime_task_dispatch_uses_backend_tasks():
    runner = _DispatchRunner()
    ctx = {"images": {}, "entry": object(), "asset_tree_path": Path("entry.json")}
    stop_event = fanxiu.threading.Event()

    assert runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 49}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 69}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "hide_floating_window", {}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "gift_code_redeem", {"codes": [" a ", "", "b"]}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "legacy_daily_task", {"legacy_name": "日常_魔祖"}, stop_event) == "unsupported"
    assert runner._execute_runtime_task(ctx, "legacy_dynamic_task", {"legacy_name": "日常_首领"}, stop_event) == "unsupported"
    with pytest.raises(RuntimeError, match="暂不支持"):
        runner._execute_runtime_task(ctx, "daily_locate", {}, stop_event)

    assert ("align_settings",) in runner.calls
    assert ("go_scene", 69) in runner.calls
    assert ("hide_floating_window",) in runner.calls
    assert ("gift_code_redeem", ("a", "b")) in runner.calls
    assert any(call == ("log", "skip", "旧版任务「日常_魔祖」尚未迁移，已跳过") for call in runner.calls)
    assert any(call == ("log", "skip", "旧版任务「日常_首领」尚未迁移，已跳过") for call in runner.calls)


def test_data_annotation_runtime_guard_tick_does_not_starve_job(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    runner._guard_enabled = True
    ctx = {"entry": object()}
    calls = []
    guard_results = [True, False]

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")

    def fake_guard_step(runtime):
        calls.append((runtime.ctx, runtime.asset_tree_path, runtime.cur_frame()))
        return guard_results.pop(0) if guard_results else False

    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", fake_guard_step)
    monkeypatch.setattr(runner, "_persist_status", lambda: calls.append("persist"))

    status = runner._runtime_guard_service_tick(
        "close_popups",
        ctx,
        tmp_path / "entry.json",
        fanxiu.threading.Event(),
    )

    assert status == fanxiu.BehaviorTreeStatus.RUNNING
    assert calls[0] == (ctx, tmp_path / "entry.json", "frame")
    assert "persist" in calls

    calls.clear()
    guard_results[:] = [True, False]
    job_calls = []
    result = runner._run_runtime_behavior_tree(
        runtime_ctx=ctx,
        asset_tree_path=tmp_path / "entry.json",
        stop_event=fanxiu.threading.Event(),
        action=lambda: job_calls.append("job") or "done",
        label="测试作业",
        tick_seconds=0.1,
    )

    assert result == "done"
    assert job_calls == ["job"]
    assert calls.count("persist") == 1


def test_data_annotation_scene_jump_wait_does_not_accept_expected_match_when_global_scene_is_source(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    shape = {"title": "日程入口", "sceneJumpTarget": "66"}
    edge = {"shape": shape, "target_ids": [66]}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_index_images", lambda tree: {})
    monkeypatch.setattr(runner, "_write_asset_tree", lambda *args: calls.append("write"))
    monkeypatch.setattr(runner, "_increment_scene_jump_target", lambda *args: calls.append("increment") or True)
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(("log", args)))

    def fake_identify(_ctx, _frame, preferred_scene_ids=None):
        if preferred_scene_ids:
            return 66, 80.0
        return 34, 90.0

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=34,
        target_scene_id=66,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    assert "increment" not in calls
    assert "write" not in calls


def test_go_scene_uses_global_current_scene_before_route_candidates(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    shape = {"title": "回到世界", "sceneJumpTarget": "34"}
    edge = {"source_id": 20, "image": {"title": "绿瓶"}, "shape": shape, "target_ids": [34]}
    ctx = {"entry": object(), "asset_tree": [], "images": {}}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (20, 100.0))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_for_route",
        lambda *_args, **_kwargs: calls.append("route-candidate") or (34, 100.0),
    )
    monkeypatch.setattr(runner, "_find_scene_route", lambda *_args, **_kwargs: [edge])
    monkeypatch.setattr(runner, "_click_scene_route_shape", lambda *_args, **_kwargs: calls.append("click"))

    def wait_scene_jump_result(*_args, **_kwargs):
        if False:
            yield fanxiu.BehaviorTreeStatus.RUNNING
        return 34

    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_scene_jump_result)

    gen = runner._go_scene_task(ctx, tmp_path / "entry.json", 34, fanxiu.threading.Event())
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "success"
    assert calls == ["click"]


def test_data_annotation_identify_scene_number_uses_best_preferred_candidate(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"images": {34: {"title": "#34"}, 66: {"title": "#66"}}}

    def fake_scene_score(_ctx, image, _frame):
        return {"#34": 90.0, "#66": 80.0}[image["title"]]

    monkeypatch.setattr(runner, "_scene_score", fake_scene_score)

    assert runner._identify_scene_number(ctx, "frame", [66, 34]) == (34, 90.0)


def test_data_annotation_runtime_start_accepts_first_batch_task_types(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    accepted = []

    def fake_run_inline_runtime_task(**kwargs):
        accepted.append(kwargs["task_type"])
        return {"ok": True, "task_type": kwargs["task_type"]}

    monkeypatch.setattr(runner, "_run_inline_runtime_task", fake_run_inline_runtime_task)

    for task_type in [
        "go_scene",
        "hide_floating_window",
            "mail_cleanup",
    ]:
        status = runner.start_runtime_task(
            entry=object(),
            entry_id="entry",
            task_type=task_type,
            payload={},
            asset_tree_path=object(),
        )
        assert status["task_type"] == task_type

    assert accepted == [
        "go_scene",
        "hide_floating_window",
        "mail_cleanup",
    ]

    status = runner.start_runtime_task(
        entry=object(),
        entry_id="entry",
        task_type="go_scene",
        payload={"target_scene_id": 69},
        asset_tree_path=object(),
    )
    assert status["task_type"] == "go_scene"


def test_data_annotation_runtime_start_rejects_unverified_task_types(monkeypatch):
    runner = create_fanxiu_runtime_runner()

    with pytest.raises(FanxiuRuntimeError) as daily_exc:
        runner.start_runtime_task(
            entry=object(),
            entry_id="entry",
            task_type="daily_locate",
            payload={},
            asset_tree_path=object(),
        )
    assert daily_exc.value.status_code == 400


def test_data_annotation_runtime_start_translates_core_runtime_error(monkeypatch, tmp_path):
    entry = type("Entry", (), {"entry_id": "entry"})()

    def fake_start_runtime_task(**_kwargs):
        raise FanxiuRuntimeError("数据标注 Runtime 正在运行任务", status_code=409)

    monkeypatch.setattr(fanxiu._runtime_control, "start_runtime_task", fake_start_runtime_task)
    monkeypatch.setattr(fanxiu, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_manual_job_state_path", lambda: tmp_path / "manual_jobs.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")

    with pytest.raises(fanxiu.HTTPException) as exc_info:
        fanxiu._start_data_annotation_runtime_task(
            entry,
            fanxiu.FanxiuDataAnnotationRuntimeTaskRequest(
                entry_id="entry",
                task_type="go_scene",
                payload={"target_scene_id": 121},
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "数据标注 Runtime 正在运行任务"


def test_data_annotation_mark_scheduler_task_advances_daily_and_sets_retry(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "daily": {
                    "id": "daily",
                    "task_type": "daily_assistant",
                    "schedule_kind": "daily",
                    "next_time": "2026-06-02 05:00:00",
                }
            }
        }
    })
    runner = create_fanxiu_runtime_runner()
    daily = {
        "id": "daily",
        "task_type": "daily_assistant",
        "schedule_kind": "daily",
        "schedule_times": ["05:00", "00:00"],
        "last_result": "",
        "last_run_at": None,
        "retry_after": None,
    }
    error_task = {
        "id": "error",
        "schedule_kind": "dynamic",
        "schedule_times": [],
        "cooldown_seconds": 120,
        "last_result": "",
        "retry_after": None,
    }

    runner._mark_scheduler_task([daily, error_task], "daily", "success")
    runner._mark_scheduler_task([daily, error_task], "error", "error")

    assert daily["last_result"] == "success"
    assert daily["last_run_at"] == "2026-06-02 06:00:00"
    assert daily["next_time"] == "2026-06-03 00:00:00"
    assert daily["retry_after"] is None
    assert error_task["last_result"] == "error"
    assert error_task["next_time"] is None
    assert error_task["retry_after"] == "2026-06-02 06:02:00"


def test_data_annotation_mark_scheduler_task_skipped_retries_without_advancing_daily(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "legacy-daily-youli",
        "task_type": "daily_youli",
        "label": "日常_游历",
        "schedule_kind": "daily",
        "schedule_times": ["00:00", "05:00"],
        "next_time": "2026-06-02 05:00:00",
        "last_result": "running",
        "retry_after": None,
        "cooldown_seconds": 600,
    }

    runner._mark_scheduler_task([task], "legacy-daily-youli", "skipped")

    assert task["last_result"] == "skipped"
    assert task["last_run_at"] == "2026-06-02 06:00:00"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_mark_scheduler_task_skipped_uses_discovered_recheck_time(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "daily-boss": {
                    "id": "daily-boss",
                    "task_type": "daily_boss",
                    "discovered_next_time": "2026-06-02 18:10:07",
                }
            }
        }
    })
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "daily-boss",
        "task_type": "daily_boss",
        "label": "日常_首领",
        "schedule_kind": "daily",
        "schedule_times": ["05:00"],
        "next_time": "2026-06-02 05:00:00",
        "last_result": "running",
        "retry_after": None,
        "cooldown_seconds": 600,
    }

    runner._mark_scheduler_task([task], "daily-boss", "skipped")

    assert task["last_result"] == "skipped"
    assert task["last_run_at"] == "2026-06-02 06:00:00"
    assert task["next_time"] == "2026-06-02 18:10:07"
    assert task["retry_after"] is None


def test_data_annotation_manual_success_advances_due_daily_scheduler_task(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 13, 18, 31, 37)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core.time, "time", lambda: fixed_now.timestamp())
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-assistant",
            "task_type": "daily_assistant",
            "label": "日常_助手",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "interruptible": True,
            "next_time": "2026-06-13 18:00:00",
            "schedule_times": ["00:00", "06:00", "12:00", "18:00"],
            "last_run_at": "2026-06-13 18:06:35",
            "last_result": "success",
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_assistant"},
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "legacy-daily-assistant": {
                    "id": "legacy-daily-assistant",
                    "task_type": "daily_assistant",
                    "schedule_kind": "daily",
                    "last_result": "success",
                    "last_run_at": "2026-06-13 18:06:35",
                    "next_time": "2026-06-13 18:00:00",
                }
            }
        }
    })
    runner = create_fanxiu_runtime_runner()

    runner._mark_matching_scheduler_tasks_for_manual_success("daily_assistant", {})

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")
    facts = fanxiu._read_data_annotation_world_facts()
    fact = facts["discoveries"]["task"]["legacy-daily-assistant"]
    assert assistant["last_run_at"] == "2026-06-13 18:31:37"
    assert assistant["last_result"] == "success"
    assert assistant["next_time"] == "2026-06-14 00:00:00"
    assert fact["last_run_at"] == "2026-06-13 18:31:37"
    assert fact["next_time"] == "2026-06-14 00:00:00"


def test_data_annotation_manual_success_updates_manual_check_pending_future_task(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 14, 11, 37, 51)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core.time, "time", lambda: fixed_now.timestamp())
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-yaozu",
            "task_type": "daily_yaozu",
            "label": "日常_妖族袭城",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "interruptible": True,
            "next_time": "2026-06-15 05:00:00",
            "schedule_times": ["05:00"],
            "last_run_at": "2026-06-14 06:32:00",
            "last_result": "manual_check_pending",
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_yaozu"},
        }
    ])
    runner = create_fanxiu_runtime_runner()

    runner._mark_matching_scheduler_tasks_for_manual_success("daily_yaozu", {})

    task = next(item for item in fanxiu._read_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-yaozu")
    assert task["last_result"] == "success"
    assert task["last_run_at"] == "2026-06-14 11:37:51"
    assert task["next_time"] == "2026-06-15 05:00:00"
    assert task["retry_after"] is None


def test_data_annotation_mark_scheduler_task_error_defaults_to_ten_minute_retry(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "manual-mail",
        "task_type": "mail_claim_check",
        "label": "邮件_领取检查",
        "schedule_kind": "manual",
        "last_result": "",
        "retry_after": None,
        "cooldown_seconds": 0,
    }

    runner._mark_scheduler_task([task], "manual-mail", "error")

    assert task["last_result"] == "error"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_mark_scheduler_task_stopped_sets_retry(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "legacy-daily-yaowang",
        "task_type": "daily_yaowang",
        "label": "日常_妖王来袭",
        "schedule_kind": "daily",
        "next_time": "2026-06-02 05:00:00",
        "last_result": "running",
        "retry_after": None,
        "cooldown_seconds": 600,
    }

    runner._mark_scheduler_task([task], "legacy-daily-yaowang", "stopped")

    assert task["last_result"] == "stopped"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


def test_scheduler_interrupted_task_marks_stopped_with_retry(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "legacy-daily-yaowang",
        "task_type": "daily_yaowang",
        "label": "日常_妖王来袭",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-02 05:00:00",
        "last_result": "",
        "retry_after": None,
        "cooldown_seconds": 600,
        "payload": {"__scheduler_definition_task_type": "daily_yaowang"},
    }
    all_tasks = [dict(task)]
    monkeypatch.setattr(runner, "_load_asset_tree", lambda path: [])
    monkeypatch.setattr(runner, "_index_images", lambda tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda ctx: None)
    monkeypatch.setattr(runner, "_clear_known_blocking_overlay_if_possible", _no_blocking_overlay_generator)

    def interrupted(*args, **kwargs):
        raise InterruptedError()

    monkeypatch.setattr(runner, "_run_runtime_behavior_tree", interrupted)

    runner._run_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        tasks=[task],
        all_tasks=all_tasks,
        asset_tree_path=tmp_path / "entry.json",
        stop_event=fanxiu.threading.Event(),
    )

    assert all_tasks[0]["last_result"] == "stopped"
    assert all_tasks[0]["next_time"] is None
    assert all_tasks[0]["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_scheduler_repairs_orphaned_queued_run(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    task = {
        "id": "mail-cleanup",
        "task_type": "mail_cleanup",
        "label": "邮件_清理",
        "source": "manual",
        "schedule_kind": "manual",
        "enabled": False,
        "priority": 110,
        "interruptible": True,
        "last_run_at": "2026-06-01 10:00:00",
        "last_result": "queued",
        "payload": {},
    }
    fanxiu._write_data_annotation_json(path, [task])
    fanxiu._write_data_annotation_json(tmp_path / "manual_jobs.json", [])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    repaired = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert repaired["task_type"] == "mail_cleanup"
    assert repaired["last_result"] == "stopped"
    assert repaired["retry_after"] is None
    assert repaired["checkpoint"]["recovered_from_orphaned_run_at"]


def test_data_annotation_scheduler_repairs_orphaned_daily_run_with_retry(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    task = {
        "id": "mail-cleanup",
        "task_type": "mail_cleanup",
        "label": "邮件_清理",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "schedule_times": ["00:05"],
        "interruptible": True,
        "last_run_at": "2026-06-01 10:00:00",
        "last_result": "running",
        "next_time": "2026-06-02 00:05:00",
        "retry_after": None,
        "cooldown_seconds": 600,
        "payload": {"__scheduler_definition_task_type": "mail_cleanup"},
    }
    fanxiu._write_data_annotation_json(path, [task])
    fanxiu._write_data_annotation_json(tmp_path / "manual_jobs.json", [])
    monkeypatch.setattr(runtime_control, "read_runtime_status", lambda _path=None: {})
    monkeypatch.setattr(runtime_control.time, "time", lambda: datetime(2026, 6, 1, 10, 5, 0).timestamp())

    tasks = runtime_control.read_scheduler_tasks(
        scheduler_state_path=path,
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )
    repaired = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert repaired["last_result"] == "stopped"
    assert repaired["next_time"] is None
    assert repaired["retry_after"] == "2026-06-01 10:15:00"
    assert repaired["checkpoint"]["recovered_from_orphaned_run_at"]


def test_data_annotation_scheduler_does_not_repair_fresh_persisted_running_task(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    runtime_state_path = tmp_path / "runtime_state.json"
    task = {
        "id": "mail-cleanup",
        "task_type": "mail_cleanup",
        "label": "邮件_清理",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "schedule_times": ["00:05"],
        "interruptible": True,
        "last_run_at": "2026-06-01 10:00:00",
        "last_result": "running",
        "next_time": None,
        "retry_after": None,
        "cooldown_seconds": 600,
        "payload": {"__scheduler_definition_task_type": "mail_cleanup"},
    }
    fanxiu._write_data_annotation_json(path, [task])
    fanxiu._write_data_annotation_json(tmp_path / "manual_jobs.json", [])
    fanxiu._write_data_annotation_json(runtime_state_path, {
        "running": True,
        "status": "running",
        "current_task_id": "mail-cleanup",
        "updated_at": datetime(2026, 6, 1, 10, 4, 30).timestamp(),
    })
    monkeypatch.setattr(runtime_control, "read_runtime_status", lambda _path=None: fanxiu._read_data_annotation_json(runtime_state_path, {}))
    monkeypatch.setattr(runtime_control.time, "time", lambda: datetime(2026, 6, 1, 10, 5, 0).timestamp())

    tasks = runtime_control.read_scheduler_tasks(
        scheduler_state_path=path,
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )
    running = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert running["last_result"] == "running"
    assert running["retry_after"] is None
    checkpoint = running.get("checkpoint") if isinstance(running.get("checkpoint"), dict) else {}
    assert "recovered_from_orphaned_run_at" not in checkpoint


def test_data_annotation_scheduler_ignores_equal_time_stale_running_fact(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    fixed_now = datetime(2026, 6, 1, 10, 5, 0)
    last_run = datetime(2026, 6, 1, 10, 0, 0)
    fanxiu._write_data_annotation_json(path, [
        {
            "id": "mail-cleanup",
            "task_type": "mail_cleanup",
            "label": "邮件_清理",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:05"],
            "interruptible": True,
            "last_run_at": "2026-06-01 10:00:00",
            "last_result": "stopped",
            "next_time": None,
            "retry_after": "2026-06-01 10:10:00",
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "mail_cleanup"},
        }
    ])
    fanxiu._write_data_annotation_json(tmp_path / "world_facts.json", {
        "discoveries": {
            "task": {
                "mail-cleanup": {
                    "id": "mail-cleanup",
                    "task_type": "mail_cleanup",
                    "last_result": "running",
                    "last_run_at": "2026-06-01 10:00:00",
                    "updated_at": last_run.timestamp(),
                }
            }
        }
    })
    monkeypatch.setattr(runtime_control.time, "time", lambda: fixed_now.timestamp())

    tasks = runtime_control.read_scheduler_tasks(
        scheduler_state_path=path,
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )
    repaired = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert repaired["last_result"] == "stopped"
    assert repaired["retry_after"] == "2026-06-01 10:10:00"
    assert repaired["next_time"] is None


def test_data_annotation_scheduler_ignores_same_run_stale_running_fact_with_update_skew(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    fixed_now = datetime(2026, 6, 1, 10, 5, 0)
    last_run = datetime(2026, 6, 1, 10, 0, 0)
    fanxiu._write_data_annotation_json(path, [
        {
            "id": "mail-cleanup",
            "task_type": "mail_cleanup",
            "label": "邮件_清理",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:05"],
            "interruptible": True,
            "last_run_at": "2026-06-01 10:00:00",
            "last_result": "stopped",
            "next_time": None,
            "retry_after": "2026-06-01 10:10:00",
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "mail_cleanup"},
        }
    ])
    fanxiu._write_data_annotation_json(tmp_path / "world_facts.json", {
        "discoveries": {
            "task": {
                "mail-cleanup": {
                    "id": "mail-cleanup",
                    "task_type": "mail_cleanup",
                    "last_result": "running",
                    "last_run_at": "2026-06-01 10:00:00",
                    "updated_at": last_run.timestamp() + 0.25,
                }
            }
        }
    })
    monkeypatch.setattr(runtime_control.time, "time", lambda: fixed_now.timestamp())

    tasks = runtime_control.read_scheduler_tasks(
        scheduler_state_path=path,
        world_facts_path=tmp_path / "world_facts.json",
        manual_job_path=tmp_path / "manual_jobs.json",
    )
    repaired = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert repaired["last_result"] == "stopped"
    assert repaired["retry_after"] == "2026-06-01 10:10:00"
    assert repaired["next_time"] is None


def test_data_annotation_scheduler_keeps_queued_run_with_pending_manual_job(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    task = {
        "id": "mail-claim-check",
        "task_type": "mail_claim_check",
        "label": "邮件_领取检查",
        "source": "manual",
        "schedule_kind": "manual",
        "enabled": False,
        "priority": 110,
        "interruptible": True,
        "last_run_at": "2026-06-01 10:00:00",
        "last_result": "queued",
        "payload": {},
    }
    fanxiu._write_data_annotation_json(path, [task])
    fanxiu._write_data_annotation_json(
        tmp_path / "manual_jobs.json",
        [
            {
                "id": "manual-mail",
                "task_type": "mail_cleanup",
                "label": "手动任务：邮件_领取检查",
                "payload": {"__scheduler_task_id": "mail-cleanup"},
                "status": "pending",
            }
        ],
    )

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    queued = next(item for item in tasks if item["id"] == "mail-cleanup")

    assert queued["last_result"] == "queued"


def test_data_annotation_scheduler_removes_obsolete_mail_full_scan_task(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    path = _scheduler_state_path(tmp_path)
    task = {
        "id": "mail-full-scan",
        "task_type": "mail_claim_check",
        "label": "邮件_全量遍历",
        "source": "manual",
        "schedule_kind": "manual",
        "enabled": False,
        "priority": 105,
        "interruptible": True,
        "payload": {"observe_only": True, "entry_mode": "stable"},
    }
    fanxiu._write_data_annotation_json(path, [task])
    fanxiu._write_data_annotation_json(tmp_path / "manual_jobs.json", [])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()

    assert "mail-full-scan" not in {str(item.get("id") or "") for item in tasks}
    assert "mail-cleanup" in {str(item.get("id") or "") for item in tasks}


def test_data_annotation_ocr_centers_in_shape_filters_signup_button_text():
    runner = create_fanxiu_runtime_runner()
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "title": "报名",
                "x": 0.7,
                "y": 0.2,
                "w": 0.25,
                "h": 0.4,
            }
        ],
    }
    lines = [
        {"text": "已报名", "x": 700, "y": 390, "w": 80, "h": 32},
        {"text": "报名", "x": 700, "y": 470, "w": 80, "h": 32},
        {"text": "报名", "x": 100, "y": 470, "w": 80, "h": 32},
    ]

    centers = runner._ocr_centers_in_shape(lines, image, "报名", include=("报名",), exclude=("已报名",))

    assert centers == [(740.0, 486.0, "报名")]


def test_daily_assistant_item_precloses_late_result_before_next_item(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    image204 = {
        "id": 204,
        "title": "小助手清单",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "滚动窗口", "x": 0.0, "y": 0.2, "w": 1.0, "h": 0.65},
            {
                "title": "宗门助手",
                "x": 0.1,
                "y": 0.3,
                "w": 0.8,
                "h": 0.1,
                "children": [
                    {"title": "标题", "x": 0.15, "y": 0.31, "w": 0.4, "h": 0.04},
                    {"title": "执行", "x": 0.75, "y": 0.33, "w": 0.12, "h": 0.04},
                ],
            },
        ],
    }
    image205 = {"id": 205, "title": "小助手执行详情", "shapes": [{"title": "点击屏幕继续", "x": 0.4, "y": 0.84, "w": 0.2, "h": 0.04}]}
    ctx = {"asset_tree_path": asset_tree, "images": {204: image204, 205: image205}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.scene_calls = 0

        def current_scene(self, view_ids=None, **kwargs):
            self.scene_calls += 1
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            if self.scene_calls == 1:
                return 34, 98.0, "late-result"
            return 204, 100.0, "list"

        def ocr_lines(self, frame):
            actions.append(("ocr_lines", frame))
            if frame == "list":
                return [{"text": "宗门助手", "x": 260, "y": 560, "w": 150, "h": 40}]
            return []

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "late-result":
                return "神物园效率加成 点击屏幕继续"
            return "小助手 宗门助手 执行"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def click_frame_point(self, view_id, x, y):
            actions.append(("click_frame_point", view_id, round(x), round(y)))

    def wait_list(*_args, **_kwargs):
        if False:
            yield None
        return 204, 100.0

    def wait_result(*_args, **_kwargs):
        if False:
            yield None
        return "no_popup"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_assistant_list_state", wait_list)
    monkeypatch.setattr(runner, "_wait_daily_assistant_item_result", wait_result)

    gen = runner._run_daily_assistant_item_from_list(ctx, fanxiu.threading.Event(), {}, image204, "执行-宗门助手")
    with pytest.raises(StopIteration) as exc_info:
        while True:
            next(gen)

    assert exc_info.value.value == "no_popup"
    assert ("wait_click", 205, "点击屏幕继续", {}) in actions
    assert any(action[0] == "click_frame_point" for action in actions)


def test_daily_assistant_shenwuyuan_execute_skips_fast_list_probe(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": asset_tree, "images": {}}
    actions: list[tuple] = []

    class FakeRuntime:
        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return 204, 100.0, "list"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "小助手 神物园助手 执行"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    gen = runner._wait_daily_assistant_item_result(ctx, fanxiu.threading.Event(), {}, "神物园助手", "执行")
    assert next(gen) is None
    gen.close()

    assert actions == [
        ("current_scene", (214, 213, 210, 208, 209, 205, 204, 69, 34), {"update": True}),
        ("ocr_text", "list"),
        ("settle", 0.35),
    ]


def test_daily_assistant_result_scene_accepts_generic_continue_reward_text():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_assistant_result_scene_id(
        None,
        "【仙花祈愿】活动积分增加454点 恭喜获得神·刘备：获得积分效率加成 点击屏幕继续",
    ) == 205


