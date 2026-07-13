from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
from types import GeneratorType
from typing import Any


def fanxiu_jupyter_connection_path() -> Path:
    from backend.core.fanxiu.runtime.behavior_tree import fanxiu_data_annotation_runtime_dir

    return fanxiu_data_annotation_runtime_dir() / "jupyter-kernel.json"


class FanxiuJupyterBinding:
    """Objects preloaded into the real IPython kernel namespace."""

    def __init__(self, runner: Any, entry: Any, entry_id: str, asset_tree_path: Path) -> None:
        self.runner = runner
        self.entry = entry
        self.entry_id = str(entry_id)
        self.asset_tree_path = Path(asset_tree_path)
        self.execution_lock = getattr(runner, "_cell_execution_lock", threading.RLock())
        self.stop_event = threading.Event()
        self.runtime_ctx: dict[str, Any] = {}
        self.runtime: Any = None
        self.ctx: Any = None
        self.refresh()

    def refresh(self) -> "FanxiuJupyterBinding":
        from backend.core.fanxiu.data_annotation.debug_eval import DataAnnotationRuntimeDebugContext

        tree = self.runner._load_asset_tree(self.asset_tree_path)
        self.runtime_ctx = {
            "entry": self.entry,
            "entry_id": self.entry_id,
            "asset_tree": tree,
            "asset_tree_path": self.asset_tree_path,
            "images": self.runner._index_images(tree),
        }
        self.runner._require_assets(self.runtime_ctx)
        self.stop_event = threading.Event()
        self.runtime = self.runner._fanxiu_runtime(
            self.runtime_ctx,
            self.asset_tree_path,
            stop_event=self.stop_event,
        )
        self.ctx = DataAnnotationRuntimeDebugContext(
            self.runner,
            self.runtime_ctx,
            self.stop_event,
            readonly=False,
        )
        return self

    def begin_cell(self, shell: Any) -> None:
        self.execution_lock.acquire()
        self.refresh()
        with self.runner._lock:
            self.runner._stop_event = self.stop_event
            self.runner._set_status_locked(
                "running",
                "Jupyter cell 执行中",
                phase="jupyter_cell",
            )
        shell.user_ns.update(self.namespace())

    def end_cell(self, result: Any) -> None:
        error = getattr(result, "error_in_exec", None) or getattr(result, "error_before_exec", None)
        with self.runner._lock:
            self.runner._stop_event = None
            self.runner._clear_current_task_locked()
            self.runner._status.update({
                "status": "error" if error else "success",
                "phase": "error" if error else "done",
                "message": f"{type(error).__name__}: {error}" if error else "Jupyter cell 执行完成",
                "error": f"{type(error).__name__}: {error}" if error else "",
                "finished_at": time.time(),
                "updated_at": time.time(),
            })
        self.runner._persist_status()
        self.execution_lock.release()

    def namespace(self) -> dict[str, Any]:
        return {
            "fanxiu": self,
            "runner": self.runner,
            "runtime": self.runtime,
            "ctx": self.ctx,
            "run": self.run,
            "run_task": self.run_task,
            "refresh": self.refresh,
        }

    def run(self, value: Any, *, label: str = "Jupyter cell") -> Any:
        if callable(value) and not isinstance(value, GeneratorType):
            value = value()
        if not isinstance(value, GeneratorType):
            return value
        return self.runner._run_runtime_behavior_tree(
            runtime_ctx=self.runtime_ctx,
            asset_tree_path=self.asset_tree_path,
            stop_event=self.stop_event,
            action=lambda: value,
            label=label,
            tick_seconds=0.2,
            max_runtime_seconds=21600.0,
        )

    def run_task(self, task_type: str, payload: dict[str, Any] | None = None) -> Any:
        from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition

        definition = get_fanxiu_data_annotation_task_cell_definition(str(task_type or ""))
        if definition is None:
            raise ValueError(f"未知凡修 task cell：{task_type}")
        normalized = dict(payload or {})
        if callable(definition.normalize_payload):
            normalized = definition.normalize_payload(normalized)
        value = definition.handler(
            self.runner,
            self.runtime_ctx,
            normalized,
            self.stop_event,
        )
        return self.run(value, label=definition.label)


def run_fanxiu_jupyter_kernel_service(*, entry_id: str, tick_seconds: float = 1.0) -> None:
    from ipykernel.kernelapp import IPKernelApp

    from backend.core.fanxiu.runtime.behavior_tree import (
        FANXIU_EMBEDDED_SERVICE_ENV,
        data_annotation_asset_tree_path,
        get_fanxiu_runtime_runner,
        resolve_fanxiu_entry,
    )

    os.environ[FANXIU_EMBEDDED_SERVICE_ENV] = "1"
    resolved_entry_id = str(entry_id)
    entry = resolve_fanxiu_entry(resolved_entry_id)
    asset_tree_path = data_annotation_asset_tree_path(resolved_entry_id)
    runner = get_fanxiu_runtime_runner()

    connection_path = fanxiu_jupyter_connection_path()
    connection_path.parent.mkdir(parents=True, exist_ok=True)
    connection_path.unlink(missing_ok=True)

    app = IPKernelApp.instance(connection_file=str(connection_path))
    app.initialize([])
    binding = FanxiuJupyterBinding(runner, entry, resolved_entry_id, asset_tree_path)
    app.shell.user_ns.update(binding.namespace())
    app.shell.events.register("pre_run_cell", lambda _info: binding.begin_cell(app.shell))
    app.shell.events.register("post_run_cell", binding.end_cell)

    runner.ensure_service(
        entry=entry,
        entry_id=resolved_entry_id,
        asset_tree_path=asset_tree_path,
        tick_seconds=max(0.2, float(tick_seconds or 1.0)),
    )

    def stop_kernel_when_service_stops() -> None:
        while True:
            time.sleep(0.5)
            if not bool(runner.status().get("service_running")):
                try:
                    app.io_loop.add_callback(app.io_loop.stop)
                except Exception:
                    pass
                return

    threading.Thread(target=stop_kernel_when_service_stops, daemon=True).start()
    try:
        app.start()
    finally:
        runner.stop_service(timeout_seconds=3.0)
        connection_path.unlink(missing_ok=True)


def execute_fanxiu_jupyter_cell(
    code: str,
    *,
    timeout_seconds: float = 120.0,
    connection_path: Path | None = None,
) -> dict[str, Any]:
    from jupyter_client import BlockingKernelClient

    path = Path(connection_path or fanxiu_jupyter_connection_path())
    deadline = time.time() + max(5.0, float(timeout_seconds or 120.0))
    while not path.is_file() and time.time() < deadline:
        time.sleep(0.1)
    if not path.is_file():
        raise RuntimeError(f"凡修 Jupyter kernel 尚未就绪：{path}")

    client = BlockingKernelClient(connection_file=str(path))
    client.load_connection_file()
    client.start_channels()
    outputs: list[str] = []
    error: dict[str, Any] | None = None
    execution_count: int | None = None
    try:
        client.wait_for_ready(timeout=min(15.0, max(1.0, deadline - time.time())))
        msg_id = client.execute(str(code or ""), allow_stdin=False, stop_on_error=True)
        idle = False
        while not idle:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("凡修 Jupyter cell 执行超时")
            try:
                message = client.get_iopub_msg(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if str(message.get("parent_header", {}).get("msg_id") or "") != msg_id:
                continue
            msg_type = str(message.get("msg_type") or "")
            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            if msg_type == "status" and content.get("execution_state") == "idle":
                idle = True
            elif msg_type == "stream":
                outputs.append(str(content.get("text") or ""))
            elif msg_type in {"execute_result", "display_data"}:
                data = content.get("data") if isinstance(content.get("data"), dict) else {}
                if "text/plain" in data:
                    outputs.append(str(data["text/plain"]))
                execution_count = content.get("execution_count") or execution_count
            elif msg_type == "error":
                error = {
                    "ename": str(content.get("ename") or "Error"),
                    "evalue": str(content.get("evalue") or ""),
                    "traceback": list(content.get("traceback") or []),
                }
        reply = client.get_shell_msg(timeout=max(1.0, deadline - time.time()))
        reply_content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
        execution_count = reply_content.get("execution_count") or execution_count
        if str(reply_content.get("status") or "") == "error" and error is None:
            error = {
                "ename": str(reply_content.get("ename") or "Error"),
                "evalue": str(reply_content.get("evalue") or ""),
                "traceback": list(reply_content.get("traceback") or []),
            }
    finally:
        client.stop_channels()

    output = "".join(outputs).strip()
    if error:
        return {
            "status": "error",
            "phase": "error",
            "message": f"{error['ename']}: {error['evalue']}",
            "error": error,
            "output": output,
            "execution_count": execution_count,
        }
    return {
        "status": "success",
        "phase": "done",
        "message": "Jupyter cell 执行完成",
        "error": "",
        "output": output,
        "execution_count": execution_count,
    }
