from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number
from pyxllib.prog import BehaviorTreeStatus

from backend.core.fanxiu.prayer_cycle import PRAYER_CYCLE_NAMES, PRAYER_CYCLE_TIMEZONE, current_prayer_cycle, next_prayer_cycle
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import find_text_matches
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
)


class DailyResourceTaskMixin:
    _daily_default_wait_condition_timeout = 12.0

    def daily_xianmeng_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = job_now()
        end_text = str(payload.get("daily_end_time") or "22:00")
        try:
            end_clock = datetime.strptime(end_text, "%H:%M").time()
        except ValueError:
            end_clock = datetime.strptime("22:00", "%H:%M").time()
        close_at = now.replace(
            hour=end_clock.hour,
            minute=end_clock.minute,
            second=end_clock.second,
            microsecond=0,
        )
        if now < close_at:
            return None
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": f"仙盟_挑战：当前已到或超过 {end_text}，活动窗口结束，未执行游戏操作",
            "next_time": None,
            "current_scene": None,
        })

    def xianshi_weekly_resources_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        if self._xianshi_weekly_resources_phase(payload) != "skip":
            return None
        now = datetime.now(PRAYER_CYCLE_TIMEZONE)
        days_until_monday = (7 - now.weekday()) % 7 or 7
        next_time = (now + timedelta(days=days_until_monday)).replace(
            hour=0,
            minute=4,
            second=0,
            microsecond=0,
        )
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "仙市_每周资源：当前不是周一领取日，未执行游戏操作",
            "next_time": next_time.strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    def _execute_daily_gongfeng_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="daily_gongfeng",
            label="日常_供奉",
            flow=self.日常供奉流程,
        )

    def 日常供奉流程(self, runtime: Any):
        task_label = "日常_供奉"
        ctx = runtime.ctx
        stop_event = runtime.stop_event or threading.Event()
        payload = runtime.payload

        image252 = runtime.view(252).raw
        image254 = runtime.view(254).raw
        image255 = runtime.view(255).raw
        image256 = runtime.view(256).raw
        image257 = runtime.view(257).raw

        scene_id, _score, frame = runtime.current_scene([34, 47, 251, 252, 255, 256, 257], update=True)
        accepted = 0
        upgraded = 0
        if scene_id == 255:
            yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255, runtime)
            scene_id = 254
        if scene_id == 47:
            yield from runtime.wait_click(47, "空白")
            scene_id, _score, frame = runtime.current_scene([34, 251, 252, 255, 256, 257], update=True)
        if scene_id == 257:
            yield from runtime.wait_click(257, "空白")
            yield from runtime.wait_action_settle(1.2)
            scene_id = 252
        if scene_id == 256:
            yield from runtime.wait_click(256, "返回")
            scene_id, _score, frame = runtime.current_scene([34, 252], update=True)

        if self._daily_gongfeng_upgrade_page_visible(runtime, frame):
            upgraded = yield from self._upgrade_daily_gongfeng_until_insufficient(ctx, stop_event, payload, image254, image255, runtime)
            yield from self._close_daily_gongfeng_upgrade_pages(ctx, stop_event, payload, image254, image256, runtime)
        else:
            if scene_id is None:
                yield from runtime.goto_view(34)
                scene_id = 34
            if scene_id == 34:
                yield from runtime.wait_click(34, "主线")
                scene_id = 251
            if scene_id == 251:
                yield from runtime.wait_click(251, "供奉")
                scene_id = 252
            if scene_id == 252:
                accepted = yield from self._accept_daily_gongfeng_until_done(ctx, stop_event, payload, image252, runtime)
                yield from self._claim_daily_gongfeng_extra_reward(ctx, stop_event, payload, image252, image257, runtime)
                upgraded = yield from self._upgrade_daily_gongfeng_law(ctx, stop_event, payload, image252, image254, image255, image256, runtime)
        yield from runtime.goto_view(34)
        runtime.set_next_time(self._next_daily_boss_reset_time_text())
        runtime.set_completion_message(f"{task_label}完成，接受 {accepted} 次，升级 {upgraded} 次，已回到世界")

    def _accept_daily_gongfeng_until_done(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        runtime: BehaviorTreeRuntime,
    ):
        accepted = 0
        max_accept = max(0, int(payload.get("max_accept") or 20))
        max_read = max(1, int(payload.get("gongfeng_count_read_retries") or 8))
        for index in range(max_accept):
            self._raise_if_stopped(stop_event)
            yield from runtime.wait_view(252, label="日常_供奉：等待供奉页 #252")
            remaining = None
            last_text = ""
            for read_index in range(max_read):
                numbers, last_text = runtime.ocr_numbers_in_shapes(252, ("次数",), padding=16)
                self._log("detail", f"日常_供奉：读取次数 {index + 1}.{read_index + 1} OCR={last_text} nums={numbers}")
                if numbers:
                    remaining = numbers[0]
                    break
                yield from runtime.wait_action_settle(0.8)
            if remaining is None:
                raise RuntimeError(f"日常_供奉：#252「次数」未识别到整数，OCR={last_text[:120]}")
            if remaining <= 0:
                self._log("success", f"日常_供奉：供奉次数已为 0，接受 {accepted} 次")
                return accepted
            with self._lock:
                self._set_status_locked("running", f"日常_供奉：接受供奉 {accepted + 1}", phase="daily_gongfeng_accept")
                self._log_locked("action", "日常_供奉：点击 #252「接受供奉」")
            yield from runtime.wait_click(252, "接受供奉")
            accepted += 1
            yield from runtime.wait_action_settle(2.0)
        raise RuntimeError(f"日常_供奉：达到最大接受次数 {max_accept} 仍未到 0，已暂停")

    def _claim_daily_gongfeng_extra_reward(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        image257: dict[str, Any],
        runtime: BehaviorTreeRuntime,
    ):
        shape = self._find_shape(image252, "额外奖励")
        if shape is None:
            raise RuntimeError("日常_供奉：缺少 #252「额外奖励」标注")
        yield from runtime.wait_view(252, label="日常_供奉：等待供奉页 #252")
        with self._lock:
            self._set_status_locked("running", "日常_供奉：点击 #252「额外奖励」", phase="daily_gongfeng_extra_reward", current_scene=252)
            self._log_locked("action", "日常_供奉：点击 #252「额外奖励」")
        yield from runtime.wait_click(252, "额外奖励")
        yield from runtime.wait_action_settle(float(payload.get("gongfeng_extra_settle_seconds") or 2.0))
        result = yield from runtime.wait_any(
            {
                "详情": runtime.view_visible(257),
                "供奉页": runtime.view_visible(252),
                "空白弹层": runtime.view_visible(47),
            },
            label="日常_供奉：等待额外奖励结果",
        )
        if result == "详情":
            yield from runtime.wait_click(257, "空白")
            yield from runtime.wait_view(252, label="日常_供奉：等待回到 #252")
            return "closed_257"
        if result == "空白弹层":
            yield from runtime.wait_click(47, "空白")
            yield from runtime.wait_view(252, label="日常_供奉：等待回到 #252")
            return "closed_47"
        self._log("success", "日常_供奉：额外奖励已领取或未弹出详情")
        return "no_popup"

    def _upgrade_daily_gongfeng_law(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        image254: dict[str, Any],
        image255: dict[str, Any],
        image256: dict[str, Any],
        runtime: BehaviorTreeRuntime,
    ) -> int:
        yield from runtime.wait_view(252, label="日常_供奉：等待供奉页 #252")
        yield from runtime.wait_click(252, "升级法则")
        yield from self._wait_daily_gongfeng_upgrade_page(runtime)
        upgraded = yield from self._upgrade_daily_gongfeng_until_insufficient(ctx, stop_event, payload, image254, image255, runtime)
        yield from self._close_daily_gongfeng_upgrade_pages(ctx, stop_event, payload, image254, image256, runtime)
        return upgraded

    def _wait_daily_gongfeng_upgrade_page(self, runtime: Any):
        return (yield from runtime.wait_any(
            {
                "升级页": runtime.ocr_contains(
                    all_of=("技能描述",),
                    any_of=("升级", "时间道蕴", "仙法"),
                    label="日常_供奉：#254 升级页 OCR",
                ),
            },
            label="日常_供奉：等待 #254 升级页",
        ))

    def _daily_gongfeng_upgrade_page_visible(
        self,
        runtime: Any,
        frame: str | None = None,
    ) -> bool:
        try:
            score = runtime.shape_score(254, "升级", frame_data_url=frame)
        except Exception:
            return False
        return score >= float(self.overlay_threshold)

    def _upgrade_daily_gongfeng_until_insufficient(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image254: dict[str, Any],
        image255: dict[str, Any],
        runtime: Any,
    ) -> int:
        upgraded = 0
        max_upgrade = max(0, int(payload.get("max_upgrade") or 50))
        read_retries = max(1, int(payload.get("gongfeng_law_read_retries") or 12))
        for loop_index in range(max_upgrade + 1):
            self._raise_if_stopped(stop_event)
            yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255, runtime)
            parsed: tuple[int, int] | None = None
            last_text = ""
            for read_index in range(read_retries):
                self._raise_if_stopped(stop_event)
                try:
                    yield from self._wait_daily_gongfeng_upgrade_page(runtime)
                except TimeoutError:
                    scene_id, score, _frame = runtime.current_scene([34, 252, 256], update=True)
                    if scene_id in {34, 252, 256}:
                        self._log("success", f"日常_供奉：升级后已离开 #254，到达 #{scene_id} {score:.0f}%，升级 {upgraded} 次")
                        return upgraded
                    raise
                nums, last_text = runtime.ocr_numbers_in_shapes(254, ("数值",), padding=26)
                self._log("detail", f"日常_供奉：读取法则数值 {loop_index + 1}.{read_index + 1} OCR={last_text} nums={nums}")
                parsed = self._parse_daily_gongfeng_law_progress(last_text)
                if parsed is not None:
                    break
                if (yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255, runtime)):
                    continue
                yield from runtime.wait_action_settle(0.8)
            if parsed is None:
                raise RuntimeError(f"日常_供奉：#254「数值」未识别到两个整数，OCR={last_text[:120]}")
            current, required = parsed
            if current < required:
                self._log("success", f"日常_供奉：法则资源不足 {current}/{required}，升级 {upgraded} 次")
                return upgraded
            with self._lock:
                self._set_status_locked("running", f"日常_供奉：升级法则 {upgraded + 1}，{current}/{required}", phase="daily_gongfeng_upgrade_law", current_scene=254)
            runtime.click_shape_center(254, "升级")
            upgraded += 1
            yield from runtime.wait_action_settle(1.5)
        raise RuntimeError(f"日常_供奉：升级超过 {max_upgrade} 次仍未到资源不足，已暂停")

    def _parse_daily_gongfeng_law_progress(self, text: Any) -> tuple[int, int] | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("：", ":").replace("O", "0").replace("o", "0")
        fraction = parse_ocr_values(normalized, expected_count=2, allow_extra_numbers=True)
        if fraction is None:
            return None
        current, required = fraction
        current_text, required_text = str(current), str(required)
        if required > 0 and current > required and len(current_text) > len(required_text):
            if current_text.startswith(required_text):
                current = int(current_text[len(required_text):].lstrip("0") or "0")
            elif len(current_text) >= len(required_text) * 2:
                suffix = int(current_text[-len(required_text):])
                if suffix <= required:
                    current = suffix
        return (current, required) if required > 0 else None

    def _close_daily_gongfeng_item_detail_if_present(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image255: dict[str, Any],
        runtime: Any,
    ):
        del ctx, stop_event, image255
        scene_id, _score, _frame = runtime.current_scene([255], update=True)
        if scene_id != 255:
            return False
        self._log("action", "日常_供奉：关闭 #255 物品详情")
        yield from runtime.wait_click(255, "空白")
        yield from runtime.wait_action_settle(1.2)
        return True

    def _close_daily_gongfeng_upgrade_pages(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image254: dict[str, Any],
        image256: dict[str, Any],
        runtime: BehaviorTreeRuntime,
    ):
        del image254
        scene_id, score, _frame = runtime.current_scene([34, 252, 254, 256], update=True)
        if scene_id in {34, 252}:
            self._log("success", f"日常_供奉：已在收尾场景 #{scene_id} {score:.0f}%，无需关闭 #254")
            return "success"
        if scene_id == 256:
            yield from runtime.wait_click(256, "返回")
            yield from runtime.wait_view(252, 34, label="日常_供奉：等待回到 #252/#34")
            return "success"
        try:
            yield from self._wait_daily_gongfeng_upgrade_page(runtime)
        except TimeoutError:
            scene_id, score, _frame = runtime.current_scene([34, 252, 256], update=True)
            if scene_id in {34, 252}:
                self._log("success", f"日常_供奉：未再检测到 #254，当前 #{scene_id} {score:.0f}%，按已收尾处理")
                return "success"
            if scene_id == 256:
                yield from runtime.wait_click(256, "返回")
                yield from runtime.wait_view(252, 34, label="日常_供奉：等待回到 #252/#34")
                return "success"
            raise
        self._log("action", "日常_供奉：点击 #254「空白」")
        yield from runtime.wait_click(254, "空白")
        yield from runtime.wait_action_settle(1.2)
        yield from runtime.wait_click(256, "返回")
        try:
            yield from runtime.wait_view(252, 34, label="日常_供奉：等待回到 #252/#34")
        except Exception as exc:
            self._log("warning", f"日常_供奉：#256 返回后未确认 #252/#34：{exc}")
        return "success"

    def _daily_gongfeng_numbers(self, text: str) -> list[int]:
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        return list(parse_ocr_values(normalized) or ())

    def _daily_gongfeng_text_is_page(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "供奉总览" in normalized and ("供奉奖励" in normalized or "接受供奉" in normalized or "每日登陆可领取奖励" in normalized)

    def _daily_gongfeng_remaining(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(r"今日接受供奉次数[:：]?\s*(\d+)", normalized)
        if not match:
            return None
        raw = match.group(1)
        if len(raw) > 1 and raw.endswith("5"):
            raw = raw[:-1]
        try:
            return int(raw)
        except ValueError:
            return None

    def _open_daily_youli_purchase(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image228: dict[str, Any],
        image229: dict[str, Any],
        image233: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._wait_daily_youli_home(
            ctx,
            stop_event,
            label="日常_游历：等待修仙传游历 #228",
        )
        result = yield from runtime.wait_click_then_any(
            228,
            "购买",
            {
                "purchase": runtime.shape_visible(229, "购买并使用"),
                "empty": runtime.shape_visible(233, "空白"),
                "empty_text": runtime.ocr_matches(
                    self._daily_youli_text_is_purchase_empty,
                    label=f"{task_label}：购买次数不足 OCR",
                    preview_chars=120,
                ),
                "home": runtime.view_visible(228, threshold=95.0),
                "home_text": runtime.ocr_matches(
                    self._daily_youli_text_is_home,
                    label=f"{task_label}：购买后修仙传游历 OCR",
                    preview_chars=120,
                ),
            },
            settle_seconds=float(payload.get("purchase_click_settle_seconds") or 2.0),
            label=f"{task_label}：等待购买体力结果",
        )
        if result in {"empty", "empty_text"}:
            return (yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label))
        if result in {"home", "home_text"}:
            self._log("warning", f"{task_label}：点击购买后仍在 #228，停止购买流程")
            return "success"
        return (yield from self._click_daily_youli_purchase_uses(ctx, stop_event, payload, image229, image233, task_label=task_label))

    def _daily_youli_text_is_purchase(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "购买并使用" in normalized and ("游历符" in normalized or "游歷符" in normalized or "剩余限购次数" in normalized)

    def _daily_youli_purchase_remaining_count(self, text: str) -> int | None:
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

    def _daily_youli_text_is_purchase_empty(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "游历符" in normalized and ("每日限购" in normalized or "增加购买次数" in normalized or "持有数量" in normalized)

    def _daily_youli_text_is_home(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "修仙传" not in normalized:
            return False
        if any(token in normalized for token in ("道祖鸿蒙", "幻境", "供奉", "机缘", "寻找机缘")):
            return False
        return (
            "人界" in normalized
            or "灵界" in normalized
            or "魔界" in normalized
            or "仙界" in normalized
            or "北寒蛮荒" in normalized
            or "探索完成" in normalized
        )

    def _daily_youli_text_is_region_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "探索进度" in normalized and ("快速游历" in normalized or "消耗体力" in normalized):
            return True
        if "背景介绍" in normalized and "挑战奖励" in normalized and "当前模式" in normalized and "今日可挑战次数" in normalized:
            return True
        return False

    def _daily_youli_text_is_region_completed(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        if "已完成所有挑战" in compact and re.search(r"今日可挑战次数[:：]?0[/／]3", compact):
            return True
        return False

    def _daily_youli_text_is_quick_result(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "游历" in normalized and ("总共获得宝物" in normalized or "游历消耗" in normalized) and "确定" in normalized

    def _daily_youli_text_is_reward_recovery(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "奖励找回" in normalized and ("一键免费" in normalized or "一键全部" in normalized or "全部找回" in normalized)

    def _daily_youli_text_is_daily_page(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "日常" in normalized and ("活跃度" in normalized or "活动报名" in normalized or "小助手" in normalized or "奖励找回" in normalized)

    def _wait_daily_youli_home(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float | None = None,
        label: str,
    ):
        return (yield from self._wait_daily_youli_scene_or_text(
            ctx,
            stop_event,
            228,
            self._daily_youli_text_is_home,
            timeout=self._daily_default_wait_condition_timeout if timeout is None else float(timeout),
            label=label,
        ))

    def _wait_daily_youli_region_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float | None = None,
        label: str,
    ):
        return (yield from self._wait_daily_youli_scene_or_text(
            ctx,
            stop_event,
            236,
            self._daily_youli_text_is_region_detail,
            timeout=self._daily_default_wait_condition_timeout if timeout is None else float(timeout),
            label=label,
        ))

    def _wait_daily_youli_quick_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float | None = None,
        label: str,
    ):
        return (yield from self._wait_daily_youli_scene_or_text(
            ctx,
            stop_event,
            237,
            self._daily_youli_text_is_quick_result,
            timeout=self._daily_default_wait_condition_timeout if timeout is None else float(timeout),
            label=label,
        ))

    def _wait_daily_youli_scene_or_text(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        target_scene_id: int,
        text_predicate: Callable[[str], bool],
        *,
        timeout: float,
        label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_threshold = 95.0 if int(target_scene_id) == 228 else 80.0
        _result, scene_id, score = yield from runtime.wait_view_or_ocr(
            target_scene_id,
            text_predicate,
            view_threshold=scene_threshold,
            timeout=timeout,
            label=label,
        )
        if int(target_scene_id) == 228 and _result != "text":
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if not text_predicate(text):
                images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
                image228 = images.get(228)
                if not isinstance(image228, dict):
                    raise RuntimeError(f"{label}：已匹配 #228，但 OCR 未确认游历页，且缺少 #228「菜单」标注")
                yield from self._select_daily_youli_tab_from_menu_if_visible(
                    ctx,
                    stop_event,
                    {},
                    image228,
                    task_label="日常_游历",
                )
                yield from runtime.wait_any(
                    {
                        "text": runtime.ocr_matches(
                            text_predicate,
                            label=f"{label} OCR确认",
                            preview_chars=120,
                        )
                    },
                    timeout=timeout,
                    label=f"{label}：确认游历菜单已选中",
                )
                scene_id, score, _frame = runtime.current_scene([target_scene_id], update=True)
        self._log("success", f"{label}：已到达 #{target_scene_id} {score:.0f}%")
        return scene_id, score

    def _close_daily_youli_purchase_empty(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image233: dict[str, Any],
        *,
        task_label: str,
    ):
        del image233
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from runtime.wait_click(233, "空白", label=f"{task_label}：关闭购买次数不足提示")
        yield from runtime.wait_action_settle(1.0)
        self._log("success", f"{task_label}：已关闭购买次数不足提示")
        return "success"

    def _close_daily_youli_purchase_empty_and_wait_home(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image233: dict[str, Any],
        *,
        task_label: str,
    ):
        yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label)
        yield from self._wait_daily_youli_home(
            ctx,
            stop_event,
            timeout=18.0,
            label=f"{task_label}：等待购买次数不足关闭后回到 #228",
        )
        return "success"

    def _close_daily_youli_purchase_dialog(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image229: dict[str, Any],
        *,
        task_label: str,
    ):
        del image229
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from runtime.wait_click(229, "空白", label=f"{task_label}：关闭购买体力弹窗")
        yield from runtime.wait_action_settle(1.0)
        self._log("success", f"{task_label}：已关闭购买体力弹窗")
        return "success"

    def _click_daily_youli_purchase_uses(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image229: dict[str, Any],
        image233: dict[str, Any],
        *,
        task_label: str,
    ):
        if not isinstance(image233, dict):
            raise RuntimeError(f"{task_label}：缺少 #233「游历购买次数不足」标注，无法确认购买终止态")
        if self._find_shape(image233, "空白") is None:
            raise RuntimeError(f"{task_label}：缺少 #233「空白」标注，无法关闭购买终止弹窗")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_count = int(payload.get("purchase_uses") or payload.get("buy_uses") or 99)
        clicked = 0
        while clicked < max_count:
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if self._daily_youli_text_is_purchase_empty(text):
                yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
                self._log("success", f"{task_label}：购买终止于 #233，已回到 #228")
                return "success"
            if not self._daily_youli_text_is_purchase(text):
                result = yield from runtime.wait_any(
                    {
                        "purchase": runtime.ocr_matches(
                            self._daily_youli_text_is_purchase,
                            label=f"{task_label}：等待购买体力 #229 OCR",
                            preview_chars=120,
                        ),
                        "empty": runtime.ocr_matches(
                            self._daily_youli_text_is_purchase_empty,
                            label=f"{task_label}：等待购买次数不足 OCR",
                            preview_chars=120,
                        ),
                    },
                    timeout=float(payload.get("purchase_timeout") or 10.0),
                    interval=float(payload.get("purchase_wait_interval_seconds") or 0.25),
                    label=f"{task_label}：等待购买弹窗结果",
                )
                frame = runtime.cur_frame(update=True)
                text = runtime.ocr_text(frame)
                if result == "empty" or self._daily_youli_text_is_purchase_empty(text):
                    yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
                    self._log("success", f"{task_label}：购买终止于 #233，已回到 #228")
                    return "success"
            remaining: int | None = None
            read_start = time.monotonic()
            read_timeout = float(payload.get("purchase_remaining_timeout") or 5.0)
            while True:
                self._raise_if_stopped(stop_event)
                numbers, text = runtime.ocr_numbers_in_shapes(229, ("剩余限购次数",), padding=12)
                remaining = numbers[-1] if numbers else None
                if remaining is not None:
                    break
                if time.monotonic() - read_start >= read_timeout:
                    break
                yield from runtime.wait_action_settle(0.5)
            if remaining is None:
                self._log("warning", f"{task_label}：未识别到剩余限购次数，继续购买直到 #233 终止态，OCR={text[:120]}")
            target_count = min(max_count, clicked + max(1, remaining or 1))
            yield from runtime.wait_click(229, "购买并使用")
            clicked += 1
            yield from runtime.wait_action_settle(float(payload.get("purchase_click_settle_seconds") or 1.2))
            scene_id, _score, frame = runtime.current_scene([233, 229], update=True)
            text = runtime.ocr_text(frame)
            self._log("detail", f"{task_label}：购买并使用 {clicked}/{target_count} 后 OCR={text[:120]}")
            if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
                yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
                self._log("success", f"{task_label}：购买并使用完成 {clicked}，已到 #233 并回到 #228")
                return "success"
            if scene_id != 229 and not self._daily_youli_text_is_purchase(text):
                self._log("success", f"{task_label}：购买弹窗已关闭，停止购买")
                return "success"
        if clicked > 0:
            scene_id, _score, frame = runtime.current_scene([229, 228, 233], update=True)
            text = runtime.ocr_text(frame)
            if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
                yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
            elif scene_id == 229 or self._daily_youli_text_is_purchase(text):
                yield from self._close_daily_youli_purchase_dialog(ctx, stop_event, image229, task_label=task_label)
        self._log("success", f"{task_label}：购买并使用完成 {clicked}")
        return "success"

    def _click_daily_youli_last_region(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image228: dict[str, Any],
        image236: dict[str, Any],
        image237: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([236, 228], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 236 or self._daily_youli_text_is_region_detail(text):
            self._log("success", f"{task_label}：当前已在游历区域详情，直接执行快速游历")
            quick_status = yield from self._click_daily_youli_quick_travel(ctx, stop_event, payload, image236, image237, task_label=task_label)
            if quick_status == "success":
                return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
            if quick_status == "completed":
                self._log("warning", f"{task_label}：当前区域已完成，不能按今日游历完成处理，返回 #228 继续查找其他区域")
                yield from self._return_daily_youli_region_to_home(ctx, stop_event, payload, image236, task_label=task_label)
            else:
                return quick_status
        yield from self._wait_daily_youli_home(ctx, stop_event, label="日常_游历：等待修仙传游历 #228")
        candidates = runtime.ocr_centers_in_shape(228, "检索区域", include=())
        if not candidates:
            raise RuntimeError("日常_游历：#228「检索区域」内未识别到可点击 OCR 文本")
        x, y, text = candidates[-1]
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：点击检索区域最后一个文本「{text}」",
                phase="daily_youli_click_region",
                current_scene=228,
            )
            self._log_locked("action", f"{task_label}：点击 #228 检索区域最后一个 OCR「{text}」")
        runtime.click_frame_point(228, x, y)
        yield from runtime.wait_action_settle(float(payload.get("region_click_settle_seconds") or 2.0))
        yield from self._wait_daily_youli_region_detail(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历区域详情 #236",
        )
        quick_status = yield from self._click_daily_youli_quick_travel(ctx, stop_event, payload, image236, image237, task_label=task_label)
        if quick_status != "completed":
            return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
        with self._lock:
            self._log_locked("action", f"{task_label}：最后区域「{text}」已探索完成，不能改点其他区域")
        yield from self._return_daily_youli_region_to_home(ctx, stop_event, payload, image236, task_label=task_label)
        raise RuntimeError(
            f"{task_label}：检索区域最后一个候选「{text}」只显示探索完成，未出现游历结果，不能按完成处理"
        )

    def _click_daily_youli_quick_travel(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image236: dict[str, Any],
        image237: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._wait_daily_youli_region_detail(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历区域详情 #236",
        )
        runtime.click_shape_center(236, "快速游历")
        yield from runtime.wait_action_settle(float(payload.get("quick_travel_settle_seconds") or 2.0))
        self._log("success", f"{task_label}：已点击快速游历")
        result = yield from runtime.wait_any(
            {
                "result": runtime.ocr_matches(
                    self._daily_youli_text_is_quick_result,
                    label=f"{task_label}：等待游历结果 #237 OCR",
                    preview_chars=120,
                ),
                "completed": runtime.ocr_matches(
                    self._daily_youli_text_is_region_completed,
                    label=f"{task_label}：等待游历区域已完成 OCR",
                    preview_chars=120,
                ),
                "resource_empty": runtime.ocr_matches(
                    self._daily_youli_text_is_purchase_empty,
                    label=f"{task_label}：等待快速游历资源不足 OCR",
                    preview_chars=120,
                ),
            },
            timeout=float(payload.get("quick_result_timeout") or self._daily_default_wait_condition_timeout),
            label=f"{task_label}：等待游历结果或已完成",
        )
        if result == "completed":
            return "completed"
        if result == "resource_empty":
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            image233 = images.get(233)
            yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label)
            raise RuntimeError(f"{task_label}：快速游历未触发结果，游历符/体力不足，已关闭提示并等待后续重试")
        return (yield from self._confirm_daily_youli_quick_result(ctx, stop_event, payload, image237, task_label=task_label))

    def _return_daily_youli_region_to_home(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image236: dict[str, Any],
        *,
        task_label: str,
    ):
        del image236
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image71 = images.get(71)
        image228 = images.get(228)
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from runtime.wait_click(236, "返回")
        result = yield from runtime.wait_any(
            {
                "home": runtime.ocr_matches(
                    self._daily_youli_text_is_home,
                    label=f"{task_label}：等待返回修仙传游历 #228 OCR",
                    preview_chars=120,
                ),
                "world_scene": runtime.view_visible(34),
            },
            timeout=18.0,
            label=f"{task_label}：等待返回修仙传或世界",
        )
        if result == "home":
            return "success"
        if not isinstance(image34, dict) or not isinstance(image228, dict):
            raise RuntimeError(f"{task_label}：区域返回落到世界，但缺少 #34/#228 标注，无法重新进入游历")
        entered = yield from self._try_enter_daily_youli_from_world_mainline(
            ctx,
            runtime,
            stop_event,
            payload,
            image34,
            image228,
            task_label=task_label,
        )
        if not entered:
            raise RuntimeError(f"{task_label}：区域返回落到世界，主线快路径未能重新进入游历")
        scene_id, _score, _frame = runtime.current_scene([71, 228], update=True)
        if scene_id == 71:
            yield from self._select_daily_youli_from_xiuxianzhuan_menu(ctx, stop_event, payload, image71, task_label=task_label)
        yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label=f"{task_label}：等待重新进入修仙传游历 #228")
        return "success"

    def _confirm_daily_youli_quick_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image237: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._wait_daily_youli_quick_result(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历结果 #237",
        )
        yield from runtime.wait_click(237, "确定")
        yield from runtime.wait_action_settle(float(payload.get("quick_result_confirm_settle_seconds") or 2.0))
        self._log("success", f"{task_label}：已确认游历结果")
        return "success"

    def _return_daily_youli_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image228: dict[str, Any],
        image236: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([236, 228, 34], update=True)
        text = runtime.ocr_text(frame)
        if self._daily_youli_text_is_reward_recovery(text):
            return (yield from self._return_daily_youli_reward_recovery_to_world(ctx, stop_event, task_label=task_label))
        if scene_id == 34:
            self._log("success", f"{task_label}：已在世界 #34")
            return "success"
        if scene_id == 236 or self._daily_youli_text_is_region_detail(text):
            yield from runtime.wait_click(236, "返回")
            yield from self._wait_daily_youli_home(
                ctx,
                stop_event,
                timeout=18.0,
                label=f"{task_label}：等待 #236 返回到修仙传游历 #228",
            )
        else:
            yield from self._wait_daily_youli_home(ctx, stop_event, label=f"{task_label}：等待修仙传游历 #228")

        yield from runtime.wait_click(228, "返回")
        result = yield from runtime.wait_any(
            {
                "world_scene": runtime.view_visible(34),
                "daily_text": runtime.ocr_matches(
                    self._daily_youli_text_is_daily_page,
                    label=f"{task_label}：等待 #228 返回后日常 OCR",
                    preview_chars=120,
                ),
            },
            timeout=18.0,
            label=f"{task_label}：等待 #228 返回到日常或世界",
        )
        if result == "daily_text":
            yield from self._exit_daily_youli_daily_page_to_world(ctx, stop_event, task_label=task_label)
        self._record_daily_entry_done(
            {},
            task_id="legacy-daily-youli",
            task_type="daily_youli",
            label=task_label,
            message="游历已完成并回到世界",
        )
        return "success"

    def _exit_daily_youli_daily_page_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        result = "daily_text"
        for attempt in range(2):
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从日常页退出到世界", phase="daily_youli_daily_exit")
                suffix = "" if attempt == 0 else f"重试 {attempt + 1}/2，"
                self._log_locked("action", f"{task_label}：{suffix}点击 #69「退出」返回世界")
            runtime.click_shape_center(69, "退出")
            yield from runtime.wait_action_settle(1.5)
            result = yield from runtime.wait_any(
                {
                    "world_scene": runtime.view_visible(34),
                    "daily_text": runtime.ocr_matches(
                        self._daily_youli_text_is_daily_page,
                        label=f"{task_label}：检查日常退出是否仍停留",
                        preview_chars=120,
                    ),
                },
                timeout=8.0,
                label=f"{task_label}：等待日常退出到世界",
            )
            if result != "daily_text":
                return "success"
        raise RuntimeError(f"{task_label}：点击 #69「退出」后仍停留在日常页，无法回到世界")

    def _return_daily_youli_reward_recovery_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        frame = runtime.cur_frame(update=True)
        text = runtime.ocr_text(frame)
        if not self._daily_youli_text_is_reward_recovery(text):
            yield from runtime.wait_any(
                {
                    "reward_recovery": runtime.ocr_matches(
                        self._daily_youli_text_is_reward_recovery,
                        label=f"{task_label}：等待奖励找回页 OCR",
                        preview_chars=120,
                    )
                },
                timeout=8.0,
                label=f"{task_label}：确认奖励找回页",
            )
        with self._lock:
            self._set_status_locked("running", f"{task_label}：从奖励找回页退出到世界", phase="daily_youli_reward_recovery_exit")
            self._log_locked("action", f"{task_label}：奖励找回 OCR 已确认，点击 #69「退出」关闭奖励找回页")
        runtime.click_shape_center(69, "退出")
        yield from runtime.wait_action_settle(1.5)
        result = yield from runtime.wait_any(
            {
                "world_scene": runtime.view_visible(34),
                "daily_text": runtime.ocr_matches(
                    self._daily_youli_text_is_daily_page,
                    label=f"{task_label}：等待奖励找回关闭后日常 OCR",
                    preview_chars=120,
                ),
            },
            timeout=12.0,
            label=f"{task_label}：等待奖励找回关闭后回到日常或世界",
        )
        if result == "daily_text":
            yield from self._exit_daily_youli_daily_page_to_world(ctx, stop_event, task_label=task_label)
        message = f"{task_label}：奖励找回页退出只证明已完成清理，不是游历完成证据，稍后重试"
        self._log("skip", message)
        return {"result": "success", "message": message}

    def _execute_daily_xianshi_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙市_秘藏阁资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image247 = images.get(247)
        image248 = images.get(248)
        image249 = images.get(249)
        image250 = images.get(250)

        task_label = "仙市_秘藏阁"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：从事务锚点 #34 开始",
                phase="daily_xianshi_go_world",
                current_scene=None,
            )
            self._log_locked("action", f"{task_label}：先回到 #34，再执行完整事务")
        yield from runtime.goto_view(34)
        yield from runtime.wait_view(34, label=f"{task_label}：等待世界 #34")
        yield from self._open_daily_xianshi_coin_list(ctx, stop_event, payload, image34, image247, image248, task_label=task_label)

        frame = runtime.cur_frame(update=True)
        text = runtime.ocr_text(frame)
        if self._daily_xianshi_text_is_box_detail(text):
            completed = yield from self._claim_daily_xianshi_coin_box(ctx, stop_event, payload, image250, task_label=task_label)
        else:
            completed = yield from self._click_daily_xianshi_free_coin_box(ctx, stop_event, payload, image249, image250, task_label=task_label)

        if completed:
            if completed == "not_free":
                self._record_daily_xianshi_done(payload, message="未发现免费宝匣，视为今日已无可领免费项")
            else:
                self._record_daily_xianshi_done(payload, message="免费宝匣已领取")
        else:
            self._record_daily_xianshi_retry(
                payload,
                message="未等到免费宝匣，本轮未确认领取",
                seconds=int(payload.get("coin_box_retry_seconds") or 600),
            )

        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_xianshi_to_world(ctx, stop_event, payload, image249, task_label=task_label),
            label=task_label,
            repeat_risk="重复领取",
        )
        if not completed:
            return "skipped"
        return "success"

    def _execute_xianshi_weekly_resources_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙市_每周资源资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image247 = images.get(247)
        if not isinstance(image34, dict) or not isinstance(image247, dict):
            raise RuntimeError("仙市_每周资源：缺少 #34 或 #247 标注，无法进入仙市")

        task_label = "仙市_每周资源"
        phase = self._xianshi_weekly_resources_phase(payload)

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, _frame = runtime.current_scene([316, 247, 34], update=True)
        claimed: list[str] = []
        claim_attempts = 0
        if scene_id == 316:
            if phase != "midnight":
                raise RuntimeError(f"{task_label}：当前停在商品详情 #316，非 00:00-05:00 补领窗口不自动领取")
            claim_attempts += 1
            claimed_item = yield from self._claim_current_xianshi_weekly_resource_detail(runtime, current_prayer_cycle())
            if claimed_item:
                claimed.append(claimed_item)
            yield from runtime.wait_view(247, label=f"{task_label}：等待返回秘藏阁 #247")
            scene_id = 247
        if scene_id != 247:
            if scene_id != 34:
                yield from runtime.goto_view(34)
                yield from runtime.wait_view(34)
            yield from self._open_xianshi_weekly_resource_entry(
                runtime,
                payload,
            )

        if phase == "midnight":
            target = current_prayer_cycle()
            max_attempts = 2
            while claim_attempts < max_attempts:
                claim_attempts += 1
                claimed_item = yield from self._claim_xianshi_weekly_resource_slot(runtime, "第1个物品", target)
                if not claimed_item:
                    break
                claimed.append(claimed_item)
        else:
            max_attempts = 8
            skipped_groups = 0
            skipped_group = next_prayer_cycle()
            for group in PRAYER_CYCLE_NAMES:
                if group == skipped_group:
                    skipped_groups += 1
                    continue
                slot = "第3个物品" if skipped_groups else "第1个物品"
                for _ in range(2):
                    if claim_attempts >= max_attempts:
                        break
                    claim_attempts += 1
                    claimed_item = yield from self._claim_xianshi_weekly_resource_slot(runtime, slot, group)
                    if not claimed_item:
                        break
                    claimed.append(claimed_item)
                if claim_attempts >= max_attempts:
                    break

        yield from runtime.wait_click_then_view(247, "返回", 34, settle_seconds=float(payload.get("xianshi_return_settle_seconds") or 1.0))
        suffix = f"，跳过 {next_prayer_cycle()} 资源" if phase == "after_reset" else ""
        claimed_text = ", ".join(claimed) if claimed else "目标资源已领完"
        next_time = self._record_xianshi_weekly_resources_done(payload)
        self._log(
            "success",
            f"{task_label}：已领取 {claimed_text}{suffix}，下次 {next_time}",
        )
        return "success"

    def _record_xianshi_weekly_resources_done(
        self,
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str:
        current = (now or datetime.now(PRAYER_CYCLE_TIMEZONE)).replace(tzinfo=None)
        next_time = next_business_time(
            ("00:00", "05:00"),
            now=current,
            weekdays=(0,),
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "xianshi-weekly-resources"),
            next_time,
        )
        return next_time

    def _open_xianshi_weekly_resource_entry(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        max_scrolls = max(0, int(payload.get("xianshi_entry_max_scrolls") or 4))
        entry_shape = runtime.shape(34, "仙市")
        internal_scene_left = False
        for scroll_index in range(max_scrolls + 1):
            if runtime.match_shape(entry_shape):
                yield from runtime.wait_click_then_shape(
                    34,
                    "仙市",
                    247,
                    "秘藏阁",
                    settle_seconds=2.0,
                    timeout=float(payload.get("xianshi_entry_wait_seconds") or 6.0),
                    retry_if_source_remains=True,
                    max_clicks=int(payload.get("xianshi_entry_max_clicks") or 3),
                )
                return True
            right_menu_text = runtime.ocr_text_in_shapes(
                34,
                ("右侧菜单",),
                padding=8,
            )
            compact_menu_text = _sanitize_ocr_text(right_menu_text).replace(" ", "")
            if (
                not internal_scene_left
                and "仙市" not in compact_menu_text
                and "天机阁" in compact_menu_text
                and "战斗" in compact_menu_text
            ):
                self._log(
                    "action",
                    "仙市_每周资源：当前是世界样式的内部场景，先通过正式离开确认返回正常世界",
                )
                runtime.click_shape_center(85, "离开")
                yield from runtime.wait_view(86, timeout=8.0, label="仙市_每周资源：等待离开场景确认")
                yield from runtime.wait_click_then_view(
                    86,
                    "确认",
                    34,
                    settle_seconds=3.0,
                    timeout=20.0,
                )
                internal_scene_left = True
                continue
            if scroll_index >= max_scrolls:
                break
            self._log(
                "action",
                f"仙市_每周资源：右侧菜单当前未显示「仙市」，向上恢复菜单 {scroll_index + 1}/{max_scrolls}",
            )
            can_continue = yield from runtime.scroll_shape_content(
                34,
                "右侧菜单",
                direction="up",
                ratio=0.55,
                duration=0.6,
                settle_seconds=0.8,
                unchanged_confirmations=2,
            )
            if not can_continue:
                break
        raise RuntimeError("仙市_每周资源：右侧菜单已恢复到顶端，仍未识别到「仙市」入口")

    def _xianshi_weekly_resources_phase(self, payload: dict[str, Any]) -> str:
        override = str(payload.get("phase") or "").strip()
        if override in {"midnight", "after_reset"}:
            return override
        now = datetime.now(PRAYER_CYCLE_TIMEZONE)
        if now.weekday() != 0:
            return "skip"
        if now.hour < 5:
            return "midnight"
        return "after_reset"

    def _classify_xianshi_weekly_resource_name(self, name: str) -> str | None:
        text = _sanitize_ocr_text(name)
        if "洗灵" in text:
            return "洗灵"
        if "花神" in text:
            return "仙花"
        if "灵草" in text:
            return "炼丹"
        if "御兽" in text or "兽" in text:
            return "灵兽"
        if "玄魄" in text or "玄" in text:
            return "淬体"
        return None

    def _claim_xianshi_weekly_resource_slot(self, runtime: Any, slot: str, expected_group: str):
        yield from runtime.wait_click_then_view(247, slot, 316)
        return (yield from self._claim_current_xianshi_weekly_resource_detail(runtime, expected_group, slot=slot))

    def _claim_current_xianshi_weekly_resource_detail(self, runtime: Any, expected_group: str, *, slot: str = "当前物品"):
        name_text = runtime.ocr_text_in_shapes(316, ("物品名称",), padding=8)
        group = self._classify_xianshi_weekly_resource_name(str(name_text or ""))
        if group != expected_group:
            self._log("skip", f"仙市_每周资源：{slot} 识别到「{name_text}」={group or '未知'}，预期 {expected_group}，视为目标资源已领完")
            yield from self._return_xianshi_weekly_resource_detail(runtime)
            return None
        yield from runtime.wait_click(316, "领取")
        yield from runtime.wait_action_settle(1.2)
        self._log("success", f"仙市_每周资源：已领取 {name_text}")
        return str(name_text or expected_group)

    def _return_xianshi_weekly_resource_detail(self, runtime: Any):
        last_error: Exception | None = None
        for shape_title in ("返回", "shape 3"):
            try:
                yield from runtime.wait_click_then_view(
                    316,
                    shape_title,
                    247,
                    settle_seconds=1.0,
                    timeout=6.0,
                )
                return True
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        return False

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, _frame = runtime.current_scene([34], update=True)
        if scene_id != 34:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：确认/恢复到世界 #34 后点击 VIP",
                    phase="daily_vip_go_world",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"{task_label}：确认/恢复到 #34")
            yield from runtime.goto_view(34)
            yield from runtime.wait_view(34, label=f"{task_label}：等待世界 #34")

        vip_shape = str(payload.get("vip_shape") or "[vip]")
        with self._lock:
            self._set_status_locked("running", f"{task_label}：点击 #34「{vip_shape}」", phase="daily_vip_click", current_scene=34)
            self._log_locked("action", f"{task_label}：点击 #34「{vip_shape}」")
        yield from runtime.wait_click(
            34,
            vip_shape,
            timeout=float(payload.get("vip_click_timeout") or payload.get("shape_click_timeout") or 8.0),
        )
        yield from runtime.wait_action_settle(float(payload.get("vip_settle_seconds") or 2.0))

        yield from runtime.wait_view(290, label=f"{task_label}：等待 VIP 月卡页 #290")
        yield from runtime.wait_click(
            290,
            "每日限购",
            timeout=float(payload.get("daily_limit_click_timeout") or payload.get("shape_click_timeout") or 8.0),
        )
        yield from runtime.wait_action_settle(float(payload.get("daily_limit_settle_seconds") or 1.5))

        yield from runtime.wait_view(291, label=f"{task_label}：等待每日限购页 #291")
        yield from runtime.wait_click(
            291,
            "修为",
            timeout=float(payload.get("xiuwei_click_timeout") or payload.get("shape_click_timeout") or 8.0),
        )
        yield from runtime.wait_action_settle(float(payload.get("xiuwei_settle_seconds") or 1.5))

        xiuwei_view = yield from runtime.wait_view(292, 291, label=f"{task_label}：等待修为限购页 #292")
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
            yield from self._return_daily_vip_to_world(runtime, payload, task_label=task_label, start_scene=291)
            self._record_daily_vip_done(payload, message="修为页未见免费礼包，已返回世界")
            return "success"

        free_status = yield from self._click_daily_vip_free_or_return(ctx, stop_event, payload, task_label=task_label)
        if free_status != "success":
            yield from self._return_daily_vip_to_world(runtime, payload, task_label=task_label, start_scene=291)
            self._record_daily_vip_done(payload, message="修为免费礼包未匹配，已返回世界")
            return "success"

        yield from self._return_daily_vip_to_world(runtime, payload, task_label=task_label, start_scene=292)

        self._record_daily_vip_done(payload, message="已点击修为免费礼包并返回世界")
        return "success"

    def _execute_daily_xianmeng_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = payload or {}
        self._daily_xianmeng_excluded_target_ids = set()
        self._daily_xianmeng_fallback_cooldowns = []
        target_rounds = int(payload.get("rounds") or payload.get("max_rounds") or 0)
        runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        attacks = 0
        remaining_attack_count: int | None = None
        swallowed_clicks = 0
        triple_attack_enabled = False
        attack_options_initialized = False
        single_attacks_since_option_probe = 0
        next_triple_probe_after = 1
        reward_score_checked = False
        reward_score_probe_failures = 0
        entry_scene = yield from self._enter_daily_xianmeng_attack_view(
            runtime,
            payload,
        )
        if entry_scene is None:
            return "skipped"
        scene_id = int(entry_scene)
        while True:
            if target_rounds > 0 and attacks >= target_rounds:
                yield from self._return_daily_xianmeng_to_world(runtime)
                self._record_daily_xianmeng_done(
                    payload,
                    message=f"已完成 {attacks} 轮攻击并返回 #34",
                )
                return "success"
            if scene_id == 317:
                yield from self._record_daily_xianmeng_immunity_cd(runtime, payload)
                return "skipped"

            if scene_id in (294, 295):
                if scene_id == 294 and not reward_score_checked:
                    reward_text = runtime.ocr_text_in_shapes(
                        294,
                        ("个人积分",),
                        padding=int(payload.get("reward_score_ocr_padding") or 12),
                        crop=True,
                    )
                    reward_points = self._parse_daily_xianmeng_personal_scores(reward_text)
                    if reward_points:
                        reward_score_checked = True
                        average_score = sum(reward_points) / len(reward_points)
                        self._daily_xianmeng_recent_average_score = average_score
                        minimum_score = float(
                            payload.get("minimum_average_personal_score") or 200
                        )
                        self._log(
                            "detail",
                            f"日常_仙盟：首份战报个人积分 {reward_points}，平均 {average_score:.1f}",
                        )
                        if average_score < minimum_score:
                            try:
                                count_snapshot = self._read_daily_xianmeng_count_snapshot()
                            except Exception as exc:
                                count_snapshot = {
                                    "ok": False,
                                    "complete": False,
                                    "reason": str(exc),
                                }
                            continue_sweep, runtime_remaining = (
                                self._daily_xianmeng_should_continue_low_score_sweep(
                                    count_snapshot,
                                    payload,
                                    now=job_now(),
                                )
                            )
                            if continue_sweep:
                                self._log(
                                    "warning",
                                    f"日常_仙盟：首份战报平均个人积分 {average_score:.1f} "
                                    f"低于 {minimum_score:g}，但 Runtime 剩余体力 "
                                    f"{runtime_remaining}，本 Cell 继续批量清扫；"
                                    "后续只按本地单次/三连成本扣减，不逐轮复查积分",
                                )
                            else:
                                self._schedule_daily_xianmeng_retry(
                                    payload,
                                    seconds=int(payload.get("low_score_retry_seconds") or 1200),
                                    message=(
                                        f"首份战报平均个人积分 {average_score:.1f} "
                                        f"低于 {minimum_score:g}，"
                                        "等待队友处理防护机制"
                                    ),
                                )
                                yield from self._return_daily_xianmeng_to_world(runtime)
                                return "skipped"
                    else:
                        reward_score_probe_failures += 1
                        if reward_score_probe_failures >= 2:
                            reward_score_checked = True
                            self._log(
                                "warning",
                                "日常_仙盟：连续两份战报未识别到个人积分，"
                                "本目标阶段不再重复 OCR，继续攻击",
                            )
                # The control can be visually recognizable before the result
                # popup has finished enabling input.
                yield from runtime.wait_action_settle(
                    float(payload.get("result_click_ready_seconds") or 0.8)
                )
                runtime.click_shape_center(scene_id, "确定" if scene_id == 294 else "关闭")
                yield from runtime.wait_action_settle(float(payload.get("result_close_settle_seconds") or 0.35))
                scene_id = yield from self._wait_daily_xianmeng_fast_attack_scene(
                    runtime,
                    payload,
                    result_min_elapsed=float(payload.get("result_close_retry_seconds") or 2.0),
                )
                continue

            if scene_id == 293:
                if not attack_options_initialized:
                    triple_attack_enabled = yield from self._ensure_daily_xianmeng_attack_options(
                        runtime,
                        payload,
                    )
                    remaining_attack_count = yield from self._read_daily_xianmeng_attack_count_once(
                        runtime,
                        payload,
                    )
                    attack_options_initialized = True
                    self._log(
                        "success",
                        "日常_仙盟：攻击阶段初始化完成，"
                        f"OCR 剩余体力 {remaining_attack_count}，三连={triple_attack_enabled}",
                    )
                    next_triple_probe_after = self._daily_xianmeng_next_triple_probe_after(
                        getattr(self, "_daily_xianmeng_last_option_snapshot", {}),
                        average_score=getattr(self, "_daily_xianmeng_recent_average_score", None),
                    )
                elif (
                    not triple_attack_enabled
                    and single_attacks_since_option_probe >= next_triple_probe_after
                ):
                    # Runtime is authoritative, but reading it after every
                    # attack is wasteful.  Probe near the estimated crossing
                    # point, then recompute from the fresh score.
                    triple_attack_enabled = yield from self._ensure_daily_xianmeng_attack_options(
                        runtime,
                        payload,
                    )
                    single_attacks_since_option_probe = 0
                    next_triple_probe_after = self._daily_xianmeng_next_triple_probe_after(
                        getattr(self, "_daily_xianmeng_last_option_snapshot", {}),
                        average_score=getattr(self, "_daily_xianmeng_recent_average_score", None),
                    )
                    if triple_attack_enabled:
                        self._log(
                            "success",
                            "日常_仙盟：Runtime 确认积分达到三连阈值，已切换并复验三连",
                        )

                if remaining_attack_count is not None and remaining_attack_count <= 0:
                        yield from self._return_daily_xianmeng_to_cover(runtime)
                        claimed = yield from self._claim_daily_xianmeng_task_rewards(
                            runtime,
                            payload,
                        )
                        if claimed > 0:
                            self._log(
                                "action",
                                f"日常_仙盟：攻击次数耗尽后又领取 {claimed} 档任务奖励，"
                                "重新进入战场清空新增体力",
                            )
                            next_scene = yield from self._enter_daily_xianmeng_attack_view(
                                runtime,
                                payload,
                            )
                            if next_scene is None:
                                return "skipped"
                            scene_id = int(next_scene)
                            attack_options_initialized = False
                            remaining_attack_count = None
                            reward_score_checked = False
                            reward_score_probe_failures = 0
                            continue
                        yield from self._return_daily_xianmeng_to_world(runtime)
                        self._record_daily_xianmeng_done(
                            payload,
                            message="任务已无可领且攻击次数为 0，已返回 #34",
                        )
                        return "success"

                required_attempts = self._daily_xianmeng_required_attempts(triple_attack_enabled)
                if (
                    triple_attack_enabled
                    and remaining_attack_count is not None
                    and 0 < remaining_attack_count < required_attempts
                ):
                    # Triple is worth more than three single attacks while it is
                    # still enabled.  Keep 1-2 stamina untouched until the
                    # triple-disable cutoff, then clear the remainder with
                    # single attacks.
                    if self._daily_xianmeng_should_preserve_tail_before_triple_disable():
                        next_time = self._daily_xianmeng_event_tail_next_time(payload)
                        if next_time:
                            yield from self._return_daily_xianmeng_to_world(runtime)
                            scheduler_task_id = str(
                                payload.get("__scheduler_task_id") or "legacy-daily-xianmeng"
                            )
                            self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
                            self._log(
                                "success",
                                f"日常_仙盟：剩余 {remaining_attack_count} 次不足三连，"
                                f"保留到下一活动尾程，下次 {next_time}",
                            )
                            return "skipped"
                    triple_attack_enabled = yield from self._disable_daily_xianmeng_triple_for_tail(
                        runtime,
                        payload,
                        remaining_attempts=remaining_attack_count,
                    )
                    required_attempts = self._daily_xianmeng_required_attempts(triple_attack_enabled)
                runtime.click_shape_center(293, "攻击")
                yield from runtime.wait_action_settle(float(payload.get("attack_click_settle_seconds") or 0.25))
                departed = yield from self._wait_daily_xianmeng_attack_departure(runtime, payload)
                if not departed:
                    swallowed_clicks += 1
                    if swallowed_clicks >= 3:
                        # This is an exceptional checkpoint, not part of the hot
                        # loop. The old OCR immunity branch is intentionally paid
                        # only after repeated attack-state non-transitions.
                        frame = runtime.cur_frame(update=True)
                        immunity_text = runtime.ocr_text_in_shapes(
                            317,
                            ("免战",),
                            padding=20,
                            frame_data_url=frame,
                            crop=True,
                        )
                        if "免战" in str(immunity_text or ""):
                            yield from self._record_daily_xianmeng_immunity_cd(runtime, payload)
                            return "skipped"
                        yield from self._return_daily_xianmeng_to_world(runtime)
                        self._schedule_daily_xianmeng_retry(
                            payload,
                            seconds=int(payload.get("attack_unconfirmed_retry_seconds") or 60),
                            message="攻击按钮连续三次未离开当前状态，本轮未计作成功攻击",
                        )
                        return "skipped"
                    self._log("warning", f"日常_仙盟：攻击按钮未触发状态迁移，重试 {swallowed_clicks}/3")
                    scene_id = 293
                    continue
                swallowed_clicks = 0
                attacks += 1
                if remaining_attack_count is not None:
                    remaining_attack_count = max(0, remaining_attack_count - required_attempts)
                if not triple_attack_enabled:
                    single_attacks_since_option_probe += required_attempts
                if attacks == 1 or attacks % 10 == 0 or remaining_attack_count == 0:
                    self._log(
                        "success",
                        "日常_仙盟：攻击已触发状态迁移，"
                        f"完成 {attacks} 轮，按本地计数剩余 {remaining_attack_count}",
                    )
                scene_id = yield from self._wait_daily_xianmeng_fast_attack_scene(
                    runtime,
                    payload,
                )
                continue

            scene_id = yield from self._wait_daily_xianmeng_fast_attack_scene(runtime, payload)

    def _wait_daily_xianmeng_attack_departure(self, runtime: Any, payload: dict[str, Any]):
        """Confirm an attack from the attack control disappearing, not a guessed next popup."""

        timeout = float(payload.get("attack_departure_timeout_seconds") or 4.0)
        threshold = float(payload.get("fast_attack_shape_threshold") or self.overlay_threshold)
        start = time.monotonic()
        while True:
            self._raise_if_stopped(runtime.stop_event or threading.Event())
            self._clear_tick_frame(runtime.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = runtime.cur_frame(update=True)
            if runtime.shape_score(293, "攻击", frame_data_url=frame) < threshold:
                return True
            if time.monotonic() - start >= timeout:
                return False
            yield from runtime.wait_action_settle(0.15)

    def _read_daily_xianmeng_attack_count_once(self, runtime: Any, payload: dict[str, Any]):
        """Read the attack count once per attack stage; never per battle."""

        for attempt in range(3):
            self._raise_if_stopped(runtime.stop_event or threading.Event())
            self._clear_tick_frame(runtime.ctx)
            yield BehaviorTreeStatus.RUNNING
            numbers, text = runtime.ocr_numbers_in_shapes(
                293,
                ("次数",),
                padding=int(payload.get("attack_count_ocr_padding") or 20),
                crop=True,
            )
            if numbers and int(numbers[0]) >= 0:
                return int(numbers[0])
            self._log("warning", f"日常_仙盟：攻击次数 OCR 第 {attempt + 1}/3 次未命中：{text[:80]}")
            yield from runtime.wait_action_settle(0.4)
        snapshot = self._read_daily_xianmeng_count_snapshot()
        if snapshot.get("ok") and snapshot.get("complete") and isinstance(snapshot.get("attack_count"), int):
            self._log("warning", "日常_仙盟：攻击次数 OCR 未命中，仅在阶段入口降级读取一次 Runtime")
            return int(snapshot["attack_count"])
        raise RuntimeError("日常_仙盟：攻击阶段无法读取剩余体力，拒绝盲目循环")

    @staticmethod
    def _parse_daily_xianmeng_personal_scores(text: str) -> list[int]:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return [
            int(value)
            for value in re.findall(r"个人积分\D{0,12}([0-9]{2,4})", normalized)
        ]

    def _wait_daily_xianmeng_fast_attack_scene(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        accept_results: bool = True,
        accept_attack: bool = True,
        result_min_elapsed: float = 0.0,
        attack_min_elapsed: float = 0.0,
    ):
        """Recognize the attack loop using only the mapped scene image ROIs."""

        timeout = float(payload.get("fast_attack_scene_timeout_seconds") or 20.0)
        threshold = float(payload.get("fast_attack_shape_threshold") or self.overlay_threshold)
        start = time.monotonic()
        while True:
            self._raise_if_stopped(runtime.stop_event or threading.Event())
            self._clear_tick_frame(runtime.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = runtime.cur_frame(update=True)
            report_confirm_score = runtime.shape_score(294, "确定", frame_data_url=frame)
            report_title_score = runtime.shape_score(294, "挑战成功", frame_data_url=frame)
            scores = {
                294: min(report_confirm_score, report_title_score),
                295: runtime.shape_score(295, "关闭", frame_data_url=frame),
                293: runtime.shape_score(293, "攻击", frame_data_url=frame),
            }
            # The reward rows vary, so their broad body anchor is deliberately
            # weaker than a normal control. Pair it with the exact 100% report
            # button; the network warning negative frame scores 0%/2% here.
            elapsed = time.monotonic() - start
            if (
                accept_results
                and elapsed >= max(0.0, float(result_min_elapsed))
                and report_confirm_score >= threshold
                and report_title_score >= threshold
            ):
                return 294
            if accept_results and elapsed >= max(0.0, float(result_min_elapsed)) and scores[295] >= threshold:
                return 295
            if accept_attack and elapsed >= max(0.0, float(attack_min_elapsed)) and scores[293] >= threshold:
                return 293
            if elapsed >= timeout:
                raise TimeoutError(
                    "日常_仙盟：轻量攻击循环等待超时，"
                    + ", ".join(f"#{key}={value:.0f}%" for key, value in scores.items())
                )
            yield from runtime.wait_action_settle(0.25)

    def _read_daily_xianmeng_command_target_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_command_target_snapshot,
        )

        return read_landcontend_command_target_snapshot()

    def _read_daily_xianmeng_immunity_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_immunity_snapshot,
        )

        return read_landcontend_immunity_snapshot()

    def _read_daily_xianmeng_count_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_count_snapshot,
        )

        return read_landcontend_count_snapshot()

    def _click_daily_xianmeng_ocr(
        self,
        runtime: Any,
        text: str,
        *,
        timeout: float,
        fuzzy: bool = False,
        occurrence: int | None = None,
        max_center_y: float | None = None,
    ):
        """Click a navigation label on the current full frame.

        #66 is used only as the 900x1600 coordinate canvas after leaving that
        page. Business decisions (command target and cooldown) never come from
        this OCR helper.
        """

        deadline = time.monotonic() + max(0.1, float(timeout))
        last_error: Exception | None = None
        while True:
            self._raise_if_stopped(runtime.stop_event or threading.Event())
            try:
                if max_center_y is not None:
                    if fuzzy:
                        raise ValueError("OCR 空间约束暂不支持模糊匹配")
                    frame = runtime.cur_frame(update=True)
                    matches = [
                        item
                        for item in find_text_matches(runtime.ocr_tokens(frame), text)
                        if item.y + item.h / 2 <= float(max_center_y)
                    ]
                    if len(matches) != 1:
                        raise RuntimeError(
                            f"上方活动列表内「{text}」命中数为 {len(matches)}，拒绝点击"
                        )
                    match = matches[0]
                    runtime.click_frame_point(66, *match.point())
                else:
                    match = runtime.click_ocr_text(
                        66,
                        text,
                        match_mode="fuzzy" if fuzzy else "exact",
                        min_similarity=72.0 if fuzzy else 100.0,
                        ambiguity_margin=8.0,
                        occurrence=occurrence,
                    )
                self._log("action", f"日常_仙盟：点击「{text}」")
                return match
            except RuntimeError as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise TimeoutError(f"日常_仙盟：等待并点击「{text}」超时：{last_error}")
            self._clear_tick_frame(runtime.ctx)
            yield BehaviorTreeStatus.RUNNING
            if (runtime.stop_event or threading.Event()).wait(1.0):
                self._raise_if_stopped(runtime.stop_event or threading.Event())

    @staticmethod
    def _daily_xianmeng_claim_markers(runtime: Any) -> list[dict[str, Any]]:
        frame = runtime.cur_frame(update=True)
        return [
            item
            for item in runtime.ocr_tokens(frame)
            if str(item.get("text") or "").strip() == "领"
        ]

    @staticmethod
    def _daily_xianmeng_first_task_fingerprint(runtime: Any) -> str:
        frame = runtime.cur_frame(update=True)
        tokens = [
            item
            for item in runtime.ocr_tokens(frame)
            if 60.0 <= float(item.get("x") or 0.0) <= 500.0
            and 275.0 <= float(item.get("y") or 0.0) <= 455.0
        ]
        return "".join(str(item.get("text") or "").strip() for item in tokens)

    @staticmethod
    def _daily_xianmeng_transient_popup_metrics(runtime: Any, frame: str) -> dict[str, Any]:
        """Recognize the broken dark business layer without inventing a scene.

        The 2026-08-16 production frame had no normal page chrome, an almost
        completely black body, and one bright in-game close control at the
        upper right.  This predicate deliberately requires both facts and is
        only used inside the Xianmeng transaction, where closing the layer is
        a reversible recovery action.
        """

        from PIL import Image

        raw = runtime.runner._decode_frame_data_url(frame)
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("L")
            width, height = image.size
            body = image.crop((0, int(height * 0.12), width, int(height * 0.95)))
            close_roi = image.crop(
                (
                    int(width * 0.80),
                    int(height * 0.045),
                    int(width * 0.94),
                    int(height * 0.13),
                )
            )
            body_histogram = body.histogram()
            close_histogram = close_roi.histogram()
            body_dark_ratio = sum(body_histogram[:24]) / max(1, body.width * body.height)
            close_bright_ratio = sum(close_histogram[170:]) / max(
                1, close_roi.width * close_roi.height
            )
        return {
            "matched": body_dark_ratio >= 0.97 and close_bright_ratio >= 0.012,
            "width": width,
            "height": height,
            "body_dark_ratio": body_dark_ratio,
            "close_bright_ratio": close_bright_ratio,
        }

    def _close_daily_xianmeng_transient_popup(self, runtime: Any):
        """Close and verify the high-confidence transient Xianmeng business layer."""

        try:
            frame = runtime.cur_frame(update=True)
            metrics = self._daily_xianmeng_transient_popup_metrics(runtime, frame)
        except Exception as exc:
            self._log("detail", f"日常_仙盟：异常业务层帧判定失败：{exc}")
            return 0
        if not metrics["matched"]:
            return 0

        evidence_root = codeyun_temp_root("fanxiu-evidence", "xianmeng-transient-popup")
        evidence_path = evidence_root / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
        evidence_path.write_bytes(runtime.runner._decode_frame_data_url(frame))
        self._log(
            "action",
            (
                "日常_仙盟：识别到异常黑色业务层，优先点击游戏内关闭；"
                f"dark={metrics['body_dark_ratio']:.3f}，"
                f"close={metrics['close_bright_ratio']:.3f}，证据={evidence_path}"
            ),
        )
        # #473 is only the canonical 900x1600 coordinate canvas here.  The
        # click is authorized by the current-frame predicate above, not by a
        # claim that the broken layer itself is scene #473.
        runtime.click_frame_point(473, 790.0, 130.0)
        yield from runtime.wait_action_settle(0.8)
        recovered_scene = yield from self._wait_daily_xianmeng_exact_view(
            runtime,
            473,
            474,
            timeout=8.0,
        )
        self._log("success", f"日常_仙盟：异常业务层已关闭，恢复到 #{recovered_scene}")
        return recovered_scene

    def _claim_daily_xianmeng_task_rewards(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        """Claim the Xianmeng/cultivation ladders without touching item icons."""

        yield from self._wait_daily_xianmeng_exact_view(runtime, 473, timeout=12.0)
        cover_markers = self._daily_xianmeng_claim_markers(runtime)
        if not any(float(item.get("y") or 0.0) >= 1200.0 for item in cover_markers):
            return 0

        runtime.click_shape_center(473, "任务")
        yield from runtime.wait_action_settle(
            float(payload.get("task_settle_seconds") or 1.0)
        )
        try:
            yield from self._wait_daily_xianmeng_exact_view(runtime, 474, timeout=12.0)
        except TimeoutError:
            # The task tab can remain unavailable during an otherwise active
            # qualifying battlefield.  Reward collection is optional and
            # must not block the challenge itself.  Only continue when a
            # fresh exact read proves the ignored tap left us on the cover;
            # any other landing remains a hard failure.
            yield from self._wait_daily_xianmeng_exact_view(runtime, 473, timeout=3.0)
            self._log(
                "skip",
                "日常_仙盟：任务入口未响应且仍在封面 #473，跳过可选领奖并继续挑战",
            )
            return 0

        claimed = 0
        max_claims = max(1, int(payload.get("max_task_claims_per_tab") or 24))
        tabs = (
            ("仙盟页签", 140.0, 260.0),
            ("修为页签", 300.0, 415.0),
        )
        for tab_shape, min_x, max_x in tabs:
            markers = self._daily_xianmeng_claim_markers(runtime)
            if not any(
                float(item.get("y") or 0.0) < 300.0
                and min_x <= float(item.get("x") or 0.0) <= max_x
                for item in markers
            ):
                continue
            runtime.click_shape_center(474, tab_shape)
            yield from runtime.wait_action_settle(0.8)
            for _index in range(max_claims):
                markers = self._daily_xianmeng_claim_markers(runtime)
                if not any(
                    float(item.get("y") or 0.0) < 300.0
                    and min_x <= float(item.get("x") or 0.0) <= max_x
                    for item in markers
                ):
                    break
                before = self._daily_xianmeng_first_task_fingerprint(runtime)
                if not before:
                    break
                # The left text/progress area claims the completed first row.
                # The right reward icons only open #250 item details and are a
                # deliberate no-click region in both code and assets.
                runtime.click_shape_center(474, "首条任务领取区")
                yield from runtime.wait_action_settle(0.9)
                recovered_scene = yield from self._close_daily_xianmeng_transient_popup(runtime)
                if recovered_scene == 473:
                    self._log(
                        "skip",
                        "日常_仙盟：领奖触发异常业务层并已恢复封面，停止可选领奖",
                    )
                    return claimed
                after = self._daily_xianmeng_first_task_fingerprint(runtime)
                if not after or after == before:
                    break
                claimed += 1
                self._log("detail", f"日常_仙盟：已领取第 {claimed} 档任务奖励")

        markers = self._daily_xianmeng_claim_markers(runtime)
        remaining_top_markers = [
            item
            for item in markers
            if float(item.get("y") or 0.0) < 300.0
        ]
        if remaining_top_markers:
            self._log(
                "skip",
                "日常_仙盟：任务页仍有领取标记但首行状态不再变化，"
                "已安全停止，拒绝点击奖励图标",
            )
        runtime.click_shape_center(474, "仙盟争霸")
        yield from runtime.wait_action_settle(0.8)
        yield from self._wait_daily_xianmeng_exact_view(runtime, 473, timeout=12.0)
        if claimed:
            self._log("success", f"日常_仙盟：已领取 {claimed} 档任务奖励并返回封面")
        return claimed

    def _return_daily_xianmeng_to_world(self, runtime: Any):
        self._log("action", "日常_仙盟：返回世界 #34")
        scene_id = 0
        if isinstance(getattr(runtime, "ctx", None), dict):
            try:
                scene_id = yield from self._wait_daily_xianmeng_exact_view(
                    runtime, 293, 317, 294, 295, 471, 475, 476, 474, 473, 34, timeout=2.0
                )
            except TimeoutError:
                scene_id = 0
        if scene_id == 294:
            runtime.click_shape_center(294, "确定")
            yield from runtime.wait_action_settle(0.8)
            scene_id = 293
        if scene_id == 295:
            runtime.click_shape_center(295, "关闭")
            yield from runtime.wait_action_settle(0.8)
            scene_id = 293
        if scene_id in (293, 317):
            runtime.click_shape_center(293, "返回")
            yield from runtime.wait_action_settle(1.0)
            scene_id = 475
        if scene_id == 471:
            runtime.click_shape_center(471, "返回")
            yield from runtime.wait_action_settle(1.0)
            yield from self._wait_daily_xianmeng_exact_view(runtime, 475, timeout=8.0)
            scene_id = 475
        if scene_id == 474:
            runtime.click_shape_center(474, "返回")
            yield from runtime.wait_action_settle(0.8)
            scene_id = 473
        if scene_id == 473:
            runtime.click_shape_center(473, "返回")
            yield from runtime.wait_action_settle(1.0)
            scene_id = 34
        if scene_id == 475:
            runtime.click_shape_center(475, "离开")
            yield from runtime.wait_action_settle(0.8)
            yield from self._wait_daily_xianmeng_exact_view(runtime, 476, timeout=8.0)
            scene_id = 476
        if scene_id == 476:
            runtime.click_shape_center(476, "确认")
            yield from runtime.wait_action_settle(1.0)
        yield from runtime.goto_view(34)
        yield from runtime.wait_view(34, timeout=30.0, label="日常_仙盟：确认返回世界 #34")
        return "success"

    def _return_daily_xianmeng_to_cover(self, runtime: Any):
        """Return from any Xianmeng subview to the activity cover (#473)."""
        yield from self._return_daily_xianmeng_to_world(runtime)
        return (yield from self._enter_daily_xianmeng_cover(runtime))

    def _enter_daily_xianmeng_cover(self, runtime: Any):
        recovered_scene = yield from self._close_daily_xianmeng_transient_popup(runtime)
        if recovered_scene == 473:
            return 473
        try:
            yield from self._wait_daily_xianmeng_exact_view(runtime, 473, timeout=2.0)
            return 473
        except TimeoutError:
            pass
        yield from runtime.goto_view(34)
        yield from runtime.wait_view(34, timeout=30.0, label="日常_仙盟：确认世界 #34")
        yield from runtime.goto_view(66)
        yield from runtime.wait_view(66, timeout=30.0, label="日常_仙盟：等待仙盟列表 #66")
        yield from self._click_daily_xianmeng_ocr(
            runtime,
            "仙盟争霸",
            timeout=20.0,
            max_center_y=800.0,
        )
        yield from runtime.wait_action_settle(2.0)
        yield from self._wait_daily_xianmeng_exact_view(runtime, 473, timeout=20.0)
        return 473

    def _advance_daily_xianmeng_after_immunity(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        """Try the next damaged non-friendly pillar after a target-specific CD."""

        selection = getattr(self, "_daily_xianmeng_target_selection", {})
        if selection.get("mode") != "non-friendly-fallback":
            yield from self._record_daily_xianmeng_immunity_cd(runtime, payload)
            return None

        target = selection.get("target") if isinstance(selection.get("target"), dict) else {}
        target_id = int(target.get("id") or 0)
        excluded = set(getattr(self, "_daily_xianmeng_excluded_target_ids", set()))
        if target_id > 0:
            excluded.add(target_id)
        self._daily_xianmeng_excluded_target_ids = excluded

        try:
            snapshot = self._read_daily_xianmeng_immunity_snapshot()
        except Exception as exc:
            snapshot = {"ok": False, "complete": False, "reason": str(exc)}
        cooldowns = list(getattr(self, "_daily_xianmeng_fallback_cooldowns", []))
        if snapshot.get("ok") and snapshot.get("complete"):
            cooldowns.append(
                {
                    "target_id": target_id,
                    "target_name": str(target.get("name") or ""),
                    "seconds": max(0, int(snapshot.get("cooldown_seconds") or 0)),
                }
            )
        self._daily_xianmeng_fallback_cooldowns = cooldowns

        remaining = [
            item
            for item in selection.get("candidates", [])
            if isinstance(item, dict) and int(item.get("id") or 0) not in excluded
        ]
        if remaining:
            self._log(
                "skip",
                f"日常_仙盟：{target.get('name') or target_id} 处于免战，"
                f"顺延尝试下一根非友军柱子 {remaining[0].get('name')}",
            )
            yield from self._return_daily_xianmeng_to_world(runtime)
            return (yield from self._enter_daily_xianmeng_attack_view(runtime, payload))

        if cooldowns:
            retry_seconds = min(int(item["seconds"]) for item in cooldowns)
            message = f"全部候选均免战，最短动态 CD 剩余 {max(0, retry_seconds)} 秒"
        else:
            retry_seconds = int(payload.get("immunity_probe_retry_seconds") or 300)
            message = "全部候选均不可攻击且动态 CD 暂不可用，按 5 分钟安全复查"
        next_time = self._schedule_daily_xianmeng_retry(
            payload,
            seconds=max(5, retry_seconds),
            message=message,
        )
        yield from self._return_daily_xianmeng_to_world(runtime)
        self._log(
            "success",
            f"日常_仙盟：降级候选已全部顺延检查，工程调度时间 {next_time}",
        )
        return None

    def _schedule_daily_xianmeng_retry(
        self,
        payload: dict[str, Any],
        *,
        seconds: int,
        message: str,
    ) -> str | None:
        now = job_now()
        retry_at = now + timedelta(seconds=max(0, int(seconds)))
        end_text = str(payload.get("daily_end_time") or "22:00")
        try:
            end_clock = datetime.strptime(end_text, "%H:%M").time()
        except ValueError:
            end_clock = datetime.strptime("22:00", "%H:%M").time()
        close_at = now.replace(
            hour=end_clock.hour,
            minute=end_clock.minute,
            second=end_clock.second,
            microsecond=0,
        )
        next_time = (
            retry_at.strftime("%Y-%m-%d %H:%M:%S")
            if now < close_at and retry_at < close_at
            else None
        )
        scheduler_task_id = str(
            payload.get("__scheduler_task_id") or "legacy-daily-xianmeng"
        )
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        if next_time is None:
            self._log(
                "skip",
                f"日常_仙盟：{message}；重试将达到或超过 {end_text}，活动结束，不再调度",
            )
        else:
            self._log("skip", f"日常_仙盟：{message}，下次 {next_time}")
        return next_time

    def _wait_daily_xianmeng_command_target(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        self._raise_if_stopped(runtime.stop_event or threading.Event())
        yield BehaviorTreeStatus.RUNNING
        try:
            snapshot = self._read_daily_xianmeng_command_target_snapshot()
        except Exception as exc:
            snapshot = {"ok": False, "complete": False, "reason": str(exc)}
        target = snapshot.get("target")
        if (
            snapshot.get("ok")
            and snapshot.get("complete")
            and int(snapshot.get("command_count") or 0) == 1
            and isinstance(target, dict)
            and int(target.get("id") or 0) > 0
            and str(target.get("name") or "").strip()
        ):
            current_hp = target.get("pillar_cur_hp")
            if not isinstance(current_hp, (int, float)) or float(current_hp) > 0:
                self._daily_xianmeng_target_selection = {
                    "mode": "command",
                    "target": target,
                    "candidates": [target],
                }
                evidence = (
                    snapshot.get("evidence")
                    if isinstance(snapshot.get("evidence"), dict)
                    else {}
                )
                self._log(
                    "success",
                    (
                        f"日常_仙盟：Runtime 唯一指挥目标 {target['name']}"
                        f"（slot={int(target.get('slot') or 0)}），"
                        f"耗时 {float(snapshot.get('elapsed_seconds') or 0.0):.2f}s，"
                        f"resolver={evidence.get('manager_resolver') or 'unknown'}"
                    ),
                )
                return target
            self._log(
                "skip",
                f"日常_仙盟：指挥目标 {target['name']} 的阵柱已被打碎，等待新指挥目标",
            )
        fallback = self._daily_xianmeng_fallback_candidates(snapshot)
        excluded = set(getattr(self, "_daily_xianmeng_excluded_target_ids", set()))
        candidates = [
            item
            for item in fallback.get("candidates", [])
            if int(item.get("id") or 0) not in excluded
        ]
        sweep_allowed = self._daily_xianmeng_stamina_sweep_allowed()
        fallback_allowed = bool(fallback.get("own_pillar_destroyed")) or sweep_allowed
        if fallback_allowed and candidates:
            target = candidates[0]
            self._daily_xianmeng_target_selection = {
                "mode": "non-friendly-fallback",
                "target": target,
                "candidates": candidates,
            }
            reason = (
                "我方柱子已爆且无法再设置指挥目标"
                if fallback.get("own_pillar_destroyed")
                else "已进入 21:10 后体力清扫且当前没有唯一指挥目标"
            )
            self._log(
                "action",
                f"日常_仙盟：{reason}，"
                f"降级选择非友军中柱子损坏最多的 {target['name']}"
                f"（剩余 {float(target['pillar_ratio']) * 100:.1f}%）",
            )
            return target
        own_rows = fallback.get("own_camps") if isinstance(fallback.get("own_camps"), list) else []
        own_hp = [
            f"{row.get('name') or row.get('id')}={float(row['pillar_cur_hp']):.0f}/{float(row['pillar_max_hp']):.0f}"
            for row in own_rows
            if isinstance(row.get("pillar_cur_hp"), (int, float))
            and isinstance(row.get("pillar_max_hp"), (int, float))
        ]
        self._log(
            "skip",
            "日常_仙盟：当前没有可攻击的唯一指挥目标，"
            f"command_count={int(snapshot.get('command_count') or 0)}，"
            f"我方阵柱={','.join(own_hp) or 'unknown'}；"
            + (
                "已允许自主选敌但当前没有可攻击的非友军；等待下次同步"
                if fallback_allowed
                else "21:10 前保持等待大师兄设置目标"
            ),
        )
        return None

    @staticmethod
    def _daily_xianmeng_stamina_sweep_allowed() -> bool:
        """Allow autonomous target selection from the first stamina sweep onward."""

        from backend.core.fanxiu.activity.daily_activity_job_registry import (
            XIANMENG_STAMINA_SWEEPS,
        )

        current = job_now()
        first_sweep = min(XIANMENG_STAMINA_SWEEPS)
        return (current.hour, current.minute) >= first_sweep

    @staticmethod
    def _daily_xianmeng_should_preserve_tail_before_triple_disable(
        now: datetime | None = None,
    ) -> bool:
        """Keep sub-three stamina before triple attacks are disabled at 21:30."""

        from backend.core.fanxiu.activity.daily_activity_job_registry import (
            XIANMENG_TRIPLE_DISABLE_AT,
        )

        current = now or job_now()
        return (current.hour, current.minute) < XIANMENG_TRIPLE_DISABLE_AT

    @staticmethod
    def _daily_xianmeng_fallback_candidates(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Build the non-friendly fallback queue from authoritative Runtime facts."""

        from backend.core.fanxiu.catalog.server_relations import (
            classify_fanxiu_target_relation,
        )

        camps = snapshot.get("camps") if isinstance(snapshot.get("camps"), list) else []
        rows: list[dict[str, Any]] = []
        own_rows: list[dict[str, Any]] = []
        prepared: list[dict[str, Any]] = []
        for raw in camps:
            if not isinstance(raw, dict):
                continue
            server_id = raw.get("server_id")
            relation = classify_fanxiu_target_relation(
                is_npc=False,
                server_id=server_id,
            )
            row = {**raw, "relation": relation}
            if relation.get("relation") == "same_server":
                own_rows.append(row)
            prepared.append(row)

        own_ids = {int(row.get("id") or 0) for row in own_rows}
        battlefield_ally_ids = {
            int(row.get("ally_camp_id") or 0)
            for row in own_rows
            if int(row.get("ally_camp_id") or 0) > 0
        }
        battlefield_ally_ids.update(
            int(row.get("id") or 0)
            for row in prepared
            if int(row.get("ally_camp_id") or 0) in own_ids
        )
        for row in prepared:
            relation = row["relation"]
            if relation.get("camp") != "non_friendly":
                continue
            if int(row.get("id") or 0) in battlefield_ally_ids:
                continue
            cur_hp = row.get("pillar_cur_hp")
            max_hp = row.get("pillar_max_hp")
            if not isinstance(cur_hp, (int, float)) or not isinstance(max_hp, (int, float)):
                continue
            if float(max_hp) <= 0 or float(cur_hp) <= 0:
                continue
            row["pillar_ratio"] = max(0.0, min(1.0, float(cur_hp) / float(max_hp)))
            rows.append(row)

        own_pillar_destroyed = any(
            isinstance(row.get("pillar_cur_hp"), (int, float))
            and float(row["pillar_cur_hp"]) <= 0
            for row in own_rows
        )
        rows.sort(
            key=lambda row: (
                float(row["pillar_ratio"]),
                float(row["pillar_cur_hp"]),
                int(row.get("id") or 0),
            )
        )
        return {
            "own_pillar_destroyed": own_pillar_destroyed,
            "own_camps": own_rows,
            "battlefield_ally_ids": sorted(battlefield_ally_ids),
            "candidates": rows,
        }

    def _enter_daily_xianmeng_attack_view(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        current_scene = 0
        try:
            current_scene = yield from self._wait_daily_xianmeng_exact_view(
                runtime,
                317,
                293,
                295,
                294,
                471,
                475,
                473,
                timeout=2.0,
            )
        except TimeoutError:
            pass
        if current_scene in (317, 293, 295, 294):
            return current_scene

        if current_scene not in (471, 475):
            if current_scene != 473:
                yield from self._enter_daily_xianmeng_cover(runtime)

            # Rewards can directly replenish challenge stamina and clone counts.
            # Therefore every cover entry claims both ladders before evaluating or
            # consuming the battlefield resource.
            yield from self._claim_daily_xianmeng_task_rewards(runtime, payload)
            entry_attempts = max(1, int(payload.get("battlefield_entry_attempts") or 3))
            cover_reentries_left = max(
                0,
                int(payload.get("battlefield_cover_reentries") or 1),
            )
            for entry_attempt in range(1, entry_attempts + 1):
                runtime.click_shape_center(473, "前往战场")
                yield from runtime.wait_action_settle(
                    float(payload.get("entry_settle_seconds") or 2.0)
                )
                try:
                    current_scene = yield from self._wait_daily_xianmeng_exact_view(
                        runtime,
                        475,
                        34,
                        timeout=float(payload.get("battlefield_entry_probe_timeout") or 8.0),
                    )
                except TimeoutError:
                    # Exclude #473 from the successor wait: recognizing the
                    # unchanged cover immediately would race the asynchronous
                    # EnterScene callback and turn one click into repeated
                    # clicks.  A timeout is the only evidence that the cover
                    # never departed during the bounded probe.
                    current_scene = 473
                if current_scene == 475:
                    break
                if current_scene == 34 and cover_reentries_left > 0:
                    cover_reentries_left -= 1
                    self._log(
                        "warning",
                        "日常_仙盟：前往战场后活动页已关闭但落回 #34，"
                        "按正式入口有界重进一次",
                    )
                    current_scene = yield from self._enter_daily_xianmeng_cover(runtime)
                    continue
                self._log(
                    "warning",
                    f"日常_仙盟：前往战场点击未生效，仍在 #473，重试 {entry_attempt}/{entry_attempts}",
                )
            if current_scene != 475:
                raise TimeoutError(
                    f"日常_仙盟：前往战场连续 {entry_attempts} 次未生效，"
                    f"最终场景 #{current_scene}"
                )
        if current_scene == 475:
            runtime.click_shape_center(475, "战场地图")
            yield from runtime.wait_action_settle(
                float(payload.get("map_settle_seconds") or 2.0)
            )
            yield from self._wait_daily_xianmeng_exact_view(runtime, 471, timeout=20.0)

        target = yield from self._wait_daily_xianmeng_command_target(runtime, payload)
        if target is None:
            yield from self._return_daily_xianmeng_to_world(runtime)
            self._schedule_daily_xianmeng_retry(
                payload,
                seconds=int(payload.get("no_command_retry_seconds") or 1800),
                message="未等到唯一指挥目标，已返回 #34",
            )
            return None

        target_id = int(target["id"])
        target_name = str(target["name"])
        target_slot = int(target.get("slot") or 0)
        if target_slot not in range(1, 9):
            raise RuntimeError(f"日常_仙盟：动态目标 {target_name} 缺少有效阵营槽位")
        runtime.click_shape_center(471, f"阵营槽位{target_slot}")
        self._log(
            "action",
            f"日常_仙盟：按动态 slot={target_slot} 选择 {target_name}({target_id})",
        )
        yield from runtime.wait_action_settle(float(payload.get("target_settle_seconds") or 1.0))
        # 目标详情弹窗跟随柱子位置浮动，不能用一个固定 #472 坐标点击。
        # 目标决策仍完全来自动态 camp id/slot；OCR 只定位已打开弹窗的动作按钮。
        yield from self._click_daily_xianmeng_ocr(runtime, "跳转", timeout=10.0)
        self._log("success", f"日常_仙盟：目标详情已打开并跳转 {target_name}({target_id})")
        yield from runtime.wait_action_settle(float(payload.get("jump_settle_seconds") or 2.0))
        return (
            yield from self._wait_daily_xianmeng_exact_view(
                runtime,
                317,
                293,
                timeout=30.0,
            )
        )

    def _read_daily_xianmeng_attack_options_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.landcontend import (
            read_landcontend_attack_options_snapshot,
        )

        return read_landcontend_attack_options_snapshot()

    @staticmethod
    def _daily_xianmeng_option_cycle_key(snapshot: dict[str, Any]) -> str:
        captured_at = str(snapshot.get("captured_at") or "")
        day = (
            captured_at[:10]
            if len(captured_at) >= 10
            else datetime.now().astimezone().date().isoformat()
        )
        stage = int(snapshot.get("stage") or 0)
        return f"{day}:stage-{stage}"

    @staticmethod
    def _daily_xianmeng_required_attempts(triple_checked: bool) -> int:
        return 3 if triple_checked else 1

    @staticmethod
    def _daily_xianmeng_next_triple_probe_after(
        snapshot: dict[str, Any],
        *,
        average_score: float | None,
    ) -> int:
        """Estimate a bounded single-attack interval before the next Runtime probe."""

        if bool(snapshot.get("triple_checked")):
            return 1
        score = max(0, int(snapshot.get("score") or 0))
        threshold = max(1, int(snapshot.get("triple_score_threshold") or 15_000))
        gap = max(0, threshold - score)
        if gap <= 0:
            return 1
        estimated_gain = max(50.0, float(average_score or 200.0))
        estimated_rounds = max(1, math.ceil(gap / estimated_gain))
        # Recheck a little before the estimate, but never burn more than ten
        # single attacks without refreshing the authoritative score.
        return max(1, min(10, estimated_rounds - 2))

    @staticmethod
    def _daily_xianmeng_should_continue_low_score_sweep(
        snapshot: dict[str, Any],
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> tuple[bool, int | None]:
        """Force a weak-score sweep only after 13:00 when stamina exceeds 60.

        The report is a target-quality sample, not proof that the attack itself
        failed.  Before the afternoon overflow boundary, the Job keeps the
        existing twenty-minute defer behavior so teammates can remove the
        target's protection.  Only an authoritative, complete Runtime count
        may enable the forced batch path; an incomplete snapshot preserves the
        defensive defer behavior.
        """

        remaining = snapshot.get("attack_count")
        if (
            not snapshot.get("ok")
            or not snapshot.get("complete")
            or not isinstance(remaining, int)
        ):
            return False, None
        current = now or job_now()
        return current.hour >= 13 and remaining > 60, remaining

    @staticmethod
    def _daily_xianmeng_attempts_exhausted(numbers: list[int]) -> bool:
        """Fail closed when the stylized zero is not recognized as a digit."""

        return not numbers or min(numbers) <= 0

    def _ensure_daily_xianmeng_attack_options(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        """Synchronize #293 toggles from Runtime facts before an attack.

        The in-memory verification cache prevents repeated probes after both options
        have been verified for the current activity day/stage. A new process
        or next-day run safely performs one fresh verification.
        """

        now_day = datetime.now().astimezone().date().isoformat()
        verification_cache = getattr(self, "_daily_xianmeng_option_verification_cache", None)
        if isinstance(verification_cache, dict) and verification_cache.get("day") == now_day:
            return bool(verification_cache.get("triple_checked"))

        snapshot = self._read_daily_xianmeng_attack_options_snapshot()
        if not snapshot.get("ok") or not snapshot.get("complete"):
            raise RuntimeError("日常_仙盟：动态插桩未返回完整攻击配置")

        score = int(snapshot["score"])
        skip_threshold = int(snapshot.get("skip_score_threshold") or 1_000)
        triple_threshold = int(snapshot.get("triple_score_threshold") or 15_000)
        desired_skip = score >= skip_threshold
        desired_triple = score >= triple_threshold
        changed: list[str] = []

        if bool(snapshot.get("skip_checked")) != desired_skip:
            runtime.click_shape_center(293, "跳过")
            changed.append("跳过")
            yield from runtime.wait_action_settle(
                float(payload.get("option_settle_seconds") or 0.35)
            )
        if bool(snapshot.get("triple_checked")) != desired_triple:
            runtime.click_shape_center(293, "三连")
            changed.append("三连")
            yield from runtime.wait_action_settle(
                float(payload.get("option_settle_seconds") or 0.35)
            )

        verified = (
            self._read_daily_xianmeng_attack_options_snapshot()
            if changed
            else snapshot
        )
        if not verified.get("ok") or not verified.get("complete"):
            raise RuntimeError("日常_仙盟：按钮配置后的动态插桩复验不完整")
        if bool(verified.get("skip_checked")) != desired_skip:
            raise RuntimeError("日常_仙盟：#293[跳过] 状态复验失败，拒绝继续攻击")
        if bool(verified.get("triple_checked")) != desired_triple:
            raise RuntimeError("日常_仙盟：#293[三连] 状态复验失败，拒绝继续攻击")

        self._daily_xianmeng_last_option_snapshot = dict(verified)

        cycle_key = self._daily_xianmeng_option_cycle_key(verified)
        if desired_triple:
            self._daily_xianmeng_option_verification_cache = {
                "day": cycle_key[:10],
                "cycle_key": cycle_key,
                "triple_checked": True,
            }
        if changed:
            self._log(
                "success",
                f"日常_仙盟：积分 {score}，已配置并复验 {'、'.join(changed)}",
            )
        return desired_triple

    def _disable_daily_xianmeng_triple_for_tail(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        remaining_attempts: int,
    ):
        """Turn triple attack off so the final one or two attempts are consumed."""

        runtime.click_shape_center(293, "三连")
        yield from runtime.wait_action_settle(
            float(payload.get("option_settle_seconds") or 0.35)
        )
        verified = self._read_daily_xianmeng_attack_options_snapshot()
        if not verified.get("ok") or not verified.get("complete"):
            raise RuntimeError("日常_仙盟：尾数切换后的动态插桩复验不完整")
        if bool(verified.get("triple_checked")):
            raise RuntimeError("日常_仙盟：尾数不足三连时关闭[三连]失败")
        cycle_key = self._daily_xianmeng_option_cycle_key(verified)
        self._daily_xianmeng_option_verification_cache = {
            "day": cycle_key[:10],
            "cycle_key": cycle_key,
            "triple_checked": False,
        }
        self._log(
            "success",
            f"日常_仙盟：剩余 {remaining_attempts} 次，已关闭并复验三连，改用单攻清零",
        )
        return False

    def _record_daily_xianmeng_done(self, payload: dict[str, Any], *, message: str) -> str | None:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-xianmeng")
        next_time = self._daily_xianmeng_event_tail_next_time(payload)
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        suffix = f"，活动尾程下次 {next_time}" if next_time else "，未安排后续触发"
        self._log("success", f"日常_仙盟：{message}{suffix}")
        return next_time

    @staticmethod
    def _daily_xianmeng_event_tail_next_time(
        payload: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Resolve the authorized event-day final sweep or a legacy override."""

        current = now or job_now()
        if bool(payload.get("schedule_tail_from_daily_activity_list")):
            from backend.core.fanxiu.activity.daily_activity_discovery import (
                read_daily_activity_discovery_plan,
            )
            from backend.core.fanxiu.activity.daily_activity_job_registry import (
                next_xianmeng_challenge_tail_time,
            )

            try:
                plan = read_daily_activity_discovery_plan(
                    target_date=current.date(),
                    allow_discovery=False,
                    force_refresh=False,
                )
                return next_xianmeng_challenge_tail_time(plan, now=current)
            except (OSError, RuntimeError, ValueError):
                return None

        event_date = str(payload.get("event_tail_date") or "").strip()
        raw_times = payload.get("event_tail_times")
        if not event_date or not isinstance(raw_times, list):
            return None
        if event_date != current.date().isoformat():
            return None
        close_at = current.replace(hour=22, minute=0, second=0, microsecond=0)
        candidates: list[datetime] = []
        for raw_time in raw_times:
            try:
                hour, minute = (int(part) for part in str(raw_time).split(":", 1))
                candidates.append(
                    current.replace(hour=hour, minute=minute, second=0, microsecond=0)
                )
            except (TypeError, ValueError):
                continue
        future = sorted(item for item in candidates if current < item < close_at)
        return future[0].strftime("%Y-%m-%d %H:%M:%S") if future else None

    def _record_daily_xianmeng_immunity_cd(self, runtime: Any, payload: dict[str, Any]):
        try:
            snapshot = self._read_daily_xianmeng_immunity_snapshot()
        except Exception as exc:
            snapshot = {"ok": False, "complete": False, "reason": str(exc)}
        if snapshot.get("ok") and snapshot.get("complete"):
            cd_seconds = int(snapshot.get("cooldown_seconds") or 0)
            retry_seconds = max(0, cd_seconds)
            message = f"动态免战 CD 剩余 {cd_seconds} 秒"
        else:
            retry_seconds = int(payload.get("immunity_probe_retry_seconds") or 300)
            message = "动态免战 CD 暂不可用，按 5 分钟安全复查"
        next_time = self._schedule_daily_xianmeng_retry(
            payload,
            seconds=retry_seconds,
            message=message,
        )
        yield from self._return_daily_xianmeng_to_world(runtime)
        self._log("success", f"日常_仙盟：免战分支已回到 #34，工程调度时间 {next_time}")
        return next_time

    def _wait_daily_xianmeng_exact_view(self, runtime: Any, *scene_ids: int, timeout: float) -> int:
        start = time.monotonic()
        while True:
            self._raise_if_stopped(runtime.stop_event or threading.Event())
            self._clear_tick_frame(runtime.ctx)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, _frame = runtime.current_scene(
                scene_ids,
                update=True,
                handle_interruptions=True,
                include_popup_candidates=True,
            )
            if scene_id in scene_ids:
                with self._lock:
                    self._status.update({"current_scene": int(scene_id), "updated_at": time.time()})
                self._log(
                    "success",
                    f"日常_仙盟：Layer0 精确命中 #{int(scene_id)} {float(score or 0.0):.0f}%",
                )
                return int(scene_id)
            if time.monotonic() - start >= float(timeout):
                expected = "/".join(f"#{int(scene_id)}" for scene_id in scene_ids)
                raise TimeoutError(f"日常_仙盟：精确等待 {expected} 超时")

    def _return_daily_vip_to_world(
        self,
        runtime: Any,
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
            runtime.click_shape_center(scene_id, "返回")
            yield from runtime.wait_action_settle(settle_seconds)
            target = yield from runtime.wait_view(*target_ids, timeout=wait_timeout, label=f"{task_label}：等待返回 {'/'.join(f'#{target_id}' for target_id in target_ids)}")
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
                runtime.click_shape_center(20, "回到世界")
                yield from runtime.wait_action_settle(settle_seconds)
                yield from runtime.wait_view(34, timeout=wait_timeout, label=f"{task_label}：等待返回 #34")
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        timeout = float(payload.get("free_match_timeout_seconds") or 60.0)
        poll_seconds = float(payload.get("free_match_poll_seconds") or 1.0)
        threshold = float(payload.get("free_match_threshold") or self.overlay_threshold)
        start = time.monotonic()
        last_score = 0.0
        while time.monotonic() - start < timeout:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            last_score = float(runtime.shape_score(292, "免费", frame_data_url=frame) or 0.0)
            if last_score >= threshold:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：#292「免费」匹配 {last_score:.0f}%，点击领取",
                        phase="daily_vip_click_free",
                        current_scene=292,
                    )
                    self._log_locked("action", f"{task_label}：点击 #292「免费」")
                yield from runtime.wait_click(292, "免费", timeout=float(payload.get("free_click_timeout") or 8.0))
                yield from runtime.wait_action_settle(float(payload.get("free_click_settle_seconds") or 1.5))
                return "success"
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待 #292「免费」匹配，当前 {last_score:.0f}%",
                    phase="daily_vip_wait_free",
                    current_scene=292,
                )
            yield from runtime.wait_action_settle(max(0.2, poll_seconds))

        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：#292「免费」{timeout:.0f}s 未匹配，点击返回",
                phase="daily_vip_free_not_found",
                current_scene=292,
            )
            self._log_locked("skip", f"{task_label}：#292「免费」未匹配，最后分数 {last_score:.0f}%")
        runtime.click_shape_center(292, "返回")
        yield from runtime.wait_action_settle(float(payload.get("return_click_settle_seconds") or 1.5))
        return "skipped"
