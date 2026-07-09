from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation import runtime_runner as _runtime_runner
from backend.core.fanxiu.data_annotation.runtime_runner import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _now,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
)

class DailyChallengeTaskMixin:
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([226, 225, 224, 223, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if self._daily_dungeon_text_is_result(text):
            return (yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label))
        if self._daily_dungeon_text_is_completed(text):
            return (yield from self._finish_daily_dungeon_completed(ctx, stop_event, payload, text=text, task_label=task_label))
        if scene_id == 226:
            yield from self._handle_daily_dungeon_quick_sweep_prompt(ctx, stop_event, payload, task_label=task_label)
            return (yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label))
        if scene_id == 225 or self._daily_dungeon_text_is_purchase_unavailable(text):
            yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
            yield from runtime.wait_view(223, timeout=10.0, label="日常_每日副本：等待回到副本挑战 #223")
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
                scene_id, _score, frame = runtime.current_scene([226, 225, 224, 223, 69, 34], update=True)
                if scene_id in {69, 34}:
                    break
                if scene_id == 226:
                    yield from self._handle_daily_dungeon_quick_sweep_prompt(ctx, stop_event, payload, task_label=task_label)
                    return (yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label))
                if scene_id == 225:
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from runtime.wait_view(223, timeout=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if scene_id == 224:
                    yield from self._click_daily_dungeon_purchase_uses(ctx, stop_event, payload, image224, image225, task_label=task_label)
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if scene_id == 223:
                    return (yield from self._click_daily_dungeon_buy(ctx, stop_event, payload, image223, image224, image225, task_label=task_label))
                text = runtime.ocr_text(frame)
                if self._daily_dungeon_text_is_result(text):
                    return (yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label))
                if self._daily_dungeon_text_is_completed(text):
                    return (yield from self._finish_daily_dungeon_completed(ctx, stop_event, payload, text=text, task_label=task_label))
                if self._daily_dungeon_text_is_purchase(text):
                    yield from self._click_daily_dungeon_purchase_uses(ctx, stop_event, payload, image224, image225, task_label=task_label)
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if self._daily_dungeon_text_is_purchase_unavailable(text):
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from runtime.wait_view(223, timeout=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                    return (yield from self._click_daily_dungeon_sweep(ctx, stop_event, payload, image223, task_label=task_label))
                if self._daily_dungeon_text_is_entry(text):
                    return (yield from self._click_daily_dungeon_recommend_and_buy(ctx, stop_event, payload, image222, image223, image224, image225, task_label=task_label))
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
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
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-dungeon"),
            next_time,
            task_type="daily_dungeon",
            label="日常_每日副本",
            last_result="success",
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image222,
            recommend_shape,
            payload,
            label=f"{task_label}：点击推荐副本",
            timeout_key="recommend_timeout",
        )
        yield from runtime.wait_action_settle(float(payload.get("recommend_click_settle_seconds") or 2.0))
        scene_id, score, frame = runtime.current_scene(update=True)
        text = runtime.ocr_text(frame)
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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待副本挑战 #223")
        text = runtime.ocr_text(update=True)
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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image223,
            buy_shape,
            payload,
            label=f"{task_label}：打开购买",
            timeout_key="buy_timeout",
        )
        yield from runtime.wait_action_settle(float(payload.get("buy_click_settle_seconds") or 1.5))
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
        yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待副本挑战 #223")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image223,
            sweep_shape,
            payload,
            label=f"{task_label}：点击扫荡",
            timeout_key="sweep_timeout",
        )
        prompt_result = yield from self._handle_daily_dungeon_quick_sweep_prompt(ctx, stop_event, payload, task_label=task_label)
        yield from self._finish_daily_dungeon_result(ctx, stop_event, payload, task_label=task_label)
        yield from runtime.wait_action_settle(float(payload.get("sweep_click_settle_seconds") or 2.0))
        text = runtime.ocr_text(update=True)
        self._log("success", f"{task_label}：已点击扫荡，提示处理={prompt_result}，OCR={text[:120]}")
        return "success"

    def _handle_daily_dungeon_quick_sweep_prompt(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        image226 = (ctx.get("images") or {}).get(226)
        if not isinstance(image226, dict):
            raise RuntimeError("日常_每日副本：缺少 #226「快速扫荡提示」标注，无法确认扫荡提示")
        continue_shape = self._find_shape(image226, "继续扫荡")
        if continue_shape is None:
            raise RuntimeError("日常_每日副本：缺少 #226「继续扫荡」标注，无法确认扫荡提示")
        timeout = float(payload.get("quick_sweep_prompt_timeout") or 10.0)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, _frame = runtime.current_scene([226], update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id == 226:
                yield from self._click_shape_respecting_conditions(
                    ctx,
                    stop_event,
                    image226,
                    continue_shape,
                    payload,
                    label=f"{task_label}：继续扫荡",
                    timeout_key="continue_sweep_timeout",
                )
                yield from runtime.wait_action_settle(float(payload.get("continue_sweep_settle_seconds") or 1.5))
                self._log("success", f"{task_label}：已点击继续扫荡")
                return "clicked"
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                self._log("detail", f"{task_label}：10 秒内未出现 #226 快速扫荡提示，继续后续逻辑，最后 {scene_text} {last_score:.0f}%")
                return "not_found"
            with self._lock:
                self._status.update({
                    "phase": "daily_dungeon_wait_quick_sweep_prompt",
                    "current_scene": scene_id,
                    "message": f"{task_label}：等待快速扫荡提示 #226，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })

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
        return (
            "次6/6" in compact
            or "6/6已完成" in compact
        )

    def _daily_dungeon_challenge_count(self, compact_text: str) -> tuple[int, int] | None:
        normalized = compact_text.translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(r"(?:今日可挑战次数|可挑战次数|挑战次数)[:：]?([0-9Oo]+)/([0-9Oo]+)", normalized)
        if not match:
            return None
        try:
            current = int(match.group(1).replace("O", "0").replace("o", "0"))
            total = int(match.group(2).replace("O", "0").replace("o", "0"))
        except ValueError:
            return None
        return current, total

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            text = runtime.ocr_text(update=True)
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
                yield from runtime.wait_action_settle(float(payload.get("result_continue_settle_seconds") or 2.0))
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
            scene_id, _score, _frame = runtime.current_scene([227, 223, 34], update=False)
            if scene_id == 34 and self._daily_lingta_text_is_world_like(text):
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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.wait_view(223, timeout=float(payload.get("return_challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
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
            scene_id, score, frame = runtime.current_scene([223, 34], update=True)
            text = runtime.ocr_text(frame)
            last_text = text or last_text
            if scene_id == 34 and self._daily_lingta_text_is_world_like(text) and not self._daily_dungeon_text_is_entry(text):
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, _frame = runtime.current_scene([224, 225, 223], update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id == 224:
                self._log("success", f"{label}：进入 #224 购买破界符")
                return 224
            if scene_id == 225:
                self._log("success", f"{label}：进入 #225 数量不足，关闭弹窗")
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from runtime.wait_view(223, timeout=10.0, label="日常_每日副本：等待回到副本挑战 #223")
                return 225
            text = runtime.ocr_text(_frame)
            if self._daily_dungeon_text_is_purchase(text):
                self._log("success", f"{label}：OCR 确认 #224 购买破界符")
                return 224
            if self._daily_dungeon_text_is_purchase_unavailable(text):
                self._log("success", f"{label}：OCR 确认 #225 数量不足，关闭弹窗")
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from runtime.wait_view(223, timeout=10.0, label="日常_每日副本：等待回到副本挑战 #223")
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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image225,
            blank_shape,
            {},
            label="日常_每日副本：关闭数量不足弹窗",
        )
        yield from runtime.wait_action_settle(1.0)

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        clicked = 0
        while clicked < max_count:
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if self._daily_dungeon_text_is_purchase_unavailable(text):
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
                self._log("success", f"{task_label}：购买终止于 #225，已回到副本挑战")
                return "success"
            if not self._daily_dungeon_text_is_purchase(text):
                result = yield from runtime.wait_any(
                    {
                        "purchase": runtime.ocr_matches(
                            self._daily_dungeon_text_is_purchase,
                            label=f"{task_label}：等待购买破界符 OCR",
                            preview_chars=120,
                        ),
                        "unavailable": runtime.ocr_matches(
                            self._daily_dungeon_text_is_purchase_unavailable,
                            label=f"{task_label}：等待购买终止 OCR",
                            preview_chars=120,
                        ),
                    },
                    timeout=float(payload.get("purchase_timeout") or 10.0),
                    interval=float(payload.get("purchase_wait_interval_seconds") or 0.25),
                    label=f"{task_label}：等待购买弹窗结果",
                )
                frame = runtime.cur_frame()
                text = runtime.ocr_text(frame)
                if result == "unavailable" or self._daily_dungeon_text_is_purchase_unavailable(text):
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
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
            yield from runtime.wait_action_settle(float(payload.get("purchase_click_settle_seconds") or 0.35))
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            self._log("detail", f"{task_label}：购买并使用 {clicked}/{target_count} 后 OCR={text[:120]}")
            if self._daily_dungeon_text_is_purchase_unavailable(text):
                yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
                self._log("success", f"{task_label}：购买并使用完成 {clicked}，已到 #225 并回到 #223")
                return "success"
            if not self._daily_dungeon_text_is_purchase(text):
                yield from runtime.wait_action_settle(float(payload.get("purchase_post_click_verify_seconds") or 0.5))
                text = runtime.ocr_text(update=True)
                if self._daily_dungeon_text_is_purchase_unavailable(text):
                    yield from self._close_daily_dungeon_purchase_unavailable(ctx, stop_event, image225)
                    yield from runtime.wait_view(223, timeout=float(payload.get("challenge_timeout") or 18.0), label=f"{task_label}：等待回到副本挑战 #223")
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

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([216, 215, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if self._daily_shuangxiu_text_is_complete(text):
            return (yield from self._click_daily_shuangxiu_continue(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_remaining_zero(text):
            return (yield from self._finish_daily_shuangxiu_after_continue(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_training_ready(text):
            return (yield from self._click_daily_shuangxiu_start_training(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_xianyuan_invite_list(text):
            return (yield from self._click_daily_shuangxiu_first_partner(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_invite(text):
            return (yield from self._click_daily_shuangxiu_xianyuan_tab(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_detail(text):
            return (yield from self._click_daily_shuangxiu_invite(ctx, stop_event, payload))
        if scene_id == 216:
            return (yield from self._click_daily_shuangxiu_invite(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_book_list(text):
            return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
        if scene_id == 215:
            return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
        if scene_id != 69:
            if scene_id != 34 and not self._daily_lingta_text_is_world_like(text):
                raise RuntimeError("日常_双修：当前不在可识别的世界、日常页或双修秘术页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_双修")):
                scene_id, _score, frame = runtime.current_scene([215, 69, 34], update=True)
                if scene_id == 215:
                    return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
            if scene_id != 69:
                yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
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
        runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        wait_result = yield from runtime.wait_any(
            {
                "scene": runtime.view_visible(215),
                "book_list": runtime.ocr_matches(
                    self._daily_shuangxiu_text_is_book_list,
                    label="日常_双修：双修秘术书列表 OCR",
                    preview_chars=120,
                ),
            },
            timeout=float(payload.get("secret_timeout") or payload.get("post_click_timeout") or 12.0),
            label="日常_双修：等待双修秘术页 #215",
        )
        if wait_result in {"scene", "book_list"}:
            return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload))
        frame = runtime.cur_frame(update=True)
        if self._daily_shuangxiu_text_is_book_list(runtime.ocr_text(frame)):
            return (yield from self._click_daily_shuangxiu_first_book(ctx, stop_event, payload, frame=frame))
        raise RuntimeError("日常_双修：已点击日常入口，但未进入 #215 双修秘术页")

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

    def _daily_shuangxiu_text_is_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "痴情咒" in compact and (
            "邀请道友" in compact
            or "双人神通" in compact
            or "双人互动" in compact
        )

    def _daily_shuangxiu_text_is_book_list(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return (
            "秘术" in compact
            and "双人" in compact
            and ("自创功法书" in compact or "合欢" in compact or "痴情咒" in compact)
        )

    def _wait_daily_shuangxiu_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from runtime.wait_any(
            {
                "detail": runtime.ocr_matches(
                    self._daily_shuangxiu_text_is_detail,
                    label="痴情咒详情",
                    preview_chars=120,
                )
            },
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

    def _daily_shuangxiu_text_is_invite(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "邀请" in compact and "仙缘" in compact and ("好友" in compact or "灵界" in compact or "次数不足" in compact)

    def _daily_shuangxiu_text_is_xianyuan_invite_list(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "邀请" in compact and "仙缘" in compact and "好感度" in compact

    def _daily_shuangxiu_text_is_training_ready(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "前往修炼" in compact and ("今日剩余修炼次数" in compact or "双人修炼规则" in compact or "修炼场景" in compact)

    def _daily_shuangxiu_text_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        compact = re.sub(r"\s+", "", normalized).replace("O", "0").replace("o", "0")
        return "今日剩余修炼次数" in compact and bool(re.search(r"今日剩余修炼次数[:：]?0(?:\D|$)", compact))

    def _daily_shuangxiu_text_is_complete(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "修炼完成" in compact and ("点击屏幕继续" in compact or "获得修为" in compact)

    def _daily_free_challenge_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(re.search(r"剩余奖励次数[:：]?(?:0|O)(?:/\d{1,3})?", normalized, re.IGNORECASE))

    def _daily_free_challenge_remaining_count(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        match = re.search(r"剩余奖励次数[:：]?(\d{1,3}|O)(?:/\d{1,3})?", normalized, re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).replace("O", "0")
        return int(value)

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
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
            task_type=task_type,
            label=task_label,
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        runtime.clear_frame()
        for _index in range(4):
            scene_id, _score, frame = runtime.current_scene([34, 69, 188, 187, 183], update=True)
            text = runtime.ocr_text(frame)
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
            runtime.click_shape_center(back_image, "返回")
            yield from runtime.wait_action_settle(2.0)
            runtime.clear_frame()
        scene_id, _score, _frame = runtime.current_scene([34, 188, 187, 183, 69], update=True)
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
            frame = runtime.cur_frame(update=True)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从挑战页返回", phase="daily_free_challenge_return_main", current_scene=188)
                self._log_locked("action", f"{task_label}：点击 #188「返回」")
            yield from runtime.wait_click(188, "返回")
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
                frame = runtime.cur_frame(update=True)
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭中间对话", phase="daily_free_challenge_close_dialogue", current_scene=187)
                    self._log_locked("action", f"{task_label}：点击 #187「空白」")
                yield from runtime.wait_click(187, "空白")
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
                frame = runtime.cur_frame(update=True)
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：返回世界", phase="daily_free_challenge_return_world_click", current_scene=183)
                    self._log_locked("action", f"{task_label}：点击 #183「返回」")
                yield from runtime.wait_click(183, "返回")
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
            frame = runtime.cur_frame(update=True)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从日常列表返回世界", phase="daily_free_challenge_return_daily", current_scene=69)
                self._log_locked("action", f"{task_label}：点击 #69「退出」")
            yield from runtime.wait_click(69, "退出")
            yield from runtime.wait_action_settle(2.0)
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                yield from runtime.wait_action_settle(2.0)
            yield from runtime.wait_view(34, timeout=18.0, label=f"{task_label}：等待世界 #34")
        runtime.clear_frame()
        scene_id, _score, _frame = runtime.current_scene([34], update=True)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([188, 189, 69, 34], update=True)
            lines = runtime.ocr_lines(frame)
            text = runtime.ocr_text(frame)
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
                runtime.click_shape_center(image227, str(continue_shape.get("title") or "继续"))
                yield from runtime.wait_action_settle(2.0)
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
                runtime.click_frame_point(69, x, y)
                yield from runtime.wait_action_settle(2.0)
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
                runtime.click_frame_point(69, x, y)
                yield from runtime.wait_action_settle(4.0)
                continue
            if "妖兽波数" in _sanitize_ocr_text(text) or ("副本" in text and "用时" in text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：等待自动剿灭完成",
                        phase="daily_free_challenge_wait_combat",
                        current_scene=scene_id,
                    )
                runtime.clear_frame()
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
                yield from runtime.wait_click(189, "点击退出")
                yield from runtime.wait_action_settle(2.0)
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
            runtime.clear_frame()
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
        payload = {"max_scrolls": 30, "reverse_scrolls": 30, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError(f"缺少{task_label}资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([223, 188, 189, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 223 or self._daily_dungeon_text_is_entry(text) or self._daily_dungeon_text_is_completed(text):
            yield from self._return_daily_dungeon_to_world(ctx, stop_event, payload, task_label=task_label)
            scene_id, _score, frame = runtime.current_scene([188, 189, 69, 34], update=True)
            text = runtime.ocr_text(frame)
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
                scene_id, _score, frame = runtime.current_scene([188, 189, 69, 34], update=True)
                text = runtime.ocr_text(frame)
            if scene_id not in {69, 188, 189}:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
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

    def _daily_assistant_entry_matches(self, lines: list[dict[str, Any]], image69: dict[str, Any]) -> list[tuple[float, float, str]]:
        scroll_shape = self._find_shape(image69, "滚动窗口")
        if scroll_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法查找小助手入口")
        image_width, image_height = self._frame_size(image69)
        box = self._box(scroll_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        tab_matches: list[tuple[float, float, str]] = []
        list_matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not re.search(r"小\s*助手|助手", text):
                continue
            line_x = float(line.get("x") or 0)
            line_y = float(line.get("y") or 0)
            line_w = float(line.get("w") or 0)
            line_h = float(line.get("h") or 0)
            cx = line_x + line_w / 2
            cy = line_y + line_h / 2
            compact = re.sub(r"\s+", "", text)
            tab_match = re.search(r"小助手|助手", compact)
            if tab_match and cy >= image_height * 0.78:
                text_len = max(1, len(compact))
                click_x = line_x + line_w * ((tab_match.start() + tab_match.end()) / 2) / text_len
                if compact.startswith("活动报名") and "奖励找回" in compact:
                    click_x = max(image_width * 0.33, min(click_x, image_width * 0.40))
                click_y = cy
                if 0 <= click_x <= image_width and 0 <= click_y <= image_height:
                    tab_matches.append((click_x, click_y, text))
                    continue
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            list_matches.append((cx, cy, text))
        return sorted(tab_matches, key=lambda item: (item[1], item[0])) + sorted(list_matches, key=lambda item: (item[1], item[0]))

    def _open_daily_assistant_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找小助手")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_scrolls = int(payload.get("assistant_max_scrolls") or payload.get("max_scrolls") or 8)
        reverse_scrolls = int(payload.get("assistant_reverse_scrolls") or payload.get("reverse_scrolls") or 8)
        for direction, scroll_count in [("down", max_scrolls), ("up", reverse_scrolls)]:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：查找小助手入口 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_assistant_find_entry",
                        current_scene=69,
                    )
                frame = runtime.cur_frame(update=True)
                lines = runtime.ocr_lines(frame)
                matches = self._daily_assistant_entry_matches(lines, image69)
                if matches:
                    x, y, matched_text = matches[0]
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_助手：点击入口 {matched_text}",
                            phase="daily_assistant_click_entry",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_助手：点击 #69「{matched_text}」")
                    runtime.click_frame_point(69, x, y)
                    yield from runtime.wait_action_settle(float(payload.get("assistant_entry_click_settle_seconds") or 2.0))
                    return "open"
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_助手：未找到小助手入口，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
                runtime.clear_frame()
        return "not_found"

    def _wait_daily_assistant_after_entry(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("post_click_timeout") or payload.get("assistant_post_click_timeout") or 20.0)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([204, 69, 34], update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id == 204:
                return 204, float(score)
            text = runtime.ocr_text(frame)
            last_text = text or last_text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, 100.0
            if scene_id in {69, 34}:
                return int(scene_id), float(score)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待小助手入口点击结果，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_wait_after_entry",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_助手：等待入口点击结果超时，未检测到小助手清单，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:120]}"
                )
            yield from runtime.wait_action_settle(0.5)

    def _wait_daily_assistant_list_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float,
        label: str,
        allow_daily_or_world: bool = False,
    ) -> tuple[int, float]:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([275, 237, 204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, float(score or 100.0)
            if allow_daily_or_world and scene_id in {69, 34}:
                return int(scene_id), float(score or 100.0)
            if scene_id == 237:
                yield from self._daily_assistant_close_youli_result(runtime, payload)
                continue
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                self._daily_assistant_close_one_key_result(ctx, runtime, frame, label=label)
                yield from runtime.wait_action_settle(float(payload.get("assistant_result_reclick_settle_seconds") or 1.0))
                continue
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"{label} 超时，未检测到 #204，最后 {scene_text} {last_score:.0f}% OCR={last_text[:160]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_wait_list_state",
                    current_scene=scene_id,
            )
            yield from runtime.wait_action_settle(0.5)

    def _daily_assistant_scene_or_text_is_list(self, scene_id: int | None, text: str) -> bool:
        if scene_id == 204:
            return True
        if scene_id in {34, 69}:
            return False
        return self._daily_assistant_text_is_list(text)

    def _daily_assistant_text_is_one_key_confirm(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(re.search(r"本次执行预计消耗.*灵石.*是否继续|是否继续执行", compact))

    def _daily_assistant_text_is_one_key_result(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            (re.search(r"神物园自动收取|仙府资源助手|本次获得的道具|自动兑换", compact) and "退出" in compact)
            or (
                "退出" in compact
                and "今日已完成" in compact
                and any(marker in compact for marker in ("宗门任务", "宗门祈福", "宗门俸禄", "宗门请安", "宗门资源"))
            )
        )

    def _daily_assistant_click_visible_exit(self, runtime: Any, frame: Any, *, scene_hint: int = 275) -> bool:
        try:
            lines = runtime.ocr_lines(frame)
        except TypeError:
            lines = runtime.ocr_lines()
        except Exception:
            lines = []
        candidates: list[tuple[float, float, float]] = []
        for line in lines or []:
            if not isinstance(line, dict):
                continue
            text = _sanitize_ocr_text(str(line.get("text") or ""))
            if "退出" not in text:
                continue
            try:
                x = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
                y = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            except (TypeError, ValueError):
                continue
            candidates.append((y, x, y))
        if not candidates:
            return False
        _sort_y, x, y = max(candidates, key=lambda item: item[0])
        runtime.click_frame_point(scene_hint, x, y)
        return True

    def _daily_assistant_close_one_key_result(self, ctx: dict[str, Any], runtime: Any, frame: Any, *, label: str) -> None:
        with self._lock:
            self._set_status_locked(
                "running",
                f"{label}：关闭小助手一键执行结果汇总",
                phase="daily_assistant_close_one_key_result",
                current_scene=275,
            )
        if self._daily_assistant_click_visible_exit(runtime, frame, scene_hint=275):
            with self._lock:
                self._log_locked("action", f"{label}：OCR 点击结果页「退出」")
            return
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image275 = images.get(275)
        if not isinstance(image275, dict) or self._find_shape(image275, "退出") is None:
            raise RuntimeError(f"{label}：结果汇总仍在前台，但缺少 #275「退出」标注，且 OCR 未定位到「退出」")
        with self._lock:
            self._log_locked("action", f"{label}：点击 #275「退出」")
        runtime.click_shape_center(image275, "退出")

    def _daily_assistant_text_is_one_key_progress(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            ("执行进度" in compact and ("剩余时间" in compact or "正在" in compact))
            or ("助手正在" in compact and ("执行进度" in compact or "寻路" in compact))
        )

    def _daily_assistant_one_key_progress_seconds(self, text: str) -> int | None:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        matches = re.findall(r"(\d{2})[:：]?(\d{2})", compact)
        for minutes, seconds in reversed(matches):
            second_value = int(seconds)
            if second_value < 60:
                return int(minutes) * 60 + second_value
        return None

    def _ensure_daily_assistant_list_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        timeout: float,
        label: str,
    ) -> tuple[int, float]:
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        poll_seconds = float(payload.get("assistant_list_state_poll_seconds") or 0.5)
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        yield from runtime.wait_action_settle(float(payload.get("assistant_list_state_initial_settle_seconds") or 1.0))
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([275, 237, 204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, float(score or 100.0)
            if scene_id == 237:
                yield from self._daily_assistant_close_youli_result(runtime, payload)
                continue
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                self._daily_assistant_close_one_key_result(ctx, runtime, frame, label=label)
                yield from runtime.wait_action_settle(float(payload.get("assistant_result_reclick_settle_seconds") or 1.0))
                continue
            if scene_id == 69:
                opened = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                if opened != "open":
                    raise RuntimeError(f"{label}：回到 #69 后未找到小助手入口，无法继续")
                return (yield from self._wait_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=timeout,
                    label=label,
                ))
            if scene_id == 34:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=label,
                )
                if scene_id == 69:
                    opened = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                    if opened != "open":
                        raise RuntimeError(f"{label}：回到 #69 后未找到小助手入口，无法继续")
                    return (yield from self._wait_daily_assistant_list_state(
                        ctx,
                        stop_event,
                        payload,
                        timeout=timeout,
                        label=label,
                    ))
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"{label}：等待小助手清单超时，最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：等待回到小助手清单，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_ensure_list_state",
                    current_scene=scene_id,
                )
            yield from runtime.wait_action_settle(poll_seconds)

    def _run_daily_assistant_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image204 = images.get(204)
        if not isinstance(image204, dict):
            image69 = images.get(69)
            exit_shape = self._find_shape(image69, "退出") if isinstance(image69, dict) else None
            if isinstance(image69, dict) and exit_shape is not None:
                asset_tree_path = ctx.get("asset_tree_path")
                runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
                with self._lock:
                    self._set_status_locked("running", "日常_助手：缺少新版小助手总览标注，先退出小助手页", phase="daily_assistant_missing_assets_return", current_scene=69)
                    self._log_locked("action", "日常_助手：缺少新版小助手总览标注，点击 #69「退出」恢复到日常页")
                yield from runtime.wait_click(69, "退出")
                yield from runtime.wait_action_settle(2.0)
            raise RuntimeError("日常_助手：已进入小助手，但资产树缺少新版 #204「小助手总览」标注，无法执行一键流程")

        one_key_shape = self._find_shape(image204, "一键执行")
        if one_key_shape is None:
            raise RuntimeError("日常_助手：旧版逐项小助手流程已下线；#204 必须标注新版「一键执行」入口")
        if any(key in payload for key in ("assistant_items", "assistant_execute_shapes", "assistant_groups", "assistant_group")):
            self._log("detail", "日常_助手：忽略旧版 assistant_items/assistant_group 参数，改用新版一键执行闭环")
        return (yield from self._run_daily_assistant_one_key_from_overview(ctx, stop_event, payload, image204))

    def _run_daily_assistant_one_key_from_overview(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image275 = images.get(275)
        image276 = images.get(276)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_助手：新版小助手总览点击一键执行",
                phase="daily_assistant_one_key_execute",
                current_scene=204,
            )
            self._log_locked("action", "日常_助手：点击 #204「一键执行」")
        yield from runtime.wait_click(204, "一键执行")
        yield from runtime.wait_action_settle(float(payload.get("assistant_one_key_click_settle_seconds") or 1.5))

        confirm_timeout = float(payload.get("assistant_one_key_confirm_timeout") or 20.0)
        start = time.monotonic()
        execution_evidence = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([276, 204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            if scene_id in {69, 34}:
                raise RuntimeError(
                    "日常_助手：不能把未确认执行结果标记为成功；"
                    f"点击一键执行后直接回到 #{scene_id}，未看到 #276/#277/#275/#237"
                )
            if scene_id == 276 or self._daily_assistant_text_is_one_key_confirm(text):
                if not isinstance(image276, dict) or self._find_shape(image276, "是") is None:
                    raise RuntimeError("日常_助手：检测到一键执行消耗确认，但缺少 #276「是」标注")
                execution_evidence = "confirm"
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：确认一键执行消耗",
                        phase="daily_assistant_one_key_confirm",
                        current_scene=276,
                    )
                    self._log_locked("action", "日常_助手：点击 #276「是」")
                yield from runtime.wait_click(276, "是")
                yield from runtime.wait_action_settle(float(payload.get("assistant_one_key_confirm_settle_seconds") or 2.0))
                break
            if scene_id == 277 or self._daily_assistant_text_is_one_key_progress(text):
                execution_evidence = "progress"
                break
            if time.monotonic() - start >= confirm_timeout:
                if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                    raise RuntimeError(
                        "日常_助手：不能把未确认执行结果标记为成功；"
                        "点击一键执行后仍在小助手总览，未看到 #276/#277/#275/#237"
                    )
                raise TimeoutError(
                    "日常_助手：点击一键执行后未检测到消耗确认或执行落点，"
                    f"最后 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待一键执行确认，当前 #{scene_id or 'unknown'} {score:.0f}%",
                    phase="daily_assistant_one_key_wait_confirm",
                    current_scene=scene_id,
            )
            yield from runtime.wait_action_settle(0.5)

        yield from self._wait_daily_assistant_one_key_progress(ctx, stop_event, payload, runtime)

        result_timeout = float(payload.get("assistant_one_key_result_timeout") or 45.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([275, 237, 204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 237:
                yield from self._daily_assistant_close_youli_result(runtime, payload)
                yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, runtime, current_scene=34)
                self._log("success", "日常_助手：已关闭游历结果页并返回世界")
                return "success"
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                self._daily_assistant_close_one_key_result(ctx, runtime, frame, label="日常_助手")
                landed_scene_id, _landed_score = yield from self._wait_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=float(payload.get("assistant_one_key_result_close_timeout") or 15.0),
                    label="日常_助手：等待结果汇总返回小助手总览",
                    allow_daily_or_world=True,
                )
                yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, runtime, current_scene=landed_scene_id)
                self._log("success", "日常_助手：新版小助手一键执行结果已关闭")
                return "success"
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                if not execution_evidence:
                    raise RuntimeError(
                        "日常_助手：不能把未确认执行结果标记为成功；"
                        "当前已在小助手总览，但没有确认/进度/结果证据"
                    )
                yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, runtime, current_scene=204)
                self._log("success", f"日常_助手：新版小助手一键执行已确认，当前已在总览，证据={execution_evidence}")
                return "success"
            if scene_id in {69, 34}:
                raise RuntimeError(
                    "日常_助手：不能把未确认执行结果标记为成功；"
                    f"一键执行后回到 #{scene_id}，但未经过 #275/#237 结果页或 #204 总览复核"
                )
            if time.monotonic() - start >= result_timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    "日常_助手：确认一键执行后未回到小助手总览，也未进入执行结果页，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待一键执行结果，当前 #{scene_id or 'unknown'} {score:.0f}%",
                    phase="daily_assistant_one_key_wait_result",
                    current_scene=scene_id,
            )
            yield from runtime.wait_action_settle(0.5)

    def _wait_daily_assistant_one_key_progress(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: Any,
    ):
        timeout = float(payload.get("assistant_one_key_progress_timeout") or 180.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([277, 275, 204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                return
            if scene_id == 277 or self._daily_assistant_text_is_one_key_progress(text):
                seconds = self._daily_assistant_one_key_progress_seconds(text)
                if seconds is None:
                    wait_seconds = 10.0
                    message = "日常_助手：等待 #277 进度，未解析到时间，10 秒后重试 OCR"
                else:
                    wait_seconds = float(min(10, max(0, seconds)))
                    message = f"日常_助手：等待 #277 进度剩余 {seconds} 秒，本轮等待 {wait_seconds:g} 秒"
                    if wait_seconds <= 0:
                        wait_seconds = 0.5
                with self._lock:
                    self._set_status_locked(
                        "running",
                        message,
                        phase="daily_assistant_one_key_wait_progress",
                        current_scene=277,
                    )
                    self._log_locked("detail", message)
                yield from runtime.wait_action_settle(wait_seconds)
                continue
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    "日常_助手：确认一键执行后未检测到 #277 进度或 #275 结果，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待进入 #277 进度，当前 #{scene_id or 'unknown'} {score:.0f}%",
                    phase="daily_assistant_one_key_wait_progress_enter",
                    current_scene=scene_id,
                )
            yield from runtime.wait_action_settle(0.5)

    def _return_after_daily_assistant_one_key(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: Any,
        *,
        current_scene: int | None,
    ):
        if not bool(payload.get("assistant_return_after_items", True)):
            if False:
                yield None
            return
        scene_id, _score, frame = runtime.current_scene([275, 237, 204, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
            self._daily_assistant_close_one_key_result(ctx, runtime, frame, label="日常_助手")
            landed_scene_id, _landed_score = yield from self._wait_daily_assistant_list_state(
                ctx,
                stop_event,
                payload,
                timeout=float(payload.get("assistant_one_key_result_close_timeout") or 15.0),
                label="日常_助手：等待结果汇总返回小助手总览",
                allow_daily_or_world=True,
            )
            current_scene = int(landed_scene_id)
        elif scene_id in {237, 204, 69, 34}:
            current_scene = int(scene_id)
        if current_scene == 204:
            for _attempt in range(3):
                scene_id, _score, frame = runtime.current_scene([275, 237, 204, 69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                    self._daily_assistant_close_one_key_result(ctx, runtime, frame, label="日常_助手")
                    landed_scene_id, _landed_score = yield from self._wait_daily_assistant_list_state(
                        ctx,
                        stop_event,
                        payload,
                        timeout=float(payload.get("assistant_one_key_result_close_timeout") or 15.0),
                        label="日常_助手：等待结果汇总返回小助手总览",
                        allow_daily_or_world=True,
                    )
                    current_scene = int(landed_scene_id)
                    continue
                if scene_id == 237:
                    yield from self._daily_assistant_close_youli_result(runtime, payload)
                    current_scene = 34
                    break
                if scene_id in {69, 34}:
                    current_scene = int(scene_id)
                    break
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：一键执行后返回日常页",
                        phase="daily_assistant_one_key_return_daily",
                        current_scene=204,
                    )
                    self._log_locked("action", "日常_助手：点击 #204「返回」")
                images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
                image204 = images.get(204)
                if not isinstance(image204, dict) or self._find_shape(image204, "返回") is None:
                    raise RuntimeError("日常_助手：缺少 #204「返回」标注，无法退出小助手总览")
                runtime.click_shape_center(image204, "返回")
                landed = yield from runtime.wait_view(
                    69,
                    34,
                    275,
                    237,
                    204,
                    timeout=float(payload.get("assistant_one_key_return_daily_timeout") or 15.0),
                    label="日常_助手：等待返回日常页",
                )
                current_scene = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
                if current_scene in {69, 34}:
                    break
        if current_scene == 237:
            yield from self._daily_assistant_close_youli_result(runtime, payload)
            current_scene = 34
        if current_scene == 69 and bool(payload.get("assistant_return_world", True)):
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_助手：一键执行后返回世界",
                    phase="daily_assistant_one_key_return_world",
                    current_scene=69,
                )
                self._log_locked("action", "日常_助手：点击 #69「退出」")
            yield from runtime.wait_click(69, "退出")
            yield from runtime.wait_view(34, timeout=float(payload.get("assistant_one_key_return_world_timeout") or 25.0), label="日常_助手：等待返回世界")

    def _daily_assistant_close_youli_result(self, runtime: Any, payload: dict[str, Any]):
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_助手：关闭游历结果页",
                phase="daily_assistant_close_youli_result",
                current_scene=237,
            )
            self._log_locked("action", "日常_助手：点击 #237「确定」关闭游历结果")
        yield from runtime.wait_click(237, "确定")
        landed = yield from runtime.wait_view(228, 204, 69, 34, timeout=float(payload.get("assistant_youli_result_close_timeout") or 15.0), label="日常_助手：等待游历结果关闭")
        landed_scene_id = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
        if landed_scene_id == 228:
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_助手：从游历页返回世界",
                    phase="daily_assistant_return_from_youli",
                    current_scene=228,
                )
                self._log_locked("action", "日常_助手：点击 #228「返回」")
            yield from runtime.wait_click(228, "返回")
            yield from runtime.wait_view(34, 69, timeout=float(payload.get("assistant_youli_return_timeout") or 18.0), label="日常_助手：等待离开游历页")

    def _execute_daily_assistant_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_助手资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([275, 204, 237, 123, 122, 121, 86, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        image275 = images.get(275)
        image275_exit = self._find_shape(image275, "退出") if isinstance(image275, dict) else None
        image275_exit_score = (
            float(self._shape_score(ctx, image275, image275_exit, frame) or 0.0)
            if isinstance(image275, dict) and isinstance(image275_exit, dict)
            else 0.0
        )
        if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text) or image275_exit_score >= 80.0:
            yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, runtime, current_scene=204)
            self._log("success", "日常_助手：启动时关闭遗留一键执行结果页")
            return "success"
        if scene_id == 237:
            yield from self._daily_assistant_close_youli_result(runtime, payload)
            scene_id, _score, frame = runtime.current_scene([204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id == 86 or self._leave_scene_confirm_text(text):
            image86 = images.get(86)
            confirm_shape = self._find_shape(image86, "确认") if isinstance(image86, dict) else None
            if not isinstance(image86, dict) or confirm_shape is None:
                raise RuntimeError("日常_助手：当前在 #86 离开确认弹窗，但缺少 #86「确认」标注，无法恢复起点")
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_助手：启动时确认离开当前场景",
                    phase="daily_assistant_start_confirm_leave",
                    current_scene=86,
                )
                self._log_locked("action", "日常_助手：启动时点击 #86「确认」离开场景")
            yield from runtime.wait_click(86, "确认")
            yield from runtime.wait_action_settle(float(payload.get("leave_confirm_settle_seconds") or 2.0))
            scene_id, _score, frame = runtime.current_scene([204, 123, 122, 121, 69, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id in {121, 122, 123}:
            yield from self._leave_mail_scene_to_world(ctx, stop_event, runtime, scene_id, label="日常_助手")
            scene_id, _score, frame = runtime.current_scene([204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
        if self._daily_assistant_scene_or_text_is_list(scene_id, text):
            return (yield from self._run_daily_assistant_from_list(ctx, stop_event, payload))
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_助手")):
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label="日常_助手",
                )

        daily_status = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
        if daily_status == "not_found":
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-assistant"),
                (_runtime_runner._now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                task_type="daily_assistant",
                label="日常_助手",
            )
            raise RuntimeError("日常_助手：未找到小助手入口，已记录 30 分钟后重试")
        scene_id, _score = yield from self._wait_daily_assistant_after_entry(ctx, stop_event, payload)
        if scene_id == 204:
            return (yield from self._run_daily_assistant_from_list(ctx, stop_event, payload))
        raise RuntimeError(f"日常_助手：入口点击后回到 #{scene_id or 'unknown'}，尚未进入新版小助手总览，不能按完成处理")

    def _leave_mail_scene_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        runtime: FanxiuRuntime,
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
            for attempt in range(3):
                runtime.click_frame_point(121, 1, 1)
                yield from runtime.wait_action_settle(1.0)
                current_scene, score, frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, [34, 121, 58], update=True)
                compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
                still_mail = any(token in compact for token in ("邮件", "已阅", "一键领取", "一键删除"))
                if current_scene == 34 and not still_mail:
                    self._log("success", f"{label}：已关闭邮件页回到 #34 {score:.0f}%")
                    return
                self._log("info", f"{label}：第 {attempt + 1} 次关闭后仍像邮件页，继续点击外侧空白")
            raise RuntimeError(f"{label}：点击外侧空白后仍未关闭 #121 邮件页")
        if scene_id == 121:
            yield from close_mail_list_to_world()
        else:
            yield from runtime.wait_click(scene_id, "空白-返回")
            view = yield from runtime.wait_view(34, 121, 227, timeout=18.0, label=f"{label}：等待离开邮件详情")
            landed_scene_id = view.id if isinstance(view, View) else None
            if landed_scene_id == 227:
                self._log("action", f"{label}：邮件详情返回后出现奖励页，点击 #227「继续」")
                yield from runtime.wait_click(227, "继续", timeout=8.0)
                view = yield from runtime.wait_view(34, 121, timeout=12.0, label=f"{label}：奖励页关闭后等待邮件或世界")
                landed_scene_id = view.id if isinstance(view, View) else None
            if landed_scene_id == 34:
                self._log("success", f"{label}：已从邮件详情回到 #34")
                return
            if landed_scene_id == 121:
                self._log("action", f"{label}：邮件详情已返回 #121，继续关闭邮件列表")
                yield from close_mail_list_to_world()
                return
            yield from runtime.wait_view(34, label=f"{label}：等待返回世界 #34")

    def _open_daily_xianyuan_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找挑战仙缘")
        if self._find_shape(image69, "滚动窗口") is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找挑战仙缘")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_scrolls = int(payload.get("max_scrolls") or payload.get("xianyuan_max_scrolls") or 14)
        reverse_scrolls = int(payload.get("reverse_scrolls") or payload.get("xianyuan_reverse_scrolls") or 18)
        passes: list[tuple[str, int]] = [("down", max_scrolls), ("up", reverse_scrolls)]
        fallback_seen = 0
        for direction, scroll_count in passes:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_挑战仙缘：查找日常任务「挑战仙缘」 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_xianyuan_find_daily_entry",
                        current_scene=69,
                    )
                frame = runtime.cur_frame(update=True)
                lines = runtime.ocr_lines(frame)
                text = runtime.ocr_text(frame)
                matches = self._daily_xianyuan_entry_matches(lines, image69)
                if matches:
                    x, y, matched_text = matches[0]
                    progress = self._daily_xianyuan_row_progress(lines, y)
                    if progress is not None and progress[0] >= progress[1]:
                        return "done"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_挑战仙缘：点击日常任务 {matched_text}",
                            phase="daily_xianyuan_click_daily_entry",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_挑战仙缘：点击 #69「{matched_text}」")
                    runtime.click_frame_point(View(image69), x, y)
                    yield from runtime.wait_action_settle(float(payload.get("xianyuan_entry_click_settle_seconds") or 2.0))
                    return "open"
                if self._daily_xianyuan_progress_done(text):
                    return "done"
                if re.search(r"(?:挑战\s*仙缘|仙缘人物)", text) and not re.search(r"仙缘斗法|斗法", text):
                    fallback_seen += 1
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：未找到「挑战仙缘」，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
                runtime.clear_frame()
        if fallback_seen >= int(payload.get("completed_fallback_min_total") or 3):
            raise RuntimeError("日常_挑战仙缘：看到标题但未解析到未完成进度，不能按完成处理")
        return "not_found"

    def _daily_xianyuan_text_is_people_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool("仙缘" in compact and ("可送礼" in compact or "隐藏已无物品的仙缘" in compact))

    def _daily_xianyuan_text_is_daily_list(self, text: str) -> bool:
        return self._daily_text_is_daily_list(text)

    def _wait_daily_xianyuan_after_entry(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("post_click_timeout") or 30.0)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([199, 198, 197, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score = scene_id, score
            if scene_id == 197 and not self._daily_xianyuan_text_is_people_list(text):
                if self._daily_xianyuan_text_is_daily_list(text):
                    return 69, float(score)
                scene_id = None
            if scene_id in {199, 198, 197, 69, 34}:
                return int(scene_id), float(score)
            last_text = text or last_text
            if self._daily_xianyuan_text_is_people_list(text):
                return 197, 100.0
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：等待入口点击结果，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_xianyuan_wait_after_entry",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_挑战仙缘：等待入口点击结果超时，未检测到 #69/#34 或仙缘列表，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:120]}"
                )

    def _scroll_daily_xianyuan_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image69: dict[str, Any],
        *,
        direction: str,
    ):
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找挑战仙缘")
        changed = yield from self._scroll_shape_content_changed(ctx, image69, list_shape, stop_event, reverse=(direction == "up"))
        if not changed:
            boundary = "顶部" if direction == "up" else "底部"
            with self._lock:
                self._log_locked("action", f"#69「滚动窗口」{direction} 拖拽后签名未变化，判定已到{boundary}")
            return False
        return True

    def _daily_xianyuan_people_list_box(self, image197: dict[str, Any]) -> dict[str, Any]:
        list_shape = self._find_shape(image197, "人物列表")
        if list_shape is not None:
            return self._box(list_shape, image197)
        width, height = self._frame_size(image197)
        return {"name": "人物列表", "x": width * 0.07, "y": height * 0.19, "w": width * 0.88, "h": height * 0.66}

    def _daily_xianyuan_target_pattern(self, payload: dict[str, Any]) -> str:
        raw = (
            payload.get("target_pattern")
            or payload.get("xianyuan_target_pattern")
            or payload.get("target")
            or payload.get("指定目标")
            or payload.get("xianyuan_target")
            or ""
        )
        target = _sanitize_ocr_text(str(raw or "")).strip()
        if target:
            return target
        return r"两立"

    def _daily_xianyuan_list_target_candidates(
        self,
        lines: list[dict[str, Any]],
        image197: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[tuple[float, float, str]]:
        box = self._daily_xianyuan_people_list_box(image197)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        pattern = self._daily_xianyuan_target_pattern(payload)
        candidates: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            line_x = float(line.get("x") or 0)
            line_y = float(line.get("y") or 0)
            line_w = float(line.get("w") or 0)
            line_h = float(line.get("h") or 0)
            cx = line_x + line_w / 2
            cy = line_y + line_h / 2
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            try:
                matches = list(re.finditer(pattern, text))
            except re.error:
                matches = []
                index = text.find(pattern)
                if index >= 0:
                    matches = [re.match(re.escape(pattern), text[index:]) or re.match(r".*", text[index:index + len(pattern)])]
            for match in matches:
                if match is None:
                    continue
                span_start, span_end = match.span()
                if span_end <= span_start:
                    continue
                text_len = max(1, len(text))
                click_x = line_x + line_w * ((span_start + span_end) / 2) / text_len
                click_y = max(top, line_y + line_h / 2 - 120)
                candidates.append((click_x, click_y, text))
        return sorted(candidates, key=lambda item: (item[1], item[0]))

    def _scroll_daily_xianyuan_people_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image197: dict[str, Any],
        *,
        direction: str = "down",
    ):
        list_shape = self._find_shape(image197, "人物列表")
        if list_shape is None:
            raise RuntimeError("日常_挑战仙缘：#197 缺少「人物列表」标注，无法滚动查找仙缘人物")
        return (yield from self._scroll_shape_content_changed(ctx, image197, list_shape, stop_event, reverse=(direction == "up")))

    def _run_daily_xianyuan_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image197 = ctx.get("images", {}).get(197)
        if not isinstance(image197, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #197「仙缘列表」标注，无法选择仙缘人物")
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_挑战仙缘：缺少资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = runtime.cur_frame(update=True)
        text = runtime.ocr_text(frame)
        if not self._daily_xianyuan_text_is_people_list(text):
            if self._daily_xianyuan_text_is_daily_list(text):
                raise RuntimeError("日常_挑战仙缘：当前仍在日常列表，#197 场景身份误判，不能查找仙缘人物")
            raise RuntimeError(f"日常_挑战仙缘：当前不是仙缘人物列表，OCR={text[:120]}")
        max_scrolls = int(payload.get("people_max_scrolls") or payload.get("xianyuan_people_max_scrolls") or 8)
        passes: list[tuple[str, int]] = [("down", max_scrolls), ("up", max_scrolls)]
        hide_empty_toggled = False
        for search_round in range(2):
            if search_round == 1:
                hide_shape = self._find_shape(image197, "隐藏已无物品的仙缘")
                if hide_shape is None:
                    break
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_挑战仙缘：关闭隐藏已无物品后继续查找",
                        phase="daily_xianyuan_toggle_hide_empty",
                        current_scene=197,
                    )
                    self._log_locked("action", "日常_挑战仙缘：点击 #197「隐藏已无物品的仙缘」后重试目标搜索")
                runtime.click_shape_center(View(image197), "隐藏已无物品的仙缘")
                hide_empty_toggled = True
                yield from runtime.wait_action_settle(float(payload.get("xianyuan_toggle_settle_seconds") or 2.0))

            for direction, scroll_count in passes:
                for scroll_index in range(scroll_count + 1):
                    self._raise_if_stopped(stop_event)
                    frame = runtime.cur_frame(update=True)
                    lines = runtime.ocr_lines(frame)
                    candidates = self._daily_xianyuan_list_target_candidates(lines, image197, payload)
                    if candidates:
                        x, y, matched_text = candidates[0]
                        with self._lock:
                            self._set_status_locked(
                                "running",
                                f"日常_挑战仙缘：选择仙缘人物 {matched_text[:24]}",
                                phase="daily_xianyuan_click_person",
                                current_scene=197,
                            )
                            self._log_locked("action", f"日常_挑战仙缘：点击 #197 仙缘人物候选「{matched_text[:40]}」")
                        runtime.click_frame_point(View(image197), x, y)
                        yield from runtime.wait_action_settle(float(payload.get("xianyuan_person_click_settle_seconds") or 2.0))
                        scene_id, score = yield from self._wait_daily_xianyuan_after_person_click(ctx, stop_event, payload)
                        if scene_id == 198:
                            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
                        raise RuntimeError(f"日常_挑战仙缘：已进入后续页面 #{scene_id or 'unknown'} {score:.0f}%，需要继续补详情/对话/挑战标注")
                    if scroll_index >= scroll_count:
                        break
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_挑战仙缘：查找仙缘人物 {direction} {scroll_index + 1}/{scroll_count}",
                            phase="daily_xianyuan_find_person",
                            current_scene=197,
                        )
                        suffix = "（已关闭隐藏无物品）" if hide_empty_toggled else ""
                        self._log_locked("action", f"日常_挑战仙缘：未找到目标{suffix}，{direction} 滚动仙缘人物列表 {scroll_index + 1}")
                    yield from self._scroll_daily_xianyuan_people_list(ctx, stop_event, image197, direction=direction)
        raise RuntimeError(f"日常_挑战仙缘：仙缘列表未找到目标「{self._daily_xianyuan_target_pattern(payload)}」")

    def _wait_daily_xianyuan_after_person_click(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_挑战仙缘：缺少资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        timeout = float(payload.get("person_click_timeout") or 18.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            runtime.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([199, 198, 197, 69, 34], update=True)
            last_scene_id, last_score = scene_id, score
            text = runtime.ocr_text(frame)
            if self._daily_xianyuan_text_is_dialogue(text):
                return 199, 100.0
            if self._daily_xianyuan_text_is_detail(text):
                return 198, 100.0
            if scene_id in {199, 198}:
                return scene_id, score
            if time.monotonic() - start >= timeout:
                return last_scene_id, last_score

    def _daily_xianyuan_text_is_detail(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            "前往" in compact
            and ("身份" in compact or "功法主修" in compact or "出没地点" in compact)
            and not re.search(r"可送礼|隐藏已无物品的仙缘|教他做人|看招吧", compact)
        )

    def _run_daily_xianyuan_from_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image198 = ctx.get("images", {}).get(198)
        if not isinstance(image198, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #198「仙缘人物详情」标注，无法前往人物")
        go_shape = self._find_shape(image198, "前往")
        if go_shape is None:
            raise RuntimeError("日常_挑战仙缘：缺少 #198「前往」标注，无法前往人物")
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：点击人物详情「前往」", phase="daily_xianyuan_go_person", current_scene=198)
            self._log_locked("action", "日常_挑战仙缘：点击 #198「前往」")
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        scene_id: int | None = None
        score = 0.0
        max_attempts = max(1, int(payload.get("detail_go_max_attempts") or 2))
        for attempt_index in range(max_attempts):
            box = self._box(go_shape, image198)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            runtime.click_frame_point(image198, x, y)
            yield from runtime.wait_action_settle(float(payload.get("xianyuan_detail_go_settle_seconds") or 2.0))
            scene_id, score = yield from self._wait_daily_xianyuan_after_detail_go(ctx, stop_event, payload)
            if scene_id == 199:
                return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
            if scene_id != 198:
                break
            if attempt_index + 1 < max_attempts:
                with self._lock:
                    self._log_locked("action", "日常_挑战仙缘：人物详情仍停留 #198，按旧版逻辑再次点击「前往」")
        raise RuntimeError(f"日常_挑战仙缘：已前往后续页面 #{scene_id or 'unknown'} {score:.0f}%，需要继续补人物对话/挑战标注")

    def _wait_daily_xianyuan_after_detail_go(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("日常_挑战仙缘：缺少资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        timeout = float(payload.get("detail_go_timeout") or 35.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            runtime.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([199, 198, 197, 69, 34], update=True)
            last_scene_id, last_score = scene_id, score
            text = runtime.ocr_text(frame)
            if self._daily_xianyuan_text_is_dialogue(text):
                return 199, 100.0
            if self._daily_xianyuan_text_is_detail(text):
                return 198, 100.0
            if scene_id in {199, 198}:
                return scene_id, score
            if time.monotonic() - start >= timeout:
                return last_scene_id, last_score

    def _daily_xianyuan_text_is_dialogue(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            ("教他做人" in compact or ("查探" in compact and "送礼" in compact))
            and not re.search(r"可送礼|隐藏已无物品的仙缘|出没地点", compact)
        )

    def _run_daily_xianyuan_from_dialogue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image199 = ctx.get("images", {}).get(199)
        if not isinstance(image199, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #199「仙缘人物对话」标注，无法发起挑战")
        observer = self._fanxiu_observer(ctx, stop_event)
        frame = observer.cur_frame(update=True)
        lines = observer.ocr_lines(frame)
        text = observer.ocr_text(frame)
        teach_matches = self._daily_xianyuan_dialogue_button_matches(lines, image199, r"教他做人")
        if not teach_matches:
            raise RuntimeError(f"日常_挑战仙缘：当前仙缘人物没有「教他做人」按钮，不能挑战；OCR={text[:120]}")
        match_x, match_y, matched_text = teach_matches[0]
        x, y = match_x, match_y
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：点击「教他做人」", phase="daily_xianyuan_teach", current_scene=199)
            self._log_locked("action", f"日常_挑战仙缘：点击 #199「{matched_text}」")
        observer.click_frame_point(View(image199), x, y)
        yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
        return (yield from self._run_daily_xianyuan_after_teach(ctx, stop_event, payload))

    def _run_daily_xianyuan_from_challenge_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        scene_id: int,
    ):
        ref_image = self._daily_xianyuan_reference_image(ctx)
        if scene_id in {200, 201}:
            return (yield from self._run_daily_xianyuan_after_teach(ctx, stop_event, payload))
        if scene_id == 202:
            yield from self._wait_daily_xianyuan_challenge_result(ctx, stop_event, payload, ref_image)
            self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image),
                label="日常_挑战仙缘",
                action="离开挑战结果",
                repeat_risk="重复挑战",
            )
            return "success"
        if scene_id == 203:
            self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image),
                label="日常_挑战仙缘",
                action="离开挑战结果",
                repeat_risk="重复挑战",
            )
            return "success"
        raise RuntimeError(f"日常_挑战仙缘：无法从 #{scene_id} 恢复挑战流程")

    def _daily_xianyuan_reference_image(self, ctx: dict[str, Any]) -> dict[str, Any]:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for scene_id in (203, 202, 201, 200, 199, 198, 197, 34, 69):
            image = images.get(scene_id)
            if isinstance(image, dict):
                return image
        return {"filename": "daily_xianyuan_runtime.png", "width": 900, "height": 1600}

    def _daily_xianyuan_challenge_count_empty(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("O", "0").replace("o", "0")
        return bool(re.search(r"(?:今日)?可挑战次数[:：]?[0零]/[1-9]\d*", normalized))

    def _daily_xianyuan_text_button_matches(
        self,
        lines: list[dict[str, Any]],
        pattern: str,
        *,
        left_ratio: float = 0.0,
        right_ratio: float = 1.0,
        top_ratio: float = 0.0,
        bottom_ratio: float = 1.0,
        width: float = 900.0,
        height: float = 1600.0,
    ) -> list[tuple[float, float, str]]:
        left = width * left_ratio
        right = width * right_ratio
        top = height * top_ratio
        bottom = height * bottom_ratio
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not re.search(pattern, text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _run_daily_xianyuan_after_teach(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        observer = self._fanxiu_observer(ctx, stop_event)
        ref_image = self._daily_xianyuan_reference_image(ctx)
        width, height = self._frame_size(ref_image)
        challenge_scene_ids = [203, 202, 201, 200, 199, 198, 197, 69, 34]

        teach_deadline = time.monotonic() + float(payload.get("teach_disappear_timeout") or 15.0)
        while time.monotonic() < teach_deadline:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, _score, frame = observer.current_scene(challenge_scene_ids, update=True)
            if scene_id in {200, 201, 202, 203}:
                break
            lines = observer.ocr_lines(frame)
            teach_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"教他做人",
                left_ratio=0.45,
                right_ratio=0.98,
                top_ratio=0.45,
                bottom_ratio=0.82,
                width=width,
                height=height,
            )
            if not teach_matches:
                break
            x, y, _text = teach_matches[0]
            observer.click_frame_point(View(ref_image), x, y)
            yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))

        attack_deadline = time.monotonic() + float(payload.get("attack_dialogue_timeout") or 45.0)
        last_advance = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, _score, frame = observer.current_scene(challenge_scene_ids, update=True)
            lines = observer.ocr_lines(frame)
            text = observer.ocr_text(frame)
            if scene_id == 201:
                break
            if self._daily_xianyuan_challenge_count_empty(text):
                self._record_daily_xianyuan_done(payload, message="仙缘对话显示今日可挑战次数已空")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_xianyuan_current_to_world(ctx, stop_event),
                    label="日常_挑战仙缘",
                    repeat_risk="重复挑战",
                )
                return "success"
            attack_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"看招吧",
                left_ratio=0.35,
                right_ratio=0.98,
                top_ratio=0.35,
                bottom_ratio=0.86,
                width=width,
                height=height,
            )
            if attack_matches:
                x, y, matched_text = attack_matches[0]
                with self._lock:
                    self._set_status_locked("running", "日常_挑战仙缘：点击「看招吧」", phase="daily_xianyuan_attack", current_scene=199)
                    self._log_locked("action", f"日常_挑战仙缘：点击「{matched_text}」")
                observer.click_frame_point(View(ref_image), x, y)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                break
            if self._daily_assistant_text_is_world_like(text) and (yield from self._leave_world_side_scene_if_present(
                ctx,
                stop_event,
                frame,
                text,
                label="日常_挑战仙缘",
            )):
                with self._lock:
                    self._log_locked("action", "日常_挑战仙缘：离开场景后重新复核日常进度")
                return (yield from self._execute_daily_xianyuan_task(ctx, stop_event, payload))
            now = time.monotonic()
            if now >= attack_deadline:
                raise TimeoutError(f"日常_挑战仙缘：等待「看招吧」超时，OCR={text[:120]}")
            if now - last_advance >= 3.0:
                observer.click_frame_point(View(ref_image), width * 0.48, height * 0.76)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_dialogue_advance_settle_seconds") or 2.0))
                last_advance = now

        continue_deadline = time.monotonic() + float(payload.get("challenge_continue_timeout") or 5.0)
        while time.monotonic() <= continue_deadline:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, _score, frame = observer.current_scene(challenge_scene_ids, update=True)
            if scene_id == 202:
                break
            lines = observer.ocr_lines(frame)
            matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"继续",
                left_ratio=0.25,
                right_ratio=0.85,
                top_ratio=0.45,
                bottom_ratio=0.88,
                width=width,
                height=height,
            )
            if matches:
                x, y, matched_text = matches[0]
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：点击挑战提示「{matched_text}」")
                observer.click_frame_point(View(ref_image), x, y)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                break

        yield from self._wait_daily_xianyuan_challenge_result(ctx, stop_event, payload, ref_image)
        self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image),
            label="日常_挑战仙缘",
            action="离开挑战结果",
            repeat_risk="重复挑战",
        )
        return "success"

    def _wait_daily_xianyuan_challenge_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        ref_image: dict[str, Any],
    ):
        observer = self._fanxiu_observer(ctx, stop_event)
        width, height = self._frame_size(ref_image)
        deadline = time.monotonic() + float(payload.get("challenge_result_timeout") or 300.0)
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            frame = observer.cur_frame(update=True)
            text = observer.ocr_text(frame)
            last_text = text or last_text
            if re.search(r"友好度|减少", _sanitize_ocr_text(text)):
                with self._lock:
                    self._log_locked("success", "日常_挑战仙缘：识别到友好度减少结果")
                observer.click_frame_point(View(ref_image), width * 0.50, height * 0.62)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                return "success"
            if re.search(r"离\s*开|离开", _sanitize_ocr_text(text)):
                return "success"
            if time.monotonic() >= deadline:
                raise TimeoutError(f"日常_挑战仙缘：等待挑战结果超时，OCR={last_text[:120]}")

    def _leave_daily_xianyuan_battle(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        ref_image: dict[str, Any],
    ):
        observer = self._fanxiu_observer(ctx, stop_event)
        width, height = self._frame_size(ref_image)
        deadline = time.monotonic() + float(payload.get("battle_leave_timeout") or 60.0)
        last_leave_click = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = observer.current_scene([34, 69, 197, 198, 199, 200, 201, 202, 203], update=True)
            if scene_id == 34:
                with self._lock:
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {score:.0f}%")
                return "success"
            lines = observer.ocr_lines(frame)
            text = observer.ocr_text(frame)
            last_text = text or last_text
            if self._daily_lingta_text_is_world_like(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", "日常_挑战仙缘：已回到世界")
                return "success"
            confirm_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"确认|确定",
                left_ratio=0.25,
                right_ratio=0.85,
                top_ratio=0.45,
                bottom_ratio=0.88,
                width=width,
                height=height,
            )
            if confirm_matches:
                x, y, matched_text = confirm_matches[-1]
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：点击离开确认「{matched_text}」")
                observer.click_frame_point(View(ref_image), x, y)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_click_settle_seconds") or 2.0))
                continue
            now = time.monotonic()
            if now - last_leave_click >= 3.0:
                leave_matches = self._daily_xianyuan_text_button_matches(
                    lines,
                    r"离\s*开|离开",
                    left_ratio=0.72,
                    right_ratio=1.0,
                    top_ratio=0.35,
                    bottom_ratio=0.72,
                    width=width,
                    height=height,
                )
                if leave_matches:
                    x, y, matched_text = leave_matches[0]
                    with self._lock:
                        self._log_locked("action", f"日常_挑战仙缘：点击「{matched_text}」")
                    observer.click_frame_point(View(ref_image), x, y)
                else:
                    observer.click_frame_point(View(ref_image), width * 0.92, height * 0.08)
                yield from observer.wait_action_settle(float(payload.get("xianyuan_leave_settle_seconds") or 2.0))
                last_leave_click = now
            if now >= deadline:
                raise TimeoutError(f"日常_挑战仙缘：点击离开后等待确认框超时，OCR={last_text[:120]}")

    def _daily_xianyuan_dialogue_button_matches(
        self,
        lines: list[dict[str, Any]],
        image199: dict[str, Any],
        pattern: str,
    ) -> list[tuple[float, float, str]]:
        width, height = self._frame_size(image199)
        left = width * 0.45
        right = width * 0.97
        top = height * 0.45
        bottom = height * 0.78
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not re.search(pattern, text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _return_daily_xianyuan_current_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        observer = self._fanxiu_observer(ctx, stop_event)
        scene_id, score, frame = observer.current_scene([34, 69, 197, 198, 199, 200, 201, 202, 203], update=True)
        if scene_id == 34:
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        text = observer.ocr_text(frame)
        if self._daily_lingta_text_is_world_like(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
                self._log_locked("success", "日常_挑战仙缘：已回到世界")
            return "success"
        if scene_id == 69:
            return (yield from self._return_daily_xianyuan_to_world(ctx, stop_event))
        if scene_id in {197, 198}:
            image = ctx.get("images", {}).get(scene_id)
            if not isinstance(image, dict):
                raise RuntimeError(f"日常_挑战仙缘：缺少 #{scene_id} 标注，无法安全返回世界")
            back_shape = self._find_shape(image, "返回")
            if back_shape is None:
                raise RuntimeError(f"日常_挑战仙缘：缺少 #{scene_id}「返回」标注，无法安全返回世界")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：从 #{scene_id} 返回日常/世界",
                    phase="daily_xianyuan_return_current",
                    current_scene=scene_id,
                )
            self._log_locked("action", f"日常_挑战仙缘：点击 #{scene_id}「返回」")
            observer.click_shape_center(View(image), "返回")
            yield from observer.wait_action_settle(2.0)
            next_scene_id, next_score, frame = observer.current_scene([34, 69], update=True)
            if next_scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {next_score:.0f}%")
                return "success"
            if next_scene_id == 69:
                return (yield from self._return_daily_xianyuan_to_world(ctx, stop_event))
            text = observer.ocr_text(frame)
            if self._daily_lingta_text_is_world_like(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", "日常_挑战仙缘：已回到世界")
                return "success"
            raise RuntimeError(f"日常_挑战仙缘：点击 #{scene_id}「返回」后未回到 #69/#34，当前 #{next_scene_id or 'unknown'} {next_score:.0f}%")
        raise RuntimeError(
            f"日常_挑战仙缘：当前 #{scene_id or 'unknown'} 显示次数已空，但缺少该页返回世界标注，不能按完成处理"
        )

    def _return_daily_xianyuan_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #69「日常」标注，无法回世界")
        observer = self._fanxiu_observer(ctx, stop_event)
        scene_id, _score, frame = observer.current_scene([69, 34], update=True)
        if scene_id == 34:
            return "success"
        text = observer.ocr_text(frame)
        if scene_id != 69 and self._daily_lingta_text_is_world_like(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        if scene_id != 69:
            raise RuntimeError("日常_挑战仙缘：当前不在 #69 或 #34，缺少后续页面标注，无法安全返回")
        exit_shape = self._find_shape(image69, "退出")
        if exit_shape is None:
            raise RuntimeError("日常_挑战仙缘：缺少 #69「退出」标注，无法回世界")
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：从日常列表返回世界", phase="daily_xianyuan_return_daily", current_scene=69)
            self._log_locked("action", "日常_挑战仙缘：点击 #69「退出」")
        observer.click_shape_center(View(image69), "退出")
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = observer.current_scene([34], update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {score:.0f}%")
                return "success"
            text = observer.ocr_text(frame)
            last_text = text or last_text
            if self._daily_lingta_text_is_world_like(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", "日常_挑战仙缘：已回到世界")
                return "success"
            if time.monotonic() - start >= 18.0:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"日常_挑战仙缘：等待世界超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：等待世界，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_xianyuan_wait_world",
                    current_scene=scene_id,
                )
        return "success"

    def _run_daily_lingzu_challenge(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
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

        observer = runtime if hasattr(runtime, "current_scene") and hasattr(runtime, "ocr_text") else self._fanxiu_observer(ctx, stop_event)
        action_runtime = runtime if hasattr(runtime, "wait_click") else self._fanxiu_runtime(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        scene_id, _score, frame = observer.current_scene([184, 185, 186, 187, 188, 189, 34], update=True)
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
            action_runtime.click_frame_point(image184, x, y)
            yield from action_runtime.wait_action_settle(1.0)
            scene_id, _score = yield from action_runtime.wait_view_id(
                187,
                timeout=float(payload.get("lingzu_elder_timeout") or 45.0),
                label="日常_灵祖：等待战灵长老 #187",
            )
            frame = observer.cur_frame(update=True)

        if scene_id == 187:
            challenge_shape = self._find_shape(image187, "灵祖挑战")
            if challenge_shape is None:
                raise RuntimeError("缺少 #187「灵祖挑战」标注，无法进入圣雷龙妖祖")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：进入圣雷龙妖祖", phase="daily_lingzu_open_boss", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「灵祖挑战」")
            yield from action_runtime.wait_click(187, "灵祖挑战")
            scene_id, _score = yield from action_runtime.wait_view_id(
                188,
                timeout=float(payload.get("lingzu_boss_timeout") or 30.0),
                label="日常_灵祖：等待圣雷龙妖祖 #188",
            )
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
            yield from action_runtime.wait_click(188, "前往")
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
            scene_id, score, frame = observer.current_scene([34, 185, 186, 188, 189], update=True)
            text = observer.ocr_text(frame)
            if scene_id == 185 or "跳过" in text:
                skip_shape = self._find_shape(image185, "跳过")
                if skip_shape is not None:
                    with self._lock:
                        self._set_status_locked("running", "日常_灵祖：跳过挑战过场", phase="daily_lingzu_skip_cutscene", current_scene=185)
                        self._log_locked("action", "日常_灵祖：点击 #185「跳过」")
                    yield from action_runtime.wait_click(185, "跳过")
                    skipped = True
                    continue
            if scene_id == 189 or "点击退出" in text:
                exit_shape = self._find_shape(image189, "点击退出")
                if exit_shape is None:
                    raise RuntimeError("缺少 #189「点击退出」标注，无法离开灵祖挑战结算")
                with self._lock:
                    self._set_status_locked("running", "日常_灵祖：退出挑战结算", phase="daily_lingzu_exit_result", current_scene=189)
                    self._log_locked("action", "日常_灵祖：点击 #189「点击退出」")
                yield from action_runtime.wait_click(189, "点击退出")
                continue
            if scene_id == 186 or "点击查看" in text or "灵环" in text or "宝魄" in text:
                self._record_daily_lingzu_done(payload, message="已回到世界并出现灵祖奖励")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            if scene_id == 188 and self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示挑战次数已消耗")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            if scene_id == 34 or ("日常" in text and ("储物袋" in text or "战斗" in text)):
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
