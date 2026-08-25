from __future__ import annotations

from typing import Any

from backend.core.fanxiu.data_annotation.tasks.activity_store import (
    ActivityStoreOperationResult,
    operate_activity_store_region,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_navigation import (
    KUNLUN_STORE_SCENE_ID,
)


def complete_kunlun_store(runtime: Any) -> ActivityStoreOperationResult:
    return operate_activity_store_region(
        runtime,
        scene_id=KUNLUN_STORE_SCENE_ID,
        region_title="区域",
        select_targets=lambda scan: tuple(
            target for target in scan.targets if not target.is_cash
        ),
        stability_timeout_seconds=30.0,
        purchase_timeout_seconds=30.0,
    )
