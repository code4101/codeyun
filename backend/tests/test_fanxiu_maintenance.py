from __future__ import annotations

from datetime import datetime
import threading

import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_control
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
from backend.core.fanxiu.data_annotation.tasks import maintenance as maintenance_task
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks


@pytest.fixture(autouse=True)
def _isolate_mumu_health(monkeypatch):
    monkeypatch.setattr(
        maintenance_task,
        "mumu_device_health_check",
        lambda **_kwargs: {"status": "unhealthy"},
    )


class _FakeRuntime:
    def __init__(self, scenes):
        self.scenes = list(scenes)
        self.clicks = []
        self.completion_message = ""

    def current_scene(self, _scene_ids, update=True):
        scene_id = self.scenes.pop(0)
        return scene_id, 100.0, f"frame-{scene_id}"

    def click_shape_center(self, scene_id, title):
        self.clicks.append((scene_id, title))

    def wait_action_settle(self, _seconds):
        if False:
            yield None

    def ocr_text(self, frame):
        return str(frame)

    def set_completion_message(self, message):
        self.completion_message = message


class _FakeMaintenanceRunner(maintenance_task.MaintenanceTaskMixin):
    def __init__(self, world_facts_path, runtime):
        self.world_facts_path = world_facts_path
        self.runtime = runtime
        self.next_times = []
        self._lock = threading.RLock()

    def _maintenance_world_facts_path(self):
        return self.world_facts_path

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.next_times.append((task_id, next_time))

    def _schedule_login_job_first(self):
        self.next_times.append(("login-game", "queue-head"))
        return "queue-head"

    def _fanxiu_runtime(self, *_args, **_kwargs):
        return self.runtime

    def _raise_if_stopped(self, stop_event):
        if stop_event.is_set():
            raise RuntimeError("stopped")

    def _set_status_locked(self, *_args, **_kwargs):
        return None

    def _log(self, *_args, **_kwargs):
        return None


def _drain(generator):
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        return exc.value


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


def test_maintenance_recovery_restarts_until_a_preset_startup_page_appears():
    task = next(
        item
        for item in default_data_annotation_scheduler_tasks()
        if item["id"] == MAINTENANCE_RECOVERY_TASK_ID
    )

    assert task["payload"]["unbounded_runtime"] is True
    assert task["payload"]["startup_timeout_seconds"] == 300
    assert "startup_restart_limit" not in task["payload"]
    assert task["error_retry_delay_seconds"] == 1800


def test_maintenance_startup_wait_accepts_world_as_available_business_scene(
    monkeypatch,
    tmp_path,
):
    runtime = _FakeRuntime([34, 14])
    runner = _FakeMaintenanceRunner(tmp_path / "world_facts.json", runtime)
    monotonic_values = iter([0.0, 1.0, 2.0])
    monkeypatch.setattr(maintenance_task.time, "monotonic", lambda: next(monotonic_values))

    result = _drain(
        runner._wait_for_game_startup_page(
            runtime,
            threading.Event(),
            timeout=300,
            poll_seconds=5,
        )
    )

    assert result["ready"] is True
    assert result["scene_id"] == 34


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


def test_maintenance_observation_bypasses_ordinary_popup_guard(tmp_path):
    class _RawRuntime:
        ctx = {}

        def cur_frame(self, *, update=True):
            return "maintenance-frame"

        def current_scene(self, *_args, **_kwargs):
            raise AssertionError("维护恢复不能经过普通作业 current_scene 门卫")

        def ocr_text(self, _frame):
            return "停更码字中，敬请期待更新"

    runtime = _RawRuntime()
    runner = _FakeMaintenanceRunner(tmp_path / "world_facts.json", runtime)
    runner._identify_scene_number = lambda *_args, **_kwargs: (47, 88.0)

    scene_id, score, frame, text = runner._observe_maintenance_scene(runtime)

    assert (scene_id, score, frame) == (LOGIN_MAINTENANCE_PROMPT_SCENE_ID, 88.0, "maintenance-frame")
    assert "停更码字中" in text


def test_recovery_task_keeps_gate_after_six_cover_probes(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([None, 14, 18, 18, 18, 18, 18, 18, 18, 18])
    runner = _FakeMaintenanceRunner(path, runtime)
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **_kwargs: {"recovered": True, "status": "healthy"},
    )

    generator = runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"probe_interval_seconds": 5, "probe_duration_seconds": 30},
    )
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        result = exc.value

    assert result["result"] == "success"
    assert len([click for click in runtime.clicks if click == (18, "进入游戏")]) == 6
    assert read_maintenance_gate(path)["active"] is True
    assert runner.next_times[-1][0] == MAINTENANCE_RECOVERY_TASK_ID
    assert runner.next_times[-1][1] is not None


def test_recovery_task_probes_actionable_cover_without_restarting(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([18, 18, 18, 18])
    runner = _FakeMaintenanceRunner(path, runtime)
    restarts = []
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **kwargs: restarts.append(kwargs) or {"recovered": True, "status": "healthy"},
    )

    result = _drain(runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"probe_interval_seconds": 1, "probe_duration_seconds": 3},
    ))

    assert result["result"] == "success"
    assert restarts == []
    assert len([click for click in runtime.clicks if click == (18, "进入游戏")]) == 3
    assert read_maintenance_gate(path)["active"] is True


def test_recovery_task_does_not_restart_healthy_unknown_scene(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([None])
    runner = _FakeMaintenanceRunner(path, runtime)
    restarts = []
    monkeypatch.setattr(
        maintenance_task,
        "mumu_device_health_check",
        lambda **_kwargs: {"status": "healthy"},
    )
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **kwargs: restarts.append(kwargs) or {"recovered": True, "status": "healthy"},
    )

    result = _drain(runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
    ))

    assert result["result"] == "success"
    assert restarts == []
    assert "不重启模拟器" in result["message"]
    assert read_maintenance_gate(path)["active"] is True


def test_recovery_task_accepts_formal_business_scene_without_restarting(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([289])
    runner = _FakeMaintenanceRunner(path, runtime)
    restarts = []
    monkeypatch.setattr(
        maintenance_task,
        "mark_mumu_device_startup_ready",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **kwargs: restarts.append(kwargs) or {"recovered": True, "status": "healthy"},
    )

    result = _drain(runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
    ))

    assert result["result"] == "success"
    assert restarts == []
    assert read_maintenance_gate(path)["active"] is False
    assert runner.next_times[-1] == (MAINTENANCE_RECOVERY_TASK_ID, None)


def test_recovery_task_keeps_gate_when_cover_click_only_reaches_unknown(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([None, 14, 18, None, 18, None, 18])
    runner = _FakeMaintenanceRunner(path, runtime)
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **_kwargs: {"recovered": True, "status": "healthy"},
    )

    result = _drain(runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"probe_interval_seconds": 1, "probe_duration_seconds": 3},
    ))

    assert result["result"] == "success"
    assert read_maintenance_gate(path)["active"] is True
    assert runner.next_times[-1][0] == MAINTENANCE_RECOVERY_TASK_ID
    assert runner.next_times[-1][1] is not None


def test_recovery_task_only_clears_gate_after_reaching_world(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([None, 14, 18, 18, 34])
    runner = _FakeMaintenanceRunner(path, runtime)
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **_kwargs: {"recovered": True, "status": "healthy"},
    )

    generator = runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"probe_interval_seconds": 5, "probe_duration_seconds": 30},
    )
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        result = exc.value

    assert result["result"] == "success"
    assert read_maintenance_gate(path)["active"] is False
    assert runner.next_times[-1] == (MAINTENANCE_RECOVERY_TASK_ID, None)


def test_recovery_task_marks_startup_ready_before_scheduling_login(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([None, 14, 18, 19])
    runner = _FakeMaintenanceRunner(path, runtime)
    ready_reasons = []
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **_kwargs: {"recovered": True, "status": "healthy"},
    )
    monkeypatch.setattr(
        maintenance_task,
        "mark_mumu_device_startup_ready",
        lambda **kwargs: ready_reasons.append(kwargs.get("reason")) or {},
    )

    result = _drain(runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
        {"probe_interval_seconds": 5, "probe_duration_seconds": 30},
    ))

    assert result["result"] == "success"
    assert read_maintenance_gate(path)["active"] is False
    assert ready_reasons == ["maintenance_startup_page_seen"]
    assert runner.next_times[-2] == (MAINTENANCE_RECOVERY_TASK_ID, None)
    assert runner.next_times[-1] == ("login-game", "queue-head")


def test_recovery_task_does_not_restart_when_world_is_already_available(monkeypatch, tmp_path):
    path = tmp_path / "world_facts.json"
    open_maintenance_gate(path, observed_at=datetime(2026, 7, 23, 16, 20))
    runtime = _FakeRuntime([34])
    runner = _FakeMaintenanceRunner(path, runtime)
    restarts = []
    monkeypatch.setattr(
        maintenance_task,
        "recover_mumu_device",
        lambda **kwargs: restarts.append(kwargs) or {"recovered": True, "status": "healthy"},
    )

    generator = runner._execute_maintenance_recovery_task(
        {"asset_tree_path": tmp_path / "asset-tree.json"},
        threading.Event(),
    )
    try:
        while True:
            next(generator)
    except StopIteration as exc:
        result = exc.value

    assert result["result"] == "success"
    assert restarts == []
    assert read_maintenance_gate(path)["active"] is False
    assert runner.next_times[-1] == (MAINTENANCE_RECOVERY_TASK_ID, None)


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
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(behavior_tree_control, "ensure_fanxiu_behavior_tree_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(behavior_tree_control, "select_due_data_annotation_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )
    monkeypatch.setattr(behavior_tree_control, "runtime_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_runtime_status",
        lambda status, **_kwargs: persisted.append(dict(status)),
        raising=False,
    )

    status = behavior_tree_control.run_due_scheduler_tasks(
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
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(behavior_tree_control, "ensure_fanxiu_behavior_tree_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(behavior_tree_control, "select_due_data_annotation_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )
    monkeypatch.setattr(behavior_tree_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: pytest.fail("测试应走统一终态执行替身"),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "schedule_login_job_first",
        lambda **kwargs: scheduled.append(kwargs) or "2026-07-25 14:01:00",
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: pytest.fail("Scheduler 不得读取画面 overlay"),
    )
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "runtime_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_runtime_status",
        lambda status, **_kwargs: persisted.append(dict(status)),
        raising=False,
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )

    status = behavior_tree_control.run_due_scheduler_tasks(
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

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", read_settings)
    monkeypatch.setattr(behavior_tree_control, "ensure_fanxiu_behavior_tree_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(behavior_tree_control, "select_due_data_annotation_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        behavior_tree_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: pytest.fail("Scheduler 不得读取画面 overlay"),
    )
    monkeypatch.setattr(behavior_tree_control, "runtime_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(
        behavior_tree_control,
        "persist_runtime_status",
        lambda *_args, **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "prepare_runtime_for_scheduler_task",
        lambda *_args, **_kwargs: pytest.fail("切到 AI 后不应进入提交准备"),
    )

    status = behavior_tree_control.run_due_scheduler_tasks(
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
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(behavior_tree_control, "ensure_fanxiu_behavior_tree_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(behavior_tree_control, "select_due_data_annotation_scheduler_tasks", lambda _tasks, **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "sort_scheduler_tasks_for_dispatch", lambda tasks: tasks)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "submit_runtime_task_cell",
        lambda **_kwargs: pytest.fail("测试应走统一终态执行替身"),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "schedule_login_job_first",
        lambda **kwargs: scheduled.append(kwargs) or "2026-07-25 14:01:00",
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Scheduler 不得读取画面 overlay")),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **kwargs: dispatched.append(kwargs["task"]["id"]) or {"status": "success"},
    )
    monkeypatch.setattr(behavior_tree_control, "prepare_runtime_for_scheduler_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "runtime_status", lambda **_kwargs: {}, raising=False)
    monkeypatch.setattr(behavior_tree_control, "persist_runtime_status", lambda *_args, **_kwargs: None, raising=False)

    status = behavior_tree_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        world_facts_path=tmp_path / "world_facts.json",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert scheduled == []
    assert dispatched == ["daily-redpacket"]


def test_scheduler_overlay_probe_does_not_treat_scene_49_ocr_as_announcement(monkeypatch, tmp_path):
    runner = behavior_tree_control.create_behavior_tree_runtime_runner()
    frame = "frame-49"

    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: frame)
    monkeypatch.setattr(runner, "_ocr_fragments", lambda _frame: "ocr-fragments")
    monkeypatch.setattr(runner, "_ocr_text", lambda _fragments: "游戏公告 更新公告 风险提醒")

    def identify(ctx, observed_frame, preferred_scene_ids):
        assert observed_frame == frame
        assert preferred_scene_ids == [14]
        ctx["_last_scene_recognition_status"] = "startup_ocr"
        return 14, 100.0

    monkeypatch.setattr(runner, "_identify_scene_number", identify)
    monkeypatch.setattr(behavior_tree_control, "create_behavior_tree_runtime_runner", lambda: runner)

    blockers = behavior_tree_control.scheduler_blocking_overlays(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert blockers == []


def test_scheduler_environment_probe_requires_current_reference_similarity(monkeypatch, tmp_path):
    from backend.core.fanxiu.data_annotation import unknown_recovery

    runner = behavior_tree_control.create_behavior_tree_runtime_runner()
    image74 = {"id": "image-74", "type": "image", "title": "#74 天道魁首引导弹窗", "filename": "0074.png"}
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [image74])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {74: image74})
    monkeypatch.setattr(runner, "_screencap", lambda _ctx: "current-frame")
    monkeypatch.setattr(behavior_tree_control, "create_behavior_tree_runtime_runner", lambda: runner)
    monkeypatch.setattr(unknown_recovery, "reference_frame_similarity", lambda *_args: 95.0)

    blockers = behavior_tree_control.scheduler_blocking_overlays(
        entry=object(),
        entry_id="entry",
        asset_tree_path=tmp_path / "asset-tree.json",
        environment_circuit={
            "scene_id": 74,
            "task_ids": ["daily-boss", "daily-assistant"],
            "incident_ids": ["incident-a", "incident-b"],
        },
    )

    assert blockers[0]["kind"] == "repeated_environment_failure"
    assert blockers[0]["blocking"] is True
    assert blockers[0]["frame_similarity"] == 95.0
    assert "next_time 保持不变" in blockers[0]["message"]


def test_scheduler_environment_probe_fails_closed_when_reference_is_unavailable(monkeypatch, tmp_path):
    runner = behavior_tree_control.create_behavior_tree_runtime_runner()
    monkeypatch.setattr(runner, "_load_asset_tree", lambda _path: [])
    monkeypatch.setattr(runner, "_index_images", lambda _tree: {})
    monkeypatch.setattr(behavior_tree_control, "create_behavior_tree_runtime_runner", lambda: runner)

    blockers = behavior_tree_control.scheduler_blocking_overlays(
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
        behavior_tree_control,
        "scheduler_blocking_overlays",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("plan 不得读取画面 overlay")),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "read_scheduler_settings",
        lambda **_kwargs: {"job_group_enabled": True},
    )
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: [ordinary_task])
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        behavior_tree_control,
        "read_world_facts",
        lambda *_args, **_kwargs: {"availability": {"game": {"active": True, "state": "maintenance"}}},
    )
    monkeypatch.setattr(behavior_tree_control, "scheduler_tasks_for_dispatch", lambda tasks, **_kwargs: tasks)
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {})
    monkeypatch.setattr(
        behavior_tree_control,
        "build_data_annotation_scheduler_plan",
        lambda *_args, **_kwargs: {"next_action": "run_due", "message": "运行到期作业"},
    )

    plan = behavior_tree_control.build_scheduler_plan(
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

    monkeypatch.setattr(behavior_tree_control, "read_scheduler_settings", lambda **_kwargs: {
        "behavior_tree_enabled": True,
        "job_group_enabled": True,
    })
    monkeypatch.setattr(behavior_tree_control, "ensure_fanxiu_behavior_tree_service", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(behavior_tree_control, "read_scheduler_tasks", lambda **_kwargs: tasks)
    monkeypatch.setattr(behavior_tree_control, "reconcile_stale_scheduler_attempts", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(behavior_tree_control, "select_due_data_annotation_scheduler_tasks", lambda _tasks, **_kwargs: list(tasks))
    monkeypatch.setattr(behavior_tree_control, "sort_scheduler_tasks_for_dispatch", lambda items: items)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_scheduler_kernel_code_current",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "prepare_runtime_for_scheduler_task",
        lambda *_args, **_kwargs: dispatched.append("prepared"),
    )
    monkeypatch.setattr(
        behavior_tree_control,
        "_run_scheduler_task_cell_and_record_terminal",
        lambda **_kwargs: dispatched.append("submitted") or {"status": "success"},
    )

    status = behavior_tree_control.run_due_scheduler_tasks(
        entry=object(),
        entry_id="entry",
        scheduler_state_path=tmp_path / "scheduler_tasks.json",
        runtime_state_path=tmp_path / "runtime_state.json",
        world_facts_path=tmp_path / "world_facts.json",
        asset_tree_path=tmp_path / "asset-tree.json",
    )

    assert status["status"] == "success"
    assert dispatched == ["prepared", "submitted"]
    assert {task["id"]: task["next_time"] for task in tasks} == original_times
