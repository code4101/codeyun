from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XIANZANG_TASK_SCENE_ID,
    XianzangPageResult,
    open_xianzang_tab,
)
from backend.core.fanxiu.instrumentation.bothdraw import read_bothdraw_task_runtime


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
    runtime: Any,
    *,
    progress_shape_title: str = "进度",
    retry_seconds: float = 1.0,
    max_clicks: int = 20,
) -> XianzangTaskCompletionResult:
    """Claim task rewards only when QuestMgr reports a claimable task.

    The game sorts claimable tasks to the first row.  OCR is deliberately not
    used as completion evidence: an unavailable runtime snapshot is a failure,
    never an implicit "all claimed" result.
    """

    click_limit = max(1, int(max_clicks))
    clicked_count = 0
    last_progress: XianzangTaskProgress | None = None
    snapshot = read_bothdraw_task_runtime()
    if snapshot.get("complete") and not list(snapshot.get("claimable") or []):
        # QuestMgr is the authoritative claim ledger.  An all-claimed retry
        # does not need to open the execution-only task tab, whose first click
        # can be swallowed while the freshly opened activity is still
        # settling.  Keep this path zero-click and verify that we remain on
        # the Xianzang main page for the following workflow phase.
        final_page = open_xianzang_tab(runtime, "蓬莱仙藏")
        return XianzangTaskCompletionResult(
            clicked_count=0,
            stop_reason="all_claimed",
            last_progress=None,
            final_page=final_page,
        )

    task_page = open_xianzang_tab(runtime, "任务")
    if task_page.scene_id != XIANZANG_TASK_SCENE_ID or task_page.score < 80.0:
        raise RuntimeError("未可靠进入 #450 蓬莱仙藏任务页，拒绝识别或点击任务")
    if not snapshot.get("complete"):
        # A cold process may only materialize QuestMgr's activity task rows
        # after the real task page has been opened.  Navigation is allowed to
        # load that read-only source naturally; it is then re-read strictly.
        snapshot = read_bothdraw_task_runtime()
        if not snapshot.get("complete"):
            raise RuntimeError(
                str(snapshot.get("reason") or "QuestMgr 活动任务状态不完整")
            )

    pending_task_id: int | None = None
    while True:
        tasks = list(snapshot.get("tasks") or [])
        if pending_task_id is not None:
            confirmed = next(
                (
                    item
                    for item in tasks
                    if int(item.get("task_id") or 0) == pending_task_id
                ),
                None,
            )
            if confirmed is None or confirmed.get("state") != "claimed":
                raise RuntimeError(
                    f"点击任务 {pending_task_id} 后 QuestMgr 未确认已领取"
                )
            pending_task_id = None

        claimable = list(snapshot.get("claimable") or [])
        if not claimable:
            stop_reason = "all_claimed"
            break
        if clicked_count >= click_limit:
            raise RuntimeError(
                f"蓬莱仙藏任务连续领取超过 {click_limit} 次仍未收敛，拒绝继续点击"
            )
        scene_id, score, frame = runtime.current_scene(
            [XIANZANG_TASK_SCENE_ID],
            update=True,
        )
        if int(scene_id or 0) != XIANZANG_TASK_SCENE_ID or float(score or 0) < 80.0:
            raise RuntimeError("领取前未可靠识别 #450，拒绝点击任务")
        target = claimable[0]
        runtime.click_shape(
            XIANZANG_TASK_SCENE_ID,
            str(progress_shape_title),
            frame_data_url=frame,
        )
        clicked_count += 1
        pending_task_id = int(target.get("task_id") or 0)
        time.sleep(max(0.0, float(retry_seconds)))
        snapshot = read_bothdraw_task_runtime()
        if not snapshot.get("complete"):
            raise RuntimeError(
                str(snapshot.get("reason") or "QuestMgr 活动任务状态不完整")
            )

    final_page = open_xianzang_tab(runtime, "蓬莱仙藏")
    return XianzangTaskCompletionResult(
        clicked_count=clicked_count,
        stop_reason=stop_reason,
        last_progress=last_progress,
        final_page=final_page,
    )
