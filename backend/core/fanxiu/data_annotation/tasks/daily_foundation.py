"""公共日常入口、清理及业务能力组合。

各玩法由下方导入的业务模块拥有；这里保留日常入口和清理，
组合能力以保持现有 Task 入口。新增玩法应建立独立模块。
"""
from __future__ import annotations

from .daily_observations import observed_scene_id

from backend.core.fanxiu.data_annotation.effective_time import job_now

import re
import threading
import time
from datetime import datetime, time as time_cls, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import View

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.job_times import (
    clip_daily_retry_to_window,
    next_business_time,
)


from .lundao_execution import LundaoTaskMixin
from .lingmai_execution import LingmaiTaskMixin
from .dongtian_execution import DongtianTaskMixin
from .daily_boss import DailyBossTaskMixin
from .xianyuan_execution import DailyXianyuanTaskMixin
from .xianyuan_duel_execution import XianyuanDuelTaskMixin
from .lingzu import DailyLingzuTaskMixin
from .youli import DailyYouliTaskMixin
from .world_return import WorldReturnMixin


from .daily_jianling import DailyJianlingTaskMixin
from .daily_lingta import DailyLingtaTaskMixin
from .weekly_dungeon import WeeklyDungeonTaskMixin


from .mojie_raid import MojieRaidTaskMixin
from .baiye import BaiyeTaskMixin


# 保留历史导出；新调用应直接依赖 activity_rewards 或 weekly_activity_observations。
from .activity_rewards import ActivityRewardsTaskMixin, read_weekly_activity_runtime_snapshot, DAILY_ACTIVITY_OCR_MAX_ATTEMPTS
from .weekly_activity_observations import (
    weekly_activity_pending_badge_present,
    weekly_activity_reward_layout_from_ocr,
    detect_weekly_activity_reward_states,
    WEEKLY_ACTIVITY_LABEL_BAND,
    WEEKLY_ACTIVITY_REWARD_Y_RATIO,
)
from backend.core.fanxiu.catalog.weekly_activity import WEEKLY_ACTIVITY_REWARD_MILESTONES


from .daily_audit import DailyAuditTaskMixin


class DailyFoundationTaskMixin(
    DailyAuditTaskMixin,
    ActivityRewardsTaskMixin,
    MojieRaidTaskMixin,
    BaiyeTaskMixin,
    DailyJianlingTaskMixin,
    DailyLingtaTaskMixin,
    WeeklyDungeonTaskMixin,
    DailyBossTaskMixin,
    LundaoTaskMixin,
    LingmaiTaskMixin,
    DongtianTaskMixin,
    DailyXianyuanTaskMixin,
    XianyuanDuelTaskMixin,
    DailyLingzuTaskMixin,
    DailyYouliTaskMixin,
    WorldReturnMixin,
):
    @staticmethod
    def _daily_window_admission(
        *,
        now: datetime,
        trigger: time_cls,
        cutoff: time_cls,
        label: str,
        window_text: str,
    ) -> dict[str, Any] | None:
        if trigger <= now.time() < cutoff:
            return None
        next_date = now.date() if now.time() < trigger else now.date() + timedelta(days=1)
        next_time = datetime.combine(next_date, trigger).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "result": "success",
            "message": f"{label}：当前不在 {window_text} 窗口，未执行游戏操作",
            "next_time": next_time,
            "current_scene": None,
        }

    def _payload_int(self, payload: dict[str, Any], *keys: str, default: int) -> int:
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None or value == "":
                continue
            return int(value)
        return int(default)


    def _safe_daily_done_cleanup(
        self,
        cleanup_factory: Callable[[], Any],
        *,
        label: str,
        action: str = "收尾回世界",
        repeat_risk: str = "重复执行",
    ):
        with self._lock:
            business_status = {
                "status": self._status.get("status") or "running",
                "message": self._status.get("message") or f"{label}：业务已完成",
                "phase": self._status.get("phase") or "daily_done",
                "current_scene": self._status.get("current_scene"),
            }
        try:
            yield from cleanup_factory()
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            # Navigation helpers publish their own transient ``error`` status
            # before raising.  This wrapper deliberately turns a cleanup-only
            # failure into business success, so it must also restore a
            # non-error runner status; otherwise Scheduler ignores the return
            # value and schedules an unsafe retry of the completed action.
            with self._lock:
                self._set_status_locked(
                    str(business_status["status"]),
                    str(business_status["message"]),
                    phase=str(business_status["phase"]),
                    current_scene=business_status["current_scene"],
                )
                self._log_locked(
                    "warning",
                    f"{label}：业务已完成，但{action}失败，按已完成处理避免{repeat_risk}：{exc}",
                )
        return "success"


    def _leave_world_side_scene_if_present(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        frame: str,
        text: str,
        *,
        label: str,
        require_world_like: bool = True,
    ):
        del frame, text, require_world_like
        context = self._behavior_tree_context(ctx, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([477, 66, 326, 325, 266, 265, 264, 233, 225, 85, 186, 69, 34], label=f'{label}：识别世界侧残留场景', wait=5.0, required=False)
        (scene_id, score, _matched_frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id in {34, 69, None}:
            return False

        # 周三/周六 16:00-20:00，世界页入口可能先打开秘境封魔杀
        # 封面。这个分支只属于“本次进入日常”的局部事务：正式资产
        # 明确声明 #477[返回] -> #66，#66[返回] -> #34。先沿这条
        # 有界返回链归一化到世界，再由调用方重新点击 #34[日常]；不把
        # #477 注册为通用 go_scene 补偿入口。
        if scene_id == 477:
            self._log("action", f"{label}：识别 #477 {score:.0f}%，沿正式「返回」回到日程/世界")
            yield from context.wait_click(477, "返回", timeout=8.0)
            scene_id = yield from context.wait_scene(
                [66,
                34],
                wait=12.0,
                label=f"{label}：等待离开 #477",
            )
            if scene_id == 34:
                ctx["_go_scene_known_scene_id"] = 34
                return True

        if scene_id == 66:
            self._log("action", f"{label}：从日程 #66 点击正式「返回」回世界")
            yield from context.wait_click(66, "返回", timeout=8.0)
            yield from context.wait_scene(
                [34],
                wait=12.0,
                label=f"{label}：等待日程返回世界 #34",
            )
            ctx["_go_scene_known_scene_id"] = 34
            return True

        if scene_id in {233, 225}:
            self._log("action", f"{label}：识别 #{scene_id} {score:.0f}%，点击正式标注「空白」关闭提示")
            yield from context.wait_click(scene_id, "空白", timeout=8.0)
            yield from context.wait_action_settle(1.0)
            return True

        if scene_id in {326, 325, 266, 265, 264}:
            self._log("action", f"{label}：识别 #{scene_id} {score:.0f}%，点击正式标注「返回」")
            yield from context.wait_click(scene_id, "返回")
            yield from context.wait_action_settle(1.0)
            if scene_id == 326:
                yield from context.wait_scene([325, 69, 34], wait=18.0, label=f"{label}：等待离开 #326")
            elif scene_id == 325:
                yield from context.wait_scene([69, 34], wait=18.0, label=f"{label}：等待离开 #325")
            elif scene_id == 266:
                yield from context.wait_scene([265, 264, 34], wait=18.0, label=f"{label}：等待离开 #266")
            elif scene_id == 265:
                yield from context.wait_scene([264, 34], wait=18.0, label=f"{label}：等待离开 #265")
            else:
                yield from context.wait_scene([34], wait=18.0, label=f"{label}：等待离开 #264")
                ctx["_go_scene_known_scene_id"] = 34
            return True

        if scene_id in {85, 186}:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：从正式场景 #{scene_id} 离开",
                    phase="world_side_scene_leave",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"{label}：点击 #{scene_id}「离开」")
            yield from context.wait_click(scene_id, "离开")
            yield from context.wait_action_settle(1.0)

        with self._lock:
            self._set_status_locked(
                "running",
                f"{label}：等待返回世界 #34",
                phase="world_side_scene_leave_wait_world",
                current_scene=scene_id,
            )
        yield from context.wait_scene([34], wait=12.0, label=f"{label}：等待返回世界 #34")
        ctx["_go_scene_known_scene_id"] = 34
        return True

    def _enter_daily_from_world_like(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
        frame: str,
        scene_id: int | None,
        text: str,
        *,
        label: str,
    ):
        if scene_id == 69:
            return 69
        if scene_id == 661:
            # #661 是带地标「进入」按钮的世界 HUD；进入地标不会回到 #34。
            # 复用正式标注的日常入口，避免场景图沿历史误学边进入天道外墟。
            yield from context.wait_click(661, "日常")
            match = yield from context.wait_scene([69], wait=15.0)
            if match.scene_id != 69:
                raise RuntimeError(f"{label}：世界变体进入日常后落到 #{match.scene_id}")
            return 69
        if scene_id is None:
            scene_id, _score, frame = context.recognize_scene_in_frame(frame_data_url=frame)
            text = context.ocr_text(frame)
            if scene_id == 69:
                return 69
        if self._daily_lundao_text_is_seated(text):
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前停在论道闻道中，先离开道场回世界",
                    phase="daily_recover_from_lundao_seated",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"{label}：OCR 命中论道闻道中，点击「离开」并确认")
            yield from self._leave_daily_lundao_seated_for_daily_entry(context, scene_id)
            _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 69:
                return 69
            world_like = scene_id == 34
        else:
            world_like = scene_id == 34
        green_bottle_like = scene_id == 20 or (not world_like and self._daily_lingta_text_is_green_bottle_like(text))
        if green_bottle_like:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前停在绿瓶 #20，先返回世界",
                    phase="daily_recover_from_green_bottle",
                    current_scene=20 if scene_id == 20 else None,
                )
                self._log_locked("action", f"{label}：命中 #20/绿瓶主界面，点击 #20「回到世界」")
            yield from self._leave_green_bottle_to_world(ctx, stop_event, label=label)
            _wait_scene_match = yield from context.wait_scene([69, 34, 20], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 69:
                return 69
            world_like = scene_id == 34
        yihuo_like = (
            hasattr(self, "_daily_yihuo_text_is_xinghai_list")
            and (
                self._daily_yihuo_text_is_xinghai_list(text)  # type: ignore[attr-defined]
                or self._daily_yihuo_text_is_claimed(text)  # type: ignore[attr-defined]
            )
        )
        if scene_id is None and not world_like and yihuo_like:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前停在异火页，先走异火返回链回世界",
                    phase="daily_recover_from_yihuo",
                    current_scene=None,
                )
                self._log_locked("action", f"{label}：OCR 命中异火页，先用已标注返回链回世界")
            yield from self._daily_yihuo_return_best_effort(context)  # type: ignore[attr-defined]
            _wait_scene_match = yield from context.wait_scene([69, 34, 20], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 69:
                return 69
            world_like = scene_id == 34
        youli_home_like = (
            scene_id is None
            and not world_like
            and hasattr(self, "_daily_youli_text_is_home")
            and self._daily_youli_text_is_home(text)  # type: ignore[attr-defined]
        )
        if youli_home_like:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前停在修仙传游历页，先返回世界",
                    phase="daily_recover_from_youli_home",
                    current_scene=None,
                )
                self._log_locked("action", f"{label}：OCR 命中修仙传游历页，点击 #228「返回」回世界")
            try:
                yield from context.wait_click(228, "返回")
                yield from context.wait_action_settle(2.0)
                _wait_scene_match = yield from context.wait_scene([69, 34, 20], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 69:
                    return 69
                world_like = scene_id == 34
            except Exception as exc:
                self._log("warning", f"{label}：修仙传游历页返回世界失败，继续尝试场景图恢复：{exc}")
        if scene_id in {477, 66}:
            recovered = yield from self._leave_world_side_scene_if_present(
                ctx,
                stop_event,
                frame,
                text,
                label=label,
                require_world_like=False,
            )
            if recovered:
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 69:
                    return 69
                world_like = scene_id == 34
        if scene_id is None and not world_like:
            recovered, scene_id, frame, text = yield from self._recover_daily_youli_result_before_daily_entry(
                ctx,
                context,
                stop_event,
                scene_id,
                frame,
                text,
                label=label,
            )
            if recovered and scene_id == 69:
                return 69
        world_like = scene_id == 34
        if scene_id is None and not world_like:
            if (yield from self._leave_world_side_scene_if_present(
                ctx,
                stop_event,
                frame,
                text,
                label=label,
                require_world_like=False,
            )):
                start = time.monotonic()
                while True:
                    self._raise_if_stopped(stop_event)
                    yield BehaviorTreeStatus.RUNNING
                    _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                    (scene_id, _score, frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                    text = context.ocr_text(frame)
                    if scene_id == 69:
                        return 69
                    if scene_id == 34:
                        world_like = True
                        break
                    if time.monotonic() - start >= 10.0:
                        break
        if scene_id is None and not world_like:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前不是日常/世界，尝试用场景图恢复到 #69",
                    phase="daily_recover_to_daily",
                    current_scene=None,
                )
                self._log_locked("action", f"{label}：当前场景未识别为日常/世界，尝试 goto #69 恢复起点")
            try:
                yield from context.go_scene(69)
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_after, score_after, frame_after) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text_after = context.ocr_text(frame_after)
                if scene_after == 69:
                    return 69
                if scene_after == 34:
                    scene_id, frame, text, world_like = scene_after, frame_after, text_after, True
                else:
                    raise RuntimeError(
                        f"恢复后仍未确认日常/世界，当前 "
                        f"{'#' + str(scene_after) if scene_after is not None else 'unknown'} {score_after:.0f}% "
                        f"OCR={text_after[:120]}"
                    )
            except Exception as exc:
                # A full-screen side page can need several animated frames to
                # leave.  Its direct route to #69 may exhaust before #424
                # [返回] settles, while the simpler route to the stable world
                # anchor is still recoverable.  Normalize to #34 once, then
                # let the shared daily-entry path below enter #69 normally.
                self._log(
                    "warning",
                    f"{label}：直接恢复到 #69 失败，先经稳定锚点 #34 恢复后重试：{exc}",
                )
                try:
                    yield from context.go_scene(34)
                    _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                    (scene_after, score_after, frame_after) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                    text_after = context.ocr_text(frame_after)
                    if scene_after == 69:
                        return 69
                    if scene_after != 34:
                        raise RuntimeError(
                            f"回到世界后仍未确认 #34，当前 "
                            f"{'#' + str(scene_after) if scene_after is not None else 'unknown'} "
                            f"{score_after:.0f}% OCR={text_after[:120]}"
                        )
                    scene_id, frame, text, world_like = scene_after, frame_after, text_after, True
                except Exception as anchor_exc:
                    raise RuntimeError(
                        f"{label}：当前不在可识别的世界或日常页，且无法经 #34 恢复到 #69：{anchor_exc}"
                    ) from anchor_exc
        if world_like and (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=label)):
            start = time.monotonic()
            while True:
                self._raise_if_stopped(stop_event)
                yield BehaviorTreeStatus.RUNNING
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 69:
                    return 69
                if scene_id == 34:
                    scene_id = 34
                    break
                if time.monotonic() - start >= 10.0:
                    break
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        if not isinstance(image34, dict):
            raise RuntimeError(f"{label}：缺少 #34「世界」标注，无法进入日常")
        with self._lock:
            self._set_status_locked("running", f"{label}：进入日常 #69", phase="daily_go_daily", current_scene=scene_id)
            self._log_locked("action", f"{label}：按场景图跳转到 #69")
        try:
            last_error: RuntimeError | None = None
            for attempt in range(2):
                yield from context.go_scene(69)
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_after, score_after, frame_after) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text_after = context.ocr_text(frame_after)
                if scene_after == 69:
                    return 69
                last_error = RuntimeError(
                    f"{label}：跳转后未确认进入日常列表，当前 "
                    f"{'#' + str(scene_after) if scene_after is not None else 'unknown'} {score_after:.0f}% "
                    f"OCR={text_after[:120]}"
                )
                if attempt > 0:
                    break
                if scene_after == 34:
                    self._log("detail", f"{label}：进入日常被世界页浮层/活动入口打断后已回到 #34，重试进入 #69")
                    yield from context.wait_action_settle(1.0)
                    continue
                if not (yield from self._leave_world_side_scene_if_present(
                    ctx,
                    stop_event,
                    frame_after,
                    text_after,
                    label=label,
                    require_world_like=False,
                )):
                    break
                yield from context.wait_action_settle(2.0)
            raise last_error or RuntimeError(f"{label}：跳转后未确认进入日常列表")
        except Exception as exc:
            raise RuntimeError(f"{label}：无法通过场景图跳转到 #69；需要补当前场景到日常页的路由/返回/离开标注：{exc}") from exc

    def _recover_daily_youli_result_before_daily_entry(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
        scene_id: int | None,
        frame: str,
        text: str,
        *,
        label: str,
    ):
        if not hasattr(self, "_daily_youli_text_is_quick_result") or not hasattr(self, "_confirm_daily_youli_quick_result"):
            return False, scene_id, frame, text
        is_quick_result = scene_id == 237 or self._daily_youli_text_is_quick_result(text)  # type: ignore[attr-defined]
        if not is_quick_result:
            _wait_scene_match = yield from context.wait_scene([237, 69, 34], wait=5.0, required=False)
            (probe_scene, _probe_score, probe_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            probe_text = context.ocr_text(probe_frame)
            if probe_scene == 69:
                return False, probe_scene, probe_frame, probe_text
            if probe_scene == 34:
                return False, probe_scene, probe_frame, probe_text
            is_quick_result = probe_scene == 237 or self._daily_youli_text_is_quick_result(probe_text)  # type: ignore[attr-defined]
            if not is_quick_result:
                return False, scene_id, frame, text
            scene_id, frame, text = probe_scene, probe_frame, probe_text
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image237 = images.get(237)
        if not isinstance(image237, dict):
            raise RuntimeError(f"{label}：当前停在 #237 游历结果页，但缺少 #237「确定」标注，无法安全恢复日常入口")
        with self._lock:
            self._set_status_locked(
                "running",
                f"{label}：当前停在游历结果页，先确认并回到日常入口",
                phase="daily_recover_from_youli_result",
                current_scene=237,
            )
            self._log_locked("action", f"{label}：命中 #237「游历结果」，点击「确定」并返回世界/日常")
        yield from self._confirm_daily_youli_quick_result(ctx, stop_event, {}, image237, task_label=label)  # type: ignore[attr-defined]
        if hasattr(self, "_return_daily_youli_to_world"):
            image228 = images.get(228)
            image236 = images.get(236)
            if isinstance(image228, dict) and isinstance(image236, dict):
                yield from self._return_daily_youli_to_world(ctx, stop_event, image228, image236, task_label=label)  # type: ignore[attr-defined]
        _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
        (scene_after, _score_after, frame_after) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text_after = context.ocr_text(frame_after)
        return True, scene_after, frame_after, text_after


    def _leave_green_bottle_to_world(self, ctx: dict[str, Any], stop_event: threading.Event, *, label: str):
        image20 = ctx.get("images", {}).get(20)
        if not isinstance(image20, dict):
            raise RuntimeError("缺少 #20「绿瓶」标注，无法回到世界")
        back_shape = self._find_shape(image20, "回到世界")
        if back_shape is None:
            raise RuntimeError("缺少 #20「回到世界」标注，无法回到世界")
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", f"{label}：退出绿瓶", phase="exit_green_bottle", current_scene=20)
            self._log_locked("action", f"{label}：点击 #20「回到世界」")
        yield from context.wait_click(20, "回到世界")
        yield BehaviorTreeStatus.RUNNING

        start = time.monotonic()
        clicked_outer_world = False
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([34, 20], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"{label}：已从绿瓶回到世界")
                return "success"
            if not clicked_outer_world and self._daily_lingta_text_is_green_bottle_like(text):
                width, height = self._frame_size(image20)
                x = width * 0.105
                y = height * 0.91
                with self._lock:
                    self._set_status_locked("running", f"{label}：绿瓶外层仍未回世界，点击左下角「世界」", phase="exit_green_bottle_outer", current_scene=scene_id)
                    self._log_locked("action", f"{label}：点击绿瓶左下角「世界」")
                context.click_frame_point(20, x, y)
                clicked_outer_world = True
                continue
            if time.monotonic() - start >= 18.0:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"{label}：退出绿瓶后未回到世界，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：等待绿瓶返回世界，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="wait_green_bottle_world",
                    current_scene=scene_id,
                )


    def _ensure_clean_world_after_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([275, 237, 204, 69, 58, 20, 34], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
            self._daily_assistant_close_one_key_result(ctx, context, frame, label=label)
            yield from context.wait_action_settle(1.0)
            _wait_scene_match = yield from context.wait_scene([237, 204, 69, 58, 20, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
        if scene_id == 237:
            yield from self._daily_assistant_close_youli_result(context, {})
            yield from context.wait_action_settle(1.0)
            _wait_scene_match = yield from context.wait_scene([204, 69, 58, 20, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
        if scene_id == 204 or self._daily_assistant_text_is_list(text):
            with self._lock:
                self._set_status_locked("running", f"{label}：小助手总览仍在前台，先返回日常页", phase="cleanup_exit_daily_assistant", current_scene=204)
                self._log_locked("action", f"{label}：点击 #204「返回」")
            yield from context.wait_click(204, "返回")
            landed = yield from context.wait_scene(
                [69,
                34,
                275,
                237],
                wait=15.0,
                label=f"{label}：等待退出小助手总览",
            )
            scene_id = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
            score = 100.0
            frame = context.cur_frame(update=True) if hasattr(context, "cur_frame") else None
            text = context.ocr_text(frame)
        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", f"{label}：从日常页返回世界", phase="cleanup_exit_daily_page", current_scene=69)
                self._log_locked("action", f"{label}：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            landed = yield from context.wait_scene([34], wait=25.0, label=f"{label}：等待日常页返回世界")
            scene_id = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
            score = 100.0
            frame = context.cur_frame(update=True) if hasattr(context, "cur_frame") else None
            text = context.ocr_text(frame)
        if scene_id == 58:
            with self._lock:
                self._set_status_locked("running", f"{label}：隐藏浮动窗后确认世界", phase="cleanup_hide_floating_window", current_scene=58)
                self._log_locked("action", f"{label}：检测到 #58 浮动窗，先执行隐藏浮动窗")
            self._execute_hide_floating_window(ctx, stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([20, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
        if scene_id == 20:
            yield from self._leave_green_bottle_to_world(ctx, stop_event, label=label)
            _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
        if scene_id == 34:
            _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
                self._log_locked("success", f"{label}：已确认干净世界 #34")
            return 34
        raise RuntimeError(
            f"{label}：收尾后未确认干净世界，当前 "
            f"{'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}% OCR={text[:120]}"
        )


    def _daily_assistant_text_is_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if re.search(r"同游结果|同游消耗|总共获得宝物|查看下一个|点击空白处关闭|本次获得的道具|神物园自动收取|自动兑换", compact):
            return False
        if "一键执行" in compact and (
            "小助手" in compact or re.search(r"游历.*灵兽|万灵.*试炼|仙府.*宗门", compact)
        ):
            return True
        task_hit = re.search(
            r"道义.*秘库|神物园助手|神物园|宗门助手|仙府资源|弟子授业|同游传道|弟子求学|弟子教学|前往设置|自动派遣",
            compact,
        )
        return bool(task_hit and ("小助手" in compact or "助手" in compact or "道义" in compact))


    def _daily_entry_matches(
        self,
        lines: list[dict[str, Any]],
        image69: dict[str, Any],
        *,
        title_pattern: str,
        exclude_pattern: str | None = None,
    ) -> list[tuple[float, float, str]]:
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            return []
        box = self._box(list_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if exclude_pattern and re.search(exclude_pattern, text):
                continue
            if not re.search(title_pattern, text):
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

    def _ensure_daily_list_frame(
        self,
        ctx: dict[str, Any],
        frame: str,
        lines: list[dict[str, Any]],
        *,
        task_label: str,
    ) -> None:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, frame_data_url=frame)
        scene_id, score, _frame = context.recognize_scene_in_frame([69, 34], frame_data_url=frame)
        text = "\n".join(str(line.get("text") or "") for line in lines if isinstance(line, dict))
        if scene_id == 69:
            return
        scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
        raise RuntimeError(f"{task_label}：未确认当前在 #69 日常列表，禁止滚动查找；当前 {scene_text} {score:.0f}% OCR={text[:120]}")

    def _open_daily_entry_from_daily(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
        title_pattern: str,
        exclude_pattern: str | None = None,
        progress_can_mark_done: bool = True,
        initial_checks: int = 1,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_scrolls = self._payload_int(payload, "max_scrolls", default=30)
        return (yield from context.open_daily_entry(
            label=task_label,
            title_pattern=title_pattern,
            exclude_pattern=exclude_pattern,
            progress_can_mark_done=progress_can_mark_done,
            max_scrolls=max_scrolls,
            initial_checks=initial_checks,
        ))

    def _record_daily_entry_done(self, payload: dict[str, Any], *, task_id: str, task_type: str, label: str, message: str) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or task_id)
        next_time = (
            next_business_time(("05:00",))
        )
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"{label}：{message}，下次 {next_time}")
        return next_time

    def _record_daily_entry_not_found_retry(
        self,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        label: str,
        entry_label: str = "入口",
        seconds: int = 1800,
        daily_start_time: str | time_cls | None = None,
        daily_end_time: str | time_cls | None = None,
    ) -> str:
        now = job_now()
        retry_at = now + timedelta(seconds=max(60, int(seconds)))
        if daily_start_time is not None and daily_end_time is not None:
            retry_at = clip_daily_retry_to_window(
                retry_at,
                now=now,
                start=daily_start_time,
                end=daily_end_time,
            )
        next_time = retry_at.strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
        )
        self._log("skip", f"{label}：#69 日常列表暂时未找到「{entry_label}」，{next_time} 重试")
        return next_time

    def _wait_unsupported_daily_entry_after_click(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> tuple[int | None, float, str]:
        timeout = float(payload.get("post_click_timeout") or 8.0)
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text or last_text
            if scene_id in {69, 34}:
                return scene_id, float(score), last_text
            if time.monotonic() - start >= timeout:
                if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                    _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                    (scene_id, score, frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                    text = context.ocr_text(frame)
                    return scene_id, float(score), text
                return last_scene_id, float(last_score), last_text
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待入口点击结果，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_entry_wait_after_click",
                    current_scene=scene_id,
                )

    def _execute_daily_entry_probe_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
        *,
        task_id: str,
        task_type: str,
        task_label: str,
        title_pattern: str,
        missing_assets_message: str,
        exclude_pattern: str | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError(f"缺少{task_label}资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                _wait_scene_match = yield from context.wait_scene([69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
            if scene_id != 69:
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
            title_pattern=title_pattern,
            exclude_pattern=exclude_pattern,
            progress_can_mark_done=False,
        )
        if daily_status == "done":
            self._record_daily_entry_done(
                payload,
                task_id=task_id,
                task_type=task_type,
                label=task_label,
                message="日常列表显示已完成",
            )
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_xianyuan_to_world(ctx, stop_event),
                label=task_label,
            )
            return "success"
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id=task_id,
                task_type=task_type,
                label=task_label,
            )
            return "skipped"

        scene_id, score, after_text = yield from self._wait_unsupported_daily_entry_after_click(ctx, stop_event, payload, task_label=task_label)
        raise RuntimeError(
            f"{task_label}：已点击 #69 入口，但后续业务闭环尚未迁移；"
            f"当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%，OCR={after_text[:120]}。"
            f"{missing_assets_message}"
        )


    def _goto_world_after_shared_victory_overlay(self, context: Any, *, label: str):
        """Finish a proven exit even if world auto-battle hides the HUD."""

        try:
            return (yield from context.go_scene(34))
        except RuntimeError as exc:
            if "场景跳转缺少可靠标注" not in str(exc):
                raise
        self._log(
            "diagnostic",
            f"{label}：退出业务页后处于世界战斗过场，等待世界[聊天] HUD 恢复",
        )
        yield from context.wait_shape(
            34,
            "聊天",
            timeout=60.0,
            label=f"{label}：等待世界战斗过场结束",
        )
        return "success"

    def _leave_shared_scene_186_to_world(
        self, context: Any, *, label: str,
        include_lundao_scene: bool = False, source_scene_id: int = 186,
    ) -> str:
        """Leave a shared scene; Layer 0 owns every intermediate popup."""

        scene_id: int | None = source_scene_id
        score = 100.0
        terminal_ids = {34, 69}
        overlay_ids = {386, 375, 295}
        source_ids = {186, 85}
        if include_lundao_scene:
            # 论道只声明可离开的业务页；#54 确认由弹窗守护处理。
            source_ids.add(53)
        candidate_ids = sorted(terminal_ids | overlay_ids | source_ids)
        for attempt in range(1, 5):
            if scene_id in terminal_ids:
                return "success"
            if scene_id in overlay_ids:
                self._log(
                    "action",
                    f"{label}：离开道场后落到已知浮层 #{scene_id}，继续按场景图返回 #34",
                )
                yield from self._goto_world_after_shared_victory_overlay(
                    context,
                    label=label,
                )
                return "success"
            if scene_id not in source_ids:
                raise RuntimeError(
                    f"{label}：离开内部场景落到未声明状态 "
                    f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
                )

            exit_shape = "离开"
            self._log(
                "action",
                f"{label}：收尾识别 #{scene_id}，点击正式标注「{exit_shape}」（第 {attempt}/4 次）",
            )
            try:
                waited_scene = yield from context.wait_click_then_scene(
                    scene_id,
                    exit_shape,
                    sorted(terminal_ids | overlay_ids),
                    settle_seconds=1.5,
                    timeout=15.0,
                    max_clicks=1,
                )
                scene_id = int(getattr(waited_scene, "id", waited_scene))
                score = float(getattr(waited_scene, "score", 100.0) or 0.0)
            except TimeoutError:
                waited_scene = yield from context.wait_scene(
                    candidate_ids,
                    wait=15.0,
                    label=f"{label}：离开后重新识别",
                )
                scene_id = observed_scene_id(waited_scene)
                score = float(getattr(waited_scene, "score", 100.0) or 0.0)

        if scene_id in terminal_ids:
            return "success"
        raise RuntimeError(
            f"{label}：离开内部场景有界重试耗尽，最后 "
            f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
        )


    def _complete_daily_clear_task(
        self,
        payload: dict[str, Any],
        *,
        task_id: str,
        label: str,
    ) -> str:
        """Persist tomorrow's run when a nightly clear job succeeds."""

        now = job_now()
        next_date = (
            now.date()
            if now.time() < time_cls(21, 0)
            else now.date() + timedelta(days=1)
        )
        next_time = datetime.combine(next_date, time_cls(21, 0)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
        )
        self._log("success", f"{label}：今日清理完成，下次 {next_time}")
        return "success"
