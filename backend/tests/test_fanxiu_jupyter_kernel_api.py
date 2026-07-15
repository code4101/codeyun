from __future__ import annotations

import importlib
import threading
from types import GeneratorType
from pathlib import Path

from backend.core.fanxiu.data_annotation import runtime_framework
from backend.core.fanxiu.data_annotation import runtime_control
from backend.core.fanxiu.runtime.kernel import FanxiuKernel


def test_task_is_only_an_ordinary_cell_constructor() -> None:
    cell = FanxiuKernel(entry_id="entry").task("detect_scene", {"probe": True})

    assert cell.code == (
        "# fanxiu:managed-task-cell\n"
        "run_task_cell('detect_scene', {'probe': True})"
    )
    assert not hasattr(cell, "submit")


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

    kernel_module = importlib.import_module("backend.core.fanxiu.runtime.kernel")
    monkeypatch.setattr(kernel_module, "FanxiuKernel", Kernel)
    result = runtime_framework.submit_task_cell(
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


def test_active_kernel_path_has_no_manual_queue_or_kernel_lock() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "backend/core/fanxiu/runtime/kernel.py",
        root / "backend/core/fanxiu/runtime/jupyter_kernel.py",
        root / "backend/core/fanxiu/data_annotation/runtime_framework.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    for forbidden in ("manual_jobs", "claim", "requeue", "dedupe", "job_group_isolation"):
        assert forbidden not in source


def test_kernel_status_keeps_kernel_and_business_runtime_orthogonal(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "idle"},
    )
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.behavior_tree.fanxiu_data_annotation_runtime_status",
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
        "backend.core.fanxiu.runtime.jupyter_kernel.send_fanxiu_kernel_manager_command",
        send,
    )

    kernel = FanxiuKernel()
    assert kernel.interrupt(timeout_seconds=2)["command"] == "interrupt"
    assert kernel.restart(timeout_seconds=3)["command"] == "restart"
    assert kernel.shutdown(timeout_seconds=4)["command"] == "shutdown"
    assert calls == ["interrupt", "restart", "shutdown"]


def test_windows_interrupt_uses_jupyter_control_channel() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/core/fanxiu/runtime/jupyter_kernel.py").read_text(encoding="utf-8")

    assert 'client.session.msg("interrupt_request"' in source
    assert "client.control_channel.send(message)" in source
    assert "_interrupt_kernel_over_control_channel(connection_path" in source


def test_restart_replaces_kernel_and_old_cell_cannot_rebind_to_new_connection() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "backend/core/fanxiu/runtime/jupyter_kernel.py").read_text(encoding="utf-8")

    assert "connection_snapshot = path.read_bytes()" in source
    assert "connection_changed = path.read_bytes() != connection_snapshot" in source
    assert "当前 cell 已作废" in source
    assert "manager.shutdown_kernel(now=True)" in source
    assert "manager = start_kernel()" in source


def test_scheduler_arbitration_stays_outside_kernel() -> None:
    root = Path(__file__).resolve().parents[2]
    kernel_source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "backend/core/fanxiu/runtime/kernel.py",
            "backend/core/fanxiu/runtime/jupyter_kernel.py",
        )
    )
    scheduler_source = (root / "backend/core/fanxiu/data_annotation/runtime_control.py").read_text(
        encoding="utf-8"
    )

    assert "select_due_scheduled_tasks" not in kernel_source
    assert "scheduler_tasks.json" not in kernel_source
    assert "select_due_scheduled_tasks" in scheduler_source
    assert "submit_runtime_task_cell" in scheduler_source


def test_busy_kernel_does_not_revive_persisted_business_attempt(monkeypatch) -> None:
    monkeypatch.setattr(runtime_control, "read_runtime_status", lambda path=None: {
        "running": True,
        "status": "running",
        "current_task": "demo",
    })
    monkeypatch.setattr(runtime_control, "fanxiu_runtime_runner_status", lambda: {
        "running": False,
        "status": "idle",
        "logs": [],
        "guard_items": {},
    })
    monkeypatch.setattr(runtime_control, "is_data_annotation_runtime_live_empty", lambda status: True)
    monkeypatch.setattr(
        "backend.core.fanxiu.runtime.jupyter_kernel.fanxiu_kernel_manager_status",
        lambda: {"alive": True, "execution_state": "busy"},
    )
    monkeypatch.setattr(runtime_control, "persist_runtime_status", lambda *args, **kwargs: None)

    status = runtime_control.runtime_status()

    assert status["running"] is False
    assert status["status"] == "stopped"
    assert status["message"] == "执行进程已重载，先前业务任务已结束"
    assert status["current_task"] == "demo"
    assert status["kernel"]["execution_state"] == "busy"


def test_formal_task_resets_to_stable_anchor_before_business_steps() -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.runtime.jupyter_kernel import FanxiuJupyterBinding

    events = []

    @register_fanxiu_data_annotation_task_cell("test_atomic_job", "测试原子作业", scheduler_supported=True)
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        return "success"

    class Runtime:
        def goto_view(self, scene_id):
            events.append(("reset", scene_id))
            if False:
                yield None

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

    assert binding.run_task("test_atomic_job", {}) == "success"
    assert events == [("reset", 34), "business", ("reset", 34)]


def test_manual_check_task_keeps_human_inspection_scene() -> None:
    from backend.core.fanxiu.data_annotation.jobs import register_fanxiu_data_annotation_task_cell
    from backend.core.fanxiu.runtime.jupyter_kernel import FanxiuJupyterBinding

    events = []

    @register_fanxiu_data_annotation_task_cell(
        "test_manual_check_job",
        "测试人工检查作业",
        scheduler_supported=True,
    )
    def handler(_runner, _ctx, _payload, _stop_event):
        events.append("business")
        return {"result": "manual_check_pending", "message": "请人工检查"}

    class Runtime:
        def goto_view(self, scene_id):
            events.append(("reset", scene_id))
            if False:
                yield None

    class Runner:
        @staticmethod
        def _normalize_runtime_task_result(value):
            return str(value["result"]), str(value["message"])

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
        if not isinstance(value, GeneratorType):
            return value
        while True:
            try:
                next(value)
            except StopIteration as stop:
                return stop.value

    binding.run = drain

    assert binding.run_task("test_manual_check_job", {}) == {
        "result": "manual_check_pending",
        "message": "请人工检查",
    }
    assert events == [("reset", 34), "business"]


def test_jupyter_binding_end_cell_tolerates_missing_pre_run_cell() -> None:
    from backend.core.fanxiu.runtime.jupyter_kernel import FanxiuJupyterBinding

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
    from backend.core.fanxiu.runtime.jupyter_kernel import FanxiuJupyterBinding

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
    from backend.core.fanxiu.data_annotation.models import FanxiuDataAnnotationRuntimeCodeCellRequest

    observed = {}
    monkeypatch.setattr(fanxiu, "_sync_data_annotation_runtime_runner_to_core", lambda: None)
    monkeypatch.setattr(fanxiu, "_runtime_log_items_for_cell", lambda: [])
    monkeypatch.setattr(
        fanxiu._runtime_framework,
        "submit_code_cell",
        lambda **_kwargs: {"status": "success"},
    )

    def record(status, *, title, source, before_keys):
        observed.update(title=title, source=source, before_keys=before_keys)
        return status

    monkeypatch.setattr(fanxiu, "_record_runtime_cell_log", record)
    request = FanxiuDataAnnotationRuntimeCodeCellRequest(entry_id="entry-1", code="1 + 1")

    result = fanxiu._submit_data_annotation_code_cell(object(), "entry-1", request)

    assert result == {"status": "success"}
    assert observed["title"] == "代码 cell"
