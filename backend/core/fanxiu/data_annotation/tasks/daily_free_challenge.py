"""妖王来袭、妖族袭城共用的免费剿灭流程。

玩法入口声明任务身份与日常列表规则，共用进入、剩余次数确认、免费剿灭、
有界重入和离场。购买弹窗策略仍由 task_type 区分，不自动购买额外次数。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.prog import BehaviorTreeStatus

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from ..ocr_values import FULLWIDTH_DIGIT_TRANSLATION, parse_ocr_values


class DailyFreeChallengeTaskMixin:
    def _execute_daily_yaowang_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return (yield from self._execute_daily_free_challenge_task(
            ctx,
            stop_event,
            payload,
            task_id="legacy-daily-yaowang",
            task_type="daily_yaowang",
            task_label="日常_妖王来袭",
            title_pattern=r"妖王\s*来袭|妖王",
        ))

    def _execute_daily_yaozu_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return (yield from self._execute_daily_free_challenge_task(
            ctx,
            stop_event,
            payload,
            task_id="legacy-daily-yaozu",
            task_type="daily_yaozu",
            task_label="日常_妖族袭城",
            title_pattern=r"妖族\s*袭城|妖族",
        ))

    def _execute_daily_free_challenge_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
        *,
        task_id: str,
        task_type: str,
        task_label: str,
        title_pattern: str,
        exclude_pattern: str | None = None,
    ) -> str:
        payload = {"max_scrolls": 30, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError(f"缺少{task_label}资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([223, 188, 189, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 223 or self._daily_dungeon_text_is_entry(text) or self._daily_dungeon_text_is_completed(text):
            yield from self._return_daily_dungeon_to_world(ctx, stop_event, payload, task_label=task_label)
            _wait_scene_match = yield from context.wait_scene([188, 189, 69, 34], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
        if self._daily_free_challenge_text_is_purchase_modal(text):
            return (yield from self._finish_daily_free_challenge_purchase_modal(
                ctx,
                stop_event,
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
            ))
        if (
            self._daily_free_challenge_text_is_selection(text)
            or self._daily_free_challenge_text_is_detail(text)
            or "妖兽波数" in _sanitize_ocr_text(text)
            or ("副本" in text and "用时" in text)
        ):
            return (yield from self._run_daily_free_challenge_with_reentry(
                ctx, stop_event, payload,
                task_id=task_id, task_type=task_type, task_label=task_label,
                title_pattern=title_pattern, exclude_pattern=exclude_pattern,
            ))
        if scene_id in {188, 189}:
            return (yield from self._run_daily_free_challenge_with_reentry(
                ctx, stop_event, payload,
                task_id=task_id, task_type=task_type, task_label=task_label,
                title_pattern=title_pattern, exclude_pattern=exclude_pattern,
            ))
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([188, 189, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
            if scene_id not in {69, 188, 189}:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=task_label,
                )
        if scene_id in {188, 189}:
            return (yield from self._run_daily_free_challenge_with_reentry(
                ctx, stop_event, payload,
                task_id=task_id, task_type=task_type, task_label=task_label,
                title_pattern=title_pattern, exclude_pattern=exclude_pattern,
            ))

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            title_pattern=title_pattern,
            exclude_pattern=exclude_pattern,
            progress_can_mark_done=False,
        )
        if daily_status == "done":
            raise RuntimeError(f"{task_label}：日常列表完成态不能作为成功依据，必须进入详情确认剩余奖励次数")
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id=task_id,
                task_type=task_type,
                label=task_label,
            )
            return "skipped"

        return (yield from self._run_daily_free_challenge_with_reentry(
            ctx, stop_event, payload,
            task_id=task_id, task_type=task_type, task_label=task_label,
            title_pattern=title_pattern, exclude_pattern=exclude_pattern,
        ))


    def _run_daily_free_challenge_with_reentry(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        task_label: str,
        title_pattern: str,
        exclude_pattern: str | None = None,
    ) -> str:
        """统一一轮剿灭后的重入上限与重新定位，重入仍从当前事实开始。"""
        result = yield from self._run_daily_free_challenge_from_scene(
            ctx,
            stop_event,
            payload,
            task_id=task_id,
            task_type=task_type,
            task_label=task_label,
        )
        if result == "reenter":
            attempt = int(payload.get("_free_challenge_attempt") or 0)
            if attempt >= int(payload.get("max_free_challenges") or 5):
                raise RuntimeError(f"{task_label}：免费剿灭重入次数超过上限，停止")
            payload["_free_challenge_attempt"] = attempt + 1
            return (yield from self._execute_daily_free_challenge_task(
                ctx,
                stop_event,
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
                title_pattern=title_pattern,
                exclude_pattern=exclude_pattern,
            ))
        return result

    def _daily_free_challenge_remaining_zero(self, text: str) -> bool:
        return self._daily_free_challenge_remaining_count(text) == 0

    def _daily_free_challenge_remaining_count(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized).replace("O", "0").replace("o", "0")
        match = re.search(r"剩余奖励次数[:：]?(.*)", normalized, re.IGNORECASE)
        if not match:
            return None
        tail = match.group(1)
        fraction = parse_ocr_values(tail, expected_count=2, allow_extra_numbers=True)
        if fraction is not None:
            return fraction[0]
        single = parse_ocr_values(tail, expected_count=1)
        return single[0] if single is not None else None

    def _daily_free_challenge_text_is_selection(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "剿灭" in normalized and "剩余奖励次数" not in normalized and ("妖王来袭" in normalized or "妖族袭城" in normalized)

    def _daily_free_challenge_text_is_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "剩余奖励次数" in normalized and "前往剿灭" in normalized

    def _daily_free_challenge_text_is_purchase_modal(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "购买并使用" in compact and ("价格" in compact or "拥有" in compact or "限购次数" in compact)

    def _finish_daily_free_challenge_purchase_modal(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        task_label: str,
    ):
        if task_type != "daily_yaozu":
            raise RuntimeError(f"{task_label}：出现「购买并使用」弹窗，默认不购买次数或道具，已停止等待人工关闭")
        self._record_daily_free_challenge_done(
            payload,
            task_id=task_id,
            task_type=task_type,
            task_label=task_label,
            message="出现「购买并使用」弹窗，判定免费妖族次数已耗尽，未购买",
        )
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_free_challenge_to_world(ctx, stop_event, task_label=task_label),
            label=task_label,
            action="关闭购买弹窗并回世界",
            repeat_risk="重复剿灭",
        )
        return "success"

    def _ocr_line_center_matching(self, lines: list[dict[str, Any]], *patterns: str) -> tuple[float, float, str] | None:
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if not any(re.search(pattern, text) for pattern in patterns):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            return x + w / 2, y + h / 2, text
        return None

    def _record_daily_free_challenge_done(
        self,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        task_label: str,
        message: str,
    ) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
        )
        self._log("success", f"{task_label}：{message}，下次 {next_time}")
        return next_time

    def _return_daily_free_challenge_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        task_label: str,
    ):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image71 = images.get(71)
        image69 = images.get(69)
        image183 = images.get(183)
        image187 = images.get(187)
        image188 = images.get(188)
        if not isinstance(image69, dict):
            raise RuntimeError(f"{task_label}：缺少 #69「日常」标注，无法收尾回世界")
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        context.clear_frame()
        for _index in range(4):
            _wait_scene_match = yield from context.wait_scene([34, 69, 188, 187, 183], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id in {34, 69, 188, 187, 183}:
                break
            if not (self._daily_free_challenge_text_is_selection(text) or self._daily_free_challenge_text_is_detail(text)):
                break
            back_image = image71 if isinstance(image71, dict) else image188
            back_title = "#71" if back_image is image71 else "#188"
            if not isinstance(back_image, dict):
                raise RuntimeError(f"{task_label}：免费剿灭页缺少通用「返回」标注，无法收尾回世界")
            back_shape = self._find_shape(back_image, "返回")
            if back_shape is None:
                raise RuntimeError(f"{task_label}：免费剿灭页缺少 {back_title}「返回」标注，无法收尾回世界")
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从免费剿灭页返回", phase="daily_free_challenge_return_ocr_page", current_scene=scene_id)
                self._log_locked("action", f"{task_label}：点击 {back_title}「返回」")
            context.click_shape_center(back_image, "返回")
            yield from context.wait_action_settle(2.0)
            context.clear_frame()
        _wait_scene_match = yield from context.wait_scene([34, 188, 187, 183, 69], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 34:
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        if scene_id == 188:
            if not isinstance(image188, dict):
                raise RuntimeError(f"{task_label}：缺少 #188「返回」标注，无法收尾回世界")
            back_shape = self._find_shape(image188, "返回")
            if back_shape is None:
                raise RuntimeError(f"{task_label}：缺少 #188「返回」标注，无法收尾回世界")
            frame = context.cur_frame(update=True)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从挑战页返回", phase="daily_free_challenge_return_main", current_scene=188)
                self._log_locked("action", f"{task_label}：点击 #188「返回」")
            yield from context.wait_click(188, "返回")
            scene_id, _score = yield from self._wait_expected_scene(
                ctx,
                stop_event,
                [69, 34, 187, 183],
                timeout=18.0,
                label=f"{task_label}：等待返回日常或世界",
            )
        if scene_id == 187 and isinstance(image187, dict):
            blank_shape = self._find_shape(image187, "空白")
            if blank_shape is not None:
                frame = context.cur_frame(update=True)
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭中间对话", phase="daily_free_challenge_close_dialogue", current_scene=187)
                    self._log_locked("action", f"{task_label}：点击 #187「空白」")
                yield from context.wait_click(187, "空白")
                scene_id, _score = yield from self._wait_expected_scene(
                    ctx,
                    stop_event,
                    [69, 34, 183],
                    timeout=18.0,
                    label=f"{task_label}：等待返回日常或世界",
                )
        if scene_id == 183 and isinstance(image183, dict):
            back_shape = self._find_shape(image183, "返回")
            if back_shape is not None:
                frame = context.cur_frame(update=True)
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：返回世界", phase="daily_free_challenge_return_world_click", current_scene=183)
                    self._log_locked("action", f"{task_label}：点击 #183「返回」")
                yield from context.wait_click(183, "返回")
                scene_id, _score = yield from self._wait_expected_scene(
                    ctx,
                    stop_event,
                    [69, 34],
                    timeout=18.0,
                    label=f"{task_label}：等待返回日常或世界",
                )
        if scene_id == 69:
            exit_shape = self._find_shape(image69, "退出")
            if exit_shape is None:
                raise RuntimeError(f"{task_label}：缺少 #69「退出」标注，无法回世界")
            frame = context.cur_frame(update=True)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从日常列表返回世界", phase="daily_free_challenge_return_daily", current_scene=69)
                self._log_locked("action", f"{task_label}：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            yield from context.wait_action_settle(2.0)
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                yield from context.wait_action_settle(2.0)
            yield from context.wait_scene([34], wait=18.0, label=f"{task_label}：等待世界 #34")
        context.clear_frame()
        _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 34:
            raise RuntimeError(f"{task_label}：收尾回世界后仍识别为 #{scene_id or 'unknown'}")
        with self._lock:
            self._status.update({"current_scene": 34, "updated_at": time.time()})
        return "success"

    def _run_daily_free_challenge_from_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        task_label: str,
    ):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image188 = images.get(188)
        image189 = images.get(189)
        image227 = images.get(227)
        image69 = images.get(69)
        if not isinstance(image69, dict):
            raise RuntimeError(f"{task_label}：缺少 #69「日常」标注，无法按 OCR 点击妖王/妖族页")
        max_runs = int(payload.get("max_free_challenges") or 5)
        run_count = 0
        scene_id: int | None
        score: float
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([188, 189, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            lines = context.ocr_fragments(frame)
            text = context.ocr_text(frame)
            if self._daily_free_challenge_text_is_purchase_modal(text):
                return (yield from self._finish_daily_free_challenge_purchase_modal(
                    ctx,
                    stop_event,
                    payload,
                    task_id=task_id,
                    task_type=task_type,
                    task_label=task_label,
                ))
            if self._daily_dungeon_text_is_result(text):
                if not isinstance(image227, dict):
                    raise RuntimeError(f"{task_label}：已进入奖励结果页，但缺少 #227「继续」标注，无法收口")
                continue_shape = self._find_shape(image227, "继续", "点击屏幕继续")
                if continue_shape is None:
                    raise RuntimeError(f"{task_label}：已进入奖励结果页，但缺少「点击屏幕继续」标注，无法收口")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭剿灭奖励页", phase="daily_free_challenge_close_reward", current_scene=scene_id)
                    self._log_locked("action", f"{task_label}：点击奖励页「点击屏幕继续」")
                context.click_shape_center(image227, str(continue_shape.get("title") or "继续"))
                yield from context.wait_action_settle(2.0)
                continue
            if self._daily_free_challenge_text_is_selection(text):
                match = self._ocr_line_center_matching(lines, r"推荐?剿灭|荐剿灭")
                if match is None:
                    raise RuntimeError(f"{task_label}：妖王/妖族选择页未找到「推荐剿灭」按钮，不能继续")
                x, y, matched_text = match
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：选择推荐剿灭目标",
                        phase="daily_free_challenge_select_recommended",
                        current_scene=scene_id,
                    )
                    self._log_locked("action", f"{task_label}：点击 OCR「{matched_text}」")
                context.click_frame_point(69, x, y)
                yield from context.wait_action_settle(2.0)
                continue
            if self._daily_free_challenge_text_is_detail(text):
                if self._daily_free_challenge_remaining_zero(text):
                    self._record_daily_free_challenge_done(
                        payload,
                        task_id=task_id,
                        task_type=task_type,
                        task_label=task_label,
                        message="详情页显示剩余奖励次数已为 0",
                    )
                    yield from self._safe_daily_done_cleanup(
                        lambda: self._return_daily_free_challenge_to_world(ctx, stop_event, task_label=task_label),
                        label=task_label,
                        repeat_risk="重复剿灭",
                    )
                    return "success"
                if run_count >= max_runs:
                    raise RuntimeError(f"{task_label}：剿灭次数超过上限 {max_runs}，停止以避免误点")
                match = self._ocr_line_center_matching(lines, r"前往剿灭")
                if match is None:
                    raise RuntimeError(f"{task_label}：详情页未找到「前往剿灭」按钮，不能继续")
                run_count += 1
                x, y, matched_text = match
                remaining = self._daily_free_challenge_remaining_count(text)
                with self._lock:
                    remaining_text = f"剩余 {remaining}" if remaining is not None else "剩余次数未读清"
                    self._set_status_locked(
                        "running",
                        f"{task_label}：执行免费剿灭 {run_count}/{max_runs}（{remaining_text}）",
                        phase="daily_free_challenge_exterminate",
                        current_scene=scene_id,
                    )
                    self._log_locked("action", f"{task_label}：点击 OCR「{matched_text}」")
                context.click_frame_point(69, x, y)
                yield from context.wait_action_settle(4.0)
                continue
            if "妖兽波数" in _sanitize_ocr_text(text) or ("副本" in text and "用时" in text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：等待自动剿灭完成",
                        phase="daily_free_challenge_wait_combat",
                        current_scene=scene_id,
                    )
                context.clear_frame()
                yield BehaviorTreeStatus.RUNNING
                continue
            if scene_id == 189 or "点击退出" in text:
                if not isinstance(image189, dict):
                    raise RuntimeError(f"{task_label}：缺少 #189「挑战结算」标注，无法关闭结算")
                exit_shape = self._find_shape(image189, "点击退出")
                if exit_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #189「点击退出」标注，无法关闭结算")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭剿灭结算", phase="daily_free_challenge_exit_result", current_scene=189)
                    self._log_locked("action", f"{task_label}：点击 #189「点击退出」")
                yield from context.wait_click(189, "点击退出")
                yield from context.wait_action_settle(2.0)
                continue
            if scene_id == 188:
                raise RuntimeError(f"{task_label}：检测到旧 #188 快速挑战页，但妖王/妖族只允许免费「前往剿灭」流程，已停止避免误点")
            if scene_id == 34 and run_count > 0:
                return "reenter"
            if scene_id == 69 and run_count > 0:
                return "reenter"
            if time.monotonic() - start >= float(payload.get("free_challenge_timeout") or 120.0):
                raise RuntimeError(f"{task_label}：等待免费剿灭流程超时，最后 #{scene_id or 'unknown'} {score:.0f}% OCR={text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待免费剿灭状态，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_free_challenge_wait",
                    current_scene=scene_id,
                )
            context.clear_frame()
            yield BehaviorTreeStatus.RUNNING
