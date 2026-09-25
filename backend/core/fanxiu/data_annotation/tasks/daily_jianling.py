"""日常剑试：进入试炼、扫荡、领取与返回世界。

通用日常入口、识别和离场由组合后的执行器提供；导入不执行游戏动作。
"""
from __future__ import annotations
import re
import threading
import time
from pathlib import Path
from typing import Any
from pyxllib.prog import BehaviorTreeStatus
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class DailyJianlingTaskMixin:
    def _next_daily_jianling_reset_time_text(self) -> str:
        return self._next_daily_boss_reset_time_text()

    def _record_daily_jianling_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_jianling_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-jianling")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_剑灵：{message}，下次 {next_time}")
        return next_time

    def _daily_jianling_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(re.search(r"(?:挑战或扫荡淬剑试炼|淬剑试炼).*(?:1/1|已完成)", normalized))

    def _daily_jianling_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(
            re.search(r"剩余次数[:：]?(?:0|O)(?:\\+)?", normalized, re.IGNORECASE)
            or "已通关" in normalized
            or "当前秘境已全通" in normalized
        )

    def _daily_jianling_text_is_result(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return (
            "扫荡奖励" in normalized
            or "点击屏幕继续" in normalized
            or "点击继续" in normalized
            or ("获得了" in normalized and ("仙侣神通" in normalized or "修为境界" in normalized))
        )

    def _daily_jianling_text_is_main(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "淬剑试炼" in normalized and ("通关进度" in normalized or "剩余次数" in normalized)

    def _execute_daily_jianling_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_剑灵资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([190, 192, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        current_text = context.ocr_text(frame)
        if scene_id == 192 or self._daily_jianling_text_is_result(current_text):
            yield from self._finish_daily_jianling_result(ctx, stop_event)
            scene_id = 190
        if self._daily_jianling_text_is_main(current_text):
            scene_id = 190
        if scene_id == 190:
            text = context.ocr_text(update=True)
            if self._daily_jianling_remaining_zero(text):
                self._record_daily_jianling_done(payload, message="淬剑试炼剩余次数为 0")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_jianling_to_world(ctx, stop_event),
                    label="日常_剑灵",
                    repeat_risk="重复扫荡",
                )
                return "success"
            yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
            return "success"

        if scene_id != 69:
            world_text = context.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                context,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_剑灵",
            )

        daily_status = yield from context.open_daily_entry(
            label="日常_剑灵",
            title_pattern=r"挑战或扫荡淬剑试炼|淬剑试炼|淬剑|剑试",
            max_scrolls=self._payload_int(payload, "max_scrolls", "jianling_max_scrolls", default=30),
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-jianling",
                task_type="daily_jianling",
                label="日常_剑灵",
                entry_label="淬剑试炼",
            )
            return "skipped"
        if daily_status == "done":
            self._record_daily_jianling_done(payload, message="日常列表显示已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_jianling_to_world(ctx, stop_event),
                label="日常_剑灵",
                repeat_risk="重复扫荡",
            )
            return "success"
        yield from context.wait_scene([190], label="日常_剑灵：等待淬剑试炼 #190")
        yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_jianling_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from context.open_daily_entry(
            label="日常_剑灵",
            title_pattern=r"挑战或扫荡淬剑试炼|淬剑试炼|淬剑|剑试",
            max_scrolls=self._payload_int(payload, "max_scrolls", "jianling_max_scrolls", default=30),
        )
        if status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-jianling",
                task_type="daily_jianling",
                label="日常_剑灵",
                entry_label="淬剑试炼",
            )
            return "skipped"
        if status != "open":
            return status
        yield from context.wait_scene([190], label="日常_剑灵：等待淬剑试炼 #190")
        return "open"

    def _run_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：点击扫荡", phase="daily_jianling_sweep", current_scene=190)
            self._log_locked("action", "日常_剑灵：点击 #190「扫荡」")
        yield from context.wait_click(190, "扫荡")
        # #191 is a Layer 0 popup.  The guard recognizes it as the declared
        # landing of「扫荡」and clicks「进行扫荡」before returning control.
        confirm_result = yield from self._confirm_daily_jianling_sweep(ctx, stop_event)
        if confirm_result == "result":
            yield from self._finish_daily_jianling_result(ctx, stop_event)
        self._record_daily_jianling_done(payload, message="淬剑试炼扫荡完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_jianling_to_world(ctx, stop_event),
            label="日常_剑灵",
            repeat_risk="重复扫荡",
        )

    def _confirm_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：等待 Layer 0 完成扫荡确认", phase="daily_jianling_confirm_sweep", current_scene=None)
            self._log_locked("detail", "日常_剑灵：#191 扫荡确认由 Layer 0 守护处理")
        start = time.monotonic()
        timeout = 18.0
        min_main_return_seconds = 8.0
        main_seen_count = 0
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([190, 192], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, float(score), text or last_text
            if scene_id == 192 or self._daily_jianling_text_is_result(text):
                return "result"
            if scene_id == 190 or self._daily_jianling_text_is_main(text):
                main_seen_count += 1
                if time.monotonic() - start >= min_main_return_seconds and main_seen_count >= 2:
                    return "main"
            else:
                main_seen_count = 0
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"日常_剑灵：等待扫荡结果超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_剑灵：等待扫荡结果，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_jianling_wait_result",
                    current_scene=scene_id,
                )

    def _finish_daily_jianling_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(8):
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([190, 192], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 190 or ("淬剑试炼" in text and "通关进度" in text):
                return "success"
            if scene_id == 192 or "点击" in text or self._daily_jianling_text_is_result(text):
                with self._lock:
                    self._set_status_locked("running", f"日常_剑灵：关闭扫荡结果 {index + 1}", phase="daily_jianling_continue_result", current_scene=192)
                    if scene_id == 192:
                        self._log_locked("action", "日常_剑灵：点击 #192「点击继续」")
                    else:
                        self._log_locked("action", "日常_剑灵：结果页未识别为 #192，按 OCR「点击屏幕继续」收口")
                if scene_id == 192:
                    yield from context.wait_click(192, "点击继续")
                else:
                    context.click_frame_point({"width": 900, "height": 1600}, 450, 1380)
                    yield from context.wait_action_settle()
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_剑灵：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_剑灵：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_jianling_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([190, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id is None and self._daily_jianling_text_is_main(text):
            scene_id = 190
        if scene_id == 190:
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：退出淬剑试炼", phase="daily_jianling_exit_main", current_scene=190)
                self._log_locked("action", "日常_剑灵：点击 #190「返回」")
            yield from context.wait_click(190, "返回")
            scene_id, _score = yield from self._wait_expected_scene(
                ctx,
                stop_event,
                [69, 34],
                timeout=18.0,
                label="日常_剑灵：等待日常 #69 或世界 #34",
            )
        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：从日常列表返回世界", phase="daily_jianling_return_daily", current_scene=69)
                self._log_locked("action", "日常_剑灵：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            yield from context.wait_scene([34], wait=18.0, label="日常_剑灵：等待世界 #34")
        yield from self._ensure_outer_world(ctx, stop_event, label="日常_剑灵")
        return "success"
