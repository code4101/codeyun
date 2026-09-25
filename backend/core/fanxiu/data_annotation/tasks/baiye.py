"""拜谒：目标法则选择、领主定位、普通与绿瓶拜谒。

执行器提供通用场景操作；本模块拥有业务完成判据和后续调度。
"""
from __future__ import annotations
from backend.core.fanxiu.data_annotation.effective_time import job_now
import math
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import locate_text_box
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class BaiyeTaskMixin:
    def _baiye_payload_target(self, payload: dict[str, Any]) -> str:
        args = payload.get("args")
        if isinstance(args, list) and args:
            text = str(args[0] or "").strip()
            if text:
                return text
        return str(payload.get("target") or payload.get("law") or "魔道").strip() or "魔道"

    def _baiye_text_is_rule_map(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text)
        return bool(("拜谒排行" in compact and ("大道" in compact or "跨法则" in compact)) or "跨法则" in compact)

    def _baiye_text_is_lord_map(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text)
        return bool(
            "法则之主" in compact
            and any(marker in compact for marker in ("可旋转", "进行拜谒", "魔道", "洗灵", "仙弈", "幻虚", "魔道"))
        )

    def _baiye_text_is_completed(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "已拜谒" in compact:
            return True
        match = re.search(r"剩余次数[:：]?(.*)", compact)
        fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True) if match else None
        return bool(fraction is not None and fraction[0] == 0 and fraction[1] > 0)

    def _baiye_text_can_worship(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "拜谒" not in compact:
            return False
        match = re.search(r"剩余次数[:：]?(.*)", compact)
        fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True) if match else None
        return bool(fraction is not None and fraction[0] > 0 and fraction[1] > 0)

    def _baiye_text_is_target_worship_page(self, text: Any, target: str) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        target_text = _sanitize_ocr_text(target).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(target_text and target_text in compact and self._baiye_text_can_worship(compact))

    def _baiye_detail_state(
        self,
        context: Any,
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ):
        """Read #266 through its formal scene and local Shapes only."""

        if isinstance(frame_data_url, str) and frame_data_url and not update:
            scene_id, _score, frame = context.recognize_scene_in_frame(
                [266], frame_data_url=frame_data_url
            )
        else:
            _wait_scene_match = yield from context.wait_scene([266], label='日常_拜谒：识别法则详情', wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        if scene_id != 266:
            return "absent", "", frame
        text = context.ocr_text_in_shapes(
            266,
            ["法则之主名称", "法则详情", "拜谒"],
            padding=8,
            frame_data_url=frame,
            # 全帧 OCR 会把横幅与低对比度的法则名称合并，按业务区域独立识别。
            crop=True,
        )
        if self._baiye_text_is_completed(text):
            return "completed", text, frame
        if self._baiye_text_can_worship(text):
            return "actionable", text, frame
        return "unknown", text, frame

    def _schedule_baiye_retry(self, payload: Mapping[str, Any], *, reason: str) -> str:
        retry_seconds = max(60, int(payload.get("baiye_lord_retry_seconds") or 300))
        next_time = (
            job_now() + timedelta(seconds=retry_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-baiye"),
            next_time,
        )
        self._log("warning", f"日常_拜谒：{reason}，于 {next_time} 重试")
        return next_time

    def _click_baiye_worship_button(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Iterator[Any]:
        self._log("action", f"日常_拜谒：{reason}，点击 #266「拜谒」")
        context.click_shape_center(266, "拜谒")
        timeout = max(2.0, float(payload.get("baiye_worship_confirm_timeout") or 20.0))
        poll_seconds = max(0.2, float(payload.get("baiye_worship_poll_seconds") or 1.0))
        state = "absent"
        worship_text = ""
        max_attempts = max(1, int(math.ceil(timeout / poll_seconds)))
        for attempt in range(1, max_attempts + 1):
            yield from context.wait_action_settle(poll_seconds)
            state, worship_text, _frame = yield from self._baiye_detail_state(
                context, update=True
            )
            if state == "completed":
                self._log("success", f"日常_拜谒：已完成拜谒，OCR={worship_text[:120]}")
                yield from self._finish_baiye_completed(
                    context,
                    payload,
                    reason="拜谒完成后收尾",
                )
                return "success"
            if attempt >= max_attempts:
                break
            self._log(
                "wait",
                f"日常_拜谒：等待拜谒完成态，state={state} OCR={worship_text[:80]}",
            )
        if state == "actionable":
            raise RuntimeError(
                f"日常_拜谒：点击 #266「拜谒」后 {timeout:.0f}s 仍未完成，OCR={worship_text[:120]}"
            )
        raise RuntimeError(
            f"日常_拜谒：点击 #266「拜谒」后 {timeout:.0f}s 未能确认完成；"
            f"禁止按未知状态返回 success，state={state} OCR={worship_text[:120]}"
        )

    def _finish_baiye_completed(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Iterator[Any]:
        """Persist proven completion before best-effort page cleanup."""

        if payload.get("__scheduler_task_id") and not payload.get("__baiye_completion_persisted"):
            self._record_daily_entry_done(
                payload,
                task_id="legacy-daily-baiye",
                task_type="daily_baiye",
                label="日常_拜谒",
                message="今日拜谒已确认完成",
            )
            payload["__baiye_completion_persisted"] = True
        try:
            yield from self._return_baiye_to_world(context, payload, reason=reason)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            self._log(
                "warning",
                f"日常_拜谒：今日拜谒已确认完成，收尾返回 #34 失败，"
                f"保留完成态并交由后续作业归一：{exc}",
            )
        return "success"

    def _return_baiye_to_world(
        self,
        context: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        reason: str,
    ) -> Iterator[Any]:
        settle_seconds = float((payload or {}).get("baiye_return_settle_seconds") or 1.0)
        self._log("action", f"日常_拜谒：{reason}，按拜谒页面栈返回 #34")
        for _attempt in range(8):
            _wait_scene_match = yield from context.wait_scene([266, 265, 264, 34], label='日常_拜谒：识别返回页面栈', wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 34:
                self._log("success", "日常_拜谒：已返回 #34 世界，闭环完成")
                return "success"
            if scene_id == 266:
                self._log("action", f"日常_拜谒：当前 #266 {score:.0f}%，点击「返回」回法则之主选择页")
                context.click_shape_center(266, "返回")
                yield from context.wait_action_settle(settle_seconds)
                continue
            if scene_id == 265:
                self._log("action", f"日常_拜谒：当前 #265 {score:.0f}%，点击「返回」回三千大道")
                context.click_shape_center(265, "返回")
                yield from context.wait_action_settle(settle_seconds)
                continue
            if scene_id == 264:
                self._log("action", f"日常_拜谒：当前 #264 {score:.0f}%，点击「返回」回世界")
                context.click_shape_center(264, "返回")
                yield from context.wait_action_settle(settle_seconds)
                continue
            self._log("warning", f"日常_拜谒：页面栈返回未识别到 #264/#265/#266/#34，当前 scene={scene_id}，回退通用 goto #34")
            yield from context.go_scene(34)
            yield from context.wait_action_settle(settle_seconds)
            _wait_scene_match = yield from context.wait_scene([34, 266, 265, 264], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 34:
                self._log("success", "日常_拜谒：已通过通用 goto 返回 #34 世界，闭环完成")
                return "success"
            break
        raise RuntimeError(f"日常_拜谒：通用 goto 返回后未能确认 #34，当前 scene={scene_id}")

    def _execute_daily_baiye_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_拜谒资产树路径，无法执行作业")
        target = self._baiye_payload_target(payload)
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([266, 265, 264, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 266:
            result = yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target)
            if result == "success" and not payload.get("__baiye_completion_persisted"):
                self._record_daily_entry_done(
                    payload,
                    task_id="legacy-daily-baiye",
                    task_type="daily_baiye",
                    label="日常_拜谒",
                    message="今日拜谒已确认完成",
                )
            return result
        if scene_id != 265:
            if scene_id != 264:
                if scene_id != 69:
                    text = context.ocr_text(frame)
                    scene_id = yield from self._enter_daily_from_world_like(
                        ctx,
                        context,
                        stop_event,
                        frame,
                        scene_id,
                        text,
                        label="日常_拜谒",
                    )
                status = yield from self._open_daily_entry_from_daily(
                    ctx,
                    stop_event,
                    payload,
                    task_label="日常_拜谒",
                    title_pattern=r"拜\s*谒",
                    progress_can_mark_done=False,
                )
                if status == "done":
                    raise RuntimeError("日常_拜谒：日常列表进度不能作为拜谒完成证据")
                if status == "not_found":
                    self._record_daily_entry_not_found_retry(
                        payload,
                        task_id="legacy-daily-baiye",
                        task_type="daily_baiye",
                        label="日常_拜谒",
                        entry_label="拜谒",
                    )
                    return "skipped"
                yield from context.wait_any(
                    {"scene": context.scene_visible(264)},
                    timeout=20.0,
                    label="日常_拜谒：等待三千大道 #264",
                )
            yield from self._open_baiye_cross_rule(ctx, stop_event, payload, keyword=str(payload.get("cross_keyword") or "16"))
        result = yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target)
        if result == "success" and not payload.get("__baiye_completion_persisted"):
            self._record_daily_entry_done(
                payload,
                task_id="legacy-daily-baiye",
                task_type="daily_baiye",
                label="日常_拜谒",
                message="今日拜谒已确认完成",
            )
        return result

    def _execute_daily_green_bottle_baiye_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_绿瓶拜谒资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(20), dict):
            raise RuntimeError("缺少 #20「绿瓶」标注，无法进入绿瓶拜谒")
        rank_scene_id = 282
        if not isinstance(images.get(rank_scene_id), dict):
            raise RuntimeError("缺少 #282「掌天瓶」标注，无法点击境界排行")
        baiye_scene_id = 283
        if not isinstance(images.get(baiye_scene_id), dict):
            raise RuntimeError("缺少 #283「拜谒」标注，无法完成绿瓶拜谒")

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：前往绿瓶 #20", phase="daily_green_bottle_baiye_goto_20")
            self._log_locked("action", "日常_绿瓶拜谒：调用通用场景移动前往 #20")
        yield from context.go_scene(20)
        _wait_scene_match = yield from context.wait_scene([20], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 20:
            raise RuntimeError(f"日常_绿瓶拜谒：未能确认到达 #20，当前 scene={scene_id} score={score:.0f}%")
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：点击 #20「绿瓶」", phase="daily_green_bottle_baiye_click_bottle", current_scene=20)
            self._log_locked("success", f"日常_绿瓶拜谒：已到达 #20 {score:.0f}%")
            self._log_locked("action", "日常_绿瓶拜谒：点击 #20「绿瓶」")
        yield from context.wait_click(20, "绿瓶")
        yield from context.wait_action_settle(float(payload.get("green_bottle_settle_seconds") or 2.0))
        _wait_scene_match = yield from context.wait_scene([282, 301, 20], wait=5.0, required=False)
        (entry_scene_id, _entry_score, entry_frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if entry_scene_id == 301:
            entry_text = context.ocr_text(entry_frame)
            compact_entry_text = re.sub(r"\s+", "", _sanitize_ocr_text(entry_text))
            if "已达巅峰" in compact_entry_text:
                with self._lock:
                    self._log_locked("success", "日常_绿瓶拜谒：掌天瓶已达巅峰，今日绿瓶状态已确认")
                    self._log_locked("action", "日常_绿瓶拜谒：点击 #301「返回」退出掌天瓶详情")
                yield from context.wait_click(301, "返回")
                yield from context.wait_action_settle(float(payload.get("green_bottle_rank_back_settle_seconds") or 2.0))
                self._record_daily_entry_done(
                    payload,
                    task_id="legacy-daily-green-bottle-baiye",
                    task_type="daily_green_bottle_baiye",
                    label="日常_绿瓶拜谒",
                    message="掌天瓶已达巅峰，今日绿瓶状态已确认",
                )
                with self._lock:
                    self._set_status_locked("running", "日常_绿瓶拜谒：收尾回到世界 #34", phase="daily_green_bottle_baiye_return_world")
                    self._log_locked("action", "日常_绿瓶拜谒：调用通用场景移动回到 #34")
                final_scene_id, final_score = None, 0.0
                try:
                    yield from context.go_scene(34)
                    _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
                    (final_scene_id, final_score, _final_frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                except Exception as exc:
                    with self._lock:
                        self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，收尾返回 #34 失败：{exc}")
                if final_scene_id != 34:
                    with self._lock:
                        self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，但收尾未确认 #34，当前 scene={final_scene_id} score={final_score:.0f}%")
                with self._lock:
                    message = "日常_绿瓶拜谒完成，已回到 #34" if final_scene_id == 34 else "日常_绿瓶拜谒今日已完成，收尾场景待后续任务重新归一"
                    self._set_status_locked("success", message, phase="daily_green_bottle_baiye_done", current_scene=final_scene_id)
                    self._log_locked("success", "日常_绿瓶拜谒完成")
                return "success"
            raise RuntimeError(f"日常_绿瓶拜谒：进入 #301 但未识别为已达巅峰，OCR={entry_text[:120]}")
        with self._lock:
            self._set_status_locked("running", f"日常_绿瓶拜谒：点击 #{rank_scene_id}「境界排行」", phase="daily_green_bottle_baiye_click_rank", current_scene=rank_scene_id)
            self._log_locked("success", "日常_绿瓶拜谒：已点击 #20「绿瓶」")
            self._log_locked("action", f"日常_绿瓶拜谒：点击 #{rank_scene_id}「境界排行」")
        yield from context.wait_click(rank_scene_id, "境界排行")
        yield from context.wait_action_settle(float(payload.get("green_bottle_rank_settle_seconds") or 2.0))
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：点击 #283「拜谒」", phase="daily_green_bottle_baiye_click_baiye", current_scene=baiye_scene_id)
            self._log_locked("success", f"日常_绿瓶拜谒：已点击 #{rank_scene_id}「境界排行」")
            self._log_locked("action", "日常_绿瓶拜谒：确认天道魁首拜谒状态")
        _wait_scene_match = yield from context.wait_scene([baiye_scene_id], wait=5.0, required=False)
        (worship_scene_id, _worship_score, _worship_frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        remaining_numbers, remaining_text = yield from self._read_green_bottle_baiye_remaining(
            context,
            payload,
            scene_id=baiye_scene_id,
        )
        remaining = remaining_numbers[0]
        if remaining == 0:
            with self._lock:
                self._log_locked("success", f"日常_绿瓶拜谒：#283[剩余次数] 首个数值为 0，今日拜谒已完成，scene={worship_scene_id}")
        else:
            with self._lock:
                self._log_locked("action", f"日常_绿瓶拜谒：#283[剩余次数]={remaining}，点击 #283「拜谒」")
            context.click_shape_center(baiye_scene_id, "拜谒")
            yield from context.wait_action_settle(float(payload.get("green_bottle_baiye_settle_seconds") or 2.0))
            # A click is not completion: observe the receipt without clicking again.
            remaining_numbers, remaining_text = yield from self._read_green_bottle_baiye_remaining(
                context, payload, scene_id=baiye_scene_id, require_exhausted=True,
            )
        with self._lock:
            self._log_locked("success", "日常_绿瓶拜谒：今日拜谒已确认完成")
            self._set_status_locked("running", "日常_绿瓶拜谒：收尾回到世界 #34", phase="daily_green_bottle_baiye_return_world")
            self._log_locked("action", "日常_绿瓶拜谒：从当前场景调用通用场景移动回到 #34")
        self._record_daily_entry_done(
            payload,
            task_id="legacy-daily-green-bottle-baiye",
            task_type="daily_green_bottle_baiye",
            label="日常_绿瓶拜谒",
            message="今日拜谒已确认完成",
        )
        # 不在业务作业里手写 #283 -> #282 -> #20 -> #34。这里必须交给
        # 通用 goto；sceneJumpTarget 是可增量学习的历史落点频次，不是硬规则。
        final_scene_id, final_score = None, 0.0
        try:
            yield from context.go_scene(34)
            _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
            (final_scene_id, final_score, _final_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        except Exception as exc:
            with self._lock:
                self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，收尾返回 #34 失败：{exc}")
        if final_scene_id != 34:
            with self._lock:
                self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，但收尾未确认 #34，当前 scene={final_scene_id} score={final_score:.0f}%")
        with self._lock:
            message = "日常_绿瓶拜谒完成，已回到 #34" if final_scene_id == 34 else "日常_绿瓶拜谒今日已完成，收尾场景待后续任务重新归一"
            self._set_status_locked("success", message, phase="daily_green_bottle_baiye_done", current_scene=final_scene_id)
            self._log_locked("success", "日常_绿瓶拜谒完成")
        return "success"

    def _read_green_bottle_baiye_remaining(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        scene_id: int = 283,
        require_exhausted: bool = False,
    ):
        """只读新帧复核次数；点击后必须有界等待已耗尽，绝不重复拜谒。"""

        max_attempts = max(1, int(payload.get("green_bottle_remaining_read_attempts") or 3))
        settle_seconds = max(0.1, float(payload.get("green_bottle_remaining_read_settle_seconds") or 0.8))
        timeout = max(2.0, float(payload.get("green_bottle_baiye_confirm_timeout") or 20.0))
        deadline = time.monotonic() + timeout
        last_text = ""
        attempt = 0
        while True:
            if require_exhausted:
                if time.monotonic() >= deadline:
                    break
            elif attempt >= max_attempts:
                break
            self._raise_if_stopped(context.stop_event)
            attempt += 1
            frame = context.cur_frame(update=True)
            observed_scene, _score, _frame = context.recognize_scene_in_frame(
                [scene_id], frame_data_url=frame,
            )
            last_text = (
                context.ocr_text_in_shapes(
                    scene_id, ["剩余次数"], padding=8,
                    frame_data_url=frame, crop=True,
                ) if observed_scene == scene_id else ""
            )
            numbers = parse_ocr_values(
                _sanitize_ocr_text(last_text).translate(FULLWIDTH_DIGIT_TRANSLATION)
            )
            if numbers is not None and (not require_exhausted or numbers[0] == 0):
                return numbers, last_text
            if require_exhausted or attempt < max_attempts:
                with self._lock:
                    self._log_locked(
                        "warning",
                        f"日常_绿瓶拜谒：#283[剩余次数] 第 {attempt} 帧未确认"
                        f"{'耗尽' if require_exhausted else '数值'}，OCR={last_text[:80]}，等待新帧复核",
                    )
                yield from context.wait_action_settle(settle_seconds)
        raise RuntimeError(
            f"日常_绿瓶拜谒：{attempt} 帧后未能从 #283[剩余次数] 确认"
            f"{'次数耗尽' if require_exhausted else '剩余次数'}，"
            f"最后 OCR={last_text[:80]}"
        )

    def _open_baiye_cross_rule(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        keyword: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        view264 = context.view(264)
        list_shape = context.shape(view264, "识别区")
        max_scrolls = self._payload_int(payload, "baiye_rule_max_scrolls", "max_scrolls", default=30)
        for direction, scroll_count in (("down", max_scrolls),):
            for scroll_index in range(max(0, int(scroll_count)) + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_拜谒：在 #264 查找包含 {keyword} 的法则 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_baiye_find_rule",
                        current_scene=264,
                    )
                frame = context.cur_frame(update=True)
                lines = context.ocr_fragments_in_shapes(264, ["识别区"], frame_data_url=frame) if hasattr(context, "ocr_fragments_in_shapes") else []
                tokens = context.ocr_tokens_in_shapes(264, ["识别区"], frame_data_url=frame)
                matches = [line for line in lines if keyword in _sanitize_ocr_text(line.get("text"))]
                if matches:
                    fragment = sorted(matches, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))[0]
                    parent_id = fragment.get("line_id")
                    line_tokens = [token for token in tokens if token.get("parent_line_id") == parent_id]
                    target_box = locate_text_box(line_tokens, keyword)
                    if target_box is None:
                        continue
                    x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
                    y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
                    text = _sanitize_ocr_text(fragment.get("text"))
                    self._log("action", f"日常_拜谒：点击 #264 OCR「{text}」")
                    context.click_frame_point(264, x, y)
                    yield from context.wait_any(
                        {"scene": context.scene_visible(265)},
                        timeout=20.0,
                        label="日常_拜谒：等待法则之主 #265",
                    )
                    return "open"
                if scroll_index >= int(scroll_count):
                    break
                self._log("action", f"日常_拜谒：#264 未找到 {keyword}，{direction} 滚动 {scroll_index + 1}")
                changed = yield from context.scroll_shape_content(view264, list_shape, direction=direction)
                if not changed:
                    break
        raise RuntimeError(f"日常_拜谒：#264 识别区未找到包含 {keyword} 的法则")

    def _baiye_target_box_from_tokens(
        self,
        tokens: list[dict[str, Any]],
        target: str,
        *,
        lines: list[dict[str, Any]] | None = None,
    ) -> dict[str, float] | None:
        if lines:
            for line in lines:
                text = _sanitize_ocr_text(line.get("text"))
                if "法则" in text or target not in text:
                    continue
                parent_id = line.get("line_id")
                line_tokens = [token for token in tokens if token.get("parent_line_id") == parent_id]
                target_box = locate_text_box(line_tokens, target)
                if target_box is not None:
                    return target_box
            return None
        # Observable legacy degradation: locate_text_box never crosses an
        # explicit parent_line_id, and unlinked tokens are accepted only for a
        # caller-provided local ROI.
        return locate_text_box(tokens, target)

    def _baiye_lord_click_point_from_box(
        self,
        box: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[float, float]:
        options = payload or {}
        x = float(box.get("x") or 0)
        y = float(box.get("y") or 0)
        w = max(1.0, float(box.get("w") or 0))
        h = max(1.0, float(box.get("h") or 0))
        x_ratio = float(options.get("baiye_lord_icon_x_ratio") or 0.5)
        y_offset_ratio = float(options.get("baiye_lord_icon_y_offset_ratio") or 1.35)
        click_x = x + w * max(0.0, min(1.0, x_ratio))
        click_y = y - h * max(0.0, y_offset_ratio)
        return click_x, click_y

    def _select_baiye_law_lord(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        target: str,
    ) -> str:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        timeout = float(payload.get("baiye_lord_timeout") or 120.0)
        scene_recovery_timeout = float(payload.get("baiye_scene_recovery_timeout") or 8.0)
        poll_seconds = float(payload.get("baiye_lord_poll_seconds") or 0.75)
        start = time.monotonic()
        last_text = ""
        unrecognized_since: float | None = None
        while True:
            self._raise_if_stopped(stop_event)
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                next_time = self._schedule_baiye_retry(
                    payload,
                    reason=f"{timeout:.0f}s 未找到「{target}」",
                )
                self._log(
                    "warning",
                    f"日常_拜谒：{timeout:.0f}s 未找到「{target}」，"
                    f"点击返回并于 {next_time} 重试",
                )
                context.click_shape_center(265, "返回")
                yield from context.wait_any(
                    {"scene": context.scene_visible(264)},
                    timeout=20.0,
                    label="日常_拜谒：等待返回 #264",
                )
                return "skipped"
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_拜谒：在 #265 查找「{target}」",
                    phase="daily_baiye_find_lord",
                    current_scene=265,
                )
            _wait_scene_match = yield from context.wait_scene([266, 265], label='日常_拜谒：识别法则之主列表或详情', wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            detail_state, detail_text, _detail_frame = yield from self._baiye_detail_state(
                context,
                frame_data_url=frame,
            )
            if detail_state == "completed":
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_拜谒：当前已在法则详情完成态",
                        phase="daily_baiye_detail_done",
                        current_scene=266,
                    )
                self._log("success", f"日常_拜谒：当前已是完成态，OCR={detail_text[:120]}")
                yield from self._finish_baiye_completed(
                    context,
                    payload,
                    reason="检测到已完成态后收尾",
                )
                return "success"
            if detail_state == "actionable":
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_拜谒：当前已在法则详情可拜谒态",
                        phase="daily_baiye_detail_worship",
                        current_scene=266,
                    )
                if not self._baiye_text_is_target_worship_page(detail_text, target):
                    self._schedule_baiye_retry(
                        payload,
                        reason=f"#266 可拜谒但未确认目标「{target}」，禁止点击",
                    )
                    return "skipped"
                reason = f"当前已在「{target}」详情页"
                return (yield from self._click_baiye_worship_button(context, payload, reason=reason))
            if detail_state == "unknown":
                self._schedule_baiye_retry(
                    payload,
                    reason=f"#266 状态不确定，禁止返回 success，OCR={detail_text[:80]}",
                )
                return "skipped"

            if scene_id != 265:
                if unrecognized_since is None:
                    unrecognized_since = elapsed
                missing_seconds = elapsed - unrecognized_since
                if missing_seconds >= scene_recovery_timeout:
                    self._schedule_baiye_retry(
                        payload,
                        reason=(
                            f"连续 {missing_seconds:.1f}s 未识别到 #265/#266，"
                            f"禁止返回 success，scene={scene_id}"
                        ),
                    )
                    return "skipped"
                self._log(
                    "wait",
                    f"日常_拜谒：进入 #265 后画面暂未稳定，继续观察，scene={scene_id}",
                )
                yield from context.wait_action_settle(poll_seconds)
                continue
            unrecognized_since = None
            ocr_options = {
                "text_det_thresh": float(payload.get("baiye_text_det_thresh") or 0.25),
                "text_det_box_thresh": float(payload.get("baiye_text_det_box_thresh") or 0.45),
                "text_det_unclip_ratio": float(payload.get("baiye_text_det_unclip_ratio") or 1.2),
            }
            lines = (
                context.ocr_fragments_in_shapes(
                    265,
                    ["识别区"],
                    frame_data_url=frame,
                    options=ocr_options,
                )
                if hasattr(context, "ocr_fragments_in_shapes")
                else []
            )
            tokens = context.ocr_tokens_in_shapes(
                265,
                ["识别区"],
                frame_data_url=frame,
                options=ocr_options,
            )
            target_box = self._baiye_target_box_from_tokens(tokens, target, lines=lines)
            source_text = "".join(_sanitize_ocr_text(token.get("text")) for token in tokens)
            last_text = source_text or last_text
            if target_box is not None:
                click_x, click_y = self._baiye_lord_click_point_from_box(target_box, payload)
                self._log(
                    "action",
                    f"日常_拜谒：OCR 命中「{target}」({source_text[:40]})，词框=({float(target_box.get('x') or 0):.1f},"
                    f"{float(target_box.get('y') or 0):.1f},{float(target_box.get('w') or 0):.1f},"
                    f"{float(target_box.get('h') or 0):.1f})，点击图标估算点 ({click_x:.1f},{click_y:.1f})",
                )
                if bool(payload.get("baiye_lord_probe_only") or payload.get("probe_only")):
                    self._log("success", f"日常_拜谒：probe 已在 #265 OCR 命中「{target}」，未点击选择目标")
                    if bool(payload.get("baiye_lord_probe_return", True)):
                        context.click_shape_center(265, "返回")
                        yield from context.wait_any(
                            {"scene": context.scene_visible(264)},
                            timeout=20.0,
                            label="日常_拜谒：probe 等待返回 #264",
                        )
                    return "skipped"
                context.click_frame_point(265, click_x, click_y)
                yield from context.wait_action_settle(1.0)
                try:
                    yield from context.wait_scene(
                        [266],
                        wait=float(payload.get("baiye_detail_wait_seconds") or 12.0),
                        label=f"日常_拜谒：等待「{target}」详情 #266",
                    )
                except TimeoutError:
                    self._schedule_baiye_retry(
                        payload,
                        reason=f"点击「{target}」后未在时限内识别到 #266",
                    )
                    return "skipped"
                after_state, after_text, _after_frame = yield from self._baiye_detail_state(
                    context, update=True
                )
                if after_state == "completed":
                    self._log("success", f"日常_拜谒：已点击「{target}」，完成态 OCR={after_text[:120]}")
                    yield from self._finish_baiye_completed(
                        context,
                        payload,
                        reason=f"已点击「{target}」进入完成态后收尾",
                    )
                    return "success"
                if after_state == "actionable":
                    if not self._baiye_text_is_target_worship_page(after_text, target):
                        self._schedule_baiye_retry(
                            payload,
                            reason=f"选择「{target}」后详情页目标不确定，禁止点击拜谒",
                        )
                        return "skipped"
                    return (yield from self._click_baiye_worship_button(context, payload, reason=f"已选中「{target}」且显示可拜谒"))
                self._schedule_baiye_retry(
                    payload,
                    reason=f"点击「{target}」后未确认 #266 完成/可拜谒态，state={after_state}",
                )
                return "skipped"
            self._log("detail", f"日常_拜谒：暂未命中「{target}」，OCR={last_text[:80]}")
            yield from context.wait_action_settle(poll_seconds)
