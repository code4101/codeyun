"""游历业务：进入修仙传、次数处理、区域选择、快速游历和结果收尾。

任务入口为 _execute_daily_youli_task；日常列表的跨任务恢复仍在
DailyFoundationTaskMixin，通过本模块的游历结果确认和离场能力完成恢复。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pyxllib.autogui import View

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from ..ocr_spatial import group_ocr_tokens, locate_text_box, query_spatial_ocr
from ..ocr_values import FULLWIDTH_DIGIT_TRANSLATION

if TYPE_CHECKING:
    from ..game_context import BehaviorTreeContext


class DailyYouliTaskMixin:
    def _execute_daily_youli_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = {"max_scrolls": 12, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_游历资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)
        image71 = images.get(71)
        image228 = images.get(228)
        image229 = images.get(229)
        image233 = images.get(233)
        image236 = images.get(236)
        image237 = images.get(237)

        task_label = "日常_游历"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, text = yield from self._daily_youli_current_state(context, update=True)
        if self._daily_youli_text_is_reward_recovery(text):
            return (yield from self._return_daily_youli_reward_recovery_to_world(ctx, stop_event, task_label=task_label))
        if scene_id == 237 or self._daily_youli_text_is_quick_result(text):
            yield from self._confirm_daily_youli_quick_result(ctx, stop_event, payload, image237, task_label=task_label)
            return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
        if scene_id == 236 or self._daily_youli_text_is_region_detail(text):
            quick_status = yield from self._click_daily_youli_quick_travel(ctx, stop_event, payload, image236, image237, task_label=task_label)
            if quick_status == "success":
                return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
            if quick_status == "completed":
                yield from self._return_daily_youli_region_to_home(ctx, stop_event, payload, image236, task_label=task_label)
                yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
                return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
            return quick_status
        if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
            yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label)
            return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
        if scene_id == 229 or self._daily_youli_text_is_purchase(text):
            yield from self._click_daily_youli_purchase_uses(ctx, stop_event, payload, image229, image233, task_label=task_label)
            return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
        if scene_id == 71:
            yield from self._select_daily_youli_from_xiuxianzhuan_menu(ctx, stop_event, payload, image71, task_label=task_label)
            yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label="日常_游历：等待修仙传游历 #228")
            yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
            return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
        if scene_id == 228:
            yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
            return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, _score, frame, text = yield from self._daily_youli_current_state(context, update=True)
                if self._daily_youli_text_is_reward_recovery(text):
                    return (yield from self._return_daily_youli_reward_recovery_to_world(ctx, stop_event, task_label=task_label))
                if scene_id == 237 or self._daily_youli_text_is_quick_result(text):
                    yield from self._confirm_daily_youli_quick_result(ctx, stop_event, payload, image237, task_label=task_label)
                    return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
                if scene_id == 236 or self._daily_youli_text_is_region_detail(text):
                    quick_status = yield from self._click_daily_youli_quick_travel(ctx, stop_event, payload, image236, image237, task_label=task_label)
                    if quick_status == "success":
                        return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
                    if quick_status == "completed":
                        yield from self._return_daily_youli_region_to_home(ctx, stop_event, payload, image236, task_label=task_label)
                        yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
                        return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
                    return quick_status
                if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
                    yield from self._close_daily_youli_purchase_empty(ctx, stop_event, image233, task_label=task_label)
                    return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
                if scene_id == 229 or self._daily_youli_text_is_purchase(text):
                    yield from self._click_daily_youli_purchase_uses(ctx, stop_event, payload, image229, image233, task_label=task_label)
                    return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
                if scene_id == 71:
                    yield from self._select_daily_youli_from_xiuxianzhuan_menu(ctx, stop_event, payload, image71, task_label=task_label)
                    yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label="日常_游历：等待修仙传游历 #228")
                    yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
                    return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
                if scene_id == 228:
                    yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
                    return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
            if scene_id != 69:
                if scene_id == 34 and (
                    yield from self._try_enter_daily_youli_from_world_mainline(
                        ctx,
                        context,
                        stop_event,
                        payload,
                        image34,
                        image228,
                        task_label=task_label,
                    )
                ):
                    _wait_scene_match = yield from context.wait_scene([71, 228], wait=5.0, required=False)
                    (scene_id, _score, frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                    if scene_id == 71:
                        yield from self._select_daily_youli_from_xiuxianzhuan_menu(ctx, stop_event, payload, image71, task_label=task_label)
                        yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label="日常_游历：等待修仙传游历 #228")
                    yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
                    return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
                scene_id = yield from self._enter_daily_from_world_like(
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
            title_pattern=r"游\s*历|修\s*仙\s*.?传|修\s*仙.*历|传\s*.?游",
            progress_can_mark_done=False,
        )
        if daily_status == "done":
            raise RuntimeError(f"{task_label}：日常列表进度不能作为游历完成证据")
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-youli",
                task_type="daily_youli",
                label=task_label,
                entry_label="修仙传游历",
            )
            return "skipped"

        yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label="日常_游历：等待修仙传游历 #228")
        yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
        return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))

    def _daily_youli_current_state(self, context: Any, *, update: bool = False) -> tuple[int | None, float, str, str]:
        _wait_scene_match = yield from context.wait_scene([237, 236, 233, 229, 228, 71, 69, 34], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id is None:
            scene_id, score, frame = context.recognize_scene_in_frame(frame_data_url=frame)
        return scene_id, float(score), frame, context.ocr_text(frame)

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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._wait_daily_youli_home(
            ctx,
            stop_event,
            label="日常_游历：等待修仙传游历 #228",
        )
        landed = yield from context.wait_click_then_scene(
            228, "购买", 229, 233,
            timeout=float(payload.get("purchase_dialog_timeout") or 20.0),
            label=f"{task_label}：等待购买体力弹窗",
        )
        landed_id = getattr(landed, "scene_id", getattr(landed, "id", landed))
        if landed_id == 233:
            return (yield from self._close_daily_youli_purchase_empty_and_wait_home(
                ctx, stop_event, image233, task_label=task_label,
            ))
        if landed_id != 229:
            # The source page can still be visible while the dialog opens.
            # It proves neither a completed purchase nor absence of a dialog.
            raise RuntimeError(f"{task_label}：购买后未确认 #229/#233，当前 #{landed_id}")
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
            timeout=timeout,
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
            timeout=timeout,
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
            timeout=timeout,
            label=label,
        ))

    def _wait_daily_youli_scene_or_text(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        target_scene_id: int,
        text_predicate: Callable[[str], bool],
        *,
        timeout: float | None,
        label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_threshold = 95.0 if int(target_scene_id) == 228 else 80.0
        _result, scene_id, score = yield from context.wait_scene_or_ocr(
            target_scene_id,
            text_predicate,
            view_threshold=scene_threshold,
            timeout=timeout,
            label=label,
        )
        if int(target_scene_id) == 228 and _result != "text":
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
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
                yield from context.wait_any(
                    {
                        "text": context.ocr_matches(
                            text_predicate,
                            label=f"{label} OCR确认",
                            preview_chars=120,
                        )
                    },
                    timeout=timeout,
                    label=f"{label}：确认游历菜单已选中",
                )
                _wait_scene_match = yield from context.wait_scene([target_scene_id], wait=5.0, required=False)
                (scene_id, score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from context.wait_click(233, "空白", label=f"{task_label}：关闭购买次数不足提示")
        yield from context.wait_action_settle(1.0)
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from context.wait_click(229, "空白", label=f"{task_label}：关闭购买体力弹窗")
        yield from context.wait_action_settle(1.0)
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_count = int(payload.get("purchase_uses") or payload.get("buy_uses") or 99)
        clicked = 0
        while clicked < max_count:
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
            if self._daily_youli_text_is_purchase_empty(text):
                yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
                self._log("success", f"{task_label}：购买终止于 #233，已回到 #228")
                return "success"
            if not self._daily_youli_text_is_purchase(text):
                result = yield from context.wait_any(
                    {
                        "purchase": context.ocr_matches(
                            self._daily_youli_text_is_purchase,
                            label=f"{task_label}：等待购买体力 #229 OCR",
                            preview_chars=120,
                        ),
                        "empty": context.ocr_matches(
                            self._daily_youli_text_is_purchase_empty,
                            label=f"{task_label}：等待购买次数不足 OCR",
                            preview_chars=120,
                        ),
                    },
                    timeout=float(payload.get("purchase_timeout") or 10.0),
                    interval=float(payload.get("purchase_wait_interval_seconds") or 0.25),
                    label=f"{task_label}：等待购买弹窗结果",
                )
                frame = context.cur_frame(update=True)
                text = context.ocr_text(frame)
                if result == "empty" or self._daily_youli_text_is_purchase_empty(text):
                    yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
                    self._log("success", f"{task_label}：购买终止于 #233，已回到 #228")
                    return "success"
            remaining: int | None = None
            read_start = time.monotonic()
            read_timeout = float(payload.get("purchase_remaining_timeout") or 5.0)
            while True:
                self._raise_if_stopped(stop_event)
                numbers, text = context.ocr_numbers_in_shapes(229, ("剩余限购次数",), padding=12)
                remaining = numbers[-1] if numbers else None
                if remaining is not None:
                    break
                if time.monotonic() - read_start >= read_timeout:
                    break
                yield from context.wait_action_settle(0.5)
            if remaining is None:
                self._log("warning", f"{task_label}：未识别到剩余限购次数，继续购买直到 #233 终止态，OCR={text[:120]}")
            target_count = min(max_count, clicked + max(1, remaining or 1))
            yield from context.wait_click(229, "购买并使用")
            clicked += 1
            yield from context.wait_action_settle(float(payload.get("purchase_click_settle_seconds") or 1.2))
            _wait_scene_match = yield from context.wait_scene([233, 229], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            self._log("detail", f"{task_label}：购买并使用 {clicked}/{target_count} 后 OCR={text[:120]}")
            if scene_id == 233 or self._daily_youli_text_is_purchase_empty(text):
                yield from self._close_daily_youli_purchase_empty_and_wait_home(ctx, stop_event, image233, task_label=task_label)
                self._log("success", f"{task_label}：购买并使用完成 {clicked}，已到 #233 并回到 #228")
                return "success"
            if scene_id != 229 and not self._daily_youli_text_is_purchase(text):
                self._log("success", f"{task_label}：购买弹窗已关闭，停止购买")
                return "success"
        if clicked > 0:
            _wait_scene_match = yield from context.wait_scene([229, 228, 233], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([236, 228], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
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
        candidates = context.ocr_centers_in_shape(228, "检索区域", include=())
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
        context.click_frame_point(228, x, y)
        yield from context.wait_action_settle(float(payload.get("region_click_settle_seconds") or 2.0))
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._wait_daily_youli_region_detail(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历区域详情 #236",
        )
        context.click_shape_center(236, "快速游历")
        yield from context.wait_action_settle(float(payload.get("quick_travel_settle_seconds") or 2.0))
        self._log("success", f"{task_label}：已点击快速游历")
        result = yield from context.wait_any(
            {
                "result": context.ocr_matches(
                    self._daily_youli_text_is_quick_result,
                    label=f"{task_label}：等待游历结果 #237 OCR",
                    preview_chars=120,
                ),
                "completed": context.ocr_matches(
                    self._daily_youli_text_is_region_completed,
                    label=f"{task_label}：等待游历区域已完成 OCR",
                    preview_chars=120,
                ),
                "resource_empty": context.ocr_matches(
                    self._daily_youli_text_is_purchase_empty,
                    label=f"{task_label}：等待快速游历资源不足 OCR",
                    preview_chars=120,
                ),
            },
            timeout=float(payload.get("quick_result_timeout") or context.default_wait_condition_timeout),
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from context.wait_click(236, "返回")
        result = yield from context.wait_any(
            {
                "home": context.ocr_matches(
                    self._daily_youli_text_is_home,
                    label=f"{task_label}：等待返回修仙传游历 #228 OCR",
                    preview_chars=120,
                ),
                "world_scene": context.scene_visible(34),
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
            context,
            stop_event,
            payload,
            image34,
            image228,
            task_label=task_label,
        )
        if not entered:
            raise RuntimeError(f"{task_label}：区域返回落到世界，主线快路径未能重新进入游历")
        _wait_scene_match = yield from context.wait_scene([71, 228], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._wait_daily_youli_quick_result(
            ctx,
            stop_event,
            label=f"{task_label}：等待游历结果 #237",
        )
        yield from context.wait_click(237, "确定")
        yield from context.wait_action_settle(float(payload.get("quick_result_confirm_settle_seconds") or 2.0))
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([236, 228, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if self._daily_youli_text_is_reward_recovery(text):
            return (yield from self._return_daily_youli_reward_recovery_to_world(ctx, stop_event, task_label=task_label))
        if scene_id == 34:
            self._log("success", f"{task_label}：已在世界 #34")
            return "success"
        if scene_id == 236 or self._daily_youli_text_is_region_detail(text):
            yield from context.wait_click(236, "返回")
            yield from self._wait_daily_youli_home(
                ctx,
                stop_event,
                timeout=18.0,
                label=f"{task_label}：等待 #236 返回到修仙传游历 #228",
            )
        else:
            yield from self._wait_daily_youli_home(ctx, stop_event, label=f"{task_label}：等待修仙传游历 #228")

        yield from context.wait_click(228, "返回")
        result = yield from context.wait_any(
            {
                "world_scene": context.scene_visible(34),
                "daily_text": context.ocr_matches(
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        result = "daily_text"
        for attempt in range(2):
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从日常页退出到世界", phase="daily_youli_daily_exit")
                suffix = "" if attempt == 0 else f"重试 {attempt + 1}/2，"
                self._log_locked("action", f"{task_label}：{suffix}点击 #69「退出」返回世界")
            context.click_shape_center(69, "退出")
            yield from context.wait_action_settle(1.5)
            result = yield from context.wait_any(
                {
                    "world_scene": context.scene_visible(34),
                    "daily_text": context.ocr_matches(
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        frame = context.cur_frame(update=True)
        text = context.ocr_text(frame)
        if not self._daily_youli_text_is_reward_recovery(text):
            yield from context.wait_any(
                {
                    "reward_recovery": context.ocr_matches(
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
        context.click_shape_center(69, "退出")
        yield from context.wait_action_settle(1.5)
        result = yield from context.wait_any(
            {
                "world_scene": context.scene_visible(34),
                "daily_text": context.ocr_matches(
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

    def _try_enter_daily_youli_from_world_mainline(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
        payload: dict[str, Any],
        image34: dict[str, Any],
        image228: dict[str, Any],
        *,
        task_label: str,
    ):
        if not bool(payload.get("youli_mainline_shortcut", True)):
            return False
        mainline_shape = self._find_shape(image34, "主线")
        if mainline_shape is None:
            self._log("detail", f"{task_label}：#34 缺少「主线」标注，跳过快路径")
            return False
        with self._lock:
            self._set_status_locked("running", f"{task_label}：尝试 #34「主线」快路径", phase="daily_youli_mainline_shortcut", current_scene=34)
            self._log_locked("action", f"{task_label}：点击 #34「主线」尝试直达修仙传游历")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image34,
            mainline_shape,
            payload,
            label=f"{task_label}：点击 #34「主线」",
            timeout_key="youli_mainline_click_timeout",
        )
        yield from self._wait_action_settle(
            ctx,
            stop_event,
            seconds=float(payload.get("youli_mainline_settle_seconds") or 2.0),
        )
        try:
            view = yield from context.wait_scene(
                [228,
                71],
                wait=float(payload.get("youli_mainline_wait_timeout") or 12.0),
                label=f"{task_label}：等待主线快路径进入修仙传",
            )
        except TimeoutError as exc:
            self._log("warning", f"{task_label}：主线快路径未进入修仙传，回退日常入口：{exc}")
            return False
        scene_id = view.id if isinstance(view, View) else int(view) if isinstance(view, int) else None
        if scene_id == 228:
            yield from self._select_daily_youli_tab_from_menu_if_visible(ctx, stop_event, payload, image228, task_label=task_label)
        _wait_scene_match = yield from context.wait_scene([228, 71], label=f'{task_label}：复核主线快路径落点', wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = self._recognized_scene_ocr_text(ctx, frame, [228, 71])
        if scene_id == 71:
            self._log("success", f"{task_label}：主线快路径进入修仙传菜单")
            return True
        if self._daily_youli_text_is_home(text):
            self._log("success", f"{task_label}：主线快路径进入修仙传游历")
            return True
        self._log("warning", f"{task_label}：主线快路径落点不是游历页，回退日常入口，OCR={text[:120]}")
        return False

    def _select_daily_youli_tab_from_menu_if_visible(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image228: dict[str, Any],
        *,
        task_label: str,
    ):
        menu_shape = self._find_shape(image228, "菜单")
        if menu_shape is None:
            self._log("detail", f"{task_label}：#228 缺少「菜单」区域，跳过游历菜单选择")
            return False
        try:
            context = self._behavior_tree_context(ctx, stop_event=stop_event)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：选择 #228[菜单/游历]", phase="daily_youli_select_menu", current_scene=228)
                self._log_locked("action", f"{task_label}：wait_click #228[菜单/游历]")
            yield from context.wait_click(
                228,
                "[菜单/游历]",
                timeout=float(payload.get("youli_menu_click_timeout") or payload.get("shape_click_timeout") or 8.0),
            )
            yield from self._wait_action_settle(
                ctx,
                stop_event,
                seconds=float(payload.get("youli_menu_settle_seconds") or 1.0),
            )
            return True
        except RuntimeError as exc:
            self._log("detail", f"{task_label}：#228[菜单/游历] 通用点击未命中，保留当前页：{exc}")
            return False

    def _select_daily_youli_from_xiuxianzhuan_menu(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image71: dict[str, Any] | None,
        *,
        task_label: str,
    ):
        if not isinstance(image71, dict):
            raise RuntimeError(f"{task_label}：缺少 #71「修仙传」标注，无法从菜单选择游历")
        frame = self._screencap(ctx)
        tokens = self._ocr_tokens_in_scene_shapes(ctx, frame, image71)
        fragments = group_ocr_tokens(tokens)
        width, height = self._frame_size(image71)
        min_y = height * float(payload.get("youli_menu_min_y_ratio") or 0.62)
        candidates: list[tuple[float, float, str]] = []
        for fragment in fragments:
            text = _sanitize_ocr_text(fragment.get("text"))
            if "游历" not in text:
                continue
            target_box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], "游历")
            if target_box is None:
                continue
            cx = float(target_box["x"]) + float(target_box["w"]) / 2
            cy = float(target_box["y"]) + float(target_box["h"]) / 2
            if cy >= min_y:
                candidates.append((cx, cy, text))
        if not candidates:
            raise RuntimeError(f"{task_label}：#71 菜单未识别到「游历」入口")
        x, y, text = sorted(candidates, key=lambda item: (item[1], item[0]))[0]
        with self._lock:
            self._set_status_locked("running", f"{task_label}：从 #71 菜单选择「{text}」", phase="daily_youli_select_xiuxianzhuan_menu", current_scene=71)
            self._log_locked("action", f"{task_label}：点击 #71 菜单 OCR「{text}」")
        self._click_frame_point(ctx, image71, x, y)
        yield from self._wait_action_settle(
            ctx,
            stop_event,
            seconds=float(payload.get("youli_menu_settle_seconds") or 1.5),
        )
        return True
