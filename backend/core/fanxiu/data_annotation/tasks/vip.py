"""VIP 修为免费礼包：领取、返回世界和次日零点调度。

聚合运行时由父作业持有调度写入权，独立运行时保留原有 VIP 作业入口。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.autogui import View

from ..job_times import next_business_time


class DailyVipTaskMixin:
    def _execute_daily_vip_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_vip资产树路径，无法执行作业")

        task_label = "日常_vip"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 34:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：确认/恢复到世界 #34 后点击 VIP",
                    phase="daily_vip_go_world",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"{task_label}：确认/恢复到 #34")
            yield from context.go_scene(34)
            yield from context.wait_scene([34], label=f"{task_label}：等待世界 #34")

        vip_shape = str(payload.get("vip_shape") or "[vip]")
        with self._lock:
            self._set_status_locked("running", f"{task_label}：点击 #34「{vip_shape}」", phase="daily_vip_click", current_scene=34)
            self._log_locked("action", f"{task_label}：点击 #34「{vip_shape}」")
        yield from context.wait_click(
            34,
            vip_shape,
            timeout=float(payload.get("vip_click_timeout") or payload.get("shape_click_timeout") or 8.0),
        )
        yield from context.wait_action_settle(float(payload.get("vip_settle_seconds") or 2.0))

        yield from context.wait_scene([290], label=f"{task_label}：等待 VIP 月卡页 #290")
        yield from context.wait_click(
            290,
            "每日限购",
            timeout=float(payload.get("daily_limit_click_timeout") or payload.get("shape_click_timeout") or 8.0),
        )
        yield from context.wait_action_settle(float(payload.get("daily_limit_settle_seconds") or 1.5))

        yield from context.wait_scene([291], label=f"{task_label}：等待每日限购页 #291")
        yield from context.wait_click(
            291,
            "修为",
            timeout=float(payload.get("xiuwei_click_timeout") or payload.get("shape_click_timeout") or 8.0),
        )
        yield from context.wait_action_settle(float(payload.get("xiuwei_settle_seconds") or 1.5))

        xiuwei_view = yield from context.wait_scene([292, 291], label=f"{task_label}：等待修为限购页 #292")
        xiuwei_scene_id = int(xiuwei_view.id) if isinstance(xiuwei_view, View) and xiuwei_view.id is not None else int(xiuwei_view)
        if xiuwei_scene_id == 291:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：点击「修为」后仍在 #291，未见 #292 免费礼包页，按今日无免费可领处理",
                    phase="daily_vip_xiuwei_no_free",
                    current_scene=291,
                )
                self._log_locked("skip", f"{task_label}：点击「修为」后仍在 #291，未见 #292 免费礼包页")
            yield from self._return_daily_vip_to_world(context, payload, task_label=task_label, start_scene=291)
            self._record_daily_vip_done(payload, message="修为页未见免费礼包，已返回世界")
            return "success"

        free_status = yield from self._click_daily_vip_free_or_return(ctx, stop_event, payload, task_label=task_label)
        if free_status != "success":
            yield from self._return_daily_vip_to_world(context, payload, task_label=task_label, start_scene=291)
            self._record_daily_vip_done(payload, message="修为免费礼包未匹配，已返回世界")
            return "success"

        yield from self._return_daily_vip_to_world(context, payload, task_label=task_label, start_scene=292)

        self._record_daily_vip_done(payload, message="已点击修为免费礼包并返回世界")
        return "success"

    def _return_daily_vip_to_world(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        start_scene: int,
    ):
        route = [(292, (291,)), (291, (290, 20, 34)), (290, (34,))]
        start_index = next((index for index, (scene_id, _target_ids) in enumerate(route) if scene_id == int(start_scene)), 0)
        settle_seconds = float(payload.get("return_click_settle_seconds") or 1.0)
        wait_timeout = float(payload.get("return_world_wait_timeout") or 18.0)
        for scene_id, target_ids in route[start_index:]:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：点击 #{scene_id}「返回」",
                    phase="daily_vip_return_world",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"{task_label}：点击 #{scene_id}「返回」")
            context.click_shape_center(scene_id, "返回")
            yield from context.wait_action_settle(settle_seconds)
            target = yield from context.wait_scene(target_ids, wait=wait_timeout, label=f"{task_label}：等待返回 {'/'.join(f'#{target_id}' for target_id in target_ids)}")
            target_id = int(target.id) if isinstance(target, View) and target.id is not None else int(target)
            if target_id == 34:
                return "success"
            if target_id == 20:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：从 #20 回到世界",
                        phase="daily_vip_return_world",
                        current_scene=20,
                    )
                    self._log_locked("action", f"{task_label}：点击 #20「回到世界」")
                context.click_shape_center(20, "回到世界")
                yield from context.wait_action_settle(settle_seconds)
                yield from context.wait_scene([34], wait=wait_timeout, label=f"{task_label}：等待返回 #34")
                return "success"
        return "success"

    def _click_daily_vip_free_or_return(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        timeout = float(payload.get("free_match_timeout_seconds") or 60.0)
        poll_seconds = float(payload.get("free_match_poll_seconds") or 1.0)
        threshold = float(payload.get("free_match_threshold") or self.overlay_threshold)
        start = time.monotonic()
        last_score = 0.0
        while time.monotonic() - start < timeout:
            self._raise_if_stopped(stop_event)
            frame = context.cur_frame(update=True)
            last_score = float(context.shape_score(292, "免费", frame_data_url=frame) or 0.0)
            if last_score >= threshold:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：#292「免费」匹配 {last_score:.0f}%，点击领取",
                        phase="daily_vip_click_free",
                        current_scene=292,
                    )
                    self._log_locked("action", f"{task_label}：点击 #292「免费」")
                yield from context.wait_click(292, "免费", timeout=float(payload.get("free_click_timeout") or 8.0))
                yield from context.wait_action_settle(float(payload.get("free_click_settle_seconds") or 1.5))
                return "success"
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待 #292「免费」匹配，当前 {last_score:.0f}%",
                    phase="daily_vip_wait_free",
                    current_scene=292,
                )
            yield from context.wait_action_settle(max(0.2, poll_seconds))

        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：#292「免费」{timeout:.0f}s 未匹配，点击返回",
                phase="daily_vip_free_not_found",
                current_scene=292,
            )
            self._log_locked("skip", f"{task_label}：#292「免费」未匹配，最后分数 {last_score:.0f}%")
        context.click_shape_center(292, "返回")
        yield from context.wait_action_settle(float(payload.get("return_click_settle_seconds") or 1.5))
        return "skipped"

    def _record_daily_vip_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_vip_reset_time_text()
        if bool(payload.get("schedule", True)):
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-vip"),
                next_time,
            )
            self._log("success", f"日常_vip：{message}，下次 {next_time}")
        else:
            # Aggregated run: the parent Job writes the canonical next_time.
            self._log("success", f"日常_vip：{message}")
        return next_time

    def _next_daily_vip_reset_time_text(self) -> str:
        return next_business_time(["00:00"])
