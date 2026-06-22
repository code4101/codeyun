from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation import runtime_runner as _runtime_runner
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.runtime_runner import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _now,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
)

_DAILY_ASSISTANT_FIRST_SCREEN_EXECUTE_ITEMS: tuple[str, ...] = (
    "执行-道义秘库助手",
    "执行-神物园助手",
    "执行-宗门助手",
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
            raise RuntimeError(f"{task_label}：#69 日常列表未找到入口，不能继续")

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
        scene_id, _score, frame = runtime.current_scene([215, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if self._daily_shuangxiu_text_is_complete(text):
            return (yield from self._click_daily_shuangxiu_continue(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_training_ready(text):
            return (yield from self._click_daily_shuangxiu_start_training(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_xianyuan_invite_list(text):
            return (yield from self._click_daily_shuangxiu_first_partner(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_invite(text):
            return (yield from self._click_daily_shuangxiu_xianyuan_tab(ctx, stop_event, payload))
        if self._daily_shuangxiu_text_is_detail(text):
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
            raise RuntimeError("日常_双修：#69 日常列表未找到「完成双人修炼1次」，不能继续")
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
        return "痴情咒" in compact and "邀请道友" in compact and ("双人神通" in compact or "双人互动" in compact)

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
                raise RuntimeError(f"{task_label}：出现「购买并使用」弹窗，默认不购买次数或道具，已停止等待人工关闭")
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
            raise RuntimeError(f"{task_label}：出现「购买并使用」弹窗，默认不购买次数或道具，已停止等待人工关闭")
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
            raise RuntimeError(f"{task_label}：#69 日常列表未找到入口，不能按完成处理")

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
        matches: list[tuple[float, float, str]] = []
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
                    matches.append((click_x, click_y, text))
                    continue
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

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
        *,
        timeout: float,
        label: str,
    ) -> tuple[int, float]:
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, float(score or 100.0)
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
            scene_id, score, frame = runtime.current_scene([204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, float(score or 100.0)
            if scene_id == 69:
                opened = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                if opened != "open":
                    raise RuntimeError(f"{label}：回到 #69 后未找到小助手入口，无法继续")
                return (yield from self._wait_daily_assistant_list_state(
                    ctx,
                    stop_event,
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
                    self._set_status_locked("running", "日常_助手：缺少小助手清单新帧标注，先退出小助手页", phase="daily_assistant_missing_assets_return", current_scene=69)
                    self._log_locked("action", "日常_助手：缺少小助手清单新帧标注，点击 #69「退出」恢复到日常页")
                yield from runtime.wait_click(69, "退出")
                yield from runtime.wait_action_settle(2.0)
            raise RuntimeError(
                "日常_助手：已进入小助手清单，但资产树尚未新增小助手清单帧标注；"
                "当前资产树最后编号是 #203，建议把小助手清单作为下一帧 #204；"
                "需要补小助手清单身份、清单区域、任务块标题、执行按钮、退出、完成页「点击屏幕继续」、"
                "同游结果「确定/查看下一个/空白关闭」等标注后才能继续复刻完整流程"
            )

        results: list[tuple[str, str]] = []
        if "assistant_items" in payload:
            assistant_items = payload.get("assistant_items")
        elif "assistant_execute_shapes" in payload:
            assistant_items = payload.get("assistant_execute_shapes")
        else:
            assistant_items = None
        if assistant_items is not None:
            results.extend((yield from self._run_daily_assistant_items_from_list(ctx, stop_event, payload, image204, assistant_items)))
        else:
            groups = payload.get("assistant_groups")
            if groups is None and payload.get("assistant_group") is not None:
                groups = payload.get("assistant_group")
            if groups is None:
                groups = ["完整小助手"]
                payload.setdefault("assistant_return_after_items", True)
            if isinstance(groups, str):
                groups = [item.strip() for item in re.split(r"[,，\s]+", groups) if item.strip()]
            if not isinstance(groups, list):
                raise RuntimeError("日常_助手：assistant_groups 参数格式错误")
            if any(re.sub(r"[\s_-]+", "", _sanitize_ocr_text(str(group) or "")).lower() in {"all", "full", "完整小助手", "全部小助手", "完整", "全部"} for group in groups):
                payload.setdefault("assistant_return_after_items", True)
            for group in groups:
                group_results = yield from self._run_daily_assistant_group_from_list(ctx, stop_event, payload, image204, str(group))
                results.extend(group_results)

        if bool(payload.get("assistant_return_after_items")):
            back_shape = self._find_shape(image204, "返回")
            if back_shape is not None:
                asset_tree_path = ctx.get("asset_tree_path")
                runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
                scene_id, score, frame = runtime.current_scene([204, 69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 69:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            "日常_助手：助手闭环后已在日常页",
                            phase="daily_assistant_return_daily",
                            current_scene=69,
                        )
                        self._log_locked("action", "日常_助手：助手闭环后已在 #69，跳过 #204 返回")
                elif self._daily_assistant_scene_or_text_is_list(scene_id, text):
                    with self._lock:
                        self._set_status_locked("running", "日常_助手：助手闭环后返回日常页", phase="daily_assistant_return_daily", current_scene=204)
                        self._log_locked("action", "日常_助手：点击 #204「返回」")
                    yield from runtime.wait_click(204, "返回")
                    yield from runtime.wait_view(69, label="日常_助手：等待返回日常页")
                else:
                    raise RuntimeError(
                        "日常_助手：助手闭环收尾时不在小助手清单或日常页，"
                        f"当前 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}"
                    )
        summary = "，".join(f"{title}={result}" for title, result in results)
        unverified = [(title, result) for title, result in results if not self._daily_assistant_result_is_verified(str(result))]
        if unverified:
            bad_summary = "，".join(f"{title}={result}" for title, result in unverified)
            raise RuntimeError(
                "日常_助手：不能把未确认执行结果标记为成功；"
                f"未确认项：{bad_summary}；"
                f"本轮摘要：{summary}"
            )
        self._log("success", f"日常_助手：小助手可见执行项闭环完成，{summary}")
        return "success"

    def _daily_assistant_result_is_verified(self, result: str) -> bool:
        return str(result or "") in {
            "detail_closed",
            "fixed_clicked",
            "teaching_complete_closed",
            "result_closed",
            "confirmed_no_result",
            "cancelled",
            "returned_daily",
            "no_popup",
            "no_popup_verified",
            "no_action",
        }

    def _run_daily_assistant_items_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
        assistant_items: Any,
    ):
        if isinstance(assistant_items, str):
            assistant_items = [item.strip() for item in re.split(r"[,，\s]+", assistant_items) if item.strip()]
        if not isinstance(assistant_items, list):
            raise RuntimeError("日常_助手：assistant_items 参数格式错误")
        results: list[tuple[str, str]] = []
        for shape_title in assistant_items:
            result = yield from self._run_daily_assistant_item_from_list(
                ctx,
                stop_event,
                payload,
                image204,
                str(shape_title),
            )
            results.append((str(shape_title), str(result)))
            if str(result) == "returned_daily" and shape_title != assistant_items[-1]:
                payload.pop("_daily_assistant_list_bottom_reached", None)
                open_status = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                if open_status != "open":
                    raise RuntimeError(f"日常_助手：执行「{shape_title}」后回到日常页，但重新打开小助手失败：{open_status}")
                yield from self._wait_daily_assistant_after_entry(ctx, stop_event, payload)
            elif shape_title != assistant_items[-1]:
                asset_tree_path = ctx.get("asset_tree_path")
                runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
                scene_id, _score, frame = runtime.current_scene([204, 69], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 69:
                    payload.pop("_daily_assistant_list_bottom_reached", None)
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_助手：执行「{shape_title}」后回到日常页，重新打开小助手",
                            phase="daily_assistant_reopen_after_daily_return",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_助手：执行「{shape_title}」后回到 #69，重新打开小助手")
                    open_status = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                    if open_status != "open":
                        raise RuntimeError(f"日常_助手：执行「{shape_title}」后回到日常页，但重新打开小助手失败：{open_status}")
                    yield from self._wait_daily_assistant_after_entry(ctx, stop_event, payload)
        return results

    def _run_daily_assistant_group_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
        group: str,
    ):
        normalized = re.sub(r"[\s_-]+", "", _sanitize_ocr_text(group or "")).lower()
        if normalized in {"initialexecutes", "initial", "前三项", "前三个执行", "firstthree"}:
            return (yield from self._run_daily_assistant_initial_execute_group(ctx, stop_event, payload, image204))
        if normalized in {"xianfuresource", "仙府资源", "仙府"}:
            return (yield from self._run_daily_assistant_xianfu_resource_group(ctx, stop_event, payload, image204))
        if normalized in {"teachingtongyouteaching", "授业传道授业", "授业-传道-授业"}:
            return (yield from self._run_daily_assistant_teaching_tongyou_teaching_group(ctx, stop_event, payload, image204))
        if normalized in {"studyteach", "studyteaching", "求学教学", "求学-教学"}:
            return (yield from self._run_daily_assistant_study_teaching_group(ctx, stop_event, payload, image204))
        if normalized in {"all", "full", "完整小助手", "全部小助手", "完整", "全部"}:
            return (yield from self._run_daily_assistant_full_group(ctx, stop_event, payload, image204))
        raise RuntimeError(f"日常_助手：未知 assistant_group={group}")

    def _run_daily_assistant_initial_execute_group(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        return (yield from self._run_daily_assistant_items_from_list(
            ctx,
            stop_event,
            payload,
            image204,
            list(_DAILY_ASSISTANT_FIRST_SCREEN_EXECUTE_ITEMS),
        ))

    def _run_daily_assistant_xianfu_resource_group(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        return (yield from self._run_daily_assistant_items_from_list(
            ctx,
            stop_event,
            payload,
            image204,
            ["仙府资源小助手/领取"],
        ))

    def _run_daily_assistant_teaching_tongyou_teaching_group(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        return (yield from self._run_daily_assistant_items_from_list(
            ctx,
            stop_event,
            payload,
            image204,
            ["弟子授业助手/执行", "同游传道助手/执行", "弟子授业助手/执行"],
        ))

    def _run_daily_assistant_study_teaching_group(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        return (yield from self._run_daily_assistant_items_from_list(
            ctx,
            stop_event,
            payload,
            image204,
            ["弟子求学助手/前往", "弟子教学助手/前往"],
        ))

    def _run_daily_assistant_full_group(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        results: list[tuple[str, str]] = []
        groups = ["initial_executes", "xianfu_resource", "授业-传道-授业", "求学-教学"]
        for index, group in enumerate(groups):
            if index > 0:
                yield from self._ensure_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=float(payload.get("assistant_item_detail_return_timeout") or payload.get("assistant_daoyi_detail_return_timeout") or 10.0),
                    label=f"日常_助手：进入「{group}」分组前确认小助手清单",
                )
            group_results = yield from self._run_daily_assistant_group_from_list(ctx, stop_event, payload, image204, group)
            results.extend(group_results)
        return results

    def _daily_assistant_item_parts(self, item_title: str) -> tuple[str, str] | None:
        text = str(item_title or "").strip()
        if not text:
            return None
        if "/" in text:
            parent, action = [part.strip() for part in text.split("/", 1)]
            if parent and action:
                return parent, action
        for prefix in ("执行-", "领取-"):
            if text.startswith(prefix) and len(text) > len(prefix):
                return text[len(prefix):].strip(), prefix[:-1]
        return None

    def _daily_assistant_is_first_screen_fixed_item(self, item_title: str) -> bool:
        return str(item_title or "").strip() in _DAILY_ASSISTANT_FIRST_SCREEN_EXECUTE_ITEMS

    def _daily_assistant_child_shape(
        self,
        parent_shape: dict[str, Any] | None,
        *titles: str,
    ) -> dict[str, Any] | None:
        if not isinstance(parent_shape, dict):
            return None
        for child in self._flatten_shapes(parent_shape.get("children")):
            title = str(child.get("title") or "").strip()
            if title in titles:
                return child
        return None

    def _daily_assistant_scroll_box(self, image204: dict[str, Any]) -> dict[str, float]:
        scroll_shape = self._find_shape(image204, "滚动窗口")
        if scroll_shape is None:
            raise RuntimeError("日常_助手：#204 缺少「滚动窗口」标注，无法定位浮动助手条目")
        box = self._box(scroll_shape, image204)
        return {
            "left": float(box.get("x") or 0),
            "top": float(box.get("y") or 0),
            "right": float(box.get("x") or 0) + float(box.get("w") or 0),
            "bottom": float(box.get("y") or 0) + float(box.get("h") or 0),
        }

    def _daily_assistant_title_center_in_scroll(
        self,
        lines: list[dict[str, Any]],
        image204: dict[str, Any],
        title: str,
    ) -> tuple[float, float, str] | None:
        title = _sanitize_ocr_text(title)
        if not title:
            return None
        box = self._daily_assistant_scroll_box(image204)
        best: tuple[float, float, str] | None = None
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            compact_text = re.sub(r"\s+", "", text)
            compact_title = re.sub(r"\s+", "", title)
            if compact_title not in compact_text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            index = compact_text.find(compact_title)
            cx = x + w * ((index + len(compact_title) / 2) / max(1, len(compact_text)))
            cy = y + h / 2
            if box["left"] <= cx <= box["right"] and box["top"] <= cy <= box["bottom"]:
                best = (cx, cy, text)
                break
        return best

    def _daily_assistant_floating_action_point(
        self,
        image204: dict[str, Any],
        lines: list[dict[str, Any]],
        parent_title: str,
        action_title: str,
    ) -> tuple[float, float, str] | None:
        parent_shape = self._find_shape(image204, parent_title)
        template_parent_shape = parent_shape or self._daily_assistant_row_template_shape(image204, action_title)
        if template_parent_shape is None:
            return None
        action_shape = self._daily_assistant_child_shape(template_parent_shape, action_title)
        if action_shape is None and action_title == "前往":
            action_shape = self._daily_assistant_child_shape(template_parent_shape, "执行", "领取")
        title_shape = self._daily_assistant_child_shape(template_parent_shape, "标题")
        if action_shape is None:
            return None
        title_hit = self._daily_assistant_title_center_in_scroll(lines, image204, parent_title)
        if title_hit is None:
            return None
        actual_title_x, actual_title_y, matched_text = title_hit
        action_x, action_y = ActionPlanner().shape_center(image204, action_shape)
        if title_shape is not None:
            title_x, title_y = ActionPlanner().shape_center(image204, title_shape)
        else:
            parent_box = self._box(template_parent_shape, image204)
            title_x = float(parent_box.get("x") or 0) + float(parent_box.get("w") or 0) * 0.62
            title_y = float(parent_box.get("y") or 0) + float(parent_box.get("h") or 0) * 0.28
        click_x = actual_title_x + (action_x - title_x)
        click_y = actual_title_y + (action_y - title_y)
        width, height = self._frame_size(image204)
        click_x = max(0.0, min(float(width), click_x))
        click_y = max(0.0, min(float(height), click_y))
        return click_x, click_y, matched_text

    def _daily_assistant_row_template_shape(self, image204: dict[str, Any], action_title: str) -> dict[str, Any] | None:
        action_candidates = [action_title]
        if action_title == "前往":
            action_candidates.extend(["执行", "领取"])
        for shape in image204.get("shapes") or []:
            if (
                self._daily_assistant_child_shape(shape, "标题") is not None
                and self._daily_assistant_child_shape(shape, *action_candidates) is not None
            ):
                return shape
        return None

    def _run_daily_assistant_daoyi_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        return (yield from self._run_daily_assistant_item_from_list(
            ctx,
            stop_event,
            payload,
            image204,
            "执行-道义秘库助手",
        ))

    def _run_daily_assistant_item_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
        shape_title: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_id, score, frame = runtime.current_scene([263, 209, 205, 204, 208, 69, 34], update=True)
        lines = runtime.ocr_lines(frame)
        text = runtime.ocr_text(frame)
        result_scene_id = self._daily_assistant_result_scene_id(scene_id, text)
        if result_scene_id is not None:
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            result_image = self._daily_assistant_result_continue_image(images, result_scene_id)
            if not isinstance(result_image, dict):
                raise RuntimeError(f"日常_助手：准备点击「{shape_title}」前停在 #{result_scene_id} 结果页，但缺少资产标注")
            continue_shape = self._find_shape(result_image, "点击屏幕继续")
            if continue_shape is None:
                raise RuntimeError(f"日常_助手：准备点击「{shape_title}」前停在 #{result_scene_id} 结果页，但缺少「点击屏幕继续」标注")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：先关闭上一条结果页，再点击「{shape_title}」",
                    phase="daily_assistant_item_preclose_result",
                    current_scene=result_scene_id,
                )
                self._log_locked("action", f"日常_助手：准备点击「{shape_title}」前先点击 #{result_scene_id}「点击屏幕继续」")
            runtime.click_shape_center(result_image, "点击屏幕继续")
            yield from self._ensure_daily_assistant_list_state(
                ctx,
                stop_event,
                payload,
                timeout=float(payload.get("assistant_item_detail_return_timeout") or payload.get("assistant_daoyi_detail_return_timeout") or 10.0),
                label="日常_助手：等待上一条结果页返回小助手清单",
            )
            scene_id, score, frame = runtime.current_scene([204, 205, 208, 69, 34], update=True)
            lines = runtime.ocr_lines(frame)
            text = runtime.ocr_text(frame)
        if not self._daily_assistant_scene_or_text_is_list(scene_id, text):
            raise RuntimeError(
                f"日常_助手：准备点击「{shape_title}」前已不在小助手清单，"
                f"当前 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}"
            )

        parts = self._daily_assistant_item_parts(shape_title)
        assistant_label = parts[0] if parts else str(shape_title).removeprefix("执行-")
        action_label = parts[1] if parts else ""
        floating_point = None
        has_floating_template = False
        fixed_first_screen_item = self._daily_assistant_is_first_screen_fixed_item(shape_title)
        if parts is not None and not fixed_first_screen_item:
            parent_shape = self._find_shape(image204, parts[0])
            has_floating_template = (
                (
                    parent_shape is not None
                    and self._daily_assistant_child_shape(parent_shape, parts[1]) is not None
                )
                or self._daily_assistant_row_template_shape(image204, parts[1]) is not None
            )
        if parts is not None and has_floating_template:
            floating_point = self._daily_assistant_floating_action_point(image204, lines, parts[0], parts[1])
            max_scrolls = int(payload.get("assistant_item_max_scrolls") or payload.get("assistant_max_scrolls") or 6)
            scroll_index = 0
            while floating_point is None and scroll_index < max_scrolls:
                if payload.get("_daily_assistant_list_bottom_reached"):
                    with self._lock:
                        self._log_locked("action", f"日常_助手：小助手清单已在底部，跳过向下查找「{parts[0]}」")
                    break
                with self._lock:
                    self._log_locked("action", f"日常_助手：当前屏未找到「{parts[0]}」，向下滚动查找 {scroll_index + 1}/{max_scrolls}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image204, direction="down")
                if not changed:
                    payload["_daily_assistant_list_bottom_reached"] = True
                    break
                yield BehaviorTreeStatus.RUNNING
                frame = runtime.cur_frame(update=True)
                lines = runtime.ocr_lines(frame)
                text = runtime.ocr_text(frame)
                if not self._daily_assistant_scene_or_text_is_list(scene_id, text):
                    raise RuntimeError(f"日常_助手：滚动查找「{parts[0]}」后已不在小助手清单，OCR={text[:120]}")
                floating_point = self._daily_assistant_floating_action_point(image204, lines, parts[0], parts[1])
                scroll_index += 1
            if floating_point is None:
                with self._lock:
                    self._log_locked("action", f"日常_助手：未找到可见的「{parts[0]}」条目，跳过「{shape_title}」")
                return "not_visible"

        shape = None if floating_point is not None else self._find_shape(image204, shape_title)
        fixed_child_point: tuple[float, float] | None = None
        if shape is None and floating_point is None and fixed_first_screen_item and parts is not None:
            parent_shape = self._find_shape(image204, parts[0])
            child_shape = self._daily_assistant_child_shape(parent_shape, parts[1])
            if child_shape is not None:
                fixed_child_point = ActionPlanner().shape_center(image204, child_shape)
        if shape is None and floating_point is None and fixed_child_point is None:
            raise RuntimeError(f"日常_助手：#204 缺少「{shape_title}」标注，无法执行助手闭环")
        if floating_point is None and fixed_child_point is None and assistant_label and assistant_label != "道义秘库助手" and not fixed_first_screen_item:
            compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
            if assistant_label not in compact:
                with self._lock:
                    self._log_locked("action", f"日常_助手：当前 OCR 未确认「{assistant_label}」，跳过「{shape_title}」")
                return "not_visible"

        if floating_point is not None:
            x, y, matched_text = floating_point
        else:
            matched_text = assistant_label
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_助手：点击「{shape_title}」",
                phase="daily_assistant_item_click",
                current_scene=204,
            )
            if floating_point is not None:
                self._log_locked("action", f"日常_助手：OCR 命中「{matched_text}」，点击 #204「{assistant_label}/{action_label}」")
            else:
                self._log_locked("action", f"日常_助手：点击 #204「{shape_title}」")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        if floating_point is not None:
            runtime.click_frame_point(204, x, y)
        elif fixed_child_point is not None:
            runtime.click_frame_point(204, fixed_child_point[0], fixed_child_point[1])
        else:
            yield from runtime.wait_click(204, shape_title)
        result = yield from self._wait_daily_assistant_item_result(ctx, stop_event, payload, assistant_label or shape_title, action_label or "执行")
        if fixed_first_screen_item and str(result) == "no_popup":
            return "fixed_clicked"
        return result

    def _wait_daily_assistant_daoyi_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        return (yield from self._wait_daily_assistant_item_result(ctx, stop_event, payload, "道义秘库助手", "执行"))

    def _daily_assistant_should_capture_transient_feedback(self, assistant_label: str, action_label: str, payload: dict[str, Any]) -> bool:
        raw = payload.get("assistant_capture_transient_feedback")
        if raw is not None:
            return str(raw).strip().lower() not in {"0", "false", "no", "off"}
        label = str(assistant_label or "")
        action = str(action_label or "")
        return ("仙府资源" in label and action == "领取") or ("弟子授业" in label and action == "执行")

    def _daily_assistant_result_scene_id(self, scene_id: int | None, text: str) -> int | None:
        if scene_id in {205, 209, 263}:
            return int(scene_id)
        if scene_id in {204, 208}:
            return None
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if not compact:
            return None
        if scene_id in {34, 69} and "点击屏幕继续" not in compact:
            return None
        if "授业结果" in compact or ("弟子评分" in compact and "消耗" in compact):
            return 209
        if "神物园" in compact and ("获得神物园" in compact or "神物园产出效率" in compact or "神物园效率加成" in compact):
            return 205
        if "点击屏幕继续" in compact and (
            "恭喜获得" in compact
            or "活动积分增加" in compact
            or "获得积分效率加成" in compact
            or "获得额外" in compact
        ):
            return 205
        if "点击屏幕继续" in compact and (
            "执行详情" in compact
            or "收益" in compact
            or "攻击" in compact
            or "气血" in compact
            or "修为" in compact
            or "服用丹药" in compact
            or "属性翻倍" in compact
            or "宗门任务完成" in compact
            or "祈福完成" in compact
        ):
            return 205
        if "服用丹药" in compact and ("属性翻" in compact or "属性翻倍" in compact):
            return 263
        if "灵力" in compact and "气血" in compact and ("+" in compact or "＋" in compact):
            return 205
        return None

    def _daily_assistant_result_continue_image(self, images: dict[int, dict[str, Any]], result_scene_id: int) -> dict[str, Any] | None:
        image = images.get(result_scene_id)
        if result_scene_id == 263 and (not isinstance(image, dict) or self._find_shape(image, "点击屏幕继续") is None):
            return images.get(205)
        return image

    def _save_daily_assistant_transient_feedback_frame(
        self,
        frame_data_url: str,
        *,
        assistant_label: str,
        action_label: str,
    ) -> Path | None:
        if not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image"):
            return None
        try:
            png_data = self._decode_frame_data_url(frame_data_url)
            output_dir = codeyun_temp_root("fanxiu_daily_assistant_feedback")
            safe_label = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", f"{assistant_label}_{action_label}").strip("_") or "assistant_feedback"
            path = output_dir / f"{_runtime_runner._now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_label}.png"
            path.write_bytes(png_data)
            return path
        except Exception as exc:
            self._log("detail", f"日常_助手：短暂反馈截图保存失败：{exc}")
            return None

    def _daily_assistant_is_tongyou_confirm_text(self, assistant_label: str, action_label: str, text: str) -> bool:
        label = str(assistant_label or "")
        action = str(action_label or "")
        if "同游传道" not in label or action != "执行":
            return False
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text or ""))
        if self._daily_assistant_is_tongyou_cancel_text(assistant_label, action_label, text):
            return False
        return (
            "一键同游" in compact
            and "同游传道" in compact
            and ("取消" in compact or "确认" in compact or "体力不足" in compact or "达到上限" in compact)
        )

    def _daily_assistant_is_tongyou_cancel_text(self, assistant_label: str, action_label: str, text: str) -> bool:
        label = str(assistant_label or "")
        action = str(action_label or "")
        if "同游传道" not in label or action != "执行":
            return False
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text or ""))
        return (
            "弟子" in compact
            and (
                "已满" in compact
                or "已达上限" in compact
                or "已达到上限" in compact
                or "数量已达上限" in compact
                or "数量已达到上限" in compact
                or "无法获得弟子" in compact
                or "仅能获得同游奖励" in compact
            )
            and ("取消" in compact or "确认" in compact)
        )

    def _daily_assistant_is_tongyou_result_text(self, assistant_label: str, action_label: str, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text or ""))
        return "同游结果" in compact and "确定" in compact

    def _daily_assistant_is_tongyou_new_disciple_text(self, assistant_label: str, action_label: str, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text or ""))
        return "喜纳弟子" in compact and "点击空白处关闭" in compact

    def _wait_daily_assistant_tongyou_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        assistant_label: str,
        action_label: str,
    ):
        timeout = float(payload.get("assistant_tongyou_result_timeout_seconds") or 10.0)
        poll_seconds = float(payload.get("assistant_item_poll_seconds") or payload.get("assistant_daoyi_poll_seconds") or 0.35)
        start = time.monotonic()
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image211 = images.get(211)
        image212 = images.get(212)
        closed_result = False
        saw_list_after_confirm = False
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene([212, 211, 263, 205, 209, 204, 210, 208, 69, 34], update=True)
            text = runtime.ocr_text(frame)
            result_scene_id = self._daily_assistant_result_scene_id(scene_id, text)
            if result_scene_id is not None:
                result_image = self._daily_assistant_result_continue_image(images, result_scene_id)
                if not isinstance(result_image, dict):
                    raise RuntimeError(f"日常_助手：同游传道后进入 #{result_scene_id} 结果页，但缺少可关闭资产标注")
                continue_shape = self._find_shape(result_image, "点击屏幕继续")
                if continue_shape is None:
                    raise RuntimeError(f"日常_助手：同游传道后 #{result_scene_id} 结果页缺少「点击屏幕继续」标注")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：关闭同游传道后 #{result_scene_id} 结果页",
                        phase="daily_assistant_tongyou_close_result",
                        current_scene=result_scene_id,
                    )
                    self._log_locked("action", f"日常_助手：点击 #{result_scene_id}「点击屏幕继续」")
                runtime.click_shape_center(result_image, "点击屏幕继续")
                yield from self._ensure_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=timeout,
                    label="日常_助手：等待同游传道结果页返回小助手清单",
                )
                return "result_closed"
            if scene_id == 211 or self._daily_assistant_is_tongyou_result_text(assistant_label, action_label, text):
                if not isinstance(image211, dict):
                    raise RuntimeError("日常_助手：已进入 #211 同游结果弹窗，但缺少 #211 资产标注")
                confirm_shape = self._find_shape(image211, "确定")
                if confirm_shape is None:
                    raise RuntimeError("日常_助手：#211 缺少「确定」标注，无法关闭同游结果")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：关闭同游传道结果",
                        phase="daily_assistant_tongyou_result_confirm",
                        current_scene=211,
                    )
                    self._log_locked("action", "日常_助手：点击 #211「确定」")
                runtime.click_shape_center(image211, "确定")
                closed_result = True
                yield from runtime.wait_action_settle(poll_seconds)
                start = time.monotonic()
                continue
            if scene_id == 212 or self._daily_assistant_is_tongyou_new_disciple_text(assistant_label, action_label, text):
                if not isinstance(image212, dict):
                    raise RuntimeError("日常_助手：已进入 #212 喜纳弟子弹窗，但缺少 #212 资产标注")
                close_shape = self._find_shape(image212, "空白关闭")
                if close_shape is None:
                    raise RuntimeError("日常_助手：#212 缺少「空白关闭」标注，无法关闭喜纳弟子弹窗")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：关闭同游传道喜纳弟子弹窗",
                        phase="daily_assistant_tongyou_new_disciple_close",
                        current_scene=212,
                    )
                    self._log_locked("action", "日常_助手：点击 #212「空白关闭」")
                runtime.click_shape_center(image212, "空白关闭")
                yield from self._wait_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    timeout=timeout,
                    label="日常_助手：等待喜纳弟子返回小助手清单",
                )
                return "result_closed"
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                if closed_result:
                    return "result_closed"
                saw_list_after_confirm = True
            if time.monotonic() - start >= timeout:
                if saw_list_after_confirm:
                    return "confirmed_no_result"
                raise TimeoutError(
                    f"日常_助手：同游传道确认后等待结果超时，最后 {'#' + str(scene_id) if scene_id else 'unknown'} "
                    f"{score:.0f}%，OCR={text[:160]}"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待同游传道结果 {time.monotonic() - start:.1f}/{timeout:.1f}s，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_tongyou_wait_result",
                    current_scene=scene_id,
                )
            yield from runtime.wait_action_settle(poll_seconds)

    def _wait_daily_assistant_item_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        assistant_label: str,
        action_label: str = "执行",
    ):
        timeout = float(
            payload.get("assistant_item_wait_seconds")
            or payload.get("assistant_daoyi_wait_seconds")
            or payload.get("assistant_execute_wait_seconds")
            or payload.get("assistant_no_popup_confirm_seconds")
            or 10.0
        )
        detail_timeout = float(payload.get("assistant_item_detail_return_timeout") or payload.get("assistant_daoyi_detail_return_timeout") or 10.0)
        poll_seconds = float(payload.get("assistant_item_poll_seconds") or payload.get("assistant_daoyi_poll_seconds") or 0.35)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        saw_list = False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image205 = images.get(205)
        image209 = images.get(209)
        image210 = images.get(210)
        image213 = images.get(213)
        image214 = images.get(214)
        capture_feedback = self._daily_assistant_should_capture_transient_feedback(assistant_label, action_label, payload)
        feedback_capture_seconds = float(payload.get("assistant_transient_feedback_capture_seconds") or 1.2)
        capture_no_popup_seconds = float(payload.get("assistant_capture_no_popup_seconds") or 6.0)
        if capture_feedback:
            timeout = max(timeout, capture_no_popup_seconds + poll_seconds)
        feedback_saved = False
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        pending_scene: tuple[int | None, float, str] | None = None
        fast_list_probe = str(payload.get("assistant_fast_list_probe", "0")).strip().lower() not in {"0", "false", "no", "off"}
        if ("神物园" in str(assistant_label or "") or "弟子授业" in str(assistant_label or "")) and str(action_label or "") == "执行":
            fast_list_probe = False
        if fast_list_probe and not capture_feedback:
            yield from runtime.wait_action_settle(float(payload.get("assistant_fast_list_probe_settle_seconds") or 1.0))
            scene_id, score, frame = runtime.current_scene([209, 205], update=True)
            if scene_id in {209, 205}:
                pending_scene = (scene_id, score, frame)
            else:
                scene_id, score, frame = runtime.current_scene([204], frame_data_url=frame)
            if pending_scene is None and scene_id == 204:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：{assistant_label} {action_label or '执行'} 后仍在 #204，按未触发快速收口",
                        phase="daily_assistant_item_no_popup",
                        current_scene=204,
                    )
                    self._log_locked(
                        "action",
                        f"日常_助手：{assistant_label} {action_label or '执行'} 后仍在 #204 {score:.0f}%，跳过弹窗 OCR 等待",
                    )
                return "no_popup"
            if pending_scene is None:
                pending_scene = (scene_id, score, frame)

        while True:
            self._raise_if_stopped(stop_event)
            if pending_scene is not None:
                scene_id, score, frame = pending_scene
                pending_scene = None
            else:
                scene_id, score, frame = runtime.current_scene([214, 213, 210, 208, 263, 209, 205, 204, 69, 34], update=True)
            elapsed = time.monotonic() - start
            if capture_feedback and not feedback_saved and elapsed <= feedback_capture_seconds:
                feedback_saved = True
                feedback_path = self._save_daily_assistant_transient_feedback_frame(
                    frame,
                    assistant_label=assistant_label,
                    action_label=action_label or "执行",
                )
                if feedback_path is not None:
                    with self._lock:
                        self._log_locked("detail", f"日常_助手：已保存 {assistant_label} {action_label or '执行'} 后短暂反馈候选帧：{feedback_path}")
            last_scene_id, last_score = scene_id, score
            text = runtime.ocr_text(frame)
            last_text = text or last_text
            compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))

            tongyou_should_confirm = scene_id == 210 or self._daily_assistant_is_tongyou_confirm_text(assistant_label, action_label, text)
            tongyou_should_cancel = scene_id == 213 or self._daily_assistant_is_tongyou_cancel_text(assistant_label, action_label, text)
            if scene_id in {210, 213} or tongyou_should_confirm or tongyou_should_cancel:
                if not tongyou_should_confirm and not tongyou_should_cancel:
                    raise RuntimeError(
                        f"日常_助手：遇到同游传道提示弹窗，但未能区分 #210/#213，"
                        f"assistant={assistant_label}/{action_label}，OCR={text[:160]}"
                    )
                action_image = image213 if tongyou_should_cancel else image210
                if not isinstance(action_image, dict):
                    raise RuntimeError("日常_助手：已进入同游传道确认弹窗，但缺少对应资产标注")
                action_shape_title = "取消" if tongyou_should_cancel else "确认"
                action_shape = self._find_shape(action_image, action_shape_title)
                if action_shape is None:
                    raise RuntimeError(f"日常_助手：同游传道确认弹窗缺少「{action_shape_title}」标注，无法完成闭环")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：取消同游传道助手风险提示" if tongyou_should_cancel else "日常_助手：确认同游传道助手一键同游",
                        phase="daily_assistant_tongyou_confirm",
                        current_scene=213 if tongyou_should_cancel else 210,
                    )
                    scene_label = "#213" if tongyou_should_cancel else "#210"
                    self._log_locked("action", f"日常_助手：点击 {scene_label}「{action_shape_title}」")
                runtime.click_shape_center(action_image, action_shape_title)
                yield from runtime.wait_action_settle(float(payload.get("assistant_tongyou_confirm_settle_seconds") or 2.0))
                if tongyou_should_cancel:
                    yield from runtime.wait_view(204, timeout=detail_timeout, label="日常_助手：等待同游风险提示取消后回到小助手清单")
                    return "cancelled"
                return (yield from self._wait_daily_assistant_tongyou_result(
                    ctx,
                    stop_event,
                    payload,
                    assistant_label,
                    action_label,
                ))

            no_action_text = ""
            if scene_id == 208 or "当前没有可执行的事项" in compact:
                no_action_text = "当前没有可执行的事项"
            elif "当前没有可授业的弟子" in compact:
                no_action_text = "当前没有可授业的弟子"
            if no_action_text:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：{assistant_label} {no_action_text}",
                        phase="daily_assistant_item_no_action",
                        current_scene=204,
                    )
                    self._log_locked("action", f"日常_助手：识别到「{no_action_text}」")
                yield from runtime.wait_action_settle(float(payload.get("assistant_no_action_settle_seconds") or 1.0))
                return "no_action"

            zongmen_info_page = (
                "宗门助手" in str(assistant_label or "")
                and "功能" in compact
                and "职位" in compact
                and "宗门俸禄" in compact
                and "宗门祈福" in compact
                and "道藏阁" in compact
            )
            if zongmen_info_page:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：宗门助手进入功能说明页，点击返回",
                        phase="daily_assistant_zongmen_info_return",
                        current_scene=scene_id,
                    )
                self._log_locked("action", "日常_助手：宗门助手进入功能说明页，点击左下返回")
                runtime.click_frame_point({"width": 900, "height": 1600}, 45, 1510)
                yield from runtime.wait_view(204, timeout=detail_timeout, label="日常_助手：等待宗门功能页返回小助手清单")
                return "no_action"

            result_scene_id = self._daily_assistant_result_scene_id(scene_id, text)
            if result_scene_id is not None:
                result_image = image209 if result_scene_id == 209 else image205
                if not isinstance(result_image, dict):
                    raise RuntimeError(f"日常_助手：已进入 #{result_scene_id} 小助手执行结果，但缺少 #{result_scene_id} 资产标注")
                continue_shape = self._find_shape(result_image, "点击屏幕继续")
                if continue_shape is None:
                    raise RuntimeError(f"日常_助手：#{result_scene_id} 缺少「点击屏幕继续」标注，无法回到 #204")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：关闭 {assistant_label} 执行结果",
                        phase="daily_assistant_item_close_detail",
                        current_scene=result_scene_id,
                    )
                    self._log_locked("action", f"日常_助手：点击 #{result_scene_id}「点击屏幕继续」")
                runtime.click_shape_center(result_image, "点击屏幕继续")
                yield from self._ensure_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=detail_timeout,
                    label="日常_助手：等待回到小助手清单",
                )
                return "detail_closed"

            if scene_id == 214:
                if "弟子教学" not in str(assistant_label or "") or str(action_label or "") != "前往":
                    raise RuntimeError(f"日常_助手：遇到 #214 指教完成弹窗，但当前执行项不是弟子教学前往：{assistant_label}/{action_label}")
                yield from self._close_daily_assistant_teaching_complete(ctx, stop_event, timeout=detail_timeout)
                return "teaching_complete_closed"

            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                saw_list = True
                if capture_feedback and elapsed >= capture_no_popup_seconds:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_助手：{assistant_label} {action_label or '执行'} 后仍在 #204，按未触发快速收口",
                            phase="daily_assistant_item_no_popup",
                            current_scene=204,
                        )
                        self._log_locked("action", f"日常_助手：{assistant_label} {action_label or '执行'} 后仍在 #204 {score:.0f}%，跳过继续等待")
                    return "no_popup"
            elif scene_id == 69:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：{assistant_label} {action_label or '执行'} 后回到日常页，按该条目已收口",
                        phase="daily_assistant_item_returned_daily",
                        current_scene=69,
                    )
                    self._log_locked("action", f"日常_助手：{assistant_label} {action_label or '执行'} 后回到 #69，准备重新进入小助手清单")
                return "returned_daily"
            elif scene_id == 34:
                raise RuntimeError(
                    f"日常_助手：点击 {assistant_label} 执行后离开小助手清单，"
                    f"当前 #{scene_id} {score:.0f}%，OCR={text[:120]}"
                )

            if elapsed >= timeout:
                if saw_list:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_助手：{assistant_label} {action_label or '执行'} 后 {timeout:.0f} 秒内未出现结果弹窗，按未触发收口",
                            phase="daily_assistant_item_no_popup",
                            current_scene=204,
                        )
                        self._log_locked("action", f"日常_助手：{assistant_label} {action_label or '执行'} 等待超时，仍在 #204，按本轮未触发处理")
                    return "no_popup"
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_助手：{assistant_label} 执行等待超时，未确认 #205/#208/#204，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}"
                )

            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待 {assistant_label} 执行结果 {elapsed:.1f}/{timeout:.1f}s，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_item_wait_result",
                    current_scene=scene_id,
                )
            yield from runtime.wait_action_settle(poll_seconds)

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
        image34 = images.get(34)
        image69 = images.get(69)

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([214, 209, 205, 204, 123, 122, 121, 86, 69, 34], update=True)
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
            scene_id, _score, frame = runtime.current_scene([214, 263, 209, 205, 204, 123, 122, 121, 69, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id in {121, 122, 123}:
            yield from self._leave_mail_scene_to_world(ctx, stop_event, runtime, scene_id, label="日常_助手")
            scene_id, _score, frame = runtime.current_scene([214, 263, 209, 205, 204, 69, 34], update=True)
            text = runtime.ocr_text(frame)
        result_scene_id = self._daily_assistant_result_scene_id(scene_id, text)
        if result_scene_id is not None:
            result_image = self._daily_assistant_result_continue_image(images, result_scene_id)
            if not isinstance(result_image, dict):
                raise RuntimeError(f"日常_助手：当前在 #{result_scene_id} 结果页，但缺少 #{result_scene_id} 资产标注")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：启动时关闭 #{result_scene_id} 执行结果",
                    phase="daily_assistant_start_close_result",
                    current_scene=result_scene_id,
                )
                self._log_locked("action", f"日常_助手：启动时点击 #{result_scene_id}「点击屏幕继续」")
            runtime.click_shape_center(result_image, "点击屏幕继续")
            yield from self._ensure_daily_assistant_list_state(
                ctx,
                stop_event,
                payload,
                timeout=float(payload.get("assistant_item_detail_return_timeout") or 10.0),
                label="日常_助手：等待结果页返回小助手清单",
            )
            return (yield from self._run_daily_assistant_from_list(ctx, stop_event, payload))
        if scene_id == 214:
            yield from self._close_daily_assistant_teaching_complete(ctx, stop_event)
            return (yield from self._run_daily_assistant_from_list(ctx, stop_event, payload))
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
        raise RuntimeError(f"日常_助手：入口点击后回到 #{scene_id or 'unknown'}，尚未进入小助手清单，不能按完成处理")

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
            self._log_locked("action", f"{label}：点击 #{scene_id}「空白-返回」恢复到世界")
        yield from runtime.wait_click(scene_id, "空白-返回")
        yield from runtime.wait_view(34, label=f"{label}：等待返回世界 #34")

    def _close_daily_assistant_teaching_complete(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float = 10.0,
    ):
        image214 = ctx.get("images", {}).get(214)
        if not isinstance(image214, dict):
            raise RuntimeError("日常_助手：已进入 #214 指教完成弹窗，但缺少 #214 资产标注")
        continue_shape = self._find_shape(image214, "继续")
        if continue_shape is None:
            raise RuntimeError("日常_助手：#214 缺少「继续」标注，无法回到 #204")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_助手：关闭弟子教学指教完成弹窗",
                phase="daily_assistant_teaching_complete_continue",
                current_scene=214,
            )
        self._log_locked("action", "日常_助手：点击 #214「继续」")
        yield from runtime.wait_click(214, "继续")
        yield from runtime.wait_view(204, timeout=timeout, label="日常_助手：等待指教完成返回小助手清单")
        return "success"

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
