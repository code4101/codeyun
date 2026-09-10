from __future__ import annotations

"""Standard navigation through the dynamic #34 lower function menu."""

from typing import Any, Iterable

from backend.core.fanxiu.instrumentation.world_menu import read_world_menu_snapshot
from backend.core.fanxiu.runtime_gui.world_menu import plan_world_menu_click


def open_world_menu_function(
    context: Any,
    target: str | int,
    *,
    expected_scene_ids: Iterable[int],
    timeout_seconds: float = 20.0,
):
    """Read the live menu, align its target to this frame, click and verify."""

    expected = tuple(dict.fromkeys(int(value) for value in expected_scene_ids))
    if not expected:
        raise ValueError("下拉菜单导航必须声明独立后继场景")
    yield from context.go_scene(34)
    yield from context.wait_click(34, "打开下方菜单")
    yield from context.wait_scene([35], wait=timeout_seconds, label="下拉菜单：等待展开")
    _wait_scene_match = yield from context.wait_scene([35], wait=5.0, required=False)
    (scene_id, score, frame) = (
        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
    )
    if scene_id != 35 or float(score or 0.0) < 80.0:
        raise RuntimeError(f"下拉菜单未可靠展开：scene={scene_id}, score={score}")
    snapshot = read_world_menu_snapshot()
    tokens = context.ocr_tokens_in_shapes(35, ("菜单",), frame_data_url=frame)
    plan = plan_world_menu_click(
        snapshot,
        target,
        tokens,
        expected_scene_ids=expected,
    )
    if not plan.ready or plan.point is None:
        raise RuntimeError(f"下拉菜单目标无法安全定位：{plan.reason}")
    context.click_frame_point(35, *plan.point)
    return (
        yield from context.wait_scene(
            expected,
            wait=timeout_seconds,
            label=f"下拉菜单：等待功能 {target} 后继",
        )
    )


__all__ = ["open_world_menu_function"]
