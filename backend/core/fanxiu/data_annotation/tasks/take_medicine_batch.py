from __future__ import annotations

"""Live-verified, fail-closed batch medicine Job.

The irreversible boundary is #594[确认]. Only the Cell that opened #594 may
cross it, exactly once. #595 is the authoritative active-queue UI. #507 or a
direct landing on #405/#408 is the accepted immediate-completion branch.
There is deliberately no API or selector for #595[停止服用].
"""

from pathlib import Path
import threading
from typing import Any


STANDARD_JOB_ID = "take-medicine-batch"


class TakeMedicineBatchSafetyError(RuntimeError):
    """The visual transaction cannot safely make further progress."""


class TakeMedicineBatchTaskMixin:
    take_medicine_scene_ids = (34, 20, 405, 408, 507, 593, 594, 595)

    def _take_medicine_finish(self, *, task_id: str, result: str, message: str,
                              confirmation_clicks: int, log_level: str) -> dict[str, Any]:
        self._persist_scheduler_task_next_time(task_id, None)
        self._log(log_level, message)
        return {"result": result, "message": message, "confirmation_clicks": confirmation_clicks}

    def _take_medicine_open_training_page(self, runtime: Any, *, scene_id: int, timeout: float):
        if scene_id == 34:
            yield from runtime.wait_click(34, "进入绿瓶", timeout=timeout)
            yield from runtime.wait_view(20, timeout=timeout, label="服用丹药：等待绿瓶 #20")
            scene_id = 20
        if scene_id != 20:
            return scene_id
        for _attempt in range(30):
            changed = yield from runtime.scroll_shape_content(20, "菜单", direction="left")
            if not changed:
                break
        else:
            raise TakeMedicineBatchSafetyError("服用丹药：绿瓶菜单向左归位 30 次仍未到边界")
        frame = runtime.cur_frame(update=True)
        runtime.click_ocr_text(
            20, "修炼", in_shapes=["菜单"], frame_data_url=frame,
            anchor="top_center", offset=(0.0, -1.0), offset_unit="height",
        )
        landed = yield from runtime.wait_view(
            405, 408, timeout=timeout, label="服用丹药：等待修炼页 #405/#408",
        )
        return int(landed.id)

    def _take_medicine_open_confirmation(self, runtime: Any, *, timeout: float):
        runtime.click_shape_center(593, "一键服用")
        try:
            confirmation = (yield from runtime.wait_view(
                594, timeout=timeout, label="服用丹药：等待批量确认 #594",
            ))
        except TimeoutError:
            scene_id, _score, _frame = runtime.current_scene([593, 594], update=True)
            if scene_id == 593:
                return None
            raise
        frame = runtime.cur_frame(update=True)
        text = "".join(
            str(token.get("text") or "")
            for token in runtime.full_frame_ocr_tokens(frame)
            if isinstance(token, dict)
        )
        compact = "".join(text.split())
        if "服用列表" not in compact or "吸收药效时间需" not in compact:
            raise TakeMedicineBatchSafetyError(
                "服用丹药：#594 不可逆确认前缺少“服用列表 + 吸收药效时间需”双 OCR 证据，拒绝点击"
            )
        return confirmation

    def _execute_take_medicine_batch_task(
        self, ctx: dict[str, Any], stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise TakeMedicineBatchSafetyError("服用丹药：缺少资产树路径")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        timeout = max(5.0, min(60.0, float(payload.get("timeout_seconds") or 20.0)))
        task_id = str(payload.get("__scheduler_task_id") or STANDARD_JOB_ID)

        scene_id, score, _frame = runtime.current_scene(list(self.take_medicine_scene_ids), update=True)
        if scene_id is None:
            raise TakeMedicineBatchSafetyError(
                f"服用丹药：当前不是已验收链路场景，score={float(score or 0):.0f}%"
            )
        if scene_id == 595:
            return self._take_medicine_finish(
                task_id=task_id, result="already_running",
                message="服用丹药：已在炼化中 #595，零点击幂等结束",
                confirmation_clicks=0, log_level="skip",
            )
        if scene_id == 594:
            return self._take_medicine_finish(
                task_id=task_id, result="manual_required",
                message="服用丹药：启动时已停在批量确认 #594；已取消自动重试，等待人工判断",
                confirmation_clicks=0, log_level="warning",
            )
        if scene_id == 507:
            yield from runtime.wait_click(507, "确认", timeout=timeout)
            yield from runtime.wait_view(405, 408, timeout=timeout, label="服用丹药：关闭即时结果 #507")
            return self._take_medicine_finish(
                task_id=task_id, result="completed",
                message="服用丹药：已确认即时完成结果 #507",
                confirmation_clicks=0, log_level="success",
            )

        scene_id = yield from self._take_medicine_open_training_page(
            runtime, scene_id=int(scene_id), timeout=timeout,
        )
        if scene_id in {405, 408}:
            landed = yield from runtime.click_shape_center_then_view(
                scene_id, "服用丹药", 593, 595, timeout=timeout,
                label="服用丹药：等待选择页或已有炼化队列",
            )
            scene_id = int(landed.id)
        if scene_id == 595:
            return self._take_medicine_finish(
                task_id=task_id, result="already_running",
                message="服用丹药：入口已落到炼化中 #595，未重复服用",
                confirmation_clicks=0, log_level="skip",
            )
        if scene_id != 593:
            raise TakeMedicineBatchSafetyError(f"服用丹药：未到选择页 #593，当前 #{scene_id}")

        confirmation = yield from self._take_medicine_open_confirmation(runtime, timeout=timeout)
        if confirmation is None:
            return self._take_medicine_finish(
                task_id=task_id, result="nothing_to_do",
                message="服用丹药：一键服用后仍在 #593，当前没有可批量服用的丹药",
                confirmation_clicks=0, log_level="skip",
            )

        self._raise_if_stopped(stop_event)
        runtime.click_shape_center(594, "确认")
        landed = yield from runtime.wait_view(
            595, 507, 405, 408, timeout=timeout,
            label="服用丹药：确认后只读等待炼化队列或即时完成",
        )
        landing_id = int(landed.id)
        if landing_id == 507:
            yield from runtime.wait_click(507, "确认", timeout=timeout)
            yield from runtime.wait_view(405, 408, timeout=timeout, label="服用丹药：关闭即时结果 #507")
            result, message = "completed", "服用丹药：已单次确认批量服用，并验收即时完成结果 #507"
        elif landing_id in {405, 408}:
            result, message = "completed", "服用丹药：已单次确认批量服用，并验收直接完成落点"
        else:
            result, message = "started", "服用丹药：已单次确认批量服用，并由 #595 炼化中页面验收"
        return self._take_medicine_finish(
            task_id=task_id, result=result, message=message,
            confirmation_clicks=1, log_level="success",
        )


__all__ = ["STANDARD_JOB_ID", "TakeMedicineBatchSafetyError", "TakeMedicineBatchTaskMixin"]
