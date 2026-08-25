from __future__ import annotations

import sys
import inspect
from copy import deepcopy
import hashlib
import json

import pytest

from scripts import fanxiu_bt


def _file_fingerprint(path):
    payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest(), path.stat().st_mtime_ns, len(payload)


def test_doctor_report_keeps_scheduler_runtime_and_world_state_byte_exact(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation import behavior_tree_control

    scheduler_path = tmp_path / "scheduler_tasks.json"
    runtime_path = tmp_path / "runtime_state.json"
    world_facts_path = tmp_path / "world_facts.json"
    scheduler_path.write_text('[{"id":"legacy"}]', encoding="utf-8")
    runtime_path.write_text('{"status":"success"}', encoding="utf-8")
    world_facts_path.write_text('{"seed":1}', encoding="utf-8")
    paths = (scheduler_path, runtime_path, world_facts_path)
    before = tuple(_file_fingerprint(path) for path in paths)

    monkeypatch.setattr(
        behavior_tree_control,
        "consolidate_arena_scheduler_instances",
        lambda raw: (raw, True),
    )

    def fake_repair(raw, _defaults, facts, **_kwargs):
        facts["derived"] = True
        return raw, True

    monkeypatch.setattr(behavior_tree_control, "repair_data_annotation_scheduler_tasks", fake_repair)
    monkeypatch.setattr(behavior_tree_control, "default_data_annotation_scheduler_tasks", lambda: [])
    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_settings",
        lambda **_kwargs: {"job_group_enabled": True, "time_sequence": {}},
    )
    monkeypatch.setattr(behavior_tree_control, "scheduler_tasks_for_dispatch", lambda tasks, **_kwargs: tasks)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "build_data_annotation_scheduler_plan",
        lambda *_args, **_kwargs: {"next_action": "idle", "message": "idle"},
    )
    monkeypatch.setattr(fanxiu_bt, "fanxiu_kernel_manager_status", lambda: {"entry_id": "entry"})
    monkeypatch.setattr(
        fanxiu_bt,
        "fanxiu_behavior_tree_runtime_status",
        lambda: json.loads(runtime_path.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda _entry_id: object())
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda _entry_id: None)
    monkeypatch.setattr(
        fanxiu_bt,
        "build_scheduler_plan",
        lambda **_kwargs: behavior_tree_control.build_scheduler_plan(
            scheduler_state_path=scheduler_path,
            world_facts_path=world_facts_path,
            include_blocking_overlays=False,
        ),
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "read_scheduler_tasks",
        lambda: behavior_tree_control.read_scheduler_tasks(
            scheduler_state_path=scheduler_path,
            world_facts_path=world_facts_path,
        ),
    )
    monkeypatch.setattr(fanxiu_bt, "_doctor_relevant_logs", lambda _limit: [])
    monkeypatch.setattr(fanxiu_bt, "_build_maintenance_summary", lambda _report: {})

    report = fanxiu_bt._build_doctor_report(log_limit=1, include_screenshot=False)

    assert report["scheduler"]["next_action"] == "idle"
    assert tuple(_file_fingerprint(path) for path in paths) == before


def test_background_doctor_watch_does_not_capture_screenshots_by_default():
    from backend.core.fanxiu.data_annotation.behavior_tree_control import ensure_doctor_watch_background

    assert inspect.signature(ensure_doctor_watch_background).parameters["include_screenshot"].default is False


def test_ensure_watch_doctor_has_no_second_dispatch_mode(monkeypatch, capsys):
    captured: dict = {}
    monkeypatch.setattr(sys, "argv", ["fanxiu_bt.py", "ensure-watch-doctor"])
    monkeypatch.setattr(
        fanxiu_bt,
        "_ensure_doctor_watch_background",
        lambda **kwargs: captured.update(kwargs) or {"started": False},
    )

    assert fanxiu_bt.main() == 0
    assert "auto_run_due" not in captured
    assert '"started": false' in capsys.readouterr().out.lower()


@pytest.mark.parametrize(
    ("kernel_state", "runtime_running"),
    [("busy", False), ("idle", True)],
)
def test_ensure_watch_doctor_defers_code_replacement_while_dispatch_is_active(
    monkeypatch,
    kernel_state,
    runtime_running,
):
    terminated: list[dict] = []
    monkeypatch.setattr(
        fanxiu_bt,
        "_read_doctor_watch_heartbeat",
        lambda: {
            "pid": 123,
            "age_seconds": 1.0,
            "runtime_consistent": True,
            "code_signature": "old",
        },
    )
    monkeypatch.setattr(fanxiu_bt, "doctor_watch_code_signature", lambda: "new")
    monkeypatch.setattr(
        fanxiu_bt,
        "fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": kernel_state},
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "fanxiu_behavior_tree_runtime_status",
        lambda: {"running": runtime_running, "current_task_id": "task", "phase": "scheduler_task"},
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "_terminate_stale_doctor_watch",
        lambda heartbeat: terminated.append(heartbeat) or {"terminated": True},
    )

    result = fanxiu_bt._ensure_doctor_watch_background(
        interval_seconds=60.0,
        duration_seconds=0.0,
        log_limit=80,
        include_screenshot=False,
        screenshot_every=10,
        stale_after_seconds=180.0,
    )

    assert result["started"] is False
    assert result["reason"] == "replacement_deferred_active_runtime"
    assert terminated == []


def test_doctor_report_defers_expensive_blocking_overlay_check_to_real_dispatch(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        fanxiu_bt,
        "fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle", "entry_id": "entry"},
    )
    monkeypatch.setattr(fanxiu_bt, "fanxiu_behavior_tree_runtime_status", lambda: {})
    monkeypatch.setattr(fanxiu_bt, "resolve_fanxiu_entry", lambda _entry_id: object())
    monkeypatch.setattr(fanxiu_bt, "data_annotation_asset_tree_path", lambda _entry_id: None)
    monkeypatch.setattr(
        fanxiu_bt,
        "build_scheduler_plan",
        lambda **kwargs: captured.update(kwargs) or {
            "next_action": "idle",
            "message": "idle",
            "job_group_enabled": True,
        },
    )
    monkeypatch.setattr(fanxiu_bt, "read_scheduler_tasks", lambda: [])
    monkeypatch.setattr(fanxiu_bt, "_doctor_relevant_logs", lambda _limit: [])
    monkeypatch.setattr(fanxiu_bt, "_build_maintenance_summary", lambda _report: {})

    fanxiu_bt._build_doctor_report(log_limit=1, include_screenshot=False)

    assert captured["include_blocking_overlays"] is False


def test_external_patrol_only_requires_engineering_ownership():
    report = _report()
    report["scheduler"]["due_tasks"] = []
    report["scheduler"]["job_group_enabled"] = True
    report["scheduler"]["next_action"] = "idle"
    assert fanxiu_bt._watch_should_run_game_state_inspection(report) is True

    report["kernel"]["execution_state"] = "busy"
    assert fanxiu_bt._watch_should_run_game_state_inspection(report) is True
    report["scheduler"]["due_tasks"] = [{"id": "due-job"}]
    assert fanxiu_bt._watch_should_run_game_state_inspection(report) is True
    report["scheduler"]["job_group_enabled"] = False
    assert fanxiu_bt._watch_should_run_game_state_inspection(report) is False


def test_external_patrol_calls_runtime_probe_without_kernel_cell_or_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.game_state_inspection.inspect_game_state_once",
        lambda **kwargs: calls.append(kwargs) or {
            "status": "running",
            "due_task_ids": ["daily-redpacket"],
        },
    )

    result = fanxiu_bt._watch_run_game_state_inspection(_report())

    assert result["due_task_ids"] == ["daily-redpacket"]
    assert calls == [{"asynchronous_recovery": True}]


def test_game_state_inspection_is_not_a_kernel_task_cell():
    from backend.core.fanxiu.data_annotation.default_jobs import (
        _DEFAULT_RUNTIME_JOB_TYPES,
    )

    assert "game_state_inspection" not in _DEFAULT_RUNTIME_JOB_TYPES


@pytest.mark.parametrize(
    "argv",
    [
        ["fanxiu_bt.py", "watch-doctor", "--auto-run-due"],
        ["fanxiu_bt.py", "watch-doctor", "--auto-run-due-min-interval-seconds", "300"],
        ["fanxiu_bt.py", "ensure-watch-doctor", "--auto-run-due"],
        ["fanxiu_bt.py", "ensure-watch-doctor", "--no-auto-run-due"],
    ],
)
def test_scheduler_cli_rejects_removed_second_mode_switches(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit, match="2"):
        fanxiu_bt.main()


def _report(*task_ids: str) -> dict:
    return {
        "kernel": {"entry_id": "entry", "execution_state": "idle"},
        "runtime": {"status": "idle"},
        "maintenance": {"severity": "attention", "automation_safe": True},
        "scheduler": {
            "next_action": "run_due",
            "due_tasks": [{"id": task_id, "next_time": "2026-07-24 05:00:00"} for task_id in task_ids],
        },
    }


def test_watch_due_batch_does_not_let_failed_first_task_starve_later_tasks(monkeypatch):
    reports = iter((_report("mail", "boss"), _report("mail", "boss"), _report("mail")))
    dispatched: list[str] = []

    def fake_auto_run(_report, *, exclude_task_ids, **_kwargs):
        selected = next(task_id for task_id in ("mail", "boss") if task_id not in exclude_task_ids)
        dispatched.append(selected)
        return {
            "triggered": True,
            "status": "error" if selected == "mail" else "success",
            "dispatched_task_id": selected,
        }

    monkeypatch.setattr(fanxiu_bt, "_watch_auto_run_due", fake_auto_run)
    monkeypatch.setattr(
        fanxiu_bt,
        "_watch_wait_for_failure_cleanup",
        lambda report, **_kwargs: report,
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "_build_doctor_report",
        lambda **_kwargs: deepcopy(next(reports)),
    )

    report = fanxiu_bt._watch_auto_run_due_batch(
        _report("mail", "boss"),
        log_limit=20,
        include_screenshot=False,
        take_screenshot=False,
    )

    assert dispatched == ["mail", "boss"]
    assert report["auto_run_due"]["run_count"] == 2
    assert report["auto_run_due_batch_exhausted"] == ["boss", "mail"]


def test_watch_waits_for_transient_failure_cleanup_without_starting_a_new_batch(monkeypatch):
    cleanup = _report("mail", "boss")
    cleanup["kernel"]["execution_state"] = "idle"
    cleanup["runtime"] = {"status": "running", "phase": "scheduler_failure_cleanup"}
    cleanup["scheduler"]["due_tasks"][0].update({
        "last_result": "running",
        "attempt_id": "attempt-mail",
    })
    ready = _report("mail", "boss")
    reports = iter((cleanup, ready))
    monkeypatch.setattr(
        fanxiu_bt,
        "_build_doctor_report",
        lambda **_kwargs: deepcopy(next(reports)),
    )
    monkeypatch.setattr(fanxiu_bt.time, "sleep", lambda _seconds: None)

    result = fanxiu_bt._watch_wait_for_failure_cleanup(
        cleanup,
        log_limit=20,
        take_screenshot=False,
        timeout_seconds=30,
    )

    assert result["runtime"]["status"] == "idle"
    assert fanxiu_bt._watch_should_auto_run_due(result) is True


def test_watch_does_not_wait_after_scheduler_has_recorded_terminal_failure(monkeypatch):
    terminal = _report("level-one")
    terminal["kernel"]["execution_state"] = "idle"
    terminal["runtime"] = {"status": "running", "phase": "scheduler_failure_cleanup"}
    terminal["scheduler"]["due_tasks"][0].update({
        "last_result": "error",
        "attempt_id": None,
    })
    monkeypatch.setattr(
        fanxiu_bt,
        "_build_doctor_report",
        lambda **_kwargs: pytest.fail("终态失败已完成 Cell 收尾，不应再等待或重读"),
    )

    result = fanxiu_bt._watch_wait_for_failure_cleanup(
        terminal,
        log_limit=20,
        take_screenshot=False,
        timeout_seconds=30,
    )

    assert result is terminal


def test_plain_business_error_does_not_become_annotation_blocker():
    report = {
        "runtime": {
            "status": "error",
            "message": "邮件_选择性领取：分红发放点击后未完成可靠的领取闭环",
        },
        "scheduler": {
            "next_action": "run_due",
            "due_tasks": [{"id": "mail", "label": "邮件_选择性领取"}],
            "scheduled_tasks": [
                {
                    "id": "mail",
                    "label": "邮件_选择性领取",
                    "last_result": "error",
                    "next_time": "2026-08-07 01:13:11",
                }
            ],
        },
    }

    maintenance = fanxiu_bt._build_maintenance_summary(report)

    assert maintenance["blocked_due_count"] == 0
    assert maintenance["needs_human_annotation"] is False
    assert maintenance["automation_safe"] is True


def test_explicit_missing_annotation_error_blocks_only_its_job():
    report = {
        "runtime": {
            "status": "error",
            "message": "邮件_选择性领取：缺少可靠标注，请人工补标/修标",
        },
        "scheduler": {
            "next_action": "run_due",
            "due_tasks": [{"id": "mail", "label": "邮件_选择性领取"}],
            "scheduled_tasks": [
                {
                    "id": "mail",
                    "label": "邮件_选择性领取",
                    "last_result": "error",
                    "next_time": "2026-08-07 01:13:11",
                }
            ],
        },
    }

    maintenance = fanxiu_bt._build_maintenance_summary(report)

    assert maintenance["blocked_due_ids"] == ["mail"]
    assert maintenance["needs_human_annotation"] is True
    assert maintenance["automation_safe"] is False


def test_stale_failure_cleanup_does_not_block_when_kernel_is_idle():
    report = _report("mail", "boss")
    report["kernel"]["execution_state"] = "idle"
    report["runtime"] = {"status": "running", "phase": "scheduler_failure_cleanup"}

    assert fanxiu_bt._watch_should_auto_run_due(report) is True


def test_stale_scheduler_runtime_does_not_block_when_kernel_is_idle():
    report = _report("mail", "boss")
    report["kernel"]["execution_state"] = "idle"
    report["runtime"] = {"status": "running", "phase": "scheduler_task"}
    report["scheduler"]["scheduled_tasks"] = [
        {"id": "redpacket", "last_result": "success", "attempt_id": None},
    ]

    assert fanxiu_bt._watch_should_auto_run_due(report) is True


def test_real_running_attempt_still_blocks_when_kernel_is_idle():
    report = _report("mail", "boss")
    report["kernel"]["execution_state"] = "idle"
    report["runtime"] = {"status": "running", "phase": "scheduler_task"}
    report["scheduler"]["scheduled_tasks"] = [
        {"id": "redpacket", "last_result": "running", "attempt_id": "attempt-redpacket"},
    ]

    assert fanxiu_bt._watch_should_auto_run_due(report) is False


def test_watch_due_batch_immediately_retries_failed_zero_delay_job(monkeypatch):
    initial = _report("daily-lingquan")
    initial["scheduler"]["due_tasks"][0].update({
        "last_result": "error",
        "last_run_at": "2026-07-24 20:30:00",
        "finished_at": "2026-07-24 20:30:05",
        "dispatch_level": 1,
        "window": ["20:30", "20:43"],
    })
    first_retry = _report("daily-lingquan")
    first_retry["scheduler"]["due_tasks"][0].update({
        "last_result": "error",
        "last_run_at": "2026-07-24 20:30:10",
        "finished_at": "2026-07-24 20:30:20",
        "dispatch_level": 1,
        "window": ["20:30", "20:43"],
    })
    completed = _report()
    completed["scheduler"]["next_action"] = "idle"
    reports = iter((first_retry, completed))
    dispatched: list[set[str]] = []

    def fake_auto_run(_report, *, exclude_task_ids, **_kwargs):
        dispatched.append(set(exclude_task_ids))
        return {
            "triggered": True,
            "status": "error" if len(dispatched) == 1 else "success",
            "dispatched_task_id": "daily-lingquan",
        }

    monkeypatch.setattr(fanxiu_bt, "_watch_auto_run_due", fake_auto_run)
    monkeypatch.setattr(
        fanxiu_bt,
        "_watch_wait_for_failure_cleanup",
        lambda report, **_kwargs: report,
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "_build_doctor_report",
        lambda **_kwargs: deepcopy(next(reports)),
    )

    report = fanxiu_bt._watch_auto_run_due_batch(
        initial,
        log_limit=20,
        include_screenshot=False,
        take_screenshot=False,
    )

    assert dispatched == [set(), set()]
    assert report["auto_run_due"]["run_count"] == 2


def test_watch_due_batch_failed_immediate_retry_does_not_starve_later_job(monkeypatch):
    initial = _report("daily-mozu", "boss")
    for item in initial["scheduler"]["due_tasks"]:
        if item["id"] == "daily-mozu":
            item.update({
                "last_result": "error",
                "dispatch_level": 1,
                "next_time": "2026-07-24 20:30:00",
            })
        else:
            item["next_time"] = "2026-07-24 20:31:00"
    after_retry = deepcopy(initial)
    next(item for item in after_retry["scheduler"]["due_tasks"] if item["id"] == "daily-mozu")[
        "next_time"
    ] = "2026-07-24 20:32:00"
    completed = _report()
    completed["scheduler"]["next_action"] = "idle"
    reports = [after_retry, completed]
    dispatched: list[str] = []

    def fake_auto_run(_report, *, exclude_task_ids, **_kwargs):
        candidates = [
            item
            for item in _report["scheduler"]["due_tasks"]
            if item["id"] not in exclude_task_ids
        ]
        selected = min(candidates, key=lambda item: item["next_time"])["id"]
        dispatched.append(selected)
        return {
            "triggered": True,
            "status": "error" if selected == "daily-mozu" else "success",
            "dispatched_task_id": selected,
        }

    monkeypatch.setattr(fanxiu_bt, "_watch_auto_run_due", fake_auto_run)
    monkeypatch.setattr(
        fanxiu_bt,
        "_watch_wait_for_failure_cleanup",
        lambda report, **_kwargs: report,
    )
    monkeypatch.setattr(
        fanxiu_bt,
        "_build_doctor_report",
        lambda **_kwargs: deepcopy(reports.pop(0)),
    )

    report = fanxiu_bt._watch_auto_run_due_batch(
        initial,
        log_limit=20,
        include_screenshot=False,
        take_screenshot=False,
    )

    assert dispatched == ["daily-mozu", "boss"]
    assert report["auto_run_due"]["run_count"] == 2
