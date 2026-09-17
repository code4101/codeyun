"""Public read-only entry point for dynamic scene navigation planning.

The live ``go_scene`` execution path and this planner share one candidate
provider (:meth:`BehaviorTreeExecutor._scene_next_edge_candidates`).  Nothing
here samples, logs, mutates assets, or touches a device/Kernel, and none of the
returned numbers is a calibrated full-path success rate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.runner import create_behavior_tree_executor
from backend.core.fanxiu.data_annotation.storage import (
    DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID,
    FanxiuDataAnnotationAssetTreeSnapshot,
    data_annotation_asset_tree_path,
    read_data_annotation_asset_tree_snapshot,
)


def load_asset_tree_snapshot(
    entry_id: str | None = None,
) -> tuple[Path, FanxiuDataAnnotationAssetTreeSnapshot]:
    """Read the persisted asset tree plus its revision through public storage.

    Read-only: the snapshot is loaded under the provider's own lock and never
    written back.
    """

    path = data_annotation_asset_tree_path(
        entry_id or DEFAULT_FANXIU_DATA_ANNOTATION_ENTRY_ID
    )
    return path, read_data_annotation_asset_tree_snapshot(path)


def plan_scene_navigation(
    tree: list[dict[str, Any]],
    source_scene_id: int,
    target_scene_id: int,
    *,
    limit: int = 3,
    max_downstream_steps: int = 16,
    planner: Any | None = None,
) -> dict[str, Any]:
    """Plan next-step scene navigation candidates without touching a device.

    ``tree`` is an already-loaded asset tree.  ``planner`` may be supplied by
    tests or by a caller reusing a runner; otherwise a fresh device-free
    executor is created.  The returned mapping keeps ``selection_probability``,
    the single-step ``landing_probabilities`` and the value-iteration
    ``discounted_reachability_gain`` distinct, and only sketches a downstream
    route; it is not a calibrated success rate.
    """

    runner = planner if planner is not None else create_behavior_tree_executor()
    return runner.plan_scene_navigation(
        tree,
        source_scene_id,
        target_scene_id,
        limit=limit,
        max_downstream_steps=max_downstream_steps,
    )
