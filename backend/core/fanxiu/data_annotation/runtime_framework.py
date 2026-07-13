from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation import runtime_control


def ensure_kernel(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return runtime_control.ensure_runtime_service(
        entry=entry,
        entry_id=entry_id,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def restart_kernel(
    *,
    entry: Any,
    entry_id: str,
    timeout_seconds: float = 5.0,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    """Restart the resident runtime kernel.

    This is a service-level restart entry, not a "stop current task" shortcut.
    """
    return runtime_control.restart_runtime_kernel(
        entry=entry,
        entry_id=entry_id,
        timeout_seconds=timeout_seconds,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def set_kernel_enabled(
    *,
    entry: Any,
    entry_id: str,
    enabled: bool,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return runtime_control.set_behavior_tree_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=enabled,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def interrupt_current_cell(
    entry_id: str,
    *,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    """Interrupt only the current business task.

    This does not stop the resident behavior-tree service itself.
    """
    return runtime_control.stop_current_task(
        entry_id,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def set_guard_item_enabled(
    *,
    entry: Any,
    entry_id: str,
    guard_id: str,
    enabled: bool,
    interval_seconds: float,
    asset_tree_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return runtime_control.set_runtime_guard(
        entry=entry,
        entry_id=entry_id,
        guard_id=guard_id,
        enabled=enabled,
        interval_seconds=interval_seconds,
        asset_tree_path=asset_tree_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def set_guard_group_enabled(
    *,
    entry: Any,
    entry_id: str,
    enabled: bool,
    asset_tree_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return runtime_control.set_runtime_guard_group_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=enabled,
        asset_tree_path=asset_tree_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def tick(
    *,
    entry: Any,
    entry_id: str,
    task_type: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one registered task through the same ordinary-cell path."""
    return runtime_control.submit_tick_task(
        entry=entry,
        entry_id=entry_id,
        task_type=task_type,
        payload=payload,
    )


def submit_task_cell(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a registered task invocation and execute it as one normal cell."""
    from backend.core.fanxiu.runtime.kernel import FanxiuKernel

    payload_dict = dict(payload or {})
    try:
        runtime_timeout = float(payload_dict.get("max_runtime_seconds", payload_dict.get("timeout_seconds", 600)) or 600)
    except (TypeError, ValueError):
        runtime_timeout = 600.0
    timeout_seconds = max(30.0, runtime_timeout + 30.0)
    del entry
    return FanxiuKernel(entry_id=str(entry_id)).task(
        str(task_type or ""),
        payload_dict,
        timeout_seconds=timeout_seconds,
    ).run(
        timeout_seconds=timeout_seconds,
    )


def submit_code_cell(
    *,
    entry: Any,
    entry_id: str,
    code: str,
    timeout_seconds: float = 120.0,
    max_output_chars: int = 4000,
) -> dict[str, Any]:
    """Execute arbitrary Python in the same resident Jupyter namespace."""
    from backend.core.fanxiu.runtime.kernel import FanxiuKernel

    del entry
    return FanxiuKernel(entry_id=str(entry_id)).cell(
        str(code or ""),
        timeout_seconds=float(timeout_seconds or 120.0),
        max_output_chars=int(max_output_chars or 4000),
    ).run()


def execute_tick(
    *,
    entry: Any,
    entry_id: str,
    guard: bool = True,
    task_cell: bool = True,
    scheduled_job: bool = True,
    run_mode: str = "tick_once",
    max_ticks: int = 10,
    timeout_seconds: float = 30.0,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return runtime_control.execute_runtime_tick(
        entry=entry,
        entry_id=entry_id,
        guard=guard,
        task_cell=task_cell,
        scheduled_job=scheduled_job,
        run_mode=run_mode,
        max_ticks=max_ticks,
        timeout_seconds=timeout_seconds,
        asset_tree_path=asset_tree_path,
        scheduler_settings_path=scheduler_settings_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )
