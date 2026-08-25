from __future__ import annotations

import threading
from typing import Any, Mapping


def enter_dongtian_home_for_research(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    payload: Mapping[str, Any] | None = None,
):
    """Enter #279 from a narrow safe origin and stop for manual research.

    This is an ordinary Cell helper, not a registered Job.  It deliberately
    owns no Scheduler lifecycle, next_time, profit claim, return, occupation,
    or battle behavior.
    """

    task_label = "洞天_研究入口"
    research_payload = dict(payload or {})
    research_payload["max_scrolls"] = min(
        30,
        max(0, int(research_payload.get("max_scrolls") or 24)),
    )
    runtime = runner._fanxiu_runtime(ctx, stop_event=stop_event)
    allowed_start_scene_ids = (34, 66, 477, 69)
    scene_id, _score, frame = runtime.current_scene(
        allowed_start_scene_ids,
        update=True,
    )
    text = runtime.ocr_text(frame)
    if scene_id not in allowed_start_scene_ids:
        raise RuntimeError(
            f"{task_label}：只接受 #34/#66/#477/#69 作为研究入口起点，当前场景未确认"
        )

    if scene_id != 69:
        recovered = yield from runner._leave_world_side_scene_if_present(
            ctx,
            stop_event,
            frame,
            text,
            label=task_label,
        )
        if recovered:
            scene_id, _score, frame = runtime.current_scene(
                [34, 69],
                update=True,
            )
            text = runtime.ocr_text(frame)
        scene_id = yield from runner._enter_daily_from_world_like(
            ctx,
            runtime,
            stop_event,
            frame,
            scene_id,
            text,
            label=task_label,
        )
    if scene_id != 69:
        raise RuntimeError(f"{task_label}：归一化后未确认进入 #69，已停止")

    daily_status = yield from runner._open_daily_entry_from_daily(
        ctx,
        stop_event,
        research_payload,
        task_label=task_label,
        title_pattern=r"洞天|九曜\s*玄墨",
        progress_can_mark_done=False,
    )
    if daily_status != "open":
        raise RuntimeError(
            f"{task_label}：#69 未找到可打开的“洞天/九曜玄墨”入口，status={daily_status}"
        )
    landing_scene_id = yield from runner._wait_daily_dongtian_home(
        ctx,
        stop_event,
        research_payload,
        task_label=task_label,
        allow_claim_page=False,
    )
    if int(landing_scene_id) != 279:
        raise RuntimeError(
            f"{task_label}：入口落点不是 #279，当前 #{landing_scene_id}，已停止"
        )
    return {
        "status": "ready_for_research",
        "scene_id": 279,
        "message": "已到洞天福地 #279，未执行收益、返回或占领动作",
    }
