from __future__ import annotations

from typing import Any

from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    ActivityStoreOperationResult,
    operate_activity_store_region,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XIANZANG_STORE_SCENE_ID,
    open_xianzang_tab,
)


def complete_xianzang_store(runtime: Any) -> ActivityStoreOperationResult:
    """Apply the explicitly defined #449 policy to every stable fresh frame."""

    # The workflow hands each phase the current Xianzang page.  Optional
    # selection may be skipped (leaving #447) or completed (returning #447), so
    # the store phase owns its navigation precondition instead of asking the
    # generic region scanner to wait for a scene that no action can produce.
    open_xianzang_tab(runtime, "商店")
    return operate_activity_store_region(
        runtime,
        scene_id=XIANZANG_STORE_SCENE_ID,
        region_title="区域",
        select_targets=lambda scan: tuple(
            target for target in scan.targets if not target.is_cash
        ),
    )
