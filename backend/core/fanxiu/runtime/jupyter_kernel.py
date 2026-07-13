from __future__ import annotations

import json
import os
import queue
import ast
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
        self._asset_signature: tuple[int, int] | None = None
        self._cached_tree: list[dict[str, Any]] | None = None
        self._cached_images: dict[int, dict[str, Any]] | None = None
        self.refresh()

    def refresh(self, *, stop_event: threading.Event | None = None) -> "FanxiuJupyterBinding":
        from backend.core.fanxiu.data_annotation.debug_eval import DataAnnotationRuntimeDebugContext

        stat = self.asset_tree_path.stat()
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
        if signature != self._asset_signature or self._cached_tree is None or self._cached_images is None:
            tree = self.runner._load_asset_tree(self.asset_tree_path)
            images = self.runner._index_images(tree)
            probe_ctx = {
                "entry": self.entry,
                "entry_id": self.entry_id,
                "asset_tree": tree,
                "asset_tree_path": self.asset_tree_path,
                "images": images,
            }
            self.runner._require_assets(probe_ctx)
            self._asset_signature = signature
            self._cached_tree = tree
            self._cached_images = images
        self.runtime_ctx.clear()
        self.runtime_ctx.update({
            "entry": self.entry,
            "entry_id": self.entry_id,
            "asset_tree": self._cached_tree,
            "asset_tree_path": self.asset_tree_path,
            "images": self._cached_images,
        })
        self.stop_event = stop_event or threading.Event()
        self.runtime = self.runner._fanxiu_runtime(
            self.runtime_ctx,
            self.asset_tree_path,
            stop_event=self.stop_event,
        )
        if self.ctx is None:
            self.ctx = DataAnnotationRuntimeDebugContext(
                self.runner,
                self.runtime_ctx,
                self.stop_event,
                readonly=False,
            )
        else:
            self.ctx.rebind(self.runtime_ctx, self.stop_event, readonly=False)
        return self

    def begin_cell(self, info: Any, shell: Any) -> None:
        self.execution_lock.acquire()
        try:
            source = str(getattr(info, "raw_cell", "") or "")
            self._managed_task_cell = source.lstrip().startswith("# fanxiu:managed-task-cell")
            self._cell_isolation_token = ""
            if source.lstrip().startswith("# fanxiu:manual-code-cell isolate=1"):
                from backend.core.fanxiu.runtime.behavior_tree import acquire_fanxiu_job_group_isolation

                self._cell_isolation_token = acquire_fanxiu_job_group_isolation(
                    reason="jupyter_code_cell",
                    ttl_seconds=21600.0,
                )
            active_stop_event = getattr(self.runner, "_stop_event", None) if self._managed_task_cell else None
            self.refresh(stop_event=active_stop_event if isinstance(active_stop_event, threading.Event) else None)
            if not self._managed_task_cell:
                with self.runner._lock:
                    self.runner._stop_event = self.stop_event
                    self.runner._set_status_locked(
                        "running",
                        "Jupyter cell 执行中",
                        phase="jupyter_cell",
                    )
            shell.user_ns.update(self.namespace())
        except Exception:
            isolation_token = str(getattr(self, "_cell_isolation_token", "") or "")
            if isolation_token:
                from backend.core.fanxiu.runtime.behavior_tree import release_fanxiu_job_group_isolation

                release_fanxiu_job_group_isolation(isolation_token)
                self._cell_isolation_token = ""
            self.execution_lock.release()
            raise

    def end_cell(self, result: Any) -> None:
        error = getattr(result, "error_in_exec", None) or getattr(result, "error_before_exec", None)
        try:
            if not getattr(self, "_managed_task_cell", False):
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
        finally:
            isolation_token = str(getattr(self, "_cell_isolation_token", "") or "")
            if isolation_token:
                from backend.core.fanxiu.runtime.behavior_tree import release_fanxiu_job_group_isolation

                release_fanxiu_job_group_isolation(isolation_token)
                self._cell_isolation_token = ""
            self.execution_lock.release()

    def namespace(self) -> dict[str, Any]:
        return {
            "fanxiu": self,
            "runner": self.runner,
            "runtime": self.runtime,
            "ctx": self.ctx,
            "run": self.run,
            "run_task": self.run_task,
            "run_task_cell": self.run_task_cell,
            "refresh": self.refresh,
        }

    def run(
        self,
        value: Any,
        *,
        label: str = "Jupyter cell",
        tick_seconds: float = 0.2,
        max_runtime_seconds: float = 21600.0,
        guard_override: bool | None = None,
    ) -> Any:
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
            tick_seconds=tick_seconds,
            max_runtime_seconds=max_runtime_seconds,
            guard_override=guard_override,
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
        return self.run(
            value,
            label=definition.label,
            tick_seconds=max(0.1, float(normalized.get("__tick_seconds") or 1.0)),
            max_runtime_seconds=self.runner._task_timeout_seconds(normalized),
            guard_override=self.runner._runtime_guard_override_from_payload(normalized),
        )

    def run_task_cell(self, task_type: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
        result = self.run_task(task_type, payload)
        result_name, message = self.runner._normalize_runtime_task_result(result)
        return {"result": str(result_name or "success"), "message": str(message or "")}

    def scene(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ctx.scene(*args, **kwargs)

    def go(self, scene: int | str, **options: Any) -> Any:
        return self.ctx.go(scene, **options)

    task = run_task


def run_fanxiu_jupyter_kernel_service(*, entry_id: str, tick_seconds: float = 1.0) -> None:
    from ipykernel.kernelapp import IPKernelApp

    from backend.core.fanxiu.runtime.behavior_tree import (
        FANXIU_EMBEDDED_SERVICE_ENV,
        data_annotation_asset_tree_path,
        get_fanxiu_runtime_runner,
        resolve_fanxiu_entry,
        ensure_fanxiu_runtime_jobs_registered,
    )

    os.environ[FANXIU_EMBEDDED_SERVICE_ENV] = "1"
    resolved_entry_id = str(entry_id)
    entry = resolve_fanxiu_entry(resolved_entry_id)
    asset_tree_path = data_annotation_asset_tree_path(resolved_entry_id)
    runner = get_fanxiu_runtime_runner()
    ensure_fanxiu_runtime_jobs_registered()

    connection_path = fanxiu_jupyter_connection_path()
    connection_path.parent.mkdir(parents=True, exist_ok=True)
    connection_path.unlink(missing_ok=True)

    app = IPKernelApp.instance(connection_file=str(connection_path))
    app.initialize([])
    binding = FanxiuJupyterBinding(runner, entry, resolved_entry_id, asset_tree_path)
    app.shell.user_ns.update(binding.namespace())
    app.shell.events.register("pre_run_cell", lambda info: binding.begin_cell(info, app.shell))
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
    max_output_chars: int = 20000,
    connection_path: Path | None = None,
    isolate_jobs: bool = True,
) -> dict[str, Any]:
    from jupyter_client import BlockingKernelClient

    path = Path(connection_path or fanxiu_jupyter_connection_path())
    deadline = time.time() + max(5.0, float(timeout_seconds or 120.0))
    while not path.is_file() and time.time() < deadline:
        time.sleep(0.1)
    if not path.is_file():
        raise RuntimeError(f"凡修 Jupyter kernel 尚未就绪：{path}")

    client: BlockingKernelClient | None = None
    while client is None:
        candidate = BlockingKernelClient(connection_file=str(path))
        candidate.load_connection_file()
        candidate.start_channels()
        try:
            candidate.wait_for_ready(timeout=min(3.0, max(1.0, deadline - time.time())))
            client = candidate
        except Exception:
            candidate.stop_channels()
            if time.time() >= deadline:
                raise
            time.sleep(0.2)
    outputs: list[str] = []
    error: dict[str, Any] | None = None
    execution_count: int | None = None
    result_text = ""
    try:
        source = str(code or "")
        if isolate_jobs and not source.lstrip().startswith("# fanxiu:"):
            source = "# fanxiu:manual-code-cell isolate=1\n" + source
        msg_id = client.execute(source, allow_stdin=False, stop_on_error=True)
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
                    result_text = str(data["text/plain"])
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

    output = "".join(outputs).strip()[:max(200, int(max_output_chars or 20000))]
    if error:
        error_message = f"{error['ename']}: {error['evalue']}"
        return {
            "status": "error",
            "phase": "error",
            "message": error_message,
            "error": error_message,
            "traceback": error["traceback"],
            "output": output,
            "execution_count": execution_count,
            "result_text": result_text,
        }
    return {
        "status": "success",
        "phase": "done",
        "message": "Jupyter cell 执行完成",
        "error": "",
        "output": output,
        "execution_count": execution_count,
        "result_text": result_text,
    }


def execute_fanxiu_jupyter_task_cell(
    task_type: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 21630.0,
) -> dict[str, Any]:
    """Execute one registered Fanxiu job as a cell in the resident IPython kernel."""
    code = (
        "# fanxiu:managed-task-cell\n"
        f"run_task_cell({str(task_type)!r}, {dict(payload or {})!r})"
    )
    response = execute_fanxiu_jupyter_cell(code, timeout_seconds=timeout_seconds, isolate_jobs=False)
    if response.get("status") == "error":
        if str(response.get("error") or "").startswith("InterruptedError:"):
            raise InterruptedError(str(response.get("message") or ""))
        raise RuntimeError(str(response.get("message") or "凡修 Jupyter task cell 执行失败"))
    text = str(response.get("result_text") or "").strip()
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError) as exc:
        raise RuntimeError(f"凡修 Jupyter task cell 缺少结构化结果：{text or '<empty>'}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"凡修 Jupyter task cell 返回类型错误：{type(value).__name__}")
    return {**response, **value}
