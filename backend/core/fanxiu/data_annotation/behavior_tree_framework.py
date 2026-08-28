from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation import behavior_tree_control


def ensure_kernel(
    *,
    entry: Any,
    entry_id: str,
    asset_tree_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    return behavior_tree_control.ensure_behavior_tree_runtime(
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
    return behavior_tree_control.restart_behavior_tree_kernel(
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
    return behavior_tree_control.set_behavior_tree_enabled(
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
    timeout_seconds: float = 15.0,
    scheduler_state_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    """Interrupt only the current business task.

    This does not stop the resident behavior-tree service itself.
    """
    return behavior_tree_control.stop_current_task(
        entry_id,
        interrupt_timeout_seconds=timeout_seconds,
        scheduler_state_path=scheduler_state_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def take_runtime_control(
    entry_id: str,
    *,
    interrupt_any_cell: bool = False,
    timeout_seconds: float = 15.0,
    scheduler_state_path: Path | None = None,
    scheduler_settings_path: Path | None = None,
    runtime_state_path: Path | None = None,
    world_facts_path: Path | None = None,
) -> dict[str, Any]:
    """Yield the shared GUI to AI/user and stop the current Cell if needed."""

    return behavior_tree_control.take_ai_runtime_control(
        entry_id,
        interrupt_any_cell=interrupt_any_cell,
        interrupt_timeout_seconds=timeout_seconds,
        scheduler_state_path=scheduler_state_path,
        scheduler_settings_path=scheduler_settings_path,
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
    return behavior_tree_control.set_runtime_guard(
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
    return behavior_tree_control.set_runtime_guard_group_enabled(
        entry=entry,
        entry_id=entry_id,
        enabled=enabled,
        asset_tree_path=asset_tree_path,
        runtime_state_path=runtime_state_path,
        world_facts_path=world_facts_path,
    )


def submit_task_cell(
    *,
    entry: Any,
    entry_id: str,
    task_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile a registered task invocation and execute it as one normal cell."""
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    payload_dict = dict(payload or {})
    if bool(payload_dict.get("unbounded_runtime")):
        timeout_seconds = None
    else:
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
    from backend.core.fanxiu.behavior_tree.kernel import FanxiuKernel

    del entry
    return FanxiuKernel(entry_id=str(entry_id)).cell(
        str(code or ""),
        timeout_seconds=float(timeout_seconds or 120.0),
        max_output_chars=int(max_output_chars or 4000),
    ).run()
