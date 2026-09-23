from __future__ import annotations

"""Batch medicine component shared by the standalone job and resource ranking.

#595 is the authoritative active-queue state. Never click 停止服用. Success
requires returning to #34; an interrupted exit rechecks game state on retry.
"""

from pathlib import Path
import threading
from typing import Any

STANDARD_JOB_ID = "take-medicine-batch"


class TakeMedicineBatchSafetyError(RuntimeError):
    """The observed game state cannot authorize the next action."""


def _wait_medicine_scene(context: Any, scenes: tuple[int, ...], timeout: float):
    match = yield from context.wait_scene(scenes, wait=timeout, label="服用丹药")
    if match is None or match.scene_id not in scenes:
        raise TakeMedicineBatchSafetyError(f"服用丹药：预期 {scenes}，实际 {match}")
    return match.scene_id


def run_take_medicine_batch_flow(context: Any, *, timeout: float = 20.0):
    """Observe before acting; return a completed result only after reaching #34.

    Existing #595 skips all consumption clicks. A preexisting #594 is a valid
    unsubmitted batch: its dedicated scene identity authorizes one confirmation.
    After that click, only an active queue or the verified training-page landing
    counts as success. Unknown landings raise and preserve the actual screen.
    Occurrence-level once-only persistence belongs to ranking checkpoints.
    """
    scene = yield from _wait_medicine_scene(context, (34, 20, 405, 408, 593, 594, 595), timeout)
    if scene == 34:
        yield from context.go_scene(20, known_paths_only=True)
        scene = yield from _wait_medicine_scene(context, (20,), timeout)
    if scene == 20:
        context.click_ocr_text(20, "修炼", in_shapes=["菜单"], match_mode="exact")
        scene = yield from _wait_medicine_scene(context, (405, 408), timeout)
    if scene in (405, 408):
        context.click_shape_center(scene, "服用丹药")
        scene = yield from _wait_medicine_scene(context, (593, 595), timeout)
    confirmation_clicks = 0
    if scene == 595:
        outcome = "already_running"
    else:
        if scene == 593:
            context.click_shape_center(593, "一键服用")
            # A selection page alone does not prove an empty inventory; fail
            # closed unless a dedicated confirmation is positively recognized.
            scene = yield from _wait_medicine_scene(context, (594,), timeout)
        if scene != 594:
            raise TakeMedicineBatchSafetyError(f"服用丹药：未到批量确认，实际 #{scene}")
        context.click_shape_center(594, "确认")
        confirmation_clicks = 1
        scene = yield from _wait_medicine_scene(context, (595, 405, 408), timeout)
        outcome = "started" if scene == 595 else "completed"
    if scene == 595:
        context.click_shape_center(595, "返回")
        yield from _wait_medicine_scene(context, (405, 408), timeout)
    yield from context.go_scene(34, known_paths_only=True)
    yield from _wait_medicine_scene(context, (34,), timeout)
    return {
        "status": "completed", "result": outcome, "current_scene": 34,
        "confirmation_clicks": confirmation_clicks,
        "message": "服用丹药：已在服用，幂等跳过并返回 #34" if outcome == "already_running"
        else "服用丹药：已确认服用并返回 #34",
    }


class TakeMedicineBatchTaskMixin:
    def _execute_take_medicine_batch_task(
        self, ctx: dict[str, Any], stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        path = ctx.get("asset_tree_path")
        if not isinstance(path, Path):
            raise TakeMedicineBatchSafetyError("服用丹药：缺少资产树路径")
        context = self._behavior_tree_context(ctx, path, stop_event=stop_event)
        result = yield from run_take_medicine_batch_flow(
            context, timeout=max(5.0, min(60.0, float(payload.get("timeout_seconds") or 20))),
        )
        self._persist_scheduler_task_next_time(str(payload.get("__scheduler_task_id") or STANDARD_JOB_ID), None)
        self._log("success", result["message"])
        return result


__all__ = ["STANDARD_JOB_ID", "TakeMedicineBatchSafetyError", "TakeMedicineBatchTaskMixin",
           "run_take_medicine_batch_flow"]
