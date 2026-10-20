"""游历业务：进入修仙传、次数处理、区域选择、快速游历和结果收尾。

任务入口为 _execute_daily_youli_task；日常列表的跨任务恢复仍在
DailyFoundationTaskMixin，通过本模块的游历结果确认和离场能力完成恢复。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from pyxllib.autogui import View

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from ..ocr_spatial import group_ocr_tokens, locate_text_box, query_spatial_ocr
from ..ocr_values import FULLWIDTH_DIGIT_TRANSLATION

if TYPE_CHECKING:
    from ..game_context import BehaviorTreeContext


def resolve_youli_purchase_limit(payload: dict[str, Any]) -> int:
    """解析明确的购买次数策略；缺省和显式零都禁止购买。

    purchase_uses 优先于兼容字段 buy_uses，包括其值为零时。拒绝布尔值、
    小数和负数，避免配置被隐式转成消费次数。正数仍须由调用方提供预算。
    """
    value = payload.get("purchase_uses", payload.get("buy_uses", 0))
    if type(value) is int:
        limit = value
    elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        limit = int(value.strip())
    else:
        raise ValueError("游历购买上限必须是明确的非负整数")
    if limit < 0:
        raise ValueError("游历购买次数上限不能为负数")
    return limit


def inspect_youli_region_resources(context):
    """观察已打开区域的实时资源与原生身份，不点击游历或购买。

    真元会自然恢复，使用详情页本轮截图的现有/单次消耗，不使用旧
    库存值。两次原生观察须保持区域、快速模式和进程一致；返回可负担
    次数仅为资源计算，不是日常已完成次数，也不构成付费授权。
    """
    from backend.core.fanxiu.instrumentation.youli import read_youli_region_dialog_snapshot

    match = yield from context.wait_scene_exact([236], timeout=20)
    before = read_youli_region_dialog_snapshot()
    text = context.ocr_text_in_shapes(
        236, ["现有体力与单次消耗"], crop=True, padding=0,
        frame_data_url=match.frame_data_url,
    )
    ratios = re.findall(r"(\d+)\s*[/／]\s*(\d+)", text)
    after = read_youli_region_dialog_snapshot()
    if (before.get("complete") is not True or after != before
            or len(ratios) != 1 or int(ratios[0][1]) <= 0):
        raise RuntimeError("游历区域身份或现有资源不明确，保留现场")
    available, cost = map(int, ratios[0])
    return {"complete": True, "read_only": True, "region": after,
            "available_energy": available, "energy_per_travel": cost,
            "affordable_travels": available // cost,
            "resource_consumption": 0}


def plan_youli_natural_next_time(now: datetime, *, daily_target_met: bool,
                                daily_time: str = "08:00", retry_seconds: int = 600) -> str:
    """日常达标排到次日；未达标按业务延期，不使用技术错误重试。"""
    start = datetime.strptime(daily_time, "%H:%M").time()
    if type(retry_seconds) is not int or retry_seconds < 60:
        raise ValueError("游历资源等待至少延期60秒")
    next_at = (now + timedelta(days=1)).replace(
        hour=start.hour, minute=start.minute, second=0, microsecond=0,
    ) if daily_target_met else now + timedelta(seconds=retry_seconds)
    return next_at.strftime("%Y-%m-%d %H:%M:%S")


YOULI_DAILY_TASK_ID = 1004


def youli_daily_target_complete(snapshot: dict[str, Any]) -> bool:
    """只接受 Quest 1004 的完整原生事实；缺失或矛盾不能当作达标。

    2026-10-15 实机：日常列表 OCR 复核在第 12 屏误判“完成修仙传游历”
    已达标，Runtime 同一时刻为 17/18。列表 OCR 仅用于导航，达标只看
    QuestMgr 的 targetTurn/turn 与 finishTasks。
    """
    if (snapshot.get("complete") is not True
            or snapshot.get("task_id") != YOULI_DAILY_TASK_ID
            or not (snapshot.get("present") is True or snapshot.get("done") is True)):
        raise RuntimeError("日常_游历：日常任务1004事实未完整加载，不能判断达标")
    if snapshot.get("done") is not True:
        turn, target = snapshot.get("turn"), snapshot.get("target_turn")
        if (type(turn) is not int or type(target) is not int
                or turn < 0 or target <= 0 or turn > target):
            raise RuntimeError("日常_游历：日常任务1004进度不完整或矛盾")
    return snapshot.get("done") is True


def execute_youli_natural_daily_job(runner, context, payload, stop_event):
    """按 Quest 1004 原生进度执行免费游历；结果确认、资源等待与下次调度闭环。

    达标只由 Runtime 日常任务事实证明，不代表全部探索奖励已领完。
    未达标时经日常列表进入并复用游历业务，只用已有真元。批次结束
    重新读取 Quest 1004，不能用点击成功、返回世界或列表 OCR 代替达标。
    """
    from backend.core.fanxiu.data_annotation.effective_time import job_now
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    from backend.core.fanxiu.instrumentation.daily_task import read_daily_task_snapshot

    if resolve_youli_purchase_limit(payload) != 0:
        raise ValueError("自然真元日常作业不接受购买次数")

    def observe_daily_target():
        snapshot = read_daily_task_snapshot(YOULI_DAILY_TASK_ID)
        return youli_daily_target_complete(snapshot), snapshot

    met, before = observe_daily_target()
    after = before
    operation = "not_needed"
    if not met:
        yield from context.enter_daily_list_direct(label="游历日常入口")
        status = yield from context.open_daily_entry(
            label="游历日常入口", title_pattern=r"完成修仙传游历|修仙传\s*游历",
            progress_can_mark_done=False,
        )
        if status != "open":
            raise RuntimeError(f"游历日常入口未打开：{status}，保留现场")
        yield from context.wait_scene_exact([228], timeout=25)
        operation = yield from runner._execute_daily_youli_task(
            context.ctx, stop_event, {**payload, "purchase_uses": 0},
        )
        if operation not in {"success", "skipped"}:
            raise RuntimeError(f"免费游历批次未形成有效结果：{operation}")
        met, after = observe_daily_target()
    yield from context.go_scene(34)
    yield from context.wait_scene_exact([34], timeout=20)
    next_time = plan_youli_natural_next_time(
        job_now(), daily_target_met=met,
        daily_time=payload.get("daily_time", "08:00"),
        retry_seconds=payload.get("retry_seconds", 600),
    )
    task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-youli")
    runner._persist_scheduler_task_next_time(task_id, next_time)
    receipt = {"outcome": "daily_target_met" if met else "waiting_for_natural_energy",
               "daily_target_proven": met, "batch_result": operation,
               "daily_task_id": YOULI_DAILY_TASK_ID,
               "progress_before": [before.get("turn"), before.get("target_turn")],
               "progress_after": [after.get("turn"), after.get("target_turn")],
               "evidence": after.get("evidence"),
               "paid_purchases": 0, "world_scene": 34, "next_time": next_time}
    record_world_discovery("youli_natural_daily_job", receipt)
    runner._log("success", f"日常_游历：{receipt['outcome']}"
                f" {receipt['progress_after'][0]}/{receipt['progress_after'][1]}，下次 {next_time}")
    return receipt


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
        purchase_limit = resolve_youli_purchase_limit(payload)
        if purchase_limit == 0:
            self._log("detail", f"{task_label}：购买上限为0，不打开购买弹窗")
            return "success"
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
            # #228 shapes OCR only the title, region row and menu; the 仙界
            # tab row lies outside them, so its region names are the proof.
            or re.search(r"[一-鿿]{2}仙[域区]", normalized) is not None
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
        # Zero is a real no-purchase policy, not a missing configuration.
        # In particular, never turn an explicit zero into 99 paid clicks.
        max_count = resolve_youli_purchase_limit(payload)
        if max_count == 0:
            frame = context.cur_frame(update=True)
            if self._daily_youli_text_is_purchase_empty(context.ocr_text(frame)):
                yield from self._close_daily_youli_purchase_empty_and_wait_home(
                    ctx, stop_event, image233, task_label=task_label)
            else:
                yield from self._close_daily_youli_purchase_dialog(
                    ctx, stop_event, image229, task_label=task_label)
            self._log("detail", f"{task_label}：购买上限为0，只使用现有体力")
            return "success"
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
        if quick_status == "success":
            return (yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=task_label))
        if quick_status != "completed":
            return quick_status
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
        resources = yield from inspect_youli_region_resources(context)
        region = resources["region"]
        if not (region["region_open"] and region["quick_enabled"] and region["quick_selected"]):
            raise RuntimeError(f"{task_label}：区域或快速模式未核实，保留现场")
        if resources["affordable_travels"] == 0:
            from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
            record_world_discovery("youli_resource_wait", {
                **resources, "outcome": "waiting_for_natural_energy",
                "daily_completion_proven": False,
            })
            self._log("skip", f"{task_label}：现有真元 {resources['available_energy']}"
                      f" < 单次消耗 {resources['energy_per_travel']}，等待自然恢复，未游历")
            return "skipped"
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
        # Leaving one batch does not prove the daily target. Scheduling belongs
        # to the Job wrapper, after its fresh business completion observation.
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
