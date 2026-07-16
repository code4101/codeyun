from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path
from types import GeneratorType
from typing import Any

import psutil


FANXIU_KERNEL_MANAGER_ADDRESS = ("127.0.0.1", 48731)
FANXIU_KERNEL_MANAGER_AUTHKEY = b"codeyun-fanxiu-kernel-v1"


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
        self._cell_lock_acquired = False
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
        self._cell_lock_acquired = True
        try:
            source = str(getattr(info, "raw_cell", "") or "")
            self._managed_task_cell = source.lstrip().startswith("# fanxiu:managed-task-cell")
            active_stop_event = getattr(self.runner, "_stop_event", None) if self._managed_task_cell else None
            self.refresh(stop_event=active_stop_event if isinstance(active_stop_event, threading.Event) else None)
            with self.runner._lock:
                if not isinstance(active_stop_event, threading.Event):
                    self.runner._stop_event = self.stop_event
                if not self._managed_task_cell:
                    self.runner._set_status_locked(
                        "running",
                        "Jupyter cell 执行中",
                        phase="jupyter_cell",
                    )
            shell.user_ns.update(self.namespace())
        except Exception:
            self._cell_lock_acquired = False
            self.execution_lock.release()
            raise

    def end_cell(self, result: Any) -> None:
        error = getattr(result, "error_in_exec", None) or getattr(result, "error_before_exec", None)
        try:
            with self.runner._lock:
                if getattr(self.runner, "_stop_event", None) is self.stop_event:
                    self.runner._stop_event = None
            if not getattr(self, "_managed_task_cell", False):
                with self.runner._lock:
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
            # IPython can emit post_run_cell without a matching successful
            # pre_run_cell (notably around interrupt/rebind and hot reload).
            # Releasing an RLock in that state turns an otherwise completed
            # Cell into a 500 response and breaks every following submission.
            if self._cell_lock_acquired:
                self._cell_lock_acquired = False
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
            "sleep": self.sleep,
        }

    @staticmethod
    def sleep(seconds: float, *, quantum: float = 0.1) -> None:
        """A Jupyter-interruptible wait for debug cells and framework code."""
        deadline = time.monotonic() + max(0.0, float(seconds or 0.0))
        interval = max(0.01, min(0.5, float(quantum or 0.1)))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(interval, remaining))

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

        def execute_task():
            # 启动级遮挡（例如重启模拟器后出现的“游戏公告”）不是业务
            # 场景。必须在场景图寻路前先清理，否则局部素材可能把整张公告
            # 误判成普通场景，并让 goto_view 在错误节点上反复点击。
            yield from self.runner._clear_known_blocking_overlay_if_possible(
                self.runtime_ctx,
                self.stop_event,
                label=definition.label,
            )
            if definition.stable_start_scene_id is not None:
                yield from self.runtime.goto_view(definition.stable_start_scene_id)
            value = definition.handler(
                self.runner,
                self.runtime_ctx,
                normalized,
                self.stop_event,
            )
            if isinstance(value, GeneratorType):
                value = yield from value
            result_name, _message = self.runner._normalize_runtime_task_result(value)
            if definition.stable_start_scene_id is not None and result_name != "manual_check_pending":
                yield from self.runtime.goto_view(definition.stable_start_scene_id)
            return value

        value = execute_task()
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


def bootstrap_fanxiu_jupyter_kernel(entry_id: str) -> dict[str, Any]:
    """Load the Fanxiu framework into the current, real IPython kernel."""
    from backend.core.fanxiu.runtime.behavior_tree import (
        data_annotation_asset_tree_path,
        get_fanxiu_runtime_runner,
        resolve_fanxiu_entry,
        ensure_fanxiu_runtime_jobs_registered,
    )

    shell = get_ipython()  # type: ignore[name-defined]
    if shell is None:
        raise RuntimeError("凡修框架只能加载到真实 IPython/Jupyter kernel")
    resolved_entry_id = str(entry_id)
    entry = resolve_fanxiu_entry(resolved_entry_id)
    asset_tree_path = data_annotation_asset_tree_path(resolved_entry_id)
    runner = get_fanxiu_runtime_runner()
    ensure_fanxiu_runtime_jobs_registered()

    binding = FanxiuJupyterBinding(runner, entry, resolved_entry_id, asset_tree_path)
    shell.user_ns.update(binding.namespace())
    shell.user_ns["_fanxiu_binding"] = binding
    shell.events.register("pre_run_cell", lambda info: binding.begin_cell(info, shell))
    shell.events.register("post_run_cell", binding.end_cell)
    return {
        "entry_id": resolved_entry_id,
        "runtime_loaded": binding.runtime is not None,
        "ctx_loaded": binding.ctx is not None,
    }


def send_fanxiu_kernel_manager_command(
    command: str,
    *,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Send a lifecycle command to the process that owns KernelManager."""
    connection = Client(FANXIU_KERNEL_MANAGER_ADDRESS, authkey=FANXIU_KERNEL_MANAGER_AUTHKEY)
    try:
        connection.send({"command": str(command or "status"), "timeout_seconds": float(timeout_seconds)})
        if not connection.poll(max(0.1, float(timeout_seconds))):
            raise TimeoutError(f"凡修 KernelManager 命令超时：{command}")
        response = connection.recv()
    finally:
        connection.close()
    if not isinstance(response, dict):
        raise RuntimeError("凡修 KernelManager 返回了无效响应")
    return response


def fanxiu_kernel_manager_status(*, timeout_seconds: float = 1.0) -> dict[str, Any]:
    try:
        return send_fanxiu_kernel_manager_command("status", timeout_seconds=timeout_seconds)
    except (OSError, EOFError, TimeoutError):
        return {
            "alive": False,
            "execution_state": "dead",
            "manager_pid": None,
            "kernel_pid": None,
        }


def _interrupt_kernel_over_control_channel(connection_path: Path, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
    """Use Jupyter's control channel so Windows launcher processes cannot swallow SIGINT."""
    from jupyter_client import BlockingKernelClient

    client = BlockingKernelClient(connection_file=str(connection_path))
    client.load_connection_file()
    client.start_channels()
    try:
        message = client.session.msg("interrupt_request", content={})
        message_id = str(message.get("header", {}).get("msg_id") or "")
        client.control_channel.send(message)
        deadline = time.time() + max(0.5, float(timeout_seconds or 5.0))
        while time.time() < deadline:
            reply = client.get_control_msg(timeout=max(0.1, deadline - time.time()))
            if str(reply.get("parent_header", {}).get("msg_id") or "") != message_id:
                continue
            content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
            return {"ok": str(content.get("status") or "ok") == "ok", "content": content}
        raise TimeoutError("Jupyter interrupt_request 超时")
    finally:
        client.stop_channels()


def run_fanxiu_jupyter_kernel_service(*, entry_id: str, tick_seconds: float = 1.0) -> None:
    """Own one native Jupyter KernelManager and its replaceable kernel child."""
    del tick_seconds  # Scheduling is external; the kernel has no resident polling loop.
    from jupyter_client import KernelManager

    connection_path = fanxiu_jupyter_connection_path()
    connection_path.parent.mkdir(parents=True, exist_ok=True)
    state_lock = threading.RLock()
    state: dict[str, Any] = {"execution_state": "starting", "generation": 0}
    monitor_stop: threading.Event | None = None
    monitor_client: Any = None
    manager: KernelManager | None = None

    def kernel_pid(km: KernelManager | None) -> int | None:
        provisioner = getattr(km, "provisioner", None) if km is not None else None
        process = getattr(provisioner, "process", None)
        if process is None:
            return None
        launcher_pid = int(process.pid)
        try:
            launcher = psutil.Process(launcher_pid)
            candidates = [launcher, *launcher.children(recursive=True)]
            ipykernels = [
                candidate
                for candidate in candidates
                if "ipykernel_launcher" in " ".join(candidate.cmdline())
            ]
            if ipykernels:
                return int(max(ipykernels, key=lambda candidate: candidate.memory_info().rss).pid)
        except (psutil.Error, OSError, ValueError):
            pass
        return launcher_pid

    def stop_monitor() -> None:
        nonlocal monitor_stop, monitor_client
        if monitor_stop is not None:
            monitor_stop.set()
        if monitor_client is not None:
            try:
                monitor_client.stop_channels()
            except Exception:
                pass
        monitor_stop = None
        monitor_client = None

    def start_monitor(km: KernelManager) -> None:
        nonlocal monitor_stop, monitor_client
        stop_event = threading.Event()
        client = km.client()
        client.start_channels()
        monitor_stop = stop_event
        monitor_client = client

        def monitor() -> None:
            while not stop_event.is_set():
                try:
                    message = client.get_iopub_msg(timeout=0.5)
                except queue.Empty:
                    continue
                except Exception:
                    return
                if str(message.get("msg_type") or "") != "status":
                    continue
                content = message.get("content") if isinstance(message.get("content"), dict) else {}
                execution_state = str(content.get("execution_state") or "")
                if execution_state:
                    with state_lock:
                        state["execution_state"] = execution_state

        threading.Thread(target=monitor, name="fanxiu-kernel-state", daemon=True).start()

    def execute_bootstrap(km: KernelManager) -> None:
        client = km.blocking_client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=15.0)
            source = (
                "from backend.core.fanxiu.runtime.jupyter_kernel import "
                "bootstrap_fanxiu_jupyter_kernel\n"
                f"bootstrap_fanxiu_jupyter_kernel({str(entry_id)!r})"
            )
            reply = client.execute_interactive(source, timeout=30.0)
            content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
            if str(content.get("status") or "") != "ok":
                raise RuntimeError(f"凡修 Kernel bootstrap 失败：{content}")
        finally:
            client.stop_channels()

    def start_kernel() -> KernelManager:
        connection_path.unlink(missing_ok=True)
        km = KernelManager(kernel_name="python3", connection_file=str(connection_path))
        km.start_kernel(cwd=os.getcwd(), env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        execute_bootstrap(km)
        with state_lock:
            state["generation"] = int(state.get("generation") or 0) + 1
            state["execution_state"] = "idle"
        start_monitor(km)
        return km

    listener = Listener(FANXIU_KERNEL_MANAGER_ADDRESS, authkey=FANXIU_KERNEL_MANAGER_AUTHKEY)
    try:
        manager = start_kernel()
        should_exit = False
        while not should_exit:
            connection = listener.accept()
            try:
                request = connection.recv()
                command = str(request.get("command") or "status") if isinstance(request, dict) else "status"
                timeout = float(request.get("timeout_seconds") or 15.0) if isinstance(request, dict) else 15.0
                if command == "interrupt":
                    try:
                        _interrupt_kernel_over_control_channel(connection_path, timeout_seconds=timeout)
                    except Exception:
                        # KernelManager remains the native fallback for kernels whose
                        # control channel does not implement interrupt_request.
                        manager.interrupt_kernel()
                    deadline = time.time() + max(0.5, timeout)
                    while time.time() < deadline:
                        with state_lock:
                            if state.get("execution_state") != "busy":
                                break
                        time.sleep(0.05)
                elif command == "restart":
                    stop_monitor()
                    manager.shutdown_kernel(now=True)
                    manager = start_kernel()
                elif command == "shutdown":
                    stop_monitor()
                    manager.shutdown_kernel(now=False)
                    should_exit = True
                elif command != "status":
                    raise ValueError(f"未知 KernelManager 命令：{command}")
                alive = bool(manager and manager.is_alive()) and not should_exit
                with state_lock:
                    response = {
                        "ok": True,
                        "command": command,
                        "alive": alive,
                        "execution_state": state.get("execution_state") if alive else "dead",
                        "generation": state.get("generation"),
                        "manager_pid": os.getpid(),
                        "kernel_pid": kernel_pid(manager),
                        "connection_file": str(connection_path),
                    }
                connection.send(response)
            except Exception as exc:
                connection.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            finally:
                connection.close()
    finally:
        stop_monitor()
        if manager is not None and manager.is_alive():
            manager.shutdown_kernel(now=True)
        listener.close()
        connection_path.unlink(missing_ok=True)


def execute_fanxiu_jupyter_cell(
    code: str,
    *,
    timeout_seconds: float = 120.0,
    max_output_chars: int = 20000,
    connection_path: Path | None = None,
) -> dict[str, Any]:
    from jupyter_client import BlockingKernelClient

    path = Path(connection_path or fanxiu_jupyter_connection_path())
    deadline = time.time() + max(5.0, float(timeout_seconds or 120.0))
    while not path.is_file() and time.time() < deadline:
        time.sleep(0.1)
    if not path.is_file():
        raise RuntimeError(f"凡修 Jupyter kernel 尚未就绪：{path}")
    connection_snapshot = path.read_bytes()

    # Bind once to the kernel that existed when this cell was submitted.
    # Re-reading the shared connection file after a restart can accidentally
    # deliver an old cell to the new kernel, which is unlike normal Jupyter
    # client semantics and makes interrupted debug code appear to "revive".
    client = BlockingKernelClient(connection_file=str(path))
    client.load_connection_file()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=min(10.0, max(1.0, deadline - time.time())))
    except Exception:
        client.stop_channels()
        raise
    outputs: list[str] = []
    error: dict[str, Any] | None = None
    execution_count: int | None = None
    result_text = ""
    try:
        source = str(code or "")
        msg_id = client.execute(source, allow_stdin=False, stop_on_error=True)
        idle = False
        while not idle:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError("凡修 Jupyter cell 执行超时")
            try:
                message = client.get_iopub_msg(timeout=min(1.0, remaining))
            except queue.Empty:
                try:
                    connection_changed = path.read_bytes() != connection_snapshot
                except FileNotFoundError:
                    connection_changed = True
                if connection_changed:
                    raise RuntimeError("Fanxiu Jupyter kernel 已重启，当前 cell 已作废")
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
