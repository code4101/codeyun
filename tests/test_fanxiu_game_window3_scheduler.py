import json
from datetime import datetime, timedelta

import pytest

from backend.api import fanxiu


def _scheduler_state_path(tmp_path):
    return tmp_path / "fanxiu" / "game-window3" / "runtime" / "scheduler_tasks.json"


def _patch_game_window3_api_common(monkeypatch, tmp_path):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu, "_game_window3_asset_tree_path", lambda entry_id: tmp_path / f"{entry_id}.json")
    monkeypatch.setattr(fanxiu, "ensure_feature_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(fanxiu, "_get_user_device_or_404", lambda *args, **kwargs: object())


def test_game_window3_json_write_retries_windows_permission_error(tmp_path, monkeypatch):
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

    fanxiu._write_game_window3_json(path, {"ok": True})

    assert calls["count"] == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp")) == []


def test_game_window3_default_scheduler_imports_legacy_behavior_tree_tasks():
    tasks = fanxiu._default_game_window3_scheduler_tasks()

    legacy_tasks = [item for item in tasks if item["source"] == "legacy_behavior_tree"]
    daily_tasks = [item for item in legacy_tasks if item["schedule_kind"] == "daily"]
    dynamic_tasks = [item for item in legacy_tasks if item["schedule_kind"] == "dynamic"]
    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")
    signup = next(item for item in tasks if item["id"] == "legacy-daily-signup")
    gift = next(item for item in tasks if item["id"] == "gift-code-weekly")

    assert len(tasks) == 28
    assert len(legacy_tasks) == 25
    assert len(daily_tasks) == 21
    assert len(dynamic_tasks) == 4
    assert youli["task_type"] == "legacy_daily_task"
    assert youli["enabled"] is False
    assert youli["interruptible"] is True
    assert signup["task_type"] == "legacy_daily_task"
    assert signup["enabled"] is False
    assert signup["legacy_name"] == "日常_报名"
    assert gift["schedule_kind"] == "manual"
    assert gift["payload"] == {"codes": []}
    assert not any(item["id"] == "daily-locate" for item in tasks)


def test_game_window3_scheduler_read_repairs_structural_fields(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: path)
    fanxiu._write_game_window3_scheduler_tasks([
        {
            "id": "legacy-daily-youli",
            "task_type": "legacy_daily_task",
            "label": "stale label",
            "source": "manual",
            "schedule_kind": "manual",
            "enabled": False,
            "priority": 123,
            "interruptible": True,
            "payload": {"custom": "kept"},
        },
        {
            "id": "gift-code-real-test",
            "task_type": "gift_code_redeem",
            "label": "真实测试礼包码",
            "source": "manual",
            "schedule_kind": "manual",
            "enabled": False,
            "priority": 40,
            "interruptible": True,
            "payload": {"codes": []},
        },
    ])

    tasks = fanxiu._read_game_window3_scheduler_tasks()
    youli = next(item for item in tasks if item["id"] == "legacy-daily-youli")

    assert not any(item["label"] == "真实测试礼包码" for item in tasks)
    assert youli["task_type"] == "legacy_daily_task"
    assert youli["source"] == "legacy_behavior_tree"
    assert youli["schedule_kind"] == "daily"
    assert youli["legacy_name"] == "日常_游历"
    assert youli["schedule_times"] == ["05:00", "00:00"]
    assert youli["payload"]["custom"] == "kept"
    assert youli["enabled"] is False
    assert youli["priority"] == 123
    assert youli["interruptible"] is True
    assert any(item["id"] == "gift-code-weekly" for item in tasks)


def test_game_window3_scheduler_response_marks_supported_tasks(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)

    response = fanxiu.get_fanxiu_game_window3_scheduler_tasks(
        current_user=object(),
        session=object(),
    )
    by_id = {item.id: item for item in response.tasks}

    assert by_id["gift-code-weekly"].supported is True
    assert by_id["go-settings"].supported is True
    assert by_id["hide-floating-window"].supported is True
    assert by_id["legacy-daily-youli"].supported is False


def test_game_window3_scheduler_put_does_not_persist_supported_view_field(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    task = fanxiu.FanxiuGameWindow3SchedulerTaskItem.model_validate({
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

    response = fanxiu.put_fanxiu_game_window3_scheduler_tasks(
        [task],
        current_user=object(),
        session=object(),
    )
    persisted = json.loads(_scheduler_state_path(tmp_path).read_text(encoding="utf-8"))

    assert response.tasks[0].supported is True
    assert "supported" not in persisted[0]


def test_game_window3_scheduler_read_forces_unsupported_tasks_disabled(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    fanxiu._write_game_window3_scheduler_tasks([
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

    response = fanxiu.get_fanxiu_game_window3_scheduler_tasks(
        current_user=object(),
        session=object(),
    )
    by_id = {item.id: item for item in response.tasks}
    persisted = json.loads(_scheduler_state_path(tmp_path).read_text(encoding="utf-8"))
    persisted_by_id = {item["id"]: item for item in persisted}

    assert by_id["legacy-daily-youli"].supported is False
    assert by_id["legacy-daily-youli"].enabled is False
    assert by_id["legacy-daily-youli"].last_result == "unsupported"
    assert persisted_by_id["legacy-daily-youli"]["enabled"] is False
    assert persisted_by_id["legacy-daily-youli"]["last_result"] == "unsupported"


def test_game_window3_runtime_scheduler_routes_replace_stepper_routes():
    paths = {route.path for route in fanxiu.status_router.routes}

    required_paths = {
        "/game-window3/runtime/status",
        "/game-window3/runtime/task/start",
        "/game-window3/runtime/task/stop",
        "/game-window3/runtime/task/tick",
        "/game-window3/runtime/logs",
        "/game-window3/scheduler/tasks",
        "/game-window3/scheduler/run-due",
        "/game-window3/scheduler/task/run-now",
    }

    assert required_paths <= paths
    assert "/game-window3/stepper/logs" not in paths
    assert not any("gift-code-task" in path for path in paths)


def test_game_window3_scheduler_daily_next_time_uses_next_clock():
    task = {
        "schedule_kind": "daily",
        "schedule_times": ["05:00", "00:00"],
    }

    assert fanxiu._next_game_window3_scheduler_time(task, datetime(2026, 6, 2, 4, 0)) == "2026-06-02 05:00:00"
    assert fanxiu._next_game_window3_scheduler_time(task, datetime(2026, 6, 2, 6, 0)) == "2026-06-03 00:00:00"


def test_game_window3_task_due_respects_enabled_next_time_and_retry(monkeypatch):
    now = datetime(2026, 6, 2, 12, 0, 0).timestamp()
    monkeypatch.setattr(fanxiu.time, "time", lambda: now)

    assert fanxiu._game_window3_task_due({"enabled": False, "next_time": None}) is False
    assert fanxiu._game_window3_task_due({"enabled": True, "next_time": None}) is True
    assert fanxiu._game_window3_task_due({"enabled": True, "next_time": "2026-06-02 12:01:00"}) is False
    assert fanxiu._game_window3_task_due({"enabled": True, "next_time": "2026-06-02 11:59:00"}) is True
    assert fanxiu._game_window3_task_due({
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

    def stop(self, entry_id):
        self.stopped_entry_id = entry_id

    def wait_until_idle(self, timeout_seconds):
        self.waited = True
        return True


def test_game_window3_prepare_scheduler_task_preempts_interruptible_runtime(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: path)
    monkeypatch.setattr(fanxiu, "_game_window3_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = _FakeRuntimeRunner(
        {
            "running": True,
            "entry_id": "entry-a",
            "current_task_id": "slow-task",
            "status": "running",
        },
        can_preempt=True,
    )
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", runner)
    tasks = [
        {"id": "slow-task", "last_result": "running"},
        {"id": "fast-task", "priority": 10, "last_result": ""},
    ]

    blocked = fanxiu._prepare_game_window3_runtime_for_scheduler_task(tasks[1], tasks)

    assert blocked is None
    assert runner.stopped_entry_id == "entry-a"
    assert runner.waited is True
    assert tasks[0]["last_result"] == "cancelled"


def test_game_window3_prepare_scheduler_task_queues_when_runtime_cannot_preempt(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = _FakeRuntimeRunner(
        {
            "running": True,
            "entry_id": "entry-a",
            "current_task_id": "locked-task",
            "status": "running",
        },
        can_preempt=False,
    )
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", runner)
    task = {"id": "queued-task", "priority": 20, "last_result": ""}

    blocked = fanxiu._prepare_game_window3_runtime_for_scheduler_task(task, [task])

    assert blocked is not None
    assert "不可抢占" in blocked["message"]
    assert task["last_result"] == "queued"
    assert runner.stopped_entry_id is None


def test_game_window3_world_facts_merges_runtime_guard_and_keeps_events(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_runtime_state_path", lambda: tmp_path / "runtime_state.json")
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")

    fanxiu._persist_game_window3_runtime_status({
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
    fanxiu._persist_game_window3_runtime_status({
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


def test_game_window3_scheduler_task_result_writes_world_fact(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = fanxiu._GameWindow3RuntimeRunner()
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


def test_game_window3_runtime_indexes_nested_frame_tree_images_and_guard_candidates():
    runner = fanxiu._GameWindow3RuntimeRunner()
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


def test_game_window3_scheduler_plan_uses_world_facts_and_due_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    runner = _FakeRuntimeRunner({"running": False, "status": "idle"}, can_preempt=True)
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", runner)
    fanxiu._write_game_window3_scheduler_tasks([
        {
            "id": "due-gift",
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
    fanxiu._record_game_window3_scheduler_task_fact({"id": "due-gift", "task_type": "gift_code_redeem", "label": "礼包"}, "success")

    plan = fanxiu._build_game_window3_scheduler_plan()

    assert plan["next_action"] == "run_due"
    assert plan["due_tasks"][0]["id"] == "due-gift"
    assert plan["due_tasks"][0]["supported"] is True
    assert plan["due_tasks"][0]["runnable"] is True
    assert plan["due_tasks"][0]["fact"]["last_result"] == "success"
    assert plan["facts_summary"]["task_fact_count"] == 1
    legacy_item = next(item for item in plan["tasks"] if item["id"] == "legacy-daily-youli")
    assert legacy_item["supported"] is False


def test_game_window3_scheduler_plan_waits_for_non_interruptible_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
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
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", runner)
    fanxiu._write_game_window3_scheduler_tasks([
        {
            "id": "due-gift",
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

    plan = fanxiu._build_game_window3_scheduler_plan()

    assert plan["next_action"] == "wait"
    assert plan["runtime"]["current_task"] == "日常游历"
    assert plan["due_tasks"][0]["id"] == "due-gift"
    assert plan["due_tasks"][0]["runnable"] is False


def test_game_window3_scheduler_syncs_dynamic_next_time_from_world_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    monkeypatch.setattr(fanxiu.time, "time", lambda: datetime(2026, 6, 2, 12, 0, 0).timestamp())
    runner = _FakeRuntimeRunner({"running": False, "status": "idle"}, can_preempt=True)
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", runner)
    fanxiu._write_game_window3_scheduler_tasks([
        {
            "id": "legacy-dynamic-daily-boss",
            "task_type": "legacy_dynamic_task",
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
    fanxiu._write_game_window3_world_facts({
        **fanxiu._initial_game_window3_world_facts(),
        "discoveries": {
            "scene": {},
            "popup": {},
            "occlusion": {},
            "task": {
                "legacy-dynamic-daily-boss": {
                    "id": "legacy-dynamic-daily-boss",
                    "discovered_next_time": "2026-06-02 13:00:00",
                    "updated_at": 123,
                }
            },
        },
    })

    tasks = fanxiu._read_game_window3_scheduler_tasks()
    target = next(item for item in tasks if item["id"] == "legacy-dynamic-daily-boss")
    plan = fanxiu._build_game_window3_scheduler_plan()
    plan_item = next(item for item in plan["tasks"] if item["id"] == "legacy-dynamic-daily-boss")

    assert target["next_time"] == "2026-06-02 13:00:00"
    assert target["checkpoint"]["world_fact_updated_at"] == 123
    assert target["enabled"] is False
    assert target["last_result"] == "unsupported"
    assert plan_item["supported"] is False
    assert plan_item["due"] is False
    assert "未启用" in plan_item["reason"]


def test_game_window3_scheduler_syncs_retry_after_from_world_facts(tmp_path, monkeypatch):
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: _scheduler_state_path(tmp_path))
    monkeypatch.setattr(fanxiu, "_game_window3_world_facts_path", lambda: tmp_path / "world_facts.json")
    fanxiu._write_game_window3_scheduler_tasks([
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
    fanxiu._write_game_window3_world_facts({
        **fanxiu._initial_game_window3_world_facts(),
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

    tasks = fanxiu._read_game_window3_scheduler_tasks()
    target = next(item for item in tasks if item["id"] == "gift-code-weekly")

    assert target["retry_after"] == "2026-06-02 13:00:00"
    assert target["checkpoint"]["world_fact_updated_at"] == 456


def test_game_window3_run_now_payload_override_does_not_mutate_scheduler_task():
    tasks = [
        {
            "id": "gift-code-weekly",
            "task_type": "gift_code_redeem",
            "label": "每周礼包码",
            "payload": {"codes": []},
        }
    ]

    run_task = fanxiu._game_window3_scheduler_run_now_task(
        tasks,
        "gift-code-weekly",
        {"codes": ["煮梅消夏"]},
    )

    assert run_task is not None
    assert run_task["payload"]["codes"] == ["煮梅消夏"]
    assert tasks[0]["payload"]["codes"] == []
    assert run_task is not tasks[0]


def test_game_window3_run_now_endpoint_uses_payload_override_without_persisting_codes(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    fanxiu._write_game_window3_scheduler_tasks([
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
    started: dict[str, object] = {}

    def fake_start_scheduler_tasks(**kwargs):
        started.update(kwargs)
        return {
            "ok": True,
            "running": True,
            "status": "running",
            "entry_id": kwargs["entry_id"],
            "task_type": "gift_code_redeem",
            "current_task": "兑换礼包码",
            "current_task_id": "gift-code-weekly",
            "message": "started",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._GAME_WINDOW3_RUNTIME_RUNNER, "start_scheduler_tasks", fake_start_scheduler_tasks)

    response = fanxiu.run_now_fanxiu_game_window3_scheduler_task(
        fanxiu.FanxiuGameWindow3SchedulerRunNowRequest(
            entry_id="entry",
            task_id="gift-code-weekly",
            payload={"codes": ["煮梅消夏"]},
        ),
        current_user=object(),
        session=object(),
    )
    persisted = fanxiu._read_game_window3_scheduler_tasks()
    persisted_task = persisted[0]
    run_task = started["tasks"][0]

    assert response.running is True
    assert run_task["payload"]["codes"] == ["煮梅消夏"]
    assert persisted_task["payload"]["codes"] == []
    assert persisted_task["last_result"] == "running"
    assert persisted_task["last_run_at"]


def test_game_window3_run_now_gift_code_executes_through_runtime_thread(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    asset_tree_path = tmp_path / "entry.json"
    asset_tree_path.write_text(json.dumps([
        {"type": "image", "id": "49", "title": "#49 设置页", "filename": "0049.png", "shapes": []},
        {"type": "image", "id": "78", "title": "#78 兑换礼包", "filename": "0078.png", "shapes": []},
    ], ensure_ascii=False), encoding="utf-8")
    fanxiu._write_game_window3_scheduler_tasks([
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
    runner = fanxiu._GameWindow3RuntimeRunner()
    executed: list[list[str]] = []

    def fake_require_assets(ctx):
        return None

    def fake_execute_gift_code_task(ctx, codes, stop_event):
        executed.append(list(codes))

    monkeypatch.setattr(runner, "_require_assets", fake_require_assets)
    monkeypatch.setattr(runner, "_execute_gift_code_task", fake_execute_gift_code_task)
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", runner)

    response = fanxiu.run_now_fanxiu_game_window3_scheduler_task(
        fanxiu.FanxiuGameWindow3SchedulerRunNowRequest(
            entry_id="entry",
            task_id="gift-code-weekly",
            payload={"codes": [" 煮梅消夏 ", ""]},
        ),
        current_user=object(),
        session=object(),
    )

    assert response.running is True
    assert runner.wait_until_idle(2.0) is True
    status = runner.status()
    persisted_status = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))
    persisted_tasks = fanxiu._read_game_window3_scheduler_tasks()
    persisted_task = next(item for item in persisted_tasks if item["id"] == "gift-code-weekly")

    assert executed == [["煮梅消夏"]]
    assert status["running"] is False
    assert status["status"] == "success"
    assert status["task_type"] == "scheduler_run_now"
    assert persisted_status["status"] == "success"
    assert persisted_task["last_result"] == "success"
    assert persisted_task["payload"]["codes"] == []


def test_game_window3_run_now_rejects_unverified_task_type(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    fanxiu._write_game_window3_scheduler_tasks([
        {
            "id": "legacy-daily-youli",
            "task_type": "legacy_daily_task",
            "label": "日常 游历",
            "source": "legacy_behavior_tree",
            "schedule_kind": "daily",
            "legacy_name": "日常_游历",
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
            "payload": {"legacy_name": "日常_游历"},
            "checkpoint": None,
        }
    ])

    with pytest.raises(fanxiu.HTTPException) as exc_info:
        fanxiu.run_now_fanxiu_game_window3_scheduler_task(
            fanxiu.FanxiuGameWindow3SchedulerRunNowRequest(
                entry_id="entry",
                task_id="legacy-daily-youli",
                payload={},
            ),
            current_user=object(),
            session=object(),
        )

    assert exc_info.value.status_code == 400
    assert "尚未纳入当前框架验收" in str(exc_info.value.detail)


def test_game_window3_run_due_endpoint_skips_legacy_placeholders(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    fanxiu._write_game_window3_scheduler_tasks([
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
    ])
    started: dict[str, object] = {}

    def fake_start_scheduler_tasks(**kwargs):
        started.update(kwargs)
        return {
            "ok": True,
            "running": True,
            "status": "running",
            "entry_id": kwargs["entry_id"],
            "task_type": "scheduler_run_due",
            "current_task": "执行全部到期任务",
            "current_task_id": "scheduler_run_due",
            "message": "started",
            "logs": [],
        }

    monkeypatch.setattr(fanxiu._GAME_WINDOW3_RUNTIME_RUNNER, "start_scheduler_tasks", fake_start_scheduler_tasks)

    response = fanxiu.run_due_fanxiu_game_window3_scheduler_tasks(
        fanxiu.FanxiuGameWindow3SchedulerRunDueRequest(entry_id="entry"),
        current_user=object(),
        session=object(),
    )
    run_tasks = started["tasks"]

    assert response.running is True
    assert [item["id"] for item in run_tasks] == ["gift-code-weekly"]
    assert all(item["task_type"] != "legacy_daily_task" for item in run_tasks)


def test_game_window3_run_due_endpoint_reports_no_executable_due_tasks(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    fanxiu._write_game_window3_scheduler_tasks([
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
    ])

    response = fanxiu.run_due_fanxiu_game_window3_scheduler_tasks(
        fanxiu.FanxiuGameWindow3SchedulerRunDueRequest(entry_id="entry"),
        current_user=object(),
        session=object(),
    )

    assert response.running is False
    assert response.message == "没有可执行的到期任务"


def test_game_window3_guard_endpoint_persists_switch_state(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)

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

    monkeypatch.setattr(fanxiu._GAME_WINDOW3_RUNTIME_RUNNER, "set_guard", fake_set_guard)

    response = fanxiu.set_fanxiu_game_window3_runtime_guard(
        fanxiu.FanxiuGameWindow3RuntimeGuardRequest(entry_id="entry", enabled=True, interval_seconds=3),
        current_user=object(),
        session=object(),
    )
    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))

    assert response.guard_enabled is True
    assert response.guard_running is True
    assert response.guard_entry_id == "entry"
    assert persisted["guard_enabled"] is True
    assert persisted["guard_interval_seconds"] == 3


def test_game_window3_runtime_status_corrects_stale_running_after_backend_reload(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
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
    fanxiu._write_game_window3_json(tmp_path / "runtime_state.json", stale_status)
    monkeypatch.setattr(fanxiu, "_GAME_WINDOW3_RUNTIME_RUNNER", fanxiu._GameWindow3RuntimeRunner())

    status = fanxiu._game_window3_runtime_status()
    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))

    assert status["running"] is False
    assert status["guard_enabled"] is False
    assert status["guard_running"] is False
    assert status["status"] == "stopped"
    assert status["message"] == "后端已重载，运行线程已结束"
    assert any(item["message"] == "旧日志" for item in status["logs"])
    assert persisted["running"] is False
    assert persisted["guard_enabled"] is False


def test_game_window3_runtime_thread_finish_persists_status(tmp_path, monkeypatch):
    _patch_game_window3_api_common(monkeypatch, tmp_path)
    runner = fanxiu._GameWindow3RuntimeRunner()
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_require_assets", lambda _ctx: None)
    monkeypatch.setattr(runner, "_execute_runtime_task", lambda *_args, **_kwargs: "success")

    runner.start_generic_runtime_task(
        entry=object(),
        entry_id="entry",
        task_type="hide_floating_window",
        payload={},
        asset_tree_path=tmp_path / "entry.json",
    )
    assert runner._thread is not None
    runner._thread.join(timeout=2)

    persisted = json.loads((tmp_path / "runtime_state.json").read_text(encoding="utf-8"))
    facts = json.loads((tmp_path / "world_facts.json").read_text(encoding="utf-8"))

    assert persisted["running"] is False
    assert persisted["status"] == "success"
    assert persisted["task_type"] == "hide_floating_window"
    assert facts["runtime"]["running"] is False
    assert facts["runtime"]["task_type"] == "hide_floating_window"


class _DispatchRunner(fanxiu._GameWindow3RuntimeRunner):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _log(self, kind, message):
        self.calls.append(("log", kind, message))

    def _align_settings(self, ctx, stop_event):
        self.calls.append(("align_settings",))

    def _execute_hide_floating_window(self, ctx, stop_event):
        self.calls.append(("hide_floating_window",))

    def _execute_gift_code_task(self, ctx, codes, stop_event):
        self.calls.append(("gift_code_redeem", tuple(codes)))


def test_game_window3_runtime_task_dispatch_uses_backend_tasks():
    runner = _DispatchRunner()
    ctx = {"images": {}, "entry": object()}
    stop_event = fanxiu.threading.Event()

    assert runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 49}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "hide_floating_window", {}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "gift_code_redeem", {"codes": [" a ", "", "b"]}, stop_event) == "success"
    assert runner._execute_runtime_task(ctx, "legacy_daily_task", {"legacy_name": "日常_魔祖"}, stop_event) == "unsupported"
    assert runner._execute_runtime_task(ctx, "legacy_dynamic_task", {"legacy_name": "日常_首领"}, stop_event) == "unsupported"
    with pytest.raises(RuntimeError, match="#49"):
        runner._execute_runtime_task(ctx, "go_scene", {"target_scene_id": 69}, stop_event)
    with pytest.raises(RuntimeError, match="暂不支持"):
        runner._execute_runtime_task(ctx, "daily_locate", {}, stop_event)

    assert ("align_settings",) in runner.calls
    assert ("hide_floating_window",) in runner.calls
    assert ("gift_code_redeem", ("a", "b")) in runner.calls
    assert any(call == ("log", "skip", "旧版任务「日常_魔祖」尚未迁移，已跳过") for call in runner.calls)
    assert any(call == ("log", "skip", "旧版任务「日常_首领」尚未迁移，已跳过") for call in runner.calls)


def test_game_window3_runtime_start_accepts_first_batch_task_types(monkeypatch):
    runner = fanxiu._GameWindow3RuntimeRunner()
    accepted = []

    def fake_start_generic_runtime_task(**kwargs):
        accepted.append(kwargs["task_type"])
        return {"ok": True, "task_type": kwargs["task_type"]}

    monkeypatch.setattr(runner, "start_generic_runtime_task", fake_start_generic_runtime_task)

    for task_type in [
        "go_scene",
        "hide_floating_window",
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
    ]


def test_game_window3_runtime_start_rejects_unverified_task_types(monkeypatch):
    runner = fanxiu._GameWindow3RuntimeRunner()
    monkeypatch.setattr(runner, "start_generic_runtime_task", lambda **kwargs: {"ok": True})

    with pytest.raises(fanxiu.HTTPException) as daily_exc:
        runner.start_runtime_task(
            entry=object(),
            entry_id="entry",
            task_type="daily_locate",
            payload={},
            asset_tree_path=object(),
        )
    assert daily_exc.value.status_code == 400

    with pytest.raises(fanxiu.HTTPException) as scene_exc:
        runner.start_runtime_task(
            entry=object(),
            entry_id="entry",
            task_type="go_scene",
            payload={"target_scene_id": 69},
            asset_tree_path=object(),
        )
    assert scene_exc.value.status_code == 400
    assert "#49" in str(scene_exc.value.detail)


def test_game_window3_mark_scheduler_task_advances_daily_and_sets_retry(tmp_path, monkeypatch):
    path = _scheduler_state_path(tmp_path)
    monkeypatch.setattr(fanxiu, "_game_window3_scheduler_state_path", lambda: path)
    fixed_now = datetime(2026, 6, 2, 6, 0, 0)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(fanxiu, "datetime", FixedDatetime)
    runner = fanxiu._GameWindow3RuntimeRunner()
    daily = {
        "id": "daily",
        "schedule_kind": "daily",
        "schedule_times": ["05:00", "00:00"],
        "last_result": "",
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
    assert daily["next_time"] == "2026-06-03 00:00:00"
    assert daily["retry_after"] is None
    assert error_task["last_result"] == "error"
    assert error_task["retry_after"] == "2026-06-02 06:02:00"


def test_game_window3_ocr_centers_in_shape_filters_signup_button_text():
    runner = fanxiu._GameWindow3RuntimeRunner()
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
