from __future__ import annotations

"""Reusable formal navigation into one #525 storage-bag category."""

from typing import Any, Callable, Mapping

from backend.core.fanxiu.runtime_gui.storage_bag_alignment import (
    StorageBagTarget,
    prepare_storage_bag_target_by_name,
)


STORAGE_BAG_SCENE = 525
STORAGE_BAG_CATEGORIES = frozenset({"全部", "书籍", "丹药", "礼物", "日程"})


def select_storage_bag_category(
    runtime: Any,
    category: str,
    *,
    timeout_seconds: float = 10.0,
):
    """Click one formal category label; never use Back/Esc or fixed coordinates."""

    selected = str(category or "").strip()
    if selected not in STORAGE_BAG_CATEGORIES:
        raise ValueError(f"不支持的储物袋分类：{selected}")
    match = yield from runtime.wait_click_ocr_text(
        STORAGE_BAG_SCENE,
        selected,
        in_shapes=("分类页签",),
        match_mode="exact",
        timeout_seconds=timeout_seconds,
    )
    yield from runtime.wait_action_settle(0.35)
    return match


def select_storage_bag_category_target(
    runtime: Any,
    *,
    category: str,
    target_name: str,
    snapshot_reader: Callable[[], Mapping[str, Any]],
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
    timeout_seconds: float = 10.0,
) -> StorageBagTarget:
    """Select a category, then resolve an exact target from its fresh Runtime list."""

    yield from select_storage_bag_category(
        runtime,
        category,
        timeout_seconds=timeout_seconds,
    )
    snapshot = snapshot_reader()
    if snapshot.get("complete") is not True:
        raise RuntimeError("储物袋分类切换后 Runtime 列表尚未完整加载")
    return prepare_storage_bag_target_by_name(
        snapshot,
        name=target_name,
        catalog_cards_by_id=catalog_cards_by_id,
    )


__all__ = [
    "STORAGE_BAG_CATEGORIES",
    "STORAGE_BAG_SCENE",
    "select_storage_bag_category",
    "select_storage_bag_category_target",
]
