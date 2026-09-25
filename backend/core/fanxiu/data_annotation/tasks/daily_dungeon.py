"""每日副本业务：入口识别、挑战次数处理、扫荡结算和返回世界。

玩法策略与结果识别集中于本模块；通用场景、点击和日常列表入口能力
由宿主执行器提供。任务入口保持 _execute_daily_dungeon_task。
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


class DailyDungeonTaskMixin:
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
