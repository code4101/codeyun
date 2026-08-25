from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


def _view_id(value: Any) -> int | None:
    raw_id = getattr(value, "id", value)
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def execute_lilian_claim_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
):
    """Claim the current Lilian resources once, then return to world #34."""

    asset_tree_path = ctx.get("asset_tree_path")
    if not isinstance(asset_tree_path, Path):
        raise RuntimeError("历练_领取：缺少资产树路径，无法执行")
    runtime = runner._fanxiu_runtime(
        ctx,
        asset_tree_path,
        stop_event=stop_event,
    )

    landed = yield from runtime.wait_click_then_view(
        34,
        "大地图",
        425,
        timeout=float(payload.get("lilian_map_timeout") or 20.0),
    )
    if _view_id(landed) != 425:
        raise RuntimeError("历练_领取：打开大地图后未进入 #425")

    landed = yield from runtime.wait_click_then_view(
        425,
        "历练按钮",
        427,
        timeout=float(payload.get("lilian_panel_timeout") or 20.0),
    )
    if _view_id(landed) != 427:
        raise RuntimeError("历练_领取：点击历练按钮后未进入 #427")

    # “确认”可能只完成一次确认而不切换场景；成功点击一次即可。
    yield from runtime.wait_click(
        427,
        "确认",
        timeout=float(payload.get("lilian_confirm_timeout") or 20.0),
    )
    yield from runtime.wait_action_settle(
        float(payload.get("lilian_confirm_settle_seconds") or 1.0)
    )

    yield from runtime.wait_click(
        427,
        "资源",
        timeout=float(payload.get("lilian_resource_timeout") or 20.0),
    )
    landed = yield from runtime.wait_view(
        441,
        timeout=float(payload.get("lilian_resource_view_timeout") or 20.0),
        label="历练_领取：等待资源页",
    )
    if _view_id(landed) != 441:
        raise RuntimeError("历练_领取：点击资源后未进入 #441")

    yield from runtime.wait_click(
        441,
        "一键收取",
        timeout=float(payload.get("lilian_claim_timeout") or 20.0),
    )
    yield from runtime.wait_action_settle(
        float(payload.get("lilian_claim_settle_seconds") or 1.0)
    )

    yield from runtime.goto_view(34)
    scene_id, _score, _frame = runtime.current_scene([34], update=True)
    if scene_id != 34:
        raise RuntimeError(
            f"历练_领取：收取后未返回 #34，当前 #{scene_id or 'unknown'}"
        )

    return {
        "result": "success",
        "message": "历练_领取：已点击一次一键收取并返回 #34",
        "current_scene": 34,
    }
