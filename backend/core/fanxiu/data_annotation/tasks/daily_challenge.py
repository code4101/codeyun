from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks.daily_assistant import DailyAssistantTaskMixin


import re
import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.prog import BehaviorTreeStatus

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


class DailyChallengeTaskMixin(DailyAssistantTaskMixin):

    def _execute_daily_dungeon_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_每日副本资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)
        image222 = images.get(222)
        image223 = images.get(223)
        image224 = images.get(224)
        image225 = images.get(225)
        image226 = images.get(226)
        image227 = images.get(227)

        task_label = "日常_每日副本"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([225, 224, 223, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if self._daily_dungeon_text_is_result(text):
            return (yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label))
        if self._daily_dungeon_text_is_completed(text):
            return (yield from self._finish_daily_dungeon_completed(ctx, stop_event, payload, text=text, task_label=task_label))
        if scene_id == 225 or self._daily_dungeon_text_is_purchase_unavailable(text):
            yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
            yield from context.wait_scene([223], wait=10.0, label="日常_每日副本：等待回到副本挑战 #223")
            return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
        if scene_id == 224 or self._daily_dungeon_text_is_purchase(text):
            yield from self._click_daily_dungeon_purchase_uses(ctx, stop_event, payload, image224, image225, task_label=task_label)
            return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
        if scene_id == 223:
            return (yield from self._click_daily_dungeon_buy(ctx, stop_event, payload, image223, image224, image225, task_label=task_label))
        if self._daily_dungeon_text_is_entry(text):
            return (yield from self._click_daily_dungeon_recommend_and_buy(ctx, stop_event, payload, image222, image223, image224, image225, task_label=task_label))
        if scene_id not in {34, 69}:
            start = time.monotonic()
            while time.monotonic() - start < float(payload.get("entry_ocr_retry_seconds") or 5.0):
                self._raise_if_stopped(stop_event)
                yield BehaviorTreeStatus.RUNNING
                _wait_scene_match = yield from context.wait_scene([225, 224, 223, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_id in {69, 34}:
                    break
                if scene_id == 225:
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from context.wait_scene([223], wait=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if scene_id == 224:
                    yield from self._click_daily_dungeon_purchase_uses(ctx, stop_event, payload, image224, image225, task_label=task_label)
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if scene_id == 223:
                    return (yield from self._click_daily_dungeon_buy(ctx, stop_event, payload, image223, image224, image225, task_label=task_label))
                text = context.ocr_text(frame)
                if self._daily_dungeon_text_is_result(text):
                    return (yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label))
                if self._daily_dungeon_text_is_completed(text):
                    return (yield from self._finish_daily_dungeon_completed(ctx, stop_event, payload, text=text, task_label=task_label))
                if self._daily_dungeon_text_is_purchase(text):
                    yield from self._click_daily_dungeon_purchase_uses(ctx, stop_event, payload, image224, image225, task_label=task_label)
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if self._daily_dungeon_text_is_purchase_unavailable(text):
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from context.wait_scene([223], wait=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if self._daily_dungeon_text_is_entry(text):
                    return (yield from self._click_daily_dungeon_recommend_and_buy(ctx, stop_event, payload, image222, image223, image224, image225, task_label=task_label))
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=task_label,
                )

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            title_pattern=r"通\s*关\s*每\s*日\s*副\s*本|每\s*日\s*副\s*本|副\s*本\s*探\s*险",
            exclude_pattern=r"悟\s*道|试\s*炼|周\s*本",
            progress_can_mark_done=False,
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-dungeon",
                task_type="daily_dungeon",
                label=task_label,
                entry_label="每日副本",
            )
            return "skipped"

        return (yield from self._click_daily_dungeon_recommend_and_buy(ctx, stop_event, payload, image222, image223, image224, image225, task_label=task_label))

    def _daily_dungeon_text_is_entry(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "副本探险" in normalized and ("今日挑战次数" in normalized or "推荐" in normalized)

    def _daily_dungeon_text_is_purchase(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "购买并使用" in normalized and ("破界符" in normalized or "剩余限购次数" in normalized)

    def _daily_dungeon_text_is_purchase_unavailable(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "破界符" in compact and ("持有数量" in compact or "每日限购" in compact or "增加购买次数" in compact)

    def _daily_dungeon_purchase_remaining_count(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("：", ":")
        match = re.search(r"剩余\s*限购\s*次数\s*[:：]?\s*([0-9Oo])", normalized)
        if not match:
            match = re.search(r"剩余.{0,4}限购.{0,4}次数\D{0,8}([0-9Oo])", normalized)
        if not match:
            return None
        raw = match.group(1).replace("O", "0").replace("o", "0")
        try:
            return max(0, int(raw))
        except ValueError:
            return None

    def _record_daily_dungeon_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-dungeon"),
            next_time,
        )
        self._log("success", f"日常_每日副本：{message}，下次 {next_time}")
        return next_time

    def _click_daily_dungeon_recommend(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image222: dict[str, Any],
        *,
        task_label: str,
    ):
        recommend_shape = self._find_shape(image222, "推荐副本")
        if recommend_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #222「推荐副本」标注，无法点击推荐副本")
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：等待 #222 推荐副本",
                phase="daily_dungeon_wait_recommend",
            )
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image222,
            recommend_shape,
            payload,
            label=f"{task_label}：点击推荐副本",
            timeout_key="recommend_timeout",
        )
        yield from context.wait_action_settle(float(payload.get("recommend_click_settle_seconds") or 2.0))
        _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：已点击推荐副本，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                phase="daily_dungeon_recommend_clicked",
                current_scene=scene_id,
            )
            self._log_locked("success", f"{task_label}：已点击推荐副本，OCR={text[:120]}")
        return "success"

    def _click_daily_dungeon_recommend_and_buy(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image222: dict[str, Any],
        image223: dict[str, Any],
        image224: dict[str, Any],
        image225: dict[str, Any],
        *,
        task_label: str,
    ):
        yield from self._click_daily_dungeon_recommend(ctx, stop_event, payload, image222, task_label=task_label)
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待副本挑战 #223")
        text = context.ocr_text(update=True)
        if self._daily_dungeon_text_is_completed(text):
            return (yield from self._finish_daily_dungeon_completed(ctx, stop_event, payload, text=text, task_label=task_label))
        return (yield from self._click_daily_dungeon_buy(ctx, stop_event, payload, image223, image224, image225, task_label=task_label))

    def _click_daily_dungeon_buy(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image223: dict[str, Any],
        image224: dict[str, Any],
        image225: dict[str, Any],
        *,
        task_label: str,
    ):
        buy_shape = self._find_shape(image223, "购买")
        if buy_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #223「购买」标注，无法打开购买")
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image223,
            buy_shape,
            payload,
            label=f"{task_label}：打开购买",
            timeout_key="buy_timeout",
        )
        yield from context.wait_action_settle(float(payload.get("buy_click_settle_seconds") or 1.5))
        self._log("success", f"{task_label}：已打开购买")
        result_scene_id = yield from self._wait_daily_dungeon_purchase_result(
            ctx,
            stop_event,
            image223,
            image224,
            image225,
            timeout=float(payload.get("purchase_timeout") or 10.0),
            label=f"{task_label}：等待购买结果",
        )
        if result_scene_id in {223, 225}:
            return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
        yield from self._click_daily_dungeon_purchase_uses(ctx, stop_event, payload, image224, image225, task_label=task_label)
        yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
        return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))

    def _click_daily_dungeon_sweep(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image223: dict[str, Any],
        *,
        task_label: str,
    ):
        sweep_shape = self._find_shape(image223, "扫荡")
        if sweep_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #223「扫荡」标注，无法扫荡")
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待副本挑战 #223")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image223,
            sweep_shape,
            payload,
            label=f"{task_label}：点击扫荡",
            timeout_key="sweep_timeout",
        )
        # Optional #226 is a declared popup landing of「扫荡」and is consumed
        # by wait_scene Layer 0 before the result scene is returned.
        prompt_result = "layer0"
        yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label)
        yield from context.wait_action_settle(float(payload.get("sweep_click_settle_seconds") or 2.0))
        text = context.ocr_text(update=True)
        self._log("success", f"{task_label}：已点击扫荡，提示处理={prompt_result}，OCR={text[:120]}")
        return "success"

    def _daily_dungeon_text_is_result(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        has_continue_hint = "点击屏幕继续" in compact or "点击继续" in compact
        has_reward_title = "恭喜获得" in compact or bool(re.search(r"[恭共]喜.{0,3}[获莎]?得", compact))
        return has_continue_hint and has_reward_title

    def _daily_dungeon_text_is_completed(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        challenge_count = self._daily_dungeon_challenge_count(compact)
        if challenge_count is not None:
            current, total = challenge_count
            return current >= 6 and total >= 6
        fraction = parse_ocr_values(compact, expected_count=2, allow_extra_numbers=True)
        return fraction == (6, 6) and "完成" in compact

    def _daily_dungeon_challenge_count(self, compact_text: str) -> tuple[int, int] | None:
        normalized = compact_text.translate(FULLWIDTH_DIGIT_TRANSLATION).replace("O", "0").replace("o", "0")
        match = re.search(r"(?:今日可挑战次数|可挑战次数|挑战次数)[:：]?(.*)", normalized)
        if not match:
            return None
        values = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True)
        return (values[0], values[1]) if values is not None else None

    def _finish_daily_dungeon_completed(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        text: str,
        task_label: str,
    ):
        self._log("success", f"{task_label}：#223 已显示完成态，OCR={text[:120]}")
        self._record_daily_dungeon_done(payload, message="副本挑战已完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_dungeon_to_world(ctx, stop_event, payload, task_label=task_label),
            label=task_label,
            repeat_risk="重复扫荡",
        )
        return "success"

    def _finish_daily_dungeon_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        image227 = (ctx.get("images") or {}).get(227)
        if not isinstance(image227, dict):
            raise RuntimeError("日常_每日副本：缺少 #227「副本扫荡结果」标注，无法收尾")
        continue_shape = self._find_shape(image227, "继续", "点击屏幕继续")
        if continue_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #227「继续」标注，无法收尾")
        timeout = float(payload.get("result_timeout") or 18.0)
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            text = context.ocr_text(update=True)
            last_text = text or last_text
            if self._daily_dungeon_text_is_result(text):
                yield from self._click_shape_respecting_conditions(
                    ctx,
                    stop_event,
                    image227,
                    continue_shape,
                    payload,
                    label=f"{task_label}：点击扫荡结果继续",
                    timeout_key="result_continue_timeout",
                )
                yield from context.wait_action_settle(float(payload.get("result_continue_settle_seconds") or 2.0))
                self._log("success", f"{task_label}：已点击扫荡结果继续")
                self._record_daily_dungeon_done(payload, message="扫荡奖励已领取")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_dungeon_to_world(ctx, stop_event, payload, task_label=task_label),
                    label=task_label,
                    repeat_risk="重复扫荡",
                )
                return "success"
            if self._daily_dungeon_text_is_completed(text):
                return (yield from self._finish_daily_dungeon_completed(ctx, stop_event, payload, text=text, task_label=task_label))
            _wait_scene_match = yield from context.wait_scene([227, 223, 34], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 34:
                raise RuntimeError(f"{task_label}：扫荡后直接回到世界，但未识别 #227 奖励结果或 #223 次数归零，禁止按完成处理")
            if time.monotonic() - start >= timeout:
                raise RuntimeError(f"{task_label}：等待 #227 扫荡结果超时，OCR={last_text[:120]}")
            with self._lock:
                self._status.update({
                    "phase": "daily_dungeon_wait_result",
                    "message": f"{task_label}：等待扫荡结果 #227",
                    "updated_at": time.time(),
                })

    def _return_daily_dungeon_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image223 = images.get(223)
        if not isinstance(image223, dict):
            raise RuntimeError("日常_每日副本：缺少 #223「副本挑战」标注，无法返回世界")
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        yield from context.wait_scene([223], wait=float(payload.get("return_challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
        back_shape = self._find_shape(image223, "返回")
        if back_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #223「返回」标注，无法返回世界")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image223,
            back_shape,
            payload,
            label=f"{task_label}：点击返回",
            timeout_key="return_timeout",
        )
        timeout = float(payload.get("return_world_timeout") or 18.0)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([223, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_text = text or last_text
            if scene_id == 34 and not self._daily_dungeon_text_is_entry(text):
                return
            if time.monotonic() - start >= timeout:
                raise RuntimeError(f"{task_label}：等待世界 #34 超时，最后 #{scene_id or 'unknown'} {score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._status.update({
                    "phase": "daily_dungeon_return_world_wait",
                    "current_scene": scene_id,
                    "message": f"{task_label}：等待真实世界 #34，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            yield BehaviorTreeStatus.RUNNING

    def _wait_daily_dungeon_purchase_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image223: dict[str, Any],
        image224: dict[str, Any],
        image225: dict[str, Any],
        *,
        timeout: float,
        label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([224, 225, 223], wait=5.0, required=False)
            (scene_id, score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_scene_id, last_score = scene_id, score
            if scene_id == 224:
                self._log("success", f"{label}：进入 #224 购买破界符")
                return 224
            if scene_id == 225:
                self._log("success", f"{label}：进入 #225 数量不足，关闭弹窗")
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from context.wait_scene([223], wait=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                return 225
            text = context.ocr_text(_frame)
            if self._daily_dungeon_text_is_purchase(text):
                self._log("success", f"{label}：OCR 确认 #224 购买破界符")
                return 224
            if self._daily_dungeon_text_is_purchase_unavailable(text):
                self._log("success", f"{label}：OCR 确认 #225 数量不足，关闭弹窗")
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from context.wait_scene([223], wait=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                return 225
            if scene_id == 223:
                self._log("success", f"{label}：购买弹窗未打开，仍在 #223")
                return 223
            with self._lock:
                self._status.update({
                    "phase": "daily_dungeon_wait_purchase_result",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到 #224/#225，最后 {scene_text} {last_score:.0f}%")

    def _close_daily_dungeon_purchase_unavailable(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image225: dict[str, Any],
    ):
        blank_shape = self._find_shape(image225, "空白")
        if blank_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #225「空白」标注，无法关闭数量不足弹窗")
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image225,
            blank_shape,
            {},
            label="日常_每日副本：关闭数量不足弹窗",
        )
        yield from context.wait_action_settle(1.0)

    def _click_daily_dungeon_purchase_uses(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image224: dict[str, Any],
        image225: dict[str, Any],
        *,
        task_label: str,
    ):
        use_shape = self._find_shape(image224, "购买并使用")
        if use_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #224「购买并使用」标注，无法购买")
        if not isinstance(image225, dict):
            raise RuntimeError("日常_每日副本：缺少 #225「购买次数不足」标注，无法确认购买终止态")
        if self._find_shape(image225, "空白") is None:
            raise RuntimeError("日常_每日副本：缺少 #225「空白」标注，无法关闭购买终止弹窗")
        max_count = int(payload.get("purchase_uses") or payload.get("buy_uses") or payload.get("max_purchase_uses") or 3)
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        clicked = 0
        while clicked < max_count:
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
            if self._daily_dungeon_text_is_purchase_unavailable(text):
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
                self._log("success", f"{task_label}：购买终止于 #225，已回到副本挑战")
                return "success"
            if not self._daily_dungeon_text_is_purchase(text):
                result = yield from context.wait_any(
                    {
                        "purchase": context.ocr_matches(
                            self._daily_dungeon_text_is_purchase,
                            label=f"{task_label}：等待购买破界符 OCR",
                            preview_chars=120,
                        ),
                        "unavailable": context.ocr_matches(
                            self._daily_dungeon_text_is_purchase_unavailable,
                            label=f"{task_label}：等待购买终止 OCR",
                            preview_chars=120,
                        ),
                    },
                    timeout=float(payload.get("purchase_timeout") or 10.0),
                    interval=float(payload.get("purchase_wait_interval_seconds") or 0.25),
                    label=f"{task_label}：等待购买弹窗结果",
                )
                frame = context.cur_frame()
                text = context.ocr_text(frame)
                if result == "unavailable" or self._daily_dungeon_text_is_purchase_unavailable(text):
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
                    self._log("success", f"{task_label}：购买终止于 #225，已回到副本挑战")
                    return "success"
            remaining = self._daily_dungeon_purchase_remaining_count(text)
            if remaining is None:
                self._log("warning", f"{task_label}：未识别到剩余限购次数，继续按 #225 终止态购买，OCR={text[:120]}")
            target_count = min(max_count, clicked + max(1, remaining or 1))
            yield from self._click_shape_respecting_conditions(
                ctx,
                stop_event,
                image224,
                use_shape,
                payload,
                label=f"{task_label}：购买并使用 {clicked + 1}/{target_count}",
                timeout_key="purchase_click_timeout",
            )
            clicked += 1
            yield from context.wait_action_settle(float(payload.get("purchase_click_settle_seconds") or 0.35))
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
            self._log("detail", f"{task_label}：购买并使用 {clicked}/{target_count} 后 OCR={text[:120]}")
            if self._daily_dungeon_text_is_purchase_unavailable(text):
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
                self._log("success", f"{task_label}：购买并使用完成 {clicked}，已到 #225 并回到 #223")
                return "success"
            if not self._daily_dungeon_text_is_purchase(text):
                yield from context.wait_action_settle(float(payload.get("purchase_post_click_verify_seconds") or 0.5))
                text = context.ocr_text(update=True)
                if self._daily_dungeon_text_is_purchase_unavailable(text):
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from context.wait_scene([223], wait=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
                    self._log("success", f"{task_label}：购买并使用完成 {clicked}，已到 #225 并回到 #223")
                    return "success"
                if not self._daily_dungeon_text_is_purchase(text):
                    self._log("success", f"{task_label}：购买弹窗已离开，停止购买，OCR={text[:120]}")
                    return "success"
        self._log("warning", f"{task_label}：购买并使用达到上限 {clicked}，仍未识别 #225，停止购买")
        return "success"

    def _execute_daily_shuangxiu_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_双修资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)
        image215 = images.get(215)
        image216 = images.get(216)
        image217 = images.get(217)
        image218 = images.get(218)
        image219 = images.get(219)
        image221 = images.get(221)

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([221, 219, 218, 217, 216, 215, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 221:
            return (yield from self._click_daily_shuangxiu_continue(ctx, stop_event, payload))
        if scene_id == 219:
            if self._daily_shuangxiu_text_remaining_zero(text):
                return (yield from self._finish_daily_shuangxiu_after_continue(ctx, stop_event, payload))
            return (yield from self._click_daily_shuangxiu_start_training(ctx, stop_event, payload))
        if scene_id == 218:
            return (yield from self._click_daily_shuangxiu_first_partner(ctx, stop_event, payload))
        if scene_id == 217:
            return (yield from self._click_daily_shuangxiu_xianyuan_tab(ctx, stop_event, payload))
        if scene_id == 216:
            return (yield from self._click_daily_shuangxiu_invite(ctx, stop_event, payload))
        if scene_id == 215:
            return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
        if scene_id != 69:
            if scene_id != 34:
                raise RuntimeError("日常_双修：当前不在可识别的世界、日常页或双修秘术页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_双修")):
                _wait_scene_match = yield from context.wait_scene([215, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_id == 215:
                    return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label="日常_双修",
                )

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label="日常_双修",
            title_pattern=r"完成\s*双\s*人\s*修\s*炼\s*1\s*次|双\s*人\s*修\s*炼|双\s*修",
            progress_can_mark_done=True,
        )
        if daily_status == "done":
            self._record_daily_entry_done(
                payload,
                task_id="legacy-daily-shuangxiu",
                task_type="daily_shuangxiu",
                label="日常_双修",
                message="日常列表显示已完成",
            )
            return "success"
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-shuangxiu",
                task_type="daily_shuangxiu",
                label="日常_双修",
                entry_label="完成双人修炼1次",
            )
            return "skipped"
        context = self._behavior_tree_context(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        yield from context.wait_scene(
            [215],
            wait=float(payload.get("secret_timeout") or payload.get("post_click_timeout") or 12.0),
            label="日常_双修：等待双修秘术页 #215",
        )
        return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload))

    def _click_daily_shuangxiu_first_book(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        frame: str | None = None,
    ) -> str:
        self._raise_if_stopped(stop_event)
        image215 = ctx.get("images", {}).get(215)
        if not isinstance(image215, dict):
            raise RuntimeError("日常_双修：缺少 #215「双修秘术」标注，无法点击痴情咒")
        book_shape = self._find_shape(image215, "痴情咒", "shape 2")
        if book_shape is None:
            raise RuntimeError("日常_双修：#215 缺少「痴情咒」点击区域标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #215「痴情咒」",
                phase="daily_shuangxiu_click_first_book",
                current_scene=215,
            )
            self._log_locked("action", "日常_双修：点击 #215「痴情咒」第一本书")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image215,
            book_shape,
            payload,
            label="日常_双修：等待 #215「痴情咒」",
            y_ratio=0.35,
            timeout_key="book_click_timeout",
        )
        yield from self._wait_daily_shuangxiu_detail(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_invite(ctx, stop_event, payload))

    def _wait_daily_shuangxiu_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from context.wait_scene(
            [216],
            wait=float(payload.get("detail_timeout") or 12.0),
            label="日常_双修：等待痴情咒详情",
        )
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：已进入痴情咒详情",
                phase="daily_shuangxiu_detail_ready",
                current_scene=216,
            )

    def _click_daily_shuangxiu_invite(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image216 = ctx.get("images", {}).get(216)
        if not isinstance(image216, dict):
            raise RuntimeError("日常_双修：缺少 #216「双修痴情咒详情」标注，无法点击邀请道友")
        invite_shape = self._find_shape(image216, "邀请道友", "shape 1")
        if invite_shape is None:
            raise RuntimeError("日常_双修：#216 缺少「邀请道友」按钮标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #216「邀请道友」",
                phase="daily_shuangxiu_click_invite",
                current_scene=216,
            )
            self._log_locked("action", "日常_双修：点击 #216「邀请道友」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image216,
            invite_shape,
            payload,
            label="日常_双修：等待 #216「邀请道友」",
            timeout_key="invite_click_timeout",
        )
        yield from self._wait_daily_shuangxiu_invite(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_xianyuan_tab(ctx, stop_event, payload))

    def _daily_shuangxiu_text_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        compact = re.sub(r"\s+", "", normalized).replace("O", "0").replace("o", "0")
        return "今日剩余修炼次数" in compact and bool(re.search(r"今日剩余修炼次数[:：]?0(?:\D|$)", compact))

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
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
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
                scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
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
                scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
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
        if scene_id in {188, 189}:
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


    def _leave_mail_scene_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        context: BehaviorTreeContext,
        scene_id: int,
        *,
        label: str,
    ):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        current = images.get(scene_id)
        back_shape = self._find_shape(current, "空白-返回") if isinstance(current, dict) else None
        if not isinstance(current, dict) or back_shape is None:
            raise RuntimeError(f"{label}：当前在邮件页 #{scene_id}，但缺少「空白-返回」标注，无法恢复到世界")
        with self._lock:
            self._set_status_locked("running", f"{label}：退出邮件页 #{scene_id}", phase="daily_leave_mail_scene", current_scene=scene_id)
            if scene_id == 121:
                self._log_locked("action", f"{label}：点击 #121 外侧空白恢复到世界")
            else:
                self._log_locked("action", f"{label}：点击 #{scene_id}「空白-返回」恢复到世界")
        def close_mail_list_to_world():
            yield from context.wait_click_then_scene(
                121,
                "空白-返回",
                34,
                timeout=25.0,
                label=f"{label}：关闭邮件列表并等待世界 #34",
            )
            self._log("success", f"{label}：已关闭邮件页回到 #34")
        if scene_id == 121:
            yield from close_mail_list_to_world()
        else:
            yield from context.wait_click(scene_id, "空白-返回")
            view = yield from context.wait_scene([34, 121, 227], wait=18.0, label=f"{label}：等待离开邮件详情")
            landed_scene_id = getattr(view, "scene_id", getattr(view, "id", None))
            if landed_scene_id == 227:
                self._log("action", f"{label}：邮件详情返回后出现奖励页，点击 #227「继续」")
                yield from context.wait_click(227, "继续", timeout=8.0)
                view = yield from context.wait_scene([34, 121], wait=12.0, label=f"{label}：奖励页关闭后等待邮件或世界")
                landed_scene_id = getattr(view, "scene_id", getattr(view, "id", None))
            if landed_scene_id == 34:
                self._log("success", f"{label}：已从邮件详情回到 #34")
                return
            if landed_scene_id == 121:
                self._log("action", f"{label}：邮件详情已返回 #121，继续关闭邮件列表")
                yield from close_mail_list_to_world()
                return
            yield from context.wait_scene([34], label=f"{label}：等待返回世界 #34")


    def _run_daily_lingzu_challenge(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image184 = ctx.get("images", {}).get(184)
        image185 = ctx.get("images", {}).get(185)
        image187 = ctx.get("images", {}).get(187)
        image188 = ctx.get("images", {}).get(188)
        image189 = ctx.get("images", {}).get(189)
        if not all(isinstance(item, dict) for item in (image184, image185, image187, image188, image189)):
            raise RuntimeError("缺少 #184/#185/#187/#188/#189 灵祖挑战标注，无法挑战灵祖")

        if not hasattr(context, "current_scene") or not hasattr(context, "ocr_text"):
            raise RuntimeError("日常_灵祖要求正式 BehaviorTreeContext 场景接口")
        observer = context
        action_context = context if hasattr(context, "wait_click") else self._behavior_tree_context(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from observer.wait_scene([184, 185, 186, 187, 188, 189, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
        )
        if self._daily_lingzu_text_is_detail(observer.ocr_text(frame)):
            scene_id = 184
        if scene_id == 184:
            go_shape = self._find_shape(image184, "前往")
            if go_shape is None:
                raise RuntimeError("缺少 #184「前往」标注，无法前往战灵长老")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：前往战灵长老", phase="daily_lingzu_go_elder", current_scene=184)
                self._log_locked("action", "日常_灵祖：点击 #184「前往」")
            box = self._box(go_shape, image184)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            action_context.click_frame_point(image184, x, y)
            yield from action_context.wait_action_settle(1.0)
            landing = yield from action_context.wait_scene(
                [187],
                wait=float(payload.get("lingzu_elder_timeout") or 45.0),
                label="日常_灵祖：等待战灵长老 #187",
            )
            scene_id, _score = int(getattr(landing, "id", landing)), 100.0
            frame = observer.cur_frame(update=True)

        if scene_id == 187:
            challenge_shape = self._find_shape(image187, "灵祖挑战")
            if challenge_shape is None:
                raise RuntimeError("缺少 #187「灵祖挑战」标注，无法进入圣雷龙妖祖")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：进入圣雷龙妖祖", phase="daily_lingzu_open_boss", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「灵祖挑战」")
            yield from action_context.wait_click(187, "灵祖挑战")
            landing = yield from action_context.wait_scene(
                [188],
                wait=float(payload.get("lingzu_boss_timeout") or 30.0),
                label="日常_灵祖：等待圣雷龙妖祖 #188",
            )
            scene_id, _score = int(getattr(landing, "id", landing)), 100.0
            frame = observer.cur_frame(update=True)

        if scene_id == 188:
            text = observer.ocr_text(frame)
            if self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示剩余奖励次数 0/1")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            go_shape = self._find_shape(image188, "前往")
            if go_shape is None:
                raise RuntimeError("缺少 #188「前往」标注，无法开始灵祖挑战")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：开始圣雷龙妖祖挑战", phase="daily_lingzu_start_boss", current_scene=188)
                self._log_locked("action", "日常_灵祖：点击 #188「前往」")
            yield from action_context.wait_click(188, "前往")
        elif scene_id == 186:
            self._record_daily_lingzu_done(payload, message="当前已在灵祖奖励完成态")
            yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
            return "success"

        start = time.monotonic()
        skipped = False
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene([34, 185, 186, 188, 189], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            text = observer.ocr_text(frame)
            if scene_id == 185 or "跳过" in text:
                skip_shape = self._find_shape(image185, "跳过")
                if skip_shape is not None:
                    with self._lock:
                        self._set_status_locked("running", "日常_灵祖：跳过挑战过场", phase="daily_lingzu_skip_cutscene", current_scene=185)
                        self._log_locked("action", "日常_灵祖：点击 #185「跳过」")
                    yield from action_context.wait_click(185, "跳过")
                    skipped = True
                    continue
            if scene_id == 189 or "点击退出" in text:
                exit_shape = self._find_shape(image189, "点击退出")
                if exit_shape is None:
                    raise RuntimeError("缺少 #189「点击退出」标注，无法离开灵祖挑战结算")
                with self._lock:
                    self._set_status_locked("running", "日常_灵祖：退出挑战结算", phase="daily_lingzu_exit_result", current_scene=189)
                    self._log_locked("action", "日常_灵祖：点击 #189「点击退出」")
                yield from action_context.wait_click(189, "点击退出")
                continue
            if scene_id == 186:
                self._record_daily_lingzu_done(payload, message="已回到世界并出现灵祖奖励")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            if scene_id == 188 and self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示挑战次数已消耗")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            if scene_id == 34:
                self._record_daily_lingzu_done(payload, message="挑战后已回到世界")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵祖：等待挑战完成，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_lingzu_wait_done",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= 90:
                detail = "，已点击跳过" if skipped else ""
                raise RuntimeError(f"日常_灵祖：等待挑战完成超时{detail}，最后文本：{text[:120]}")
