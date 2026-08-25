from __future__ import annotations

"""Standard navigation through the dynamic #34 lower function menu."""

from typing import Any, Iterable

from backend.core.fanxiu.instrumentation.world_menu import read_world_menu_snapshot
from backend.core.fanxiu.runtime_gui.world_menu import plan_world_menu_click


def open_world_menu_function(
    runtime: Any,
    target: str | int,
    *,
    expected_scene_ids: Iterable[int],
    timeout_seconds: float = 20.0,
):
    """Read the live menu, align its target to this frame, click and verify."""

    expected = tuple(dict.fromkeys(int(value) for value in expected_scene_ids))
    if not expected:
        raise ValueError("下拉菜单导航必须声明独立后继场景")
    yield from runtime.go_scene(34)
    yield from runtime.wait_click(34, "打开下方菜单")
    yield from runtime.wait_scene(35, timeout=timeout_seconds, label="下拉菜单：等待展开")
    frame = runtime.cur_frame(update=True)
    scene_id, score, _ = runtime.current_scene(
        views=[35], frame_data_url=frame, update=False
    )
    if scene_id != 35 or float(score or 0.0) < 80.0:
        raise RuntimeError(f"下拉菜单未可靠展开：scene={scene_id}, score={score}")
    snapshot = read_world_menu_snapshot()
    tokens = runtime.ocr_tokens_in_shapes(35, ("菜单",), frame_data_url=frame)
    plan = plan_world_menu_click(
        snapshot,
        target,
        tokens,
        expected_scene_ids=expected,
    )
    if not plan.ready or plan.point is None:
        raise RuntimeError(f"下拉菜单目标无法安全定位：{plan.reason}")
    runtime.click_frame_point(35, *plan.point)
    return (
        yield from runtime.wait_scene(
            *expected,
            timeout=timeout_seconds,
            label=f"下拉菜单：等待功能 {target} 后继",
        )
    )


__all__ = ["open_world_menu_function"]
