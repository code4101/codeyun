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
            yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255)
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

        if self._daily_gongfeng_upgrade_page_visible(ctx, image254, frame):
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
        ctx: dict[str, Any],
        image254: dict[str, Any],
        frame: str | None = None,
    ) -> bool:
        upgrade_shape = self._find_shape(image254, "升级")
        if upgrade_shape is None:
            return False
        try:
            frame_data_url = frame or self._screencap(ctx)
            score = float(self._shape_score(ctx, image254, upgrade_shape, frame_data_url) or 0)
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
            yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255)
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
                if (yield from self._close_daily_gongfeng_item_detail_if_present(ctx, stop_event, image255)):
                    continue
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=0.8)
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
    ):
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [255])
        if scene_id != 255:
            return False
        blank = self._find_shape(image255, "空白")
        if blank is None:
            raise RuntimeError("日常_供奉：缺少 #255「空白」标注，无法关闭物品详情")
        self._log("action", "日常_供奉：关闭 #255 物品详情")
        self._click_shape(ctx, image255, blank, frame)
        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.2)
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
        blank254 = self._find_shape(image254, "空白")
        if blank254 is None:
            raise RuntimeError("日常_供奉：缺少 #254「空白」标注，无法返回法则页")
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
        frame = self._screencap(ctx)
        self._log("action", "日常_供奉：点击 #254「空白」")
        self._click_shape(ctx, image254, blank254, frame)
        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.2)
        yield from runtime.wait_click(256, "返回")
        try:
            yield from runtime.wait_view(252, 34, label="日常_供奉：等待回到 #252/#34")
        except Exception as exc:
            self._log("warning", f"日常_供奉：#256 返回后未确认 #252/#34：{exc}")
        return "success"

    def _daily_gongfeng_numbers(self, text: str) -> list[int]:
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        return [int(match) for match in re.findall(r"\d+", normalized)]

    def _daily_gongfeng_join_ocr_lines(self, lines: list[dict[str, Any]]) -> str:
        texts: list[str] = []
        for line in lines:
            text = str(line.get("text") or "").strip().translate(FULLWIDTH_DIGIT_TRANSLATION)
            if text:
                texts.append(text)
        return " ".join(texts)

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
                "home": runtime.view_visible(228, threshold=95.0),
            },
            settle_seconds=float(payload.get("purchase_click_settle_seconds") or 2.0),
            label=f"{task_label}：等待购买体力结果",
        )
        if result == "empty":
            return (yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label))
        if result == "home":
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

    def _daily_youli_purchase_remaining_count_from_frame(
        self,
        frame: str,
        image229: dict[str, Any],
    ) -> tuple[int | None, str]:
        local_text = self._ocr_text(self._ocr_lines_in_shapes(frame, image229, ("剩余限购次数",), padding=12))
        remaining = self._daily_youli_purchase_remaining_count(local_text)
        if remaining is not None:
            return remaining, local_text
        full_text = self._ocr_text(self._ocr_lines(frame))
        return self._daily_youli_purchase_remaining_count(full_text), full_text

    def _daily_youli_text_is_purchase_empty(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "游历符" in normalized and ("每日限购" in normalized or "增加购买次数" in normalized or "持有数量" in normalized)

    def _daily_youli_text_is_home(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "修仙传" not in normalized:
            return False
        if any(token in normalized for token in ("道祖逸闻", "幻境", "供奉", "机缘", "寻找机缘")):
            return False
        return (
            "游历" in normalized
            or "人界" in normalized
            or "灵界" in normalized
            or "魔界" in normalized
            or "仙界" in normalized
            or "探索完成" in normalized
        )

    def _daily_youli_text_is_region_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "探索进度" in normalized and ("快速游历" in normalized or "消耗体力" in normalized)

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
        yield from runtime.wait_any(
            {
                "scene": runtime.view_visible(target_scene_id, threshold=scene_threshold),
                "text": runtime.ocr_matches(text_predicate, label=f"{label} OCR"),
            },
            timeout=timeout,
            label=label,
        )
        scene_id, score, _frame = runtime.current_scene([target_scene_id])
        if scene_id != target_scene_id:
            scene_id, score = target_scene_id, 0.0
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
        blank_shape = self._find_shape(image233, "空白")
        if blank_shape is None:
            raise RuntimeError("日常_游历：缺少 #233「空白」标注，无法关闭限购提示")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image233,
            blank_shape,
            {},
            label=f"{task_label}：关闭购买次数不足提示",
        )
        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.0)
        self._log("success", f"{task_label}：已关闭购买次数不足提示")
        return "success"

    def _close_daily_youli_purchase_dialog(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image229: dict[str, Any],
        *,
        task_label: str,
    ):
        blank_shape = self._find_shape(image229, "空白")
        if blank_shape is None:
            self._log("warning", f"{task_label}：#229 缺少「空白」标注，无法主动关闭购买弹窗")
            return "missing"
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image229,
            blank_shape,
            {},
            label=f"{task_label}：关闭购买体力弹窗",
        )
        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.0)
        self._log("success", f"{task_label}：已关闭购买体力弹窗")
        return "success"

    def _wait_daily_youli_purchase_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image229: dict[str, Any],
        image233: dict[str, Any],
        *,
        timeout: float,
        label: str,
    ):
        del image229, image233
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            scene_id, score = self._identify_scene_number(ctx, frame, [233, 229, 228])
            last_scene_id, last_score = scene_id, score
            if scene_id == 229 or self._daily_youli_text_is_purchase(text):
                self._log("success", f"{label}：进入 #229 游历购买体力")
                return 229
            if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
                self._log("success", f"{label}：进入 #233 购买次数不足")
                return 233
            if scene_id == 228:
                self._log("success", f"{label}：购买页未打开，仍在 #228")
                return 228
            with self._lock:
                self._status.update({
                    "phase": "daily_youli_wait_purchase_result",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到 #229/#233，最后 {scene_text} {last_score:.0f}%，OCR={last_text[:120]}")

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
        use_shape = self._find_shape(image229, "购买并使用")
        if use_shape is None:
            raise RuntimeError("日常_游历：缺少 #229「购买并使用」标注，无法购买体力")
        max_count = int(payload.get("purchase_uses") or payload.get("buy_uses") or 99)
        clicked = 0
        while clicked < max_count:
            yield from self._wait_scene_id(
                ctx,
                stop_event,
                229,
                timeout=float(payload.get("purchase_timeout") or 10.0),
                label=f"{task_label}：等待购买体力 #229",
            )
            remaining: int | None = None
            text = ""
            read_start = time.monotonic()
            read_timeout = float(payload.get("purchase_remaining_timeout") or 5.0)
            while True:
                self._raise_if_stopped(stop_event)
                frame = self._screencap(ctx)
                remaining, text = self._daily_youli_purchase_remaining_count_from_frame(frame, image229)
                if remaining is not None:
                    break
                if time.monotonic() - read_start >= read_timeout:
                    break
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=0.5)
            if remaining is None:
                self._log("warning", f"{task_label}：未识别到剩余限购次数，停止购买，OCR={text[:120]}")
                yield from self._close_daily_youli_purchase_dialog(ctx, stop_event, image229, task_label=task_label)
                break
            if remaining <= 0:
                self._log("success", f"{task_label}：剩余限购次数为 0，停止购买")
                yield from self._close_daily_youli_purchase_dialog(ctx, stop_event, image229, task_label=task_label)
                break
            target_count = min(max_count, clicked + remaining)
            yield from self._click_shape_respecting_conditions(
                ctx,
                stop_event,
                image229,
                use_shape,
                payload,
                label=f"{task_label}：购买并使用 {clicked + 1}/{target_count}",
                timeout_key="purchase_click_timeout",
            )
            clicked += 1
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=float(payload.get("purchase_click_settle_seconds") or 1.2),
            )
            frame = self._screencap(ctx)
            _remaining_after, text = self._daily_youli_purchase_remaining_count_from_frame(frame, image229)
            self._log("detail", f"{task_label}：购买并使用 {clicked}/{target_count} 后 OCR={text[:120]}")
            scene_id, _score = self._identify_scene_number(ctx, frame, [233, 229])
            if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
                yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label)
                break
            if scene_id != 229 and not self._daily_youli_text_is_purchase(text):
                self._log("success", f"{task_label}：购买弹窗已关闭，停止购买")
                break
        if clicked > 0:
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, [229, 228, 233])
            text = self._ocr_text(self._ocr_lines(frame))
            if scene_id == 229 or self._daily_youli_text_is_purchase(text):
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
        yield from self._wait_daily_youli_home(
            ctx,
            stop_event,
            label="日常_游历：等待修仙传游历 #228",
        )
        search_shape = self._find_shape(image228, "检索区域")
        if search_shape is None:
            raise RuntimeError("日常_游历：缺少 #228「检索区域」标注，无法选择游历区域")
        frame = self._screencap(ctx)
        lines = self._ocr_lines(frame)
        box = self._box(search_shape, image228)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        candidates: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                candidates.append((cx, cy, text))
        if not candidates:
            raise RuntimeError("日常_游历：#228「检索区域」内未识别到可点击 OCR 文本")
        x, y, text = sorted(candidates, key=lambda item: (item[1], item[0]))[-1]
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：点击检索区域最后文本「{text}」",
                phase="daily_youli_click_last_region",
                current_scene=228,
            )
            self._log_locked("action", f"{task_label}：点击 #228 检索区域最后 OCR「{text}」")
        self._click_frame_point(ctx, image228, x, y)
        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=float(payload.get("region_click_settle_seconds") or 2.0))
        yield from self._wait_daily_youli_region_detail(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历区域详情 #236",
        )
        yield from self._click_daily_youli_quick_travel(ctx, stop_event, payload, image236, image237, task_label=task_label)
        return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))

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
        yield from runtime.wait_click(236, "快速游历")
        yield from runtime.wait_action_settle(float(payload.get("quick_travel_settle_seconds") or 2.0))
        self._log("success", f"{task_label}：已点击快速游历")
        yield from self._wait_daily_youli_quick_result(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历结果 #237",
        )
        return (yield from self._confirm_daily_youli_quick_result(ctx, stop_event, payload, image237, task_label=task_label))

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
            yield from self._wait_daily_youli_home(ctx, stop_event, label=f"{task_label}：等待修仙传游历 #228")
        else:
            yield from self._wait_daily_youli_home(ctx, stop_event, label=f"{task_label}：等待修仙传游历 #228")

        yield from runtime.wait_click(228, "返回")
        yield from runtime.wait_view(34, label=f"{task_label}：等待世界 #34")
        self._record_daily_entry_done(
            {},
            task_id="legacy-daily-youli",
            task_type="daily_youli",
            label=task_label,
            message="游历已完成并回到世界",
        )
        return "success"

    def _return_daily_youli_reward_recovery_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", f"{task_label}：从奖励找回页退出到世界", phase="daily_youli_reward_recovery_exit")
            self._log_locked("action", f"{task_label}：点击 #69「退出」关闭奖励找回页")
        yield from runtime.wait_click(69, "退出")
        yield from runtime.wait_action_settle(1.5)
        scene_id, _score, frame = runtime.current_scene([34, 69], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 69 or self._daily_youli_text_is_daily_page(text):
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从日常页退出到世界", phase="daily_youli_daily_exit")
                self._log_locked("action", f"{task_label}：奖励找回已关闭，点击 #69「退出」返回世界")
            yield from runtime.wait_click(69, "退出")
            yield from runtime.wait_action_settle(1.5)
        yield from runtime.wait_view(34, label=f"{task_label}：等待世界 #34")
        self._record_daily_entry_done(
            {},
            task_id="legacy-daily-youli",
            task_type="daily_youli",
            label=task_label,
            message="游历已完成并从奖励找回页回到世界",
        )
        return "success"

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
        scene_id, _score, frame = runtime.current_scene([34], update=True)
        text = runtime.ocr_text(frame)
        if not self._daily_xianshi_text_is_coin_list(text) and not self._daily_xianshi_text_is_box_detail(text):
            if scene_id != 34 and not self._daily_lingta_text_is_world_like(text):
                raise RuntimeError(f"{task_label}：当前不在可识别的世界或仙市页，无法开始")
            if scene_id != 34:
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
