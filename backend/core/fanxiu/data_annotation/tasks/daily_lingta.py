"""日常灵塔扫荡与收尾；与灵塔_挑战连续闯关入口分开。

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


class DailyLingtaTaskMixin:
    def _next_daily_lingta_reset_time_text(self) -> str:
        return self._next_daily_boss_reset_time_text()

    def _record_daily_lingta_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_lingta_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingta")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_灵塔：{message}，下次 {next_time}")
        return next_time

    def _daily_lingta_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(re.search(r"(?:挑战或扫荡混沌灵塔|混沌灵塔|灵塔).*(?:1/1|已完成)", normalized))

    def _daily_lingta_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(re.search(r"剩余次数[:：]?(?:0|O)(?:\\+)?", normalized, re.IGNORECASE))

    def _daily_lingta_text_is_main(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "混沌灵塔" in normalized and ("剩余次数" in normalized or "扫荡" in normalized)

    def _daily_lingta_text_is_green_bottle_like(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        markers = ("绿瓶", "炼丹", "丹炉", "丹药", "灵液", "世界")
        return sum(1 for marker in markers if marker in normalized) >= 2

    def _leave_daily_lingta_green_bottle(self, ctx: dict[str, Any], stop_event: threading.Event):
        return (yield from self._leave_green_bottle_to_world(ctx, stop_event, label="日常_灵塔"))

    def _execute_daily_lingta_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_灵塔资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([196, 194, 193, 69, 34, 20], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 20:
            yield from self._leave_daily_lingta_green_bottle(ctx, stop_event)
            _wait_scene_match = yield from context.wait_scene([196, 194, 193, 69, 34, 20], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        text = context.ocr_text(frame)
        if scene_id is None and self._daily_lingta_text_is_main(text):
            scene_id = 194
        if scene_id == 196:
            result_status = yield from self._finish_daily_lingta_result(ctx, stop_event)
            if result_status == "done":
                self._record_daily_lingta_done(payload, message="灵塔扫荡结果显示剩余次数为 0")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_lingta_to_world(ctx, stop_event),
                    label="日常_灵塔",
                    repeat_risk="重复扫荡",
                )
                return "success"
            scene_id = 194
        if scene_id == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
            scene_id = 194
        if scene_id == 194:
            text = context.ocr_text(update=True)
            if self._daily_lingta_remaining_zero(text):
                self._record_daily_lingta_done(payload, message="混沌灵塔剩余次数为 0")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_lingta_to_world(ctx, stop_event),
                    label="日常_灵塔",
                    repeat_risk="重复扫荡",
                )
                return "success"
            yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
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
                label="日常_灵塔",
            )

        daily_status = yield from context.open_daily_entry(
            label="日常_灵塔",
            title_pattern=r"挑战或扫荡混沌灵塔|混沌灵塔|灵塔",
            max_scrolls=self._payload_int(payload, "max_scrolls", "lingta_max_scrolls", default=10),
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lingta",
                task_type="daily_lingta",
                label="日常_灵塔",
                entry_label="混沌灵塔",
            )
            return "skipped"
        if daily_status == "done":
            self._record_daily_lingta_done(payload, message="日常列表显示已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_lingta_to_world(ctx, stop_event),
                label="日常_灵塔",
                repeat_risk="重复扫荡",
            )
            return "success"
        view = yield from context.wait_scene([193, 194], label="日常_灵塔：等待区域入口 #193 或混沌灵塔 #194")
        if getattr(view, "id", view) == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
        yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_lingta_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from context.open_daily_entry(
            label="日常_灵塔",
            title_pattern=r"挑战或扫荡混沌灵塔|混沌灵塔|灵塔",
            max_scrolls=self._payload_int(payload, "max_scrolls", "lingta_max_scrolls", default=10),
        )
        if status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lingta",
                task_type="daily_lingta",
                label="日常_灵塔",
                entry_label="混沌灵塔",
            )
            return "skipped"
        if status != "open":
            return status
        scene_id, _score = yield from self._wait_expected_scene(
            ctx,
            stop_event,
            [193, 194],
            timeout=24.0,
            label="日常_灵塔：等待区域入口 #193 或混沌灵塔 #194",
        )
        if scene_id == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
        return "open"

    def _open_daily_lingta_main_from_entry(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(3):
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([194, 193], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 194:
                return "success"
            if self._daily_jianling_text_is_main(text):
                with self._lock:
                    self._log_locked("warning", "日常_灵塔：#193「进入」实际进入淬剑试炼，先返回后停止，等待修正灵塔入口标注")
                yield from self._return_daily_jianling_to_world(ctx, stop_event)
                raise RuntimeError("日常_灵塔：#193「进入」实际进入淬剑试炼，不是混沌灵塔；需要修正灵塔入口动作标注")
            if scene_id == 193:
                with self._lock:
                    self._set_status_locked("running", "日常_灵塔：点击区域入口「进入」", phase="daily_lingta_enter_area", current_scene=193)
                    self._log_locked("action", "日常_灵塔：点击 #193「进入」")
                yield from context.wait_click(193, "进入")
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([194], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_text = text or last_text
            if scene_id == 194:
                with self._lock:
                    self._status.update({"current_scene": 194, "updated_at": time.time()})
                    self._log_locked("success", f"日常_灵塔：等待混沌灵塔 #194：已到达 #194 {score:.0f}%")
                return "success"
            if self._daily_jianling_text_is_main(text):
                with self._lock:
                    self._log_locked("warning", "日常_灵塔：#193「进入」等待阶段落到淬剑试炼，先返回后停止，等待修正灵塔入口标注")
                yield from self._return_daily_jianling_to_world(ctx, stop_event)
                raise RuntimeError("日常_灵塔：#193「进入」实际进入淬剑试炼，不是混沌灵塔；需要修正灵塔入口动作标注")
            if time.monotonic() - start >= 18.0:
                raise RuntimeError(f"日常_灵塔：等待混沌灵塔 #194 超时，OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵塔：等待混沌灵塔 #194，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_lingta_wait_main",
                    current_scene=scene_id,
                )
        return "success"

    def _run_daily_lingta_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：点击扫荡", phase="daily_lingta_sweep", current_scene=194)
            self._log_locked("action", "日常_灵塔：点击 #194「扫荡」")
        yield from context.wait_click(194, "扫荡")
        # #195 is consumed by Layer 0 as the declared result of「扫荡」.
        yield from self._confirm_daily_lingta_sweep(ctx, stop_event)
        result_status = yield from self._finish_daily_lingta_result(ctx, stop_event)
        self._record_daily_lingta_done(payload, message="混沌灵塔扫荡完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_lingta_to_world(ctx, stop_event),
            label="日常_灵塔",
            repeat_risk="重复扫荡",
        )

    def _confirm_daily_lingta_sweep(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：等待 Layer 0 完成扫荡确认", phase="daily_lingta_confirm_sweep", current_scene=None)
            self._log_locked("detail", "日常_灵塔：#195 扫荡确认由 Layer 0 守护处理")
        yield from context.wait_scene([196], label="日常_灵塔：等待扫荡结果 #196")

    def _finish_daily_lingta_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(8):
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([194, 196], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if self._daily_lingta_remaining_zero(text):
                return "done"
            if scene_id == 194 or ("混沌灵塔" in text and "剩余次数" in text):
                return "success"
            if scene_id == 196 or "点击" in text or "扫荡奖励" in text:
                with self._lock:
                    self._set_status_locked("running", f"日常_灵塔：关闭扫荡结果 {index + 1}", phase="daily_lingta_continue_result", current_scene=196)
                    self._log_locked("action", "日常_灵塔：点击 #196「点击继续」")
                yield from context.wait_click(196, "点击继续")
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_灵塔：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_灵塔：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_lingta_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([194, 69, 20, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id is None and self._daily_lingta_text_is_main(text):
            scene_id = 194
        if scene_id == 194:
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：退出混沌灵塔", phase="daily_lingta_exit_main", current_scene=194)
                self._log_locked("action", "日常_灵塔：点击 #194「返回」")
            yield from context.wait_click(194, "返回")
            scene_id, _score = yield from self._wait_expected_scene(
                ctx,
                stop_event,
                [69, 20, 34],
                timeout=18.0,
                label="日常_灵塔：等待日常 #69、绿瓶 #20 或世界 #34",
            )
        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：从日常列表返回世界", phase="daily_lingta_return_daily", current_scene=69)
                self._log_locked("action", "日常_灵塔：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            scene_id, _score = yield from self._wait_expected_scene(
                ctx,
                stop_event,
                [20, 34],
                timeout=18.0,
                label="日常_灵塔：等待绿瓶 #20 或世界 #34",
            )
        if scene_id == 20:
            yield from self._leave_daily_lingta_green_bottle(ctx, stop_event)
        yield from self._ensure_outer_world(ctx, stop_event, label="日常_灵塔")
        return "success"
