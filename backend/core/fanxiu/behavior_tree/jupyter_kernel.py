from __future__ import annotations

import hashlib
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

from backend.core.settings import ROOT_DIR
from backend.core.services.launcher import (
    apply_managed_child_env,
    install_child_process_no_window_default,
)


FANXIU_KERNEL_MANAGER_ADDRESS = ("127.0.0.1", 48731)
FANXIU_KERNEL_MANAGER_AUTHKEY = b"codeyun-fanxiu-kernel-v1"


def fanxiu_jupyter_connection_path() -> Path:
    from backend.core.fanxiu.behavior_tree.runtime import fanxiu_behavior_tree_runtime_dir

    return fanxiu_behavior_tree_runtime_dir() / "jupyter-kernel.json"


def fanxiu_kernel_child_env() -> dict[str, str]:
    """Build the mandatory environment for the real Fanxiu Python Kernel.

    The Kernel executes all scheduled jobs and therefore owns their ADB/OCR
    subprocesses.  Do not rely on whichever shell happened to start the
    KernelManager: enforce CodeYun's no-window policy at this boundary.
    """

    env = apply_managed_child_env(os.environ, root_dir=ROOT_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["CODEYUN_SKIP_PLATFORM_WMI_PROCESSOR"] = "1"
    # GUI recognition is latency-sensitive but must not occupy every logical
    # core while the user is playing another game.  Native OCR/BLAS libraries
    # otherwise create a large worker pool per resident Kernel.
    env["OMP_NUM_THREADS"] = "4"
    env["MKL_NUM_THREADS"] = "4"
    env["OPENBLAS_NUM_THREADS"] = "4"
    env["NUMEXPR_NUM_THREADS"] = "4"
    env["OPENCV_FOR_THREADS_NUM"] = "4"
    return env


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
        self._asset_revision = ""
        self._asset_generation = 0
        self._asset_reload_requested = False
        self._cached_tree: list[dict[str, Any]] | None = None
        self._cached_images: dict[int, dict[str, Any]] | None = None
        self._cell_active = False
        self._shell: Any = None
        self._refresh_binding(force_assets=True)

    def _asset_file_signature(self) -> tuple[int, int]:
        stat = self.asset_tree_path.stat()
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def _asset_tree_revision(self) -> str:
        """Use the storage layer's raw-file SHA-256 revision semantics."""

        return hashlib.sha256(self.asset_tree_path.read_bytes()).hexdigest()

    def _load_assets_if_needed(self, *, force: bool = False) -> bool:
        signature = self._asset_file_signature()
        should_reload = bool(
            force
            or self._asset_reload_requested
            or signature != self._asset_signature
            or self._cached_tree is None
            or self._cached_images is None
        )
        if not should_reload:
            return False

        try:
            tree = self.runner._load_asset_tree(self.asset_tree_path)
            invalidate = getattr(self.runner, "_invalidate_asset_derived_caches", None)
            if callable(invalidate):
                invalidate(self.asset_tree_path)
            images = self.runner._index_images(tree)
            probe_ctx = {
                "entry": self.entry,
                "entry_id": self.entry_id,
                "asset_tree": tree,
                "asset_tree_path": self.asset_tree_path,
                "images": images,
            }
            self.runner._require_assets(probe_ctx)
        except Exception:
            # A failed forced reload must remain pending.  Otherwise a same-
            # signature retry could silently fall back to the old snapshot.
            self._asset_reload_requested = True
            raise

        # Publish only after the complete new snapshot has parsed, resolved and
        # passed validation.  A Cell therefore sees either the old generation
        # or the new one, never a half-updated tree/images pair.
        self._asset_signature = signature
        self._asset_revision = self._asset_tree_revision()
        self._asset_generation += 1
        self._asset_reload_requested = False
        self._cached_tree = tree
        self._cached_images = images
        return True

    def _refresh_binding(
        self,
        *,
        stop_event: threading.Event | None = None,
        force_assets: bool = False,
    ) -> bool:
        from backend.core.fanxiu.data_annotation.debug_eval import BehaviorTreeRuntimeDebugContext

        reloaded = self._load_assets_if_needed(force=force_assets)
        self.runtime_ctx.clear()
        self.runtime_ctx.update({
            "entry": self.entry,
            "entry_id": self.entry_id,
            "asset_tree": self._cached_tree,
            "asset_tree_path": self.asset_tree_path,
            "asset_tree_revision": self._asset_revision,
            "asset_tree_generation": self._asset_generation,
            "images": self._cached_images,
        })
        self.stop_event = stop_event or threading.Event()
        self.runtime = self.runner._fanxiu_runtime(
            self.runtime_ctx,
            self.asset_tree_path,
            stop_event=self.stop_event,
        )
        if self.ctx is None:
            self.ctx = BehaviorTreeRuntimeDebugContext(
                self.runner,
                self.runtime_ctx,
                self.stop_event,
                readonly=False,
            )
        else:
            self.ctx.rebind(self.runtime_ctx, self.stop_event, readonly=False)
        return reloaded

    def _sync_shell_namespace(self) -> None:
        shell = self._shell
        if shell is not None and isinstance(getattr(shell, "user_ns", None), dict):
            shell.user_ns.update(self.namespace())

    def _asset_refresh_result(self, *, reloaded: bool, pending: bool) -> dict[str, Any]:
        return {
            "reloaded": bool(reloaded),
            "pending": bool(pending),
            "generation": int(self._asset_generation),
            "revision": str(self._asset_revision),
            "signature": self._asset_signature,
        }

    def refresh_assets(self, *, force: bool = False) -> dict[str, Any]:
        """Refresh the cached asset snapshot without restarting the Kernel.

        Assets are pinned for the lifetime of one Cell.  A refresh requested
        from inside a running Cell is therefore queued for the next Cell
        boundary; an idle caller can refresh immediately under the same lock
        used by Cell execution.  Automatic file-change refresh and explicit
        force refresh share the exact same loader and cache invalidation path.
        """

        signature_changed = self._asset_file_signature() != self._asset_signature
        should_reload = bool(force or signature_changed or self._asset_reload_requested)
        if self._cell_active:
            self._asset_reload_requested = should_reload
            return self._asset_refresh_result(reloaded=False, pending=should_reload)

        with self.execution_lock:
            reloaded = self._refresh_binding(force_assets=bool(force))
            self._sync_shell_namespace()
        return self._asset_refresh_result(reloaded=reloaded, pending=False)

    def refresh(self, *, force: bool = False) -> "FanxiuJupyterBinding":
        """Backward-compatible alias preserving the historical ``self`` result."""

        self.refresh_assets(force=force)
        return self

    def begin_cell(self, info: Any, shell: Any) -> None:
        self.execution_lock.acquire()
        self._cell_lock_acquired = True
        try:
            self._shell = shell
            source = str(getattr(info, "raw_cell", "") or "")
            self._managed_task_cell = source.lstrip().startswith("# fanxiu:managed-task-cell")
            active_stop_event = getattr(self.runner, "_stop_event", None) if self._managed_task_cell else None
            self._refresh_binding(
                stop_event=active_stop_event if isinstance(active_stop_event, threading.Event) else None,
            )
            self._cell_active = True
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
            self._cell_active = False
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
            self._cell_active = False
            # IPython can emit post_run_cell without a matching successful
            # pre_run_cell (notably around interrupt/rebind and hot reload).
            # Releasing an RLock in that state turns an otherwise completed
            # Cell into a 500 response and breaks every following submission.
            if self._cell_lock_acquired:
                self._cell_lock_acquired = False
                self.execution_lock.release()

    def namespace(self) -> dict[str, Any]:
        from backend.core.fanxiu.choice_knowledge.catalog import (
            choice_knowledge_catalog,
        )
        from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
            set_data_annotation_scheduler_task_trigger_time,
        )

        return {
            "fanxiu": self,
            "runner": self.runner,
            "runtime": self.runtime,
            "ctx": self.ctx,
            "choice_bank": choice_knowledge_catalog,
            "run": self.run,
            "run_task": self.run_task,
            "run_task_cell": self.run_task_cell,
            "refresh_assets": self.refresh_assets,
            "refresh": self.refresh,
            "sleep": self.sleep,
            "set_trigger_time": set_data_annotation_scheduler_task_trigger_time,
            "设置触发时间": set_data_annotation_scheduler_task_trigger_time,
        }

    def recognize_info_window_scene(self) -> tuple[int | None, float]:
        """Publish one observational scene result for explicit active mode."""

        marker = "_fanxiu_scene_observation_source"
        previous = self.runtime_ctx.get(marker)
        self.runtime_ctx[marker] = "info_window_active"
        try:
            self.runner._clear_tick_frame(self.runtime_ctx)
            frame = self.runner._screencap(self.runtime_ctx)
            return self.runner._identify_scene_number(self.runtime_ctx, frame)
        finally:
            self.runner._clear_tick_frame(self.runtime_ctx)
            if previous is None:
                self.runtime_ctx.pop(marker, None)
            else:
                self.runtime_ctx[marker] = previous

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

    def _defer_scheduled_task_for_active_maintenance(
        self,
        task_type: str,
        task_label: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Keep ordinary Scheduler jobs out of the GUI while maintenance is latched."""

        from backend.core.fanxiu.data_annotation.maintenance import (
            maintenance_check_time_text,
            maintenance_gate_blocks_task,
            read_maintenance_gate,
        )

        task_id = str(payload.get("__scheduler_task_id") or "").strip()
        world_facts_path = getattr(self.runner, "_maintenance_world_facts_path", None)
        if not task_id or not callable(world_facts_path):
            return None
        gate = read_maintenance_gate(world_facts_path())
        if not maintenance_gate_blocks_task(
            gate,
            {"id": task_id, "task_type": str(task_type or "")},
        ):
            return None
        next_time = maintenance_check_time_text()
        self.runner._persist_scheduler_task_next_time(task_id, next_time)
        return {
            "result": "success",
            "message": (
                f"检测到游戏维护门闩；{task_label}休眠至 {next_time}，"
                "仅允许系统_维护恢复"
            ),
        }

    def run_task(self, task_type: str, payload: dict[str, Any] | None = None) -> Any:
        from backend.core.fanxiu.data_annotation.effective_time import job_effective_time
        from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
        from backend.core.fanxiu.data_annotation.task_context import runtime_task_payload

        definition = get_fanxiu_data_annotation_task_cell_definition(str(task_type or ""))
        if definition is None:
            raise ValueError(f"未知凡修 task cell：{task_type}")
        normalized = dict(payload or {})
        if callable(definition.normalize_payload):
            normalized = definition.normalize_payload(normalized)

        maintenance_deferred = self._defer_scheduled_task_for_active_maintenance(
            str(task_type or ""),
            definition.label,
            normalized,
        )
        if maintenance_deferred is not None:
            return maintenance_deferred

        def evaluate_admission() -> dict[str, Any] | None:
            if not callable(definition.admission):
                return None
            decision = definition.admission(self.runner, normalized)
            if isinstance(decision, GeneratorType):
                raise TypeError(f"{definition.label}：作业准入必须是无副作用同步判断")
            if decision is None:
                return None
            if not isinstance(decision, dict):
                raise TypeError(f"{definition.label}：作业准入必须返回 dict 或 None")
            if "next_time" in decision:
                raise RuntimeError(
                    f"{definition.label}：作业准入不得通过返回值传递 next_time；"
                    "必须在业务准入函数内原子写入"
                )
            return decision

        def execute_task():
            try:
                # 业务时间、星期域和完成周期都归作业所有。注册了准入判断的
                # 作业可在调用 handler 前无副作用地退出；通用包装层除此之外
                # 不执行登录、清遮挡、场景导航或任何业务前置/后置动作。
                admission_result = evaluate_admission()
                if admission_result is not None:
                    terminal = {
                        "result": "success",
                        "message": str(admission_result.get("message") or f"{definition.label}完成"),
                    }
                    if isinstance(admission_result.get("scheduler_incident"), dict):
                        terminal["scheduler_incident"] = admission_result["scheduler_incident"]
                    return terminal
                # Bind the ordinary Scheduler Cell payload to the shared
                # Runtime context for the complete handler/generator lifetime.
                # Business completion points can therefore identify their
                # exact Scheduler Job without globals or label-based fallback.
                with runtime_task_payload(self.runtime_ctx, normalized):
                    value = definition.handler(
                        self.runner,
                        self.runtime_ctx,
                        normalized,
                        self.stop_event,
                    )
                    if isinstance(value, GeneratorType):
                        value = yield from value
                # Any normal handler return means this Cell was triggered and
                # completed successfully from the engineering Scheduler's
                # perspective.  The handler's business outcome is deliberately
                # not a Scheduler status: it must be encoded by the next_time
                # the handler persisted (``now`` means immediately due again).
                # A trigger/execution failure must raise and is handled by the
                # exception path below.
                _result_name, message = self.runner._normalize_runtime_task_result(value)
                terminal = {"result": "success", "message": message}
                if isinstance(value, dict) and isinstance(value.get("scheduler_incident"), dict):
                    terminal["scheduler_incident"] = value["scheduler_incident"]
                return terminal
            except Exception as exc:
                from backend.core.fanxiu.data_annotation.maintenance import FanxiuMaintenanceDetected
                from backend.core.fanxiu.data_annotation.popup_guard import (
                    FanxiuEmulatorRestartRequired,
                )

                if isinstance(exc, FanxiuEmulatorRestartRequired):
                    if exc.recovery_succeeded:
                        self.runner._schedule_login_job_first()
                        raise FanxiuEmulatorRestartRequired(
                            f"{exc.detail}；已将“登录”排到现有 next_time 队首，当前业务仍须整单重跑",
                            evidence=exc.evidence,
                            recovery_succeeded=True,
                        ) from exc
                    raise

                if isinstance(exc, FanxiuMaintenanceDetected):
                    # The first ordinary Job that discovers maintenance opens
                    # the persistent gate inside the Runtime.  Convert that
                    # expected availability outcome into a normal Scheduler
                    # terminal after moving this Job to the next maintenance
                    # wake; otherwise the technical error retry loops on the
                    # same prompt every minute and can starve recovery.
                    deferred = self._defer_scheduled_task_for_active_maintenance(
                        str(task_type or ""),
                        definition.label,
                        normalized,
                    )
                    if deferred is not None:
                        return deferred

                # 场景恢复属于具体作业的业务流程。通用 Cell 包装层只传播
                # 异常，由 Scheduler 记录本次 attempt 结果，不代替作业导航。
                raise

        # The override is scoped to this ordinary Job Cell.  Scheduler due
        # checks, attempt timestamps and error retry bookkeeping stay on the
        # real wall clock outside this context.
        with job_effective_time(normalized):
            value = execute_task()
            return self.run(
                value,
                label=definition.label,
                tick_seconds=max(0.1, float(normalized.get("__tick_seconds") or 1.0)),
                max_runtime_seconds=self.runner._task_timeout_seconds(normalized),
                guard_override=self.runner._runtime_guard_override_from_payload(normalized),
            )

    def run_task_cell(self, task_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition

        definition = get_fanxiu_data_annotation_task_cell_definition(str(task_type or ""))
        normalized_payload = dict(payload or {})
        task_label = str(definition.label if definition is not None else task_type or "凡修作业")
        task_id = str(normalized_payload.get("__scheduler_task_id") or "")
        attempt_id = str(normalized_payload.get("__scheduler_attempt_id") or "")
        running_message = f"{task_label}：执行中"
        with self.runner._lock:
            self.runner._status.update({
                "ok": True,
                "running": True,
                "status": "running",
                "phase": "scheduler_task" if task_id else "task_cell",
                "task_type": str(task_type or ""),
                "current_task": task_label,
                "current_task_id": task_id,
                "scheduler_task_id": task_id,
                "scheduler_attempt_id": attempt_id,
                "scheduler_terminal_result": "",
                "scheduler_terminal_message": "",
                "scheduler_terminal_at": None,
                "message": running_message,
                "error": "",
                "started_at": time.time(),
                "finished_at": None,
                "updated_at": time.time(),
                "interruptible": True,
            })
        self.runner._persist_status()
        try:
            result = self.run_task(task_type, normalized_payload)
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            with self.runner._lock:
                self.runner._clear_current_task_locked()
                self.runner._status.update({
                    "ok": False,
                    "status": "error",
                    "phase": "error",
                    "message": str(detail),
                    "error": str(detail),
                    "scheduler_task_id": task_id,
                    "scheduler_attempt_id": attempt_id,
                    "scheduler_terminal_result": "error",
                    "scheduler_terminal_message": str(detail),
                    "scheduler_terminal_at": time.time(),
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                })
            self.runner._persist_status()
            raise
        else:
            result_name, message = self.runner._normalize_runtime_task_result(result)
            with self.runner._lock:
                existing_message = str(self.runner._status.get("message") or "")
            business_message = (
                existing_message if existing_message != running_message else ""
            )
            resolved_message = str(
                message or business_message or f"{task_label}完成"
            )
            terminal = {"result": str(result_name or "success")}
            if isinstance(result, dict) and isinstance(result.get("scheduler_incident"), dict):
                terminal["scheduler_incident"] = result["scheduler_incident"]
            # Many production handlers persist their precise business outcome
            # through the Runtime status and return the framework-level string
            # ``success``.  Preserve that already-established message in the
            # Cell terminal payload so Scheduler history can distinguish the
            # business terminal from a generic execution success.
            terminal["message"] = resolved_message
        with self.runner._lock:
            self.runner._clear_current_task_locked()
            self.runner._status.update({
                "ok": True,
                "status": str(result_name or "success"),
                "phase": "done",
                "message": resolved_message,
                "error": "",
                "scheduler_task_id": task_id,
                "scheduler_attempt_id": attempt_id,
                "scheduler_terminal_result": str(result_name or "success"),
                "scheduler_terminal_message": resolved_message,
                "scheduler_terminal_at": time.time(),
                "finished_at": time.time(),
                "updated_at": time.time(),
            })
        self.runner._persist_status()
        return terminal

    def scene(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.ctx.scene(*args, **kwargs)

    def go(self, scene: int | str, **options: Any) -> Any:
        return self.ctx.go(scene, **options)

    task = run_task


def bootstrap_fanxiu_jupyter_kernel(entry_id: str) -> dict[str, Any]:
    """Load the Fanxiu framework into the current, real IPython kernel."""
    install_child_process_no_window_default()
    from backend.core.fanxiu.behavior_tree.runtime import (
        data_annotation_asset_tree_path,
        get_behavior_tree_runtime_runner,
        resolve_fanxiu_entry,
        ensure_behavior_tree_runtime_jobs_registered,
    )

    shell = get_ipython()  # type: ignore[name-defined]
    if shell is None:
        raise RuntimeError("凡修框架只能加载到真实 IPython/Jupyter kernel")
    resolved_entry_id = str(entry_id)
    entry = resolve_fanxiu_entry(resolved_entry_id)
    asset_tree_path = data_annotation_asset_tree_path(resolved_entry_id)
    runner = get_behavior_tree_runtime_runner()
    ensure_behavior_tree_runtime_jobs_registered()
    from sqlmodel import Session

    from backend.core.fanxiu.choice_knowledge.catalog import (
        load_choice_knowledge_catalog,
    )
    from backend.db import engine

    with Session(engine) as session:
        choice_count = load_choice_knowledge_catalog(session)

    binding = FanxiuJupyterBinding(runner, entry, resolved_entry_id, asset_tree_path)
    from backend.core.fanxiu.info_window import FanxiuInfoWindowObserver

    previous_info_window = shell.user_ns.get("_fanxiu_info_window_observer")
    if previous_info_window is not None and hasattr(previous_info_window, "stop"):
        previous_info_window.stop()
    info_window_observer = FanxiuInfoWindowObserver(
        execution_lock=binding.execution_lock,
        recognize=binding.recognize_info_window_scene,
    ).start()
    shell.user_ns.update(binding.namespace())
    shell.user_ns["_fanxiu_binding"] = binding
    shell.user_ns["_fanxiu_info_window_observer"] = info_window_observer
    shell.events.register("pre_run_cell", lambda info: binding.begin_cell(info, shell))
    shell.events.register("post_run_cell", binding.end_cell)
    return {
        "entry_id": resolved_entry_id,
        "runtime_loaded": binding.runtime is not None,
        "ctx_loaded": binding.ctx is not None,
        "choice_knowledge_loaded": choice_count,
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
        status = send_fanxiu_kernel_manager_command("status", timeout_seconds=timeout_seconds)
        connection_file = str(status.get("connection_file") or "").strip()
        if bool(status.get("alive")) and connection_file and not Path(connection_file).is_file():
            status = dict(status)
            status["alive"] = False
            status["execution_state"] = "dead"
            status["error"] = "Jupyter connection file missing"
        return status
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
    install_child_process_no_window_default()
    from jupyter_client import KernelManager
    from backend.core.fanxiu.runtime.code_signature import (
        fanxiu_behavior_tree_code_signature,
    )

    connection_path = fanxiu_jupyter_connection_path()
    connection_path.parent.mkdir(parents=True, exist_ok=True)
    state_lock = threading.RLock()
    state: dict[str, Any] = {"execution_state": "starting", "generation": 0}
    monitor_stop: threading.Event | None = None
    monitor_client: Any = None
    manager: KernelManager | None = None

    def kernel_processes(km: KernelManager | None) -> list[psutil.Process]:
        provisioner = getattr(km, "provisioner", None) if km is not None else None
        process = getattr(provisioner, "process", None)
        if process is None:
            return []
        try:
            root = psutil.Process(int(process.pid))
            return [*root.children(recursive=True), root]
        except (psutil.Error, OSError, TypeError, ValueError):
            return []

    def shutdown_kernel(km: KernelManager | None, *, now: bool) -> None:
        """Stop Jupyter and reap the Windows launcher plus its real child.

        ``pythonw.exe`` from a virtual environment is a launcher process on
        Windows.  Jupyter can stop that launcher while leaving the real
        ``ipykernel`` child behind, so retain the complete tree before asking
        Jupyter to shut down and explicitly reap anything still alive.
        """

        processes = kernel_processes(km)
        try:
            if km is not None:
                km.shutdown_kernel(now=now)
        finally:
            for process in processes:
                try:
                    if process.is_running():
                        process.terminate()
                except psutil.Error:
                    pass
            _, alive = psutil.wait_procs(processes, timeout=3.0)
            for process in alive:
                try:
                    process.kill()
                except psutil.Error:
                    pass
            if alive:
                psutil.wait_procs(alive, timeout=2.0)

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

    def lower_kernel_priority(km: KernelManager | None) -> None:
        for process in kernel_processes(km):
            try:
                if os.name == "nt":
                    process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                else:
                    process.nice(max(5, int(process.nice())))
            except (psutil.Error, OSError, TypeError, ValueError):
                continue

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
                "from backend.core.fanxiu.behavior_tree.jupyter_kernel import "
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
        loaded_code_signature = fanxiu_behavior_tree_code_signature()
        connection_path.unlink(missing_ok=True)
        km = KernelManager(kernel_name="python3", connection_file=str(connection_path))
        try:
            km.start_kernel(cwd=os.getcwd(), env=fanxiu_kernel_child_env())
            execute_bootstrap(km)
            lower_kernel_priority(km)
        except Exception:
            shutdown_kernel(km, now=True)
            connection_path.unlink(missing_ok=True)
            raise
        with state_lock:
            state["generation"] = int(state.get("generation") or 0) + 1
            state["execution_state"] = "idle"
            state["behavior_tree_code_signature"] = loaded_code_signature
        start_monitor(km)
        return km

    listener = Listener(FANXIU_KERNEL_MANAGER_ADDRESS, authkey=FANXIU_KERNEL_MANAGER_AUTHKEY)
    try:
        manager = start_kernel()
        should_exit = False
        while not should_exit:
            try:
                connection = listener.accept()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, EOFError):
                # A short-lived local CLI client may disconnect during the
                # multiprocessing authentication handshake.  That client is
                # gone, but the manager and live kernel must keep serving the
                # next control request.
                continue
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
                    previous_manager = manager
                    manager = None
                    shutdown_kernel(previous_manager, now=True)
                    manager = start_kernel()
                elif command == "shutdown":
                    stop_monitor()
                    previous_manager = manager
                    manager = None
                    shutdown_kernel(previous_manager, now=False)
                    should_exit = True
                elif command != "status":
                    raise ValueError(f"未知 KernelManager 命令：{command}")
                # A live child process without its connection file is not a
                # usable Jupyter Kernel.  Treat it as dead so callers restart
                # this child through the still-responsive manager instead of
                # waiting forever for a file that will never reappear.
                alive = (
                    bool(manager and manager.is_alive())
                    and connection_path.is_file()
                    and not should_exit
                )
                with state_lock:
                    response = {
                        "ok": True,
                        "command": command,
                        "alive": alive,
                        "execution_state": state.get("execution_state") if alive else "dead",
                        "generation": state.get("generation"),
                        "behavior_tree_code_signature": state.get("behavior_tree_code_signature"),
                        "manager_pid": os.getpid(),
                        "kernel_pid": kernel_pid(manager),
                        "connection_file": str(connection_path),
                    }
                connection.send(response)
            except Exception as exc:
                try:
                    connection.send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, EOFError, OSError):
                    pass
            finally:
                connection.close()
    finally:
        stop_monitor()
        if manager is not None:
            shutdown_kernel(manager, now=True)
        listener.close()
        connection_path.unlink(missing_ok=True)


def execute_fanxiu_jupyter_cell(
    code: str,
    *,
    timeout_seconds: float | None = 120.0,
    max_output_chars: int = 20000,
    connection_path: Path | None = None,
) -> dict[str, Any]:
    from jupyter_client import BlockingKernelClient

    path = Path(connection_path or fanxiu_jupyter_connection_path())
    deadline = (
        None
        if timeout_seconds is None
        else time.time() + max(5.0, float(timeout_seconds or 120.0))
    )
    while not path.is_file() and (deadline is None or time.time() < deadline):
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
        ready_timeout = 10.0 if deadline is None else min(10.0, max(1.0, deadline - time.time()))
        client.wait_for_ready(timeout=ready_timeout)
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
            remaining = None if deadline is None else deadline - time.time()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("凡修 Jupyter cell 执行超时")
            try:
                message = client.get_iopub_msg(
                    timeout=1.0 if remaining is None else min(1.0, remaining)
                )
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
        reply_content: dict[str, Any] = {}
        shell_deadline = time.time() + 2.0 if deadline is None else min(deadline, time.time() + 2.0)
        while time.time() < shell_deadline:
            try:
                reply = client.get_shell_msg(timeout=max(0.1, shell_deadline - time.time()))
            except queue.Empty:
                break
            if str(reply.get("parent_header", {}).get("msg_id") or "") != msg_id:
                continue
            reply_content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
            break
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
