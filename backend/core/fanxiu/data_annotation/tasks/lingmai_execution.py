"""灵脉任务的执行流程；策略选择由 lingmai 领域能力提供。

通过执行器组合取得日志、调度和日常导航能力；本模块不反向导入执行器。
入口和完成判据在同一业务模块内，其他玩法无需理解这里的页面细节。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.fanxiu.data_annotation.game_context import BehaviorTreeContext

import re
import threading
import time
from datetime import (
    datetime,
    time as time_cls,
    timedelta,
)
from pathlib import Path
from typing import (
    Any,
    Literal,
    Mapping,
)

from pyxllib.autogui import (
    Shape,
    View,
)

from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.runtime_gui.scroll import DEFAULT_SCROLL_UNCHANGED_THRESHOLD
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    group_ocr_tokens,
    locate_text_box,
    query_spatial_ocr,
)
from backend.core.fanxiu.runtime_gui import (
    DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
    normalize_ocr_name,
    ocr_name_similarity,
)
from backend.core.fanxiu.data_annotation.job_times import clip_daily_retry_to_window
from backend.core.fanxiu.data_annotation.tasks.lingmai import (
    LINGMAI_SHENGMAI_MIN_STRENGTH,
    LINGMAI_UNION_SHENGMAI_ROOM_ID,
    LINGMAI_UNION_SHENMAI_ROOM_ID,
    lingmai_facts_retry_seconds,
    lingmai_name_variants,
    refresh_and_select_lingmai_seat_action,
    refresh_lingmai_daily_status,
    select_visible_lingmai_target,
)
from backend.core.fanxiu.data_annotation.state import parse_data_annotation_daily_clock


class _DailyLingmaiKickTargetLost(RuntimeError):
    """Raised when a Runtime-selected Lingmai target cannot be trusted in #286 GUI OCR."""


_DAILY_LINGMAI_ENTRY_PATTERN = (
    r"参\s*与?.*灵\s*脉.*(?:争|夺).*?(?:1|一)?.*?(?:小\s*时|时)?|"
    r"灵\s*脉.*(?:争|夺)|灵\s*脉"
)


class LingmaiTaskMixin:
    def _record_daily_lingmai_done(self, payload: dict[str, Any], *, message: str) -> str:
        now = job_now()
        start_clock = parse_data_annotation_daily_clock(payload.get("daily_start_time") or "17:30") or time_cls(17, 30)
        next_time = datetime.combine(now.date() + timedelta(days=1), start_clock).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lingmai-seat"),
            next_time,
        )
        self._log("success", f"灵脉_座位：{message}，下次 {next_time}")
        return next_time

    def _record_daily_lingmai_resource_insufficient(
        self,
        payload: dict[str, Any],
        *,
        message: str,
    ) -> dict[str, Any]:
        """Close today's seat attempt when the game proves the consumable is gone."""

        now = job_now()
        start_clock = parse_data_annotation_daily_clock(payload.get("daily_start_time") or "17:30") or time_cls(17, 30)
        next_time = datetime.combine(now.date() + timedelta(days=1), start_clock).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lingmai-seat"),
            next_time,
        )
        self._log("error", f"灵脉_座位：{message}，本日无法入座，下次 {next_time}")
        return {
            "ok": False,
            "outcome": "resource_insufficient",
            "message": message,
        }

    def daily_lingmai_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        start_clock = parse_data_annotation_daily_clock(payload.get("daily_start_time") or "17:30") or time_cls(17, 30)
        end_clock = parse_data_annotation_daily_clock(payload.get("daily_end_time") or "22:00") or time_cls(22, 0)
        decision = self._daily_window_admission(
            now=job_now(),
            trigger=start_clock,
            cutoff=end_clock,
            label="灵脉_座位",
            window_text=f"{start_clock.strftime('%H:%M')}-{end_clock.strftime('%H:%M')}",
        )
        return self._persist_admission_decision(payload, decision)

    def _execute_daily_lingmai_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        max_idle_room_recoveries = max(
            0,
            min(
                3,
                int(payload.get("lingmai_idle_room_recovery_attempts") or 2),
            ),
        )
        for recovery_index in range(max_idle_room_recoveries + 1):
            result = yield from self._run_daily_lingmai_task(
                ctx,
                stop_event,
                payload,
            )
            if result != "retry_idle_room":
                break
            if recovery_index >= max_idle_room_recoveries:
                raise RuntimeError(
                    "灵脉_座位：连续恢复空闲 #588 房间达到上限，已回到稳定入口"
                )
            self._log(
                "info",
                "灵脉_座位：驱离事务未创建，已离开 #588；"
                f"从正式业务入口重新读取座位并重选（{recovery_index + 1}/"
                f"{max_idle_room_recoveries}）",
            )
        if result == "success":
            next_time = self._record_daily_lingmai_done(payload, message="本日座位流程已完成")
            return {
                "result": "success",
                "message": f"灵脉_座位：本日座位流程已完成，下次 {next_time}",
            }
        if result == "skipped":
            message = str(
                payload.get("__lingmai_terminal_message")
                or "本次已安全结束并写入后续检查时间"
            )
            next_time = str(payload.get("__lingmai_terminal_next_time") or "")
            return {
                "result": "skipped",
                "message": f"灵脉_座位：{message}" + (f"，{next_time} 重试" if next_time else ""),
            }
        return result

    @staticmethod
    def _daily_lingmai_level_plan(status: Mapping[str, Any]) -> dict[str, Any]:
        """Choose the stable Lingmai tier before any GUI seat action."""

        self_seat = status.get("self_seat_facts") if isinstance(status.get("self_seat_facts"), Mapping) else {}
        seated = self_seat.get("seated") is True
        try:
            own_room_id = int(self_seat.get("room_id") or status.get("own_room_id") or 0)
        except (TypeError, ValueError):
            own_room_id = 0
        try:
            strength = float(status.get("strength"))
        except (TypeError, ValueError):
            strength = None
        if seated and own_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID:
            return {
                "action": "hold_shengmai",
                "room_id": LINGMAI_UNION_SHENGMAI_ROOM_ID,
                "level_name": "天罡圣脉",
                "strength": strength,
            }
        if strength is None:
            return {
                "action": "hold_current" if seated else "enter_shenmai",
                "room_id": own_room_id if seated else LINGMAI_UNION_SHENMAI_ROOM_ID,
                "level_name": "仙煌神脉",
                "strength": None,
                "reason": "strength_missing",
            }
        if strength < LINGMAI_SHENGMAI_MIN_STRENGTH:
            return {
                "action": "hold_current" if seated else "enter_shenmai",
                "room_id": own_room_id if seated else LINGMAI_UNION_SHENMAI_ROOM_ID,
                "level_name": "仙煌神脉",
                "strength": strength,
                "reason": "shengmai_strength_reserve_insufficient",
            }
        return {
            "action": "try_shengmai",
            "room_id": LINGMAI_UNION_SHENGMAI_ROOM_ID,
            "level_name": "天罡圣脉",
            "strength": strength,
        }

    def _run_daily_lingmai_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = {"max_scrolls": 30, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少灵脉_座位资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(285), dict):
            raise RuntimeError("灵脉_座位：缺少 #285「造化灵脉」标注，无法确认入口后的场景锚点")

        task_label = "灵脉_座位"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([380, 382, 375, 374, 443, 318, 588, 306, 305, 288, 286, 285, 69, 34], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 380:
            return (yield from self._recover_daily_lingmai_occupied_arrival(
                ctx, stop_event, payload, context, task_label=task_label
            ))
        if scene_id == 306:
            return (yield from self._finish_daily_lingmai_to_world(
                context, payload, task_label=task_label, scene_id=scene_id, frame=frame
            ))
        if scene_id in {374, 382, 375}:
            return (yield from self._finish_daily_lingmai_kick_battle(
                context, payload, task_label=task_label, battle_scene_id=int(scene_id)
            ))
        if scene_id in {34, 69}:
            runtime_guard = yield from self._daily_lingmai_world_runtime_guard(
                context,
                ctx,
                payload,
                scene_id=scene_id,
            )
            if runtime_guard is not None:
                return runtime_guard
        if scene_id == 318:
            self._log("success", f"{task_label}：当前已在 #318 灵脉对白/确认，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._confirm_daily_lingmai_reward(context, payload, task_label=task_label))
        if scene_id == 588:
            runtime_status = refresh_lingmai_daily_status()
            room_state = self._daily_lingmai_588_runtime_state(runtime_status)
            self._log(
                "info",
                f"{task_label}：恢复已有 #588 灵脉房间，场景分 {score:.0f}%，"
                f"Runtime 状态={room_state}",
            )
            if room_state == "battle_pending":
                return (yield from self._finish_daily_lingmai_kick_battle(
                    context,
                    payload,
                    task_label=task_label,
                    battle_scene_id=588,
                ))
            if room_state == "unknown":
                raise RuntimeError(
                    f"{task_label}：#588 的 Runtime 战斗/座位事实不完整，"
                    "保留现场且未离场"
                )
            if room_state == "idle_unseated":
                payload["__lingmai_resume_after_idle_room"] = True
            return (yield from self._finish_daily_lingmai_to_world(
                context,
                payload,
                task_label=task_label,
                scene_id=scene_id,
                frame=frame,
            ))
        if scene_id == 443:
            self._log("success", f"{task_label}：当前已在 #443 灵脉更换确认，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._confirm_daily_lingmai_switch_popup(
                context,
                payload,
                task_label=task_label,
                scene_id=443,
                frame=frame,
            ))
        if scene_id == 305:
            self._log("success", f"{task_label}：当前已在 #305 灵脉聚灵确认弹窗，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._confirm_daily_lingmai_gather(context, payload, task_label=task_label))
        if scene_id == 288:
            self._log("success", f"{task_label}：当前已在 #288，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_from_final_occupy(ctx, stop_event, payload, context, task_label=task_label))
        if scene_id == 286:
            self._log("success", f"{task_label}：当前已在 #286 选择空位，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, context, frame, task_label=task_label))
        if scene_id == 285:
            self._log("success", f"{task_label}：当前已在 #285 造化灵脉，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_from_zaohua(ctx, stop_event, payload, context, frame, task_label=task_label))

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([443, 318, 588, 305, 288, 286, 285, 69, 34], wait=5.0, required=False)
                (scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 443:
                    self._log("success", f"{task_label}：当前已在 #443 灵脉更换确认，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._confirm_daily_lingmai_switch_popup(
                        context,
                        payload,
                        task_label=task_label,
                        scene_id=443,
                        frame=frame,
                    ))
                if scene_id == 318:
                    self._log("success", f"{task_label}：当前已在 #318 灵脉对白/确认，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._confirm_daily_lingmai_reward(context, payload, task_label=task_label))
                if scene_id == 588:
                    self._log(
                        "info",
                        f"{task_label}：恢复已有 #588 灵脉占位详情，场景分 {score:.0f}%",
                    )
                    return (yield from self._finish_daily_lingmai_to_world(
                        context,
                        payload,
                        task_label=task_label,
                        scene_id=scene_id,
                        frame=frame,
                    ))
                if scene_id == 305:
                    self._log("success", f"{task_label}：当前已在 #305 灵脉聚灵确认弹窗，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._confirm_daily_lingmai_gather(context, payload, task_label=task_label))
                if scene_id == 288:
                    self._log("success", f"{task_label}：当前已在 #288，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._continue_daily_lingmai_from_final_occupy(ctx, stop_event, payload, context, task_label=task_label))
                if scene_id == 286:
                    self._log("success", f"{task_label}：当前已在 #286 选择空位，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, context, frame, task_label=task_label))
                if scene_id == 285:
                    self._log("success", f"{task_label}：当前已在 #285 造化灵脉，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._continue_daily_lingmai_from_zaohua(ctx, stop_event, payload, context, frame, task_label=task_label))

        _scene_after, _score_after, frame_after = yield from self._enter_daily_lingmai_zaohua_from_world_or_daily(
            ctx,
            stop_event,
            payload,
            context,
            scene_id,
            frame,
            text,
            task_label=task_label,
        )
        if _scene_after is None:
            return "skipped"
        return (yield from self._continue_daily_lingmai_from_zaohua(ctx, stop_event, payload, context, frame_after, task_label=task_label))

    def _daily_lingmai_world_runtime_guard(
        self,
        context: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        *,
        scene_id: int,
    ):
        """Resolve completed/seated Lingmai state before opening the daily UI."""

        runtime_override = payload.get(
            "__lingmai_runtime_snapshot_override"
        )
        execution_status = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else refresh_lingmai_daily_status()
        )
        if (
            execution_status.get("available")
            and execution_status.get("complete")
        ):
            ctx["_daily_lingmai_status"] = execution_status
        else:
            return None
        try:
            remaining_ms = int(
                execution_status.get("remaining_milliseconds")
            )
        except (TypeError, ValueError):
            return None
        self_seat = (
            execution_status.get("self_seat_facts")
            if isinstance(
                execution_status.get("self_seat_facts"),
                Mapping,
            )
            else {}
        )
        seated = self_seat.get("seated") is True
        if remaining_ms > 0 and not seated:
            return None
        level_plan = self._daily_lingmai_level_plan(execution_status)
        if remaining_ms > 0 and seated and level_plan.get("action") == "try_shengmai":
            self._log(
                "info",
                "灵脉_座位：当前已坐神脉且体力 "
                f"{float(level_plan.get('strength') or 0):.0f}≥{LINGMAI_SHENGMAI_MIN_STRENGTH}，"
                "继续打开灵脉检查圣脉升级机会",
            )
            return None
        if scene_id != 34:
            yield from context.go_scene(34)
        if remaining_ms <= 0 or execution_status.get("completed") is True:
            self._log(
                "success",
                "灵脉_座位：世界页 Runtime 已确认 "
                "leftListenTime=0，今日聚灵真正完成",
            )
            return "success"
        if level_plan.get("action") == "hold_shengmai":
            self._log(
                "success",
                "灵脉_座位：世界页 Runtime 已确认仍在最高目标天罡圣脉，"
                "本日不再打开 UI，等待新被踢邮件或次日检查",
            )
            return "success"
        self._schedule_daily_lingmai_next_check(
            payload,
            message=(
                "世界页 Runtime 已确认仍在圣脉聚灵中"
                if level_plan.get("action") == "hold_shengmai"
                else (
                    "当前体力不足 300，保留至少一次重新落座资源并继续坐神脉"
                    if level_plan.get("reason") == "shengmai_strength_reserve_insufficient"
                    else "Runtime 已确认仍在灵脉聚灵中，体力事实不足时不尝试驱离升级"
                )
            ),
            seconds=int(
                payload.get("lingmai_gathering_recheck_seconds")
                or 1800
            ),
        )
        return "skipped"

    def daily_lingmai_clear_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        decision = self._daily_window_admission(
            now=job_now(),
            trigger=time_cls(21, 0),
            cutoff=time_cls(22, 0),
            label="灵脉_清体力",
            window_text="21:00-22:00",
        )
        return self._persist_admission_decision(payload, decision)

    def _execute_daily_lingmai_clear_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """进入造化灵脉并执行一键探索清体力流程。

        ``daily_lingmai_clear`` 与普通 ``daily_lingmai`` 是两种业务：前者从
        #285 进入探索并一次性选择最大体力，后者寻找空位并占领聚灵位。二者
        只复用 ``#34 -> #69 -> #285`` 的进入能力，进入 #285 后必须分流。

        #314 的滚动条必须通过 Runtime 的 shape 级基础动作从控件内部拖到画面
        右边缘，业务层不得读取标注框或换算坐标。``#314[确定]`` 后可能短暂
        出现 #315「继续」。本作业固定只处理一轮：无论一轮后回到 #285 还是
        #313，都按本轮完成处理，不根据剩余体力再次进入探索。#313「体力」的
        文本格式是“单次消耗/现有体力”（例如 30/1113）；仅当现有体力小于
        单次消耗时用于执行前短路，直接回 #34 并按成功完成。除此之外，一键
        探索仍负责在本轮中尽量消耗体力。正式 task cell 随后由通用稳定锚点
        收尾回到 #34。

        每次正式执行都从稳定起点整单运行，不跨 Kernel restart 承接
        #313/#314/#315 等中间业务进度。点击未生效的有限重试、瞬时 #315、
        单轮完成判定和滑块拖满均由 Runtime/本作业闭环处理。
        """
        payload = {"max_scrolls": 30, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少灵脉_清体力资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(285), dict):
            raise RuntimeError("灵脉_清体力：缺少 #285「造化灵脉」标注，无法确认入口后的场景锚点")

        task_label = "灵脉_清体力"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([589, 315, 314, 313, 312, 285, 69, 34], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 589:
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(
                context,
                payload,
                task_label=task_label,
                ctx=ctx,
                stop_event=stop_event,
                guiyuan_already_open=True,
            ))
        if scene_id == 315:
            return (yield from self._continue_daily_lingmai_clear_from_transient(context, payload, task_label=task_label))
        if scene_id == 314:
            return (yield from self._continue_daily_lingmai_clear_from_amount(context, payload, task_label=task_label))
        if scene_id == 313:
            return (yield from self._continue_daily_lingmai_clear_from_explore(context, payload, task_label=task_label))
        if scene_id == 312:
            yield from context.wait_click_then_scene(312, "确认", 285)
            frame = context.cur_frame(update=True)
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(
                context,
                payload,
                task_label=task_label,
                ctx=ctx,
                stop_event=stop_event,
            ))
        if scene_id == 285:
            self._log("success", f"{task_label}：已在 #285 造化灵脉，继续清理体力，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(
                context,
                payload,
                task_label=task_label,
                ctx=ctx,
                stop_event=stop_event,
            ))
        scene_after, _score_after, frame_after = yield from self._enter_daily_lingmai_zaohua_from_world_or_daily(
            ctx,
            stop_event,
            payload,
            context,
            scene_id,
            frame,
            text,
            task_label=task_label,
        )
        if scene_after == 285:
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(
                context,
                payload,
                task_label=task_label,
                ctx=ctx,
                stop_event=stop_event,
            ))
        return "skipped"

    def _continue_daily_lingmai_clear_from_zaohua(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        ctx: dict[str, Any] | None = None,
        stop_event: threading.Event | None = None,
        guiyuan_already_open: bool = False,
    ):
        yield from self._check_daily_lingmai_guiyuan_upgrade(
            context,
            payload,
            task_label=task_label,
            already_open=guiyuan_already_open,
        )
        if ctx is not None and stop_event is not None:
            _wait_scene_match = yield from context.wait_scene([285, 69, 34], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 285:
                self._log(
                    "warning",
                    f"{task_label}：归元凝神返回后实际落到 "
                    f"{'#' + str(scene_id) if scene_id is not None else 'unknown'}，"
                    "重新从业务入口进入 #285",
                )
                scene_after, _score_after, _frame_after = yield from self._enter_daily_lingmai_zaohua_from_world_or_daily(
                    ctx,
                    stop_event,
                    payload,
                    context,
                    scene_id,
                    frame,
                    context.ocr_text(frame),
                    task_label=task_label,
                )
                if scene_after != 285:
                    raise RuntimeError(
                        f"{task_label}：归元凝神返回后未能重新进入 #285"
                    )
        landing = yield from context.wait_click_then_scene(
            285,
            "探索",
            [313, 286],
            timeout=float(payload.get("lingmai_explore_dialog_timeout_seconds") or 15.0),
            label=f"{task_label}：等待 #313 探索业务框或 #286 完成页",
        )
        landing_id = int(getattr(landing, "id", landing) or 0)
        frame = context.cur_frame(update=True)
        stamina_text = context.ocr_text_in_shapes(
            313,
            ("体力",),
            frame_data_url=frame,
        )
        stamina = self._parse_daily_lingmai_clear_stamina(stamina_text)
        if stamina is None or stamina[0] != 30 or stamina[1] < 0:
            if landing_id == 286:
                daily_status = refresh_lingmai_daily_status()
                if (
                    daily_status.get("available")
                    and daily_status.get("completed")
                    and daily_status.get("remaining_milliseconds") == 0
                ):
                    self._log(
                        "success",
                        f"{task_label}：#286 只读状态确认今日灵脉已完成、剩余 0ms，"
                        "无需再次进入探索",
                    )
                    yield from context.go_scene(34)
                    return self._complete_daily_clear_task(
                        payload,
                        task_id="legacy-daily-lingmai-clear",
                        label=task_label,
                    )
            raise RuntimeError(
                f"{task_label}：点击探索后未可靠读取 #313[体力]「{stamina_text}」，"
                "禁止继续"
            )
        self._log(
            "detail",
            f"{task_label}：#313[体力] 已读取 {stamina[0]}/{stamina[1]}，"
            "确认业务探索框已打开",
        )
        return (yield from self._continue_daily_lingmai_clear_from_explore(context, payload, task_label=task_label))

    def _check_daily_lingmai_guiyuan_upgrade(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        already_open: bool = False,
    ):
        """在 #285 检查一次归元凝神；资源不足时零升级并返回入口页。"""

        if not already_open:
            yield from context.wait_click_then_scene(
                285,
                "归元凝神",
                589,
                timeout=float(payload.get("lingmai_guiyuan_open_timeout_seconds") or 15.0),
                label=f"{task_label}：打开 #589 归元凝神",
            )
        values, resource_text = context.ocr_value_in_shapes(
            589, ("凝神资源",),
            parse_value=lambda text: parse_ocr_values(text, expected_count=2),
        )
        if values is None:
            self._log(
                "detail",
                f"{task_label}：#589 未可靠读取凝神资源「{resource_text}」，本次不升级",
            )
            yield from context.wait_click_then_scene(589, "返回", 285)
            return "unknown"

        available, cost = values
        if cost <= 0:
            self._log(
                "detail",
                f"{task_label}：#589 凝神消耗无效 {available}/{cost}，本次不升级",
            )
            yield from context.wait_click_then_scene(589, "返回", 285)
            return "unknown"
        if available < cost:
            self._log(
                "success",
                f"{task_label}：归元凝神资源 {available}/{cost}，当前不可升级，直接通过",
            )
            yield from context.wait_click_then_scene(589, "返回", 285)
            return "not_upgradable"

        self._log(
            "action",
            f"{task_label}：归元凝神资源 {available}/{cost}，执行一次凝神升级",
        )
        yield from context.wait_click_then_scene(
            589,
            "凝神",
            346,
            timeout=float(payload.get("lingmai_guiyuan_upgrade_timeout_seconds") or 15.0),
            label=f"{task_label}：等待归元凝神升级成功层",
        )
        yield from context.wait_click_then_scene(
            346,
            "继续",
            589,
            timeout=float(payload.get("lingmai_guiyuan_continue_timeout_seconds") or 15.0),
            label=f"{task_label}：关闭归元凝神成功层",
        )
        after_values, after_text = context.ocr_value_in_shapes(
            589, ("凝神资源",),
            parse_value=lambda text: parse_ocr_values(text, expected_count=2),
        )
        if after_values is None or after_values[0] != available - cost:
            raise RuntimeError(
                f"{task_label}：归元凝神升级后资源复验失败，"
                f"升级前 {available}/{cost}，升级后「{after_text}」"
            )
        self._log(
            "success",
            f"{task_label}：归元凝神升级成功，资源 {available}->{after_values[0]}",
        )
        yield from context.wait_click_then_scene(589, "返回", 285)
        return "upgraded"

    def _continue_daily_lingmai_clear_from_explore(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        """在 #313 勾选一键探索，再进入 #314 选择消耗体力。"""
        frame = context.cur_frame(update=True)
        stamina_text = context.ocr_text_in_shapes(313, ("体力",), frame_data_url=frame)
        stamina = self._parse_daily_lingmai_clear_stamina(stamina_text)
        if stamina is not None:
            per_run_cost, available = stamina
            self._log(
                "detail",
                f"{task_label}：#313 体力={per_run_cost}/{available}（单次消耗/现有体力）",
            )
            if available < per_run_cost:
                self._log(
                    "success",
                    f"{task_label}：现有体力 {available} 小于单次消耗 {per_run_cost}，本次无需探索，返回 #34",
                )
                yield from context.go_scene(34)
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-lingmai-clear",
                    label=task_label,
                )
        else:
            self._log("detail", f"{task_label}：未可靠解析 #313[体力]「{stamina_text}」，继续固定一轮探索")
        unchecked_score = context.shape_score(313, "一键探索", frame_data_url=frame)
        # #313「一键探索」参考图表示未勾选状态。真实调试中未勾选为 100，
        # 勾选后仍可能因大部分背景相同而达到 81；因此这里使用独立的严格
        # 状态阈值，而不能沿用普通 overlay_threshold=55。
        unchecked_threshold = float(payload.get("lingmai_one_click_unchecked_threshold") or 95.0)
        self._log(
            "detail",
            f"{task_label}：#313「一键探索」未勾选图像 score={unchecked_score:.0f}% threshold={unchecked_threshold:.0f}%",
        )
        if unchecked_score >= unchecked_threshold:
            yield from context.wait_click(313, "一键探索")
            yield from context.wait_action_settle(float(payload.get("lingmai_one_click_settle_seconds") or 1.0))
        yield from context.wait_click_then_scene(313, "确定", 314)
        return (yield from self._continue_daily_lingmai_clear_from_amount(context, payload, task_label=task_label))

    @staticmethod
    def _parse_daily_lingmai_clear_stamina(text: str) -> tuple[int, int] | None:
        """Parse #313 stamina as (single-run cost, currently available)."""
        values = parse_ocr_values(text, expected_count=2, allow_extra_numbers=True)
        return (values[0], values[1]) if values is not None else None

    def _continue_daily_lingmai_clear_from_amount(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        """在 #314 通过已标注滚动条选择最大体力并确认。"""
        self._log("action", f"{task_label}：拖动 #314「滚动条」到最右端选择最大体力")
        selected = available = remainder = -1
        amount_text = ""
        start_ratio: float | None = None
        for drag_attempt in range(3):
            context.drag_shape_to_frame_edge(
                314,
                "滚动条",
                direction="right",
                duration=float(payload.get("lingmai_amount_drag_seconds") or 0.6),
                start_ratio=start_ratio,
            )
            yield from context.wait_action_settle(float(payload.get("lingmai_amount_settle_seconds") or 1.0))
            frame = context.cur_frame(update=True)
            amount, amount_text = context.ocr_value_in_shapes(
                314, ("消耗体力",),
                parse_value=self._parse_daily_lingmai_clear_stamina,
            )
            if amount is None:
                raise RuntimeError(
                    f"{task_label}：#314 拖动后未可靠读取消耗体力「{amount_text}」，禁止确认"
                )
            selected, available = amount
            remainder = available - selected
            if remainder < 30 or available <= 0:
                break
            start_ratio = selected / available
            self._log(
                "warning",
                f"{task_label}：#314 第 {drag_attempt + 1} 次拖动仅到 {selected}/{available}，从当前比例继续推到末端",
            )
        self._log(
            "detail",
            f"{task_label}：#314 拖动后消耗体力={selected}/{available}，差值={remainder}",
        )
        if selected < 0 or available < 0 or remainder < 0:
            raise RuntimeError(
                f"{task_label}：#314 消耗体力数值无效 {selected}/{available}，禁止确认"
            )
        if remainder >= 30:
            raise RuntimeError(
                f"{task_label}：#314 滚动条未拖到末端，消耗体力 {selected}/{available}，"
                f"差值 {remainder} 不小于 30，禁止确认"
            )
        landing = yield from context.wait_click_then_scene(
            314,
            "确定",
            [315, 313, 285],
            timeout=float(payload.get("lingmai_finish_timeout_seconds") or 15.0),
            label=f"{task_label}：等待 #314 确定后的 #315/#313/#285",
        )
        landing_id = int(getattr(landing, "id", landing) or 0)
        if landing_id == 315:
            return (yield from self._continue_daily_lingmai_clear_from_transient(context, payload, task_label=task_label))
        if landing_id == 313:
            self._log("success", f"{task_label}：已完成固定一轮探索并回到 #313，剩余体力不再继续处理")
            return self._complete_daily_clear_task(
                payload,
                task_id="legacy-daily-lingmai-clear",
                label=task_label,
            )
        self._log("success", f"{task_label}：#315 未出现或已自动消失，已回到 #285 造化灵脉")
        return self._complete_daily_clear_task(
            payload,
            task_id="legacy-daily-lingmai-clear",
            label=task_label,
        )

    def _continue_daily_lingmai_clear_from_transient(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        """尽快消费有时效性的 #315；错过它时仍以回到 #285 为完成。"""
        self._log("action", f"{task_label}：检测到有时效性的 #315，尝试点击「继续」")
        try:
            landing = yield from context.wait_click_then_scene(
                315,
                "继续",
                [313, 285],
                settle_seconds=0.2,
                timeout=float(payload.get("lingmai_transient_click_timeout_seconds") or 2.0),
                label=f"{task_label}：点击 #315 继续后等待 #313/#285",
            )
            if int(getattr(landing, "id", landing) or 0) == 313:
                self._log("success", f"{task_label}：已完成固定一轮探索并回到 #313，剩余体力不再继续处理")
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-lingmai-clear",
                    label=task_label,
                )
        except TimeoutError:
            self._log("detail", f"{task_label}：#315 可能已自动消失，确认回到 #313 或 #285")
            landing = yield from context.wait_scene(
                [313,
                285],
                wait=float(payload.get("lingmai_finish_timeout_seconds") or 15.0),
                label=f"{task_label}：等待有时效性的 #315 自动消失并回到 #313/#285",
            )
            if int(getattr(landing, "id", landing) or 0) == 313:
                self._log("success", f"{task_label}：已完成固定一轮探索并回到 #313，剩余体力不再继续处理")
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-lingmai-clear",
                    label=task_label,
                )
        self._log("success", f"{task_label}：体力已清理并回到 #285 造化灵脉")
        return self._complete_daily_clear_task(
            payload,
            task_id="legacy-daily-lingmai-clear",
            label=task_label,
        )

    def _enter_daily_lingmai_zaohua_from_world_or_daily(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: BehaviorTreeContext,
        scene_id: int | None,
        frame: str | None,
        text: str,
        *,
        task_label: str,
    ) -> tuple[int | None, float, str | None]:
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([285, 69, 34], wait=5.0, required=False)
                (scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 285:
                    self._log("success", f"{task_label}：已到达 #285 造化灵脉，当前 #{scene_id} {score:.0f}%，OCR={text[:160]}")
                    return scene_id, score, frame
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label=task_label)

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            title_pattern=_DAILY_LINGMAI_ENTRY_PATTERN,
            progress_can_mark_done=False,
            initial_checks=self._payload_int(payload, "lingmai_daily_initial_checks", default=10),
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lingmai",
                task_type="daily_lingmai",
                label=task_label,
                entry_label="参与灵脉争夺1小时",
            )
            return None, 0.0, frame
        landed = yield from self._wait_daily_lingmai_zaohua_after_entry(
            context,
            payload,
            task_label=task_label,
        )
        landed_id = int(landed.id if isinstance(landed, View) else landed)
        if landed_id != 285:
            raise RuntimeError(f"{task_label}：处理入口弹窗后未确认到达 #285")
        scene_after = 285
        score_after = 100.0
        frame_after = context.cur_frame(update=True)
        text_after = context.ocr_text(frame_after)
        self._log("success", f"{task_label}：已到达 #285 造化灵脉，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return scene_after, score_after, frame_after

    def _wait_daily_lingmai_zaohua_after_entry(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        timeout = float(payload.get("lingmai_entry_timeout") or 25.0)
        deadline = time.monotonic() + max(1.0, timeout)
        retried_entry = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{task_label}：点击 #69 入口后等待 #285/#312 超过 {timeout:.0f} 秒")
            try:
                scene_id = yield from context.wait_scene(
                    [285,
                    312],
                    wait=max(1.0, min(8.0, remaining)) if not retried_entry else max(1.0, remaining),
                    label=f"{task_label}：点击 #69 入口后等待 #285 造化灵脉",
                )
            except TimeoutError:
                _wait_scene_match = yield from context.wait_scene([285, 312, 69], wait=5.0, required=False)
                (scene_after, _score_after, _frame_after) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_after in {285, 312}:
                    scene_id = context.view(scene_after)
                elif scene_after == 69 and not retried_entry and time.monotonic() < deadline:
                    retried_entry = True
                    self._log(
                        "action",
                        f"{task_label}：入口点击后的瞬态弹层已清理但仍在 #69，原等待预算内重开入口一次",
                    )
                    reopen_status = yield from context.open_daily_entry(
                        label=task_label,
                        title_pattern=_DAILY_LINGMAI_ENTRY_PATTERN,
                        progress_can_mark_done=False,
                        max_scrolls=0,
                        initial_checks=1,
                    )
                    if reopen_status != "open":
                        raise RuntimeError(f"{task_label}：返回 #69 后未能重新定位灵脉入口")
                    continue
                elif time.monotonic() < deadline and not retried_entry:
                    # The first eight-second window may end during a real
                    # transition. Keep waiting within the original total
                    # budget, but never repeat the click without fresh #69.
                    retried_entry = True
                    continue
                else:
                    raise
            if int(scene_id.id if isinstance(scene_id, View) else scene_id) == 285:
                return scene_id
            yield from context.wait_click_then_scene(312, "确认", [285, 312], timeout=8.0)
            _wait_scene_match = yield from context.wait_scene([285, 312], wait=5.0, required=False)
            (scene_after, _score_after, _frame_after) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_after == 285:
                return context.view(285)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{task_label}：处理 #312 确认弹窗后仍未到达 #285")

    def _schedule_daily_lingmai_next_check(
        self,
        payload: dict[str, Any],
        *,
        message: str,
        seconds: int = 1800,
        retry_at_ms: int | None = None,
    ) -> str:
        now = job_now()
        if retry_at_ms is None:
            retry_at = now + timedelta(seconds=max(5, int(seconds)))
        else:
            retry_at = datetime.fromtimestamp(max(int(retry_at_ms), int(time.time() * 1000) + 5000) / 1000)
        retry_at = clip_daily_retry_to_window(
            retry_at,
            now=now,
            start=str(payload.get("daily_start_time") or "17:30"),
            end=str(payload.get("daily_end_time") or "22:00"),
        )
        next_time = retry_at.strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-lingmai"),
            next_time,
        )
        # Ephemeral terminal evidence for the outer Cell wrapper.  This is not
        # persisted task state; Scheduler continues to own only ``next_time``.
        payload["__lingmai_terminal_message"] = message
        payload["__lingmai_terminal_next_time"] = next_time
        self._log("skip", f"灵脉_座位：{message}，{next_time} 重试")
        return next_time

    def _confirm_daily_lingmai_gather(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        yield from context.wait_click_then_scene(
            305,
            "确定",
            wait_leave=True,
            timeout=float(payload.get("lingmai_gather_confirm_timeout") or 20.0),
        )
        yield from context.wait_action_settle(float(payload.get("lingmai_gather_confirm_settle_seconds") or 3.0))
        _wait_scene_match = yield from context.wait_scene([318, 306, 285, 288, 305, 186, 34], wait=5.0, required=False)
        (scene_after, score_after, frame_after) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text_after = context.ocr_text(frame_after)
        if scene_after == 305:
            raise RuntimeError(f"{task_label}：点击 #305「确定」后仍停留在灵脉聚灵确认弹窗，OCR={text_after[:160]}")
        if scene_after == 318:
            return (yield from self._confirm_daily_lingmai_reward(context, payload, task_label=task_label))
        if scene_after == 306:
            return (yield from self._finish_daily_lingmai_to_world(context, payload, task_label=task_label, scene_id=scene_after, frame=frame_after))
        self._log("success", f"{task_label}：已确认聚灵，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return (yield from self._finish_daily_lingmai_to_world(context, payload, task_label=task_label))

    def _confirm_daily_lingmai_reward(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        # Reward text and post-battle speech share #318 and its confirmed
        # arrow. Drain the bounded dialogue chain, rather than assuming one
        # click must leave this scene identity.
        return (yield from self._finish_daily_lingmai_post_battle(
            context, payload, task_label=task_label
        ))

    def _finish_daily_lingmai_to_world(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int | None = None,
        frame: str | None = None,
    ) -> str:
        if scene_id is None:
            _wait_scene_match = yield from context.wait_scene([34, 306, 318, 285, 286, 288, 305, 186, 588], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        if scene_id in {318, 303}:
            return (yield from self._finish_daily_lingmai_post_battle(
                context, payload, task_label=task_label
            ))
        if scene_id is None:
            raise RuntimeError(f"{task_label}：离场前场景仍为 unknown，保留现场且未调用世界导航")
        text = context.ocr_text(frame) if isinstance(frame, str) and frame else context.ocr_text(update=True)
        daily_remaining_seconds = self._parse_daily_lingmai_remaining_seconds(text)
        if scene_id == 588:
            # #588 is a stable Lingmai room page, but it does not by itself
            # prove that the preceding kick transaction was created.  Its
            # caller has already classified pending replay / seated / idle by
            # Runtime; this helper only performs the annotated room exit.
            try:
                yield from context.wait_click_then_scene(
                    588,
                    "离开",
                    [306, 318, 303, 186, 85, 34, 285],
                    timeout=float(payload.get("lingmai_occupied_leave_timeout") or 30.0),
                )
            except SceneClickMismatch as exc:
                if exc.expected_scene_id != 588 or exc.actual_scene_id not in {306, 318, 303}:
                    raise
                # The delayed business overlay arrived before input was sent.
                # Consume it below; never retry an exit through that overlay.
                self._log("info", f"{task_label}：离场前已出现 #{exc.actual_scene_id} 结算/对白，转入收尾")
            _wait_scene_match = yield from context.wait_scene([306, 318, 303, 186, 85, 34, 285], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        if scene_id == 186:
            # The room-exit animation reveals #186 before the gathering
            # summary arrives. Give that business popup ownership and a
            # bounded foreground window before clicking the world exit.
            with context.expect_views(306, 318):
                summary = yield from context.wait_scene(
                    [306, 318], wait=15.0, required=False,
                    label=f"{task_label}：离开房间后等待延迟结算或稳定外层",
                )
                scene_id = int(summary) if summary is not None else None
                frame = getattr(summary, "frame_data_url", None)
        if scene_id in {318, 303}:
            return (yield from self._finish_daily_lingmai_post_battle(
                context, payload, task_label=task_label
            ))
        if scene_id == 306:
            yield from self._confirm_daily_lingmai_summary_popup(context, payload, task_label=task_label, scene_id=scene_id, frame=frame)
            landed = yield from context.wait_scene(
                [312,
                285,
                85,
                186,
                34],
                wait=float(payload.get("lingmai_summary_landing_timeout") or 30.0),
                label=f"{task_label}：#306 确认后等待可选 #312 或灵脉内部/世界",
            )
            scene_id = int(landed.id if isinstance(landed, View) else landed)
            if scene_id == 312:
                yield from context.wait_click_then_scene(
                    312,
                    "确认",
                    [285, 85, 186, 34],
                    timeout=float(payload.get("lingmai_summary_312_timeout") or 20.0),
                )
                _wait_scene_match = yield from context.wait_scene([285, 85, 186, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
        if scene_id == 186:
            yield from self._leave_shared_scene_186_to_world(context, label=task_label)
            scene_id = 34
        if (
            scene_id is not None
            and scene_id != 34
            and isinstance(getattr(context, "ctx", None), dict)
        ):
            # Preserve the just-resolved business Layer 0 result for the first
            # generic goto step.  Without this hand-off, goto immediately runs
            # an unrelated global recognition pass and can replace a valid
            # #186 exit scene with another business frame before clicking.
            context.ctx["_go_scene_known_scene_id"] = int(scene_id)
        if scene_id is None:
            raise RuntimeError(f"{task_label}：结算后场景仍为 unknown，保留现场且未调用世界导航")
        if daily_remaining_seconds == 0:
            if scene_id != 34:
                yield from context.go_scene(34)
            self._log("success", f"{task_label}：今日聚灵剩余已为 00:00:00，结算后回到 #34，不再尝试占座或驱离")
            return "success"
        if scene_id != 34:
            self._log("action", f"{task_label}：本轮操作后按场景图回到 #34 世界")
            yield from context.go_scene(34)
        self._log("success", f"{task_label}：本轮操作后已回到 #34 世界")

        runtime_override = payload.get(
            "__lingmai_post_action_runtime_snapshot_override"
        )
        execution_status = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else refresh_lingmai_daily_status()
        )
        if (
            execution_status.get("available")
            and execution_status.get("complete")
        ):
            try:
                remaining_ms = int(
                    execution_status.get("remaining_milliseconds")
                )
            except (TypeError, ValueError):
                remaining_ms = None
            if execution_status.get("completed") is True or remaining_ms == 0:
                self._log(
                    "success",
                    f"{task_label}：战后 Runtime 确认 leftListenTime=0，"
                    "今日聚灵真正完成",
                )
                return "success"
            if remaining_ms is not None and remaining_ms > 0:
                self_seat = (
                    execution_status.get("self_seat_facts")
                    if isinstance(
                        execution_status.get("self_seat_facts"),
                        Mapping,
                    )
                    else {}
                )
                if (
                    payload.get("__lingmai_resume_after_idle_room") is True
                    and self_seat.get("seated") is not True
                ):
                    self._log(
                        "info",
                        f"{task_label}：Runtime 确认驱离事务未创建、角色仍未入座，"
                        "已回 #34 并交回整单入口重选",
                    )
                    return "retry_idle_room"
                try:
                    expected_room_id = int(payload.get("__lingmai_expected_room_id") or 0)
                    actual_room_id = int(
                        self_seat.get("room_id")
                        or execution_status.get("own_room_id")
                        or 0
                    )
                except (TypeError, ValueError):
                    expected_room_id = 0
                    actual_room_id = 0
                if expected_room_id > 0 and actual_room_id != expected_room_id:
                    self._schedule_daily_lingmai_next_check(
                        payload,
                        message=(
                            f"目标房间 {expected_room_id} 尚未形成服务端终态，"
                            f"当前仍为 {actual_room_id or '未落座'}，短周期复核"
                        ),
                        seconds=int(payload.get("lingmai_postcondition_retry_seconds") or 60),
                    )
                    return "skipped"
                if (
                    self_seat.get("seated") is True
                    and actual_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID
                ):
                    self._log(
                        "success",
                        f"{task_label}：Runtime 确认已坐最高目标天罡圣脉，"
                        "等待新被踢邮件或次日检查",
                    )
                    return "success"
                seat_text = (
                    "且 seated=true"
                    if self_seat.get("seated") is True
                    else ""
                )
                self._schedule_daily_lingmai_next_check(
                    payload,
                    message=(
                        f"Runtime 确认今日聚灵仍剩 {remaining_ms}ms"
                        f"{seat_text}，30 分钟后复查"
                    ),
                    seconds=int(
                        payload.get(
                            "lingmai_gathering_recheck_seconds"
                        )
                        or 1800
                    ),
                )
                return "skipped"

        self._schedule_daily_lingmai_next_check(
            payload,
            message=(
                "本轮已回世界，但 Runtime 尚未形成完整终态，"
                "10 分钟后复核，不能提前标记今日完成"
            ),
            seconds=int(
                payload.get("lingmai_incomplete_retry_seconds")
                or 600
            ),
        )
        return "skipped"

    @staticmethod
    def _daily_lingmai_588_runtime_state(
        status: Mapping[str, Any],
    ) -> str:
        """Classify visually identical #588 states from public Runtime facts."""

        if not status.get("available") or not status.get("complete"):
            return "unknown"
        replay = (
            status.get("battle_replay")
            if isinstance(status.get("battle_replay"), Mapping)
            else {}
        )
        if not replay.get("available"):
            return "unknown"
        if replay.get("pending") is True:
            return "battle_pending"
        if replay.get("pending") is not False:
            return "unknown"
        self_seat = (
            status.get("self_seat_facts")
            if isinstance(status.get("self_seat_facts"), Mapping)
            else {}
        )
        if not self_seat.get("available"):
            return "unknown"
        return "stable_seated" if self_seat.get("seated") is True else "idle_unseated"

    @staticmethod
    def _parse_daily_lingmai_remaining_seconds(text: str) -> int | None:
        compact = _sanitize_ocr_text(text)
        matched = re.search(
            r"(?:今日)?聚灵剩余[:：]?(\d{1,2}):(\d{2}):(\d{2})",
            compact,
        )
        if matched is None:
            return None
        hours, minutes, seconds = (int(value) for value in matched.groups())
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds

    def _confirm_daily_lingmai_summary_popup(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int | None = None,
        frame: str | None = None,
    ) -> str:
        if scene_id is None:
            if isinstance(frame, str) and frame:
                scene_id, _score, _frame = context.recognize_scene_in_frame(
                    [306], frame_data_url=frame
                )
            else:
                _wait_scene_match = yield from context.wait_scene([306], label=f'{task_label}：识别灵脉收益确认', wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
        if scene_id != 306:
            raise RuntimeError(f"{task_label}：未识别到正式灵脉收益确认 scene，拒绝按 OCR 猜按钮")
        view = context.view(scene_id)
        action = view.get_shape("确认") or view.get_shape("确定")
        if action is None:
            raise RuntimeError(f"{task_label}：#{scene_id} 缺少「确认/确定」动作 shape")
        self._log("action", f"{task_label}：点击 #{scene_id}「{action.title}」")
        context.click_shape_center(view, action)
        yield from context.wait_action_settle(float(payload.get("lingmai_summary_confirm_settle_seconds") or 2.0))
        return "success"

    def _confirm_daily_lingmai_switch_popup(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int = 443,
        frame: str | None = None,
    ) -> str:
        """Confirm #443 and wait through dialogue until take-seat success."""

        yield from self._click_daily_lingmai_switch_confirm(
            context,
            payload,
            task_label=task_label,
            scene_id=scene_id,
            frame=frame,
        )

        landed = yield from context.wait_scene(
            [318,
            303,
            305,
            306,
            443],
            wait=float(payload.get("lingmai_switch_landing_timeout") or 20.0),
            label=f"{task_label}：确认更换灵脉后等待对话或入座成功页",
        )
        landed_id = int(landed.id if isinstance(landed, View) else landed)
        if landed_id == 443:
            raise RuntimeError(
                f"{task_label}：点击灵脉更换「确定」后仍停留在 #{landed_id}，"
                f"OCR={context.ocr_text(update=True)[:160]}"
            )
        if landed_id in {318, 303}:
            landed_id = yield from self._advance_daily_lingmai_kick_dialogue(
                context,
                terminal_scene_ids=(305, 306),
                timeout=float(payload.get("lingmai_switch_dialogue_timeout") or 60.0),
                label=f"{task_label}：推进更换灵脉对话直到入座成功页",
            )
        if landed_id == 305:
            return (yield from self._confirm_daily_lingmai_gather(context, payload, task_label=task_label))
        if landed_id == 306:
            _wait_scene_match = yield from context.wait_scene([306], wait=5.0, required=False)
            (scene_after, _score_after, frame_after) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            return (yield from self._finish_daily_lingmai_to_world(
                context,
                payload,
                task_label=task_label,
                scene_id=scene_after,
                frame=frame_after,
            ))
        raise RuntimeError(
            f"{task_label}：确认更换灵脉后未到达 #305/#306；当前 #{landed_id}"
        )

    def _click_daily_lingmai_switch_confirm(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int = 443,
        frame: str | None = None,
    ):
        """Click a semantically verified Lingmai switch prompt without consuming its tail."""

        if int(scene_id) != 443:
            raise RuntimeError(
                f"{task_label}：通用提示 #{scene_id} 不拥有灵脉更换确认权限，等待正式 #443"
            )
        frame = frame if isinstance(frame, str) and frame else context.cur_frame(update=True)
        text = context.ocr_text_in_shapes(
            443,
            ("是否更换",),
            frame_data_url=frame,
        )
        compact = _sanitize_ocr_text(text)
        required_markers = ("你已在", "是否更换到该灵脉")
        missing = [marker for marker in required_markers if marker not in compact]
        if missing:
            raise RuntimeError(
                f"{task_label}：#443[是否更换] 不符合灵脉更换确认语义，缺少 {missing}，OCR={text[:160]}"
            )

        self._log("action", f"{task_label}：确认从当前灵脉更换到目标灵脉")
        image = (
            context.ctx.get("images", {}).get(scene_id)
            if isinstance(getattr(context, "ctx", None), dict)
            else None
        )
        if isinstance(image, dict) and self._find_shape(image, "确定") is not None:
            context.click_shape_center(scene_id, "确定")
        else:
            raise RuntimeError(f"{task_label}：#{scene_id} 缺少「确定」动作 shape，拒绝按 OCR 猜按钮")
        yield from context.wait_action_settle(float(payload.get("lingmai_switch_confirm_settle_seconds") or 2.0))

    def _enter_daily_lingmai_level(
        self,
        context: Any,
        payload: Mapping[str, Any],
        *,
        level_name: str,
        search_direction: Literal["up", "down"] | None = None,
        wait_for_landing: bool = True,
        frame: str | None = None,
        task_label: str,
    ):
        """Enter any #285 Lingmai level from its OCR title.

        The title can produce multiple OCR matches.  Lingmai cards intentionally
        accept the first one and click the title box relation ``(x, y + 2h)``;
        other OCR actions keep their existing ambiguity protection.
        """

        target = str(level_name or "").strip()
        if not target:
            raise ValueError(f"{task_label}：目标灵脉等级不能为空")
        self._log(
            "action",
            f"{task_label}：在 #285[窗口] {search_direction or '双向'}查找「{target}」，"
            "命中后按 (x, y+2h) 进入对应灵脉",
        )
        match = yield from context.wait_click_ocr_text(
            285,
            target,
            in_shapes=("窗口",),
            occurrence=0,
            anchor="top_left",
            offset=(0.0, 2.0),
            offset_unit="height",
            timeout_seconds=float(payload.get("lingmai_level_search_timeout") or 30.0),
            max_scrolls_per_direction=int(payload.get("lingmai_level_search_scrolls") or 8),
            search_direction=search_direction,
            frame_data_url=frame,
        )
        self._log(
            "success",
            f"{task_label}：已从「{target}」OCR 框 "
            f"({match.x:.0f},{match.y:.0f},{match.w:.0f},{match.h:.0f}) 点击对应条目",
        )
        if not wait_for_landing:
            return match
        return (yield from context.wait_scene(
            [286],
            wait=float(payload.get("lingmai_select_slot_timeout") or 12.0),
            label=f"{task_label}：点击「{target}」后等待 #286 座位页",
        ))

    def _continue_daily_lingmai_from_zaohua(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: BehaviorTreeContext,
        frame: str | None,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image285 = images.get(285)
        if not isinstance(image285, dict):
            raise RuntimeError(f"{task_label}：缺少 #285「造化灵脉」标注，无法进入神脉")
        self._raise_if_stopped(stop_event)
        frame = frame if isinstance(frame, str) and frame else context.cur_frame(update=True)
        daily_status = (
            ctx.get("_daily_lingmai_status")
            if isinstance(ctx.get("_daily_lingmai_status"), dict)
            else refresh_lingmai_daily_status()
        )
        ctx["_daily_lingmai_status"] = daily_status
        if daily_status.get("available"):
            self._log(
                "detail",
                f"{task_label}：#285 只读状态读取今日剩余聚灵 "
                f"{daily_status.get('remaining_milliseconds')}ms，"
                f"source={daily_status.get('source')} protocol={daily_status.get('protocol')}",
            )
        else:
            self._log(
                "detail",
                f"{task_label}：#285 Runtime 暂无完整的今日剩余聚灵状态，"
                f"reason={daily_status.get('reason') or 'unavailable'}，继续视觉流程",
            )
        if daily_status.get("available") and daily_status.get("completed"):
            self._log(
                "success",
                f"{task_label}：#285 只读状态确认 leftListenTime=0，今日 3 小时聚灵已完成",
            )
            yield from context.go_scene(34)
            return "success"
        level_plan = (
            self._daily_lingmai_level_plan(daily_status)
            if daily_status.get("available")
            else {
                "action": "enter_shenmai",
                "room_id": LINGMAI_UNION_SHENMAI_ROOM_ID,
                "level_name": "仙煌神脉",
                "strength": None,
            }
        )
        if level_plan.get("action") in {"hold_current", "hold_shengmai"}:
            strength = level_plan.get("strength")
            reason = (
                "已坐圣脉，保持当前最高层级"
                if level_plan.get("action") == "hold_shengmai"
                else (
                    f"当前体力 {float(strength):.0f}<300，只坐神脉并保留重新落座资源"
                    if strength is not None
                    else "当前体力事实缺失，保守保持已有神脉座位"
                )
            )
            self._log("success", f"{task_label}：{reason}")
            yield from context.go_scene(34)
            if level_plan.get("action") == "hold_shengmai":
                return "success"
            self._schedule_daily_lingmai_next_check(
                payload,
                message=f"{reason}，30 分钟后复查",
                seconds=int(payload.get("lingmai_gathering_recheck_seconds") or 1800),
            )
            return "skipped"
        gathering_shape = self._find_shape(image285, "聚灵中")
        gathering_threshold = float(payload.get("lingmai_gathering_threshold") or self.overlay_threshold)
        gathering_score = (
            context.shape_score(285, "聚灵中", frame_data_url=frame)
            if gathering_shape is not None
            else 0.0
        )
        gathering_ocr_hit = False
        if gathering_score < gathering_threshold:
            gathering_ocr_hit = "聚灵中" in _sanitize_ocr_text(context.ocr_text(frame))
        self._log(
            "detail",
            f"{task_label}：#285 幂等校验「聚灵中」score={gathering_score:.0f}% "
            f"threshold={gathering_threshold:.0f}%，full_ocr={'命中' if gathering_ocr_hit else '未命中'}",
        )
        if (
            gathering_score >= gathering_threshold or gathering_ocr_hit
        ) and level_plan.get("action") != "try_shengmai":
            self._log("success", f"{task_label}：#285 已在聚灵中，不再重复抢座，退出后定时复查")
            yield from context.go_scene(34)
            self._schedule_daily_lingmai_next_check(
                payload,
                message="当前仍在聚灵中，30 分钟后复查是否被驱离",
                seconds=int(payload.get("lingmai_gathering_recheck_seconds") or 1800),
            )
            return "skipped"

        if self._find_shape(image285, "窗口") is None:
            raise RuntimeError(f"{task_label}：缺少 #285「窗口」shape 标注，无法按等级检索灵脉")
        target_level_name = str(level_plan.get("level_name") or "仙煌神脉")
        target_room_id = int(level_plan.get("room_id") or LINGMAI_UNION_SHENMAI_ROOM_ID)
        ctx["_daily_lingmai_target_room_id"] = target_room_id
        ctx["_daily_lingmai_target_level_name"] = target_level_name
        payload["__lingmai_expected_room_id"] = target_room_id
        search_direction: Literal["up", "down"] | None = (
            "up" if target_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID else None
        )
        if bool(payload.get("stop_after_click_285_empty")):
            yield from self._enter_daily_lingmai_level(
                context,
                payload,
                level_name=target_level_name,
                search_direction=search_direction,
                wait_for_landing=False,
                frame=frame,
                task_label=task_label,
            )
            self._log("success", f"{task_label}：试运行已点击 #285「{target_level_name}」，按 payload 停止")
            return "success"
        yield from self._enter_daily_lingmai_level(
            context,
            payload,
            level_name=target_level_name,
            search_direction=search_direction,
            frame=frame,
            task_label=task_label,
        )
        _wait_scene_match = yield from context.wait_scene([286], wait=5.0, required=False)
        (scene_next, score_next, frame_next) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text_next = context.ocr_text(frame_next)
        self._log("success", f"{task_label}：已到达 #286「{target_level_name}」座位页，当前 #{scene_next if scene_next is not None else 'unknown'} {score_next:.0f}%，OCR={text_next[:160]}")
        return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, context, frame_next, task_label=task_label))

    def _fallback_daily_lingmai_to_shenmai(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: Any,
        *,
        task_label: str,
    ):
        """Leave a failed Shengmai attempt and enter Shenmai as the fallback."""

        self._raise_if_stopped(stop_event)
        self._log("action", f"{task_label}：圣脉暂无合法座位，返回 #285 并进入仙煌神脉保底")
        yield from context.wait_click_then_scene(
            286,
            "返回",
            285,
            timeout=float(payload.get("lingmai_select_return_timeout") or 15.0),
        )
        ctx["_daily_lingmai_target_room_id"] = LINGMAI_UNION_SHENMAI_ROOM_ID
        ctx["_daily_lingmai_target_level_name"] = "仙煌神脉"
        yield from self._enter_daily_lingmai_level(
            context,
            payload,
            level_name="仙煌神脉",
            task_label=task_label,
        )
        # The seat list may retain Shengmai's scroll offset across this tier
        # switch, so the fallback search must explicitly start from the top.
        payload["lingmai_kick_reset_to_top"] = True
        _wait_scene_match = yield from context.wait_scene([286], wait=5.0, required=False)
        (_scene, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        return (yield from self._continue_daily_lingmai_from_select_slot(
            ctx,
            stop_event,
            payload,
            context,
            frame,
            task_label=task_label,
        ))

    def _continue_daily_lingmai_from_select_slot(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: BehaviorTreeContext,
        frame: str | None,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image286 = images.get(286)
        if not isinstance(image286, dict):
            raise RuntimeError(f"{task_label}：缺少 #286 神脉座位页标注，无法选择座位动作")

        self._raise_if_stopped(stop_event)
        threshold = float(payload.get("lingmai_select_empty_slot_threshold") or self.overlay_threshold)
        if not frame:
            frame = context.cur_frame(update=True)
        daily_status = (
            ctx.get("_daily_lingmai_status")
            if isinstance(ctx.get("_daily_lingmai_status"), dict)
            else {}
        )
        if not daily_status.get("available"):
            daily_status = refresh_lingmai_daily_status()
            ctx["_daily_lingmai_status"] = daily_status
        if daily_status.get("available"):
            self._log(
                "detail",
                f"{task_label}：#286 只读状态复核今日剩余聚灵 "
                f"{daily_status.get('remaining_milliseconds')}ms，"
                f"source={daily_status.get('source')} protocol={daily_status.get('protocol')}",
            )
        if daily_status.get("available") and daily_status.get("completed"):
            self._log(
                "success",
                f"{task_label}：#286 只读状态确认 leftListenTime=0，今日 3 小时聚灵已完成",
            )
            yield from context.go_scene(34)
            return "success"
        level_plan = (
            self._daily_lingmai_level_plan(daily_status)
            if daily_status.get("available")
            else {"action": "enter_shenmai", "room_id": LINGMAI_UNION_SHENMAI_ROOM_ID}
        )
        target_room_id = int(
            ctx.get("_daily_lingmai_target_room_id")
            or level_plan.get("room_id")
            or LINGMAI_UNION_SHENMAI_ROOM_ID
        )
        payload["__lingmai_expected_room_id"] = target_room_id
        target_level_name = str(
            ctx.get("_daily_lingmai_target_level_name")
            or ("天罡圣脉" if target_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID else "仙煌神脉")
        )
        empty_shape = self._find_shape(image286, "选择空位")
        empty_score = (
            context.shape_score(286, "选择空位", frame_data_url=frame)
            if empty_shape is not None
            else 0.0
        )
        self._log(
            "detail",
            f"{task_label}：优先校验 #286「选择空位」score={empty_score:.0f}% threshold={threshold:.0f}%",
        )
        selection = refresh_and_select_lingmai_seat_action(
            target_room_id=target_room_id,
        )
        if not selection.get("ok"):
            selection_status = str(selection.get("status") or "")
            selection_reason = str(selection.get("reason") or "unknown")
            transient_reasons = {
                "lingmai_runtime_unavailable",
                "self_seat_missing",
                "self_profile_missing",
                "self_profile_incomplete",
                "seat_roster_missing",
                "seat_roster_incomplete",
                "self_veins_group_missing",
            }
            if selection_status == "runtime_unavailable" or selection_reason in transient_reasons:
                yield from context.go_scene(34)
                next_time = self._schedule_daily_lingmai_next_check(
                    payload,
                    message=(
                        f"「{target_level_name}」运行态事实暂不完整 "
                        f"({selection_status or 'invalid_facts'}/{selection_reason})"
                    ),
                    seconds=lingmai_facts_retry_seconds(payload),
                )
                self._log(
                    "skip",
                    f"{task_label}：未在事实不完整时点击座位，已返回 #34，{next_time} 重新读取",
                )
                return "skipped"
            raise RuntimeError(
                f"{task_label}：读取「{target_level_name}」座位或自身战力失败，"
                f"status={selection_status} reason={selection_reason}"
            )
        action = str(selection.get("action") or "")
        if action == "fallback_shenmai":
            return (yield from self._fallback_daily_lingmai_to_shenmai(
                ctx,
                stop_event,
                payload,
                context,
                task_label=task_label,
            ))
        if action == "already_seated":
            self_seat = selection.get("self_seat") if isinstance(selection.get("self_seat"), dict) else {}
            self._log(
                "success",
                f"{task_label}：只读状态确认自己已在「{target_level_name}」座位 {self_seat.get('seat_id')}，"
                "不再占空位或驱离玩家",
            )
            yield from context.go_scene(34)
            if target_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID:
                return "success"
            self._schedule_daily_lingmai_next_check(
                payload,
                message=f"Runtime 已确认仍在「{target_level_name}」聚灵中，30 分钟后复查是否被驱离",
                seconds=int(
                    payload.get("lingmai_gathering_recheck_seconds")
                    or 1800
                ),
            )
            return "skipped"
        if action == "retry":
            retry_at_ms = selection.get("retry_at_ms")
            retry_reason = str(selection.get("retry_reason") or "no_target")
            self_seat_facts = selection.get("self_seat_facts") if isinstance(selection.get("self_seat_facts"), dict) else {}
            if (
                target_room_id == LINGMAI_UNION_SHENGMAI_ROOM_ID
                and self_seat_facts.get("seated") is not True
            ):
                return (yield from self._fallback_daily_lingmai_to_shenmai(
                    ctx,
                    stop_event,
                    payload,
                    context,
                    task_label=task_label,
                ))
            yield from context.go_scene(34)
            next_time = self._schedule_daily_lingmai_next_check(
                payload,
                message=(
                    f"「{target_level_name}」当前无可驱离目标，等待最早可击败目标保护结束"
                    if retry_reason == "earliest_beatable_protection_end"
                    else f"「{target_level_name}」当前及保护期内均无可击败的非友军"
                ),
                seconds=int(payload.get("lingmai_no_target_retry_seconds") or 1800),
                retry_at_ms=int(retry_at_ms) if retry_at_ms is not None else None,
            )
            self._log("skip", f"{task_label}：已返回 #34，{next_time} 重新读取座位清单")
            return "skipped"
        if action == "kick":
            target = selection.get("target") if isinstance(selection.get("target"), dict) else None
            if target is None:
                raise RuntimeError(f"{task_label}：选人策略返回 kick 但缺少目标，已停止且未点击")
            eligible_targets = [
                item
                for item in selection.get("eligible_targets") or []
                if isinstance(item, dict)
            ]
            visible_roster_text = context.ocr_text(update=True)
            visible_target = select_visible_lingmai_target(
                eligible_targets,
                visible_roster_text,
            )
            if visible_target is not None:
                if visible_target.get("seat_id") != target.get("seat_id"):
                    self._log(
                        "info",
                        f"{task_label}：最低战力目标「{target.get('name')}」已不在当前 GUI 视窗，"
                        f"改用同一 Runtime 快照中仍可见的安全目标「{visible_target.get('name')}」",
                    )
                target = visible_target
            self._log(
                "info",
                f"{task_label}：在「{target_level_name}」选择最低战力可驱离非友军「{target.get('name')}」，"
                f"seat_id={target.get('seat_id')}，战力 {float(target.get('battle_score') or 0):.3e}",
            )
            try:
                return (yield from self._click_daily_lingmai_kick_target(
                    ctx,
                    stop_event,
                    payload,
                    context,
                    target_player=target,
                    task_label=task_label,
                ))
            except _DailyLingmaiKickTargetLost as exc:
                yield from context.go_scene(34)
                next_time = self._schedule_daily_lingmai_next_check(
                    payload,
                    message=f"目标「{target.get('name')}」在 #286 GUI 列表中未达到可信定位门槛，已停止点击并返回 #34",
                    seconds=int(payload.get("lingmai_no_target_retry_seconds") or 1800),
                )
                self._log("skip", f"{task_label}：{exc}；已返回 #34，{next_time} 重新读取座位清单")
                return "skipped"
        if action != "occupy_empty":
            raise RuntimeError(f"{task_label}：未知神脉座位动作 {action!r}，已停止且未点击")

        image287 = images.get(287)
        image288 = images.get(288)
        if not isinstance(image287, dict):
            raise RuntimeError(f"{task_label}：缺少 #287「前往灵脉」确认弹窗标注，无法确认占领")
        if not isinstance(image288, dict):
            raise RuntimeError(f"{task_label}：缺少 #288「占领」过渡后场景标注，无法确认灵脉占领")
        if self._find_shape(image286, "选择空位") is None:
            raise RuntimeError(f"{task_label}：缺少 #286「选择空位」shape 标注，无法二次校验")
        if self._find_shape(image286, "占领") is None:
            raise RuntimeError(f"{task_label}：缺少 #286「占领」shape 标注，无法点击占领")
        if self._find_shape(image286, "返回") is None:
            raise RuntimeError(f"{task_label}：缺少 #286「返回」shape 标注，无法在校验失败时安全返回")
        if self._find_shape(image287, "前往灵脉") is None:
            raise RuntimeError(f"{task_label}：缺少 #287「前往灵脉」shape 标注，无法识别占领确认弹窗")
        if self._find_shape(image287, "确认") is None:
            raise RuntimeError(f"{task_label}：缺少 #287「确认」shape 标注，无法确认占领")
        if self._find_shape(image288, "占领") is None:
            raise RuntimeError(f"{task_label}：缺少 #288「占领」shape 标注，无法点击过渡后的占领按钮")

        # ``current_scene([286])`` can identify the stable page title before the
        # dynamic seat rows finish rendering.  Runtime seat selection may then
        # take several seconds, so reusing the entry frame here turns an early
        # 0% into a fake "second check" even though the empty row is now visible.
        # Wait on fresh frames after the authoritative Runtime decision instead.
        try:
            fresh_empty_frame = yield from context.wait_shape(
                286,
                "选择空位",
                timeout=float(payload.get("lingmai_select_empty_slot_timeout") or 10.0),
                threshold=threshold,
                label=f"{task_label}：等待 #286 空位行完成渲染",
            )
            score = context.shape_score(
                286,
                "选择空位",
                frame_data_url=fresh_empty_frame,
            )
        except TimeoutError:
            score = 0.0
        self._log("detail", f"{task_label}：新鲜帧复核 #286「选择空位」score={score:.0f}% threshold={threshold:.0f}%")
        if score < threshold:
            self._log("warning", f"{task_label}：#286「选择空位」新鲜帧匹配不足 {score:.0f}%，点击返回")
            yield from context.wait_click(286, "返回")
            yield from context.wait_action_settle(float(payload.get("lingmai_select_return_settle_seconds") or 2.0))
            raise RuntimeError(f"{task_label}：#286「选择空位」新鲜帧校验失败 {score:.0f}%<{threshold:.0f}%，已返回")

        self._log("success", f"{task_label}：#286「选择空位」新鲜帧校验通过 {score:.0f}%，点击「占领」")
        yield from context.wait_click(286, "占领")
        yield from context.wait_action_settle(float(payload.get("lingmai_occupy_click_settle_seconds") or 2.0))
        _wait_scene_match = yield from context.wait_scene([285, 286], wait=5.0, required=False)
        (scene_next, score_next, frame_next) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text_next = context.ocr_text(frame_next)
        text_compact = _sanitize_ocr_text(text_next)
        if re.search(r"聚灵体力符持有数量[:：]?0(?:\D|$)", text_compact):
            return self._record_daily_lingmai_resource_insufficient(
                payload,
                message="聚灵体力符持有数量为 0",
            )
        if "前往灵脉" in text_compact:
            self._click_daily_lingmai_go_button(context, frame_next, task_label=task_label)
            yield from context.wait_action_settle(float(payload.get("lingmai_go_button_settle_seconds") or 2.0))
        else:
            self._log(
                "warning",
                f"{task_label}：点击 #286「占领」后未直接识别到 #287，继续等待 #288；"
                f"当前 {'#' + str(scene_next) if scene_next is not None else 'unknown'} {score_next:.0f}%，OCR={text_next[:120]}",
            )

        yield from context.wait_scene(
            [288, 380],
            wait=float(payload.get("lingmai_after_confirm_timeout") or 90.0),
            label=f"{task_label}：点击 #287「确认」后等待真实 #288 占领页",
        )
        _wait_scene_match = yield from context.wait_scene([288, 380], wait=5.0, required=False)
        (scene_after, score_after, frame_after) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text_after = context.ocr_text(frame_after)
        if scene_after == 380:
            return (yield from self._recover_daily_lingmai_occupied_arrival(
                ctx, stop_event, payload, context, task_label=task_label
            ))
        if scene_after != 288:
            raise RuntimeError(f"{task_label}：到达场景为 #{scene_after}，未确认空位，停止占领")
        self._log("success", f"{task_label}：已到达 #288，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，点击「占领」")
        return (yield from self._continue_daily_lingmai_from_final_occupy(ctx, stop_event, payload, context, task_label=task_label))

    def _recover_daily_lingmai_occupied_arrival(
        self, ctx, stop_event, payload, context, *, task_label: str,
    ):
        """Arrival can show an occupied seat; never infer permission to kick.

        Dismiss the dialogue and re-enter the normal fresh-state decision tree.
        The resulting page may be #588 (existing seat), not the original list.
        Bound repeated occupancy changes across recursive entry payload copies.
        """
        attempts = int(payload.get("__lingmai_occupied_arrivals") or 0)
        if attempts >= 2:
            raise RuntimeError(f"{task_label}：连续两次到达已占座位，停止并等待重新检查")
        payload["__lingmai_occupied_arrivals"] = attempts + 1
        self._log("warning", f"{task_label}：到达 #380 已占座位，关闭对白并重新确认座位状态")
        yield from context.wait_click(380, "打扰了")
        match = yield from context.wait_scene(
            [588, 286, 285, 403, 34], wait=15.0, required=False,
            label=f"{task_label}：关闭已占座位对白",
        )
        if match is None or match.scene_id == 380:
            raise RuntimeError(f"{task_label}：未确认关闭 #380，停止重新选位")
        return (yield from self._run_daily_lingmai_task(ctx, stop_event, payload))

    def _click_daily_lingmai_kick_target(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: BehaviorTreeContext,
        *,
        target_player: Mapping[str, Any],
        task_label: str,
    ):
        """Find the selected #286 player row and click its aligned kick button.

        The read-only Runtime-selected seat id is the identity evidence. The exact player
        name is used only to find its visible row.  The click point is derived
        from the user-annotated [姓名] -> [驱离按钮] offset, never from a global
        fixed coordinate or an OCR-discovered button belonging to another row.
        """

        target_id = str(target_player.get("seat_id") or target_player.get("id") or "").strip()
        target_name = _sanitize_ocr_text(target_player.get("name"))
        target_name_match = normalize_ocr_name(target_name)
        # Some Lingmai role names are stored as a pipe-separated decorated
        # value while the seat list renders only one visible segment.  Keep the
        # full value as identity evidence, but let OCR locate any non-trivial
        # rendered segment instead of demanding the invisible suffix.
        target_name_variants = lingmai_name_variants(target_name)
        if not target_id or not target_name_match:
            raise RuntimeError(f"{task_label}：驱离目标缺少稳定座位 ID 或姓名，已停止且未点击")
        if bool(target_player.get("excluded") or target_player.get("is_ally")):
            raise RuntimeError(f"{task_label}：驱离目标被标记为同盟/排除，已停止且未点击")

        name_shape = context.shape(286, "姓名")
        button_shape = context.shape(286, "驱离按钮")
        view286 = context.view(286)
        name_box = context.runner._box(name_shape.raw, view286.raw)
        button_box = context.runner._box(button_shape.raw, view286.raw)
        name_center_y = float(name_box.get("y") or 0) + float(name_box.get("h") or 0) / 2
        button_center_x = float(button_box.get("x") or 0) + float(button_box.get("w") or 0) / 2
        button_center_y = float(button_box.get("y") or 0) + float(button_box.get("h") or 0) / 2
        offset_y = button_center_y - name_center_y
        frame_width, frame_height = context.runner._frame_size(view286.raw)
        safe_top_ratio = min(
            0.45,
            max(0.0, float(payload.get("lingmai_kick_safe_top_ratio") or 0.34)),
        )
        safe_bottom_ratio = min(
            1.0,
            max(safe_top_ratio + 0.1, float(payload.get("lingmai_kick_safe_bottom_ratio") or 0.78)),
        )
        safe_top = frame_height * safe_top_ratio
        safe_bottom = frame_height * safe_bottom_ratio
        safe_left = max(0.0, min(float(name_box.get("x") or 0), float(button_box.get("x") or 0)))
        safe_right = min(
            frame_width,
            max(
                float(name_box.get("x") or 0) + float(name_box.get("w") or 0),
                float(button_box.get("x") or 0) + float(button_box.get("w") or 0),
            ),
        )
        safe_viewport = Shape(
            {
                "id": "lingmai-kick-safe-viewport",
                "title": "灵脉驱离安全视口",
                "x": safe_left / max(1.0, frame_width),
                "y": safe_top / max(1.0, frame_height),
                "w": max(1.0, safe_right - safe_left) / max(1.0, frame_width),
                "h": max(1.0, safe_bottom - safe_top) / max(1.0, frame_height),
                "loadDirection": "down",
            },
            parent_view=view286,
        )

        max_scrolls = max(0, int(payload.get("lingmai_kick_max_scrolls") or 12))
        minimum_name_similarity = min(
            1.0,
            max(
                0.0,
                float(
                    payload.get("lingmai_kick_name_min_similarity")
                    or DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD
                ),
            ),
        )

        def collect_candidates(frame_data_url: str) -> list[dict[str, Any]]:
            # The annotated ``姓名`` box is a row anchor, not an OCR crop.
            # Live decorated names can extend beyond its right edge (for
            # example ``虚天、张舒`` was truncated to ``虚天``), so query
            # the already-authorized safe viewport spanning name to the
            # same-row kick button.  Click geometry remains annotation-based.
            cached_ocr = self._shared_spatial_ocr_result(
                ctx,
                frame_data_url,
                options={"return_word_box": True},
            )
            tokens = query_spatial_ocr(
                cached_ocr.get("tokens") or [],
                {
                    "x": safe_left,
                    "y": safe_top,
                    "w": max(1.0, safe_right - safe_left),
                    "h": max(1.0, safe_bottom - safe_top),
                },
            )["tokens"]
            candidates: list[dict[str, Any]] = []
            for fragment in group_ocr_tokens(tokens):
                fragment_x = float(fragment.get("x") or 0)
                fragment_w = float(fragment.get("w") or 0)
                horizontal_overlap = max(
                    0.0,
                    min(
                        fragment_x + fragment_w,
                        float(name_box.get("x") or 0) + float(name_box.get("w") or 0),
                    ) - max(fragment_x, float(name_box.get("x") or 0)),
                )
                if horizontal_overlap / max(
                    1.0,
                    min(fragment_w, float(name_box.get("w") or 0)),
                ) < 0.3:
                    continue
                fragment_text = _sanitize_ocr_text(fragment.get("text"))
                fragment_tokens = query_spatial_ocr(tokens, fragment)["tokens"]
                target_box = None
                for target_variant in target_name_variants:
                    target_box = locate_text_box(fragment_tokens, target_variant)
                    if target_box is not None:
                        break
                if target_box is None and target_name_match != target_name:
                    target_box = locate_text_box(fragment_tokens, target_name_match)
                if target_box is None:
                    target_box = {
                        key: float(fragment.get(key) or 0)
                        for key in ("x", "y", "w", "h")
                    }
                candidates.append({"box": target_box, "text": fragment_text})
            for candidate in candidates:
                candidate["similarity"] = max(
                    ocr_name_similarity(variant, candidate["text"])
                    for variant in target_name_variants
                )
                candidate["passed_threshold"] = (
                    float(candidate["similarity"]) >= minimum_name_similarity
                )
            return sorted(
                candidates,
                key=lambda candidate: -float(candidate["similarity"]),
            )

        def candidate_click_box(candidate: Mapping[str, Any]) -> dict[str, float]:
            target_box = candidate["box"]
            target_top = float(target_box.get("y") or 0)
            target_bottom = target_top + float(target_box.get("h") or 0)
            target_center_y = target_top + float(target_box.get("h") or 0) / 2
            predicted_button_center_y = target_center_y + offset_y
            predicted_button_top = predicted_button_center_y - float(button_box.get("h") or 0) / 2
            predicted_button_bottom = predicted_button_center_y + float(button_box.get("h") or 0) / 2
            return {
                "x": min(float(target_box.get("x") or 0), float(button_box.get("x") or 0)),
                "y": min(target_top, predicted_button_top),
                "w": max(
                    float(target_box.get("x") or 0) + float(target_box.get("w") or 0),
                    float(button_box.get("x") or 0) + float(button_box.get("w") or 0),
                ) - min(float(target_box.get("x") or 0), float(button_box.get("x") or 0)),
                "h": max(target_bottom, predicted_button_bottom) - min(target_top, predicted_button_top),
            }

        def candidate_is_click_safe(candidate: Mapping[str, Any]) -> bool:
            click_box = candidate_click_box(candidate)
            return (
                float(click_box["x"]) >= safe_left
                and float(click_box["x"]) + float(click_box["w"]) <= safe_right
                and float(click_box["y"]) >= safe_top
                and float(click_box["y"]) + float(click_box["h"]) <= safe_bottom
            )

        def click_candidate(best: dict[str, Any], *, screen_label: str, fallback: bool = False):
            target_box = best["box"]
            target_y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
            click_x = button_center_x
            click_y = target_y + offset_y
            if not (0 < click_x < frame_width and 0 < click_y < frame_height):
                raise RuntimeError(
                    f"{task_label}：目标「{target_name}」对应驱离按钮中心超出画面，已停止且未点击"
                )
            mode = "全列表最高相似度兜底" if fallback else "达到阈值"
            self._log(
                "action",
                f"{task_label}：在 #286 {screen_label}按{mode}匹配目标「{target_name}」"
                f"到 OCR「{best['text']}」{float(best['similarity']):.0%}，"
                "点击同条目「驱离按钮」",
            )
            context.click_frame_point(286, click_x, click_y)
            yield from context.wait_scene(
                [380],
                wait=float(payload.get("lingmai_kick_to_380_timeout") or 60.0),
                label=f"{task_label}：点击「{target_name}」驱离按钮后等待 #380",
            )
            _wait_scene_match = yield from context.wait_scene([380], wait=5.0, required=False)
            (scene_id, score, frame380) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 380:
                raise RuntimeError(f"{task_label}：驱离后未确认到达 #380，已停止后续点击")
            self._log("success", f"{task_label}：已到达 #380（{score:.0f}%），OCR={context.ocr_text(frame380)[:120]}")
            return (yield from self._complete_daily_lingmai_kick(context, payload, task_label=task_label))

        # The game preserves the seat-list scroll offset when switching
        # between Shengmai and Shenmai.  A down-only search from that retained
        # offset can therefore declare "bottom" while the selected target is
        # above the viewport.  Normalize to the top before scanning downward.
        if bool(payload.get("lingmai_kick_reset_to_top", False)):
            context.drag_shape_to_frame_edge(
                286,
                "姓名",
                direction="down",
                duration=float(payload.get("lingmai_kick_scroll_seconds") or 0.8),
            )
            yield from context.wait_action_settle(
                float(payload.get("lingmai_kick_scroll_settle_seconds") or 1.0)
            )
            _wait_scene_match = yield from context.wait_scene([286], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 286:
                raise RuntimeError(f"{task_label}：复位座位列表到顶部时离开 #286，已停止且未点击")
            self._log("detail", f"{task_label}：已先复位 #286 座位列表到顶部，再向下查找目标")

        reached_list_end = False
        best_below_threshold: dict[str, Any] | None = None
        for index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            frame = context.cur_frame(update=True)
            candidates = collect_candidates(frame)
            if candidates:
                best = candidates[0]
                if best_below_threshold is None or float(best["similarity"]) > float(best_below_threshold["similarity"]):
                    best_below_threshold = dict(best)
                if bool(best.get("passed_threshold")):
                    if candidate_is_click_safe(best):
                        return (yield from click_candidate(best, screen_label=f"第 {index + 1} 屏", fallback=False))
                    self._log(
                        "detail",
                        f"{task_label}：目标「{target_name}」当前贴近 #286 列表裁剪边缘，"
                        "先小幅复位后重新确认同一行驱离按钮",
                    )
                    direction = yield from context.nudge_shape_content_for_box(
                        safe_viewport,
                        candidate_click_box(best),
                        edge_margin_ratio=float(payload.get("lingmai_kick_edge_margin_ratio") or 0.18),
                        nudge_ratio=float(payload.get("lingmai_kick_nudge_ratio") or 0.18),
                        duration=float(payload.get("lingmai_kick_nudge_seconds") or 0.8),
                        settle_seconds=float(payload.get("lingmai_kick_scroll_settle_seconds") or 1.0),
                    )
                    if direction is not None:
                        continue
            if index >= max_scrolls:
                break
            before_signature = context.image_signature_bytes_in_shape(
                name_shape,
                frame_data_url=frame,
            )
            context.drag_shape_to_frame_edge(
                286,
                "姓名",
                direction="up",
                duration=float(payload.get("lingmai_kick_scroll_seconds") or 0.8),
            )
            yield from context.wait_action_settle(float(payload.get("lingmai_kick_scroll_settle_seconds") or 1.0))
            _wait_scene_match = yield from context.wait_scene([286], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 286:
                raise RuntimeError(f"{task_label}：滚动寻找目标时离开 #286，已停止且未点击")
            after_signature = context.image_signature_bytes_in_shape(
                name_shape,
                frame_data_url=_frame,
            )
            similarity = context.image_signature_similarity(before_signature, after_signature)
            self._log(
                "detail",
                f"{task_label}：#286 滚动后姓名识别区相似度 {similarity:.1f}%",
            )
            if similarity >= DEFAULT_SCROLL_UNCHANGED_THRESHOLD:
                reached_list_end = True
                self._log(
                    "info",
                    f"{task_label}：#286 滚动后列表画面未变化，确认已到底并停止拖拽",
                )
                break
        if best_below_threshold is None:
            ending = "列表已到底" if reached_list_end else "滚动达到上限"
            raise _DailyLingmaiKickTargetLost(f"#286 {ending}且没有可用 OCR 姓名，已停止且未点击")

        raise _DailyLingmaiKickTargetLost(
            f"#286 最高相似度 OCR「{best_below_threshold['text']}」"
            f"{float(best_below_threshold['similarity']):.0%} 低于门槛 "
            f"{float(minimum_name_similarity):.0%}，拒绝兜底点击目标「{target_name}」"
        )

    def _complete_daily_lingmai_kick(
        self, context: BehaviorTreeContext, payload: dict[str, Any], *, task_label: str,
    ) -> str:
        # A rejection can follow #380 itself, not only #381. Keep its evidence
        # owned throughout the transaction instead of letting popup cleanup
        # turn a rejected challenge into an imaginary battle on #588.
        with context.expect_views(47):
            return (yield from self._complete_daily_lingmai_kick_owned(
                context, payload, task_label=task_label,
            ))

    def _complete_daily_lingmai_kick_owned(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        """Complete #380 -> battle -> #306 and reuse the normal Lingmai tail."""

        confirmation_scene_id: int | None = None
        open_attempts = max(
            1,
            min(3, int(payload.get("lingmai_kick_open_confirm_attempts") or 3)),
        )
        yield from context.wait_action_settle(
            float(payload.get("lingmai_kick_button_ready_seconds") or 3.0)
        )
        for open_attempt in range(1, open_attempts + 1):
            # A successful click can open #381 only after the preceding
            # settle/sample has completed.  Re-authorize every retry from a
            # fresh scene first: if the delayed confirmation (or a direct
            # battle landing) is already present, consume it instead of
            # waiting for the now-absent #380 button and reporting a false
            # timeout.
            _wait_scene_match = yield from context.wait_scene([47, 381, 318, 443, 374, 382, 375, 380, 588], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id in {47, 381, 318, 443, 374, 382, 375}:
                confirmation_scene_id = int(scene_id)
                break
            if scene_id not in {380, 588}:
                raise RuntimeError(
                    f"{task_label}：重试点击 #380「驱离」前场景身份不可靠：scene={scene_id}"
                )
            yield from context.wait_click(380, "驱离")
            yield from context.wait_action_settle(
                float(payload.get("lingmai_kick_open_confirm_settle_seconds") or 3.0)
            )
            _wait_scene_match = yield from context.wait_scene([47, 381, 318, 443, 374, 382, 375, 380, 588], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id in {47, 381, 318, 443, 374, 382, 375}:
                confirmation_scene_id = int(scene_id)
                break
            if scene_id in {None, 588}:
                # #588 is the underlying occupied-seat page.  While the
                # business confirmation is animating/OCR is warming up, a
                # single sample can still project that background (or
                # unknown).  Neither state owns #380[驱离], so retrying the
                # old button here can only wait on a vanished Shape and lose
                # the already-open confirmation to popup cleanup.  Keep the
                # transaction bound to its legal overlay/direct successors;
                # only an exact fresh #380 below may authorize another click.
                try:
                    confirmation = yield from context.wait_scene_exact(
                        [47, 381, 318, 443, 374, 382, 375],
                        observation_scenes=[380, 588],
                        timeout=float(
                            payload.get("lingmai_kick_confirm_appear_timeout") or 20.0
                        ),
                        label=(
                            f"{task_label}：#380「驱离」点击后等待业务确认层/战前对白"
                        ),
                    )
                except TimeoutError:
                    _wait_scene_match = yield from context.wait_scene([47, 381, 318, 443, 374, 382, 375, 380, 588], wait=5.0, required=False)
                    (scene_id, _score, _frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                    if scene_id in {47, 381, 318, 443, 374, 382, 375}:
                        confirmation_scene_id = int(scene_id)
                        break
                    if open_attempt < open_attempts and scene_id == 380:
                        self._log(
                            "warning",
                            f"{task_label}：第 {open_attempt} 次点击 #380「驱离」后"
                            "确认层未出现，fresh frame 仍精确为 #380，有限重试",
                        )
                        continue
                    raise RuntimeError(
                        f"{task_label}：点击 #380「驱离」后未进入确认/战斗链："
                        f"scene={scene_id}；未重复点击背景/unknown"
                    )
                confirmation_scene_id = int(
                    confirmation.id if isinstance(confirmation, View) else confirmation
                )
                break
            if open_attempt < open_attempts and scene_id == 380:
                self._log(
                    "warning",
                    f"{task_label}：第 {open_attempt} 次点击 #380「驱离」未打开确认层，"
                    "fresh frame 仍精确为 #380，保留同一目标并有限重试",
                )
                continue
            raise RuntimeError(
                f"{task_label}：点击 #380「驱离」后未进入确认/战斗链：scene={scene_id}"
            )
        # The target row can refresh while the confirmation layer is opening.
        # Bind the click to #381[确定]'s current-frame OCR condition instead of
        # sleeping and then clicking a stale fixed coordinate.  Direct Shape
        # matching is performed only after ``wait_click``'s mandatory popup
        # guard has cleared interruptions.
        if confirmation_scene_id == 47:
            prompt_text = "".join(
                str(token.get("text") or "")
                for token in context.full_frame_ocr_tokens()
            )
            raise RuntimeError(
                f"{task_label}：驱离入口后出现 #47 提示，保留现场且未关闭；"
                f"OCR={prompt_text[:800]}"
            )
        if confirmation_scene_id in {374, 382, 375}:
            return (yield from self._finish_daily_lingmai_kick_battle(
                context, payload, task_label=task_label,
                battle_scene_id=confirmation_scene_id,
            ))
        if confirmation_scene_id == 381:
            # #47 can carry a challenge rejection. Claim it before clicking:
            # the generic popup guard otherwise dismisses the only evidence
            # and the flow later mistakes the room background for progress.
            with context.expect_views(47):
                pre_battle_scene = yield from context.wait_click_then_scene(
                    381,
                    "确定",
                    [47, 318, 443],
                    timeout=float(payload.get("lingmai_kick_battle_dialogue_timeout") or 45.0),
                    max_clicks=1,
                    label=f"{task_label}：点击 #381「确定」后等待提示、更换确认或战前对白",
                )
                pre_battle_scene_id = int(
                    pre_battle_scene.id if isinstance(pre_battle_scene, View) else pre_battle_scene
                )
                if pre_battle_scene_id == 47:
                    # Keep the exact matched prompt frame; a fresh capture
                    # could already contain the next transition instead.
                    prompt_frame = getattr(pre_battle_scene, "frame_data_url", None)
                    prompt_text = "".join(
                        str(token.get("text") or "")
                        for token in context.full_frame_ocr_tokens(prompt_frame)
                    )
                    raise RuntimeError(
                        f"{task_label}：驱离确认后出现 #47 提示，保留现场且未关闭；"
                        f"OCR={prompt_text[:800]}"
                    )
        else:
            pre_battle_scene_id = int(confirmation_scene_id or 0)
        if pre_battle_scene_id == 443:
            yield from self._click_daily_lingmai_switch_confirm(
                context,
                payload,
                task_label=task_label,
                scene_id=443,
            )
            yield from context.wait_scene(
                [318],
                wait=float(payload.get("lingmai_kick_battle_dialogue_timeout") or 45.0),
                label=f"{task_label}：确认更换灵脉后等待 #318 战前对白",
            )
        battle_scene_id = yield from self._advance_daily_lingmai_kick_dialogue(
            context,
            # Runtime classifies the visually identical idle/battle #588
            # state after the dialogue callback has had time to run.
            terminal_scene_ids=(374, 382, 375, 588),
            timeout=float(payload.get("lingmai_kick_battle_start_timeout") or 60.0),
            label=f"{task_label}：推进战前对白直到战斗或胜利",
        )
        if battle_scene_id == 588:
            runtime_status = refresh_lingmai_daily_status()
            room_state = self._daily_lingmai_588_runtime_state(runtime_status)
            if room_state == "idle_unseated":
                payload["__lingmai_resume_after_idle_room"] = True
                self._log(
                    "info",
                    f"{task_label}：对白结束后未形成 replayRecord、体力/座位事务，"
                    "按目标状态竞争恢复，不把 #588 当作战斗",
                )
                return (yield from self._finish_daily_lingmai_to_world(
                    context,
                    payload,
                    task_label=task_label,
                    scene_id=588,
                ))
            if room_state == "stable_seated":
                return (yield from self._finish_daily_lingmai_to_world(
                    context,
                    payload,
                    task_label=task_label,
                    scene_id=588,
                ))
            if room_state == "unknown":
                raise RuntimeError(
                    f"{task_label}：对白结束到达 #588，但 Runtime 战斗/座位事实不完整，"
                    "保留现场且未离场"
                )
        return (yield from self._finish_daily_lingmai_kick_battle(
            context, payload, task_label=task_label, battle_scene_id=battle_scene_id
        ))

    def _finish_daily_lingmai_kick_battle(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        task_label: str,
        battle_scene_id: int,
        remaining_battles: int = 8,
    ) -> str:
        """Close a real result, including when a new formal attempt starts there.

        The server may assign the seat before the client plays the battle.
        Neither #588 nor Runtime seated authorizes skipping the result layer.
        """
        victory_scene_id = battle_scene_id
        if battle_scene_id in {374, 588}:
            deadline = time.monotonic() + float(
                payload.get("lingmai_kick_battle_finish_timeout") or 180.0
            )
            with context.expect_views(382, 375, 47):
                while time.monotonic() < deadline:
                    landed = yield from context.wait_scene(
                        [382, 375, 374, 588],
                        wait=min(10.0, max(0.0, deadline - time.monotonic())),
                        required=False,
                        label=f"{task_label}：等待真实战斗结果 #382/#375，过渡背景不授权离场",
                    )
                    victory_scene_id = int(landed) if landed is not None else None
                    if victory_scene_id in {382, 375}:
                        break
                    if victory_scene_id not in {None, 374, 588}:
                        # A transition frame can match an unrelated global
                        # scene before the victory sheet materializes. This
                        # phase only observes: keep its original deadline and
                        # never click or depart on that incidental match.
                        self._log(
                            "warning",
                            f"{task_label}：结果过渡识别为 #{victory_scene_id}，"
                            "继续限时等待真实胜负页，未点击或离场",
                        )
                    yield from context.wait_action_settle(1.0)
                else:
                    raise RuntimeError(f"{task_label}：等待战斗结果超时，保留现场且未离场")
        if victory_scene_id not in {382, 375}:
            raise RuntimeError(f"{task_label}：#{victory_scene_id} 不是可关闭的战斗结果")
        if victory_scene_id == 382:
            yield from self._close_daily_lingmai_victory_layers(
                context,
                payload,
                task_label=task_label,
            )
        else:
            yield from context.wait_click(victory_scene_id, "关闭")
            yield from context.wait_action_settle(
                float(payload.get("lingmai_kick_victory_close_settle_seconds") or 2.0)
            )
        return (yield from self._finish_daily_lingmai_post_battle(
            context, payload, task_label=task_label, remaining_battles=remaining_battles
        ))

    def _finish_daily_lingmai_post_battle(
        self, context: BehaviorTreeContext, payload: dict[str, Any], *, task_label: str,
        remaining_battles: int = 8,
    ) -> str:
        """Drain real #318/#303 dialogue before leaving a stable room."""
        post_battle_scene_id = yield from self._advance_daily_lingmai_kick_dialogue(
            context,
            terminal_scene_ids=(374, 382, 375, 443, 305, 306, 85, 186, 285, 588, 34),
            timeout=float(payload.get("lingmai_kick_summary_timeout") or 45.0),
            label=f"{task_label}：推进战后对白直到换座/入座确认或稳定灵脉场景",
        )
        if post_battle_scene_id in {374, 382, 375}:
            if remaining_battles <= 0:
                raise RuntimeError(f"{task_label}：连续战斗达到上限，保留现场")
            return (yield from self._finish_daily_lingmai_kick_battle(
                context, payload, task_label=task_label,
                battle_scene_id=post_battle_scene_id, remaining_battles=remaining_battles - 1,
            ))
        if post_battle_scene_id == 443:
            return (yield from self._confirm_daily_lingmai_switch_popup(
                context,
                payload,
                task_label=task_label,
                scene_id=443,
            ))
        if post_battle_scene_id == 305:
            return (yield from self._confirm_daily_lingmai_gather(
                context,
                payload,
                task_label=task_label,
            ))
        else:
            _wait_scene_match = yield from context.wait_scene([post_battle_scene_id, 374, 382, 375, 318, 303, 588], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        # A foreground battle may appear after the dialogue's background hit.
        # Re-dispatch the fresh observation before handing anything to goto.
        if scene_id in {374, 382, 375}:
            if remaining_battles <= 0:
                raise RuntimeError(f"{task_label}：连续战斗达到上限，保留现场")
            return (yield from self._finish_daily_lingmai_kick_battle(
                context, payload, task_label=task_label,
                battle_scene_id=scene_id, remaining_battles=remaining_battles - 1,
            ))
        return (yield from self._finish_daily_lingmai_to_world(
            context,
            payload,
            task_label=task_label,
            scene_id=scene_id,
            frame=frame,
        ))

    def _close_daily_lingmai_victory_layers(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> int:
        """Close every materialized #382 result layer with fresh scene proof."""

        successors = [382, 318, 303, 443, 305, 306, 85, 186, 285, 588]
        max_layers = max(1, int(payload.get("lingmai_kick_victory_max_layers") or 4))
        for layer_index in range(max_layers):
            yield from context.wait_click(382, "关闭")
            # Multiple materialized victory sheets reuse the same #382 scene
            # identity.  A successful click may therefore reveal a fresh
            # #382 rather than leave the scene.  Waiting for identity loss
            # here deadlocks before the bounded layer loop can do its job.
            yield from context.wait_action_settle(
                float(payload.get("lingmai_kick_victory_settle_seconds") or 1.0)
            )
            landed = yield from context.wait_scene(
                successors,
                wait=float(payload.get("lingmai_kick_victory_layer_timeout") or 60.0),
                label=f"{task_label}：关闭第 {layer_index + 1} 层胜利结果后等待 fresh 后继",
            )
            scene_id = int(landed.id if isinstance(landed, View) else landed)
            if scene_id != 382:
                return scene_id
            self._log(
                "detail",
                f"{task_label}：关闭第 {layer_index + 1} 层 #382 后出现下一层同型胜利结果，继续逐层关闭",
            )
        raise RuntimeError(f"{task_label}：连续关闭 {max_layers} 层 #382 后仍有胜利结果层")

    def _advance_daily_lingmai_kick_dialogue(
        self,
        context: BehaviorTreeContext,
        *,
        terminal_scene_ids: tuple[int, ...],
        timeout: float,
        label: str,
        max_clicks: int = 16,
    ) -> int:
        """Advance alternating #318/#303 Lingmai dialogue to a real terminal scene."""

        terminals = tuple(int(scene_id) for scene_id in terminal_scene_ids)
        for _index in range(max(1, int(max_clicks))):
            scene = yield from context.wait_scene(
                [318,
                303,
                *terminals],
                wait=timeout,
                label=label,
            )
            scene_id = int(scene.id if isinstance(scene, View) else scene)
            if scene_id == 588 and 588 in terminals:
                settled = yield from context.wait_scene(
                    [318, 303, *[sid for sid in terminals if sid != 588]],
                    wait=min(10.0, timeout),
                    required=False,
                    label=f"{label}：#588 可能是对白间背景，等待真实后继或稳定房间",
                )
                if settled is None:
                    return 588
                scene_id = int(settled)
            if scene_id in terminals:
                return scene_id
            if scene_id == 318:
                context.click_shape_center(318, "确认")
            elif scene_id == 303:
                context.click_shape_center(303, "对话")
            else:
                raise RuntimeError(f"{label}：出现未声明对白场景 #{scene_id}")
            yield from context.wait_action_settle(1.0)
        raise RuntimeError(f"{label}：连续推进 {max_clicks} 次仍未到达 {terminals}")

    def _click_daily_lingmai_go_button(
        self,
        context: BehaviorTreeContext,
        frame: str | None,
        *,
        task_label: str,
    ) -> None:
        frame = frame if isinstance(frame, str) and frame else context.cur_frame(update=True)
        view = context.view(287)
        action = view.get_shape("前往灵脉") or view.get_shape("确认")
        if action is None:
            raise RuntimeError(f"{task_label}：#287 缺少「前往灵脉/确认」动作 shape")
        self._log("action", f"{task_label}：点击 #287「{action.title}」")
        context.click_shape_center(view, action)

    def _continue_daily_lingmai_from_final_occupy(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: BehaviorTreeContext,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image288 = images.get(288)
        if not isinstance(image288, dict):
            raise RuntimeError(f"{task_label}：缺少 #288「占领」过渡后场景标注，无法确认灵脉占领")
        if self._find_shape(image288, "占领") is None:
            raise RuntimeError(f"{task_label}：缺少 #288「占领」shape 标注，无法点击过渡后的占领按钮")
        terminal_scene_ids = [443, 318, 306, 305, 285, 286, 287, 186]
        try:
            waited_scene = yield from context.wait_click_then_scene(
                288,
                "占领",
                terminal_scene_ids,
                settle_seconds=float(payload.get("lingmai_final_occupy_settle_seconds") or 2.0),
                timeout=float(payload.get("lingmai_final_occupy_timeout") or 20.0),
                max_clicks=int(payload.get("lingmai_final_occupy_max_clicks") or 2),
            )
        except TimeoutError:
            _wait_scene_match = yield from context.wait_scene([288, *terminal_scene_ids], wait=5.0, required=False)
            (scene_final, score_final, frame_final) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text_final = context.ocr_text(frame_final)
            raise RuntimeError(
                f"{task_label}：点击 #288「占领」后未进入合法后继；"
                f"当前 {'#' + str(scene_final) if scene_final is not None else 'unknown'} "
                f"{score_final:.0f}%，OCR={text_final[:160]}"
            )
        waited_scene_id = int(
            waited_scene.id if isinstance(waited_scene, View) else waited_scene
        )
        _wait_scene_match = yield from context.wait_scene(terminal_scene_ids, wait=5.0, required=False)
        (scene_final, score_final, frame_final) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_final is None and waited_scene_id in terminal_scene_ids:
            scene_final = waited_scene_id
            score_final = 100.0
            frame_final = context.cur_frame(update=True)
        text_final = context.ocr_text(frame_final)
        if scene_final == 318:
            return (yield from self._confirm_daily_lingmai_reward(context, payload, task_label=task_label))
        if scene_final == 305:
            return (yield from self._confirm_daily_lingmai_gather(context, payload, task_label=task_label))
        if scene_final == 443:
            return (yield from self._confirm_daily_lingmai_switch_popup(
                context,
                payload,
                task_label=task_label,
                scene_id=int(scene_final),
                frame=frame_final,
            ))
        if scene_final in {306, 186}:
            return (yield from self._finish_daily_lingmai_to_world(context, payload, task_label=task_label, scene_id=scene_final, frame=frame_final))
        raise RuntimeError(
            f"{task_label}：点击 #288「占领」后出现未实现的业务后继；"
            f"当前 {'#' + str(scene_final) if scene_final is not None else 'unknown'} {score_final:.0f}%，OCR={text_final[:160]}"
        )
