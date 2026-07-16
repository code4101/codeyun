import base64
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend.api import fanxiu
from backend.core.fanxiu.runtime import behavior_tree as fanxiu_behavior_tree
from backend.core.fanxiu.data_annotation import default_jobs as data_annotation_default_jobs
from backend.core.fanxiu.data_annotation import scheduler as scheduler_core
from backend.core.fanxiu.data_annotation import state as data_annotation_state
from backend.core.fanxiu.data_annotation import runtime_control as runtime_control
from backend.core.fanxiu.data_annotation import runtime_runner as runtime_runner_core
from backend.core.fanxiu.data_annotation.tasks import daily_resources as fanxiu_daily_resources
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
    monkeypatch.setattr(fanxiu, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_settings_path", lambda: _scheduler_settings_path(tmp_path))
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(runtime_control, "scheduler_blocking_overlays", lambda **kwargs: [])
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu_behavior_tree, "fanxiu_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
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


def test_ensure_runtime_service_preserves_behavior_tree_setting(tmp_path, monkeypatch):
    settings_path = _scheduler_settings_path(tmp_path)
    runtime_control.write_scheduler_settings(
        {"job_group_enabled": False, "behavior_tree_enabled": False},
        scheduler_settings_path=settings_path,
    )

    def fake_ensure_service(entry, entry_id, **kwargs):
        return {
            "ok": True,
            "alive": True,
            "execution_state": "idle",
            "entry_id": entry_id,
        }

    persisted = {}

    monkeypatch.setattr(runtime_control, "ensure_fanxiu_behavior_tree_service", fake_ensure_service)
    monkeypatch.setattr(runtime_control, "persist_runtime_status", lambda status, **_kwargs: persisted.update(status))

    status = runtime_control.ensure_runtime_service(
        entry=object(),
        entry_id="entry",
        scheduler_settings_path=settings_path,
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert status["kernel"]["alive"] is True
    assert status["behavior_tree_enabled"] is False
    assert persisted["behavior_tree_enabled"] is False


def test_daily_audit_visible_rows_maps_incomplete_runtime_tasks():
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "滚动窗口", "x": 0.05, "y": 0.18, "w": 0.9, "h": 0.66}],
    }
    lines = [
        {"text": "挑战或扫荡淬剑试炼", "x": 120, "y": 320, "w": 380, "h": 40},
        {"text": "活 10/次", "x": 420, "y": 390, "w": 120, "h": 36},
        {"text": "次 0/1", "x": 420, "y": 436, "w": 100, "h": 36},
        {"text": "完成双人修炼1次", "x": 120, "y": 520, "w": 360, "h": 40},
        {"text": "活 10/次", "x": 420, "y": 590, "w": 120, "h": 36},
        {"text": "次 3/3", "x": 420, "y": 636, "w": 100, "h": 36},
        {"text": "收取两万九曜玄墨", "x": 120, "y": 680, "w": 360, "h": 40},
        {"text": "活 10/次", "x": 420, "y": 750, "w": 120, "h": 36},
        {"text": "次 0/2 採炁中", "x": 420, "y": 796, "w": 180, "h": 36},
        {"text": "寻道历练1次", "x": 120, "y": 980, "w": 320, "h": 40},
        {"text": "活 5/次", "x": 420, "y": 1050, "w": 120, "h": 36},
        {"text": "次 0/4", "x": 420, "y": 1096, "w": 100, "h": 36},
    ]

    rows = runner._daily_audit_visible_rows(lines, image69)
    by_task = {row["task_type"]: row for row in rows if row["task_type"]}
    unmapped = [row for row in rows if not row["task_type"]]

    assert by_task["daily_jianling"]["task_id"] == "legacy-daily-jianling"
    assert by_task["daily_jianling"]["done"] is False
    assert by_task["daily_shuangxiu"]["done"] is True
    assert by_task["daily_dongtian"]["task_id"] == "legacy-daily-dongtian"
    assert by_task["daily_dongtian"]["done"] is False
    assert unmapped[0]["title"].startswith("寻道历练")
    assert unmapped[0]["done"] is False


def test_daily_audit_dungeon_requires_full_purchased_attempts():
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "滚动窗口", "x": 0.05, "y": 0.18, "w": 0.9, "h": 0.66}],
    }
    lines = [
        {"text": "通关每日副本", "x": 120, "y": 320, "w": 320, "h": 40},
        {"text": "活 15/次", "x": 420, "y": 390, "w": 120, "h": 36},
        {"text": "次 3/3 已完成", "x": 420, "y": 436, "w": 180, "h": 36},
        {"text": "完成双人修炼1次", "x": 120, "y": 520, "w": 360, "h": 40},
        {"text": "活 10/次", "x": 420, "y": 590, "w": 120, "h": 36},
        {"text": "次 3/3 已完成", "x": 420, "y": 636, "w": 180, "h": 36},
    ]

    rows = runner._daily_audit_visible_rows(lines, image69)
    by_task = {row["task_type"]: row for row in rows if row["task_type"]}

    assert by_task["daily_dungeon"]["done"] is False
    assert by_task["daily_shuangxiu"]["done"] is True

    lines[2]["text"] = "次 6/6 已完成"
    rows = runner._daily_audit_visible_rows(lines, image69)
    by_task = {row["task_type"]: row for row in rows if row["task_type"]}

    assert by_task["daily_dungeon"]["done"] is True


class _FakeDailyAuditRuntime:
    def __init__(self, image69):
        self.image69 = image69
        self.actions = []
        self._frames = ["frame-top", "frame-next"]
        self._frame_index = 0
        self._up_results = [True, False]
        self._down_results = [True, False]

    def current_scene(self, candidates=None, **kwargs):
        return 69, 100.0, kwargs.get("frame_data_url") or "frame-current"

    def ocr_text(self, frame):
        return "日常 活跃度 活动报名 挑战或扫荡淬剑试炼 0/1"

    def view(self, view_id):
        assert view_id == 69
        return self.image69

    def shape(self, view, title):
        assert view is self.image69
        return next(shape for shape in self.image69["shapes"] if shape["title"] == title)

    def scroll_shape_content(self, view, shape, *, direction="down"):
        self.actions.append(("scroll_shape_content", direction))
        results = self._up_results if direction == "up" else self._down_results
        if False:
            yield None
        return results.pop(0) if results else False

    def cur_frame(self, update=True):
        frame = self._frames[min(self._frame_index, len(self._frames) - 1)]
        self._frame_index += 1
        return frame


def test_daily_audit_scroll_loop_records_merged_world_facts(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "title": "日常",
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "滚动窗口", "x": 0.05, "y": 0.18, "w": 0.9, "h": 0.66}],
    }
    fake_runtime = _FakeDailyAuditRuntime(image69)
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: fake_runtime)
    lines_by_frame = {
        "frame-top": [
            {"text": "日常 活跃度 活动报名", "x": 120, "y": 230, "w": 420, "h": 40},
            {"text": "挑战或扫荡淬剑试炼", "x": 120, "y": 320, "w": 380, "h": 40},
            {"text": "活 10/次", "x": 420, "y": 390, "w": 120, "h": 36},
            {"text": "次 0/1", "x": 420, "y": 436, "w": 100, "h": 36},
        ],
        "frame-next": [
            {"text": "日常 活跃度 活动报名", "x": 120, "y": 230, "w": 420, "h": 40},
            {"text": "完成双人修炼1次", "x": 120, "y": 520, "w": 360, "h": 40},
            {"text": "活 10/次", "x": 420, "y": 590, "w": 120, "h": 36},
            {"text": "次 3/3", "x": 420, "y": 636, "w": 100, "h": 36},
        ],
    }
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda _ctx, frame: lines_by_frame[frame])

    result = _drain_generator(
        runner._execute_daily_audit_task(
            {"asset_tree_path": tmp_path / "asset-tree.json", "images": {69: image69}},
            fanxiu.threading.Event(),
            {"max_scrolls": 3},
        )
    )

    facts = runtime_runner_core._read_data_annotation_world_facts()
    audit = facts["discoveries"]["daily_audit"]
    by_task = {row["task_type"]: row for row in audit["rows"] if row["task_type"]}

    assert result == "success"
    assert fake_runtime.actions == [
        ("scroll_shape_content", "up"),
        ("scroll_shape_content", "up"),
        ("scroll_shape_content", "down"),
        ("scroll_shape_content", "down"),
    ]
    assert audit["row_count"] == 2
    assert by_task["daily_jianling"]["done"] is False
    assert by_task["daily_shuangxiu"]["done"] is True
    assert audit["incomplete_task_ids"] == ["legacy-daily-jianling"]
    assert audit["completed_task_ids"] == ["legacy-daily-shuangxiu"]


def test_scheduler_plan_treats_fresh_daily_audit_incomplete_as_due():
    now = datetime(2026, 6, 20, 8, 0, 0)
    now_ts = now.timestamp()
    tasks = [
        {
            "id": "legacy-daily-jianling",
            "task_type": "daily_jianling",
            "label": "日常_剑灵",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": "2026-06-21 05:00:00",
            "last_run_at": "2026-06-20 05:10:00",
            "last_result": "success",
            "retry_after": None,
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {},
            "daily_audit": {
                "updated_at": datetime(2026, 6, 20, 7, 0, 0).timestamp(),
                "incomplete_task_ids": ["legacy-daily-jianling"],
                "mapped_incomplete": [{"task_id": "legacy-daily-jianling", "task_type": "daily_jianling"}],
            },
        }
    }

    plan = scheduler_core.build_data_annotation_scheduler_plan(
        tasks,
        {"running": False, "status": "idle"},
        facts,
        Path("scheduler_tasks.json"),
        task_supported=lambda task: True,
        task_due=lambda task: False,
        now_ts=now_ts,
    )

    assert [task["id"] for task in plan["due_tasks"]] == ["legacy-daily-jianling"]


def test_scheduler_plan_ignores_daily_audit_older_than_last_run():
    now = datetime(2026, 6, 20, 8, 0, 0)
    tasks = [
        {
            "id": "legacy-daily-jianling",
            "task_type": "daily_jianling",
            "label": "日常_剑灵",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": "2026-06-21 05:00:00",
            "last_run_at": "2026-06-20 07:30:00",
            "last_result": "success",
            "retry_after": None,
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {},
            "daily_audit": {
                "updated_at": datetime(2026, 6, 20, 7, 0, 0).timestamp(),
                "incomplete_task_ids": ["legacy-daily-jianling"],
            },
        }
    }

    plan = scheduler_core.build_data_annotation_scheduler_plan(
        tasks,
        {"running": False, "status": "idle"},
        facts,
        Path("scheduler_tasks.json"),
        task_supported=lambda task: True,
        task_due=lambda task: False,
        now_ts=now.timestamp(),
    )

    assert plan["due_tasks"] == []


def test_scheduler_syncs_fresh_daily_audit_completed_to_success():
    tasks = [
        {
            "id": "legacy-daily-dungeon",
            "task_type": "daily_dungeon",
            "label": "日常_每日副本",
            "source": "data_annotation_runtime",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": None,
            "last_run_at": "2026-06-21 18:41:17",
            "last_result": "error",
            "retry_after": "2026-06-21 18:51:34",
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {},
            "daily_audit": {
                "updated_at": datetime(2026, 6, 21, 19, 30, 0).timestamp(),
                "updated_at_text": "2026-06-21 19:30:00",
                "completed_task_ids": ["legacy-daily-dungeon"],
                "mapped_completed": [
                    {
                        "task_id": "legacy-daily-dungeon",
                        "task_type": "daily_dungeon",
                        "text": "通关每日副本活15/次次6/6已完成",
                        "progress": {"current": 6, "total": 6},
                        "done": True,
                    }
                ],
            },
        }
    }

    changed = scheduler_core.sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        facts,
        now=datetime(2026, 6, 21, 19, 31, 0),
    )

    assert changed is True
    assert tasks[0]["last_result"] == "success"
    assert tasks[0]["last_run_at"] == "2026-06-21 19:30:00"
    assert tasks[0]["retry_after"] is None
    assert tasks[0]["next_time"] == "2026-06-22 05:00:00"


def test_scheduler_keeps_success_task_fact_after_sync_as_durable_backup():
    tasks = [
        {
            "id": "legacy-daily-vip",
            "task_type": "daily_vip",
            "label": "日常_vip",
            "source": "data_annotation_runtime",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["00:00"],
            "next_time": "2026-07-10 00:00:00",
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {
                "legacy-daily-vip": {
                    "id": "legacy-daily-vip",
                    "task_type": "daily_vip",
                    "label": "日常_vip",
                    "source": "data_annotation_runtime",
                    "schedule_kind": "daily",
                    "last_result": "success",
                    "last_run_at": "2026-07-10 00:15:30",
                    "next_time": "2026-07-11 00:00:00",
                    "discovered_next_time": "2026-07-11 00:00:00",
                    "retry_after": None,
                    "updated_at": datetime(2026, 7, 10, 0, 15, 30).timestamp(),
                }
            }
        }
    }

    changed = scheduler_core.sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        facts,
        now=datetime(2026, 7, 10, 3, 52, 0),
    )

    assert changed is True
    assert tasks[0]["last_result"] == "success"
    assert tasks[0]["last_run_at"] == "2026-07-10 00:15:30"
    assert tasks[0]["next_time"] == "2026-07-11 00:00:00"
    assert "legacy-daily-vip" in facts["discoveries"]["task"]


def test_scheduler_does_not_sync_dungeon_audit_completed_below_required_total():
    tasks = [
        {
            "id": "legacy-daily-dungeon",
            "task_type": "daily_dungeon",
            "label": "日常_每日副本",
            "source": "data_annotation_runtime",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": None,
            "last_run_at": "2026-06-21 18:41:17",
            "last_result": "error",
            "retry_after": "2026-06-21 18:51:34",
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {},
            "daily_audit": {
                "updated_at": datetime(2026, 6, 21, 19, 30, 0).timestamp(),
                "updated_at_text": "2026-06-21 19:30:00",
                "completed_task_ids": ["legacy-daily-dungeon"],
                "mapped_completed": [
                    {
                        "task_id": "legacy-daily-dungeon",
                        "task_type": "daily_dungeon",
                        "text": "通关每日副本活15/次次3/3已完成",
                        "progress": {"current": 3, "total": 3},
                        "done": True,
                    }
                ],
            },
        }
    }

    changed = scheduler_core.sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        facts,
        now=datetime(2026, 6, 21, 19, 31, 0),
    )

    assert changed is False
    assert tasks[0]["last_result"] == "error"
    assert tasks[0]["retry_after"] == "2026-06-21 18:51:34"


def test_scheduler_syncs_same_run_success_fact_over_skipped_retry():
    tasks = [
        {
            "id": "mail-selective-claim",
            "task_type": "mail_selective_claim",
            "label": "邮件_选择性领取",
            "source": "data_annotation_runtime",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["00:05"],
            "next_time": None,
            "last_run_at": "2026-06-28 08:36:41",
            "last_result": "skipped",
            "retry_after": "2026-06-28 08:06:54",
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {
                "mail-selective-claim": {
                    "id": "mail-selective-claim",
                    "task_type": "mail_selective_claim",
                    "label": "邮件_选择性领取",
                    "last_result": "success",
                    "last_run_at": "2026-06-28 08:36:41",
                    "retry_after": None,
                    "next_time": None,
                    "updated_at": datetime(2026, 6, 28, 8, 36, 41).timestamp(),
                }
            }
        }
    }

    changed = scheduler_core.sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        facts,
        now=datetime(2026, 6, 28, 8, 40, 0),
    )

    assert changed is True
    assert tasks[0]["last_result"] == "success"
    assert tasks[0]["retry_after"] is None
    assert tasks[0]["next_time"] == "2026-06-29 00:05:00"


def test_scheduler_does_not_overwrite_newer_retry_with_stale_success_fact():
    tasks = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": None,
            "last_run_at": "2026-06-22 20:04:11",
            "last_result": "skipped",
            "retry_after": "2026-06-22 20:34:07",
            "payload": {},
        }
    ]
    facts = {
        "discoveries": {
            "task": {
                "daily-boss": {
                    "id": "daily-boss",
                    "task_type": "daily_boss",
                    "label": "日常_首领",
                    "last_result": "success",
                    "last_run_at": "2026-06-22 20:00:09",
                    "next_time": "2026-06-23 05:00:00",
                    "discovered_next_time": "2026-06-23 05:00:00",
                    "updated_at": datetime(2026, 6, 22, 20, 0, 9).timestamp(),
                }
            }
        }
    }

    changed = scheduler_core.sync_data_annotation_scheduler_tasks_from_world_facts(
        tasks,
        facts,
        now=datetime(2026, 6, 22, 20, 15, 0),
    )

    assert changed is False
    assert tasks[0]["last_result"] == "skipped"
    assert tasks[0]["retry_after"] == "2026-06-22 20:34:07"
    assert tasks[0]["next_time"] is None


def test_scheduler_retries_green_bottle_after_cooldown_on_error():
    tasks = [
        {
            "id": "legacy-daily-green-bottle-baiye",
            "task_type": "daily_green_bottle_baiye",
            "label": "日常_绿瓶拜谒",
            "source": "data_annotation_runtime",
            "enabled": True,
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": None,
            "last_run_at": "2026-06-29 00:37:48",
            "last_result": "error",
            "retry_after": "2026-06-29 00:48:59",
            "cooldown_seconds": 600,
            "payload": {},
        }
    ]

    repaired, changed = scheduler_core.repair_data_annotation_scheduler_tasks(
        tasks,
        default_tasks=[],
        facts={},
        task_supported=lambda _task: True,
        now=datetime(2026, 6, 29, 0, 40, 0),
    )

    assert changed is True
    assert repaired[0]["retry_after"] == "2026-06-29 00:48:59"
    assert repaired[0]["next_time"] is None


def test_data_annotation_default_scheduler_imports_legacy_behavior_tree_tasks():
    tasks = fanxiu._default_data_annotation_scheduler_tasks()

    legacy_tasks = [item for item in tasks if item["source"] == "legacy_behavior_tree"]
    daily_tasks = [item for item in legacy_tasks if item["schedule_kind"] == "daily"]
    dynamic_tasks = [item for item in legacy_tasks if item["schedule_kind"] == "dynamic"]
    baiye = next(item for item in tasks if item["id"] == "legacy-daily-baiye")
    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")
    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    dongtian = next(item for item in tasks if item["id"] == "legacy-daily-dongtian")
    dongtian_clear = next(item for item in tasks if item["id"] == "legacy-daily-dongtian-clear")
    lingmai_clear = next(item for item in tasks if item["id"] == "legacy-daily-lingmai-clear")
    gift = next(item for item in tasks if item["id"] == "gift-code-weekly")
    lundao = next(item for item in tasks if item["id"] == "daily-lundao-seat")
    weekly_dungeon = next(item for item in tasks if item["id"] == "daily-weekly-dungeon")
    mojie_raid = next(item for item in tasks if item["id"] == "legacy-daily-mojie-raid")

    assert legacy_tasks
    assert daily_tasks
    assert dynamic_tasks
    assert not any(item["id"] == "legacy-daily-youli" for item in tasks)
    assert not any(item["id"] == "legacy-daily-jianling" for item in tasks)
    assert not any(item["id"] == "legacy-daily-yihuo" for item in tasks)
    assert signup["task_type"] == "daily_signup"
    assert signup["source"] == "data_annotation_runtime"
    assert signup["enabled"] is True
    assert signup["schedule_times"] == ["05:00"]
    assert signup["legacy_name"] == "日常_报名"
    assert assistant["task_type"] == "daily_assistant"
    assert assistant["source"] == "data_annotation_runtime"
    assert assistant["enabled"] is True
    assert assistant["schedule_times"] == ["00:00", "06:00", "12:00", "18:00"]
    assert dongtian["label"] == "洞天_领取"
    assert dongtian_clear["label"] == "洞天_行动力"
    assert dongtian_clear["cooldown_seconds"] == 300
    assert lingmai_clear["label"] == "灵脉_清体力"
    assert lingmai_clear["cooldown_seconds"] == 300
    assert baiye["task_type"] == "daily_baiye"
    assert baiye["source"] == "data_annotation_runtime"
    assert baiye["payload"] == {"args": ["魔道"]}
    assert gift["schedule_kind"] == "manual"
    assert gift["payload"] == {"codes": []}
    assert lundao["task_type"] == "daily_lundao"
    assert lundao["label"] == "论道_座位"
    assert lundao["schedule_kind"] == "manual"
    assert lundao["enabled"] is False
    assert lundao["schedule_times"] == []
    assert weekly_dungeon["task_type"] == "daily_weekly_dungeon"
    assert weekly_dungeon["schedule_kind"] == "weekly"
    assert weekly_dungeon["weekdays"] == [0]
    assert weekly_dungeon["schedule_times"] == ["05:00"]
    assert mojie_raid["task_type"] == "daily_mojie_raid"
    assert mojie_raid["source"] == "data_annotation_runtime"
    assert mojie_raid["enabled"] is True
    assert mojie_raid["schedule_times"] == ["13:00", "21:30"]
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
    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")

    assert not any(item["label"] == "真实测试礼包码" for item in tasks)
    assert not any(item["id"] == "legacy-daily-youli" for item in tasks)
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
    assert "legacy-daily-youli" not in by_id
    assert "legacy-daily-jianling" not in by_id


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
        "scheduler_meta": None,
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


def test_data_annotation_scheduler_api_single_toggle_preserves_xianshi_weekly_enabled(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    xianshi = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "xianshi-weekly-resources").copy()
    signup = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    xianshi["enabled"] = True
    signup["enabled"] = True
    fanxiu._write_data_annotation_scheduler_tasks([xianshi, signup])

    incoming = dict(signup)
    incoming["enabled"] = False
    response = fanxiu.put_fanxiu_data_annotation_scheduler_tasks(
        [fanxiu.FanxiuDataAnnotationSchedulerTaskItem.model_validate(incoming)],
        current_user=object(),
        session=object(),
    )

    by_id = {item.id: item for item in response.tasks}
    persisted = json.loads(_scheduler_state_path(tmp_path).read_text(encoding="utf-8"))
    persisted_by_id = {item["id"]: item for item in persisted}

    assert by_id["legacy-daily-signup"].enabled is False
    assert by_id["xianshi-weekly-resources"].enabled is True
    assert persisted_by_id["xianshi-weekly-resources"]["enabled"] is True


def test_data_annotation_scheduler_repair_preserves_enabled_for_temporarily_unsupported_task():
    task = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "xianshi-weekly-resources").copy()
    task["enabled"] = True
    task["next_time"] = "2026-07-06 00:05:00"

    tasks, changed = scheduler_core.repair_data_annotation_scheduler_tasks(
        [task],
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda _task: False,
        now=datetime(2026, 7, 6, 0, 0, 0),
    )
    repaired = next(item for item in tasks if item["id"] == "xianshi-weekly-resources")
    plan = scheduler_core.build_data_annotation_scheduler_plan(
        tasks,
        {},
        {},
        Path("scheduler_tasks.json"),
        task_supported=lambda _task: False,
        task_due=runtime_control.data_annotation_task_due,
        now_ts=datetime(2026, 7, 6, 0, 6, 0).timestamp(),
    )

    assert repaired["enabled"] is True
    assert repaired["next_time"] == "2026-07-06 00:05:00"
    assert changed is True
    assert plan["next_action"] == "blocked"
    planned = next(item for item in plan["tasks"] if item["id"] == "xianshi-weekly-resources")
    assert planned["supported"] is False
    assert planned["enabled"] is True
    assert planned["runnable"] is False


def test_data_annotation_scheduler_repair_keeps_xianshi_weekly_resources_enabled():
    task = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "xianshi-weekly-resources").copy()
    task["enabled"] = False

    tasks, changed = scheduler_core.repair_data_annotation_scheduler_tasks(
        [task],
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 6, 0, 0, 0),
    )

    repaired = next(item for item in tasks if item["id"] == "xianshi-weekly-resources")
    assert repaired["enabled"] is True
    assert changed is True


def test_data_annotation_scheduler_repair_does_not_rewind_blocked_daily_task():
    task = next(
        item for item in fanxiu._default_data_annotation_scheduler_tasks()
        if item["id"] == "legacy-daily-assistant"
    ).copy()
    task.update({
        "enabled": True,
        "last_result": "blocked",
        "next_time": "2026-07-14 06:00:00",
        "retry_after": None,
    })

    tasks, _changed = scheduler_core.repair_data_annotation_scheduler_tasks(
        [task],
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda _task: True,
        now=datetime(2026, 7, 14, 1, 30, 0),
    )

    repaired = next(item for item in tasks if item["id"] == "legacy-daily-assistant")
    assert repaired["last_result"] == "blocked"
    assert repaired["next_time"] == "2026-07-14 06:00:00"


def test_data_annotation_scheduler_read_removes_assistant_covered_legacy_tasks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-youli",
            "task_type": "legacy_daily_task",
            "label": "日常 灵泉",
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

    assert "legacy-daily-youli" not in by_id
    assert "legacy-daily-youli" not in persisted_by_id




def test_game_window2_service_activate_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_activate_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "activated": True, "payload": payload}

    monkeypatch.setattr(fanxiu, "_activate_game_window2_service", fake_activate_game_window2_service)

    response = fanxiu.activate_fanxiu_game_window2_service(
        fanxiu.FanxiuGameWindow2ServiceActivateRequest(
            title="凡人修仙传",
            title_match="exact",
            click_title=False,
        ),
        _token_device=object(),
    )

    assert response["ok"] is True
    assert calls["payload"] == {
        "title": "凡人修仙传",
        "title_match": "exact",
        "click_title": False,
    }
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_click_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_click_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "clicked": True, "payload": payload}

    monkeypatch.setattr(fanxiu, "_click_game_window2_service", fake_click_game_window2_service)

    response = fanxiu.click_fanxiu_game_window2_service(
        fanxiu.FanxiuGameWindow2ServiceClickRequest(
            x=12.5,
            y=34.75,
            title="凡人修仙传",
            title_match="exact",
            mode="printwindow",
            area="outer",
            crop="1,2,3,4",
            rotate="cw",
            fixed_width=1280,
            fixed_height=720,
            frame_width=1920,
            frame_height=1080,
            input_backend="desktop",
        ),
        _token_device=object(),
    )

    assert response["ok"] is True
    assert calls["payload"] == {
        "x": 12.5,
        "y": 34.75,
        "title": "凡人修仙传",
        "title_match": "exact",
        "mode": "printwindow",
        "area": "outer",
        "crop": "1,2,3,4",
        "rotate": "cw",
        "fixed_width": 1280,
        "fixed_height": 720,
        "frame_width": 1920,
        "frame_height": 1080,
        "input_backend": "desktop",
    }
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_drag_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_drag_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "dragged": True, "payload": payload}

    monkeypatch.setattr(fanxiu, "_drag_game_window2_service", fake_drag_game_window2_service)

    response = fanxiu.drag_fanxiu_game_window2_service(
        fanxiu.FanxiuGameWindow2ServiceDragRequest(
            start_x=10.0,
            start_y=20.0,
            end_x=110.0,
            end_y=220.0,
            duration_ms=650,
            title="凡人修仙传",
            title_match="exact",
            mode="printwindow",
            area="outer",
            crop="1,2,3,4",
            trim_border="5,6,7,8",
            rotate="cw",
            fixed_width=1280,
            fixed_height=720,
            frame_width=1920,
            frame_height=1080,
            input_backend="desktop",
        ),
        _token_device=object(),
    )

    assert response["ok"] is True
    assert calls["payload"] == {
        "start_x": 10.0,
        "start_y": 20.0,
        "end_x": 110.0,
        "end_y": 220.0,
        "duration_ms": 650,
        "title": "凡人修仙传",
        "title_match": "exact",
        "mode": "printwindow",
        "area": "outer",
        "crop": "1,2,3,4",
        "trim_border": "5,6,7,8",
        "rotate": "cw",
        "fixed_width": 1280,
        "fixed_height": 720,
        "frame_width": 1920,
        "frame_height": 1080,
        "input_backend": "desktop",
    }
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_keyevent_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_keyevent_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "sent": payload}

    monkeypatch.setattr(fanxiu, "_keyevent_game_window2_service", fake_keyevent_game_window2_service)

    response = fanxiu.keyevent_fanxiu_game_window2_service(
        fanxiu.FanxiuGameWindow2ServiceKeyeventRequest(
            key="ENTER",
            keys=["CTRL", "S"],
        ),
        _token_device=object(),
    )

    assert response == {"ok": True, "sent": {"key": "ENTER", "keys": ["CTRL", "S"]}}
    assert calls["payload"] == {"key": "ENTER", "keys": ["CTRL", "S"]}
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_text_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_text_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "typed": payload["text"]}

    monkeypatch.setattr(fanxiu, "_text_game_window2_service", fake_text_game_window2_service)

    response = fanxiu.text_fanxiu_game_window2_service(
        fanxiu.FanxiuGameWindow2ServiceTextRequest(text="测试输入"),
        _token_device=object(),
    )

    assert response == {"ok": True, "typed": "测试输入"}
    assert calls["payload"] == {"text": "测试输入"}
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_save_frame_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_save_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "saved": payload.get("overwrite_filename")}

    monkeypatch.setattr(fanxiu, "_save_game_window2_service", fake_save_game_window2_service)

    response = fanxiu.save_fanxiu_game_window2_frame_service(
        fanxiu.FanxiuGameWindow2ServiceSaveFrameRequest(
            title="凡人修仙传",
            title_match="exact",
            mode="printwindow",
            area="outer",
            crop="1,2,3,4",
            trim_border="5,6,7,8",
            rotate="cw",
            fixed_width=1280,
            fixed_height=720,
            quality=91,
            current_frame_data_url="data:image/png;base64,AAAA",
            overwrite_filename="frame-001.png",
        ),
        _token_device=object(),
    )

    assert response == {"ok": True, "saved": "frame-001.png"}
    assert calls["payload"] == {
        "title": "凡人修仙传",
        "title_match": "exact",
        "mode": "printwindow",
        "area": "outer",
        "crop": "1,2,3,4",
        "trim_border": "5,6,7,8",
        "rotate": "cw",
        "fixed_width": 1280,
        "fixed_height": 720,
        "quality": 91,
        "current_frame_data_url": "data:image/png;base64,AAAA",
        "overwrite_filename": "frame-001.png",
    }
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_match_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_match_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "matched": True, "filename": payload["filename"]}

    monkeypatch.setattr(fanxiu, "_match_game_window2_service", fake_match_game_window2_service)

    response = fanxiu.match_fanxiu_game_window2_screenshot_box_service(
        fanxiu.FanxiuGameWindow2ServiceMatchRequest(
            filename="frame-001.png",
            box=fanxiu.FanxiuGameWindow2MatchBox(name="按钮", x=10, y=20, w=30, h=40),
            scan=True,
            scan_box=fanxiu.FanxiuGameWindow2MatchBox(name="区域", x=1, y=2, w=300, h=400),
            pixel_tolerance=9,
            alpha_mask_data_url="data:image/png;base64,ALPHA",
            title="凡人修仙传",
            title_match="exact",
            mode="printwindow",
            area="outer",
            crop="1,2,3,4",
            rotate="cw",
            fixed_width=1280,
            fixed_height=720,
            current_frame_data_url="data:image/png;base64,FRAME",
            prefer_cached=False,
            quality=91,
            match_strategy="anchor_pixel",
            match_search_radius=12,
            ocr_enabled=True,
            ocr_text="确认",
            ocr_match_mode="exact",
            ocr_min_confidence=0.87,
            read_only_cache=True,
            save_match_frame=False,
            debug_match=True,
        ),
        _token_device=object(),
    )

    assert response == {"ok": True, "matched": True, "filename": "frame-001.png"}
    assert calls["payload"] == {
        "filename": "frame-001.png",
        "box": {"name": "按钮", "x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0},
        "scan": True,
        "scan_box": {"name": "区域", "x": 1.0, "y": 2.0, "w": 300.0, "h": 400.0},
        "pixel_tolerance": 9,
        "alpha_mask_data_url": "data:image/png;base64,ALPHA",
        "title": "凡人修仙传",
        "title_match": "exact",
        "mode": "printwindow",
        "area": "outer",
        "crop": "1,2,3,4",
        "rotate": "cw",
        "fixed_width": 1280,
        "fixed_height": 720,
        "current_frame_data_url": "data:image/png;base64,FRAME",
        "prefer_cached": False,
        "quality": 91,
        "match_strategy": "anchor_pixel",
        "match_search_radius": 12,
        "ocr_enabled": True,
        "ocr_text": "确认",
        "ocr_match_mode": "exact",
        "ocr_min_confidence": 0.87,
        "read_only_cache": True,
        "save_match_frame": False,
        "debug_match": True,
    }
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_burst_save_endpoint_forwards_payload_to_local_service(monkeypatch):
    calls = {}

    def fake_save_burst_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "burst_saved": payload.get("overwrite_filename")}

    monkeypatch.setattr(fanxiu, "_save_burst_game_window2_service", fake_save_burst_game_window2_service)

    response = fanxiu.save_fanxiu_game_window2_burst_frame_service(
        fanxiu.FanxiuGameWindow2ServiceBurstFrameRequest(
            title="凡人修仙传",
            title_match="exact",
            mode="printwindow",
            area="outer",
            crop="1,2,3,4",
            trim_border="5,6,7,8",
            rotate="cw",
            fixed_width=1280,
            fixed_height=720,
            quality=91,
            current_frame_data_url="data:image/png;base64,AAAA",
            overwrite_filename="burst-001.png",
        ),
        _token_device=object(),
    )

    assert response == {"ok": True, "burst_saved": "burst-001.png"}
    assert calls["payload"] == {
        "title": "凡人修仙传",
        "title_match": "exact",
        "mode": "printwindow",
        "area": "outer",
        "crop": "1,2,3,4",
        "trim_border": "5,6,7,8",
        "rotate": "cw",
        "fixed_width": 1280,
        "fixed_height": 720,
        "quality": 91,
        "current_frame_data_url": "data:image/png;base64,AAAA",
        "overwrite_filename": "burst-001.png",
    }
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_burst_list_endpoint_forwards_paging_payload(monkeypatch):
    calls = {}

    def fake_list_burst_game_window2_service(payload):
        calls["payload"] = payload
        return {"ok": True, "items": [], "page": payload["page"], "page_size": payload["page_size"]}

    monkeypatch.setattr(fanxiu, "_list_burst_game_window2_service", fake_list_burst_game_window2_service)

    response = fanxiu.list_fanxiu_game_window2_burst_frames_service(
        fanxiu.FanxiuGameWindow2ServiceBurstListRequest(page=3, page_size=12),
        _token_device=object(),
    )

    assert response == {"ok": True, "items": [], "page": 3, "page_size": 12}
    assert calls["payload"] == {"page": 3, "page_size": 12}
    assert "entry_id" not in calls["payload"]


def test_game_window2_service_screenshot_pre_label_endpoint_forwards_filename(monkeypatch):
    calls = {}

    def fake_screenshot_game_window2_service_pre_label(filename):
        calls["filename"] = filename
        return {"ok": True, "filename": filename, "pre_label": {"shapes": []}}

    monkeypatch.setattr(
        fanxiu,
        "_screenshot_game_window2_service_pre_label",
        fake_screenshot_game_window2_service_pre_label,
    )

    response = fanxiu.get_fanxiu_game_window2_screenshot_pre_label_service(
        fanxiu.FanxiuGameWindow2ServiceScreenshotPreLabelRequest(filename="frame-001.png"),
        _token_device=object(),
    )

    assert response == {"ok": True, "filename": "frame-001.png", "pre_label": {"shapes": []}}
    assert calls["filename"] == "frame-001.png"


def test_game_window2_service_screenshot_pre_label_save_endpoint_forwards_payload(monkeypatch):
    calls = {}

    def fake_save_screenshot_game_window2_service_pre_label(filename, payload):
        calls["filename"] = filename
        calls["payload"] = payload
        return {"ok": True, "filename": filename, "saved": payload}

    monkeypatch.setattr(
        fanxiu,
        "_save_screenshot_game_window2_service_pre_label",
        fake_save_screenshot_game_window2_service_pre_label,
    )

    payload = {
        "version": "5.0.0",
        "shapes": [{"label": "入口", "points": [[1, 2], [3, 4]]}],
    }
    response = fanxiu.save_fanxiu_game_window2_screenshot_pre_label_service(
        fanxiu.FanxiuGameWindow2ServiceScreenshotPreLabelSaveRequest(
            filename="frame-001.png",
            payload=payload,
        ),
        _token_device=object(),
    )

    assert response == {"ok": True, "filename": "frame-001.png", "saved": payload}
    assert calls == {"filename": "frame-001.png", "payload": payload}


def test_game_window2_service_screenshot_delete_endpoint_forwards_filename(monkeypatch):
    calls = {}

    def fake_delete_screenshot_game_window2_service_image(filename):
        calls["filename"] = filename
        return {"ok": True, "deleted": filename}

    monkeypatch.setattr(
        fanxiu,
        "_delete_screenshot_game_window2_service_image",
        fake_delete_screenshot_game_window2_service_image,
    )

    response = fanxiu.delete_fanxiu_game_window2_screenshot_service(
        fanxiu.FanxiuGameWindow2ServiceScreenshotDeleteRequest(filename="frame-001.png"),
        _token_device=object(),
    )

    assert response == {"ok": True, "deleted": "frame-001.png"}
    assert calls["filename"] == "frame-001.png"


def test_data_annotation_scheduler_daily_next_time_uses_next_clock():
    task = {
        "schedule_kind": "daily",
        "schedule_times": ["05:00", "00:00"],
    }

    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 2, 4, 0)) == "2026-06-02 05:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 2, 6, 0)) == "2026-06-03 00:00:00"


def test_data_annotation_scheduler_weekly_next_time_uses_weekday_and_clock():
    task = {
        "schedule_kind": "weekly",
        "weekdays": [0],
        "schedule_times": ["00:05", "05:05"],
    }

    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 29, 0, 1)) == "2026-06-29 00:05:00"
    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 29, 1, 0)) == "2026-06-29 05:05:00"
    assert fanxiu._next_data_annotation_scheduler_time(task, datetime(2026, 6, 29, 6, 0)) == "2026-07-06 00:05:00"


def test_scheduler_repair_fills_missing_weekly_next_time(tmp_path):
    scheduler_path = _scheduler_state_path(tmp_path)
    runtime_control.write_scheduler_tasks(
        [
            {
                "id": "daily-weekly-dungeon",
                "task_type": "daily_weekly_dungeon",
                "label": "日常_周本",
                "enabled": True,
                "source": "data_annotation_runtime",
                "schedule_kind": "weekly",
                "weekdays": [0],
                "schedule_times": ["05:00"],
                "next_time": None,
                "last_run_at": None,
                "last_result": "",
                "retry_after": None,
            }
        ],
        scheduler_state_path=scheduler_path,
        preserve_runtime_state=False,
    )

    tasks = runtime_control.read_scheduler_tasks(
        scheduler_state_path=scheduler_path,
        world_facts_path=tmp_path / "world_facts.json",
        now=datetime(2026, 7, 9, 15, 40, 0),
    )
    weekly = next(item for item in tasks if item["id"] == "daily-weekly-dungeon")

    assert weekly["next_time"] == "2026-07-13 05:00:00"


def test_data_annotation_scheduler_advance_next_marks_success_for_next_daily_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 30, 23, 58, 0)

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "daily-test",
            "task_type": "daily_test",
            "label": "日常测试",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "next_time": "2026-06-30 23:55:00",
            "schedule_times": ["23:55"],
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ])

    updated_tasks = fanxiu._advance_data_annotation_scheduler_task_to_next_trigger("daily-test")
    updated = next(item for item in updated_tasks if item["id"] == "daily-test")

    assert updated["last_result"] == "success"
    assert updated["last_run_at"] == "2026-06-30 23:58:00"
    assert updated["next_time"] == "2026-07-01 23:55:00"
    assert updated["retry_after"] is None
    assert updated["scheduler_meta"]["manual_advance_next_at"] == "2026-06-30 23:58:00"


def test_data_annotation_scheduler_advance_next_marks_success_for_next_weekly_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 30, 23, 58, 0)

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "daily-weekly-dungeon",
            "task_type": "daily_weekly_dungeon",
            "label": "日常_周本",
            "source": "data_annotation_runtime",
            "schedule_kind": "weekly",
            "enabled": True,
            "next_time": "2026-06-30 23:58:00",
            "weekdays": [0],
            "schedule_times": ["05:00"],
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ])

    updated_tasks = fanxiu._advance_data_annotation_scheduler_task_to_next_trigger("daily-weekly-dungeon")
    updated = next(item for item in updated_tasks if item["id"] == "daily-weekly-dungeon")

    assert updated["last_result"] == "success"
    assert updated["last_run_at"] == "2026-06-30 23:58:00"
    assert updated["next_time"] == "2026-07-06 05:00:00"
    assert updated["retry_after"] is None


def test_data_annotation_scheduler_normalize_preserves_weekdays():
    task = data_annotation_state.normalize_data_annotation_scheduler_task({
        "id": "xianshi-weekly-resources",
        "task_type": "xianshi_weekly_resources",
        "schedule_kind": "weekly",
        "weekdays": ["0", 7, "bad", 2],
        "schedule_times": ["00:05"],
    })

    assert task["weekdays"] == [0, 2]


def test_xianshi_weekly_resources_rejects_unlisted_wanling_box_name():
    runner = create_fanxiu_runtime_runner()

    assert runner._classify_xianshi_weekly_resource_name("万灵珍品宝匣二") is None


def test_xianshi_weekly_resources_unknown_detail_returns_without_claiming():
    runner = create_fanxiu_runtime_runner()
    actions = []

    class FakeRuntime:
        def ocr_text_in_shapes(self, scene_id, shape_titles, padding=0):
            actions.append(("ocr", scene_id, tuple(shape_titles), padding))
            return "万灵珍品宝匣二"

        def wait_click(self, scene_id, shape_title, *args, **kwargs):
            actions.append(("wait_click", scene_id, shape_title))
            if False:
                yield None
            return True

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id, **kwargs):
            actions.append(("wait_click_then_view", scene_id, shape_title, target_scene_id))
            if shape_title == "返回":
                raise RuntimeError("missing shape")
            if False:
                yield None
            return True

    result = _drain_generator(runner._claim_current_xianshi_weekly_resource_detail(FakeRuntime(), "灵兽", slot="第1个物品"))

    assert result is None
    assert ("wait_click", 316, "领取") not in actions
    assert ("wait_click_then_view", 316, "shape 3", 247) in actions


def test_xianshi_weekly_resources_midnight_unknown_slot_returns_world(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    actions = []

    class FakeRuntime:
        def current_scene(self, candidates, update=False):
            return 247, 100, object()

        def wait_click_then_shape(self, *args, **kwargs):
            actions.append(("wait_click_then_shape", args))
            if False:
                yield None
            return True

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id, **kwargs):
            actions.append(("wait_click_then_view", scene_id, shape_title, target_scene_id))
            if scene_id == 247 and shape_title == "第1个物品":
                if False:
                    yield None
                return True
            if scene_id == 316 and shape_title == "返回":
                raise RuntimeError("missing shape")
            if False:
                yield None
            return True

        def ocr_text_in_shapes(self, scene_id, shape_titles, padding=0):
            return "万灵珍品宝匣二"

        def wait_click(self, scene_id, shape_title, *args, **kwargs):
            actions.append(("wait_click", scene_id, shape_title))
            if False:
                yield None
            return True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            247: {"title": "秘藏阁"},
        },
    }
    result = _drain_generator(runner._execute_xianshi_weekly_resources_task(ctx, fanxiu.threading.Event(), {"phase": "midnight"}))

    assert result == "success"
    assert ("wait_click", 316, "领取") not in actions
    assert ("wait_click_then_view", 247, "返回", 34) in actions


def test_xianshi_weekly_resources_midnight_counts_current_detail_as_one_attempt(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    actions = []
    ocr_texts = iter(["万灵珍品宝匣二", "御兽灵兽宝匣"])
    monkeypatch.setattr(fanxiu_daily_resources, "current_prayer_cycle", lambda: "灵兽")

    class FakeRuntime:
        def current_scene(self, candidates, update=False):
            return 316, 100, object()

        def wait_view(self, scene_id, **kwargs):
            actions.append(("wait_view", scene_id))
            if False:
                yield None
            return True

        def wait_click_then_shape(self, *args, **kwargs):
            actions.append(("wait_click_then_shape", args))
            if False:
                yield None
            return True

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id, **kwargs):
            actions.append(("wait_click_then_view", scene_id, shape_title, target_scene_id))
            if scene_id == 316 and shape_title == "返回":
                raise RuntimeError("missing shape")
            if False:
                yield None
            return True

        def ocr_text_in_shapes(self, scene_id, shape_titles, padding=0):
            return next(ocr_texts)

        def wait_click(self, scene_id, shape_title, *args, **kwargs):
            actions.append(("wait_click", scene_id, shape_title))
            if False:
                yield None
            return True

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None
            return True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            247: {"title": "秘藏阁"},
        },
    }

    result = _drain_generator(runner._execute_xianshi_weekly_resources_task(ctx, fanxiu.threading.Event(), {"phase": "midnight"}))

    assert result == "success"
    assert actions.count(("wait_click_then_view", 247, "第1个物品", 316)) == 1
    assert actions.count(("wait_click", 316, "领取")) == 1
    assert ("wait_click_then_view", 247, "返回", 34) in actions


def test_xianshi_weekly_resources_after_reset_caps_list_attempts_at_eight(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    actions = []
    monkeypatch.setattr(fanxiu_daily_resources, "next_prayer_cycle", lambda: "炼丹")

    class FakeRuntime:
        def current_scene(self, candidates, update=False):
            return 247, 100, object()

        def wait_click_then_shape(self, *args, **kwargs):
            actions.append(("wait_click_then_shape", args))
            if False:
                yield None
            return True

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id, **kwargs):
            actions.append(("wait_click_then_view", scene_id, shape_title, target_scene_id))
            if False:
                yield None
            return True

        def ocr_text_in_shapes(self, scene_id, shape_titles, padding=0):
            return {
                "洗灵": "洗灵宝匣",
                "仙花": "花神宝匣",
                "灵兽": "御兽宝匣",
                "淬体": "玄魄宝匣",
            }[self.expected_group]

        def wait_click(self, scene_id, shape_title, *args, **kwargs):
            actions.append(("wait_click", scene_id, shape_title))
            if False:
                yield None
            return True

        def wait_action_settle(self, seconds):
            if False:
                yield None
            return True

    runtime = FakeRuntime()
    original_claim_slot = runner._claim_xianshi_weekly_resource_slot

    def fake_claim_slot(runtime_arg, slot, expected_group):
        runtime_arg.expected_group = expected_group
        return original_claim_slot(runtime_arg, slot, expected_group)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: runtime)
    monkeypatch.setattr(runner, "_claim_xianshi_weekly_resource_slot", fake_claim_slot)
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            34: {"title": "世界"},
            247: {"title": "秘藏阁"},
        },
    }

    result = _drain_generator(runner._execute_xianshi_weekly_resources_task(ctx, fanxiu.threading.Event(), {"phase": "after_reset"}))

    list_clicks = [action for action in actions if action[:2] == ("wait_click_then_view", 247) and action[2] != "返回"]
    assert result == "success"
    assert len(list_clicks) == 8
    assert all(action[2] in {"第1个物品", "第3个物品"} for action in list_clicks)
    assert ("wait_click_then_view", 247, "返回", 34) in actions


def test_reset_scheduler_task_runs_clears_runtime_fields_and_world_facts(tmp_path, monkeypatch):
    scheduler_path = _scheduler_state_path(tmp_path)
    world_facts_path = tmp_path / "world_facts.json"
    backup_root = tmp_path / "backup"
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *_parts: backup_root)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 20, 12, 30, 0)

    monkeypatch.setattr(runtime_control, "datetime", FixedDatetime)
    tasks = [
        {
            "id": "legacy-daily-assistant",
            "task_type": "daily_assistant",
            "label": "日常_助手",
            "enabled": True,
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "schedule_times": ["12:00"],
            "next_time": "2026-06-20 18:00:00",
            "last_run_at": "2026-06-20 12:24:51",
            "last_result": "success",
            "last_message": "旧成功不可信",
            "retry_after": "2026-06-20 12:34:51",
            "payload": {"__scheduler_definition_task_type": "daily_assistant"},
        },
        {
            "id": "disabled-task",
            "task_type": "daily_signup",
            "label": "日常_报名",
            "enabled": False,
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "schedule_times": ["05:00"],
            "next_time": "2026-06-21 05:00:00",
            "last_result": "success",
        },
    ]
    runtime_control.write_scheduler_tasks(tasks, scheduler_state_path=scheduler_path)
    runtime_control.write_world_facts(
        {"discoveries": {"task": {"legacy-daily-assistant": {"last_result": "success", "next_time": "2026-06-20 18:00:00"}}}},
        world_facts_path,
    )

    result = runtime_control.reset_scheduler_task_runs(
        task_ids=["legacy-daily-assistant"],
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
    )

    assert result["reset_ids"] == ["legacy-daily-assistant"]
    assert Path(result["backup_path"]).is_file()
    current_tasks = runtime_control.read_scheduler_tasks(scheduler_state_path=scheduler_path, world_facts_path=world_facts_path)
    reset_task = next(item for item in current_tasks if item["id"] == "legacy-daily-assistant")
    assert reset_task["last_result"] == ""
    assert reset_task["last_run_at"] is None
    assert reset_task.get("last_message") is None
    assert reset_task["retry_after"] is None
    assert reset_task["next_time"] == "2026-06-20 00:00:00"
    disabled = next(item for item in current_tasks if item["id"] == "disabled-task")
    assert disabled["last_result"] == "success"
    facts = runtime_control.read_world_facts(world_facts_path)
    assert "legacy-daily-assistant" not in facts["discoveries"]["task"]


def test_reset_scheduler_task_runs_can_explicitly_clear_next_time(tmp_path, monkeypatch):
    scheduler_path = _scheduler_state_path(tmp_path)
    world_facts_path = tmp_path / "world_facts.json"
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *_parts: tmp_path / "backup")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 6, 20, 12, 30, 0)

    monkeypatch.setattr(runtime_control, "datetime", FixedDatetime)
    runtime_control.write_scheduler_tasks(
        [
            {
                "id": "daily-test",
                "task_type": "daily_assistant",
                "label": "日常测试",
                "enabled": True,
                "source": "data_annotation_runtime",
                "schedule_kind": "daily",
                "schedule_times": ["00:00"],
                "next_time": "2026-06-20 18:00:00",
                "last_result": "success",
            }
        ],
        scheduler_state_path=scheduler_path,
    )

    runtime_control.reset_scheduler_task_runs(
        task_ids=["daily-test"],
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
        clear_next_time=True,
    )

    current_tasks = runtime_control.read_scheduler_tasks(scheduler_state_path=scheduler_path, world_facts_path=world_facts_path)
    reset_task = next(item for item in current_tasks if item["id"] == "daily-test")
    assert reset_task["next_time"] == "2026-06-20 00:00:00"


def test_reset_scheduler_task_runs_keeps_weekly_next_time_by_default(tmp_path, monkeypatch):
    scheduler_path = _scheduler_state_path(tmp_path)
    world_facts_path = tmp_path / "world_facts.json"
    monkeypatch.setattr(runtime_control, "codeyun_temp_root", lambda *_parts: tmp_path / "backup")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 9, 15, 30, 0)

    monkeypatch.setattr(runtime_control, "datetime", FixedDatetime)
    runtime_control.write_scheduler_tasks(
        [
            {
                "id": "daily-weekly-dungeon",
                "task_type": "daily_weekly_dungeon",
                "label": "日常_周本",
                "enabled": True,
                "source": "data_annotation_runtime",
                "schedule_kind": "weekly",
                "weekdays": [0],
                "schedule_times": ["05:00"],
                "next_time": "2026-07-13 05:00:00",
                "last_run_at": "2026-07-06 11:35:07",
                "last_result": "success",
            }
        ],
        scheduler_state_path=scheduler_path,
    )

    runtime_control.reset_scheduler_task_runs(
        task_ids=["daily-weekly-dungeon"],
        scheduler_state_path=scheduler_path,
        world_facts_path=world_facts_path,
    )

    current_tasks = runtime_control.read_scheduler_tasks(scheduler_state_path=scheduler_path, world_facts_path=world_facts_path)
    weekly = next(item for item in current_tasks if item["id"] == "daily-weekly-dungeon")
    assert weekly["last_result"] == ""
    assert weekly["last_run_at"] is None
    assert weekly["next_time"] == "2026-07-13 05:00:00"


def test_scheduler_write_preserves_existing_runtime_state_from_default_backfill(tmp_path):
    scheduler_path = _scheduler_state_path(tmp_path)
    runtime_control.write_scheduler_tasks(
        [
            {
                "id": "daily-weekly-dungeon",
                "task_type": "daily_weekly_dungeon",
                "label": "日常_周本",
                "enabled": True,
                "source": "data_annotation_runtime",
                "schedule_kind": "weekly",
                "weekdays": [0],
                "schedule_times": ["05:00"],
                "last_run_at": "2026-07-01 06:17:12",
                "last_result": "success",
                "next_time": "2026-07-06 05:00:00",
                "retry_after": None,
            }
        ],
        scheduler_state_path=scheduler_path,
    )

    runtime_control.write_scheduler_tasks(
        [
            {
                "id": "daily-weekly-dungeon",
                "task_type": "daily_weekly_dungeon",
                "label": "日常_周本",
                "enabled": True,
                "source": "data_annotation_runtime",
                "schedule_kind": "weekly",
                "weekdays": [0],
                "schedule_times": ["05:00"],
                "last_run_at": None,
                "last_result": "",
                "next_time": None,
                "retry_after": None,
            }
        ],
        scheduler_state_path=scheduler_path,
    )

    task = next(
        item
        for item in runtime_control.read_scheduler_tasks(scheduler_state_path=scheduler_path, world_facts_path=tmp_path / "world.json")
        if item["id"] == "daily-weekly-dungeon"
    )
    assert task["last_result"] == "success"
    assert task["last_run_at"] == "2026-07-01 06:17:12"
    assert task["next_time"] == "2026-07-06 05:00:00"


def test_world_facts_write_preserves_newer_scheduler_task_fact(tmp_path):
    world_facts_path = tmp_path / "world_facts.json"
    runtime_control.write_world_facts(
        {
            "discoveries": {
                "task": {
                    "daily-weekly-dungeon": {
                        "last_result": "success",
                        "last_run_at": "2026-07-01 06:17:12",
                        "next_time": "2026-07-06 05:00:00",
                        "updated_at": 200.0,
                    }
                }
            }
        },
        world_facts_path,
    )

    runtime_control.write_world_facts(
        {
            "runtime": {"current_scene": 34},
            "discoveries": {"task": {}},
            "events": [],
        },
        world_facts_path,
    )

    fact = runtime_control.read_world_facts(world_facts_path)["discoveries"]["task"]["daily-weekly-dungeon"]
    assert fact["last_result"] == "success"
    assert fact["next_time"] == "2026-07-06 05:00:00"


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
    assert signup["next_time"] == "2026-06-02 00:00:00"
    assert assistant["enabled"] is True
    assert assistant["next_time"] == "2026-06-02 00:00:00"


def test_data_annotation_scheduler_read_retries_enabled_failed_runtime_task(tmp_path, monkeypatch):
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
            "id": "legacy-daily-signup",
            "task_type": "daily_signup",
            "label": "日常_报名",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:00", "05:00"],
            "last_result": "stopped",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": None,
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_signup"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    task = next(item for item in tasks if item["id"] == "legacy-daily-signup")

    assert task["last_result"] == "stopped"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


def test_data_annotation_scheduler_read_retries_failed_runtime_task(tmp_path, monkeypatch):
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
            "id": "legacy-daily-xianshi",
            "task_type": "daily_xianshi",
            "label": "日常_仙市",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "error",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": "2026-06-03 05:00:00",
            "retry_after": "2026-06-02 06:10:00",
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_xianshi"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    task = next(item for item in tasks if item["id"] == "legacy-daily-xianshi")

    assert task["last_result"] == "error"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


@pytest.mark.parametrize(
    ("fixed_now", "expected_next_time"),
    [
        (datetime(2026, 7, 3, 7, 0, 0), "2026-07-03 14:00:00"),
        (datetime(2026, 7, 3, 11, 0, 0), "2026-07-03 14:00:00"),
        (datetime(2026, 7, 3, 23, 0, 0), "2026-07-04 14:00:00"),
    ],
)
def test_data_annotation_scheduler_windowed_daily_error_defers_to_next_trigger(tmp_path, monkeypatch, fixed_now, expected_next_time):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-dongtian",
            "task_type": "daily_dongtian",
            "label": "日常_洞天福地",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["14:00"],
            "window": ["10:00", "22:00"],
            "last_result": "error",
            "last_run_at": "2026-07-03 07:00:00",
            "next_time": expected_next_time,
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_dongtian"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    task = next(item for item in tasks if item["id"] == "legacy-daily-dongtian")

    assert task["last_result"] == "error"
    assert task["next_time"] == expected_next_time
    assert task["retry_after"] is None


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
            "id": "mail-selective-claim",
            "task_type": "mail_selective_claim",
            "label": "邮件_选择性领取",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["00:05"],
            "last_result": "stopped",
            "last_run_at": "2026-06-02 00:16:35",
            "next_time": "2026-06-03 00:05:00",
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "mail_selective_claim"},
        }
    ])

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    mail = next(item for item in tasks if item["id"] == "mail-selective-claim")

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


def test_data_annotation_scheduler_sync_retries_failed_runtime_task(tmp_path, monkeypatch):
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
            "id": "legacy-daily-xianshi",
            "task_type": "daily_xianshi",
            "label": "日常_仙市",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "error",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": None,
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_xianshi"},
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "legacy-daily-xianshi": {
                    "id": "legacy-daily-xianshi",
                    "task_type": "daily_xianshi",
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
    task = next(item for item in tasks if item["id"] == "legacy-daily-xianshi")

    assert task["last_result"] == "error"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


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


def test_data_annotation_scheduler_sync_keeps_successful_runtime_task(tmp_path, monkeypatch):
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
            "id": "legacy-daily-xianshi",
            "task_type": "daily_xianshi",
            "label": "日常_仙市",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "last_result": "skipped",
            "last_run_at": "2026-06-02 05:58:00",
            "next_time": None,
            "retry_after": "2026-06-02 05:50:00",
            "cooldown_seconds": 600,
            "payload": {"__scheduler_definition_task_type": "daily_xianshi"},
        }
    ])
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "legacy-daily-xianshi": {
                    "id": "legacy-daily-xianshi",
                    "task_type": "daily_xianshi",
                    "last_result": "success",
                    "last_run_at": "2026-06-02 05:58:00",
                    "discovered_next_time": "2026-06-03 00:00:00",
                    "retry_after": None,
                    "updated_at": datetime(2026, 6, 2, 5, 58, 0).timestamp(),
                }
            }
        }
    })

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    task = next(item for item in tasks if item["id"] == "legacy-daily-xianshi")

    assert task["last_result"] == "success"
    assert task["next_time"] == "2026-06-03 00:00:00"
    assert task["retry_after"] is None


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


def test_xianfu_dynamic_initial_check_gets_backend_next_time():
    tasks = []
    for task_id in ("xianfu-visit-partner", "xianfu-learn-skill"):
        task = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == task_id).copy()
        task.update({
            "enabled": True,
            "schedule_kind": "dynamic",
            "next_time": None,
            "retry_after": None,
            "last_result": "",
        })
        tasks.append(task)

    repaired, changed = scheduler_core.repair_data_annotation_scheduler_tasks(
        tasks,
        tasks,
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 2, 6, 0, 0),
    )

    assert changed is True
    by_id = {task["id"]: task for task in repaired}
    assert by_id["xianfu-visit-partner"]["next_time"] == "2026-06-02 06:30:00"
    assert by_id["xianfu-learn-skill"]["next_time"] == "2026-06-02 06:30:00"
    assert by_id["xianfu-visit-partner"]["retry_after"] is None
    assert by_id["xianfu-learn-skill"]["retry_after"] is None


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
    assert signup["scheduler_meta"]["world_fact_synced_at"]


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
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
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
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
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
            "scheduler_meta": None,
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
    legacy_item = next(item for item in plan["tasks"] if item["id"] == "legacy-daily-assistant")
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






def test_scheduler_plan_keeps_due_tasks_but_marks_job_group_disabled(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(fanxiu.time, "time", lambda: datetime(2026, 6, 2, 12, 0).timestamp())
    runtime_control.set_scheduler_job_group_enabled(False, scheduler_settings_path=_scheduler_settings_path(tmp_path))
    monkeypatch.setattr(
        runtime_control,
        "scheduler_blocking_overlays",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("blocking overlay check should be skipped")),
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
            "next_time": "2026-06-02 04:00:00",
            "payload": {"codes": []},
        }
    ])

    plan = fanxiu._build_data_annotation_scheduler_plan()

    assert plan["job_group_enabled"] is False
    assert plan["next_action"] == "job_group_disabled"
    assert plan["due_tasks"][0]["id"] == "due-gift"
















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
            "scheduler_meta": None,
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
            "scheduler_meta": None,
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
    assert target["scheduler_meta"]["world_fact_updated_at"] == 123
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
            "scheduler_meta": None,
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
    assert target["scheduler_meta"]["world_fact_updated_at"] == 456


def test_data_annotation_scheduler_syncs_same_second_skipped_retry_from_world_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    last_run_at = "2026-07-01 12:42:43"
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "priority": 40,
            "interruptible": True,
            "next_time": "2026-07-02 05:00:00",
            "schedule_times": ["05:00"],
            "window": None,
            "last_run_at": last_run_at,
            "last_result": "skipped",
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"max_runtime_seconds": 1800},
            "scheduler_meta": None,
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
                    "task_type": "daily_boss",
                    "label": "日常_首领",
                    "last_result": "skipped",
                    "last_run_at": last_run_at,
                    "discovered_next_time": None,
                    "next_time": None,
                    "discovered_retry_after": "2026-07-01 12:49:18",
                    "retry_after": "2026-07-01 12:49:18",
                    "updated_at": datetime(2026, 7, 1, 12, 42, 43).timestamp(),
                }
            },
        },
    })

    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    target = next(item for item in tasks if item["id"] == "daily-boss")

    assert target["last_result"] == "skipped"
    assert target["next_time"] is None
    assert target["retry_after"] == "2026-07-01 12:49:18"


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










def test_data_annotation_scheduler_settings_enable_engineering_ensures_kernel(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    entry = type("Entry", (), {"entry_id": "resolved-mf-entry"})()
    calls = {}

    monkeypatch.setattr(fanxiu, "_get_user_device_or_404", lambda _session, _user, _entry_id: entry)

    def fake_set_kernel_enabled(**kwargs):
        calls.update(kwargs)
        return {"ok": True, "entry_id": kwargs["entry_id"], "status": "idle", "running": False}

    monkeypatch.setattr(fanxiu._runtime_framework, "set_kernel_enabled", fake_set_kernel_enabled)

    response = fanxiu.put_fanxiu_data_annotation_scheduler_settings(
        fanxiu.FanxiuDataAnnotationSchedulerSettingsRequest(job_group_enabled=True, entry_id="request-mf-entry"),
        current_user=object(),
        session=object(),
    )

    assert response.job_group_enabled is True
    assert calls["entry"] is entry
    assert calls["entry_id"] == "resolved-mf-entry"
    assert calls["enabled"] is True
    assert calls["asset_tree_path"] == tmp_path / "resolved-mf-entry.json"
    assert calls["scheduler_settings_path"] == _scheduler_settings_path(tmp_path)
    assert calls["runtime_state_path"] == tmp_path / "runtime_state.json"
    assert calls["world_facts_path"] == tmp_path / "world_facts.json"






def test_daily_xianmeng_clicks_current_293_attack(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            293: {"shapes": [{"title": "攻击"}]},
            294: {"shapes": [{"title": "确定"}]},
            295: {"shapes": [{"title": "点击关闭"}]},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        scene = 293

        def __init__(self):
            self.ctx = ctx
            self.stop_event = fanxiu.threading.Event()

        def wait_view(self, *scene_ids, **kwargs):
            actions.append(("wait_view", tuple(scene_ids), kwargs))
            if False:
                yield None
            return self.scene

        def wait_click(self, source, shape, **kwargs):
            actions.append(("wait_click", source, shape, kwargs))
            self.scene = 295
            if False:
                yield None
            return "success"

        def ocr_numbers_in_shapes(self, view_id, shape_titles, **kwargs):
            actions.append(("ocr_numbers_in_shapes", view_id, tuple(shape_titles), kwargs))
            return [], ""

        def wait_click_then_view(self, source, shape, target, **kwargs):
            actions.append(("wait_click_then_view", source, shape, target, kwargs.get("label")))
            self.scene = 293
            if False:
                yield None
            return self.scene

        def click_shape_center(self, scene_id, shape):
            actions.append(("click_shape_center", scene_id, shape))
            self.scene = 295 if scene_id == 293 else 293

        def click_frame_point(self, scene_id, x, y):
            actions.append(("click_frame_point", scene_id, x, y))

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

    def fake_wait_exact(runtime, *scene_ids, timeout):
        del scene_ids
        actions.append(("wait_view", (293, 295, 294), {"timeout": timeout}))
        if False:
            yield None
        return runtime.scene

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_xianmeng_exact_view", fake_wait_exact)
    result = _drain_generator(runner._execute_daily_xianmeng_task(ctx, fanxiu.threading.Event(), {"rounds": 50}))

    assert result == "success"
    attacks = [action for action in actions if action[0:3] == ("click_shape_center", 293, "攻击")]
    closes = [action for action in actions if action[0:3] == ("click_shape_center", 295, "点击关闭")]
    assert len(attacks) == 50
    assert len(closes) == 50
    assert actions[0] == ("wait_view", (293, 295, 294), {"timeout": 5.0})
    assert actions[1] == ("ocr_numbers_in_shapes", 293, ("次数",), {"padding": 16})
    assert actions[2] == ("click_shape_center", 293, "攻击")
    assert closes[0] == ("click_shape_center", 295, "点击关闭")


def test_daily_xianmeng_closes_295_to_293(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            293: {"shapes": [{"title": "攻击"}]},
            294: {"shapes": [{"title": "确定"}]},
            295: {"shapes": [{"title": "点击关闭"}]},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        scene = 295

        def __init__(self):
            self.ctx = ctx
            self.stop_event = fanxiu.threading.Event()

        def wait_view(self, *scene_ids, **kwargs):
            actions.append(("wait_view", tuple(scene_ids), kwargs))
            if False:
                yield None
            return self.scene

        def wait_click(self, source, shape, **kwargs):
            actions.append(("wait_click", source, shape, kwargs))
            self.scene = 295
            if False:
                yield None
            return "success"

        def ocr_numbers_in_shapes(self, view_id, shape_titles, **kwargs):
            actions.append(("ocr_numbers_in_shapes", view_id, tuple(shape_titles), kwargs))
            return [], ""

        def wait_click_then_view(self, source, shape, target, **kwargs):
            actions.append(("wait_click_then_view", source, shape, target, kwargs.get("label")))
            self.scene = 293 if source == 295 else 295
            if False:
                yield None
            return self.scene

        def click_shape_center(self, scene_id, shape):
            actions.append(("click_shape_center", scene_id, shape))
            self.scene = 293 if scene_id == 295 else 295

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

    def fake_wait_exact(runtime, *scene_ids, timeout):
        del scene_ids
        actions.append(("wait_view", (293, 295, 294), {"timeout": timeout}))
        if False:
            yield None
        return runtime.scene

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_xianmeng_exact_view", fake_wait_exact)
    result = _drain_generator(runner._execute_daily_xianmeng_task(ctx, fanxiu.threading.Event(), {"rounds": 50}))

    assert result == "success"
    attacks = [action for action in actions if action[0:3] == ("click_shape_center", 293, "攻击")]
    closes = [action for action in actions if action[0:3] == ("click_shape_center", 295, "点击关闭")]
    assert len(attacks) == 50
    assert len(closes) == 51
    assert actions[0] == ("wait_view", (293, 295, 294), {"timeout": 5.0})
    assert actions[1] == ("click_shape_center", 295, "点击关闭")


def test_daily_xianmeng_stops_when_293_count_below_3(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-xianmeng",
            "task_type": "daily_xianmeng",
            "label": "日常_仙盟",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "enabled": False,
            "interruptible": True,
            "next_time": None,
            "schedule_times": [],
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "cooldown_seconds": 600,
            "payload": {"max_runtime_seconds": 7200},
            "scheduler_meta": None,
        }
    ])
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            293: {"shapes": [{"title": "攻击"}, {"title": "次数"}]},
            294: {"shapes": [{"title": "确定"}]},
            295: {"shapes": [{"title": "点击关闭"}]},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def __init__(self):
            self.ctx = ctx
            self.stop_event = fanxiu.threading.Event()

        def wait_view(self, *scene_ids, **kwargs):
            actions.append(("wait_view", tuple(scene_ids), kwargs))
            if False:
                yield None
            return 293

        def ocr_numbers_in_shapes(self, view_id, shape_titles, **kwargs):
            actions.append(("ocr_numbers_in_shapes", view_id, tuple(shape_titles), kwargs))
            return [2], "2"

        def wait_click(self, source, shape, **kwargs):
            actions.append(("wait_click", source, shape, kwargs))
            if False:
                yield None
            return "success"

    def fake_wait_exact(_runtime, *scene_ids, timeout):
        del scene_ids
        actions.append(("wait_view", (293, 295, 294), {"timeout": timeout}))
        if False:
            yield None
        return 293

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_xianmeng_exact_view", fake_wait_exact)
    result = _drain_generator(runner._execute_daily_xianmeng_task(ctx, fanxiu.threading.Event(), {}))

    assert result == "success"
    assert actions == [
        ("wait_view", (293, 295, 294), {"timeout": 5.0}),
        ("ocr_numbers_in_shapes", 293, ("次数",), {"padding": 16}),
    ]
    facts = runtime_runner_core._read_data_annotation_world_facts()
    task_fact = ((facts.get("discoveries") or {}).get("task") or {}).get("legacy-daily-xianmeng")
    assert not task_fact or not task_fact.get("discovered_next_time")


@pytest.mark.parametrize(
    ("fixed_now", "expected_next_time"),
    [
        (datetime(2026, 7, 1, 7, 0, 0), "2026-07-01 21:30:00"),
        (datetime(2026, 6, 30, 23, 32, 0), "2026-07-01 21:30:00"),
    ],
)
def test_daily_lingmai_clear_outside_window_records_next_window(tmp_path, monkeypatch, fixed_now, expected_next_time):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: fixed_now)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingmai-clear",
            "task_type": "daily_lingmai_clear",
            "label": "灵脉_清体力",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "next_time": "2026-06-30 21:30:00",
            "schedule_times": ["21:30"],
            "window": ["10:00", "22:00"],
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ])
    runner = create_fanxiu_runtime_runner()

    result = _drain_generator(
        runner._execute_daily_lingmai_clear_task({}, fanxiu.threading.Event(), {"__scheduler_task_id": "legacy-daily-lingmai-clear"})
    )

    assert result == "skipped"
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-lingmai-clear"]
    assert fact["last_result"] == "skipped"
    assert fact["discovered_next_time"] == expected_next_time
    tasks = fanxiu._read_data_annotation_scheduler_tasks()
    runner._mark_scheduler_task(tasks, "legacy-daily-lingmai-clear", "skipped")
    updated = next(item for item in fanxiu._read_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-lingmai-clear")
    assert updated["next_time"] == expected_next_time
    assert updated["retry_after"] is None


@pytest.mark.parametrize(
    ("fixed_now", "expected_next_time"),
    [
        (datetime(2026, 7, 1, 7, 0, 0), "2026-07-01 21:30:00"),
        (datetime(2026, 6, 30, 23, 32, 0), "2026-07-01 21:30:00"),
    ],
)
def test_daily_dongtian_clear_outside_window_records_next_window(tmp_path, monkeypatch, fixed_now, expected_next_time):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: fixed_now)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-dongtian-clear",
            "task_type": "daily_dongtian_clear",
            "label": "日常_洞天福地_清行动力",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "next_time": "2026-06-30 21:30:00",
            "schedule_times": ["21:30"],
            "window": ["10:00", "22:00"],
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ])
    runner = create_fanxiu_runtime_runner()

    result = runner._execute_daily_dongtian_clear_task({}, fanxiu.threading.Event(), {"__scheduler_task_id": "legacy-daily-dongtian-clear"})

    assert result == "skipped"
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-dongtian-clear"]
    assert fact["last_result"] == "skipped"
    assert fact["discovered_next_time"] == expected_next_time


@pytest.mark.parametrize(
    ("fixed_now", "expected_next_time"),
    [
        (datetime(2026, 7, 3, 7, 0, 0), "2026-07-04 14:00:00"),
        (datetime(2026, 7, 3, 11, 0, 0), "2026-07-03 14:00:00"),
        (datetime(2026, 7, 3, 23, 0, 0), "2026-07-04 14:00:00"),
    ],
)
def test_daily_dongtian_outside_window_records_next_schedule(tmp_path, monkeypatch, fixed_now, expected_next_time):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: fixed_now)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-dongtian",
            "task_type": "daily_dongtian",
            "label": "日常_洞天福地",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "next_time": expected_next_time,
            "schedule_times": ["14:00"],
            "window": ["10:00", "22:00"],
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ])
    runner = create_fanxiu_runtime_runner()

    result = _drain_generator(
        runner._execute_daily_dongtian_task({}, fanxiu.threading.Event(), {"__scheduler_task_id": "legacy-daily-dongtian"})
    )

    assert result == "skipped"
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-dongtian"]
    assert fact["last_result"] == "skipped"
    assert fact["discovered_next_time"] == expected_next_time


def test_daily_lingmai_clear_inside_window_continues_runtime_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: tmp_path / "scheduler_tasks.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 30, 21, 45, 0))
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingmai-clear",
            "task_type": "daily_lingmai_clear",
            "label": "灵脉_清体力",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "next_time": "2026-06-30 21:30:00",
            "schedule_times": ["21:30"],
            "window": ["10:00", "22:00"],
            "last_result": "",
            "retry_after": None,
            "payload": {},
        }
    ])
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {285: {"id": 285, "title": "造化灵脉", "shapes": []}},
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 285, 100.0, "frame"

        def ocr_text(self, _frame):
            return "造化灵脉"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    continued: list[tuple[int, str]] = []

    def continue_lingmai(_runtime, _payload, *, task_label):
        continued.append((285, task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_continue_daily_lingmai_clear_from_zaohua", continue_lingmai)

    result = _drain_generator(
        runner._execute_daily_lingmai_clear_task(ctx, fanxiu.threading.Event(), {"__scheduler_task_id": "legacy-daily-lingmai-clear"})
    )

    assert result == "success"
    assert continued == [(285, "灵脉_清体力")]


def test_daily_green_bottle_baiye_first_step_goto_20(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            20: {"id": 20, "title": "绿瓶"},
            280: {"id": 280, "title": "掌天瓶"},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def goto_view(self, scene_id):
            actions.append(("goto_view", scene_id))
            if False:
                yield None
            return "success"

        def current_scene(self, scene_ids, **kwargs):
            actions.append(("current_scene", tuple(scene_ids), kwargs))
            return 20, 100.0, "frame"

        def wait_click(self, scene_id, shape_title):
            actions.append(("wait_click", scene_id, shape_title))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._execute_daily_green_bottle_baiye_task(
            ctx,
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result == "success"
    assert actions == [
        ("goto_view", 20),
        ("current_scene", (20,), {"update": True}),
        ("wait_click", 20, "绿瓶"),
        ("settle", 2.0),
        ("wait_click", 280, "境界排行"),
        ("settle", 2.0),
    ]


def test_daily_green_bottle_baiye_accepts_rank_scene_281(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {
            20: {"id": 20, "title": "绿瓶"},
            281: {"id": 281, "title": "掌天瓶"},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def goto_view(self, scene_id):
            actions.append(("goto_view", scene_id))
            if False:
                yield None
            return "success"

        def current_scene(self, scene_ids, **kwargs):
            actions.append(("current_scene", tuple(scene_ids), kwargs))
            return 20, 100.0, "frame"

        def wait_click(self, scene_id, shape_title):
            actions.append(("wait_click", scene_id, shape_title))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._execute_daily_green_bottle_baiye_task(
            ctx,
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result == "success"
    assert actions == [
        ("goto_view", 20),
        ("current_scene", (20,), {"update": True}),
        ("wait_click", 20, "绿瓶"),
        ("settle", 2.0),
        ("wait_click", 281, "境界排行"),
        ("settle", 2.0),
    ]


def test_daily_green_bottle_baiye_accepts_reward_result_text():
    runner = create_fanxiu_runtime_runner()

    assert runner._green_bottle_baiye_text_is_reward_result("恭喜获得 点击屏幕继续 2秒后自动关闭") is True
    assert runner._green_bottle_baiye_text_is_reward_result("剩余次数：0/1 拜谒") is False


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
    assert "legacy-daily-youli" not in by_id
    assert "legacy-daily-jianling" not in by_id
    assert "legacy-daily-yihuo" not in by_id
    assert by_id["legacy-daily-gongfeng"]["task_type"] == "daily_gongfeng"
    assert by_id["legacy-daily-xianshi"]["task_type"] == "daily_xianshi"
    assert by_id["xianshi-weekly-resources"]["task_type"] == "xianshi_weekly_resources"
    assert by_id["xianshi-weekly-resources"]["schedule_kind"] == "weekly"
    assert by_id["xianshi-weekly-resources"]["weekdays"] == [0]
    assert by_id["xianshi-weekly-resources"]["schedule_times"] == ["00:05", "05:05"]
    assert by_id["legacy-daily-xianmeng"]["task_type"] == "daily_xianmeng"
    assert by_id["legacy-daily-xianmeng"]["label"] == "日常_仙盟"
    assert by_id["legacy-daily-xianmeng"]["schedule_kind"] == "dynamic"
    assert by_id["legacy-daily-xianmeng"]["schedule_times"] == []
    assert by_id["legacy-daily-xianmeng"]["next_time"] is None
    assert by_id["legacy-daily-xianmeng"]["enabled"] is False
    assert by_id["legacy-daily-vip"]["task_type"] == "daily_vip"
    assert by_id["legacy-daily-vip"]["label"] == "日常_vip"
    assert by_id["legacy-daily-vip"]["enabled"] is True
    assert by_id["legacy-daily-vip"]["schedule_times"] == ["00:00"]
    assert by_id["legacy-daily-dongtian"]["label"] == "洞天_领取"
    assert by_id["legacy-daily-dongtian-clear"]["label"] == "洞天_行动力"
    assert by_id["legacy-daily-lingmai-clear"]["label"] == "灵脉_清体力"
    assert by_id["legacy-daily-mojie-raid"]["task_type"] == "daily_mojie_raid"
    assert by_id["legacy-daily-mojie-raid"]["label"] == "日常_奇袭魔界"
    assert by_id["legacy-daily-mojie-raid"]["enabled"] is True
    assert by_id["legacy-daily-mojie-raid"]["schedule_times"] == ["13:00", "21:30"]


def test_daily_vip_recovers_world_then_claims_free_xiuwei(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 14, 11, 30, 0))
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": asset_tree, "images": {scene_id: {"id": scene_id, "title": str(scene_id)} for scene_id in (34, 290, 291, 292)}}
    actions: list[tuple] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def __init__(self):
            self.scene = 75

        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return self.scene, 80.0, "frame"

        def goto_view(self, view_id):
            actions.append(("goto_view", view_id))
            self.scene = view_id
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", tuple(view_ids), kwargs))
            self.scene = view_ids[0]
            if False:
                yield BehaviorTreeStatus.RUNNING
            return view_ids[0]

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

        def click_shape_center(self, view_id, shape, **kwargs):
            actions.append(("click_shape_center", view_id, shape, kwargs))

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

        def cur_frame(self, **kwargs):
            actions.append(("cur_frame", kwargs))
            return "frame"

        def shape_score(self, view_id, shape, **kwargs):
            actions.append(("shape_score", view_id, shape, kwargs))
            return 100.0

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_vip_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert actions == [
        ("current_scene", (34,), {"update": True}),
        ("goto_view", 34),
        ("wait_view", (34,), {"label": "日常_vip：等待世界 #34"}),
        ("wait_click", 34, "[vip]", {"timeout": 8.0}),
        ("settle", 2.0),
        ("wait_view", (290,), {"label": "日常_vip：等待 VIP 月卡页 #290"}),
        ("wait_click", 290, "每日限购", {"timeout": 8.0}),
        ("settle", 1.5),
        ("wait_view", (291,), {"label": "日常_vip：等待每日限购页 #291"}),
        ("wait_click", 291, "修为", {"timeout": 8.0}),
        ("settle", 1.5),
        ("wait_view", (292,), {"label": "日常_vip：等待修为限购页 #292"}),
        ("cur_frame", {"update": True}),
        ("shape_score", 292, "免费", {"frame_data_url": "frame"}),
        ("wait_click", 292, "免费", {"timeout": 8.0}),
        ("settle", 1.5),
        ("click_shape_center", 292, "返回", {}),
        ("settle", 1.0),
        ("wait_view", (291,), {"timeout": 18.0, "label": "日常_vip：等待返回 #291"}),
        ("click_shape_center", 291, "返回", {}),
        ("settle", 1.0),
        ("wait_view", (290, 20, 34), {"timeout": 18.0, "label": "日常_vip：等待返回 #290/#20/#34"}),
        ("click_shape_center", 290, "返回", {}),
        ("settle", 1.0),
        ("wait_view", (34,), {"timeout": 18.0, "label": "日常_vip：等待返回 #34"}),
    ]
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-vip"]
    assert fact["task_type"] == "daily_vip"
    assert fact["discovered_next_time"] == "2026-06-15 00:00:00"


def test_daily_xianshi_missing_free_box_records_next_day(tmp_path, monkeypatch):
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

        def wait(self, _seconds):
            return False

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def fake_open_coin_list(_ctx, _stop_event, _payload, _image34, _image247, _image248, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (34, 80.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘藏阁 天衍灵石 仙币"}])
    monkeypatch.setattr(runner, "_open_daily_xianshi_coin_list", fake_open_coin_list)
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {"coin_box_retry_seconds": 600}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-xianshi"]
    assert fact["discovered_next_time"] == "2026-06-15 05:00:00"
    assert fact.get("discovered_retry_after") is None


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

        def wait(self, _seconds):
            return False

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def fake_open_coin_list(_ctx, _stop_event, _payload, _image34, _image247, _image248, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (34, 80.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘藏阁 天衍灵石 仙币"}])
    monkeypatch.setattr(runner, "_open_daily_xianshi_coin_list", fake_open_coin_list)
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

        def wait(self, _seconds):
            return False

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def fake_open_coin_list(_ctx, _stop_event, _payload, _image34, _image247, _image248, *, task_label):
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
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, _candidates=None: (34, 80.0))
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "秘藏阁 天衍灵石 仙币"}])
    monkeypatch.setattr(runner, "_open_daily_xianshi_coin_list", fake_open_coin_list)
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert observed == ["ocr_text"]


def test_daily_xianshi_recovers_to_world_before_opening_xianshi(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    images = {scene_id: {"id": scene_id, "title": str(scene_id), "width": 900, "height": 1600, "shapes": []} for scene_id in (34, 247, 248, 249, 250)}
    ctx = {"asset_tree_path": asset_tree, "images": images}
    actions: list[tuple] = []

    class FakeStopEvent:
        def is_set(self):
            return False

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return 75, 80.0, "daily-subframe"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "大地图"

        def goto_view(self, view_id):
            actions.append(("goto_view", view_id))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

        def wait_view(self, view_id, **kwargs):
            actions.append(("wait_view", view_id, kwargs))
            if False:
                yield BehaviorTreeStatus.RUNNING
            return "success"

        def cur_frame(self, **kwargs):
            actions.append(("cur_frame", kwargs))
            return "coin-list"

    def fail_enter_daily(*_args, **_kwargs):
        raise AssertionError("日常_仙市不应先恢复到 #69")
        yield BehaviorTreeStatus.RUNNING

    def fake_open_coin_list(_ctx, _stop_event, _payload, _image34, _image247, _image248, *, task_label):
        actions.append(("open_coin_list", task_label))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        actions.append(("click_free_box", task_label))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        actions.append(("return_world", task_label))
        if False:
            yield BehaviorTreeStatus.RUNNING
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_enter_daily_from_world_like", fail_enter_daily)
    monkeypatch.setattr(runner, "_open_daily_xianshi_coin_list", fake_open_coin_list)
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert ("goto_view", 34) in actions
    assert any(action[0] == "wait_view" and action[1] == 34 for action in actions)
    assert any(action[0] == "open_coin_list" for action in actions)


def test_daily_xianshi_open_coin_list_reads_as_runtime_steps(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def cur_frame(self, **kwargs):
            actions.append(("cur_frame", kwargs))
            return "world-frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "仙市 仙府 储物袋"

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
        ("cur_frame", {"update": True}),
        ("ocr_text", "world-frame"),
        (
            "wait_click_then_shape",
            34,
            "仙市",
            247,
            "秘藏阁",
            {
                "settle_seconds": 2.0,
                "timeout": 6.0,
                "retry_if_source_remains": True,
                "max_clicks": 3,
                "label": "日常_仙市：等待仙市入口页",
            },
        ),
        ("wait_click_then_shape", 247, "秘藏阁", 248, "仙币", {"settle_seconds": 1.5, "label": "日常_仙市：等待秘藏阁仙币页"}),
        ("wait_click", 248, "仙币", {}),
        ("settle", 2.5),
    ]


def test_daily_xianshi_return_to_world_uses_goto_world(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def goto_view(self, view_id, **kwargs):
            actions.append(("goto_view", view_id, kwargs.get("layer0_wait_seconds")))
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
        ("goto_view", 34, 2.0),
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


def test_daily_xianshi_free_coin_box_direct_click_claims_when_claim_button_visible(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        default_wait_click_timeout = 10.0

        def click_shape_center(self, view_id, shape):
            actions.append(("click_shape_center", view_id, shape))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None
            return None

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
        ("click_shape_center", 249, "灵石仙币宝匣"),
        ("wait_action_settle", 1.25),
        ("claim", "日常_仙市"),
    ]


def test_daily_xianshi_free_coin_box_no_claim_button_is_idempotent_done(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def click_shape_center(self, view_id, shape):
            actions.append(("click_shape_center", view_id, shape))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None

        def wait_view(self, view_id, **kwargs):
            actions.append(("wait_view", view_id, kwargs.get("label")))
            if False:
                yield None
            return view_id, 100.0

    def fake_claim(_ctx, _stop_event, _payload, _image250, *, task_label):
        actions.append(("claim", task_label))
        raise RuntimeError("wait_click #250 [领取] 超时，最后 0% OCR=兑换 购买")
        yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", fake_claim)

    result = _drain_generator(
        runner._click_daily_xianshi_free_coin_box(
            ctx,
            fanxiu.threading.Event(),
            {},
            {"id": 249},
            {"id": 250},
            task_label="日常_仙市",
        )
    )

    assert result == "not_free"
    assert actions == [
        ("click_shape_center", 249, "灵石仙币宝匣"),
        ("wait_action_settle", 1.5),
        ("claim", "日常_仙市"),
        ("click_shape_center", 250, "返回"),
        ("wait_action_settle", 1.0),
    ]


def test_daily_xianshi_always_starts_from_world_anchor_even_if_current_frame_looks_like_detail(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 6, 14, 11, 30, 0))
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    images = {scene_id: {"id": scene_id, "title": str(scene_id), "width": 900, "height": 1600, "shapes": []} for scene_id in (34, 247, 248, 249, 250)}
    ctx = {"asset_tree_path": asset_tree, "images": images}
    actions: list[tuple] = []

    class FakeStopEvent:
        def is_set(self):
            return False

        def wait(self, _seconds):
            return False

    class FakeRuntime:
        def goto_view(self, view_id):
            actions.append(("goto_view", view_id))
            if False:
                yield None
            return "success"

        def wait_view(self, view_id, **kwargs):
            actions.append(("wait_view", view_id, kwargs.get("label")))
            if False:
                yield None
            return view_id, 100.0

        def cur_frame(self, **kwargs):
            actions.append(("cur_frame", kwargs))
            return "coin-list-frame"

        def ocr_text(self, *args, **kwargs):
            actions.append(("ocr_text", args, kwargs))
            return "秘藏阁 天衍灵石 仙币"

    def fake_open_coin_list(_ctx, _stop_event, _payload, _image34, _image247, _image248, *, task_label):
        actions.append(("open_coin_list", task_label))
        if False:
            yield None
        return "success"

    def fake_click_free_box(_ctx, _stop_event, _payload, _image249, _image250, *, task_label):
        actions.append(("click_free_box", task_label))
        if False:
            yield None
        return "not_free"

    def fake_return_world(_ctx, _stop_event, _payload, _image249, *, task_label):
        actions.append(("return_world", task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_open_daily_xianshi_coin_list", fake_open_coin_list)
    monkeypatch.setattr(runner, "_click_daily_xianshi_free_coin_box", fake_click_free_box)
    monkeypatch.setattr(runner, "_return_daily_xianshi_to_world", fake_return_world)

    result = runner._run_direct_runtime_action(
        lambda: runner._execute_daily_xianshi_task(ctx, FakeStopEvent(), {}),
        stop_event=FakeStopEvent(),
        tick_seconds=0.01,
    )

    assert result == "success"
    assert actions[:3] == [
        ("goto_view", 34),
        ("wait_view", 34, "仙市_秘藏阁：等待世界 #34"),
        ("open_coin_list", "仙市_秘藏阁"),
    ]
    assert ("click_free_box", "仙市_秘藏阁") in actions
    assert ("return_world", "仙市_秘藏阁") in actions
    fact = runtime_runner_core._read_data_annotation_world_facts()["discoveries"]["task"]["legacy-daily-xianshi"]
    assert fact["discovered_next_time"] == "2026-06-15 05:00:00"
    assert fact.get("discovered_retry_after") is None


def test_daily_xianshi_claim_non_match_error_is_not_idempotent(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def click_shape_center(self, view_id, shape):
            pass

        def wait_action_settle(self, seconds):
            if False:
                yield None

    def fake_claim(_ctx, _stop_event, _payload, _image250, *, task_label):
        raise RuntimeError("ADB截图失败")
        yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", fake_claim)

    with pytest.raises(RuntimeError, match="ADB截图失败"):
        _drain_generator(
            runner._click_daily_xianshi_free_coin_box(
                ctx,
                fanxiu.threading.Event(),
                {},
                {"id": 249},
                {"id": 250},
                task_label="日常_仙市",
            )
        )


def test_daily_xianshi_non_claim_box_detail_returns_to_list_and_completes(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def click_shape_center(self, view_id, shape):
            actions.append(("click_shape_center", view_id, shape))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None

        def wait_view(self, view_id, **kwargs):
            actions.append(("wait_view", view_id, kwargs.get("label")))
            if False:
                yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    def fake_claim(_ctx, _stop_event, _payload, _image250, *, task_label):
        actions.append(("claim", task_label))
        raise RuntimeError("wait_click #250 [领取] 超时，最后 0% OCR=阵眼自选宝匣 兑换所需 仙币")
        yield None

    monkeypatch.setattr(runner, "_claim_daily_xianshi_coin_box", fake_claim)

    result = _drain_generator(
        runner._click_daily_xianshi_free_coin_box(
            ctx,
            fanxiu.threading.Event(),
            {},
            {"id": 249},
            {"id": 250},
            task_label="日常_仙市",
        )
    )

    assert result == "not_free"
    assert actions == [
        ("click_shape_center", 249, "灵石仙币宝匣"),
        ("wait_action_settle", 1.5),
        ("claim", "日常_仙市"),
        ("click_shape_center", 250, "返回"),
        ("wait_action_settle", 1.0),
    ]


def _drain_generator(gen):
    while True:
        try:
            next(gen)
        except StopIteration as exc:
            return exc.value


def test_daily_weekly_dungeon_retries_when_tiangong_challenge_keeps_source_scene():
    runner = create_fanxiu_runtime_runner()
    actions = []

    class FakeRuntime:
        def __init__(self):
            self.attempts = 0

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id, **kwargs):
            actions.append(("wait_click_then_view", scene_id, shape_title, target_scene_id, kwargs.get("timeout")))
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("still on #326")
            if False:
                yield None
            return target_scene_id

        def wait_view(self, scene_id, **kwargs):
            actions.append(("wait_view", scene_id, kwargs.get("timeout")))
            if False:
                yield None
            return scene_id

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None

        def current_scene(self, candidates, update=False):
            actions.append(("current_scene", tuple(candidates), update))
            return 326, 100.0, "frame326"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "玉霄天宫 本周剩余奖励次数:3/3 挑战"

    result = _drain_generator(runner._open_daily_weekly_dungeon_challenge_view(FakeRuntime(), {}))

    assert result == 327
    assert actions == [
        ("wait_view", 326, 10.0),
        ("wait_action_settle", 6.0),
        ("ocr_text", None),
        ("wait_click_then_view", 326, "挑战", 327, 10.0),
        ("current_scene", (327, 326), True),
        ("ocr_text", "frame326"),
        ("wait_view", 326, 10.0),
        ("wait_action_settle", 6.0),
        ("ocr_text", None),
        ("wait_click_then_view", 326, "挑战", 327, 10.0),
    ]


def test_daily_weekly_dungeon_retries_when_tiangong_entry_keeps_source_scene():
    runner = create_fanxiu_runtime_runner()
    actions = []

    class FakeRuntime:
        def __init__(self):
            self.attempts = 0

        def wait_click_then_view(self, scene_id, shape_title, target_scene_id, **kwargs):
            actions.append(("wait_click_then_view", scene_id, shape_title, target_scene_id, kwargs.get("timeout")))
            self.attempts += 1
            if self.attempts == 1:
                raise TimeoutError("still on #325")
            if False:
                yield None
            return target_scene_id

        def current_scene(self, candidates, update=False):
            actions.append(("current_scene", tuple(candidates), update))
            return 325, 80.0, "frame325"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "周本 天宫"

    result = _drain_generator(runner._open_daily_weekly_dungeon_tiangong_view(FakeRuntime(), {}))

    assert result == 326
    assert actions == [
        ("wait_click_then_view", 325, "天宫", 326, 8.0),
        ("current_scene", (326, 325, 69), True),
        ("ocr_text", "frame325"),
        ("wait_click_then_view", 325, "天宫", 326, 8.0),
    ]


def test_unknown_evidence_classifies_partial_scene_identity_match(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image261 = {
        "id": 261,
        "title": "0261.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "箱子", "isSceneIdentity": True, "sceneIdentityRole": "required", "sceneIdentityScope": "global", "imageMatchRole": "required", "pixelTolerance": 30},
            {"title": "升阶", "isSceneIdentity": True, "sceneIdentityRole": "required", "sceneIdentityScope": "local", "imageMatchRole": "required", "pixelTolerance": 5},
            {"title": "返回", "imageMatchRole": "required", "x": 0.05, "y": 0.90, "w": 0.08, "h": 0.04},
        ],
    }
    ctx = {"images": {261: image261}}
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame, **kwargs: 94.0 if shape["title"] == "箱子" else 37.0 if shape["title"] == "升阶" else 90.0)
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda ctx, frame: [{"text": "异火 净莲妖火 升阶"}])

    evidence = runtime_runner_core.build_unknown_evidence(
        runner,
        ctx,
        "not-a-data-url",
        label="wait_click",
        expected_scene_ids=[261],
        last_scene_id=None,
        last_score=0.0,
    )

    assert evidence.classification == "target_identity_partial_match"
    assert "升阶" in evidence.suggestion
    assert evidence.frame_path is None
    assert evidence.candidates[0].scene_id == 261
    assert evidence.candidates[0].exit_shapes == ["返回"]


def _solid_png_data_url(color: tuple[int, int, int]) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def test_unknown_evidence_classifies_unstable_transition_frame(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image261 = {
        "id": 261,
        "title": "0261.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "箱子", "isSceneIdentity": True, "sceneIdentityRole": "required", "imageMatchRole": "required"},
            {"title": "升阶", "isSceneIdentity": True, "sceneIdentityRole": "required", "imageMatchRole": "required"},
        ],
    }
    ctx = {"images": {261: image261}}
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame, **kwargs: 0.0)
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda ctx, frame: [])

    evidence = runtime_runner_core.build_unknown_evidence(
        runner,
        ctx,
        _solid_png_data_url((255, 255, 255)),
        label="wait_click",
        expected_scene_ids=[261],
        last_scene_id=None,
        last_score=0.0,
        previous_frame_data_url=_solid_png_data_url((0, 0, 0)),
    )

    assert evidence.classification == "transition_unknown"
    assert evidence.frame_stability_score == 0.0
    assert "等待稳定帧" in evidence.suggestion


def test_unknown_evidence_matches_existing_reference_frame(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    frame_data_url = _solid_png_data_url((230, 220, 210))
    reference_bytes = base64.b64decode(frame_data_url.split(",", 1)[1])
    screenshot_dir = tmp_path / "screenshots"
    screenshot_dir.mkdir()
    (screenshot_dir / "0261.png").write_bytes(reference_bytes)
    monkeypatch.setenv("FX_SCREENSHOT_FRAME_DIR", os.fspath(screenshot_dir))
    monkeypatch.setenv("FX_MATCH_FRAME_DIR", os.fspath(tmp_path / "missing-match-dir"))
    image261 = {
        "id": 261,
        "title": "0261.png",
        "filename": "0261.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "箱子", "isSceneIdentity": True, "sceneIdentityRole": "required", "imageMatchRole": "required"},
            {"title": "升阶", "isSceneIdentity": True, "sceneIdentityRole": "required", "imageMatchRole": "required"},
            {"title": "返回", "imageMatchRole": "required", "x": 0.05, "y": 0.90, "w": 0.08, "h": 0.04},
        ],
    }
    ctx = {"images": {261: image261}}
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame, **kwargs: 0.0)
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda ctx, frame: [])

    evidence = runtime_runner_core.build_unknown_evidence(
        runner,
        ctx,
        frame_data_url,
        label="wait_click",
        expected_scene_ids=[261],
        last_scene_id=None,
        last_score=0.0,
    )

    assert evidence.classification == "full_frame_similar_identity_mismatch"
    assert "身份证据仅 0%" in evidence.suggestion
    assert evidence.candidates[0].scene_id == 261
    assert evidence.candidates[0].frame_similarity == 100.0
    assert evidence.candidates[0].exit_shapes == ["返回"]


def test_unknown_evidence_scores_all_candidates_before_limiting(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    frame_data_url = _solid_png_data_url((11, 22, 33))
    images = {
        scene_id: {
            "id": scene_id,
            "title": f"scene-{scene_id}",
            "width": 900,
            "height": 1600,
            "shapes": [
                {
                    "title": f"identity-{scene_id}",
                    "isSceneIdentity": True,
                    "sceneIdentityRole": "required",
                    "imageMatchRole": "required",
                }
            ],
        }
        for scene_id in range(1, 31)
    }
    ctx = {"images": images}
    monkeypatch.setattr(runner, "_runtime_scene_candidate_ids", lambda ctx: [1, 2, 3])
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame, **kwargs: 96.0 if image["id"] == 30 else 0.0)
    monkeypatch.setattr(runner, "_scene_score", lambda ctx, image, frame: 96.0 if image["id"] == 30 else 0.0)
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda ctx, frame: [])

    evidence = runtime_runner_core.build_unknown_evidence(
        runner,
        ctx,
        frame_data_url,
        label="wait_click",
        expected_scene_ids=[],
        last_scene_id=None,
        last_score=0.0,
        max_candidates=3,
    )

    assert evidence.classification == "matched_existing_frame"
    assert evidence.candidates[0].scene_id == 30
    assert len(evidence.candidates) == 1


def test_wait_view_timeout_reports_unknown_evidence(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image261 = {
        "id": 261,
        "title": "0261.png",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "箱子", "isSceneIdentity": True, "sceneIdentityRole": "required", "sceneIdentityScope": "global", "imageMatchRole": "required", "pixelTolerance": 30},
            {"title": "升阶", "isSceneIdentity": True, "sceneIdentityRole": "required", "sceneIdentityScope": "local", "imageMatchRole": "required", "pixelTolerance": 5},
        ],
    }
    ctx = {"images": {261: image261}}
    runtime = runtime_runner_core.FanxiuRuntime(runner, ctx, stop_event=fanxiu.threading.Event())
    monkeypatch.setattr(runner, "_screencap", lambda ctx: "not-a-data-url")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda ctx, frame, preferred=None: (None, 0.0))
    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame, **kwargs: 94.0 if shape["title"] == "箱子" else 37.0)
    monkeypatch.setattr(runner, "_cached_ocr_lines", lambda ctx, frame: [{"text": "异火 净莲妖火 升阶"}])
    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", lambda runtime: False)

    with pytest.raises(TimeoutError) as exc_info:
        _drain_generator(runtime.wait_view(261, timeout=0.0, label="wait_click"))

    message = str(exc_info.value)
    assert "unknown诊断=target_identity_partial_match" in message
    assert "升阶" in message


def test_daily_youli_does_not_mark_done_from_daily_progress(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[bool] = []
    retries: list[dict[str, object]] = []

    class FakeRuntime:
        def current_scene(self, *args, **kwargs):
            return 69, 100.0, "frame"

        def ocr_text(self, *args, **kwargs):
            return "日常 游历 1/1"

    def fake_open_entry(*args, **kwargs):
        calls.append(bool(kwargs.get("progress_can_mark_done")))
        if False:
            yield None
        return "not_found"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_open_daily_entry_from_daily", fake_open_entry)
    monkeypatch.setattr(
        runner,
        "_record_daily_entry_not_found_retry",
        lambda payload, **kwargs: retries.append({"payload": payload, **kwargs}) or "2026-06-11 11:30:00",
    )

    result = _drain_generator(
        runner._execute_daily_youli_task(
            {"asset_tree_path": Path("asset.json"), "images": {}},
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result == "skipped"
    assert calls == [False]
    assert retries and retries[0]["task_type"] == "daily_youli"


def test_daily_boss_detail_cd_records_retry_and_returns_skipped(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    retries: list[dict[str, object]] = []

    class FakeRuntime:
        def ocr_text_in_shapes(self, *args, **kwargs):
            return "剩余奖励次数 2 刷新 00:30:00"

    def fake_record_retry(task_id, retry_after_text, **kwargs):
        retries.append({"task_id": task_id, "retry_after": retry_after_text, **kwargs})

    def fake_return(*args, **kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_retry_after", fake_record_retry)
    monkeypatch.setattr(runner, "_return_daily_boss_to_world", fake_return)

    result = _drain_generator(
        runner._handle_daily_boss_detail(
            {
                "asset_tree_path": Path("asset.json"),
                "images": {179: {"id": 179, "title": "首领详情", "shapes": []}},
            },
            fanxiu.threading.Event(),
            {"__scheduler_task_id": "daily-boss"},
        )
    )

    assert result == "skipped"
    assert retries
    assert retries[0]["task_id"] == "daily-boss"
    assert retries[0]["task_type"] == "daily_boss"
    assert retries[0]["last_result"] == "skipped"


def test_daily_boss_done_frame_records_retry_when_remaining_unknown(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    retries: list[dict[str, object]] = []

    class FakeRuntime:
        def get_view(self, _scene_id):
            return None

    def fake_recheck(payload, *, seconds):
        retries.append({"payload": payload, "seconds": seconds})
        return "2026-07-01 13:27:19"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: (181, 100.0, "frame", "封印"))
    monkeypatch.setattr(runner, "_record_daily_boss_recheck_time", fake_recheck)

    next_time, source = _drain_generator(
        runner._record_daily_boss_next_time_after_done(
            {"asset_tree_path": Path("asset.json"), "images": {}},
            fanxiu.threading.Event(),
            {"__scheduler_task_id": "daily-boss"},
        )
    )

    assert next_time == "2026-07-01 13:27:19"
    assert source == "已识别 #181 封印完成；挑战前奖励次数未知，半小时后复查刷新 CD"
    assert retries == [{"payload": {"__scheduler_task_id": "daily-boss"}, "seconds": 1800}]


def test_daily_boss_done_frame_reads_list_cd_after_returning_to_list(monkeypatch):
    runner = create_fanxiu_runtime_runner()

    class FakeRuntime:
        pass

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: (178, 100.0, "frame", "刷新时间 00:03:23"))
    monkeypatch.setattr(
        runner,
        "_record_daily_boss_next_time_from_current_list",
        lambda _ctx, _payload: ("2026-07-01 13:05:33", "按 #182 刷新时间读取 00:03:23"),
    )

    next_time, source = _drain_generator(
        runner._record_daily_boss_next_time_after_done(
            {"asset_tree_path": Path("asset.json"), "images": {}},
            fanxiu.threading.Event(),
            {"__scheduler_task_id": "daily-boss"},
        )
    )

    assert next_time == "2026-07-01 13:05:33"
    assert source == "已识别 #181 封印完成；按 #182 刷新时间读取 00:03:23"


def test_mark_scheduler_task_skipped_uses_retry_fact_not_daily_next(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    state_path = _scheduler_state_path(tmp_path)
    facts_path = tmp_path / "world_facts.json"
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: state_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: facts_path)
    tasks = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "next_time": "2026-06-22 05:00:00",
            "retry_after": None,
            "last_result": "running",
            "payload": {"__scheduler_definition_task_type": "daily_boss"},
        }
    ]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    facts_path.write_text(
        json.dumps(
            {
                "discoveries": {
                    "task": {
                        "daily-boss": {
                            "id": "daily-boss",
                            "task_type": "daily_boss",
                            "discovered_retry_after": "2026-06-21 11:58:27",
                            "retry_after": "2026-06-21 11:58:27",
                            "last_result": "skipped",
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runner._mark_scheduler_task(tasks, "daily-boss", "skipped")

    updated = json.loads(state_path.read_text(encoding="utf-8"))[0]
    assert updated["last_result"] == "skipped"
    assert updated["next_time"] is None
    assert updated["retry_after"] == "2026-06-21 11:58:27"


def test_mark_scheduler_task_success_respects_runtime_retry_fact(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    state_path = _scheduler_state_path(tmp_path)
    facts_path = tmp_path / "world_facts.json"
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: state_path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: facts_path)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 21, 11, 30, 0)

    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    tasks = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": True,
            "schedule_times": ["05:00"],
            "next_time": "2026-06-21 05:00:00",
            "retry_after": None,
            "last_run_at": "2026-06-21 11:20:00",
            "last_result": "running",
            "payload": {"__scheduler_definition_task_type": "daily_boss"},
        }
    ]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
    facts_path.write_text(
        json.dumps(
            {
                "discoveries": {
                    "task": {
                        "daily-boss": {
                            "id": "daily-boss",
                            "task_type": "daily_boss",
                            "discovered_retry_after": "2026-06-21 12:00:00",
                            "retry_after": "2026-06-21 12:00:00",
                            "last_result": "skipped",
                            "updated_at": datetime(2026, 6, 21, 11, 25, 0).timestamp(),
                        }
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    runner._mark_scheduler_task(tasks, "daily-boss", "success")

    updated = json.loads(state_path.read_text(encoding="utf-8"))[0]
    assert updated["last_result"] == "skipped"
    assert updated["next_time"] is None
    assert updated["retry_after"] == "2026-06-21 12:00:00"


def test_daily_dungeon_world_return_without_reward_result_is_not_success(monkeypatch):
    runner = create_fanxiu_runtime_runner()

    class FakeRuntime:
        def ocr_text(self, *args, **kwargs):
            return "储物袋 角色 装备 功法书 日程"

        def current_scene(self, *args, **kwargs):
            return 34, 100.0, "frame"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    with pytest.raises(RuntimeError, match="未识别 #227 奖励结果"):
        _drain_generator(
            runner._finish_daily_dungeon_result(
                {
                    "asset_tree_path": Path("asset.json"),
                    "images": {
                        227: {
                            "id": 227,
                            "title": "副本扫荡结果",
                            "shapes": [{"title": "继续", "x": 0, "y": 0, "w": 1, "h": 1}],
                        }
                    },
                },
                fanxiu.threading.Event(),
                {},
                task_label="日常_每日副本",
            )
        )


def test_daily_dungeon_result_accepts_ocr_reward_title_variants():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_dungeon_text_is_result("共喜莎得力创造灵书 点击屏幕继续") is True
    assert runner._daily_dungeon_text_is_result("恭喜获得 百变剑意 点击继续") is True
    assert runner._daily_dungeon_text_is_result("点击屏幕继续") is False




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


def test_jianling_cuiling_closes_popup_and_accepts_full_level_layer0_terminal(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions = []

    class FakeRuntime:
        def __init__(self):
            self.scenes = iter((349, 351, None))
            self.levels = iter((([173], "等级：173"), ([1000], "凝炼等级：1000（圆满）")))
            self.completion_message = ""

        def cur_frame(self, update=False):
            assert update is True
            return "frame"

        def current_scene(self, candidates, **_kwargs):
            assert candidates == [351, 349]
            return next(self.scenes), 100.0, "frame"

        def ocr_numbers_in_shapes(self, scene_id, shapes, **_kwargs):
            assert (scene_id, shapes) == (349, ("等级",))
            return next(self.levels)

        def long_press_shape(self, scene_id, shape, *, duration):
            actions.append(("long_press_shape", scene_id, shape, duration))

        def wait_click(self, scene_id, shape):
            actions.append(("wait_click", scene_id, shape))
            if False:
                yield None

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def set_completion_message(self, message):
            self.completion_message = message

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: runtime)

    result = _drain_generator(
        runner._execute_jianling_cuiling_task({}, fanxiu.threading.Event(), {})
    )

    assert result == "success"
    assert actions == [
        ("long_press_shape", 349, "淬灵", 3.0),
        ("settle", 0.8),
        ("wait_click", 351, "继续"),
        ("settle", 0.8),
    ]
    assert "1000" in runtime.completion_message




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


def test_fanxiu_runtime_wait_click_nested_floating_image_uses_child_template_and_parent_scan_box(monkeypatch):
    image = {
        "id": 320,
        "title": "奇袭魔界",
        "filename": "0320.png",
        "width": 1000,
        "height": 2000,
        "shapes": [{
            "title": "检索区域",
            "kind": "group",
            "x": 0.05,
            "y": 0.04,
            "w": 0.85,
            "h": 0.17,
            "children": [{
                "title": "修罗",
                "floating": True,
                "imageMatchRole": "required",
                "ocrMatchRole": "off",
                "x": 0.38,
                "y": 0.10,
                "w": 0.09,
                "h": 0.05,
                "pixelTolerance": 80,
            }],
        }],
    }
    runner, runtime = _wait_click_runtime(image)
    captured: dict[str, object] = {}
    clicks: list[dict[str, object]] = []

    def fake_run_match(ctx, img, shape, frame, **kwargs):
        captured["match_shape"] = dict(shape)
        captured["match_kwargs"] = dict(kwargs)
        payload = runner._build_shape_match_payload(
            img,
            shape,
            frame,
            entry_id="entry",
            scan=bool(kwargs.get("scan")),
            match_strategy=str(kwargs.get("match_strategy") or "auto"),
            ocr_enabled=bool(kwargs.get("ocr_enabled")),
        )
        captured["payload"] = payload
        return {
            "similarity": 100,
            "matched": True,
            "box": payload["box"],
            "fixed_box": {"x": 382, "y": 205, "w": 88, "h": 44},
        }

    monkeypatch.setattr(runner, "_screencap", lambda ctx: "frame")
    monkeypatch.setattr(runner, "_run_match", fake_run_match)
    monkeypatch.setattr(runtime_runner_core, "_click_game_window2_service", lambda payload: clicks.append(dict(payload)))

    _drain_generator(runtime.wait_click(320, "[检索区域/修罗]"))

    assert captured["match_shape"]["title"] == "修罗"
    assert captured["match_shape"]["x"] == pytest.approx(0.38)
    assert captured["match_shape"]["y"] == pytest.approx(0.10)
    assert captured["match_shape"]["w"] == pytest.approx(0.09)
    assert captured["match_shape"]["h"] == pytest.approx(0.05)
    assert captured["match_shape"]["_match_scan_box"] == {"x": 0.05, "y": 0.04, "w": 0.85, "h": 0.17}
    assert captured["match_shape"].get("ocrText") in (None, "")
    assert captured["match_kwargs"]["scan"] is True
    assert captured["match_kwargs"]["ocr_enabled"] is False
    assert {key: captured["payload"]["box"][key] for key in ("x", "y", "w", "h")} == pytest.approx(
        {"x": 380.0, "y": 200.0, "w": 90.0, "h": 100.0}
    )
    assert {key: captured["payload"]["scan_box"][key] for key in ("x", "y", "w", "h")} == pytest.approx(
        {"x": 50.0, "y": 80.0, "w": 850.0, "h": 340.0}
    )
    assert clicks
    assert clicks[-1]["x"] == pytest.approx(426.0)
    assert clicks[-1]["y"] == pytest.approx(227.0)


def test_daily_mojie_raid_remaining_ocr_fallback_accepts_b_as_eight():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_mojie_raid_remaining_ocr_fallback("进攻次数：B") == 8
    assert runner._daily_mojie_raid_remaining_ocr_fallback("剩余次数：8") == 8
    assert runner._daily_mojie_raid_remaining_ocr_fallback("其他次数：B") is None


def test_daily_mojie_raid_attack_in_progress_requires_countdown_clock():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_mojie_raid_attack_in_progress_text("进攻倒计时：14:24:24") is True
    assert runner._daily_mojie_raid_attack_in_progress_text("进攻倒计时") is False
    assert runner._daily_mojie_raid_attack_in_progress_text("活动倒计时：14:24:24") is False


def test_daily_mojie_raid_top_attack_target_clicks_above_count_by_default():
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple[object, ...]] = []

    class FakeRunner:
        def _frame_size(self, _raw):
            return 864, 1536

        def _ocr_match_resolved_box(self, line, _token, _mode):
            return {"x": line["x"], "y": line["y"], "w": line["w"], "h": line["h"]}

    class FakeRuntime:
        default_wait_click_timeout = 12.0
        runner = FakeRunner()

        def wait_view(self, *scene_ids, **kwargs):
            actions.append(("wait_view", scene_ids, kwargs))
            if False:
                yield None
            return 320 if scene_ids == (320,) else 321

        def view(self, _scene_id):
            return type("View", (), {"raw": {}})()

        def cur_frame(self, **kwargs):
            actions.append(("cur_frame", kwargs))
            return "frame"

        def ocr_lines(self, _frame):
            return [{"text": "11/30", "x": 348.0, "y": 396.0, "w": 72.0, "h": 29.0}]

        def click_frame_point(self, scene_id, x, y):
            actions.append(("click_frame_point", scene_id, x, y))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None
            return "settled"

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(
            FakeRuntime(),
            {},
        )
    )

    assert result == 321
    assert actions[0][0] == "wait_view"
    assert actions[0][1] == (320,)
    assert actions[2] == ("click_frame_point", 320, 384.0, pytest.approx(195.46))
    assert actions[3] == ("wait_action_settle", 1.5)
    assert actions[4][0] == "wait_view"
    assert actions[4][1] == (321, 331)


def test_daily_mojie_raid_top_attack_target_allows_shape_override():
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple[object, ...]] = []

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def wait_click(self, scene_id, shape, **kwargs):
            actions.append(("wait_click", scene_id, shape, kwargs))
            if False:
                yield None
            return "clicked"

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None
            return "settled"

        def wait_view(self, *scene_ids, **kwargs):
            actions.append(("wait_view", scene_ids, kwargs))
            if False:
                yield None
            return 321

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(
            FakeRuntime(),
            {"mojie_raid_target_shape": "检索区域/修罗", "mojie_raid_target_match_timeout": 3},
        )
    )

    assert result == 321
    assert actions[0] == ("wait_click", 320, "检索区域/修罗", {"timeout": 3.0})


def test_daily_mojie_raid_top_attack_target_accepts_direct_331():
    runner = create_fanxiu_runtime_runner()

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def wait_click(self, scene_id, shape, **kwargs):
            if False:
                yield None
            return "clicked"

        def wait_action_settle(self, seconds):
            if False:
                yield None
            return "settled"

        def wait_view(self, *scene_ids, **kwargs):
            if False:
                yield None
            return 331

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(
            FakeRuntime(),
            {"mojie_raid_target_shape": "检索区域/修罗"},
        )
    )

    assert result == 331


def test_daily_mojie_raid_top_attack_target_normalizes_wait_view_object():
    runner = create_fanxiu_runtime_runner()

    class FakeView:
        id = 321

    class FakeRuntime:
        default_wait_click_timeout = 12.0

        def wait_click(self, scene_id, shape, **kwargs):
            if False:
                yield None
            return "clicked"

        def wait_action_settle(self, seconds):
            if False:
                yield None
            return "settled"

        def wait_view(self, *scene_ids, **kwargs):
            if False:
                yield None
            return FakeView()

    result = _drain_generator(
        runner._click_daily_mojie_raid_top_attack_target(
            FakeRuntime(),
            {"mojie_raid_target_shape": "检索区域/修罗"},
        )
    )

    assert result == 321


def test_daily_mojie_raid_attack_count_candidates_clicks_above_top_count():
    runner = create_fanxiu_runtime_runner()

    class FakeRunner:
        def _frame_size(self, _raw):
            return 864, 1536

        def _ocr_match_resolved_box(self, line, _token, _mode):
            return {"x": line["x"], "y": line["y"], "w": line["w"], "h": line["h"]}

    class FakeRuntime:
        runner = FakeRunner()

        def view(self, _scene_id):
            return type("View", (), {"raw": {}})()

        def ocr_lines(self, _frame):
            return [{"text": "11/30", "x": 348.0, "y": 396.0, "w": 72.0, "h": 29.0}]

    candidates = runner._daily_mojie_raid_attack_count_candidates(FakeRuntime(), "frame", {})

    assert candidates == [(384.0, pytest.approx(195.46), "11/30")]


def test_daily_mojie_raid_attack_count_candidates_uses_annotated_target_vector():
    runner = create_fanxiu_runtime_runner()

    class FakeRunner:
        @staticmethod
        def _frame_size(_raw):
            return 900, 1600

        @staticmethod
        def _ocr_match_resolved_box(line, _token, _mode):
            return {key: line[key] for key in ("x", "y", "w", "h")}

    class FakeShape:
        def __init__(self, raw):
            self.raw = raw

        def box(self):
            return {
                "x": self.raw["x"] * 900,
                "y": self.raw["y"] * 1600,
                "w": self.raw["w"] * 900,
                "h": self.raw["h"] * 1600,
            }

    class FakeRuntime:
        runner = FakeRunner()

        @staticmethod
        def view(_scene):
            return type("View", (), {"raw": {"width": 900, "height": 1600}})()

        @staticmethod
        def shape(_view, selector):
            shapes = {
                "检索区域/修罗": {
                    "x": 0.48827160493827165,
                    "y": 0.128125,
                    "w": 0.0876543209876543,
                    "h": 0.05,
                },
                "检索区域/队伍数": {
                    "x": 0.4759259259259259,
                    "y": 0.29270833333333335,
                    "w": 0.1,
                    "h": 0.025,
                },
            }
            return FakeShape(shapes[selector])

        @staticmethod
        def ocr_lines(_frame):
            return [{"text": "14/30", "x": 428.333333, "y": 468.333333, "w": 90.0, "h": 40.0}]

    candidates = runner._daily_mojie_raid_attack_count_candidates(FakeRuntime(), "frame", {})

    assert candidates == [(pytest.approx(473.333333), pytest.approx(245.0), "14/30")]

    FakeRuntime.ocr_lines = staticmethod(
        lambda _frame: [{"text": "15/30困破魔灵宝", "x": 438.0, "y": 428.0, "w": 371.0, "h": 135.0}]
    )
    merged_candidates = runner._daily_mojie_raid_attack_count_candidates(FakeRuntime(), "frame", {})

    assert merged_candidates == [(pytest.approx(483.0), pytest.approx(252.166667), "15/30")]


def test_daily_mojie_raid_attack_count_candidates_splits_joined_ocr_line():
    runner = create_fanxiu_runtime_runner()

    class FakeRunner:
        def _frame_size(self, _raw):
            return 864, 1536

        def _ocr_match_resolved_box(self, _line, _token, _mode):
            return None

    class FakeRuntime:
        runner = FakeRunner()

        def view(self, _scene_id):
            return type("View", (), {"raw": {}})()

        def ocr_lines(self, _frame):
            return [{"text": "3/30困御龙魔息困3/30", "x": 122.0, "y": 551.0, "w": 639.0, "h": 129.0}]

    candidates = runner._daily_mojie_raid_attack_count_candidates(FakeRuntime(), "frame", {})

    assert len(candidates) == 2
    assert candidates[0][0] < 260
    assert candidates[1][0] > 620
    assert candidates[0][0] != candidates[1][0]


def test_daily_mojie_raid_remaining_zero_marks_week_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_now", lambda: datetime(2026, 7, 4, 13, 5, 0))
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset-tree.json"
    asset_tree.write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def __init__(self):
            self.clicks: list[tuple[int, str]] = []

        def current_scene(self, scene_ids=None, *, update=False):
            return 319, 100, "frame"

        def ocr_text(self, frame):
            return "剩余次数：0"

        def ocr_numbers_in_shapes(self, scene_id, shape_titles, *, padding=16):
            return [0], "剩余次数：0"

        def wait_click(self, scene_id, shape_title, *args, **kwargs):
            self.clicks.append((scene_id, shape_title))
            if False:
                yield None
            return "clicked"

    runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: runtime)

    result = _drain_generator(runner._execute_daily_mojie_raid_task(
        {"asset_tree_path": asset_tree},
        threading.Event(),
        {"__scheduler_task_id": "legacy-daily-mojie-raid"},
    ))

    facts = json.loads((tmp_path / "world_facts.json").read_text(encoding="utf-8"))
    fact = facts["discoveries"]["task"]["legacy-daily-mojie-raid"]
    assert result == "success"
    assert runtime.clicks == [(319, "返回")]
    assert fact["task_type"] == "daily_mojie_raid"
    assert fact["label"] == "日常_奇袭魔界"
    assert fact["last_result"] == "success"
    assert fact["discovered_next_time"] == "2026-07-06 13:00:00"
    assert fact["next_time"] == "2026-07-06 13:00:00"


def test_data_annotation_scheduler_preserves_mojie_raid_week_complete_next_time():
    raw = [{
        "id": "legacy-daily-mojie-raid",
        "task_type": "daily_mojie_raid",
        "label": "日常_奇袭魔界",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "interruptible": True,
        "schedule_times": ["13:00", "21:30"],
        "next_time": "2026-07-06 13:00:00",
        "last_result": "success",
        "last_run_at": "2026-07-04 13:05:00",
        "retry_after": None,
        "payload": {},
        "checkpoint": {
            "world_fact_synced_at": "2026-07-04 13:05:01",
            "world_fact_updated_at": 1783155901.0,
        },
    }]

    tasks, _changed = scheduler_core.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 7, 4, 13, 10, 0),
    )

    mojie = next(item for item in tasks if item["id"] == "legacy-daily-mojie-raid")
    assert mojie["next_time"] == "2026-07-06 13:00:00"
    assert mojie["scheduler_meta"]["world_fact_updated_at"] == 1783155901.0
    assert "checkpoint" not in mojie


def test_fanxiu_runtime_wait_click_ocr_floating_child_uses_shape_center(monkeypatch):
    monkeypatch.setenv("CODEYUN_FANXIU_ACTION_TRACE", "0")
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


def test_goto_view_route_candidate_ranking_prefers_clarity_then_shortest_after_threshold(monkeypatch):
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


def test_goto_view_route_from_green_bottle_rank_prefers_exit_when_returning_world():
    runner = create_fanxiu_runtime_runner()
    tree = [
        {
            "type": "image",
            "id": 282,
            "title": "掌天瓶",
            "shapes": [
                {"id": "rank", "title": "境界排行", "sceneJumpTarget": "283(1)", "isSceneIdentity": True},
                {"id": "back", "title": "返回", "sceneJumpTarget": "20(1)"},
            ],
        },
        {"type": "image", "id": 283, "title": "天道魁首", "shapes": [{"id": "back", "title": "返回", "sceneJumpTarget": "282(1)"}]},
        {"type": "image", "id": 20, "title": "绿瓶", "shapes": [{"id": "world", "title": "回到世界", "sceneJumpTarget": "34(44)"}]},
        {"type": "image", "id": 34, "title": "世界", "shapes": []},
    ]

    edge_to_world = runner._select_scene_next_edge(tree, 282, 34)
    assert edge_to_world is not None
    assert edge_to_world["edge"]["shape"]["title"] == "返回"

    edge_to_rank = runner._select_scene_next_edge(tree, 282, 283)
    assert edge_to_rank is not None
    assert edge_to_rank["edge"]["shape"]["title"] == "境界排行"

    edge_from_baiye = runner._select_scene_next_edge(tree, 283, 34)
    assert edge_from_baiye is not None
    assert edge_from_baiye["edge"]["shape"]["title"] == "返回"
    assert edge_from_baiye["edge"]["target_ids"] == [282]


def test_xianfu_home_text_rejects_world_chrome_with_xianfu_buttons():
    runner = create_fanxiu_runtime_runner()

    assert runner._xianfu_home_text_is_scene("仙侣居 本命金身 拜仙台 寻仙台 仙府管家")
    assert not runner._xianfu_home_text_is_scene(
        "大地图 日程 角色 装备 星海 功法书 储物袋 仙侣居 本命金身 拜仙台 寻仙台 仙府管家"
    )


def test_world_side_leave_matches_split_vertical_ocr():
    runner = create_fanxiu_runtime_runner()

    matches = runner._world_scene_leave_matches(
        [
            {"text": "离", "x": 792, "y": 790, "w": 42, "h": 44},
            {"text": "开", "x": 792, "y": 840, "w": 42, "h": 44},
            {"text": "离", "x": 80, "y": 790, "w": 42, "h": 44},
            {"text": "开", "x": 80, "y": 840, "w": 42, "h": 44},
        ],
        width=900,
        height=1600,
    )

    assert matches
    x, y, text = matches[0]
    assert text == "离开"
    assert x > 760
    assert 720 < y < 860


def test_go_scene_unknown_start_tries_world_side_leave_once(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    ctx = {"asset_tree": [], "images": {69: {"id": 69, "title": "日常", "width": 900, "height": 1600, "shapes": []}}}
    calls: list[str] = []
    scene_results = [(None, 4.0), (69, 96.0)]

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_scene_route_candidate_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(runner, "_recover_unknown_start_to_world", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_ocr_lines", lambda _frame: [{"text": "离", "x": 792, "y": 790, "w": 42, "h": 44}, {"text": "开", "x": 792, "y": 840, "w": 42, "h": 44}])
    monkeypatch.setattr(runner, "_ocr_text", lambda _lines: "离开")

    def fake_identify(_ctx, _frame, scene_ids=None):
        calls.append("identify")
        return scene_results.pop(0)

    def fake_leave(*_args, **_kwargs):
        calls.append("leave")
        if False:
            yield None
        return True

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    monkeypatch.setattr(runner, "_leave_world_side_scene_if_present", fake_leave)

    result = _drain_generator(runner._go_scene_task(ctx, asset_tree, 69, fanxiu.threading.Event()))

    assert result == "success"
    assert calls == ["identify", "leave", "identify"]


def test_world_side_leave_falls_back_to_scene85_leave_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            34: {"id": 34, "title": "世界", "width": 900, "height": 1600, "shapes": []},
            85: {
                "id": 85,
                "title": "某区域内部",
                "width": 900,
                "height": 1600,
                "shapes": [{"title": "离开", "x": 0.855, "y": 0.48, "w": 0.11, "h": 0.05}],
            },
        }
    }
    clicks: list[tuple[float, float]] = []

    class FakeObserver:
        def ocr_lines(self, _frame):
            return []

        def click_frame_point(self, _view, x, y):
            clicks.append((float(x), float(y)))

        def wait_action_settle(self, _seconds):
            if False:
                yield None
            return None

        def current_scene(self, *_args, **_kwargs):
            return None, 0.0, "after"

        def ocr_text(self, _frame):
            return ""

    monkeypatch.setattr(runner, "_fanxiu_observer", lambda *_args, **_kwargs: FakeObserver())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 90.0)

    result = _drain_generator(
        runner._leave_world_side_scene_if_present(
            ctx,
            fanxiu.threading.Event(),
            "frame",
            "韶非烟",
            label="测试",
            require_world_like=False,
        )
    )

    assert result is True
    assert clicks
    x, y = clicks[0]
    assert x > 760
    assert 740 < y < 850


def test_world_side_leave_ignores_weak_scene85_leave_shape(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            34: {"id": 34, "title": "世界", "width": 900, "height": 1600, "shapes": []},
            85: {
                "id": 85,
                "title": "某区域内部",
                "width": 900,
                "height": 1600,
                "shapes": [{"title": "离开", "x": 0.855, "y": 0.48, "w": 0.11, "h": 0.05}],
            },
        }
    }
    clicks: list[tuple[float, float]] = []

    class FakeObserver:
        def ocr_lines(self, _frame):
            return []

        def click_frame_point(self, _view, x, y):
            clicks.append((float(x), float(y)))

    monkeypatch.setattr(runner, "_fanxiu_observer", lambda *_args, **_kwargs: FakeObserver())
    monkeypatch.setattr(runner, "_shape_score", lambda *_args, **_kwargs: 55.0)

    result = _drain_generator(
        runner._leave_world_side_scene_if_present(
            ctx,
            fanxiu.threading.Event(),
            "frame",
            "韶非烟",
            label="测试",
            require_world_like=False,
        )
    )

    assert result is False
    assert clicks == ["离开"]


def test_data_annotation_daily_schedule_uses_nearest_future_time_independent_of_order():
    task_a = {"schedule_kind": "daily", "schedule_times": ["05:00", "00:00"]}
    task_b = {"schedule_kind": "daily", "schedule_times": ["00:00", "05:00"]}

    assert fanxiu._next_data_annotation_scheduler_time(task_a, datetime(2026, 6, 13, 23, 0, 0)) == "2026-06-14 00:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task_b, datetime(2026, 6, 13, 23, 0, 0)) == "2026-06-14 00:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task_a, datetime(2026, 6, 14, 0, 4, 0)) == "2026-06-14 05:00:00"
    assert fanxiu._next_data_annotation_scheduler_time(task_b, datetime(2026, 6, 14, 6, 0, 0)) == "2026-06-15 00:00:00"


def test_data_annotation_scheduler_repair_removes_signup_midnight_clock():
    raw = [{
        "id": "legacy-daily-signup",
        "task_type": "daily_signup",
        "label": "日常_报名",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-14 05:00:00",
        "schedule_times": ["05:00", "00:00"],
        "last_run_at": "2026-06-13 18:00:00",
        "last_result": "success",
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_signup"},
    }]

    tasks, changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 13, 18, 16, 0),
    )

    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    assert changed is True
    assert signup["schedule_times"] == ["05:00"]
    assert signup["next_time"] == "2026-06-14 05:00:00"


def test_data_annotation_scheduler_repair_keeps_unfinished_runtime_task():
    raw = [{
        "id": "legacy-daily-xianshi",
        "task_type": "daily_xianshi",
        "label": "日常_仙市",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-19 05:00:00",
        "schedule_times": ["05:00"],
        "last_run_at": None,
        "last_result": "",
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_xianshi"},
    }]

    tasks, changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 18, 15, 0, 0),
    )

    task = next(item for item in tasks if item["id"] == "legacy-daily-xianshi")
    assert changed is True
    assert task["task_type"] == "daily_xianshi"
    assert task["next_time"] == "2026-06-18 05:00:00"


def test_data_annotation_scheduler_defaults_enable_standard_daily_tasks():
    tasks = fanxiu._default_data_annotation_scheduler_tasks()
    by_id = {item["id"]: item for item in tasks}

    for task_id in {
        "daily-boss",
        "legacy-daily-xianyuan",
    }:
        assert by_id[task_id]["enabled"] is True


def test_data_annotation_scheduler_repair_reenables_standard_daily_tasks():
    raw = [
        {
            "id": "legacy-daily-xianyuan",
            "task_type": "daily_xianyuan",
            "label": "日常_挑战仙缘",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": False,
            "next_time": "2026-06-22 05:00:00",
            "schedule_times": ["05:00"],
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "payload": {"__scheduler_definition_task_type": "daily_xianyuan"},
        },
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "source": "data_annotation_runtime",
            "schedule_kind": "daily",
            "enabled": False,
            "next_time": "2026-06-22 05:00:00",
            "schedule_times": ["05:00"],
            "last_run_at": None,
            "last_result": "",
            "retry_after": None,
            "payload": {"__scheduler_definition_task_type": "daily_boss"},
        },
    ]

    tasks, changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 21, 22, 0, 0),
    )

    by_id = {item["id"]: item for item in tasks}
    assert changed is True
    assert by_id["legacy-daily-xianyuan"]["enabled"] is True
    assert by_id["daily-boss"]["enabled"] is True


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
    assert signup["next_time"] == "2026-06-19 00:00:00"


def test_daily_signup_treats_bottom_confirmed_all_signed_as_success():
    runner = create_fanxiu_runtime_runner()

    class FakeRuntime:
        payload = {"signup_bottom_confirmations": 2}

        def current_scene(self, view_ids=None, **_kwargs):
            return 69, 100.0, "frame"

        def ocr_text(self, _frame=None):
            return "报名 活动时间 已报名"

        def ocr_row_clicks_in_shape(self, _view_id, _shape, *, include=(), exclude=()):
            if include == ("已报名",):
                return [(720.0, 460.0, "已报名")]
            if include == ("报名",) and exclude == ("已报名",):
                return []
            raise AssertionError((include, exclude))

        def scroll_shape_content(self, view_id, shape):
            assert (view_id, shape) == (23, "报名列")
            if False:
                yield None
            return False

        def click_shape_center(self, view_id, shape):
            assert (view_id, shape) == (69, "退出")

        def wait_action_settle(self, seconds):
            assert seconds == 1.0
            if False:
                yield None
            return "success"

        def view_visible(self, view_id, **_kwargs):
            return ("view_visible", view_id)

        def ocr_matches(self, predicate, **_kwargs):
            return ("ocr_matches", predicate)

        def wait_any(self, conditions, **kwargs):
            assert set(conditions) == {"scene", "text"}
            if False:
                yield None
            return "scene"

    result = _drain_generator(runner.日常报名流程(FakeRuntime()))

    assert result == {
        "result": "success",
        "claimed": 0,
        "signup_page_opened": True,
        "evidence": "all_items_already_signed",
    }


def test_daily_signup_without_claim_or_signed_evidence_still_retries():
    runner = create_fanxiu_runtime_runner()

    class FakeRuntime:
        payload = {"signup_bottom_confirmations": 2}

        def current_scene(self, view_ids=None, **_kwargs):
            return 69, 100.0, "frame"

        def ocr_text(self, _frame=None):
            return "报名 活动时间 待报名"

        def ocr_row_clicks_in_shape(self, _view_id, _shape, *, include=(), exclude=()):
            return []

        def scroll_shape_content(self, view_id, shape):
            assert (view_id, shape) == (23, "报名列")
            if False:
                yield None
            return False

        def click_shape_center(self, view_id, shape):
            assert (view_id, shape) == (69, "退出")

        def wait_action_settle(self, seconds):
            assert seconds == 1.0
            if False:
                yield None
            return "success"

        def view_visible(self, view_id, **_kwargs):
            return ("view_visible", view_id)

        def ocr_matches(self, predicate, **_kwargs):
            return ("ocr_matches", predicate)

        def wait_any(self, conditions, **kwargs):
            assert set(conditions) == {"scene", "text"}
            if False:
                yield None
            return "scene"

    result = _drain_generator(runner.日常报名流程(FakeRuntime()))

    assert result["result"] == "skipped"
    assert result["claimed"] == 0
    assert "不能确认" in result["message"]

def test_daily_signup_return_world_uses_fixed_exit_click_before_world_wait():
    runner = create_fanxiu_runtime_runner()
    actions = []
    state = {"scene": 69}

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            return state["scene"], 100.0, "frame"

        def ocr_text(self, _frame=None):
            actions.append(("ocr_text", state["scene"]))
            return "日常 活跃度 活动报名 小助手" if state["scene"] == 69 else "世界 储物袋 角色"

        def click_shape_center(self, view_id, shape):
            actions.append(("click_shape_center", view_id, shape))
            assert view_id == 69
            assert shape == "退出"
            state["scene"] = 34

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
            if False:
                yield None
            return "success"

        def view_visible(self, view_id, **_kwargs):
            return ("view_visible", view_id)

        def ocr_matches(self, predicate, **_kwargs):
            return ("ocr_matches", predicate)

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", tuple(conditions.keys()), kwargs.get("label")))
            assert state["scene"] == 34
            if False:
                yield None
            return "scene"

        def wait_click(self, *_args, **_kwargs):
            raise AssertionError("日常_报名返回世界不应依赖 #69 退出图像匹配条件")

    gen = runner._日常报名返回世界(FakeRuntime())
    while True:
        try:
            next(gen)
        except StopIteration:
            break

    assert actions == [
        ("current_scene", (69, 34), {"update": True}),
        ("ocr_text", 69),
        ("click_shape_center", 69, "退出"),
        ("wait_action_settle", 1.0),
        ("wait_any", ("scene", "text"), "日常_报名：等待返回世界 #34"),
    ]


def test_data_annotation_scheduler_repair_keeps_due_multi_clock_task_due():
    raw = [{
        "id": "legacy-daily-assistant",
        "task_type": "daily_assistant",
        "label": "日常_助手",
        "source": "data_annotation_runtime",
        "schedule_kind": "daily",
        "enabled": True,
        "next_time": "2026-06-14 00:00:00",
        "schedule_times": ["05:00", "00:00"],
        "retry_after": None,
        "payload": {"__scheduler_definition_task_type": "daily_assistant"},
    }]

    tasks, _changed = fanxiu.repair_data_annotation_scheduler_tasks(
        raw,
        fanxiu._default_data_annotation_scheduler_tasks(),
        {},
        task_supported=lambda task: True,
        now=datetime(2026, 6, 14, 0, 0, 1),
    )

    assistant = next(item for item in tasks if item["id"] == "legacy-daily-assistant")
    assert assistant["next_time"] == "2026-06-14 00:00:00"


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

        def ocr_text(self, _frame):
            return "11:58:07后可免费抽取"

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


def test_xianfu_visit_partner_rejects_daily_page_as_entry(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {}

    class FakeRuntime:
        def __init__(self):
            self.scene_id = 69
            self.goto_targets = []
            self.ctx = {"images": images}

        def cur_frame(self, update=False):
            return object()

        def current_scene(self, view_ids=None, **kwargs):
            return self.scene_id, 100.0, self.cur_frame(update=bool(kwargs.get("update")))

        def ocr_text(self, _frame):
            return "日常 活动 小助手"

        def goto_view(self, target_id):
            self.goto_targets.append(target_id)
            if False:
                yield None
            return "success"

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: fake_runtime)
    ctx = {"asset_tree_path": tmp_path / "asset-tree.json", "images": images}

    with pytest.raises(RuntimeError, match="日常页 #69.*禁止.*仙府寻访入口"):
        _drain_generator(
            runner._execute_xianfu_visit_partner_task(
                ctx,
                fanxiu.threading.Event(),
                {"__scheduler_task_id": "xianfu-visit-partner"},
            )
        )
    assert fake_runtime.goto_targets == []


def test_xianfu_visit_partner_requires_world_to_xianfu_route(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        34: {"id": 34, "title": "世界", "shapes": [{"title": "日常", "sceneJumpTarget": "69"}]},
        171: {"id": 171, "title": "仙府主页", "shapes": [{"title": "寻仙台", "sceneJumpTarget": "172"}]},
    }

    class FakeRuntime:
        def current_scene(self, view_ids=None, **kwargs):
            return 34, 100.0, object()

        def ocr_text(self, _frame):
            return "世界 储物袋 角色 装备 功法书"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: FakeRuntime())
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "asset_tree": [
            {"type": "image", "id": 34, "title": "世界", "shapes": images[34]["shapes"]},
            {"type": "image", "id": 171, "title": "仙府主页", "shapes": images[171]["shapes"]},
        ],
        "images": images,
    }

    with pytest.raises(RuntimeError, match="#34.*仙府.*sceneJumpTarget=171"):
        _drain_generator(
            runner._execute_xianfu_visit_partner_task(
                ctx,
                fanxiu.threading.Event(),
                {"__scheduler_task_id": "xianfu-visit-partner"},
            )
        )


def test_xianfu_visit_partner_enters_xianfu_from_world_when_route_exists(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        34: {"id": 34, "title": "世界", "shapes": [{"title": "仙府", "sceneJumpTarget": "171"}]},
        171: {
            "id": 171,
            "title": "仙府主页",
            "shapes": [
                {"title": "寻仙台", "x": 0.8, "y": 0.45, "w": 0.1, "h": 0.1, "sceneJumpTarget": "172"},
                {"title": "离开", "x": 0.8, "y": 0.3, "w": 0.1, "h": 0.1, "sceneJumpTarget": "34"},
            ],
        },
        172: {"id": 172, "title": "寻仙台", "shapes": [{"title": "寻访", "x": 0.7, "y": 0.5, "w": 0.1, "h": 0.1, "sceneJumpTarget": "174"}]},
        174: {
            "id": 174,
            "title": "绝品仙侣",
            "shapes": [
                {"title": "状态", "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.03},
                {"title": "免费提示", "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.03},
                {"title": "退出", "x": 0.05, "y": 0.9, "w": 0.08, "h": 0.05, "sceneJumpTarget": "171"},
            ],
        },
    }

    class FakeRuntime:
        def __init__(self):
            self.scene_id = 34
            self.goto_targets = []
            self.clicked = []

        def cur_frame(self, update=False):
            return object()

        def current_scene(self, view_ids=None, **kwargs):
            return self.scene_id, 100.0, self.cur_frame(update=bool(kwargs.get("update")))

        def ocr_text(self, _frame):
            return "世界 储物袋 角色 装备 功法书" if self.scene_id == 34 else "06:00:00后可免费抽取"

        def get_view(self, view_id):
            image = images.get(int(view_id))
            return runtime_runner_core.View(image) if image else None

        def goto_view(self, target_id):
            self.goto_targets.append(target_id)
            self.scene_id = int(target_id)
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            if False:
                yield None
            return self.get_view(self.scene_id) if self.scene_id in view_ids else self.scene_id

        def click_shape(self, view, shape):
            self.clicked.append((view.id, shape.title))
            if view.id == 171 and shape.title == "寻仙台":
                self.scene_id = 172
            elif view.id == 172 and shape.title == "寻访":
                self.scene_id = 174
            elif view.id == 174 and shape.title == "退出":
                self.scene_id = 171
            elif view.id == 171 and shape.title == "离开":
                self.scene_id = 34

    fake_runtime = FakeRuntime()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_fanxiu_runtime_ocr_text_in_shapes", lambda *args, **kwargs: "06:00:00后可免费抽取")
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_next_time", lambda *args, **kwargs: None)
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "asset_tree": [{"type": "image", **image} for image in images.values()],
        "images": images,
    }

    result = _drain_generator(
        runner._execute_xianfu_visit_partner_task(
            ctx,
            fanxiu.threading.Event(),
            {"__scheduler_task_id": "xianfu-visit-partner"},
        )
    )

    assert result == "success"
    assert fake_runtime.goto_targets == [171]
    assert fake_runtime.clicked == [(171, "寻仙台"), (172, "寻访"), (174, "退出"), (171, "离开")]
    assert fake_runtime.scene_id == 34


def test_xianfu_learn_skill_uses_runtime_status_when_identify_unknown(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        171: {
            "id": 171,
            "title": "仙府主页",
            "shapes": [{"title": "寻仙台", "x": 0.8, "y": 0.45, "w": 0.1, "h": 0.1, "sceneJumpTarget": "172"}],
        },
        172: {
            "id": 172,
            "title": "寻仙台",
            "shapes": [{"title": "领悟绝技", "x": 0.7, "y": 0.5, "w": 0.1, "h": 0.1, "sceneJumpTarget": "176"}],
        },
        176: {
            "id": 176,
            "title": "绝技",
            "shapes": [
                {"title": "状态", "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.03},
                {"title": "价格", "x": 0.1, "y": 0.84, "w": 0.3, "h": 0.03},
            ],
        },
    }

    class FakeRuntime:
        def __init__(self):
            self.scene_id = None
            self.goto_targets = []
            self.clicked = []

        def cur_frame(self, update=False):
            return object()

        def current_scene(self, view_ids=None, **kwargs):
            return None, 0.0, self.cur_frame(update=bool(kwargs.get("update")))

        def ocr_text(self, _frame):
            return "寻仙台 仙府管家"

        def get_view(self, view_id):
            image = images.get(int(view_id))
            return runtime_runner_core.View(image) if image else None

        def goto_view(self, target_id):
            self.goto_targets.append(target_id)
            self.scene_id = int(target_id)
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            if False:
                yield None
            return self.get_view(self.scene_id) if self.scene_id in view_ids else self.scene_id

        def click_shape(self, view, shape):
            self.clicked.append((view.id, shape.title))
            if view.id == 171 and shape.title == "寻仙台":
                self.scene_id = 172
            elif view.id == 172 and shape.title == "领悟绝技":
                self.scene_id = 176

    def _return_frame(*_args, **_kwargs):
        if False:
            yield None
        return "frame"

    fake_runtime = FakeRuntime()
    runner._status["current_scene"] = 171
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_ensure_xianfu_learn_skill_xianpin_tab", _return_frame)
    monkeypatch.setattr(runner, "_fanxiu_runtime_ocr_text_in_shapes", lambda *args, **kwargs: "06:00:00后可免费领悟")
    monkeypatch.setattr(runner, "_record_scheduler_task_discovered_retry_after", lambda *args, **kwargs: None)
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "asset_tree": [{"type": "image", **image} for image in images.values()],
        "images": images,
    }

    result = _drain_generator(
        runner._execute_xianfu_learn_skill_task(
            ctx,
            fanxiu.threading.Event(),
            {"__scheduler_task_id": "xianfu-learn-skill"},
        )
    )

    assert result == "skipped"
    assert fake_runtime.goto_targets == [34]
    assert fake_runtime.clicked == [(171, "寻仙台"), (172, "领悟绝技")]
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


def test_open_daily_entry_from_daily_defaults_to_bidirectional_scan(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[dict[str, object]] = []

    class FakeRuntime:
        def open_daily_entry(self, **kwargs):
            calls.append(kwargs)
            if False:
                yield None
            return "not_found"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._open_daily_entry_from_daily(
            {"asset_tree_path": Path("asset.json")},
            fanxiu.threading.Event(),
            {"max_scrolls": 12},
            task_label="日常_游历",
            title_pattern=r"游\s*历",
        )
    )

    assert result == "not_found"
    assert calls[0]["max_scrolls"] == 12
    assert calls[0]["reverse_scrolls"] == 12


def test_daily_youli_reward_recovery_exit_clicks_existing_exit_shape_without_scene_wait(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[tuple[str, object, object]] = []
    wait_any_results = ["daily_text", "world_text"]

    class FakeRuntime:
        def cur_frame(self, *, update=False):
            calls.append(("cur_frame", update, None))
            return "reward-frame"

        def ocr_text(self, frame):
            calls.append(("ocr_text", frame, None))
            return "奖励找回 一键免费 一键全部 全部找回"

        def click_shape_center(self, view, shape):
            calls.append(("click_shape_center", view, shape))

        def wait_action_settle(self, seconds=1.0):
            calls.append(("wait_action_settle", seconds, None))
            if False:
                yield None
            return None

        def wait_any(self, conditions, **kwargs):
            calls.append(("wait_any", sorted(conditions), kwargs.get("label")))
            if False:
                yield None
            return wait_any_results.pop(0)

        def view_visible(self, view, **kwargs):
            return ("view_visible", view, kwargs)

        def ocr_matches(self, predicate, **kwargs):
            return ("ocr_matches", predicate, kwargs)

        def wait_click(self, view, shape, **kwargs):
            calls.append(("wait_click", view, shape))
            if False:
                yield None
            return None

        def wait_view(self, view, **kwargs):
            calls.append(("wait_view", view, kwargs.get("label")))
            if False:
                yield None
            return (view, 100.0)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._return_daily_youli_reward_recovery_to_world(
            {"asset_tree_path": Path("asset.json")},
            fanxiu.threading.Event(),
            task_label="日常_游历",
        )
    )

    assert result["result"] == "skipped"
    assert calls[0][0] == "cur_frame"
    assert ("click_shape_center", 69, "退出") in calls
    assert ("wait_click", 69, "退出") not in calls
    assert calls.count(("click_shape_center", 69, "退出")) == 2


def test_daily_boss_daily_list_uses_bidirectional_runtime_scan(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[dict[str, object]] = []
    waits: list[tuple] = []

    class FakeRuntime:
        def open_daily_entry(self, **kwargs):
            calls.append(kwargs)
            if False:
                yield None
            return "open"

    def fake_wait(*args, **kwargs):
        waits.append((args, kwargs))
        if False:
            yield None
        return 178

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_boss_list", fake_wait)

    result = _drain_generator(
        runner._open_daily_boss_list_from_daily(
            {"asset_tree_path": Path("asset.json")},
            fanxiu.threading.Event(),
        )
    )

    assert result == "success"
    assert calls[0]["title_pattern"] == r"击\s*败\s*首\s*领"
    assert calls[0]["progress_can_mark_done"] is False
    assert calls[0]["max_scrolls"] == 10
    assert calls[0]["reverse_scrolls"] == 10
    assert waits


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
    state = {"clicks": 0}
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
        def cur_frame(self, update=False):
            actions.append(("cur_frame", update))
            return "purchase-frame"

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
            if view_id == 229 and shape == "购买并使用":
                state["clicks"] += 1
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            yield None
            return "success"

        def current_scene(self, views, **kwargs):
            actions.append(("current_scene", tuple(views), kwargs))
            if state["clicks"] >= 3:
                return 233, 100.0, "empty-frame"
            return 229, 100.0, "purchase-frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "empty-frame":
                return "游历符持有数量：0 每日限购 提升VIP等级增加购买次数"
            return "游历符 购买并使用"

    def close_empty_home(_ctx, _stop_event, _image233, *, task_label):
        actions.append(("close_empty_home", _image233["id"], task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_close_daily_youli_purchase_empty_and_wait_home", close_empty_home)
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
    assert actions[-1] == ("close_empty_home", 233, "日常_游历")


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

        def ocr_matches(self, predicate, **kwargs):
            label = str(kwargs.get("label") or "")
            sample = "游历符持有数量：0 每日限购 提升VIP等级增加购买次数" if "购买次数不足" in label else "修仙传 游历 仙界 探索完成"
            actions.append(("ocr_matches", predicate(sample), kwargs))
            return ("ocr_matches", label)

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
        ("ocr_matches", True, {"label": "日常_游历：购买次数不足 OCR", "preview_chars": 120}),
        ("view_visible", 228, {"threshold": 95.0}),
        ("ocr_matches", True, {"label": "日常_游历：购买后修仙传游历 OCR", "preview_chars": 120}),
        (
            "wait_click_then_any",
            228,
            "购买",
            {
                "purchase": ("shape_visible", 229, "购买并使用"),
                "empty": ("shape_visible", 233, "空白"),
                "empty_text": ("ocr_matches", "日常_游历：购买次数不足 OCR"),
                "home": ("view_visible", 228),
                "home_text": ("ocr_matches", "日常_游历：购买后修仙传游历 OCR"),
            },
            {"settle_seconds": 1.5, "label": "日常_游历：等待购买体力结果"},
        ),
        ("purchase_uses", 229, 233, "日常_游历"),
    ]


def test_daily_youli_open_purchase_closes_when_empty_text_matches(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    image228 = {"id": 228, "title": "修仙传游历"}
    image229 = {"id": 229, "title": "游历购买体力"}
    image233 = {"id": 233, "title": "游历购买次数不足"}

    class FakeRuntime:
        def shape_visible(self, view_id, shape, **kwargs):
            return ("shape_visible", view_id, shape)

        def view_visible(self, view_id, **kwargs):
            return ("view_visible", view_id)

        def ocr_matches(self, predicate, **kwargs):
            label = str(kwargs.get("label") or "")
            sample = "游历符持有数量：0 每日限购 提升VIP等级增加购买次数" if "购买次数不足" in label else "修仙传 游历 仙界 探索完成"
            return ("ocr_matches", label, predicate(sample))

        def wait_click_then_any(self, view_id, shape, conditions, **kwargs):
            actions.append(("wait_click_then_any", view_id, shape, conditions, kwargs))
            if False:
                yield None
            return "empty_text"

    def fake_wait_home(*_args, **_kwargs):
        actions.append(("wait_home",))
        if False:
            yield None
        return "success"

    def fake_close(_ctx, _stop_event, _image233, *, task_label):
        actions.append(("close_empty", _image233["id"], task_label))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_home)
    monkeypatch.setattr(runner, "_close_daily_youli_purchase_empty", fake_close)
    monkeypatch.setattr(runner, "_click_daily_youli_purchase_uses", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not purchase")))

    result = _drain_generator(
        runner._open_daily_youli_purchase(
            ctx,
            fanxiu.threading.Event(),
            {},
            image228,
            image229,
            image233,
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert actions[-1] == ("close_empty", 233, "日常_游历")
    assert actions[0] == ("wait_home",)


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
    frame = object()

    class FakeRuntime:
        def current_scene(self, candidates, **kwargs):
            actions.append(("current_scene", candidates, kwargs))
            return 228, 100.0, frame

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "修仙传 游历 人界"

        def ocr_centers_in_shape(self, view_id, shape_title, **kwargs):
            actions.append(("ocr_centers", view_id, shape_title, kwargs))
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
        ("current_scene", [236, 228], {"update": True}),
        ("ocr_text", frame),
        ("wait_home",),
        ("ocr_centers", 228, "检索区域", {"include": ()}),
        ("click_point", 228, 120.0, 300.0),
        ("settle", 1.5),
        ("wait_region",),
        ("quick", 236, 237, "日常_游历"),
        ("return", 228, 236, "日常_游历"),
    ]


def test_daily_youli_last_region_continues_from_region_detail_after_purchase(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    image228 = {"id": 228, "title": "修仙传游历", "shapes": [{"title": "检索区域"}]}
    image236 = {"id": 236, "title": "游历区域详情"}
    image237 = {"id": 237, "title": "游历结果"}
    frame = object()

    class FakeRuntime:
        def current_scene(self, candidates, **kwargs):
            actions.append(("current_scene", candidates, kwargs))
            return None, 0.0, frame

        def ocr_text(self, seen_frame):
            actions.append(("ocr_text", seen_frame))
            return "黑风海域 黑风煞翼 背景介绍 挑战奖励 当前模式：修罗 今日可挑战次数：6/3 组队扫荡"

    def fake_wait_home(*_args, **_kwargs):
        actions.append(("wait_home",))
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
    monkeypatch.setattr(runner, "_click_daily_youli_quick_travel", fake_quick)
    monkeypatch.setattr(runner, "_return_daily_youli_to_world", fake_return)

    result = _drain_generator(
        runner._click_daily_youli_last_region(
            ctx,
            fanxiu.threading.Event(),
            {},
            image228,
            image236,
            image237,
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert actions == [
        ("current_scene", [236, 228], {"update": True}),
        ("ocr_text", frame),
        ("quick", 236, 237, "日常_游历"),
        ("return", 228, 236, "日常_游历"),
    ]


def test_daily_youli_last_region_completed_region_does_not_mark_done(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")
    image228 = {"id": 228, "title": "修仙传游历", "shapes": [{"title": "检索区域"}]}
    image236 = {"id": 236, "title": "游历区域详情"}
    image237 = {"id": 237, "title": "游历结果"}
    frame = object()

    class FakeRuntime:
        def current_scene(self, candidates, **kwargs):
            actions.append(("current_scene", candidates, kwargs))
            return 236, 100.0, frame

        def ocr_text(self, seen_frame):
            actions.append(("ocr_text", seen_frame))
            return "黑风煞翼 背景介绍 挑战奖励 当前模式：修罗 已完成所有挑战 今日可挑战次数：0/3"

        def ocr_centers_in_shape(self, view_id, shape_title, **kwargs):
            actions.append(("ocr_centers", view_id, shape_title, kwargs))
            return []

    def fake_quick(_ctx, _stop_event, _payload, _image236, _image237, *, task_label):
        actions.append(("quick", _image236["id"], _image237["id"], task_label))
        if False:
            yield None
        return "completed"

    def fake_return_region(_ctx, _stop_event, _payload, _image236, *, task_label):
        actions.append(("return_region", _image236["id"], task_label))
        if False:
            yield None
        return "success"

    def fake_wait_home(*_args, **_kwargs):
        actions.append(("wait_home",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_click_daily_youli_quick_travel", fake_quick)
    monkeypatch.setattr(runner, "_return_daily_youli_region_to_home", fake_return_region)
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_home)
    monkeypatch.setattr(
        runner,
        "_return_daily_youli_to_world",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("completed region must not mark daily_youli done")),
    )

    with pytest.raises(RuntimeError, match="未识别到可点击 OCR 文本"):
        _drain_generator(
            runner._click_daily_youli_last_region(
                ctx,
                fanxiu.threading.Event(),
                {},
                image228,
                image236,
                image237,
                task_label="日常_游历",
            )
        )
    assert actions == [
        ("current_scene", [236, 228], {"update": True}),
        ("ocr_text", frame),
        ("quick", 236, 237, "日常_游历"),
        ("return_region", 236, "日常_游历"),
        ("wait_home",),
        ("ocr_centers", 228, "检索区域", {"include": ()}),
    ]


def test_daily_youli_execute_region_completed_continues_without_mark_done(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    monkeypatch.setattr(
        runner,
        "_daily_youli_current_state",
        lambda *_args, **_kwargs: (
            236,
            100.0,
            object(),
            "黑风煞翼 背景介绍 挑战奖励 已完成所有挑战 今日可挑战次数：0/3",
        ),
    )
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: object())

    def fake_quick(*_args, **_kwargs):
        actions.append(("quick",))
        if False:
            yield None
        return "completed"

    def fake_return_region(*_args, **_kwargs):
        actions.append(("return_region",))
        if False:
            yield None
        return "success"

    def fake_open_purchase(*_args, **_kwargs):
        actions.append(("open_purchase",))
        if False:
            yield None
        return "success"

    def fake_last_region(*_args, **_kwargs):
        actions.append(("last_region",))
        if False:
            yield None
        return "retry-later"

    monkeypatch.setattr(runner, "_click_daily_youli_quick_travel", fake_quick)
    monkeypatch.setattr(runner, "_return_daily_youli_region_to_home", fake_return_region)
    monkeypatch.setattr(runner, "_open_daily_youli_purchase", fake_open_purchase)
    monkeypatch.setattr(runner, "_click_daily_youli_last_region", fake_last_region)
    monkeypatch.setattr(
        runner,
        "_return_daily_youli_to_world",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("completed region must not mark daily_youli done")),
    )

    result = _drain_generator(
        runner._execute_daily_youli_task(
            ctx,
            fanxiu.threading.Event(),
            {},
        )
    )

    assert result == "retry-later"
    assert actions == [("quick",), ("return_region",), ("open_purchase",), ("last_region",)]


def test_daily_youli_region_return_reenters_when_back_lands_world(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {
            34: {"id": 34, "title": "世界", "shapes": [{"title": "主线"}]},
            71: {"id": 71, "title": "修仙传"},
            228: {"id": 228, "title": "修仙传游历"},
        },
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def wait_click(self, view_id, shape, **_kwargs):
            actions.append(("wait_click", view_id, shape))
            if False:
                yield None
            return "success"

        def ocr_matches(self, _predicate, **kwargs):
            actions.append(("ocr_matches", kwargs.get("label")))
            return ("ocr", kwargs.get("label"))

        def view_visible(self, view_id):
            actions.append(("view_visible", view_id))
            return ("view", view_id)

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", sorted(conditions), kwargs.get("label")))
            if False:
                yield None
            return "world_text"

        def current_scene(self, view_ids=None, **_kwargs):
            actions.append(("current_scene", tuple(view_ids or ())))
            return 228, 100.0, "frame"

    def fake_try_enter(*_args, **_kwargs):
        actions.append(("try_enter",))
        if False:
            yield None
        return True

    def fake_wait_home(*_args, **_kwargs):
        actions.append(("wait_home",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_try_enter_daily_youli_from_world_mainline", fake_try_enter)
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_home)

    result = _drain_generator(
        runner._return_daily_youli_region_to_home(
            ctx,
            fanxiu.threading.Event(),
            {},
            {"id": 236, "title": "游历区域详情"},
            task_label="日常_游历",
        )
    )

    assert result == "success"
    assert ("try_enter",) in actions
    assert ("wait_home",) in actions


def test_daily_youli_quick_travel_reports_completed_without_success(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def click_shape_center(self, view_id, shape_title):
            actions.append(("click_shape_center", view_id, shape_title))

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

        def ocr_matches(self, predicate, **kwargs):
            actions.append((
                "ocr_matches",
                predicate("黑风海域 背景介绍 挑战奖励 当前模式：修罗 已完成所有挑战 今日可挑战次数：0/3"),
                kwargs,
            ))
            return ("ocr_matches", kwargs.get("label"))

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", sorted(conditions), kwargs))
            if False:
                yield None
            return "completed"

    def fake_wait_region(*_args, **_kwargs):
        actions.append(("wait_region",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_region_detail", fake_wait_region)

    result = _drain_generator(
        runner._click_daily_youli_quick_travel(
            ctx,
            fanxiu.threading.Event(),
            {"quick_travel_settle_seconds": 1.5},
            {"id": 236},
            {"id": 237},
            task_label="日常_游历",
        )
    )

    assert actions[:3] == [
        ("wait_region",),
        ("click_shape_center", 236, "快速游历"),
        ("settle", 1.5),
    ]
    assert result == "completed"


def test_daily_youli_quick_travel_resource_empty_closes_prompt(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset_tree.json",
        "images": {233: {"id": 233, "title": "游历购买次数不足"}},
    }
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def click_shape_center(self, view_id, shape_title):
            actions.append(("click_shape_center", view_id, shape_title))

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return None

        def ocr_matches(self, predicate, **kwargs):
            sample = "游历符持有数量：0 每日限购 提升VIP等级增加购买次数"
            actions.append(("ocr_matches", bool(predicate(sample)), kwargs.get("label")))
            return ("ocr_matches", kwargs.get("label"))

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", sorted(conditions), kwargs.get("label")))
            if False:
                yield None
            return "resource_empty"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

    def fake_wait_region(*_args, **_kwargs):
        actions.append(("wait_region",))
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_region_detail", fake_wait_region)

    try:
        _drain_generator(
            runner._click_daily_youli_quick_travel(
                ctx,
                fanxiu.threading.Event(),
                {"quick_travel_settle_seconds": 1.5},
                {"id": 236},
                {"id": 237},
                task_label="日常_游历",
            )
        )
    except RuntimeError as exc:
        assert "快速游历未触发结果" in str(exc)
    else:
        raise AssertionError("resource-empty quick travel should fail fast")

    assert ("wait_click", 233, "空白", {"label": "日常_游历：关闭购买次数不足提示"}) in actions


def test_daily_youli_region_completed_accepts_no_challenge_count():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_youli_text_is_region_completed(
        "黑风海域 背景介绍 挑战奖励 当前模式：修罗 已完成所有挑战 今日可挑战次数：0/3 组队扫荡"
    ) is True
    home_text = "260/330 修仙传 人界 灵界 魔界 仙界 探索完成 境界达到真仙中期一层开启 北寒蛮荒 游历道祖逸闻"
    assert runner._daily_youli_text_is_region_detail(home_text) is False
    assert runner._daily_youli_text_is_region_completed(home_text) is False


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
    menu_selected: list[str] = []

    def click_shape(ctx, stop_event, image, shape, payload, **kwargs):
        clicked.append(shape["title"])
        if False:
            yield None
        return "success"

    def wait_settle(*args, **kwargs):
        if False:
            yield None
        return "success"

    def select_menu(*args, **kwargs):
        menu_selected.append("游历")
        if False:
            yield None
        return True

    class FakeRuntime:
        def wait_view(self, *view_ids, **kwargs):
            waited.append(tuple(view_ids))
            if False:
                yield None
            return 228

    monkeypatch.setattr(runner, "_click_shape_respecting_conditions", click_shape)
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", wait_settle)
    monkeypatch.setattr(runner, "_select_daily_youli_tab_from_menu_if_visible", select_menu)
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
    assert menu_selected == ["游历"]


def test_daily_youli_mainline_shortcut_rejects_daozu_tab(monkeypatch):
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

    class FakeRuntime:
        def wait_view(self, *view_ids, **kwargs):
            if False:
                yield None
            return 228

    def empty_result(value):
        if False:
            yield None
        return value

    monkeypatch.setattr(runner, "_click_shape_respecting_conditions", lambda *args, **kwargs: empty_result("success"))
    monkeypatch.setattr(runner, "_wait_runtime_action_settle", lambda *args, **kwargs: empty_result("success"))
    monkeypatch.setattr(runner, "_select_daily_youli_tab_from_menu_if_visible", lambda *args, **kwargs: empty_result(False))
    monkeypatch.setattr(runner, "_screencap", lambda ctx: "daozu-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "修仙传 道祖鸿蒙 幻境 机缘 游历道祖逸闻"}])
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

    assert result is False


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


def test_runtime_ocr_words_in_shapes_requests_word_boxes_and_restores_crop_offset(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image = {
        "id": 265,
        "title": "法则之主",
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "识别区", "x": 0.1, "y": 0.2, "w": 0.4, "h": 0.3}],
    }
    observed: list[dict[str, object] | None] = []

    monkeypatch.setattr(
        runner,
        "_crop_frame_data_url_for_shapes",
        lambda *_args, **_kwargs: ("crop-frame", 90.0, 320.0),
    )

    def fake_ocr_frame(frame_data_url, *, options=None):
        observed.append(options)
        assert frame_data_url == "crop-frame"
        return {
            "lines": [{"text": "魔道仙弈", "x": 10.0, "y": 20.0, "w": 80.0, "h": 24.0}],
            "words": [
                {"text": "魔", "x": 12.0, "y": 20.0, "w": 16.0, "h": 24.0, "line_index": 0},
                {"text": "道", "x": 30.0, "y": 20.0, "w": 16.0, "h": 24.0, "line_index": 0},
            ],
        }

    monkeypatch.setattr(runner, "_ocr_frame", fake_ocr_frame)
    ctx = {"images": {265: image}}
    runtime = runner._fanxiu_runtime(ctx)

    words = runtime.ocr_words_in_shapes(265, ["识别区"], frame_data_url="frame")

    assert observed == [{"return_word_box": True}]
    assert words == [
        {"text": "魔", "x": 102.0, "y": 340.0, "w": 16.0, "h": 24.0, "line_index": 0},
        {"text": "道", "x": 120.0, "y": 340.0, "w": 16.0, "h": 24.0, "line_index": 0},
    ]


def test_daily_youli_home_text_rejects_xiuxianzhuan_story_menu():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_youli_text_is_home("修仙传 游历 人界 探索完成") is True
    assert runner._daily_youli_text_is_home("修仙传 游历 人界 游历道祖逸闻") is True
    assert runner._daily_youli_text_is_home("修仙传 道祖鸿蒙 幻境 机缘 游历道祖逸闻") is False


def test_daily_youli_wait_home_selects_youli_menu_when_228_is_daozu(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    selected: list[str] = []
    actions: list[tuple] = []
    ctx = {
        "asset_tree_path": tmp_path / "asset-tree.json",
        "images": {228: {"id": 228, "title": "修仙传游历", "shapes": [{"title": "菜单"}]}},
    }

    class FakeRuntime:
        def wait_view_or_ocr(self, view_id, predicate, **kwargs):
            actions.append(("wait_view_or_ocr", view_id, predicate("修仙传 道祖鸿蒙 幻境 机缘 游历道祖逸闻"), kwargs.get("label")))
            if False:
                yield None
            return "scene", 228, 100.0

        def cur_frame(self, update=False):
            return "daozu-frame"

        def ocr_text(self, frame):
            return "修仙传 道祖鸿蒙 幻境 机缘 游历道祖逸闻"

        def ocr_matches(self, predicate, **kwargs):
            actions.append(("ocr_matches", predicate("修仙传 游历 人界 游历道祖逸闻"), kwargs.get("label")))
            return ("ocr", kwargs.get("label"))

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", sorted(conditions), kwargs.get("label")))
            if False:
                yield None
            return "text"

        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs.get("update")))
            return 228, 100.0, "youli-frame"

    def fake_select(*_args, **_kwargs):
        selected.append("游历")
        if False:
            yield None
        return True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_select_daily_youli_tab_from_menu_if_visible", fake_select)

    result = _drain_generator(
        runner._wait_daily_youli_home(
            ctx,
            fanxiu.threading.Event(),
            label="日常_游历：等待修仙传游历 #228",
        )
    )

    assert result == (228, 100.0)
    assert selected == ["游历"]
    assert ("wait_any", ["text"], "日常_游历：等待修仙传游历 #228：确认游历菜单已选中") in actions


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

        def view_visible(self, view_id, **_kwargs):
            return ("view", int(view_id))

        def ocr_matches(self, predicate, **_kwargs):
            return ("ocr_matches", predicate)

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", tuple(conditions.keys()), kwargs.get("label")))
            for key, condition in conditions.items():
                kind = condition[0]
                if kind == "view" and state["scene"] == condition[1]:
                    if False:
                        yield None
                    return key
                if kind == "ocr_matches" and condition[1](self.ocr_text()):
                    if False:
                        yield None
                    return key
            raise AssertionError(f"no fake wait_any condition matched: {conditions}")

    def fake_wait_daily_youli_home(*_args, **kwargs):
        actions.append(("wait_home", kwargs.get("label")))
        assert state["scene"] == 228
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
        ("wait_home", "日常_游历：等待 #236 返回到修仙传游历 #228"),
        ("wait_click", 228, "返回"),
        ("wait_any", ("world_scene", "world_text", "daily_text"), "日常_游历：等待 #228 返回到日常或世界"),
    ]


def test_daily_youli_return_to_world_exits_daily_page_after_228_return(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    state = {"scene": 228, "text": "修仙传游历 返回"}
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def current_scene(self, view_ids=None, **_kwargs):
            actions.append(("current_scene", tuple(view_ids or ())))
            return state["scene"], 100.0, "frame"

        def ocr_text(self, _frame=None):
            actions.append(("ocr_text", state["text"]))
            return state["text"]

        def wait_click(self, view_id, shape, **_kwargs):
            actions.append(("wait_click", view_id, shape))
            assert view_id == 228
            assert shape == "返回"
            state["scene"] = 69
            state["text"] = "日常 活跃度 游历 1/1"
            if False:
                yield None
            return "success"

        def view_visible(self, view_id, **_kwargs):
            return ("view", int(view_id))

        def ocr_matches(self, predicate, **_kwargs):
            return ("ocr_matches", predicate)

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", tuple(conditions.keys()), kwargs.get("label")))
            for key, condition in conditions.items():
                kind = condition[0]
                if kind == "view" and state["scene"] == condition[1]:
                    if False:
                        yield None
                    return key
                if kind == "ocr_matches" and condition[1](self.ocr_text()):
                    if False:
                        yield None
                    return key
            raise AssertionError(f"no fake wait_any condition matched: {conditions}")

    def fake_wait_daily_youli_home(*_args, **kwargs):
        actions.append(("wait_home", kwargs.get("label")))
        assert state["scene"] == 228
        if False:
            yield None
        return "success"

    def fake_exit_daily_page(*_args, **_kwargs):
        actions.append(("exit_daily_page",))
        if False:
            yield None
        return "success"

    record_calls: list[dict[str, object]] = []

    def fake_record_daily_entry_done(*_args, **kwargs):
        actions.append(("record_done", kwargs.get("message")))
        record_calls.append(kwargs)

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_youli_home", fake_wait_daily_youli_home)
    monkeypatch.setattr(runner, "_exit_daily_youli_daily_page_to_world", fake_exit_daily_page)
    monkeypatch.setattr(runner, "_record_daily_entry_done", fake_record_daily_entry_done)

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
    assert actions.index(("exit_daily_page",)) < actions.index(("record_done", "游历已完成并回到世界"))
    assert record_calls == [{
        "task_id": "legacy-daily-youli",
        "task_type": "daily_youli",
        "label": "日常_游历",
        "message": "游历已完成并回到世界",
    }]


def test_daily_youli_reward_recovery_return_uses_runtime_clicks(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    state = {"scene": 69, "after_first_exit": True}
    ctx = {"asset_tree_path": tmp_path / "asset_tree.json", "images": {}}
    ctx["asset_tree_path"].write_text("[]", encoding="utf-8")

    class FakeRuntime:
        def cur_frame(self, update=False):
            actions.append(("cur_frame", update))
            return "frame"

        def click_shape_center(self, view_id, shape, **_kwargs):
            actions.append(("click_shape_center", view_id, shape))
            if view_id == 69 and shape == "退出":
                if state["after_first_exit"]:
                    state["after_first_exit"] = False
                    state["scene"] = 69
                    state["text"] = "日常 活跃度 活动报名 小助手 奖励找回"
                else:
                    state["scene"] = 34

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
            return state.get("text", "奖励找回 一键免费 一键全部 全部找回")

        def view_visible(self, view_id, **_kwargs):
            return ("view", int(view_id))

        def ocr_matches(self, predicate, **_kwargs):
            return ("ocr_matches", predicate)

        def wait_any(self, conditions, **kwargs):
            actions.append(("wait_any", tuple(conditions.keys()), kwargs.get("label")))
            for key, condition in conditions.items():
                kind = condition[0]
                if kind == "view" and state["scene"] == condition[1]:
                    if False:
                        yield None
                    return key
                if kind == "ocr_matches" and condition[1](self.ocr_text()):
                    if False:
                        yield None
                    return key
            raise AssertionError(f"no fake wait_any condition matched: {conditions}")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(
        runner,
        "_record_daily_entry_done",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reward recovery cleanup must not mark daily_youli done")),
    )

    result = _drain_generator(
        runner._return_daily_youli_reward_recovery_to_world(
            ctx,
            fanxiu.threading.Event(),
            task_label="日常_游历",
        )
    )

    assert result["result"] == "skipped"
    assert "不是游历完成证据" in result["message"]
    assert actions == [
        ("cur_frame", True),
        ("ocr_text",),
        ("click_shape_center", 69, "退出"),
        ("settle", 1.5),
        ("wait_any", ("world_scene", "world_text", "daily_text"), "日常_游历：等待奖励找回关闭后回到日常或世界"),
        ("ocr_text",),
        ("ocr_text",),
        ("click_shape_center", 69, "退出"),
        ("settle", 1.5),
        ("wait_any", ("world_scene", "world_text", "daily_text"), "日常_游历：等待日常退出到世界"),
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


@pytest.mark.skip(reason="日常_异火已从作业入口删除，不再自动执行")
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

        def current_scene(self, *_args, **_kwargs):
            actions.append("current_scene")
            return state["page"], 100.0, str(state["page"])

        def ocr_text(self, _frame=None):
            actions.append("ocr_text")
            if state["page"] == 34:
                return "大地图"
            if state["page"] == 259:
                return "蓝色星海 异火 提纯"
            if state["page"] == 261:
                return "已领取 次日5点刷新 净莲妖火"
            return ""

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

        def click_shape_center(self, view_id, shape, **kwargs):
            actions.append(f"click_shape_center:{view_id}:{shape}")
            if view_id == 259 and shape == "异火":
                state["page"] = 260
            elif view_id == 260 and shape == "净莲":
                state["page"] = 261
            elif view_id == 261 and shape == "返回":
                state["page"] = 260
            elif view_id == 260 and shape == "返回":
                state["page"] = 259
            elif view_id == 259 and shape == "返回":
                state["page"] = 34

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

        def ocr_matches(self, predicate, **kwargs):
            return ("ocr_matches", predicate)

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
                    all_of = condition[1].get("all_of") or ()
                    any_of = condition[1].get("any_of") or ()
                    text = self.ocr_text()
                    return all(item in text for item in all_of) and (not any_of or any(item in text for item in any_of))
                if kind == "ocr_matches":
                    return bool(condition[1](self.ocr_text()))
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
        "current_scene",
        "ocr_text",
        "goto:34",
        "wait_click:34:下方菜单/星海",
        "ocr_text",
        "click_shape_center:259:异火",
        "wait_view:(260,)",
        "click_shape_center:260:净莲",
        "wait_click:261:箱子",
        "click_shape_center:261:返回",
        "ocr_text",
        "click_shape_center:260:返回",
        "ocr_text",
        "click_shape_center:259:返回",
        "ocr_text",
        "wait_view:(34,)",
    ]


@pytest.mark.skip(reason="日常_异火已从作业入口删除，不再自动执行")
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


def test_daily_entry_recovers_from_hidden_world_popup_before_goto_daily(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    images = {
        34: {
            "id": 34,
            "title": "世界",
            "width": 900,
            "height": 1600,
            "shapes": [],
        },
        59: {
            "id": 59,
            "title": "封魔杀",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "封魔杀", "x": 0.34, "y": 0.41, "w": 0.23, "h": 0.10},
                {"title": "空白", "x": 0.61, "y": 0.01, "w": 0.11, "h": 0.03},
            ],
        },
        69: {
            "id": 69,
            "title": "日常",
            "width": 900,
            "height": 1600,
            "shapes": [
                {"title": "日常", "x": 0.05, "y": 0.05, "w": 0.12, "h": 0.06},
            ],
        },
    }
    state = {"page": 59}
    actions: list[str] = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            actions.append("current_scene")
            if state["page"] == 59:
                return None, 0.0, str(state["page"])
            return state["page"], 100.0, str(state["page"])

        def ocr_text(self, _frame=None):
            actions.append("ocr_text")
            if state["page"] == 59:
                return ""
            if state["page"] == 69:
                return "日常 活跃度 活动报名 活 0/次"
            return "世界 主线"

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(f"wait_click:{view_id}:{shape}:{kwargs.get('label')}")
            assert (view_id, shape) == (59, "空白")
            state["page"] = 34
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=0.0):
            actions.append(f"settle:{seconds}")
            if False:
                yield None
            return None

        def find_view(self, group=""):
            return None

        def goto_view(self, view_id):
            actions.append(f"goto:{view_id}")
            state["page"] = int(view_id)
            if False:
                yield None
            return "success"

    def no_leave(*_args, **_kwargs):
        actions.append("leave_check")
        if False:
            yield None
        return False

    monkeypatch.setattr(runner, "_leave_world_side_scene_if_present", no_leave)

    def popup_guard(runtime, **kwargs):
        actions.append(f"popup_guard:{kwargs.get('allow_confirm_actions')}:{kwargs.get('during_task')}")
        state["page"] = 34
        return True

    monkeypatch.setattr(runner, "_auto_close_popup_guard_step", popup_guard)

    result = _drain_generator(
        runner._enter_daily_from_world_like(
            {"images": images},
            FakeRuntime(),
            fanxiu.threading.Event(),
            "59",
            None,
            "",
            label="日常_妖王来袭",
        )
    )

    assert result == 69
    assert actions == [
        "current_scene",
        "ocr_text",
        "popup_guard:False:True",
        "settle:2.0",
        "current_scene",
        "ocr_text",
        "leave_check",
        "goto:69",
        "current_scene",
        "ocr_text",
    ]


def test_scene_recovery_closes_hidden_world_popup_before_unknown_world_goto(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image59 = {
        "id": 59,
        "title": "封魔杀",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "封魔杀", "x": 0.34, "y": 0.41, "w": 0.23, "h": 0.10},
            {"title": "空白", "x": 0.61, "y": 0.01, "w": 0.11, "h": 0.03},
        ],
    }
    actions: list[str] = []
    ctx = {"images": {59: image59}}

    monkeypatch.setattr(runner, "_shape_score", lambda ctx, image, shape, frame: 100.0)
    monkeypatch.setattr(runner, "_save_action_trace", lambda *args, **kwargs: actions.append("trace"))

    def click_frame_point(ctx, image, x, y):
        actions.append(f"click:{image['id']}:{round(x, 1)}:{round(y, 1)}")

    monkeypatch.setattr(runner, "_click_frame_point", click_frame_point)

    assert runner._recover_unknown_start_to_world(ctx, "frame", target_scene_id=34) is True
    assert actions == ["trace", "click:59:598.5:40.0"]


def test_scene_recovery_unknown_world_goto_does_not_assume_green_bottle(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "id": 34,
        "title": "世界",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "空白", "x": 0.10, "y": 0.10, "w": 0.20, "h": 0.20},
        ],
    }
    actions: list[str] = []
    ctx = {"images": {34: image34}}

    monkeypatch.setattr(runner, "_recover_unknown_promo_offer_page", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_recover_unknown_hidden_world_popup", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_click_frame_point", lambda *_args, **_kwargs: actions.append("click"))

    assert runner._recover_unknown_start_to_world(ctx, "frame", target_scene_id=34) is False
    assert actions == []


@pytest.mark.skip(reason="日常_异火已从作业入口删除，不再自动执行")
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

        def current_scene(self, *_args, **_kwargs):
            actions.append("current_scene")
            return state["page"], 100.0, str(state["page"])

        def ocr_text(self, _frame=None):
            actions.append("ocr_text")
            if state["page"] == 34:
                return "大地图"
            if state["page"] == 259:
                return "蓝色星海 异火 提纯"
            if state["page"] == 261:
                return "已领取 次日5点刷新 净莲妖火"
            return ""

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

        def click_shape_center(self, view_id, shape, **kwargs):
            actions.append(f"click_shape_center:{view_id}:{shape}")
            if view_id == 259 and shape == "异火":
                state["page"] = 260
            elif view_id == 260 and shape == "净莲":
                state["page"] = 261
            elif view_id == 261 and shape == "返回":
                state["page"] = 260
            elif view_id == 260 and shape == "返回":
                state["page"] = 259
            elif view_id == 259 and shape == "返回":
                state["page"] = 34

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

        def ocr_matches(self, predicate, **kwargs):
            return ("ocr_matches", predicate)

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
                    all_of = condition[1].get("all_of") or ()
                    any_of = condition[1].get("any_of") or ()
                    text = self.ocr_text()
                    return all(item in text for item in all_of) and (not any_of or any(item in text for item in any_of))
                if kind == "ocr_matches":
                    return bool(condition[1](self.ocr_text()))
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
        "current_scene",
        "ocr_text",
        "click_shape_center:260:净莲",
        "wait_click:261:箱子",
        "click_shape_center:261:返回",
        "ocr_text",
        "click_shape_center:260:返回",
        "ocr_text",
        "click_shape_center:259:返回",
        "ocr_text",
        "wait_view:(34,)",
    ]


@pytest.mark.skip(reason="日常_异火已从作业入口删除，不再自动执行")
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
    actions = []

    class FakeRuntime:
        def current_scene(self, *_args, **_kwargs):
            return 184, 100.0, "frame"

        def ocr_text(self, *_args, **_kwargs):
            return "灵祖挑战 今日剩余次数 1/1"

        def wait_click(self, *_args, **_kwargs):
            if False:
                yield None
            return "success"

        def click_frame_point(self, image, x, y):
            actions.append(("click_frame_point", image["title"], x, y))

        def wait_action_settle(self, seconds):
            actions.append(("wait_action_settle", seconds))
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
    assert actions == [
        ("click_frame_point", "灵祖挑战详情", pytest.approx(360.0), pytest.approx(1200.0)),
        ("wait_action_settle", 1.0),
    ]


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


def test_scheduler_preflight_reports_dungeon_purchase_without_terminal_return(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            224: {
                "title": "购买破界符",
                "shapes": [
                    {"title": "购买并使用"},
                    {"title": "限购次数标识"},
                ],
            },
        },
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "purchase-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "破界符 剩余限购次数：1 价格：200 购买并使用"}])

    info = runner._known_blocking_overlay_info(ctx)

    assert info == {
        "scene_id": 224,
        "title": "购买破界符",
        "blocking": True,
        "all_shapes": ["购买并使用", "限购次数标识"],
        "message": "检测到 #224「购买破界符」弹窗；资产树缺少 #225「空白」，无法按 #224 连续购买到 #225 后回退",
    }


def test_scheduler_preflight_allows_dungeon_purchase_with_terminal_return(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            224: {
                "title": "购买破界符",
                "shapes": [
                    {"title": "购买并使用"},
                ],
            },
            225: {
                "title": "购买次数不足",
                "shapes": [
                    {"title": "空白", "x": 0.05, "y": 0.9, "w": 0.1, "h": 0.05},
                ],
            },
        },
    }

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "purchase-frame")
    monkeypatch.setattr(runner, "_ocr_lines", lambda frame: [{"text": "破界符 剩余限购次数：1 价格：200 购买并使用"}])

    info = runner._known_blocking_overlay_info(ctx)

    assert info == {
        "scene_id": 224,
        "title": "购买破界符",
        "blocking": False,
        "all_shapes": ["购买并使用"],
        "action_shapes": ["购买并使用", "#225 空白"],
        "message": "检测到 #224「购买破界符」弹窗，已有连续购买与 #225 回退标注",
    }
    assert runner._known_blocking_overlay_message(ctx) is None


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


















def test_data_annotation_run_now_rejects_unverified_task_type(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingquan",
            "task_type": "legacy_daily_task",
            "label": "日常 游历",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
                "legacy_name": "日常_灵泉",
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
                "payload": {"legacy_name": "日常_灵泉"},
            "scheduler_meta": None,
        }
    ])

    with pytest.raises(fanxiu.HTTPException) as exc_info:
        fanxiu.run_now_fanxiu_data_annotation_scheduler_task(
            fanxiu.FanxiuDataAnnotationSchedulerRunNowRequest(
                entry_id="entry",
                task_id="legacy-daily-lingquan",
                payload={},
            ),
            current_user=object(),
            session=object(),
        )

    assert exc_info.value.status_code == 400
    assert "尚未纳入当前框架验收" in str(exc_info.value.detail)


def test_data_annotation_run_due_endpoint_skips_legacy_placeholders(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    selected = {}

    def fake_run_selected(**kwargs):
        selected.update(kwargs["task"])
        return {"running": False, "phase": "done", "status": "success", "message": "测试任务已执行"}

    monkeypatch.setattr(runtime_control, "prepare_runtime_for_scheduler_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime_control, "_run_scheduler_task_cell_and_record_terminal", fake_run_selected)
    disabled_signup = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    disabled_signup["enabled"] = False
    disabled_mail = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "mail-selective-claim").copy()
    disabled_mail["enabled"] = False
    disabled_assistant = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-assistant").copy()
    disabled_assistant["enabled"] = False
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingquan",
            "task_type": "legacy_daily_task",
            "label": "日常 灵泉",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_灵泉",
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
            "payload": {"legacy_name": "日常_灵泉"},
            "scheduler_meta": None,
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
            "scheduler_meta": None,
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
    assert response.phase == "done"
    assert selected["id"] == "legacy-daily-assistant"


def test_data_annotation_run_due_endpoint_queues_synced_default_assistant_task(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    selected = {}

    def fake_run_selected(**kwargs):
        selected.update(kwargs["task"])
        return {"running": False, "phase": "done", "status": "success", "message": "测试任务已执行"}

    monkeypatch.setattr(runtime_control, "prepare_runtime_for_scheduler_task", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime_control, "_run_scheduler_task_cell_and_record_terminal", fake_run_selected)
    disabled_signup = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-signup").copy()
    disabled_signup["enabled"] = False
    disabled_mail = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "mail-selective-claim").copy()
    disabled_mail["enabled"] = False
    disabled_assistant = next(item for item in fanxiu._default_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-assistant").copy()
    disabled_assistant["enabled"] = False
    fanxiu._write_data_annotation_scheduler_tasks([
        {
            "id": "legacy-daily-lingquan",
            "task_type": "legacy_daily_task",
            "label": "日常 灵泉",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_灵泉",
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
            "payload": {"legacy_name": "日常_灵泉"},
            "scheduler_meta": None,
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
    assert response.phase == "done"
    assert selected["id"] == "legacy-daily-assistant"


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


def test_data_annotation_service_behavior_tree_endpoint_uses_service_entry_and_shared_paths(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    service_entry = type("ServiceEntry", (), {"entry_id": "resolved-behavior-entry"})()
    calls = {}

    monkeypatch.setattr(fanxiu, "_get_service_user_device_or_404", lambda session, entry_id: service_entry)

    def fake_set_kernel_enabled(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "behavior_tree_enabled": kwargs["enabled"],
            "running": False,
            "status": "idle",
            "entry_id": kwargs["entry_id"],
            "message": "service behavior tree set",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._runtime_framework, "set_kernel_enabled", fake_set_kernel_enabled)

    response = fanxiu.set_fanxiu_data_annotation_runtime_service_behavior_tree(
        fanxiu.FanxiuDataAnnotationRuntimeBehaviorTreeRequest(entry_id="request-behavior-entry", enabled=False),
        session=object(),
    )

    assert response.behavior_tree_enabled is False
    assert response.entry_id == "resolved-behavior-entry"
    assert calls["entry"] is service_entry
    assert calls["entry_id"] == "resolved-behavior-entry"
    assert calls["enabled"] is False
    assert calls["asset_tree_path"] == tmp_path / "resolved-behavior-entry.json"
    assert calls["scheduler_settings_path"] == _scheduler_settings_path(tmp_path)
    assert calls["runtime_state_path"] == tmp_path / "runtime_state.json"
    assert calls["world_facts_path"] == tmp_path / "world_facts.json"


def test_data_annotation_service_kernel_restart_endpoint_uses_service_entry_and_shared_paths(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    service_entry = type("ServiceEntry", (), {"entry_id": "resolved-kernel-entry"})()
    calls = {}

    monkeypatch.setattr(fanxiu, "_get_service_user_device_or_404", lambda session, entry_id: service_entry)

    def fake_restart_kernel(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "running": True,
            "status": "running",
            "entry_id": kwargs["entry_id"],
            "message": "service kernel restarted",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._runtime_framework, "restart_kernel", fake_restart_kernel)

    response = fanxiu.restart_fanxiu_data_annotation_runtime_service_kernel(
        fanxiu.FanxiuDataAnnotationRuntimeKernelRestartRequest(
            entry_id="request-kernel-entry",
            timeout_seconds=11,
        ),
        session=object(),
    )

    assert response.running is True
    assert response.entry_id == "resolved-kernel-entry"
    assert calls["entry"] is service_entry
    assert calls["entry_id"] == "resolved-kernel-entry"
    assert calls["timeout_seconds"] == 11
    assert calls["asset_tree_path"] == tmp_path / "resolved-kernel-entry.json"
    assert calls["scheduler_settings_path"] == _scheduler_settings_path(tmp_path)
    assert calls["runtime_state_path"] == tmp_path / "runtime_state.json"
    assert calls["world_facts_path"] == tmp_path / "world_facts.json"








def test_data_annotation_service_code_cell_endpoint_uses_service_entry_and_shared_helper(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    service_entry = type("ServiceEntry", (), {"entry_id": "resolved-code-cell-entry"})()
    calls = {}

    monkeypatch.setattr(fanxiu, "_get_service_user_device_or_404", lambda session, entry_id: service_entry)

    def fake_submit_code_cell(entry, entry_id, req, *, source=""):
        calls["entry"] = entry
        calls["entry_id"] = entry_id
        calls["code"] = req.code
        calls["mode"] = req.mode
        calls["timeout_seconds"] = req.timeout_seconds
        calls["max_output_chars"] = req.max_output_chars
        calls["source"] = source
        return {
            "ok": True,
            "running": True,
            "status": "running",
            "entry_id": entry_id,
            "message": "code cell submitted",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu, "_submit_data_annotation_code_cell", fake_submit_code_cell)

    response = fanxiu.submit_fanxiu_data_annotation_runtime_service_code_cell(
        fanxiu.FanxiuDataAnnotationRuntimeCodeCellRequest(
            entry_id="request-code-cell-entry",
            code="result = ctx.scene()",
            mode="readonly",
            timeout_seconds=66,
            max_output_chars=888,
        ),
        session=object(),
    )

    assert response.running is True
    assert response.entry_id == "resolved-code-cell-entry"
    assert calls == {
        "entry": service_entry,
        "entry_id": "resolved-code-cell-entry",
        "code": "result = ctx.scene()",
        "mode": "readonly",
        "timeout_seconds": 66,
        "max_output_chars": 888,
        "source": "service",
    }


def test_data_annotation_service_stop_task_endpoint_uses_shared_runtime_paths(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    calls = {}

    def fake_interrupt_current_cell(entry_id, **kwargs):
        calls["entry_id"] = entry_id
        calls.update(kwargs)
        return {
            "ok": True,
            "running": False,
            "status": "idle",
            "entry_id": entry_id,
            "message": "service task stopped",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._runtime_framework, "interrupt_current_cell", fake_interrupt_current_cell)

    response = fanxiu.stop_fanxiu_data_annotation_runtime_service_task(
        fanxiu.FanxiuDataAnnotationRuntimeStopRequest(entry_id="request-stop-entry")
    )

    assert response.running is False
    assert response.entry_id == "request-stop-entry"
    assert calls["entry_id"] == "request-stop-entry"
    assert calls["runtime_state_path"] == tmp_path / "runtime_state.json"
    assert calls["world_facts_path"] == tmp_path / "world_facts.json"


def test_data_annotation_service_guard_endpoint_uses_service_entry_and_shared_paths(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    service_entry = type("ServiceEntry", (), {"entry_id": "resolved-entry"})()
    calls = {}

    monkeypatch.setattr(fanxiu, "_get_service_user_device_or_404", lambda session, entry_id: service_entry)

    def fake_set_guard_item_enabled(**kwargs):
        calls.update(kwargs)
        return {
            "ok": True,
            "running": False,
            "guard_enabled": kwargs["enabled"],
            "guard_running": kwargs["enabled"],
            "guard_entry_id": kwargs["entry_id"],
            "guard_interval_seconds": kwargs["interval_seconds"],
            "status": "idle",
            "entry_id": kwargs["entry_id"],
            "message": "service guard set",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._runtime_framework, "set_guard_item_enabled", fake_set_guard_item_enabled)

    response = fanxiu.set_fanxiu_data_annotation_runtime_service_guard(
        fanxiu.FanxiuDataAnnotationRuntimeGuardRequest(
            entry_id="request-entry",
            guard_id="wanling_invite",
            enabled=True,
            interval_seconds=7,
        ),
        session=object(),
    )

    assert response.guard_enabled is True
    assert response.guard_entry_id == "resolved-entry"
    assert calls["entry"] is service_entry
    assert calls["entry_id"] == "resolved-entry"
    assert calls["guard_id"] == "wanling_invite"
    assert calls["enabled"] is True
    assert calls["interval_seconds"] == 7
    assert calls["asset_tree_path"] == tmp_path / "resolved-entry.json"
    assert calls["runtime_state_path"] == tmp_path / "runtime_state.json"
    assert calls["world_facts_path"] == tmp_path / "world_facts.json"


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


def test_data_annotation_service_guard_group_endpoint_uses_service_entry_and_shared_paths(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    service_entry = type("ServiceEntry", (), {"entry_id": "resolved-group-entry"})()
    calls = {}

    monkeypatch.setattr(fanxiu, "_get_service_user_device_or_404", lambda session, entry_id: service_entry)

    def fake_set_guard_group_enabled(**kwargs):
        calls.update(kwargs)
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
            "message": "service guard group set",
            "guard_items": {"close_popups": {"id": "close_popups", "enabled": False}},
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._runtime_framework, "set_guard_group_enabled", fake_set_guard_group_enabled)

    response = fanxiu.set_fanxiu_data_annotation_runtime_service_guard_group(
        fanxiu.FanxiuDataAnnotationRuntimeGuardGroupRequest(entry_id="request-group-entry", enabled=False),
        session=object(),
    )

    assert response.guard_group_enabled is False
    assert response.guard_entry_id == "resolved-group-entry"
    assert calls["entry"] is service_entry
    assert calls["entry_id"] == "resolved-group-entry"
    assert calls["enabled"] is False
    assert calls["asset_tree_path"] == tmp_path / "resolved-group-entry.json"
    assert calls["runtime_state_path"] == tmp_path / "runtime_state.json"
    assert calls["world_facts_path"] == tmp_path / "world_facts.json"


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
    assert status["kernel"]["execution_state"] in {"idle", "busy", "dead"}
    assert status["status"] == "stopped"
    assert status["message"] == "执行进程已重载，先前业务任务已结束"
    assert any(item["message"] == "旧日志" for item in status["logs"])
    assert persisted["running"] is False
    assert persisted["guard_enabled"] is True












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
    assert runner._execute_runtime_task(ctx, "legacy_daily_task", {"legacy_name": "日常_灵泉"}, stop_event) == "unsupported"
    assert runner._execute_runtime_task(ctx, "legacy_dynamic_task", {"legacy_name": "日常_首领"}, stop_event) == "unsupported"
    with pytest.raises(RuntimeError, match="暂不支持"):
        runner._execute_runtime_task(ctx, "daily_locate", {}, stop_event)

    assert ("align_settings",) in runner.calls
    assert ("go_scene", 69) in runner.calls
    assert ("hide_floating_window",) in runner.calls
    assert ("gift_code_redeem", ("a", "b")) in runner.calls
    assert any(call == ("log", "skip", "旧版任务「日常_灵泉」尚未迁移，已跳过") for call in runner.calls)
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


def test_popup_guard_closes_world_guide_bubble_by_ocr():
    runner = create_fanxiu_runtime_runner()
    clicks: list[tuple[float, float]] = []

    class FakeRuntime:
        def cur_frame(self, **_kwargs):
            return "not-a-data-url"

        def ocr_lines(self, _frame):
            return [
                {"text": "衣装阁内有时装可升级，速", "x": 70, "y": 1280, "w": 360, "h": 42},
                {"text": "去升级吧！", "x": 118, "y": 1325, "w": 145, "h": 34},
            ]

        def ocr_text(self, _frame):
            return "衣装阁内有时装可升级，速去升级吧！"

        def click_frame_point(self, _image, x, y):
            clicks.append((x, y))

    handled = runner._handle_auto_close_popup_47_world_guide_bubble(
        FakeRuntime(),
        {
            "time": 1.0,
            "kind": "popup",
            "image": "#47",
            "title": "所有提示窗口",
            "folder_path": "弹窗",
            "score": 95.0,
            "action": "",
        },
    )

    assert handled is True
    assert clicks == [(250.0, 1319.5)]
    assert runner.status()["last_guard_event"]["action"] == "click:世界引导气泡"


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


def test_xianfu_scene_jump_wait_allows_185_cutscene_before_home(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "entry": object(),
        "images": {
            185: {
                "title": "灵祖挑战过场",
                "shapes": [{"title": "跳过", "isSceneIdentity": True}],
            }
        },
    }
    shape = {"title": "仙府", "sceneJumpTarget": "171"}
    edge = {"shape": shape, "target_ids": [171]}
    calls = []
    loop = {"count": 0}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_index_images", lambda tree: {})
    monkeypatch.setattr(runner, "_write_asset_tree", lambda *args: calls.append("write"))
    monkeypatch.setattr(runner, "_increment_scene_jump_target", lambda *args: calls.append("increment") or True)
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(("log", args)))
    monkeypatch.setattr(runner, "_find_scene_route", lambda *_args, **_kwargs: [{"source_id": 185, "target_id": 171}])

    def fake_identify(_ctx, _frame, preferred_scene_ids=None):
        if preferred_scene_ids == [171]:
            loop["count"] += 1
            return (None, 0.0) if loop["count"] == 1 else (171, 100.0)
        return 185, 100.0

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=34,
        target_scene_id=171,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 171
    assert "increment" in calls
    assert any("仙府过场" in str(item) for item in calls)


def test_xianfu_scene_jump_wait_does_not_fail_source_stall_at_eight_seconds(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    shape = {"title": "仙府", "sceneJumpTarget": "171"}
    edge = {"shape": shape, "target_ids": [171]}
    calls = []
    now = {"value": 100.0}
    loops = {"count": 0}

    monkeypatch.setattr(runtime_runner_core.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_index_images", lambda tree: {})
    monkeypatch.setattr(runner, "_write_asset_tree", lambda *args: calls.append("write"))
    monkeypatch.setattr(runner, "_increment_scene_jump_target", lambda *args: calls.append("increment") or True)
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(("log", args)))

    def fake_identify(_ctx, _frame, preferred_scene_ids=None):
        if preferred_scene_ids == [171]:
            return (171, 100.0) if loops["count"] >= 3 else (None, 0.0)
        loops["count"] += 1
        if loops["count"] >= 3:
            return 171, 100.0
        return 34, 100.0

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=34,
        target_scene_id=171,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    now["value"] += 12.0
    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    now["value"] += 3.0
    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    now["value"] += 1.0
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 171
    assert "increment" in calls


def test_xianfu_scene_jump_allows_recoverable_xianyuan_list_landing(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    shape = {"title": "仙府", "sceneJumpTarget": "171"}
    edge = {"shape": shape, "target_ids": [171]}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None: (None, 0.0) if preferred_scene_ids else (197, 100.0))
    monkeypatch.setattr(runner, "_find_scene_route", lambda _tree, start, target: [{"source_id": start, "target_id": target}] if (start, target) == (197, 171) else None)
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(args))

    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=34,
        target_scene_id=171,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 197
    assert any("实际到达 #197" in str(item) for item in calls)


def test_xianfu_scene_jump_returns_cutscene_for_replanning_even_when_edge_targets_world(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "entry": object(),
        "images": {
            185: {
                "title": "灵祖挑战过场",
                "shapes": [{"title": "跳过", "isSceneIdentity": True}],
            }
        },
    }
    shape = {"title": "返回", "sceneJumpTarget": "34(3),69(1)"}
    edge = {"shape": shape, "target_ids": [34, 69]}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None: (None, 0.0) if preferred_scene_ids else (185, 100.0))
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(args))

    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=197,
        target_scene_id=171,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 185
    assert any("仙府过场" in str(item) for item in calls)


def test_xianfu_cutscene_skip_accepts_home_even_if_shape_targets_world(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    shape = {"title": "跳过", "sceneJumpTarget": "34(2),24"}
    edge = {"shape": shape, "target_ids": [34, 24]}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None: (None, 0.0) if preferred_scene_ids else (171, 100.0))
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(args))

    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=185,
        target_scene_id=171,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 171
    assert any("仙府过场跳过后到达主页" in str(item) for item in calls)


def test_scene_jump_records_new_route_landing_and_replans(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    shape = {"title": "普通入口", "sceneJumpTarget": "171"}
    edge = {"shape": shape, "target_ids": [171]}

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_write_asset_tree", lambda *_args: None)
    monkeypatch.setattr(runner, "_identify_scene_number", lambda _ctx, _frame, preferred_scene_ids=None: (None, 0.0) if preferred_scene_ids else (197, 100.0))
    monkeypatch.setattr(runner, "_find_scene_route", lambda _tree, start, target: [{"source_id": start, "target_id": target}] if (start, target) == (197, 171) else None)

    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=34,
        target_scene_id=171,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
        layer0_wait_seconds=0.0,
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 197
    assert shape["sceneJumpTarget"] == "197(1),171"
    assert "observedLanding" not in shape


def test_scene_jump_wait_allows_dynamic_minus_one_landing_to_replan(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    ctx = {"entry": object(), "images": {}}
    shape = {"title": "确认", "sceneJumpTarget": "-1"}
    edge = {"shape": shape, "target_ids": [69]}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_clear_tick_frame", lambda _ctx: None)
    monkeypatch.setattr(runner, "_index_images", lambda tree: {})
    monkeypatch.setattr(runner, "_write_asset_tree", lambda *args: calls.append("write"))
    monkeypatch.setattr(runner, "_increment_scene_jump_target", lambda *args: calls.append("increment") or True)
    monkeypatch.setattr(runner, "_log", lambda *args, **kwargs: calls.append(("log", args)))

    def fake_identify(_ctx, _frame, preferred_scene_ids=None):
        if preferred_scene_ids:
            return None, 0.0
        return 34, 100.0

    monkeypatch.setattr(runner, "_identify_scene_number", fake_identify)
    monkeypatch.setattr(runner, "_find_scene_route", lambda _tree, source, target: [{"source_id": source, "target_id": target}] if (source, target) == (34, 69) else None)

    iterator = runner._wait_scene_jump_result(
        ctx,
        tmp_path / "entry.json",
        [],
        source_scene_id=86,
        target_scene_id=69,
        edge=edge,
        stop_event=fanxiu.threading.Event(),
    )

    assert next(iterator) == fanxiu.BehaviorTreeStatus.RUNNING
    with pytest.raises(StopIteration) as exc_info:
        next(iterator)

    assert exc_info.value.value == 34
    assert "increment" not in calls
    assert "write" not in calls
    assert any("动态落点" in str(item) for item in calls)


def test_go_scene_uses_global_current_scene_before_route_candidates(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    shape = {"title": "回到世界", "sceneJumpTarget": "34"}
    image20 = {"type": "image", "id": "20", "title": "绿瓶", "filename": "0020.png", "layer": 2, "shapes": [shape]}
    image34 = {"type": "image", "id": "34", "title": "世界", "filename": "0034.png", "layer": 2, "shapes": []}
    ctx = {"entry": object(), "asset_tree": [image20, image34], "images": {20: image20, 34: image34}}
    calls = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (20, 100.0))
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_for_route",
        lambda *_args, **_kwargs: calls.append("route-candidate") or (34, 100.0),
    )
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


def test_go_scene_next_edge_prefers_shorter_route_over_high_history_count():
    runner = create_fanxiu_runtime_runner()
    direct_shape = {"title": "进入绿瓶", "sceneJumpTarget": "20"}
    noisy_shape = {"title": "日常", "sceneJumpTarget": "69(451)"}
    exit_shape = {"title": "退出", "sceneJumpTarget": "34(52)"}
    image34 = {"type": "image", "title": "世界", "filename": "0034.png", "layer": 1, "shapes": [noisy_shape, direct_shape]}
    image69 = {"type": "image", "title": "日常", "filename": "0069.png", "layer": 1, "shapes": [exit_shape]}
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    tree = [image34, image69, image20]

    decision = runner._select_scene_next_edge(tree, 34, 20)

    assert decision is not None
    assert decision["edge"]["shape"]["title"] == "进入绿瓶"


def test_go_scene_next_edge_rejects_immediate_backtracking_route():
    runner = create_fanxiu_runtime_runner()
    daily_shape = {"title": "日常", "sceneJumpTarget": "69(451)"}
    exit_shape = {"title": "退出", "sceneJumpTarget": "34(52)"}
    image34 = {"type": "image", "title": "世界", "filename": "0034.png", "layer": 1, "shapes": [daily_shape]}
    image69 = {"type": "image", "title": "日常", "filename": "0069.png", "layer": 1, "shapes": [exit_shape]}
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    tree = [image34, image69, image20]

    assert runner._select_scene_next_edge(tree, 69, 20) is None


def test_go_scene_uses_routed_scene_layer3_child_as_parent_navigation(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    entry_shape = {"title": "进入绿瓶", "sceneJumpTarget": "20"}
    image68 = {"type": "image", "id": "68", "title": "世界-下方动态", "filename": "0068.png", "layer": 3, "shapes": []}
    image34 = {
        "type": "image",
        "id": "34",
        "title": "世界",
        "filename": "0034.png",
        "layer": 1,
        "shapes": [entry_shape],
        "children": [image68],
    }
    image20 = {"type": "image", "id": "20", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    tree = [image34, image20]
    ctx = {"entry": object(), "asset_tree": tree, "images": {34: image34, 68: image68, 20: image20}}
    clicks: list[str] = []

    assert 68 in runner._scene_route_candidate_ids(tree, 20)

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (None, 0.0))
    monkeypatch.setattr(runner, "_strong_ocr_scene_number", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_identify_scene_number_for_route", lambda *_args, **_kwargs: (68, 86.7))
    monkeypatch.setattr(runner, "_click_scene_route_shape", lambda _ctx, _image, shape, _frame: clicks.append(str(shape.get("title") or "")))

    def wait_scene_jump_result(*_args, **_kwargs):
        if False:
            yield fanxiu.BehaviorTreeStatus.RUNNING
        return 20

    monkeypatch.setattr(runner, "_wait_scene_jump_result", wait_scene_jump_result)

    result = _drain_generator(runner._go_scene_task(ctx, tmp_path / "entry.json", 20, fanxiu.threading.Event()))

    assert result == "success"
    assert clicks == ["进入绿瓶"]


def test_go_scene_missing_route_reports_clear_annotation_gap(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    image34 = {
        "type": "image",
        "title": "世界",
        "filename": "0034.png",
        "layer": 1,
        "shapes": [{"title": "进入绿瓶", "sceneJumpTarget": ""}],
    }
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    ctx = {"entry": object(), "asset_tree": [image34, image20], "images": {34: image34, 20: image20}}
    clicks: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (34, 100.0))
    monkeypatch.setattr(runner, "_click_scene_route_shape", lambda _ctx, _image, shape, _frame: clicks.append(str(shape.get("title") or "")))

    gen = runner._go_scene_task(ctx, tmp_path / "entry.json", 20, fanxiu.threading.Event())
    with pytest.raises(RuntimeError, match=r"go_scene\(20\) 失败：无法从当前#34找到可达#20的路径，请检查标注shape。"):
        _drain_generator(gen)

    assert clicks == []

def test_go_scene_prefers_recorded_target_over_unlinked_target_named_shape():
    runner = create_fanxiu_runtime_runner()
    unknown_shape = {"title": "进入绿瓶", "sceneJumpTarget": ""}
    recorded_shape = {"title": "小绿瓶入口", "sceneJumpTarget": "20(3)"}
    image34 = {"type": "image", "title": "世界", "filename": "0034.png", "layer": 1, "shapes": [unknown_shape, recorded_shape]}
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    tree = [image34, image20]

    decision = runner._select_scene_next_edge(tree, 34, 20)

    assert decision is not None
    assert decision["edge"]["shape"]["title"] == "小绿瓶入口"


def test_go_scene_route_rejects_business_action_as_navigation():
    runner = create_fanxiu_runtime_runner()
    xianfu_shape = {"title": "仙府", "sceneJumpTarget": "171(20)"}
    reward_shape = {"title": "领取", "sceneJumpTarget": "20"}
    image34 = {"type": "image", "title": "世界", "filename": "0034.png", "layer": 1, "shapes": [xianfu_shape]}
    image171 = {"type": "image", "title": "仙府", "filename": "0171.png", "layer": 1, "shapes": [reward_shape]}
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    tree = [image34, image171, image20]

    assert runner._select_scene_next_edge(tree, 34, 20) is None


def test_go_scene_does_not_use_unranked_fallback_route(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    xianfu_shape = {"title": "仙府", "sceneJumpTarget": "171"}
    leave_shape = {"title": "离开", "sceneJumpTarget": "34"}
    image34 = {"type": "image", "title": "世界", "filename": "0034.png", "layer": 1, "shapes": [xianfu_shape]}
    image171 = {"type": "image", "title": "仙府", "filename": "0171.png", "layer": 1, "shapes": [leave_shape]}
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    ctx = {"entry": object(), "asset_tree": [image34, image171, image20], "images": {34: image34, 171: image171, 20: image20}}
    clicks: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (34, 100.0))
    monkeypatch.setattr(runner, "_click_scene_route_shape", lambda _ctx, _image, shape, _frame: clicks.append(str(shape.get("title") or "")))

    gen = runner._go_scene_task(ctx, tmp_path / "entry.json", 20, fanxiu.threading.Event())
    with pytest.raises(RuntimeError, match=r"go_scene\(20\) 失败：无法从当前#34找到可达#20的路径，请检查标注shape。"):
        _drain_generator(gen)

    assert clicks == []


def test_go_scene_missing_route_does_not_synthesize_leave_confirm_edge(monkeypatch, tmp_path):
    runner = create_fanxiu_runtime_runner()
    leave_shape = {"title": "离开", "sceneJumpTarget": ""}
    image171 = {"type": "image", "title": "仙府", "filename": "0171.png", "layer": 1, "shapes": [leave_shape]}
    image20 = {"type": "image", "title": "绿瓶", "filename": "0020.png", "layer": 1, "shapes": []}
    ctx = {"entry": object(), "asset_tree": [image171, image20], "images": {171: image171, 20: image20}}
    clicks: list[str] = []

    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "frame")
    monkeypatch.setattr(runner, "_identify_scene_number", lambda *_args, **_kwargs: (171, 100.0))
    monkeypatch.setattr(runner, "_click_scene_route_shape", lambda _ctx, _image, shape, _frame: clicks.append(str(shape.get("title") or "")))

    gen = runner._go_scene_task(ctx, tmp_path / "entry.json", 20, fanxiu.threading.Event())
    with pytest.raises(RuntimeError, match=r"go_scene\(20\) 失败：无法从当前#171找到可达#20的路径，请检查标注shape。"):
        _drain_generator(gen)

    assert clicks == ["离开"]


def test_ensure_clean_world_after_task_exits_green_bottle(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[str] = []

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        def current_scene(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return 20, 91.0, "green-bottle-frame"
            return 34, 90.0, "world-frame"

        def ocr_text(self, _frame):
            return "角色 装备 功法书 修为"

    def fake_leave(_ctx, _stop_event, *, label):
        calls.append(label)
        if False:
            yield None
        return "success"

    def fake_close_stack(_ctx, _runtime, _stop_event, *, label, max_attempts=15):
        calls.append(f"close:{label}:{max_attempts}")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_leave_green_bottle_to_world", fake_leave)
    monkeypatch.setattr(runner, "_close_world_reward_tip_stack_if_present", fake_close_stack)

    result = _drain_generator(
        runner._ensure_clean_world_after_task(
            {"images": {}, "asset_tree_path": Path("entry.json")},
            fanxiu.threading.Event(),
            label="邮件_选择性领取",
        )
    )

    assert result == 34
    assert calls == ["邮件_选择性领取", "close:邮件_选择性领取:15"]


def test_ensure_clean_world_after_task_confirms_leave_scene(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []
    frames = iter(["confirm-frame", "world-frame", "world-frame-2"])

    class FakeRuntime:
        def current_scene(self, scene_ids, **kwargs):
            frame = next(frames)
            actions.append(("current_scene", tuple(scene_ids), kwargs, frame))
            if frame == "confirm-frame":
                return 86, 100.0, frame
            return 34, 100.0, frame

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "confirm-frame":
                return "提示 是否离开当前场景 取消 确认"
            return "世界 储物袋 角色 装备 功法书"

        def wait_click(self, view_id, shape):
            actions.append(("wait_click", view_id, shape))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

    def fake_close_stack(_ctx, _runtime, _stop_event, *, label, max_attempts=15):
        actions.append(("close", label, max_attempts))
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_close_world_reward_tip_stack_if_present", fake_close_stack)

    result = _drain_generator(
        runner._ensure_clean_world_after_task(
            {"images": {}, "asset_tree_path": Path("entry.json")},
            fanxiu.threading.Event(),
            label="仙府_寻访仙侣",
        )
    )

    assert result == 34
    assert actions == [
        ("current_scene", (275, 237, 204, 69, 289, 86, 58, 20, 34), {"update": True}, "confirm-frame"),
        ("ocr_text", "confirm-frame"),
        ("wait_click", 86, "确认"),
        ("settle", 2.0),
        ("current_scene", (58, 20, 34), {"update": True}, "world-frame"),
        ("ocr_text", "world-frame"),
        ("close", "仙府_寻访仙侣", 15),
        ("current_scene", (34,), {"update": True}, "world-frame-2"),
        ("ocr_text", "world-frame-2"),
    ]


def test_ensure_clean_world_after_task_exits_daily_assistant_overview(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.scene = 204

        def current_scene(self, scene_ids, **kwargs):
            actions.append(("current_scene", tuple(scene_ids), kwargs, self.scene))
            if self.scene == 204:
                return 204, 100.0, "assistant-list"
            return 34, 100.0, "world-frame"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "assistant-list":
                return "游历副本 查看 灵兽 万灵 查看 仙窍试炼 一键执行"
            return "世界 储物袋 角色 装备 功法书"

        def wait_click(self, view_id, shape):
            actions.append(("wait_click", view_id, shape))
            if view_id == 204 and shape == "返回":
                self.scene = 69
            if view_id == 69 and shape == "退出":
                self.scene = 34
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs, self.scene))
            if False:
                yield None
            return self.scene

        def cur_frame(self, update=False):
            actions.append(("cur_frame", update, self.scene))
            return "world-frame" if self.scene == 34 else "assistant-list"

    def fake_close_stack(_ctx, _runtime, _stop_event, *, label, max_attempts=15):
        actions.append(("close", label, max_attempts))
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_close_world_reward_tip_stack_if_present", fake_close_stack)

    result = _drain_generator(
        runner._ensure_clean_world_after_task(
            {"images": {}, "asset_tree_path": Path("entry.json")},
            fanxiu.threading.Event(),
            label="邮件_选择性领取",
        )
    )

    assert result == 34
    assert ("wait_click", 204, "返回") in actions
    assert ("wait_click", 69, "退出") in actions
    assert ("close", "邮件_选择性领取", 15) in actions


def test_ensure_clean_world_after_task_hides_floating_window(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    calls: list[str] = []

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        def current_scene(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return 58, 92.0, "floating-frame"
            return 34, 90.0, "world-frame"

        def ocr_text(self, _frame):
            return "角色 装备 功法书 修为"

    def fake_close_stack(_ctx, _runtime, _stop_event, *, label, max_attempts=15):
        calls.append(f"close:{label}:{max_attempts}")
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_execute_hide_floating_window", lambda *_args, **_kwargs: calls.append("hide-floating"))
    monkeypatch.setattr(runner, "_close_world_reward_tip_stack_if_present", fake_close_stack)

    result = _drain_generator(
        runner._ensure_clean_world_after_task(
            {"images": {}, "asset_tree_path": Path("entry.json")},
            fanxiu.threading.Event(),
            label="日常_收尾",
        )
    )

    assert result == 34
    assert calls == ["hide-floating", "close:日常_收尾:15"]


def test_world_reward_tip_stack_closes_multiple_cards(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    image34 = {"id": 34, "title": "世界", "width": 900, "height": 1600}
    ctx = {"images": {34: image34}}
    clicks: list[tuple[float, float]] = []
    settles: list[float] = []

    class FakeRuntime:
        def __init__(self):
            self.frames = iter(["frame-1", "frame-2", "frame-3"])

        def cur_frame(self, update=False):
            return next(self.frames)

        def ocr_text(self, frame):
            return "合欢灵玉 点击查看" if frame != "frame-3" else "角色 装备 功法书"

        def click_frame_point(self, _image, x, y):
            clicks.append((round(float(x), 1), round(float(y), 1)))

        def wait_action_settle(self, seconds):
            settles.append(float(seconds))
            if False:
                yield None

    _drain_generator(
        runner._close_world_reward_tip_stack_if_present(
            ctx,
            FakeRuntime(),
            fanxiu.threading.Event(),
            label="日常_收尾",
            max_attempts=5,
        )
    )

    assert clicks == [(690.3, 968.0), (690.3, 968.0)]
    assert settles == [0.6, 0.6]


def test_action_trace_creates_temp_directory(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    trace_dir = tmp_path / "missing" / "trace"
    image = {"id": 121, "title": "邮件", "width": 900, "height": 1600}
    action = {"kind": "click", "point": [100, 200], "label": "click test"}
    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    )

    monkeypatch.setattr(runner, "_action_trace_dir", lambda: trace_dir)
    monkeypatch.setattr(runner, "_capture_frame", lambda _ctx: runner._data_url(png_data))
    monkeypatch.setattr(runner, "_annotate_action_trace_png", lambda data, _action: data)
    monkeypatch.setattr(runner, "_prune_action_trace_files", lambda *_args, **_kwargs: None)

    runner._save_action_trace({}, image, action)

    assert trace_dir.exists()
    assert list(trace_dir.glob("*_before.png"))
    assert (trace_dir / "index.jsonl").exists()


def test_mail_selective_claim_detail_timeout_still_runs_delete_read_cleanup(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_fanxiu_runtime_runner()
    image34 = {"id": 34, "title": "世界", "width": 900, "height": 1600, "shapes": []}
    image121 = {
        "id": 121,
        "title": "邮件",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "邮件清单2", "x": 0.05, "y": 0.10, "w": 0.90, "h": 0.70},
            {"title": "一键删除", "x": 0.10, "y": 0.90, "w": 0.22, "h": 0.06},
        ],
    }
    image278 = {"id": 278, "title": "一键删除确认", "width": 900, "height": 1600, "shapes": [{"title": "确认"}]}
    ctx = {"asset_tree_path": tmp_path / "asset-tree.json", "images": {34: image34, 121: image121, 278: image278}}
    view121 = runtime_runner_core.View(image121)
    title_shape = runtime_runner_core.Shape(
        {"title": "虚天殿冰火路奖励", "x": 0.10, "y": 0.20, "w": 0.40, "h": 0.05},
        parent_view=view121,
    )
    row = runtime_runner_core._RuntimeMailRow(
        raw={"title": "虚天殿冰火路奖励", "time_text": "2026年07月03日 05:00", "status": "未阅", "policy": "delete"},
        title_shape=title_shape,
    )
    clicks: list[str] = []

    class FakeRuntime:
        def __init__(self):
            self.attrs = {}
            self.wait_views = [runtime_runner_core.View(image278), runtime_runner_core.View(image34)]

        def cur_frame(self, update=False):
            return "frame"

        def click_shape(self, view, shape, **_kwargs):
            clicks.append(str(shape.title) if isinstance(shape, runtime_runner_core.Shape) else str(shape))

        def wait_view(self, *_views, **_kwargs):
            return_value = self.wait_views.pop(0)
            if False:
                yield None
            return return_value

        def scroll_shape_content(self, _shape, **_kwargs):
            if False:
                yield None
            return False

        def observe_scroll_content(self, *_args, **_kwargs):
            return True

    fake_runtime = FakeRuntime()

    monkeypatch.setattr(runtime_runner_core, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_tasks, "ensure_fanxiu_capture_runtime_backstop", lambda _reason: {"ensured": True, "status": {"state": "running"}})
    monkeypatch.setattr(runner, "_wait_mail_capture_runtime_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_open_mail_selective_claim_entry", lambda _runtime: (_ for _ in ()))
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: (121, 100.0, "frame", "邮件 一键删除"))
    monkeypatch.setattr(runner, "_refresh_recent_mail_packets_for_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_align_mail_records_from_visible_adjacency", lambda *_args, **_kwargs: {"updated": 0})
    monkeypatch.setattr(runner, "_prepare_mail_row_policy", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", lambda *_args, **_kwargs: [row])
    monkeypatch.setattr(runner, "_mark_pending_packet_mail_actions_not_visible", lambda **_kwargs: 0)

    def no_green_bottle(*_args, **_kwargs):
        if False:
            yield None
        return False

    def clean_world(*_args, **_kwargs):
        if False:
            yield None
        return 34

    def no_tip_stack(*_args, **_kwargs):
        if False:
            yield None
        return None

    def claim_timeout(*_args, **_kwargs):
        if False:
            yield None
        raise TimeoutError("详情超时")

    monkeypatch.setattr(runner, "_leave_green_bottle_to_world_if_present", no_green_bottle)
    monkeypatch.setattr(runner, "_ensure_clean_world_after_task", clean_world)
    monkeypatch.setattr(runner, "_close_mail_world_reward_tip_stack_if_present", no_tip_stack)
    monkeypatch.setattr(runner, "_claim_runtime_mail_row", claim_timeout)

    result = _drain_generator(runner._execute_mail_selective_claim_task(ctx, fanxiu.threading.Event(), {"max_scrolls": 1}))

    assert result == "success"
    assert clicks == ["一键删除", "确认"]


def test_mail_selective_claim_read_mail_probes_detail_delete_before_bulk_cleanup(tmp_path, monkeypatch):
    from backend.core.fanxiu.data_annotation.tasks import mail as mail_tasks

    runner = create_fanxiu_runtime_runner()
    image34 = {"id": 34, "title": "世界", "width": 900, "height": 1600, "shapes": []}
    image121 = {
        "id": 121,
        "title": "邮件",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "邮件清单2", "x": 0.05, "y": 0.10, "w": 0.90, "h": 0.70},
            {"title": "一键删除", "x": 0.10, "y": 0.90, "w": 0.22, "h": 0.06},
        ],
    }
    image278 = {"id": 278, "title": "一键删除确认", "width": 900, "height": 1600, "shapes": [{"title": "确认"}]}
    ctx = {"asset_tree_path": tmp_path / "asset-tree.json", "images": {34: image34, 121: image121, 278: image278}}
    view121 = runtime_runner_core.View(image121)
    title_shape = runtime_runner_core.Shape(
        {"title": "灵祖挑战个人奖励补发", "x": 0.10, "y": 0.20, "w": 0.40, "h": 0.05},
        parent_view=view121,
    )
    read_row = runtime_runner_core._RuntimeMailRow(
        raw={"title": "灵祖挑战个人奖励补发", "time_text": "2026年07月03日 05:00", "status": "已阅", "x": 220, "y": 260},
        title_shape=title_shape,
    )
    rows_by_call = [[read_row], []]
    clicks: list[str] = []
    probed_titles: list[str] = []

    class FakeRuntime:
        def __init__(self):
            self.attrs = {}
            self.wait_views = [runtime_runner_core.View(image278), runtime_runner_core.View(image34)]

        def cur_frame(self, update=False):
            return "frame"

        def click_shape(self, view, shape, **_kwargs):
            clicks.append(str(shape.title) if isinstance(shape, runtime_runner_core.Shape) else str(shape))

        def wait_view(self, *_views, **_kwargs):
            return_value = self.wait_views.pop(0)
            if False:
                yield None
            return return_value

        def scroll_shape_content(self, _shape, **_kwargs):
            if False:
                yield None
            return False

        def observe_scroll_content(self, *_args, **_kwargs):
            return True

    fake_runtime = FakeRuntime()

    monkeypatch.setattr(runtime_runner_core, "ensure_fanxiu_mail_table", lambda: None)
    monkeypatch.setattr(mail_tasks, "ensure_fanxiu_capture_runtime_backstop", lambda _reason: {"ensured": True, "status": {"state": "running"}})
    monkeypatch.setattr(runner, "_wait_mail_capture_runtime_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    monkeypatch.setattr(runner, "_open_mail_selective_claim_entry", lambda _runtime: (_ for _ in ()))
    monkeypatch.setattr(runner, "_fanxiu_runtime_scene_text", lambda *_args, **_kwargs: (121, 100.0, "frame", "邮件 一键删除"))
    monkeypatch.setattr(runner, "_refresh_recent_mail_packets_for_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_align_mail_records_from_visible_adjacency", lambda *_args, **_kwargs: {"updated": 0})
    monkeypatch.setattr(runner, "_mark_pending_packet_mail_actions_not_visible", lambda **_kwargs: 0)

    def fake_rows(*_args, **_kwargs):
        return rows_by_call.pop(0)

    def fake_probe(_ctx, _stop_event, _image121, row):
        probed_titles.append(str(row.get("title") or ""))
        if False:
            yield None
        return "processed"

    def no_green_bottle(*_args, **_kwargs):
        if False:
            yield None
        return False

    def clean_world(*_args, **_kwargs):
        if False:
            yield None
        return 34

    def no_tip_stack(*_args, **_kwargs):
        if False:
            yield None
        return None

    monkeypatch.setattr(runner, "_runtime_mail_rows_from_frame", fake_rows)
    monkeypatch.setattr(runner, "_probe_and_maybe_delete_mail_row", fake_probe)
    monkeypatch.setattr(runner, "_leave_green_bottle_to_world_if_present", no_green_bottle)
    monkeypatch.setattr(runner, "_ensure_clean_world_after_task", clean_world)
    monkeypatch.setattr(runner, "_close_mail_world_reward_tip_stack_if_present", no_tip_stack)

    result = _drain_generator(runner._execute_mail_selective_claim_task(ctx, fanxiu.threading.Event(), {"max_scrolls": 1}))

    assert result == "success"
    assert probed_titles == ["灵祖挑战个人奖励补发"]
    assert clicks == ["一键删除", "确认"]


def test_data_annotation_identify_scene_number_uses_graph_preferred_candidates(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {"images": {34: {"title": "#34"}, 66: {"title": "#66"}}}
    calls = []

    def identify(recog_ctx, frame_data_url, preferred_scene_ids=None, trace=None):
        legacy_filter_present = any(str(key).endswith("scope_filter") for key in recog_ctx)
        calls.append((frame_data_url, tuple(preferred_scene_ids or ()), legacy_filter_present))
        return 34, 90.0, "matched"

    monkeypatch.setattr(runner, "_identify_scene_number_by_graph", identify)

    assert runner._identify_scene_number(ctx, "frame", [66, 34]) == (34, 90.0)
    assert calls == [("frame", (66, 34), False)]


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


def test_data_annotation_mark_scheduler_task_ignores_expired_runtime_next_time(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 6, 2, 12, 10, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core.time, "time", lambda: fixed_now.timestamp())
    fanxiu._write_data_annotation_world_facts({
        "discoveries": {
            "task": {
                "legacy-daily-assistant": {
                    "id": "legacy-daily-assistant",
                    "task_type": "daily_assistant",
                    "discovered_next_time": "2026-06-02 12:00:00",
                    "updated_at": fixed_now.timestamp() + 1,
                }
            }
        }
    })
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "legacy-daily-assistant",
        "task_type": "daily_assistant",
        "schedule_kind": "daily",
        "schedule_times": ["00:00", "06:00", "12:00", "18:00"],
        "next_time": "2026-06-02 12:00:00",
        "last_result": "running",
        "last_run_at": "2026-06-02 12:08:00",
        "retry_after": None,
    }

    runner._mark_scheduler_task([task], "legacy-daily-assistant", "success")

    assert task["last_result"] == "success"
    assert task["next_time"] == "2026-06-02 18:00:00"
    assert task["retry_after"] is None


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
        "id": "legacy-daily-xianshi",
        "task_type": "daily_xianshi",
        "label": "日常_仙市",
        "schedule_kind": "daily",
        "schedule_times": ["00:00", "05:00"],
        "next_time": "2026-06-02 05:00:00",
        "last_result": "running",
        "retry_after": None,
        "cooldown_seconds": 600,
    }

    runner._mark_scheduler_task([task], "legacy-daily-xianshi", "skipped")

    assert task["last_result"] == "skipped"
    assert task["last_run_at"] == "2026-06-02 06:00:00"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-06-02 06:10:00"


def test_generic_runtime_task_does_not_mutate_external_scheduler_state(tmp_path, monkeypatch):
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
        "id": "legacy-daily-xianshi",
        "task_type": "daily_xianshi",
        "label": "日常_仙市",
        "schedule_kind": "daily",
        "schedule_times": ["00:00", "05:00"],
        "next_time": "2026-06-02 05:00:00",
        "last_result": "queued",
        "retry_after": None,
        "cooldown_seconds": 600,
        "enabled": True,
    }
    fanxiu._write_data_annotation_scheduler_tasks([task])
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(
        runner,
        "_run_runtime_behavior_tree",
        lambda **_kwargs: {"result": "skipped", "message": "日常_仙市：稍后重试"},
    )

    runner._run_generic_runtime_task(
        entry=object(),
        entry_id="entry",
        task_type="daily_xianshi",
        payload={"__scheduler_task_id": "legacy-daily-xianshi"},
        asset_tree_path=tmp_path / "entry.json",
        stop_event=threading.Event(),
    )

    updated = next(item for item in fanxiu._read_data_annotation_scheduler_tasks() if item["id"] == "legacy-daily-xianshi")
    assert updated["last_result"] == "queued"
    assert updated.get("last_run_at") is None
    assert updated["next_time"] == "2026-06-02 05:00:00"
    assert updated["retry_after"] is None


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


def test_daily_weekly_dungeon_326_zero_remaining_marks_week_complete(tmp_path, monkeypatch):
    _patch_data_annotation_api_common(monkeypatch, tmp_path)
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    actions: list[tuple] = []

    class FakeRuntime:
        def current_scene(self, view_ids=None, update=False):
            actions.append(("current_scene", tuple(view_ids or ()), update))
            return 326, 100.0, "frame"

        def ocr_text(self, frame=None, update=False):
            actions.append(("ocr_text", frame, update))
            return "玉霄天宫 本周剩余奖励次数：0/3 挑战"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def wait_click_then_view(self, *args, **kwargs):
            actions.append(("wait_click_then_view", args, kwargs))
            raise AssertionError("周本次数为 0 时不应继续点击挑战")

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_daily_weekly_dungeon_next_time_text", lambda _payload: "2026-07-13 05:00:00")

    result = _drain_generator(
        runner._execute_daily_weekly_dungeon_task(
            {"asset_tree_path": asset_tree},
            threading.Event(),
            {"__scheduler_task_id": "daily-weekly-dungeon", "tiangong_challenge_pre_click_wait": 0},
        )
    )

    fact = fanxiu._read_data_annotation_world_facts()["discoveries"]["task"]["daily-weekly-dungeon"]
    assert result == "success"
    assert fact["last_result"] == "success"
    assert fact["discovered_next_time"] == "2026-07-13 05:00:00"
    assert fact["discovered_retry_after"] is None
    assert not any(action[0] == "wait_click_then_view" for action in actions)


def test_data_annotation_mark_weekly_dungeon_error_keeps_retry(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 7, 9, 1, 39, 3)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    runner = create_fanxiu_runtime_runner()
    task = {
        "id": "daily-weekly-dungeon",
        "task_type": "daily_weekly_dungeon",
        "label": "日常_周本",
        "schedule_kind": "weekly",
        "weekdays": [0],
        "schedule_times": ["05:00"],
        "next_time": "2026-07-06 05:00:00",
        "last_result": "running",
        "retry_after": None,
        "cooldown_seconds": 600,
    }

    runner._mark_scheduler_task([task], "daily-weekly-dungeon", "error")

    assert task["last_result"] == "error"
    assert task["last_run_at"] == "2026-07-09 01:39:03"
    assert task["next_time"] is None
    assert task["retry_after"] == "2026-07-09 01:49:03"


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


def test_data_annotation_clear_runtime_tasks_retry_after_five_minutes(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_scheduler_state_path", lambda: path)
    monkeypatch.setattr(runtime_runner_core, "_data_annotation_world_facts_path", lambda: tmp_path / "world_facts.json")
    fixed_now = datetime(2026, 7, 4, 21, 40, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    monkeypatch.setattr(runtime_runner_core, "datetime", FixedDatetime)
    runner = create_fanxiu_runtime_runner()
    tasks = [
        item
        for item in fanxiu._default_data_annotation_scheduler_tasks()
        if item["id"] in {"legacy-daily-dongtian-clear", "legacy-daily-lingmai-clear"}
    ]

    runner._mark_scheduler_task(tasks, "legacy-daily-dongtian-clear", "error")
    runner._mark_scheduler_task(tasks, "legacy-daily-lingmai-clear", "stopped")

    by_id = {item["id"]: item for item in tasks}
    assert by_id["legacy-daily-dongtian-clear"]["retry_after"] == "2026-07-04 21:45:00"
    assert by_id["legacy-daily-lingmai-clear"]["retry_after"] == "2026-07-04 21:45:00"


def test_scheduler_failure_cleanup_accepts_reliably_identified_world_after_undeclared_landing(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()

    def failed_route(*args, **kwargs):
        raise RuntimeError("实际到达 #34，但返回 shape 未声明该落点")

    class RuntimeProbe:
        @staticmethod
        def current_scene(preferred, update=False):
            assert preferred == [34]
            assert update is True
            return 34, 100.0, "frame"

    monkeypatch.setattr(runner, "_run_direct_runtime_action", failed_route)
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *args, **kwargs: RuntimeProbe())

    cleaned = runner._cleanup_failed_scheduler_task_to_world(
        ctx={},
        asset_tree_path=tmp_path / "asset-tree.json",
        task_label="仙府_领悟绝技",
    )

    assert cleaned is True
    assert runner.status()["current_scene"] == 34
    assert any("目标锚点已可靠确认" in item["message"] for item in runner.status()["logs"])




















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


def test_daily_assistant_entry_prefers_bottom_assistant_tab():
    runner = create_fanxiu_runtime_runner()
    image69 = {
        "id": 69,
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "滚动窗口", "x": 0.08, "y": 0.25, "w": 0.85, "h": 0.55},
        ],
    }
    lines = [
        {"text": "仙府小助手任务", "x": 300, "y": 500, "w": 260, "h": 42},
        {"text": "活动报名小助手奖励找回新", "x": 160, "y": 1410, "w": 420, "h": 40},
    ]

    matches = runner._daily_assistant_entry_matches(lines, image69)

    assert matches
    x, y, text = matches[0]
    assert "活动报名" in text
    assert 900 * 0.33 <= x <= 900 * 0.40
    assert y > 1600 * 0.78


def test_daily_assistant_new_overview_text_is_list():
    runner = create_fanxiu_runtime_runner()

    assert runner._daily_assistant_text_is_list("小助手 游历 灵兽 万灵 试炼 仙府 宗门 一键执行")


def test_daily_assistant_one_key_waits_progress_then_closes_result(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    image204 = {
        "id": 204,
        "title": "小助手总览",
        "width": 900,
        "height": 1600,
        "shapes": [
            {"title": "一键执行", "x": 0.25, "y": 0.83, "w": 0.5, "h": 0.08},
            {"title": "返回", "x": 0.04, "y": 0.93, "w": 0.1, "h": 0.04},
        ],
    }
    image276 = {"id": 276, "title": "小助手消耗确认", "shapes": [{"title": "是", "x": 0.62, "y": 0.64, "w": 0.1, "h": 0.04}]}
    image277 = {"id": 277, "title": "小助手执行进度", "shapes": [{"title": "进度", "x": 0.52, "y": 0.51, "w": 0.22, "h": 0.05}]}
    image275 = {"id": 275, "title": "小助手执行结果", "shapes": [{"title": "退出", "x": 0.36, "y": 0.91, "w": 0.28, "h": 0.06}]}
    ctx = {"asset_tree_path": asset_tree, "images": {204: image204, 275: image275, 276: image276, 277: image277}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.scene_calls = 0
            self.result_closed = False

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def click_shape_center(self, image, shape_title):
            actions.append(("click_shape_center", image.get("id"), shape_title))
            if image.get("id") == 275 and shape_title == "退出":
                self.result_closed = True

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

        def current_scene(self, view_ids=None, **kwargs):
            self.scene_calls += 1
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            if self.scene_calls == 1:
                return 276, 98.0, "confirm"
            if self.scene_calls == 2:
                return 277, 100.0, "progress"
            if self.result_closed:
                return 204, 100.0, "list"
            return 275, 100.0, "result"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "confirm":
                return "本次执行预计消耗140灵石 是否继续执行 是 否"
            if frame == "progress":
                return "小助手执行中 剩余时间 0007"
            if frame == "list":
                return "小助手 游历 灵兽 一键执行"
            return "神物园自动收取 本次获得的道具 退出"

    def wait_list(*_args, **_kwargs):
        actions.append(("wait_list",))
        if False:
            yield None
        return 204, 100.0

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_assistant_list_state", wait_list)

    result = _drain_generator(runner._run_daily_assistant_from_list(ctx, fanxiu.threading.Event(), {"assistant_return_after_items": True}))

    assert result == "success"
    assert ("wait_click", 204, "一键执行", {}) in actions
    assert ("wait_click", 276, "是", {}) in actions
    assert ("settle", 7.0) in actions
    assert ("click_shape_center", 275, "退出") in actions
    assert ("wait_list",) in actions
    assert ("click_shape_center", 204, "返回") in actions
    assert any(action[0] == "wait_click" and action[1:3] == (69, "退出") for action in actions)
    assert any(action[0] == "wait_view" and 34 in action[1] for action in actions)


def test_daily_assistant_one_key_closes_result_before_list_success(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    image204 = {
        "id": 204,
        "title": "小助手总览",
        "width": 900,
        "height": 1600,
        "shapes": [{"title": "一键执行", "x": 0.25, "y": 0.83, "w": 0.5, "h": 0.08}],
    }
    image276 = {"id": 276, "title": "小助手消耗确认", "shapes": [{"title": "是", "x": 0.62, "y": 0.64, "w": 0.1, "h": 0.04}]}
    image277 = {"id": 277, "title": "小助手执行进度", "shapes": [{"title": "进度", "x": 0.52, "y": 0.51, "w": 0.22, "h": 0.05}]}
    image275 = {"id": 275, "title": "小助手执行结果", "shapes": [{"title": "退出", "x": 0.36, "y": 0.91, "w": 0.28, "h": 0.06}]}
    ctx = {"asset_tree_path": asset_tree, "images": {204: image204, 275: image275, 276: image276, 277: image277}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.scene_calls = 0

        def wait_click(self, view_id, shape, **kwargs):
            actions.append(("wait_click", view_id, shape, kwargs))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None

        def click_shape_center(self, image, shape_title):
            actions.append(("click_shape_center", image.get("id"), shape_title))

        def current_scene(self, view_ids=None, **kwargs):
            self.scene_calls += 1
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            if self.scene_calls == 1:
                return 276, 100.0, "confirm"
            if self.scene_calls == 2:
                return 277, 100.0, "progress"
            return 204, 99.0, "result-overlay"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "confirm":
                return "本次执行预计消耗140灵石 是否继续执行 是 否"
            if frame == "progress":
                return "小助手执行中 剩余时间 0033"
            return "小助手 游历 灵兽 一键执行 神物园自动收取 本次获得的道具 退出"

    def wait_list(*_args, **_kwargs):
        actions.append(("wait_list",))
        if False:
            yield None
        return 204, 100.0

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_wait_daily_assistant_list_state", wait_list)

    result = _drain_generator(runner._run_daily_assistant_from_list(ctx, fanxiu.threading.Event(), {}))

    assert result == "success"
    assert ("settle", 10.0) in actions
    assert ("click_shape_center", 275, "退出") in actions
    assert ("wait_list",) in actions


def test_daily_assistant_ensure_list_waits_transition_before_reopen(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": asset_tree, "images": {}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            self.calls += 1
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            if self.calls == 1:
                return None, 0.0, "transition"
            return 204, 100.0, "list"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "" if frame == "transition" else "小助手 道义秘库助手"

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._ensure_daily_assistant_list_state(
            ctx,
            fanxiu.threading.Event(),
            {"assistant_list_state_initial_settle_seconds": 0.001, "assistant_list_state_poll_seconds": 0.001},
            timeout=1.0,
            label="测试等待清单",
        )
    )

    assert result == (204, 100.0)
    assert [action[0] for action in actions].count("current_scene") == 2


def test_daily_assistant_ensure_list_reclicks_result_exit(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    image275 = {"id": 275, "title": "小助手执行结果", "shapes": [{"title": "退出", "x": 0.36, "y": 0.91, "w": 0.28, "h": 0.06}]}
    ctx = {"asset_tree_path": asset_tree, "images": {275: image275}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            self.calls += 1
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            if self.calls == 1:
                return None, 97.0, "result-text"
            return 204, 100.0, "list"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "result-text":
                return "宗门任务收益今日已完成 宗门祈福收益今日已完成 宗门俸禄收益今日已完成 宗门请安收益今日已完成 宗门游历 退出"
            return "小助手 游历 灵兽 一键执行"

        def click_shape_center(self, image, shape_title):
            actions.append(("click_shape_center", image.get("id"), shape_title))
            if image.get("id") == 275 and shape_title == "退出":
                self.result_closed = True
            if image.get("id") == 275 and shape_title == "退出":
                self.result_closed = True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._ensure_daily_assistant_list_state(
            ctx,
            fanxiu.threading.Event(),
            {
                "assistant_list_state_initial_settle_seconds": 0.001,
                "assistant_list_state_poll_seconds": 0.001,
                "assistant_result_reclick_settle_seconds": 0.001,
            },
            timeout=1.0,
            label="测试等待清单",
        )
    )

    assert result == (204, 100.0)
    assert ("click_shape_center", 275, "退出") in actions


def test_daily_assistant_ensure_list_clicks_ocr_exit_when_scene_unknown(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": asset_tree, "images": {}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.closed = False

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, self.closed))
            if self.closed:
                return 204, 100.0, "list"
            return None, 89.0, "result-text"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "result-text":
                return "宗门任务收益今日已完成 宗门祈福收益今日已完成 宗门俸禄收益今日已完成 宗门资源 退出"
            return "小助手 游历 灵兽 一键执行"

        def ocr_lines(self, frame):
            actions.append(("ocr_lines", frame))
            return [{"text": "退出", "x": 320, "y": 1420, "w": 90, "h": 44}]

        def click_frame_point(self, scene_id, x, y):
            actions.append(("click_frame_point", scene_id, x, y))
            self.closed = True

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())

    result = _drain_generator(
        runner._ensure_daily_assistant_list_state(
            ctx,
            fanxiu.threading.Event(),
            {
                "assistant_list_state_initial_settle_seconds": 0.001,
                "assistant_list_state_poll_seconds": 0.001,
                "assistant_result_reclick_settle_seconds": 0.001,
            },
            timeout=1.0,
            label="测试等待清单",
        )
    )

    assert result == (204, 100.0)
    assert ("click_frame_point", 275, 365.0, 1442.0) in actions


def test_daily_assistant_return_accepts_daily_page_after_result_close(tmp_path):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": asset_tree, "images": {}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.closed = False

        def current_scene(self, view_ids=None, **kwargs):
            actions.append(("current_scene", tuple(view_ids or ()), kwargs, self.closed))
            if self.closed:
                return 69, 98.0, "daily"
            return None, 89.0, "result"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            if frame == "result":
                return "宗门任务收益今日已完成 宗门祈福收益今日已完成 宗门资源 退出"
            return "日常 活动报名 小助手 奖励找回 日常周常"

        def ocr_lines(self, frame):
            actions.append(("ocr_lines", frame))
            return [{"text": "退出", "x": 320, "y": 1420, "w": 90, "h": 44}]

        def click_frame_point(self, scene_id, x, y):
            actions.append(("click_frame_point", scene_id, x, y))
            self.closed = True

        def wait_action_settle(self, seconds):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

        def wait_click(self, view_id, shape):
            actions.append(("wait_click", view_id, shape))
            if False:
                yield None
            return "success"

        def wait_view(self, *view_ids, **kwargs):
            actions.append(("wait_view", view_ids, kwargs))
            if False:
                yield None
            return view_ids[0]

    fake_runtime = FakeRuntime()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: fake_runtime)
    try:
        _drain_generator(
            runner._return_after_daily_assistant_one_key(
                ctx,
                fanxiu.threading.Event(),
            {
                "assistant_one_key_result_close_timeout": 1.0,
                "assistant_result_reclick_settle_seconds": 0.001,
            },
            fake_runtime,
            current_scene=204,
            )
        )
    finally:
        monkeypatch.undo()

    assert ("click_frame_point", 275, 365.0, 1442.0) in actions
    assert ("wait_click", 69, "退出") in actions


def test_daily_assistant_ensure_list_closes_youli_result(tmp_path, monkeypatch):
    runner = create_fanxiu_runtime_runner()
    asset_tree = tmp_path / "asset_tree.json"
    asset_tree.write_text("[]", encoding="utf-8")
    ctx = {"asset_tree_path": asset_tree, "images": {}}
    actions: list[tuple] = []

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **kwargs):
            self.calls += 1
            actions.append(("current_scene", tuple(view_ids or ()), kwargs))
            if self.calls == 1:
                return 237, 100.0, "youli-result"
            return 204, 100.0, "list"

        def ocr_text(self, frame):
            actions.append(("ocr_text", frame))
            return "本次获得的道具 退出" if frame == "youli-result" else "小助手 游历 灵兽 一键执行"

    def close_youli(runtime, payload):
        actions.append(("close_youli", payload))
        if False:
            yield None

    monkeypatch.setattr(runner, "_fanxiu_runtime", lambda *_args, **_kwargs: FakeRuntime())
    monkeypatch.setattr(runner, "_daily_assistant_close_youli_result", close_youli)

    result = _drain_generator(
        runner._ensure_daily_assistant_list_state(
            ctx,
            fanxiu.threading.Event(),
            {"assistant_list_state_initial_settle_seconds": 0.001, "assistant_list_state_poll_seconds": 0.001},
            timeout=1.0,
            label="测试等待清单",
        )
    )

    assert result == (204, 100.0)
    assert any(action[0] == "close_youli" for action in actions)


def test_daily_lingmai_clicks_slot_entry_after_region_teleport(monkeypatch):
    runner = create_fanxiu_runtime_runner()
    ctx = {
        "images": {
            286: {"shapes": [{"title": "选择空位"}, {"title": "占领"}, {"title": "返回"}]},
            287: {"shapes": [{"title": "前往灵脉"}, {"title": "确认"}]},
            288: {"shapes": [{"title": "占领"}]},
        }
    }
    actions = []

    class FakeRuntime:
        def shape_score(self, *_args, **_kwargs):
            return 100.0

        def wait_click(self, view_id, shape, **_kwargs):
            actions.append(("wait_click", view_id, shape))
            if False:
                yield None
            return "success"

        def wait_action_settle(self, seconds=1.0):
            actions.append(("settle", seconds))
            if False:
                yield None
            return "success"

        def current_scene(self, view_ids=None, **_kwargs):
            candidates = tuple(view_ids or ())
            actions.append(("current_scene", candidates))
            if 287 in candidates:
                return 287, 100.0, "confirm-frame"
            if 85 in candidates:
                return 85, 100.0, "region-frame"
            return 288, 100.0, "occupy-frame"

        def ocr_text(self, frame):
            return {
                "confirm-frame": "前往灵脉 取消 确认",
                "region-frame": "仙煌神脉 剩余空位 1/10 聚灵位 离开 探索",
                "occupy-frame": "占领",
            }[frame]

        def wait_view(self, *view_ids, **_kwargs):
            actions.append(("wait_view", view_ids))
            if False:
                yield None
            return 288

    def click_slot_entry(_runtime, frame, *, task_label):
        actions.append(("click_slot_entry", frame, task_label))

    def continue_from_final(*_args, **_kwargs):
        if False:
            yield None
        return "success"

    monkeypatch.setattr(runner, "_click_daily_lingmai_slot_entry", click_slot_entry)
    monkeypatch.setattr(runner, "_continue_daily_lingmai_from_final_occupy", continue_from_final)

    result = _drain_generator(
        runner._continue_daily_lingmai_from_select_slot(
            ctx,
            threading.Event(),
            {},
            FakeRuntime(),
            "select-frame",
            task_label="灵脉_清体力",
        )
    )

    assert result == "success"
    assert ("click_slot_entry", "region-frame", "灵脉_清体力") in actions
    assert ("wait_view", (288,)) in actions

