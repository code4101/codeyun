from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import (
    bubble_sdk_overlay_scene,
    record_bubble_hidden,
)


class BubbleHideTaskMixin:
    """Hide the 37 SDK bubble after this week's pill claim is complete."""

    def _ensure_bubble_hidden(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        now = job_now()
        facts_path = self._bubble_lifecycle_world_facts_path()
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("气泡_隐藏：缺少资产树路径")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        settle_seconds = max(0.5, min(3.0, float(payload.get("settle_seconds") or 1.0)))
        frame = runtime.cur_frame(update=True)
        overlay_scene = bubble_sdk_overlay_scene(runtime, frame=frame)
        if overlay_scene is not None:
            raise RuntimeError(
                f"气泡_隐藏：SDK 事务仍打开在 #{overlay_scene}，拒绝穿过弹窗拖拽"
            )

        match = runtime.shape_matches(421, "气泡", frame_data_url=frame)
        resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
        grace_samples = max(
            0,
            min(30, int(payload.get("bubble_appearance_grace_samples") or 0)),
        )
        grace_poll = max(
            0.2,
            min(2.0, float(payload.get("bubble_appearance_poll_seconds") or 1.0)),
        )
        for _sample in range(grace_samples if match is None else 0):
            yield from runtime.wait_action_settle(grace_poll)
            frame = runtime.cur_frame(update=True)
            overlay_scene = bubble_sdk_overlay_scene(runtime, frame=frame)
            if overlay_scene is not None:
                raise RuntimeError(
                    f"气泡_隐藏：延迟观察时出现 SDK 事务 #{overlay_scene}"
                )
            match = runtime.shape_matches(421, "气泡", frame_data_url=frame)
            resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
            if match is not None:
                break
        if match is not None:
            if not isinstance(resolved, dict) or not bool((match or {}).get("unique_match", True)):
                raise RuntimeError("气泡_隐藏：悬浮球未唯一定位，拒绝拖拽")
            start_x = float(resolved.get("x") or 0) + float(resolved.get("w") or 0) / 2
            start_y = float(resolved.get("y") or 0) + float(resolved.get("h") or 0) / 2
            self._log(
                "action",
                f"气泡_隐藏：从 ({start_x:.0f},{start_y:.0f}) 拖到正式 [拖拽隐藏] 区",
            )
            runtime.drag_shape_to_shape(
                421,
                "气泡",
                "拖拽隐藏",
                duration=0.65,
                frame_data_url=frame,
            )
            yield from runtime.wait_action_settle(settle_seconds)

        # The bubble is an Android top-level overlay.  Its absence is accepted
        # only on two fresh frames that also contain no SDK-owned modal layer;
        # the underlying game scene is irrelevant and is never navigated.
        absent_count = 0
        for _sample in range(3):
            verify_frame = runtime.cur_frame(update=True)
            overlay_scene = bubble_sdk_overlay_scene(runtime, frame=verify_frame)
            if overlay_scene is not None:
                raise RuntimeError(
                    f"气泡_隐藏：验证时意外进入 SDK 事务 #{overlay_scene}"
                )
            if runtime.shape_matches(421, "气泡", frame_data_url=verify_frame) is None:
                absent_count += 1
                if absent_count >= 2:
                    break
            else:
                absent_count = 0
            yield from runtime.wait_action_settle(settle_seconds)
        if absent_count < 2:
            raise RuntimeError("气泡_隐藏：拖拽后悬浮球仍可见，未写入隐藏成功事实")

        record_bubble_hidden(facts_path, now=now)
        message = (
            "气泡_隐藏：悬浮球原本已不可见，并经连续两帧重新确认"
            if match is None
            else "气泡_隐藏：已在连续两帧可靠世界中确认悬浮球消失"
        )
        self._log("success", message)
        return {
            "result": (
                "already_hidden" if match is None else "success"
            ),
            "message": message,
        }
