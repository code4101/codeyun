from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Sequence

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XIANZANG_TASK_SCENE_ID,
    XianzangPageResult,
    open_xianzang_tab,
)


@dataclass(frozen=True)
class XianzangTaskProgress:
    numerator: int
    denominator: int
    text: str

    @property
    def complete(self) -> bool:
        return self.numerator == self.denominator


@dataclass(frozen=True)
class XianzangTaskCompletionResult:
    clicked_count: int
    stop_reason: str
    last_progress: XianzangTaskProgress | None
    final_page: XianzangPageResult


def parse_xianzang_task_progress(
    tokens: Sequence[dict[str, Any]],
) -> XianzangTaskProgress | None:
    """Read exactly two ordered integers from the tight progress region."""

    candidates = [
        str(fragment.get("text") or "")
        for fragment in group_ocr_tokens(list(tokens))
    ]
    if not candidates:
        candidates = [
            str(token.get("text") or "")
            for token in tokens
            if isinstance(token, dict)
        ]
    candidates.append("".join(candidates))
    for text in candidates:
        values = parse_ocr_values(text, expected_count=2)
        if values is None:
            continue
        numerator, denominator = values
        if denominator <= 0 or numerator < 0 or numerator > denominator:
            continue
        return XianzangTaskProgress(numerator, denominator, text)
    return None


def complete_xianzang_tasks(
    context: Any,
    *,
    progress_shape_title: str = "进度",
    observer_shape_title: str = "第三行任务标题",
    retry_seconds: float = 3.0,
    no_change_confirmations: int = 3,
    max_clicks: int = 20,
) -> XianzangTaskCompletionResult:
    """Claim the first-row task reward until the task page becomes stable.

    Penglai keeps a claimable task at the top of the list.  Clicking the
    annotated first-row progress region is idempotent when nothing is
    claimable.  A changed first-row OCR observation proves that the list
    advanced; three consecutive unchanged observations close the task phase.
    This deliberately follows the Beast Abyss GUI convergence pattern without
    depending on QuestMgr Runtime state.
    """

    confirmations = max(1, int(no_change_confirmations))
    click_limit = max(confirmations, int(max_clicks))
    clicked_count = 0
    last_progress: XianzangTaskProgress | None = None
    task_page = yield from open_xianzang_tab(context, "任务")
    if task_page.scene_id != XIANZANG_TASK_SCENE_ID or task_page.score < 80.0:
        raise RuntimeError("未可靠进入 #450 蓬莱仙藏任务页，拒绝识别或点击任务")

    def observe(frame: str) -> str:
        text = context.ocr_text_in_shapes(
            XIANZANG_TASK_SCENE_ID,
            (str(observer_shape_title),),
            padding=8,
            frame_data_url=frame,
            crop=True,
        )
        return re.sub(r"\s+", "", str(text or "")).strip() or "<empty>"

    frame = context.cur_frame(update=True)
    observer = observe(frame)
    unchanged = 0
    while unchanged < confirmations:
        if clicked_count >= click_limit:
            raise RuntimeError(
                f"蓬莱仙藏任务连续领取超过 {click_limit} 次仍未收敛，拒绝继续点击"
            )
        _wait_scene_match = yield from context.wait_scene([XIANZANG_TASK_SCENE_ID], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if int(scene_id or 0) != XIANZANG_TASK_SCENE_ID or float(score or 0) < 80.0:
            raise RuntimeError("领取前未可靠识别 #450，拒绝点击任务")
        context.click_shape(
            XIANZANG_TASK_SCENE_ID,
            str(progress_shape_title),
            frame_data_url=frame,
        )
        clicked_count += 1
        yield from context.wait_action_settle(max(0.0, float(retry_seconds)))
        _wait_scene_match = yield from context.wait_scene([XIANZANG_TASK_SCENE_ID], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if int(scene_id or 0) != XIANZANG_TASK_SCENE_ID or float(score or 0) < 80.0:
            raise RuntimeError("领取后未可靠识别 #450，拒绝继续点击任务")
        next_observer = observe(frame)
        if next_observer != observer:
            observer = next_observer
            unchanged = 0
        else:
            unchanged += 1

    final_page = yield from open_xianzang_tab(context, "蓬莱仙藏")
    return XianzangTaskCompletionResult(
        clicked_count=clicked_count,
        stop_reason="stable_no_change",
        last_progress=last_progress,
        final_page=final_page,
    )
