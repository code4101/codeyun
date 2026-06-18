from __future__ import annotations

from backend.core.fanxiu.data_annotation.runtime_framework import (
    ensure_kernel,
    execute_tick,
    interrupt_current_cell,
    restart_kernel,
    set_guard_group_enabled,
    set_guard_item_enabled,
    set_kernel_enabled,
    tick,
)

interrupt_current_block = interrupt_current_cell

