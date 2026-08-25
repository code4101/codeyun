from __future__ import annotations

import time
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_tasks import (
    XianzangTaskCompletionResult,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_navigation import (
    KUNLUN_TASK_SCENE_ID,
    open_kunlun_tab,
    read_kunlun_page,
)
from backend.core.fanxiu.instrumentation.bothdraw import read_bothdraw_task_runtime


def complete_kunlun_tasks(
    runtime: Any, *, retry_seconds: float = 1.0, max_clicks: int = 20
) -> XianzangTaskCompletionResult:
    clicked_count = 0
    snapshot = read_bothdraw_task_runtime()
    if not snapshot.get("complete"):
        raise RuntimeError(str(snapshot.get("reason") or "活动任务状态不完整"))
    if not snapshot.get("claimable"):
        current_page = read_kunlun_page(runtime, update=True)
        if current_page is None:
            raise RuntimeError("任务已全部领取，但当前不在可靠的昆仑秘藏页面")
        return XianzangTaskCompletionResult(
            clicked_count=0,
            stop_reason="all_claimed",
            last_progress=None,
            final_page=current_page,
        )

    page = open_kunlun_tab(runtime, "任务")
    if page.scene_id != KUNLUN_TASK_SCENE_ID or page.score < 80.0:
        raise RuntimeError("未可靠进入 #543 昆仑秘藏任务页，拒绝点击")
    pending_task_id: int | None = None
    while True:
        tasks = list(snapshot.get("tasks") or [])
        if pending_task_id is not None:
            confirmed = next(
                (item for item in tasks if int(item.get("task_id") or 0) == pending_task_id),
                None,
            )
            if confirmed is None or confirmed.get("state") != "claimed":
                raise RuntimeError(f"点击任务 {pending_task_id} 后未确认已领取")
            pending_task_id = None
        claimable = list(snapshot.get("claimable") or [])
        if not claimable:
            break
        if clicked_count >= max(1, int(max_clicks)):
            raise RuntimeError("昆仑秘藏任务领取未在预算内收敛")
        scene_id, score, frame = runtime.current_scene([KUNLUN_TASK_SCENE_ID], update=True)
        if int(scene_id or 0) != KUNLUN_TASK_SCENE_ID or float(score or 0) < 80.0:
            raise RuntimeError("领取前未可靠识别 #543，拒绝点击")
        runtime.click_shape(KUNLUN_TASK_SCENE_ID, "进度", frame_data_url=frame)
        clicked_count += 1
        pending_task_id = int(claimable[0].get("task_id") or 0)
        time.sleep(max(0.0, float(retry_seconds)))
        snapshot = read_bothdraw_task_runtime()
        if not snapshot.get("complete"):
            raise RuntimeError(str(snapshot.get("reason") or "活动任务状态不完整"))
    final_page = open_kunlun_tab(runtime, "昆仑秘藏")
    return XianzangTaskCompletionResult(
        clicked_count=clicked_count,
        stop_reason="all_claimed",
        last_progress=None,
        final_page=final_page,
    )
