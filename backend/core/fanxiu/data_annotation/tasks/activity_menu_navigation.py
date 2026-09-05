from __future__ import annotations

"""Standard navigation for the loaded #34 activity menus.

Runtime supplies the authoritative menu identity and order.  The current
frame and the caller-provided formal shapes supply geometry only.  This module
deliberately does not open a menu, guess a scene, or fall back to global OCR.
"""

from dataclasses import replace
from typing import Any, Iterable, Literal

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens

from backend.core.fanxiu.instrumentation.activity_menu import (
    ActivityMenuSnapshot,
    read_activity_menu_snapshot,
)
from backend.core.fanxiu.runtime_gui.activity_menu import (
    ActivityMenuGrid,
    GROUP_POPUP_ACTIVITY_GRID,
    WORLD_LEFT_ACTIVITY_GRID,
    plan_activity_menu_click,
)


ActivityMenuKind = Literal["world_left", "group_popup"]


def _default_grid(kind: ActivityMenuKind) -> ActivityMenuGrid:
    if kind == "world_left":
        return WORLD_LEFT_ACTIVITY_GRID
    if kind == "group_popup":
        return GROUP_POPUP_ACTIVITY_GRID
    raise ValueError(f"不支持的活动菜单类型：{kind}")


def _same_menu_snapshot(
    before: ActivityMenuSnapshot,
    after: ActivityMenuSnapshot,
) -> bool:
    """Require the process and exact ordered menu to remain unchanged."""

    return bool(
        before.complete
        and after.complete
        and before.kind == after.kind
        and before.pid == after.pid
        and before.process_start_ticks == after.process_start_ticks
        and before.fingerprint
        and before.fingerprint == after.fingerprint
    )


def open_loaded_activity_menu_item(
    context: Any,
    target: str | int,
    *,
    kind: ActivityMenuKind,
    source_scene_id: int,
    ocr_shape_names: Iterable[str],
    expected_scene_ids: Iterable[int],
    target_gui_name: str | None = None,
    grid: ActivityMenuGrid | None = None,
    timeout_seconds: float = 20.0,
):
    """Click one item in an already loaded activity menu and verify its page.

    The caller owns the preceding navigation which naturally loads the menu.
    It must also provide the formal OCR shapes belonging to that menu and the
    independent successor scenes.  Missing geometry or a changing Runtime
    fingerprint fails closed; this helper never falls back to a fixed point or
    unrestricted OCR.
    """

    source_scene = int(source_scene_id)
    shapes = tuple(dict.fromkeys(str(value).strip() for value in ocr_shape_names))
    shapes = tuple(value for value in shapes if value)
    if not shapes:
        raise ValueError("活动菜单导航必须声明正式 OCR Shape")
    expected = tuple(dict.fromkeys(int(value) for value in expected_scene_ids))
    if not expected:
        raise ValueError("活动菜单导航必须声明独立后继场景")

    snapshot = read_activity_menu_snapshot(kind)
    if not snapshot.complete:
        raise RuntimeError(f"活动菜单尚未完整加载：{snapshot.reason}")

    frame = context.cur_frame(update=True)
    tokens = context.ocr_tokens_in_shapes(
        source_scene,
        shapes,
        frame_data_url=frame,
    )
    plan_snapshot = snapshot
    gui_name = str(target_gui_name or "").strip()
    if gui_name:
        target_text = str(target).strip()
        matches = [
            item
            for item in snapshot.items
            if item.key == target_text
            or (item.activity_id is not None and str(item.activity_id) == target_text)
            or (item.group_type is not None and str(item.group_type) == target_text)
        ]
        if len(matches) != 1:
            raise RuntimeError("活动菜单 GUI 别名没有唯一 Runtime 身份")
        plan_snapshot = replace(
            snapshot,
            items=tuple(
                replace(item, name=gui_name) if item is matches[0] else item
                for item in snapshot.items
            ),
        )
    spatial_fragments = tuple(group_ocr_tokens(tokens))
    gui_candidates = tuple(tokens) + spatial_fragments
    plan = plan_activity_menu_click(
        plan_snapshot,
        target,
        gui_candidates,
        grid=grid or _default_grid(kind),
    )
    if not plan.ready or plan.point is None:
        observed_texts = tuple(
            dict.fromkeys(
                str(item.get("text") or "").strip()
                for item in gui_candidates
                if str(item.get("text") or "").strip()
            )
        )
        raise RuntimeError(
            f"活动菜单目标无法安全定位：{plan.reason}；"
            f"OCR候选={observed_texts}；锚点={plan.anchors}"
        )

    refreshed = read_activity_menu_snapshot(kind)
    if not _same_menu_snapshot(snapshot, refreshed):
        raise RuntimeError("活动菜单在定位后发生变化，拒绝点击旧坐标")

    context.click_frame_point(source_scene, *plan.point)
    landed = yield from context.wait_scene(
        expected,
        wait=timeout_seconds,
        label=f"活动菜单：等待 {target} 后继",
    )
    landed_scene_id = int(getattr(landed, "scene_id", getattr(landed, "id", landed)))
    if landed_scene_id not in expected:
        raise RuntimeError(
            f"活动菜单 {target} 点击后落在 #{landed_scene_id}，"
            f"不是预期后继 {list(expected)}"
        )
    return landed


__all__ = ["open_loaded_activity_menu_item"]
