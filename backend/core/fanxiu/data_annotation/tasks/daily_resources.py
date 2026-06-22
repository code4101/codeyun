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

from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.runtime_runner import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
)


class DailyResourceTaskMixin:
    _daily_default_wait_condition_timeout = 12.0

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
        runtime.set_completion_message(f"{task_label}完成，接受 {accepted} 次，升级 {upgraded} 次，已回到世界")

    def _accept_daily_gongfeng_until_done(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image252: dict[str, Any],
        runtime: FanxiuRuntime,
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
        runtime: FanxiuRuntime,
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
        runtime: FanxiuRuntime,
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
        matches = list(re.finditer(r"(\d{1,12})\s*/\s*(\d{1,8})", normalized))
        if not matches:
            return None
        match = matches[-1]
        current_text, required_text = match.group(1), match.group(2)
        current = int(current_text)
        required = int(required_text)
        if required > 0 and current > required and len(current_text) > len(required_text):
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
        runtime: FanxiuRuntime,
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
        return [int(match) for match in re.findall(r"\d+", normalized)]

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
                "world_text": runtime.ocr_matches(
                    self._daily_lingta_text_is_world_like,
                    label=f"{task_label}：等待返回世界 OCR",
                    preview_chars=120,
                ),
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
                "world_text": runtime.ocr_matches(
                    self._daily_lingta_text_is_world_like,
                    label=f"{task_label}：等待 #228 返回后世界 OCR",
                    preview_chars=120,
                ),
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
                    "world_text": runtime.ocr_matches(
                        self._daily_lingta_text_is_world_like,
                        label=f"{task_label}：等待日常退出后世界 OCR",
                        preview_chars=120,
                    ),
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
                "world_text": runtime.ocr_matches(
                    self._daily_lingta_text_is_world_like,
                    label=f"{task_label}：等待奖励找回关闭后世界 OCR",
                    preview_chars=120,
                ),
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
        return {"result": "skipped", "message": message}

    def _execute_daily_xianshi_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_仙市资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image247 = images.get(247)
        image248 = images.get(248)
        image249 = images.get(249)
        image250 = images.get(250)

        task_label = "日常_仙市"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([34, 69], update=True)
        text = runtime.ocr_text(frame)
        if not self._daily_xianshi_text_is_coin_list(text) and not self._daily_xianshi_text_is_box_detail(text):
            if scene_id != 34 and not self._daily_lingta_text_is_world_like(text):
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=task_label,
                )
                if scene_id == 69:
                    yield from runtime.goto_view(34)
                    yield from runtime.wait_view(34, label=f"{task_label}：等待世界 #34")
            elif scene_id != 34:
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
