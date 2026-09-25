from __future__ import annotations

from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation import kernel_scheduler_control
from backend.core.fanxiu.data_annotation.maintenance import (
    LOGIN_MAINTENANCE_PROMPT_SCENE_ID,
    infer_game_startup_scene,
    MAINTENANCE_RECOVERY_TASK_ID,
    clear_maintenance_gate,
    maintenance_check_time_text,
    maintenance_gate_blocks_task,
    open_maintenance_gate,
    read_maintenance_gate,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import default_kernel_scheduler_tasks


def test_maintenance_wake_times_follow_operational_policy():
    assert maintenance_check_time_text(datetime(2026, 7, 23, 16, 0)) == "2026-07-23 16:05:00"
    assert maintenance_check_time_text(datetime(2026, 7, 23, 17, 0)) == "2026-07-23 17:05:00"
    assert maintenance_check_time_text(datetime(2026, 7, 23, 17, 31)) == "2026-07-23 17:35:00"
    assert maintenance_check_time_text(datetime(2026, 7, 23, 18, 30)) == "2026-07-23 18:35:00"
    assert maintenance_check_time_text(datetime(2026, 7, 23, 19, 7)) == "2026-07-23 19:10:00"


def test_maintenance_gate_is_persistent_and_idempotent(tmp_path):
    path = tmp_path / "world_facts.json"

    first = open_maintenance_gate(
        path,
        observed_at=datetime(2026, 7, 23, 16, 20),
        evidence={"ocr": "停更码字中"},
    )
    second = open_maintenance_gate(
        path,
        observed_at=datetime(2026, 7, 23, 16, 25),
        evidence={"source": "scene_415"},
    )

    assert first["active"] is True
    assert second["opened_at"] == first["opened_at"]
    assert second["last_observed_at"] > first["last_observed_at"]
    assert read_maintenance_gate(path)["evidence"] == {"source": "scene_415"}

    cleared = clear_maintenance_gate(
        path,
        resolved_at=datetime(2026, 7, 23, 17, 31),
        evidence={"scene_id": 34},
    )
    assert cleared["active"] is False
    assert cleared["state"] == "available"
    assert read_maintenance_gate(path)["evidence"] == {"scene_id": 34}


def test_maintenance_gate_only_allows_recovery_task():
    gate = {"active": True, "state": "maintenance"}

    assert maintenance_gate_blocks_task(gate, {"id": "daily-boss", "task_type": "daily_boss"})
    assert not maintenance_gate_blocks_task(
        gate,
        {"id": MAINTENANCE_RECOVERY_TASK_ID, "task_type": "maintenance_recovery"},
    )
    assert not maintenance_gate_blocks_task(
        {"active": False},
        {"id": "daily-boss", "task_type": "daily_boss"},
    )


def test_maintenance_recovery_default_has_unbounded_execution_and_startup_budget():
    task = next(
        item
        for item in default_kernel_scheduler_tasks()
        if item["id"] == MAINTENANCE_RECOVERY_TASK_ID
    )

    assert task["payload"]["unbounded_execution"] is True
    assert task["payload"]["startup_timeout_seconds"] == 300
    assert "startup_restart_limit" not in task["payload"]
    assert task["error_retry_delay_seconds"] == 1800


def test_game_startup_ocr_only_infers_stable_pages():
    assert infer_game_startup_scene(
        None,
        "AppVer:2.46.700211 正在初始化资源...76%",
    ) is None
    assert infer_game_startup_scene(
        None,
        "AppVer:2.46.700211 进入游戏 健康游戏忠告",
    ) == 18
    assert infer_game_startup_scene(
        None,
        "停更码字中，敬请期待更新",
    ) == LOGIN_MAINTENANCE_PROMPT_SCENE_ID
    assert infer_game_startup_scene(
        47,
        "停更码字中，敬请期待更新",
    ) == LOGIN_MAINTENANCE_PROMPT_SCENE_ID


def test_scheduler_dispatches_ordinary_due_task_while_maintenance_gate_is_active(monkeypatch, tmp_path):
    ordinary_task = {
        "id": "daily-boss",
        "task_type": "daily_boss",
        "label": "日常_首领",
        "next_time": "2026-07-23 16:00:00",
    }
    world_path = tmp_path / "world_facts.json"
    open_maintenance_gate(world_path, observed_at=datetime(2026, 7, 23, 16, 20))
    persisted = []
    dispatched = []
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(kernel_scheduler_control, "ensure_fanxiu_kernel_scheduler_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(kernel_scheduler_control, "select_due_kernel_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(kernel_scheduler_control, "prepare_kernel_scheduler_for_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )
    monkeypatch.setattr(kernel_scheduler_control, "execution_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "persist_execution_status",
        lambda status, **_kwargs: persisted.append(dict(status)),
        raising=False,
    )

    status = kernel_scheduler_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        world_facts_path=world_path,
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert dispatched == ["daily-boss"]


def test_scheduler_does_not_reorder_due_job_from_startup_gate(monkeypatch, tmp_path):
    ordinary_task = {
        "id": "daily-redpacket",
        "task_type": "daily_redpacket",
        "label": "日常_红包",
        "next_time": "2026-07-25 14:00:00",
    }
    dispatched = []
    scheduled = []
    persisted = []
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(kernel_scheduler_control, "ensure_fanxiu_kernel_scheduler_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(kernel_scheduler_control, "select_due_kernel_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(kernel_scheduler_control, "prepare_kernel_scheduler_for_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )
    monkeypatch.setattr(kernel_scheduler_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "submit_task_cell",
        lambda **_kwargs: pytest.fail("测试应走统一终态执行替身"),
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "schedule_login_job_first",
        lambda **kwargs: scheduled.append(kwargs) or "2026-07-25 14:01:00",
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: pytest.fail("Scheduler 不得读取画面 overlay"),
    )
    monkeypatch.setattr(kernel_scheduler_control, "prepare_kernel_scheduler_for_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "execution_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "persist_execution_status",
        lambda status, **_kwargs: persisted.append(dict(status)),
        raising=False,
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )

    status = kernel_scheduler_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        world_facts_path=tmp_path / "world_facts.json",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert scheduled == []
    assert dispatched == ["daily-redpacket"]


def test_scheduler_rechecks_engineering_run_authority_before_business_submit(monkeypatch, tmp_path):
    ordinary_task = {
        "id": "daily-redpacket",
        "task_type": "daily_redpacket",
        "label": "日常_红包",
        "next_time": "2026-07-25 14:00:00",
    }
    settings_values = iter((
        {"behavior_tree_enabled": True, "job_group_enabled": True},
        {"behavior_tree_enabled": True, "job_group_enabled": False},
    ))
    def read_settings(**_kwargs):
        return next(settings_values, {"behavior_tree_enabled": True, "job_group_enabled": False})

    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_settings", read_settings)
    monkeypatch.setattr(kernel_scheduler_control, "ensure_fanxiu_kernel_scheduler_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(kernel_scheduler_control, "select_due_kernel_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: pytest.fail("Scheduler 不得读取画面 overlay"),
    )
    monkeypatch.setattr(kernel_scheduler_control, "execution_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "persist_execution_status",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "prepare_kernel_scheduler_for_task",
        lambda *_args, **_kwargs: pytest.fail("切到 AI 后不应进入提交准备"),
    )

    status = kernel_scheduler_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        world_facts_path=tmp_path / "world_facts.json",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert status["phase"] == "scheduler_job_group_disabled"
    assert "不再提交" in status["message"]


def test_scheduler_ignores_announcement_and_blocking_overlay_producers(monkeypatch, tmp_path):
    ordinary_task = {
        "id": "daily-redpacket",
        "task_type": "daily_redpacket",
        "label": "日常_红包",
        "next_time": "2026-07-25 14:00:00",
    }
    dispatched = []
    scheduled = []
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(kernel_scheduler_control, "ensure_fanxiu_kernel_scheduler_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(kernel_scheduler_control, "select_due_kernel_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "submit_task_cell",
        lambda **_kwargs: pytest.fail("测试应走统一终态执行替身"),
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "schedule_login_job_first",
        lambda **kwargs: scheduled.append(kwargs) or "2026-07-25 14:01:00",
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Scheduler 不得读取画面 overlay")),
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )
    monkeypatch.setattr(kernel_scheduler_control, "prepare_kernel_scheduler_for_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "execution_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(kernel_scheduler_control, "persist_execution_status", lambda *_args, **_kwargs: None, raising=False)

    status = kernel_scheduler_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        world_facts_path=tmp_path / "world_facts.json",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert scheduled == []
    assert dispatched == ["daily-redpacket"]


def test_scheduler_environment_probe_fails_closed_when_reference_is_unavailable(monkeypatch, tmp_path):
    runner = kernel_scheduler_control.create_behavior_tree_executor()
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(kernel_scheduler_control, "create_behavior_tree_executor", lambda: runner)

    blockers = kernel_scheduler_control.scheduler_blocking_overlays(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "asset-tree.json",
        environment_circuit={
            "scene_id": 74,
            "task_ids": ["daily-boss", "daily-assistant"],
            "incident_ids": ["incident-a", "incident-b"],
        },
    )

    assert blockers[0]["blocking"] is True
    assert blockers[0]["kind"] == "repeated_environment_failure"
    assert "无法证明环境已经恢复" in blockers[0]["message"]


def test_scheduler_plan_never_probes_visual_overlays(monkeypatch, tmp_path):
    ordinary_task = {
        "id": "daily-redpacket",
        "task_type": "daily_redpacket",
        "next_time": "2026-07-25 14:00:00",
    }
    monkeypatch.setattr(
        kernel_scheduler_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("plan 不得读取画面 overlay")),
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "read_scheduler_settings",
        lambda **_kwargs: {"job_group_enabled": True},
    )
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(kernel_scheduler_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "read_world_facts",
        lambda *_args, **_kwargs: {"availability": {"game": {"active": True, "state": "maintenance"}}},
    )
    monkeypatch.setattr(kernel_scheduler_control, "scheduler_tasks_for_dispatch", lambda tasks, **_kwargs: tasks)
    monkeypatch.setattr(kernel_scheduler_control, "behavior_tree_executor_status", lambda: {})
    monkeypatch.setattr(
        kernel_scheduler_control,
        "build_kernel_scheduler_plan",
        lambda *_args, **_kwargs: {"next_action": "run_due", "message": "运行到期作业"},
    )

    plan = kernel_scheduler_control.build_scheduler_plan(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "asset-tree.json",
        include_blocking_overlays=True,
    )

    assert plan["next_action"] == "run_due"
    assert plan["maintenance_gate"]["active"] is True
    assert "blocking_overlays" not in plan


def test_scheduler_environment_incidents_do_not_stop_engineering_dispatch(monkeypatch, tmp_path):
    tasks = [
        {
            "id": "daily-boss",
            "task_type": "daily_boss",
            "label": "日常_首领",
            "next_time": "2026-08-17 05:00:00",
            "last_result": "error",
        },
        {
            "id": "daily-assistant",
            "task_type": "daily_assistant",
            "label": "日常_助手",
            "next_time": "2026-08-17 05:01:00",
            "last_result": "error",
        },
    ]
    original_times = {task["id"]: task["next_time"] for task in tasks}
    dispatched = []
    circuit = {
        "kind": "repeated_environment_failure",
        "scene_id": 74,
        "task_ids": ["daily-assistant", "daily-boss"],
        "incident_ids": ["incident-a", "incident-b"],
    }
    blocker = {
        "kind": "repeated_environment_failure",
        "scene_id": 74,
        "blocking": True,
        "message": "#74 稳定环境仍存在",
    }

    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(kernel_scheduler_control, "ensure_fanxiu_kernel_scheduler_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(kernel_scheduler_control, "read_scheduler_tasks", lambda **_kwargs: tasks)
    monkeypatch.setattr(kernel_scheduler_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(kernel_scheduler_control, "select_due_kernel_scheduler_tasks", lambda _tasks, **_kwargs: list(tasks))
    monkeypatch.setattr(kernel_scheduler_control, "sort_scheduler_tasks_for_dispatch", lambda items: items)
    monkeypatch.setattr(
        kernel_scheduler_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "prepare_kernel_scheduler_for_task",
        lambda *_args, **_kwargs: dispatched.append("prepared"),
    )
    monkeypatch.setattr(
        kernel_scheduler_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **_kwargs: dispatched.append("submitted") or {"status": "success"},
    )

    status = kernel_scheduler_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        scheduler_state_path=tmp_path / "scheduler_tasks.json",
        execution_state_path=tmp_path / "execution_state.json",
        world_facts_path=tmp_path / "world_facts.json",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert status["status"] == "success"
    assert dispatched == ["prepared", "submitted"]
    assert {task["id"]: task["next_time"] for task in tasks} == original_times
