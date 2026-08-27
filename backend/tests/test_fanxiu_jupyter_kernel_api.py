from __future__ import annotations

import importlib
import inspect
import os
import platform
import queue
import runpy
import threading
from types import GeneratorType
from pathlib import Path

import pytest

from backend.core.fanxiu.data_annotation import behavior_tree_framework
from backend.core.fanxiu.data_annotation import behavior_tree_control
from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel


def test_task_is_only_an_ordinary_cell_constructor() -> None:
    cell = FanxiuKernel(entry_id="entry").task("detect_scene", {"probe": True})

    assert cell.code == (
        "# fanxiu:managed-task-cell\n"
        "run_task_cell('detect_scene', {'probe': True})"
    )
    assert not hasattr(cell, "submit")


def test_repo_wmi_guard_preserves_platform_query_field_arity() -> None:
    if os.name != "nt":
        pytest.skip("Windows-only platform WMI contract")

    original = platform._wmi_query
    try:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "sitecustomize.py"))
        assert platform._wmi_query("CPU", "Manufacturer", "Caption") == ("", "")
        assert platform._wmi_query(
            "OS",
            "Version",
            "ProductType",
            "BuildType",
            "ServicePackMajorVersion",
            "ServicePackMinorVersion",
        ) == ("", "1", "", "", "")
    finally:
        platform._wmi_query = original


def test_kernel_child_environment_skips_optional_platform_wmi_probe() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_child_env

    assert fanxiu_kernel_child_env()["CODEYUN_SKIP_PLATFORM_WMI_PROCESSOR"] == "1"


def test_kernel_manager_service_uses_hidden_console_python(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu.behavior_tree import runtime
    from backend.core.fanxiu.behavior_tree import jupyter_kernel

    statuses = iter((
        {"alive": False, "execution_state": "dead", "manager_pid": None},
        {"alive": True, "execution_state": "idle", "manager_pid": 10, "kernel_pid": 11},
    ))
    captured: dict[str, object] = {}

    class Process:
        pid = 12

        @staticmethod
        def poll():
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(jupyter_kernel, "fanxiu_kernel_manager_status", lambda **_kwargs: next(statuses))
    monkeypatch.setattr(runtime, "resolve_python", lambda **_kwargs: r"C:\Python\python.exe")
    monkeypatch.setattr(runtime, "popen_service", fake_popen)
    monkeypatch.setattr(runtime, "codeyun_temp_root", lambda _name: tmp_path)

    result = runtime._start_external_fanxiu_behavior_tree_service_unlocked(
        "entry",
        wait_seconds=1,
    )

    assert result["started"] is True
    assert captured["command"][0] == r"C:\Python\python.exe"
    assert captured["command"][-1] == "service"


def test_runtime_framework_task_uses_kernel_cell_path(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Cell:
        def run(self, *, timeout_seconds):
            calls.append(("run", timeout_seconds))
            return {"status": "success"}

    class Kernel:
        def __init__(self, *, entry_id):
            calls.append(("kernel", entry_id))

        def task(self, task_type, payload, *, timeout_seconds):
            calls.append(("task", (task_type, payload, timeout_seconds)))
            return Cell()

    kernel_module = importlib.import_module("backend.core.fanxiu.behavior_tree.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)
    result = behavior_tree_framework.submit_task_cell(
        entry=object(),
        entry_id="entry",
        task_type="detect_scene",
        payload={"max_runtime_seconds": 40},
    )

    assert result == {"status": "success"}
    assert calls == [
        ("kernel", "entry"),
        ("task", ("detect_scene", {"max_runtime_seconds": 40}, 70.0)),
        ("run", 70.0),
    ]


def test_runtime_framework_unbounded_task_keeps_cell_wait_unbounded(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class Cell:
        def run(self, *, timeout_seconds):
            calls.append(("run", timeout_seconds))
            return {"status": "success"}

    class Kernel:
        def __init__(self, *, entry_id):
            calls.append(("kernel", entry_id))

        def task(self, task_type, payload, *, timeout_seconds):
            calls.append(("task", (task_type, payload, timeout_seconds)))
            return Cell()

    kernel_module = importlib.import_module("backend.core.fanxiu.behavior_tree.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)

    result = behavior_tree_framework.submit_task_cell(
        entry=object(),
        entry_id="entry",
        task_type="login_game",
        payload={"unbounded_runtime": True},
    )

    assert result == {"status": "success"}
    assert calls == [
        ("kernel", "entry"),
        ("task", ("login_game", {"unbounded_runtime": True}, None)),
        ("run", None),
    ]


@pytest.mark.parametrize("timeout_seconds", [5, None])
def test_execute_cell_finishes_when_iopub_is_idle_but_shell_reply_is_missing(
    monkeypatch,
    tmp_path,
    timeout_seconds,
) -> None:
    from backend.core.fanxiu.behavior_tree import jupyter_kernel

    connection_path = tmp_path / "kernel.json"
    connection_path.write_text("{}", encoding="utf-8")
    calls: list[str] = []

    class Client:
        def __init__(self, *, connection_file):
            assert connection_file == str(connection_path)

        def load_connection_file(self):
            return None

        def start_channels(self):
            return None

        def wait_for_ready(self, *, timeout):
            raise AssertionError("routine cell submission must not probe kernel readiness")

        def execute(self, source, *, allow_stdin, stop_on_error):
            assert source == "1 + 1"
            assert allow_stdin is False
            assert stop_on_error is True
            return "cell-message"

        def get_iopub_msg(self, *, timeout):
            assert timeout > 0
            return {
                "parent_header": {"msg_id": "cell-message"},
                "msg_type": "status",
                "content": {"execution_state": "idle"},
            }

        def get_shell_msg(self, *, timeout):
            calls.append("shell")
            raise queue.Empty

        def stop_channels(self):
            calls.append("stop")

    jupyter_client = importlib.import_module("jupyter_client")
    monkeypatch.setattr(jupyter_client, "BlockingKernelClient", Client)

    result = jupyter_kernel.execute_fanxiu_jupyter_cell(
        "1 + 1",
        timeout_seconds=timeout_seconds,
        connection_path=connection_path,
    )

    assert result["status"] == "success"
    assert calls == ["shell", "stop"]


def test_active_kernel_path_has_no_manual_queue_or_kernel_lock() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "backend/core/fanxiu/behavior_tree/kernel.py",
        root / "backend/core/fanxiu/behavior_tree/jupyter_kernel.py",
        root / "backend/core/fanxiu/data_annotation/behavior_tree_framework.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    for forbidden in ("manual_jobs", "claim", "requeue", "dedupe", "job_group_isolation"):
        assert forbidden not in source
    for forbidden in (
        "FanxiuInfoWindowObserver",
        "_fanxiu_info_window_observer",
        "recognize_info_window_scene",
    ):
        assert forbidden not in source


def test_kernel_status_keeps_kernel_and_business_runtime_orthogonal(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.runtime.fanxiu_behavior_tree_runtime_status",
        lambda: {"status": "success", "current_task": "demo"},
    )

    assert FanxiuKernel().status() == {
        "kernel": {"alive": True, "execution_state": "idle"},
        "runtime": {"status": "success", "current_task": "demo"},
    }


def test_interrupt_and_restart_are_distinct_native_commands(monkeypatch) -> None:
    calls: list[str] = []

    def send(command: str, *, timeout_seconds: float):
        calls.append(command)
        return {"ok": True, "command": command, "timeout": timeout_seconds}

    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.send_fanxiu_kernel_manager_command",
        send,
    )

    kernel = FanxiuKernel()
    assert kernel.interrupt(timeout_seconds=2)["command"] == "interrupt"
    assert kernel.restart(timeout_seconds=3)["command"] == "restart"
    assert kernel.shutdown(timeout_seconds=4)["command"] == "shutdown"
    assert calls == ["interrupt", "restart", "shutdown"]


def test_scheduler_keeps_current_kernel_generation_when_code_signature_matches(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_control, "fanxiu_behavior_tree_code_signature", lambda: "current")
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: {
            "alive": True,
            "execution_state": "idle",
            "generation": 7,
            "behavior_tree_code_signature": "current",
        },
    )

    result = behavior_tree_control.ensure_scheduler_kernel_code_current(
        entry=object(),
        entry_id="entry",
    )

    assert result["ready"] is True
    assert result["restarted"] is False
    assert result["kernel"]["generation"] == 7


def test_scheduler_replaces_idle_manager_before_job_when_code_is_stale(monkeypatch) -> None:
    statuses = iter((
        {
            "alive": True,
            "execution_state": "idle",
            "generation": 7,
            "manager_pid": 123,
            "behavior_tree_code_signature": "old",
        },
        {
            "alive": True,
            "execution_state": "idle",
            "generation": 1,
            "manager_pid": 456,
            "behavior_tree_code_signature": "current",
        },
    ))
    calls: list[tuple[str, float]] = []

    class Kernel:
        def __init__(self, *, entry_id):
            assert entry_id == "entry"

        def shutdown(self, *, timeout_seconds):
            calls.append(("shutdown", timeout_seconds))
            return {"ok": True}

    monkeypatch.setattr(behavior_tree_control, "fanxiu_behavior_tree_code_signature", lambda: "current")
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: next(statuses),
    )
    kernel_module = importlib.import_module("backend.core.fanxiu.behavior_tree.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_fanxiu_behavior_tree_service",
        lambda _entry, _entry_id: calls.append(("ensure", 0.0)),
    )

    result = behavior_tree_control.ensure_scheduler_kernel_code_current(
        entry=object(),
        entry_id="entry",
    )

    assert result["ready"] is True
    assert result["restarted"] is True
    assert result["kernel"]["generation"] == 1
    assert calls == [("shutdown", 15.0), ("ensure", 0.0)]


def test_scheduler_replaces_stale_manager_even_when_child_is_dead(monkeypatch) -> None:
    statuses = iter((
        {
            "alive": False,
            "execution_state": "dead",
            "generation": 7,
            "manager_pid": 123,
            "behavior_tree_code_signature": "old",
        },
        {
            "alive": True,
            "execution_state": "idle",
            "generation": 1,
            "manager_pid": 456,
            "behavior_tree_code_signature": "current",
        },
    ))
    calls: list[str] = []

    class Kernel:
        def __init__(self, *, entry_id):
            assert entry_id == "entry"

        def shutdown(self, *, timeout_seconds):
            assert timeout_seconds == 15.0
            calls.append("shutdown")
            return {"ok": True, "alive": True}

    monkeypatch.setattr(behavior_tree_control, "fanxiu_behavior_tree_code_signature", lambda: "current")
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: next(statuses),
    )
    kernel_module = importlib.import_module("backend.core.fanxiu.behavior_tree.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_fanxiu_behavior_tree_service",
        lambda *_args, **_kwargs: calls.append("spawn"),
    )

    result = behavior_tree_control.ensure_scheduler_kernel_code_current(entry=object(), entry_id="entry")

    assert result["ready"] is True
    assert result["kernel"]["generation"] == 1
    assert calls == ["shutdown", "spawn"]


def test_scheduler_replaces_legacy_manager_that_cannot_report_loaded_code(monkeypatch) -> None:
    statuses = iter((
        {
            "alive": True,
            "execution_state": "idle",
            "generation": 7,
            "manager_pid": 123,
        },
        {
            "alive": True,
            "execution_state": "idle",
            "generation": 1,
            "manager_pid": 456,
            "behavior_tree_code_signature": "current",
        },
    ))
    calls: list[str] = []

    class Kernel:
        def __init__(self, *, entry_id):
            assert entry_id == "entry"

        def shutdown(self, *, timeout_seconds):
            assert timeout_seconds == 15.0
            calls.append("shutdown")
            return {"ok": True}

    monkeypatch.setattr(behavior_tree_control, "fanxiu_behavior_tree_code_signature", lambda: "current")
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda **_kwargs: next(statuses),
    )
    kernel_module = importlib.import_module("backend.core.fanxiu.behavior_tree.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)
    monkeypatch.setattr(
        behavior_tree_control,
        "ensure_fanxiu_behavior_tree_service",
        lambda _entry, _entry_id: calls.append("ensure"),
    )

    result = behavior_tree_control.ensure_scheduler_kernel_code_current(
        entry=object(),
        entry_id="entry",
    )

    assert result["ready"] is True
    assert result["restarted"] is True
    assert calls == ["shutdown", "ensure"]


def test_windows_interrupt_uses_jupyter_control_channel() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/core/fanxiu/behavior_tree/jupyter_kernel.py").read_text(encoding="utf-8")

    assert 'client.session.msg("interrupt_request"' in source
    assert "client.control_channel.send(message)" in source
    assert "_interrupt_kernel_over_control_channel(connection_path" in source


def test_restart_replaces_kernel_and_old_cell_cannot_rebind_to_new_connection() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/core/fanxiu/behavior_tree/jupyter_kernel.py").read_text(encoding="utf-8")

    assert "connection_snapshot = path.read_bytes()" in source
    assert "connection_changed = path.read_bytes() != connection_snapshot" in source
    assert "当前 cell 已作废" in source


def test_manager_requires_connection_file_before_reporting_kernel_alive() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/core/fanxiu/behavior_tree/jupyter_kernel.py").read_text(encoding="utf-8")

    assert "and connection_path.is_file()" in source
    assert "def shutdown_kernel(" in source
    assert "root.children(recursive=True)" in source
    assert "shutdown_kernel(previous_manager, now=True)" in source
    assert "manager = start_kernel()" in source


def test_scheduler_arbitration_stays_outside_kernel() -> None:
    root = Path(__file__).resolve().parents[2]
    kernel_source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "backend/core/fanxiu/behavior_tree/kernel.py",
            "backend/core/fanxiu/behavior_tree/jupyter_kernel.py",
        )
    )
    scheduler_source = (root / "backend/core/fanxiu/data_annotation/behavior_tree_control.py").read_text(
        encoding="utf-8"
    )

    assert "select_due_data_annotation_scheduler_tasks" not in kernel_source
    assert "scheduler_tasks.json" not in kernel_source
    assert "select_due_data_annotation_scheduler_tasks" in scheduler_source
    assert "submit_runtime_task_cell" in scheduler_source


def test_busy_kernel_preserves_persisted_business_attempt_after_backend_reload(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_control, "read_behavior_tree_runtime_status", lambda path=None: {
        "running": True,
        "status": "running",
        "current_task": "demo",
    })
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy"},
    )
    monkeypatch.setattr(behavior_tree_control, "persist_behavior_tree_runtime_status", lambda *args, **kwargs: None)

    status = behavior_tree_control.behavior_tree_runtime_status()

    assert status["running"] is True
    assert status["status"] == "running"
    assert status["message"] != "执行进程已重载，先前业务任务已结束"
    assert status["current_task"] == "demo"
    assert status["kernel"]["execution_state"] == "busy"


def test_idle_kernel_stops_persisted_business_attempt_after_backend_reload(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_control, "read_behavior_tree_runtime_status", lambda path=None: {
        "running": True,
        "status": "running",
        "current_task": "demo",
    })
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(behavior_tree_control, "persist_behavior_tree_runtime_status", lambda *args, **kwargs: None)

    status = behavior_tree_control.behavior_tree_runtime_status()

    assert status["running"] is False
    assert status["status"] == "stopped"
    assert status["message"] == "执行进程已重载，先前业务任务已结束"
    assert status["current_task"] == "demo"


def test_recent_idle_kernel_waits_for_managed_cell_terminal_writeback(monkeypatch) -> None:
    monkeypatch.setattr(behavior_tree_control.time, "time", lambda: 120.0)
    monkeypatch.setattr(behavior_tree_control, "read_behavior_tree_runtime_status", lambda path=None: {
        "running": True,
        "status": "running",
        "current_task": "demo",
        "updated_at": 100.0,
    })
    monkeypatch.setattr(behavior_tree_control, "behavior_tree_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(
        "backend.core.fanxiu.behavior_tree.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(behavior_tree_control, "persist_behavior_tree_runtime_status", lambda *args, **kwargs: None)

    status = behavior_tree_control.behavior_tree_runtime_status()

    assert status["running"] is True
    assert status["status"] == "running"
    assert status["message"] != "执行进程已重载，先前业务任务已结束"


def test_task_admission_finishes_before_any_game_side_effect() -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    events = []

    def admission(runner, payload):
        events.append("admission")
        runner._persist_scheduler_task_next_time(
            payload["__scheduler_task_id"],
            "2026-07-30 12:30:00",
        )
        return {
            "result": "success",
            "message": "窗口外不操作游戏",
        }

    @register_fanxiu_data_annotation_task_cell(
        "test_admission_job",
        "测试准入作业",
        scheduler_supported=True,
        admission=admission,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        return "success"

    class Runtime:
        def goto_view(self, scene_id):
            events.append(("goto", scene_id))
            if False:
                yield None

    class Runner:
        @staticmethod
        def _persist_scheduler_task_next_time(task_id, next_time):
            events.append(("next_time", task_id, next_time))

        @staticmethod
        def _ensure_world_ready_via_login_game(*_args):
            events.append("ensure_login")
            if False:
                yield None

        @staticmethod
        def _execute_hide_floating_window(*_args):
            events.append("hide_floating")

        @staticmethod
        def _clear_known_blocking_overlay_if_possible(*_args, **_kwargs):
            events.append("clear_overlay")
            if False:
                yield None

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.runtime = Runtime()
    binding.runtime_ctx = {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    result = binding.run_task(
        "test_admission_job",
        {"__scheduler_task_id": "scheduler-job"},
    )

    assert result["result"] == "success"
    assert events == [
        "admission",
        ("next_time", "scheduler-job", "2026-07-30 12:30:00"),
    ]


def test_active_maintenance_gate_defers_scheduled_job_before_handler(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.data_annotation.maintenance import open_maintenance_gate

    world_facts_path = tmp_path / "world_facts.json"
    open_maintenance_gate(world_facts_path)
    events = []

    @register_fanxiu_data_annotation_task_cell(
        "test_maintenance_admission_job",
        "测试维护准入作业",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        return "success"

    class Runner:
        @staticmethod
        def _maintenance_world_facts_path():
            return world_facts_path

        @staticmethod
        def _persist_scheduler_task_next_time(task_id, next_time):
            events.append(("next_time", task_id, next_time))

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.maintenance.maintenance_check_time_text",
        lambda: "2026-08-21 17:00:00",
    )
    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()

    result = binding.run_task(
        "test_maintenance_admission_job",
        {"__scheduler_task_id": "scheduled-job"},
    )

    assert result == {
        "result": "success",
        "message": "检测到游戏维护门闩；测试维护准入作业休眠至 2026-08-21 17:00:00，仅允许系统_维护恢复",
    }
    assert events == [("next_time", "scheduled-job", "2026-08-21 17:00:00")]


def test_first_maintenance_detection_defers_scheduled_job_without_error_retry(monkeypatch, tmp_path) -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.data_annotation.maintenance import (
        FanxiuMaintenanceDetected,
        open_maintenance_gate,
    )

    world_facts_path = tmp_path / "world_facts.json"
    events = []

    @register_fanxiu_data_annotation_task_cell(
        "test_first_maintenance_detection_job",
        "测试首次维护识别作业",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        open_maintenance_gate(world_facts_path, scene_id=546)
        raise FanxiuMaintenanceDetected("检测到游戏维护")

    class Runner:
        @staticmethod
        def _maintenance_world_facts_path():
            return world_facts_path

        @staticmethod
        def _persist_scheduler_task_next_time(task_id, next_time):
            events.append(("next_time", task_id, next_time))

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value.get("result") or ""), str(value.get("message") or "")

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

    monkeypatch.setattr(
        "backend.core.fanxiu.data_annotation.maintenance.maintenance_check_time_text",
        lambda: "2026-08-21 17:00:00",
    )
    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.runtime_ctx = {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    result = binding.run_task(
        "test_first_maintenance_detection_job",
        {"__scheduler_task_id": "scheduled-job"},
    )

    assert result["result"] == "success"
    assert "休眠至 2026-08-21 17:00:00" in result["message"]
    assert events == [
        "business",
        ("next_time", "scheduled-job", "2026-08-21 17:00:00"),
    ]


def test_managed_task_cell_persists_success_terminal_status() -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    @register_fanxiu_data_annotation_task_cell(
        "test_managed_success_terminal",
        "测试成功终态",
        scheduler_supported=True,
    )
    def _handler(_runner, _ctx, _payload, _stop_event):
        return "success"

    class Runner:
        def __init__(self):
            self._lock = threading.RLock()
            self._status = {}
            self.persisted = []

        def _persist_status(self):
            self.persisted.append(dict(self._status))

        def _clear_current_task_locked(self):
            self._status.update({
                "running": False,
                "task_type": "",
                "current_task": "",
                "current_task_id": "",
                "interruptible": True,
            })

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value), ""

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()

    def run_task(_task_type, _payload):
        # Daily handlers commonly persist their precise business terminal on
        # the Runtime and return only the framework-level success marker.
        binding.runner._status["message"] = "今日业务已完成并回到世界 #34"
        return "success"

    binding.run_task = run_task

    result = binding.run_task_cell(
        "test_managed_success_terminal",
        {"__scheduler_task_id": "job-a", "__scheduler_attempt_id": "attempt-a"},
    )

    assert result == {
        "result": "success",
        "message": "今日业务已完成并回到世界 #34",
    }
    assert binding.runner.persisted[0]["status"] == "running"
    assert binding.runner.persisted[-1]["status"] == "success"
    assert binding.runner.persisted[-1]["phase"] == "done"
    assert binding.runner.persisted[-1]["running"] is False
    assert binding.runner.persisted[-1]["scheduler_task_id"] == "job-a"
    assert binding.runner.persisted[-1]["scheduler_attempt_id"] == "attempt-a"
    assert binding.runner.persisted[-1]["scheduler_terminal_result"] == "success"
    assert binding.runner.persisted[-1]["scheduler_terminal_message"] == "今日业务已完成并回到世界 #34"
    assert binding.runner.persisted[-1]["scheduler_terminal_at"]

    binding.run_task = lambda _task_type, _payload: "success"
    assert binding.run_task_cell(
        "test_managed_success_terminal",
        {"__scheduler_task_id": "job-a"},
    ) == {
        "result": "success",
        "message": "测试成功终态完成",
    }


def test_managed_task_cell_persists_error_terminal_status() -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    @register_fanxiu_data_annotation_task_cell(
        "test_managed_error_terminal",
        "测试失败终态",
        scheduler_supported=True,
    )
    def _handler(_runner, _ctx, _payload, _stop_event):
        return "success"

    class Runner:
        def __init__(self):
            self._lock = threading.RLock()
            self._status = {}
            self.persisted = []

        def _persist_status(self):
            self.persisted.append(dict(self._status))

        def _clear_current_task_locked(self):
            self._status.update({
                "running": False,
                "task_type": "",
                "current_task": "",
                "current_task_id": "",
                "interruptible": True,
            })

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()

    def fail(_task_type, _payload):
        raise RuntimeError("boom")

    binding.run_task = fail

    with pytest.raises(RuntimeError, match="boom"):
        binding.run_task_cell(
            "test_managed_error_terminal",
            {"__scheduler_task_id": "job-b", "__scheduler_attempt_id": "attempt-b"},
        )

    assert binding.runner.persisted[-1]["status"] == "error"
    assert binding.runner.persisted[-1]["phase"] == "error"
    assert binding.runner.persisted[-1]["running"] is False
    assert binding.runner.persisted[-1]["error"] == "boom"
    assert binding.runner.persisted[-1]["scheduler_task_id"] == "job-b"
    assert binding.runner.persisted[-1]["scheduler_attempt_id"] == "attempt-b"
    assert binding.runner.persisted[-1]["scheduler_terminal_result"] == "error"
    assert binding.runner.persisted[-1]["scheduler_terminal_message"] == "boom"


def test_recovered_emulator_restart_schedules_login_and_ends_old_gui_transaction() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.data_annotation.popup_guard import FanxiuEmulatorRestartRequired

    events: list[str] = []

    @register_fanxiu_data_annotation_task_cell(
        "test_recovered_emulator_restart",
        "测试模拟器恢复",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        raise FanxiuEmulatorRestartRequired(
            "模拟器已恢复，旧事务作废",
            recovery_succeeded=True,
        )

    class Runner:
        @staticmethod
        def _schedule_login_job_first():
            events.append("schedule_login")

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value.get("result") or ""), str(value.get("message") or "")

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.runtime_ctx = {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    with pytest.raises(FanxiuEmulatorRestartRequired) as captured:
        binding.run_task(
            "test_recovered_emulator_restart",
            {"__scheduler_task_id": "scheduled-job"},
        )

    assert captured.value.recovery_succeeded is True
    assert captured.value.detail == (
        "模拟器已恢复，旧事务作废；已将“登录”排到现有 next_time 队首，当前业务仍须整单重跑"
    )
    assert events == ["business", "schedule_login"]


def test_scheduler_support_does_not_imply_any_scene_lifecycle() -> None:
    from backend.core.fanxiu.data_annotation.jobs import (
        get_fanxiu_data_annotation_task_cell_definition,
        register_fanxiu_data_annotation_task_cell,
    )

    @register_fanxiu_data_annotation_task_cell(
        "test_scheduler_without_scene_policy",
        "测试无场景策略作业",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        return "success"

    definition = get_fanxiu_data_annotation_task_cell_definition("test_scheduler_without_scene_policy")

    assert definition is not None
    assert definition.scheduler_supported is True
    assert not hasattr(definition, "lifecycle")


def test_scheduler_task_cell_has_no_fixed_business_pre_or_post_actions() -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    events = []

    @register_fanxiu_data_annotation_task_cell(
        "test_scheduler_without_navigation",
        "测试调度器无导航",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        return "success"

    class Runner:
        @staticmethod
        def _execute_hide_floating_window(_ctx, _stop_event):
            raise AssertionError("通用 Cell 包装层不得隐藏悬浮窗")

        @staticmethod
        def _clear_known_blocking_overlay_if_possible(_ctx, _stop_event, *, label):
            raise AssertionError("通用 Cell 包装层不得清理遮挡")
            yield label

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value), ""

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

        @staticmethod
        def _cleanup_failed_scheduler_task_to_scene(**_kwargs):
            raise AssertionError("通用 Cell 包装层不得清理业务场景")

    class Runtime:
        @staticmethod
        def goto_view(_scene_id):
            raise AssertionError("通用 Cell 包装层不得导航业务场景")
            yield

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.runtime = Runtime()
    binding.runtime_ctx = {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        if not isinstance(value, GeneratorType):
            return value
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    assert binding.run_task(
        "test_scheduler_without_navigation",
        {"__scheduler_task_id": "scheduled-job"},
    ) == {"result": "success", "message": ""}
    assert events == ["business"]


def _task_context_binding(runner, *, runtime_ctx=None):
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = runner
    binding.runtime_ctx = runtime_ctx if runtime_ctx is not None else {}
    binding.stop_event = threading.Event()

    def drain(value, **_kwargs):
        if not isinstance(value, GeneratorType):
            return value
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain
    return binding


def test_formal_lingquan_completion_sees_scheduler_task_id_in_runtime_context() -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntime
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    writes = []

    class Runtime:
        @staticmethod
        def goto_view(scene_id):
            assert scene_id == 34
            if False:
                yield None

    class Runner:
        @staticmethod
        def daily_lingquan_admission(_payload):
            return None

        def _execute_daily_lingquan_task(self, ctx, _stop_event, _payload):
            BehaviorTreeRuntime(self, ctx).set_next_time("2026-08-26 20:30:00")
            if False:
                yield None
            return {"result": "success", "message": "灵泉完成"}

        @staticmethod
        def _fanxiu_runtime(_ctx, stop_event=None):
            assert stop_event is not None
            return Runtime()

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value.get("result") or "success"), str(value.get("message") or "")

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

        @staticmethod
        def _persist_scheduler_task_next_time(task_id, next_time):
            writes.append((task_id, next_time))

    ctx = {}
    result = _task_context_binding(Runner(), runtime_ctx=ctx).run_task(
        "daily_lingquan",
        {"__scheduler_task_id": "legacy-daily-lingquan"},
    )

    assert result == {"result": "success", "message": "灵泉完成"}
    assert writes == [("legacy-daily-lingquan", "2026-08-26 20:30:00")]
    assert ctx == {}


def test_generator_job_keeps_scheduler_task_context_until_completion() -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntime
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell

    writes = []

    @register_fanxiu_data_annotation_task_cell(
        "test_generator_task_context",
        "测试生成器任务上下文",
        scheduler_supported=True,
    )
    def handler(runner, ctx, _payload, _stop_event):
        yield "tick"
        BehaviorTreeRuntime(runner, ctx).set_next_time("2026-08-27 00:00:00")
        return "success"

    class Runner:
        @staticmethod
        def _persist_scheduler_task_next_time(task_id, next_time):
            writes.append((task_id, next_time))

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value), ""

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

    result = _task_context_binding(Runner()).run_task(
        "test_generator_task_context",
        {"__scheduler_task_id": "generator-job"},
    )

    assert result == {"result": "success", "message": ""}
    assert writes == [("generator-job", "2026-08-27 00:00:00")]


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_task_payload_context_restores_after_error_or_interrupt(error_type) -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell

    task_type = f"test_task_context_{error_type.__name__.lower()}"

    @register_fanxiu_data_annotation_task_cell(
        task_type,
        "测试任务上下文清理",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        yield "tick"
        raise error_type("stop")

    class Runner:
        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value), ""

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

    previous_payload = {"owner": "previous-job"}
    ctx = {"attrs": {"payload": previous_payload, "keep": True}}
    binding = _task_context_binding(Runner(), runtime_ctx=ctx)

    with pytest.raises(error_type, match="stop"):
        binding.run_task(task_type, {"__scheduler_task_id": "failing-job"})

    assert ctx["attrs"]["payload"] is previous_payload
    assert ctx["attrs"]["keep"] is True


def test_synchronous_job_can_persist_its_own_scheduler_next_time() -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import BehaviorTreeRuntime
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell

    writes = []

    @register_fanxiu_data_annotation_task_cell(
        "test_sync_task_context",
        "测试同步任务上下文",
        scheduler_supported=True,
    )
    def handler(runner, ctx, _payload, _stop_event):
        BehaviorTreeRuntime(runner, ctx).set_next_time(None)
        return "success"

    class Runner:
        @staticmethod
        def _persist_scheduler_task_next_time(task_id, next_time):
            writes.append((task_id, next_time))

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value), ""

        @staticmethod
        def _task_timeout_seconds(_payload):
            return 60.0

        @staticmethod
        def _runtime_guard_override_from_payload(_payload):
            return None

    result = _task_context_binding(Runner()).run_task(
        "test_sync_task_context",
        {"__scheduler_task_id": "sync-job"},
    )

    assert result == {"result": "success", "message": ""}
    assert writes == [("sync-job", None)]


def test_generic_runtime_task_path_keeps_payload_for_generator_completion() -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
        BehaviorTreeRuntime,
        BehaviorTreeRuntimeRunner,
    )
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell

    writes = []

    @register_fanxiu_data_annotation_task_cell(
        "test_generic_generator_task_context",
        "测试通用生成器任务上下文",
        scheduler_supported=True,
    )
    def handler(runner, ctx, _payload, _stop_event):
        yield "tick"
        BehaviorTreeRuntime(runner, ctx).set_next_time("2026-08-28 00:00:00")
        return "success"

    runner = BehaviorTreeRuntimeRunner()
    runner._persist_scheduler_task_next_time = lambda task_id, next_time: writes.append((task_id, next_time))
    ctx = {}
    result = runner._execute_runtime_task(
        ctx,
        "test_generic_generator_task_context",
        {"__scheduler_task_id": "generic-generator-job"},
        threading.Event(),
    )

    assert isinstance(result, GeneratorType)
    assert _task_context_binding(runner).run(result) == "success"
    assert writes == [("generic-generator-job", "2026-08-28 00:00:00")]
    assert ctx == {}


def test_world_navigation_jobs_are_registered_without_framework_lifecycle_metadata() -> None:
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        get_fanxiu_data_annotation_task_cell_definition,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    start_and_finish_at_world = {
        "weekly_gift_code", "daily_mozu", "daily_activity", "weekly_activity",
        "daily_redpacket", "daily_signup", "moyu_signup", "daily_boss",
        "daily_experience", "lingzhuang_strengthening", "daily_youli",
        "daily_shuangxiu", "daily_baiye", "daily_green_bottle_baiye",
        "daily_gongfeng", "daily_xianshi",
        "xianshi_weekly_resources", "daily_lundao", "daily_xianyuan_duel",
        "daily_mojie_raid", "daily_weekly_dungeon", "weekly_hanli", "daily_vip",
        "daily_signin", "daily_xuanhuang", "daily_dongtian",
        "daily_dongtian_clear", "daily_lingmai", "daily_lingmai_clear",
        "daily_dungeon", "daily_assistant", "lilian_claim", "lilian_event",
        "xianqiao_trial", "mail_selective_claim", "prayer_daily_resource",
        "xianfu_learn_skill", "penglai_xianzang_config",
        "penglai_xianzang_lottery", "xutian_palace_rankings",
    }
    finish_at_world = {
        "login_game", "daily_zhenxie", "daily_xianyuan", "daily_daofa",
        "daily_lingquan", "weekly_shengzu",
        "xianfu_visit_partner",
    }

    for task_type in start_and_finish_at_world:
        definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
        assert definition is not None, task_type
        assert not hasattr(definition, "lifecycle")
    for task_type in finish_at_world:
        definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
        assert definition is not None, task_type
        assert not hasattr(definition, "lifecycle")
    for task_type in {"daily_xianmeng", "jianling_cuiling", "tianjige_forum_quiz"}:
        definition = get_fanxiu_data_annotation_task_cell_definition(task_type)
        assert definition is not None, task_type
        assert "goto_view(34)" not in inspect.getsource(definition.handler)


def test_xianfu_visit_wrapper_does_not_repeat_business_owned_cleanup() -> None:
    from backend.core.fanxiu.data_annotation.default_jobs import (
        register_fanxiu_data_annotation_default_runtime_jobs,
    )
    from backend.core.fanxiu.data_annotation.jobs import (
        get_fanxiu_data_annotation_task_cell_definition,
    )

    register_fanxiu_data_annotation_default_runtime_jobs()
    definition = get_fanxiu_data_annotation_task_cell_definition(
        "xianfu_visit_partner"
    )
    assert definition is not None
    source = inspect.getsource(definition.handler)
    assert source.count("goto_view(34)") == 1
    assert "_execute_xianfu_visit_partner_task" in source


def test_run_task_cell_strips_business_terminal_fields() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    class Runner:
        def __init__(self):
            self._lock = threading.RLock()
            self._status = {}

        def _persist_status(self):
            pass

        def _clear_current_task_locked(self):
            self._status.update({
                "running": False,
                "task_type": "",
                "current_task": "",
                "current_task_id": "",
                "interruptible": True,
            })

        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value["result"]), str(value["message"])

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.run_task = lambda *_args, **_kwargs: {
        "result": "success",
        "message": "done",
        "next_time": "2026-07-25 20:30:00",
        "answers": 6,
        "scheduler_incident": {"kind": "window_expired"},
    }

    assert binding.run_task_cell("daily_lingquan", {}) == {
        "result": "success",
        "message": "done",
        "scheduler_incident": {"kind": "window_expired"},
    }


def test_jupyter_binding_end_cell_tolerates_missing_pre_run_cell() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    class Runner:
        def __init__(self):
            self._lock = threading.RLock()
            self._stop_event = None
            self._status = {}

        def _clear_current_task_locked(self):
            return None

        def _persist_status(self):
            return None

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.stop_event = threading.Event()
    binding.execution_lock = threading.RLock()
    binding._cell_lock_acquired = False
    binding._managed_task_cell = False
    result = type("Result", (), {"error_in_exec": None, "error_before_exec": None})()

    binding.end_cell(result)

    assert binding.runner._status["status"] == "success"


def test_jupyter_binding_end_cell_releases_acquired_lock_once() -> None:
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import FanxiuJupyterBinding

    class Lock:
        def __init__(self):
            self.releases = 0

        def release(self):
            self.releases += 1

    class Runner:
        def __init__(self):
            self._lock = threading.RLock()
            self._stop_event = None
            self._status = {}

        def _clear_current_task_locked(self):
            return None

        def _persist_status(self):
            return None

    binding = object.__new__(FanxiuJupyterBinding)
    binding.runner = Runner()
    binding.stop_event = threading.Event()
    binding.execution_lock = Lock()
    binding._cell_lock_acquired = True
    binding._managed_task_cell = False
    result = type("Result", (), {"error_in_exec": None, "error_before_exec": None})()

    binding.end_cell(result)
    binding.end_cell(result)

    assert binding.execution_lock.releases == 1


def test_submit_code_cell_records_log_without_removed_mode_field(monkeypatch) -> None:
    from backend.api import fanxiu
    from backend.core.fanxiu.data_annotation.models import FanxiuBehaviorTreeRuntimeCodeCellRequest

    observed = {}
    monkeypatch.setattr(fanxiu, "_sync_behavior_tree_runtime_runner_to_core", lambda: None)
    monkeypatch.setattr(fanxiu, "_runtime_log_items_for_cell", lambda: [])
    monkeypatch.setattr(
        fanxiu._behavior_tree_framework,
        "submit_code_cell",
        lambda **_kwargs: {"status": "success"},
    )

    def record(status, *, title, source, before_keys):
        observed.update(title=title, source=source, before_keys=before_keys)
        return status

    monkeypatch.setattr(fanxiu, "_record_runtime_cell_log", record)
    request = FanxiuBehaviorTreeRuntimeCodeCellRequest(entry_id="entry-1", code="1 + 1")

    result = fanxiu._submit_data_annotation_code_cell(object(), "entry-1", request)

    assert result == {"status": "success"}
    assert observed["title"] == "代码 cell"
