"""公共日常入口、清理与尚待拆分的日常玩法。

首领、论道、灵脉和洞天分别由对应执行模块拥有；这里组合能力以保持
现有 Task 入口。新增玩法应建立独立模块，不再扩充本兼容组合类。
"""
from __future__ import annotations

from .daily_observations import observed_scene_id

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_tasks
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, write_world_facts

import base64
import io
import math
import re
import threading
import time
from datetime import datetime, time as time_cls, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import Shape, View

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens, locate_text_box, query_spatial_ocr
from backend.core.fanxiu.data_annotation.duel_strategy import XIANYUAN_CAREER_LABELS, best_xianyuan_partner_order, parse_slot_value_title, plan_swaps
from backend.core.fanxiu.data_annotation.arena_schedule import (
    XIANYUAN_DUEL_TASK_ID,
    next_xianyuan_duel_cycle_trigger_at,
    next_xianyuan_duel_trigger_at,
    xianyuan_duel_scheduler_in_window,
    xianyuan_duel_window_text,
)
from backend.core.fanxiu.data_annotation.job_times import (
    clip_daily_retry_to_window,
    next_business_time,
)
from backend.core.fanxiu.data_annotation.storage import data_annotation_entry_image_dir
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION


XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG = "_xianyuan_duel_entry_not_found_date"
DAILY_ACTIVITY_OCR_MAX_ATTEMPTS = 5


from backend.core.fanxiu.data_annotation.tasks.xianyuan_duel import (
    choose_xianyuan_duel_target,
    map_xianyuan_duel_targets_to_slots,
)
from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.data_annotation.state import parse_data_annotation_task_time


WEEKLY_ACTIVITY_REWARD_Y_RATIO = 270.0 / 1600.0
WEEKLY_ACTIVITY_LABEL_BAND = (0.205, 0.245)
# ActiveTasks.ActiveProgress type=2, config ids 13..19.  This is the
# authoritative weekly rail, not a list inferred from whichever slice #402
# happens to show after its automatic horizontal scroll.
WEEKLY_ACTIVITY_REWARD_MILESTONES = (400, 600, 800, 1200, 1600, 2000, 2400)


def read_weekly_activity_runtime_snapshot() -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.weekly_activity import (
        read_weekly_activity_snapshot,
    )

    return read_weekly_activity_snapshot()


def weekly_activity_pending_badge_present(
    tokens: list[dict[str, Any]],
    *,
    frame_width: int,
    frame_height: int,
) -> bool:
    """Return whether the selected 周常 tab still shows its local ``领`` badge."""

    badge_box = {
        "x": frame_width * 0.82,
        "y": frame_height * 0.84,
        "w": frame_width * 0.16,
        "h": frame_height * 0.09,
    }
    spatial = query_spatial_ocr(tokens or [], badge_box)
    return any(
        _sanitize_ocr_text(fragment.get("text")) == "领"
        for fragment in spatial.get("fragments") or []
        if isinstance(fragment, dict)
    )


def weekly_activity_reward_layout_from_ocr(
    tokens: list[dict[str, Any]],
    *,
    frame_width: int,
    frame_height: int,
) -> dict[int, dict[str, Any]]:
    """Map the currently visible #402 milestone labels to their reward icons.

    The reward rail scrolls horizontally as activity grows, so a screen x
    coordinate never identifies a fixed milestone.  The numeric labels under
    the rail are the frame-local source of truth; their x centres project
    vertically to the icons above them.
    """

    if frame_width <= 0 or frame_height <= 0:
        raise RuntimeError("周常_活跃度：#402 当前帧尺寸无效")
    min_y = frame_height * WEEKLY_ACTIVITY_LABEL_BAND[0]
    max_y = frame_height * WEEKLY_ACTIVITY_LABEL_BAND[1]
    min_x = frame_width * 0.25
    max_x = frame_width * 0.96
    layout: dict[int, dict[str, Any]] = {}
    for token in group_ocr_tokens(tokens or []):
        if not isinstance(token, dict):
            continue
        text = str(token.get("text") or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.fullmatch(r"\s*([1-9]\d{2,3})\s*", text)
        if match is None:
            continue
        x = float(token.get("x") or 0)
        y = float(token.get("y") or 0)
        w = float(token.get("w") or 0)
        h = float(token.get("h") or 0)
        center_x = x + w / 2
        center_y = y + h / 2
        milestone = int(match.group(1))
        if (
            w <= 0
            or h <= 0
            or not min_x <= center_x <= max_x
            or not min_y <= center_y <= max_y
            or milestone % 100 != 0
        ):
            continue
        if milestone not in WEEKLY_ACTIVITY_REWARD_MILESTONES:
            # 横向滚动时单帧 OCR 可能把 1200 漏读为 200。让调用方取
            # 新帧复核；持续未知仍报错，不能把未知档位当作领取事实。
            raise RuntimeError(f"周常_活跃度：奖励轨道档位标签识别到未知档 {milestone}")
        if milestone in layout:
            raise RuntimeError(f"周常_活跃度：档位标签 {milestone} OCR 重复，拒绝投影")
        layout[milestone] = {
            "point": (center_x, frame_height * WEEKLY_ACTIVITY_REWARD_Y_RATIO),
            "label_box": (x, y, w, h),
        }

    ordered = sorted(layout.items(), key=lambda item: item[1]["point"][0])
    if not ordered:
        raise RuntimeError("周常_活跃度：未识别到奖励轨道档位标签，拒绝使用固定坐标")
    milestones = [milestone for milestone, _row in ordered]
    if milestones != sorted(milestones) or len(milestones) < 2:
        raise RuntimeError(f"周常_活跃度：奖励轨道档位标签不完整或顺序异常：{milestones}")
    return dict(ordered)


def detect_weekly_activity_reward_states(
    frame_data_url: str,
    reward_layout: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Classify the frame-local #402 milestones as claimed/claimable/unknown."""

    import cv2
    import numpy as np

    payload = str(frame_data_url or "")
    if "," not in payload:
        raise RuntimeError("周常_活跃度：#402 当前帧不是有效 data URL")
    try:
        image = cv2.imdecode(
            np.frombuffer(base64.b64decode(payload.split(",", 1)[1]), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
    except Exception as exc:
        raise RuntimeError(f"周常_活跃度：#402 当前帧解码失败：{exc}") from exc
    if image is None or image.size == 0:
        raise RuntimeError("周常_活跃度：#402 当前帧解码为空")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    radius = max(18, int(round(width * 48.0 / 900.0)))
    states: dict[int, dict[str, Any]] = {}
    for milestone, layout_row in reward_layout.items():
        point = layout_row.get("point")
        if not isinstance(point, (tuple, list)) or len(point) != 2:
            raise RuntimeError(f"周常_活跃度：{milestone} 档缺少 OCR 投影点")
        x = int(round(float(point[0])))
        y = int(round(float(point[1])))
        x1, x2 = max(0, x - radius), min(width, x + radius)
        y1, y2 = max(0, y - radius), min(height, y + radius)
        crop = hsv[y1:y2, x1:x2]
        if crop.size == 0:
            raise RuntimeError(f"周常_活跃度：{milestone} 档奖励框超出当前帧")
        green = cv2.inRange(crop, (35, 50, 50), (100, 255, 255))
        bright_gold = cv2.inRange(crop, (10, 20, 180), (40, 255, 255))
        green_ratio = float(np.count_nonzero(green)) / float(green.size)
        bright_gold_ratio = float(np.count_nonzero(bright_gold)) / float(bright_gold.size)
        if green_ratio >= 0.05:
            state = "claimed"
        elif bright_gold_ratio >= 0.25:
            state = "claimable"
        else:
            state = "unknown"
        states[milestone] = {
            "state": state,
            "point": (float(x), float(y)),
            "green_ratio": green_ratio,
            "bright_gold_ratio": bright_gold_ratio,
        }
    return states


_DAILY_AUDIT_TASK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("daily_boss", "daily-boss", r"击败首领"),
    ("daily_dungeon", "legacy-daily-dungeon", r"通关每日副本|每日副本|副本探险"),
    ("daily_shuangxiu", "legacy-daily-shuangxiu", r"完成双人修炼|双人修炼|双修"),
    ("daily_jianling", "legacy-daily-jianling", r"淬剑试炼|剑试"),
    ("daily_lingta", "legacy-daily-lingta", r"混沌灵塔|灵塔"),
    ("daily_youli", "legacy-daily-youli", r"完成修仙传游历|修仙传游历"),
    ("daily_xianyuan", "legacy-daily-xianyuan", r"挑战仙缘"),
    ("daily_lingzu", "legacy-daily-lingzu", r"灵祖|圣雷龙"),
    ("daily_yaowang", "legacy-daily-yaowang", r"妖王来袭|妖王"),
    ("daily_yaozu", "legacy-daily-yaozu", r"妖族袭城|妖族"),
    ("daily_dongtian", "legacy-daily-dongtian", r"九曜\s*玄墨|玄墨|採炁|采炁"),
    ("daily_xianshi", "legacy-daily-xianshi", r"仙市"),
    # 以下为日常页里长期存在、但此前未映射的日常条目。只映射每日重置且
    # 每日运行的作业；周常条目（圣祖、韩立等）不在此映射，避免跨周误判。
    ("daily_lingquan", "legacy-daily-lingquan", r"宗门灵泉|灵泉"),
    ("daily_zhenxie", "daily-zhenxie", r"宗门镇邪|镇邪"),
    ("daily_lundao", "daily-lundao-seat", r"参与论道|论道"),
    ("daily_mojie_raid", "legacy-daily-mojie-raid", r"奇袭魔界"),
    ("daily_xuanhuang", "daily-xuanhuang", r"玄荒古域|玄荒"),
    ("moyu_challenge", "moyu-challenge", r"魔狱封阵|完成一次魔狱"),
)

_DAILY_AUDIT_COMPLETION_MIN_TOTAL: dict[str, int] = {
    "daily_dungeon": 6,
}


def read_xianyuan_duel_runtime_snapshot(
    *,
    include_formations: bool = True,
    self_power_hint: int | float | None = None,
) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.arena import read_xianyuan_duel_snapshot

    return read_xianyuan_duel_snapshot(
        include_formations=include_formations,
        self_power_hint=self_power_hint,
    )


def xianyuan_duel_dynamic_signature(facts: dict[str, Any]) -> tuple[Any, ...]:
    """Return only the round-changing facts; stable self power is excluded."""

    targets = tuple(
        (
            item.get("target_id"),
            str(item.get("name") or ""),
            item.get("score"),
            item.get("team_power"),
        )
        for item in facts.get("targets") or []
        if isinstance(item, dict)
    )
    return (
        facts.get("remaining_challenges"),
        facts.get("remaining_refreshes"),
        facts.get("rank"),
        targets,
    )


def xianyuan_duel_runtime_facts_advanced(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    return xianyuan_duel_dynamic_signature(current) != xianyuan_duel_dynamic_signature(previous)


from .lundao_execution import LundaoTaskMixin
from .lingmai_execution import LingmaiTaskMixin
from .dongtian_execution import DongtianTaskMixin
from .daily_boss import DailyBossTaskMixin
from .xianyuan_execution import DailyXianyuanTaskMixin


class DailyFoundationTaskMixin(
    DailyBossTaskMixin, LundaoTaskMixin, LingmaiTaskMixin, DongtianTaskMixin, DailyXianyuanTaskMixin,
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

    def _next_daily_activity_time_text(self) -> str:
        now = job_now()
        next_at = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
        return next_at.strftime("%Y-%m-%d %H:%M:%S")

    def daily_activity_flow(self, context: Any):
        yield from context.go_scene(69)

        attempt = 0
        while attempt < DAILY_ACTIVITY_OCR_MAX_ATTEMPTS:
            attempt += 1
            _wait_scene_match = yield from context.wait_scene([69], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 69:
                raise RuntimeError(f"日常_活跃度：读取总活跃度时已不在 #69：#{scene_id or 'unknown'} {score:.0f}%")

            values, text = context.ocr_numbers_in_shapes(
                69,
                ["总活跃度"],
                padding=0,
                frame_data_url=frame,
            )
            if values:
                total_activity = int(values[0])
                self._log("detail", f"日常_活跃度：第 {attempt} 次读取总活跃度={total_activity}，OCR={text!r}")
                break

            if attempt < DAILY_ACTIVITY_OCR_MAX_ATTEMPTS:
                self._log("detail", f"日常_活跃度：第 {attempt} 次未读到总活跃度数值，OCR={text!r}，继续识别")
                yield from context.wait_action_settle(0.8)
            else:
                self._log("detail", f"日常_活跃度：第 {attempt} 次仍未读到总活跃度数值，OCR={text!r}，停止本次识别")
        else:
            raise RuntimeError(
                "日常_活跃度：连续 "
                f"{DAILY_ACTIVITY_OCR_MAX_ATTEMPTS} 次未读到总活跃度数值，停止本次作业等待技术重试"
            )

        if total_activity < 500:
            yield from context.go_scene(34)
            context.set_next_time(
                (job_now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            )
            return {
                "result": "success",
                "message": f"日常_活跃度：总活跃度 {total_activity} < 500，已回到世界，1 小时后重试",
                "current_scene": 34,
            }

        context.click_shape_center(69, "奖励")
        yield from context.wait_action_settle(1.5)
        yield from context.go_scene(34)
        context.set_next_time(self._next_daily_activity_time_text())
        return {
            "result": "success",
            "message": f"日常_活跃度：总活跃度 {total_activity}，已点击奖励并回到世界",
            "current_scene": 34,
        }

    def _execute_daily_activity_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        normalized_payload = dict(payload or {})
        normalized_payload.setdefault("fallback_seconds", 3600)
        result = yield from self._execute_daily_task(
            ctx,
            stop_event,
            normalized_payload,
            task_type="daily_activity",
            label="日常_活跃度",
            flow=self.daily_activity_flow,
        )
        return result

    def _next_weekly_activity_time_text(
        self,
        *,
        completed: bool,
        now: datetime | None = None,
    ) -> str:
        current = now or job_now()
        if not completed and current.weekday() in {3, 4}:
            next_at = current + timedelta(days=1)
        else:
            days_until_thursday = (3 - current.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
            next_at = current + timedelta(days=days_until_thursday)
        return next_at.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def weekly_activity_flow(self, context: Any):
        def read_reward_layout(frame: str):
            """Read the #402 reward rail over bounded fresh frames.

            页签切换、横向滚动和点击档位之后，奖励轨道都有自己的入场/惯性
            动画：某一帧可能只渲染出部分档位标签，直接判定"标签不完整"会把
            瞬态动画当成资产缺陷（2026-09-18 真实失败：只读到 [1200]）。
            这里只对"标签不足"这类瞬态结果重取帧，越界、重复档位等结构性
            异常仍然立即失败，保持原有的失败关闭语义。
            """

            transient_markers = (
                "未识别到奖励轨道档位标签",
                "档位标签不完整或顺序异常",
                "档位标签识别到未知档",
            )
            attempts = 4
            last_error: RuntimeError | None = None
            current_frame = frame
            for attempt in range(attempts):
                try:
                    layout = weekly_activity_reward_layout_from_ocr(
                        context.full_frame_ocr_tokens(current_frame),
                        frame_width=900,
                        frame_height=1600,
                    )
                    # 档位状态是在同一帧上按投影点取色的，必须把实际用于
                    # 投影的那一帧一起交回调用方，避免用旧帧判色。
                    return layout, current_frame
                except RuntimeError as exc:
                    if not any(marker in str(exc) for marker in transient_markers):
                        raise
                    last_error = exc
                    if attempt + 1 >= attempts:
                        break
                    self._log(
                        "detail",
                        f"周常_活跃度：奖励轨道标签尚未渲染完整，重新取帧复核"
                        f"（第 {attempt + 1} 次）",
                    )
                    yield from context.wait_action_settle(0.8)
                    current_frame = context.cur_frame(update=True)
            assert last_error is not None
            raise last_error

        yield from context.go_scene(69)
        context.click_shape_center(69, "周常")
        yield from context.wait_action_settle(float(context.payload.get("weekly_tab_settle_seconds") or 1.5))

        attempt = 0
        while True:
            attempt += 1
            _wait_scene_match = yield from context.wait_scene([402], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 402:
                self._log(
                    "detail",
                    f"周常_活跃度：第 {attempt} 次尚未识别到 #402：#{scene_id or 'unknown'} {score:.0f}%，继续识别",
                )
                yield from context.wait_action_settle(0.8)
                continue

            values, text = context.ocr_numbers_in_shapes(
                402,
                ["活跃度"],
                padding=0,
                frame_data_url=frame,
            )
            if values:
                total_activity = int(values[0])
                self._log(
                    "detail",
                    f"周常_活跃度：第 {attempt} 次读取活跃度={total_activity}，OCR={text!r}",
                )
                break

            self._log(
                "detail",
                f"周常_活跃度：第 {attempt} 次未读到活跃度数值，OCR={text!r}，继续识别",
            )
            yield from context.wait_action_settle(0.8)

        threshold = max(1, int(context.payload.get("weekly_activity_threshold") or 2400))
        now = job_now()
        if total_activity < threshold:
            final_attempt = now.weekday() == 5
            next_time = self._next_weekly_activity_time_text(completed=final_attempt, now=now)
            if final_attempt:
                message = (
                    f"周常_活跃度：周六最终检查 {total_activity} < {threshold}，"
                    f"本周结束，下次 {next_time}"
                )
            else:
                message = f"周常_活跃度：活跃度 {total_activity} < {threshold}，下次 {next_time} 复查"
            yield from context.go_scene(34)
            context.set_next_time(next_time)
            return {
                "result": "success",
                "message": message,
                "current_scene": 34,
            }

        reward_layout, frame = yield from read_reward_layout(frame)
        reward_states = detect_weekly_activity_reward_states(frame, reward_layout)
        runtime_snapshot = read_weekly_activity_runtime_snapshot()
        if runtime_snapshot.get("complete") is not True:
            raise RuntimeError(
                f"周常_活跃度：Runtime 权威领取集合不完整："
                f"{runtime_snapshot.get('reason') or runtime_snapshot.get('status') or 'unknown'}"
            )
        if tuple(runtime_snapshot.get("thresholds") or ()) != WEEKLY_ACTIVITY_REWARD_MILESTONES:
            raise RuntimeError(f"周常_活跃度：Runtime 档位全集漂移：{runtime_snapshot.get('thresholds')}")
        if int(runtime_snapshot.get("active_num") or -1) != total_activity:
            raise RuntimeError(
                f"周常_活跃度：GUI/Runtime 活跃度不一致："
                f"GUI={total_activity} Runtime={runtime_snapshot.get('active_num')}"
            )

        def validate_gui_cross_check(snapshot: Mapping[str, Any], states: Mapping[int, Mapping[str, Any]]) -> None:
            claimed = {int(value) for value in snapshot.get("claimed_thresholds") or []}
            claimable = {int(value) for value in snapshot.get("claimable_thresholds") or []}
            disagreements: list[str] = []
            for milestone, row in states.items():
                if milestone > total_activity:
                    continue
                expected = "claimed" if milestone in claimed else "claimable" if milestone in claimable else "unknown"
                if row.get("state") != expected:
                    disagreements.append(f"{milestone}:{row.get('state')}!=Runtime-{expected}")
            if disagreements:
                raise RuntimeError(f"周常_活跃度：GUI/Runtime 档位状态不一致：{disagreements}")

        validate_gui_cross_check(runtime_snapshot, reward_states)

        def confirm_reward_scene(*, action_label: str):
            """Allow the reward page a few frames to settle without repeating the action."""
            latest_frame = ""
            latest_scene_id: int | None = None
            latest_score = 0.0
            for attempt in range(3):
                _wait_scene_match = yield from context.wait_scene([402], label=f'周常_活跃度：{action_label}后复核奖励页', wait=5.0, required=False)
                (latest_scene_id, latest_score, latest_frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if latest_scene_id == 402:
                    return latest_frame
                if attempt < 2:
                    self._log(
                        "detail",
                        f"周常_活跃度：{action_label}后第 {attempt + 1} 帧暂未识别 #402，继续复核",
                    )
                    yield from context.wait_action_settle(0.4)
            raise RuntimeError(
                f"周常_活跃度：{action_label}后未留在 #402："
                f"#{latest_scene_id or 'unknown'} {latest_score:.0f}%"
            )

        claimed_now: list[int] = []
        scroll_attempts = 0
        final_frame = frame
        final_milestone = max(WEEKLY_ACTIVITY_REWARD_MILESTONES)
        while True:
            claimable_thresholds = [
                int(value) for value in runtime_snapshot.get("claimable_thresholds") or []
            ]
            if not claimable_thresholds and final_milestone in reward_states:
                break
            validate_gui_cross_check(runtime_snapshot, reward_states)
            visible_claimable = [
                milestone for milestone in claimable_thresholds if milestone in reward_states
            ][:1]
            # 每次动作后重算候选；刷新 Runtime 不会使旧列表自动失效。
            # 全部已领时仍滚到最高档，完成页面终态核验。
            if not visible_claimable:
                if scroll_attempts >= 4:
                    raise RuntimeError(
                        f"周常_活跃度：横向滚动后仍未找到可领档 {claimable_thresholds}"
                    )
                before_visible = set(reward_states)
                context.drag_frame_point(402, 760, 350, 260, 350, duration_ms=1000)
                yield from context.wait_action_settle(0.8)
                final_frame = yield from confirm_reward_scene(action_label="横向滚动")
                reward_layout, final_frame = yield from read_reward_layout(final_frame)
                reward_states = detect_weekly_activity_reward_states(final_frame, reward_layout)
                scroll_attempts += 1
                if set(reward_states) == before_visible:
                    raise RuntimeError(
                        f"周常_活跃度：横向滚动后可见档位未推进：{sorted(before_visible)}"
                    )
                continue

            for milestone in visible_claimable:
                before = reward_states[milestone]
                click_x, click_y = before["point"]
                context.click_frame_point(402, click_x, click_y)
                yield from context.wait_action_settle(float(context.payload.get("reward_settle_seconds") or 1.5))
                after_frame = yield from confirm_reward_scene(
                    action_label=f"点击 {milestone} 档",
                )
                after_layout, after_frame = yield from read_reward_layout(after_frame)
                if milestone not in after_layout:
                    raise RuntimeError(f"周常_活跃度：点击 {milestone} 档后该档已离开可见轨道，无法复验")
                after_states = detect_weekly_activity_reward_states(after_frame, after_layout)
                if after_states[milestone]["state"] != "claimed":
                    raise RuntimeError(
                        f"周常_活跃度：点击 {milestone} 档后未复验为绿色勾："
                        f"{after_states[milestone]['state']}"
                    )
                runtime_snapshot = read_weekly_activity_runtime_snapshot()
                if runtime_snapshot.get("complete") is not True:
                    raise RuntimeError(f"周常_活跃度：点击 {milestone} 档后 Runtime 快照不完整")
                if milestone not in {int(value) for value in runtime_snapshot.get("claimed_thresholds") or []}:
                    raise RuntimeError(f"周常_活跃度：点击 {milestone} 档后 Runtime 未确认该档已领取")
                validate_gui_cross_check(runtime_snapshot, after_states)
                claimed_now.append(milestone)
                reward_states = after_states
                final_frame = after_frame
        remaining_claimable = [int(value) for value in runtime_snapshot.get("claimable_thresholds") or []]
        if remaining_claimable:
            raise RuntimeError(f"周常_活跃度：领取后仍有 Runtime 可领档：{remaining_claimable}")
        if reward_states.get(final_milestone, {}).get("state") != "claimed":
            raise RuntimeError(f"周常_活跃度：右边界 {final_milestone} 档未显示绿色勾")
        final_tokens = context.full_frame_ocr_tokens(final_frame)
        if weekly_activity_pending_badge_present(
            final_tokens,
            frame_width=900,
            frame_height=1600,
        ):
            raise RuntimeError("周常_活跃度：Runtime 无可领档但周常页签仍显示“领”，拒绝写入下周")
        yield from context.go_scene(34)
        next_time = self._next_weekly_activity_time_text(completed=True, now=now)
        context.set_next_time(next_time)
        reward_message = (
            f"本次领取 {claimed_now}"
            if claimed_now
            else "全部达标档位已领取，零点击幂等结束"
        )
        return {
            "result": "success",
            "message": (
                f"周常_活跃度：活跃度 {total_activity} >= {threshold}，"
                f"{reward_message}，下次 {next_time}"
            ),
            "current_scene": 34,
        }

    def _execute_weekly_activity_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        return (yield from self._execute_daily_task(
            ctx,
            stop_event,
            payload,
            task_type="weekly_activity",
            label="周常_活跃度",
            flow=self.weekly_activity_flow,
        ))


    def _next_daily_lingzu_reset_time_text(self) -> str:
        return self._next_daily_boss_reset_time_text()

    def _daily_lingzu_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(re.search(r"(?:1/1|已完成|完成一次灵祖挑战.*1/1)", normalized))

    def _daily_lingzu_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(re.search(r"(?:今日剩余次数|剩余奖励次数)[:：]?(?:0/1|O/1)", normalized, re.IGNORECASE))

    def _daily_lingzu_scene_from_frame(
        self,
        ctx: dict[str, Any],
        frame: str,
        scene_id: int | None = None,
        score: float = 0.0,
    ) -> tuple[int | None, float, str]:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, frame_data_url=frame)
        text = context.ocr_text(frame)
        if scene_id is None:
            scene_id, score, _frame = context.recognize_scene_in_frame([34, 69, 183, 184, 185, 186, 187, 188, 189], frame_data_url=frame)
        return scene_id, score, text

    def _record_daily_lingzu_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_lingzu_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingzu")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_灵祖：{message}，下次 {next_time}")
        return next_time

    def _safe_return_daily_lingzu_to_world_after_done(self, ctx: dict[str, Any], stop_event: threading.Event):
        try:
            yield from self._return_daily_lingzu_to_world(ctx, stop_event)
        except Exception as exc:
            if self._daily_lingzu_cleanup_error_requires_attention(exc):
                raise
            self._log("warning", f"日常_灵祖：业务已完成，但收尾回世界失败，按已完成处理避免重复挑战：{exc}")
        return "success"

    def _daily_lingzu_cleanup_error_requires_attention(self, exc: Exception) -> bool:
        message = str(exc)
        return "#186" in message or "奖励浮层" in message

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

    def _daily_lingzu_next_time_is_future(self, payload: dict[str, Any]) -> str | None:
        task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingzu").strip() or "legacy-daily-lingzu"
        task = next(
            (item for item in read_scheduler_tasks(now=job_now()) if str(item.get("id") or "") == task_id),
            None,
        )
        next_time = str(task.get("next_time") or "").strip() if isinstance(task, dict) else ""
        if not next_time:
            return None
        due_at = parse_data_annotation_task_time(next_time)
        if due_at is None or due_at <= time.time():
            return None
        return next_time

    def _execute_daily_lingzu_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_灵祖资产树路径，无法执行作业")
        next_time = self._daily_lingzu_next_time_is_future(payload)
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        scene_id, _score, current_text = self._daily_lingzu_scene_from_frame(ctx, frame, scene_id, _score)
        if next_time and scene_id not in {183, 184, 185, 186, 187, 188, 189}:
            with self._lock:
                self._set_status_locked(
                    "done",
                    f"日常_灵祖：已记录今日完成，下次 {next_time}",
                    phase="daily_lingzu_already_done",
                    current_scene=scene_id,
                )
                self._log_locked("success", self._status["message"])
            return "success"
        if scene_id == 186:
            yield from self._return_daily_lingzu_to_world(ctx, stop_event)
            self._record_daily_lingzu_done(payload, message="当前已在灵祖奖励完成态")
            return "success"
        if scene_id in {185, 187, 188, 189}:
            return (yield from self._run_daily_lingzu_challenge(ctx, context, stop_event, payload))
        if scene_id == 184:
            return (yield from self._run_daily_lingzu_challenge(ctx, context, stop_event, payload))
        if scene_id == 183:
            detail_status = yield from self._open_daily_lingzu_detail(ctx, context, stop_event, payload)
            if detail_status == "done":
                return "success"
            return (yield from self._run_daily_lingzu_challenge(ctx, context, stop_event, payload))
        if scene_id != 69:
            world_text = context.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                context,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_灵祖",
            )

        daily_status = yield from self._open_daily_lingzu_activity_from_daily(ctx, stop_event, payload)
        if daily_status == "done":
            self._record_daily_lingzu_done(payload, message="日常列表显示已完成")
            yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
            return "success"

        detail_status = yield from self._open_daily_lingzu_detail(ctx, context, stop_event, payload)
        if detail_status == "done":
            yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
            return "success"

        return (yield from self._run_daily_lingzu_challenge(ctx, context, stop_event, payload))

    def _return_daily_lingzu_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            with self._lock:
                self._log_locked("warning", "日常_灵祖：缺少资产树路径，无法收尾回世界 #34")
            return "skipped"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        scene_id, _score, _text = self._daily_lingzu_scene_from_frame(ctx, frame, scene_id, _score)
        if scene_id == 34:
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        with self._lock:
            self._set_status_locked("running", "日常_灵祖：收尾回到世界 #34", phase="daily_lingzu_return_world", current_scene=scene_id)
            self._log_locked("action", "日常_灵祖：完成后按灵祖返回链路回到 #34 世界")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image69 = images.get(69)
        image183 = images.get(183)
        image184 = images.get(184)
        image186 = images.get(186)
        image187 = images.get(187)
        image188 = images.get(188)
        if not all(isinstance(item, dict) for item in (image69, image183, image184, image187, image188)):
            raise RuntimeError("日常_灵祖：缺少 #69/#183/#184/#187/#188 返回世界标注")
        if scene_id == 186:
            if not isinstance(image186, dict):
                raise RuntimeError("日常_灵祖：缺少 #186「灵祖奖励浮层」标注，无法关闭奖励浮层")
            close_shape = (
                self._find_shape(image186, "关闭")
                or self._find_shape(image186, "空白")
                or self._find_shape(image186, "返回")
                or self._find_shape(image186, "退出")
                or self._find_shape(image186, "离开")
            )
            if close_shape is None:
                raise RuntimeError("日常_灵祖：#186 奖励浮层缺少「关闭/空白/返回/退出/离开」动作标注，无法确认已清理浮层")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：关闭奖励浮层", phase="daily_lingzu_close_reward", current_scene=186)
                self._log_locked("action", f"日常_灵祖：点击 #186「{close_shape.get('title') or '关闭'}」")
            yield from context.wait_click(186, str(close_shape.get("title") or "关闭"))
            start = time.monotonic()
            while True:
                self._raise_if_stopped(stop_event)
                yield from context.wait_action_settle(1.0)
                _wait_scene_match = yield from context.wait_scene([34, 183, 184, 187, 188], wait=5.0, required=False)
                (scene_id, _score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_id is not None:
                    break
                if time.monotonic() - start >= 12:
                    raise RuntimeError("日常_灵祖：点击 #186 关闭动作后奖励浮层仍未消失")

        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：从日常列表返回世界", phase="daily_lingzu_return_daily", current_scene=69)
                self._log_locked("action", "日常_灵祖：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            yield from context.wait_action_settle(2.0)
            frame = context.cur_frame(update=True)
            text = context.ocr_text(frame)
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_灵祖")):
                yield from context.wait_action_settle(2.0)
            yield from context.wait_scene([34], wait=18.0, label="日常_灵祖：等待世界 #34")

        if scene_id == 188:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：从圣雷龙妖祖返回战灵长老", phase="daily_lingzu_return_elder", current_scene=188)
                self._log_locked("action", "日常_灵祖：点击 #188「返回」")
            yield from context.wait_click(188, "返回")
            landing = yield from context.wait_scene([187], wait=18.0, label="日常_灵祖：等待战灵长老 #187")
            scene_id, _score = int(getattr(landing, "id", landing)), 100.0

        if scene_id == 187:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：关闭战灵长老对话", phase="daily_lingzu_close_elder", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「空白」")
            yield from context.wait_click(187, "空白")
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [183, 34],
                timeout=18.0,
                label="日常_灵祖：等待灵祖活动列表 #183 或世界 #34",
            )

        if scene_id == 184:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：关闭灵祖详情", phase="daily_lingzu_close_detail", current_scene=184)
                self._log_locked("action", "日常_灵祖：点击 #184「空白」")
            yield from context.wait_click(184, "空白")
            landing = yield from context.wait_scene([183], wait=18.0, label="日常_灵祖：等待灵祖活动列表 #183")
            scene_id, _score = int(getattr(landing, "id", landing)), 100.0

        if scene_id == 183:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：返回世界", phase="daily_lingzu_return_world_click", current_scene=183)
                self._log_locked("action", "日常_灵祖：点击 #183「返回」")
            yield from context.wait_click(183, "返回")
            yield from context.wait_scene([34], wait=18.0, label="日常_灵祖：等待世界 #34")

        _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id != 34:
            raise RuntimeError(f"日常_灵祖：回世界后仍识别为 #{scene_id or 'unknown'}")
        yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
        with self._lock:
            self._status.update({"current_scene": 34, "updated_at": time.time()})
        return "success"

    def _ensure_daily_lingzu_outer_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image85 = images.get(85)
        if not isinstance(image85, dict):
            with self._lock:
                self._log_locked("warning", "日常_灵祖：缺少 #85「某区域内部」标注，无法确认是否已离开宗门内部")
            return "skipped"
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        text = context.ocr_text(update=True)
        if not self._daily_lingzu_world_text_is_internal_area(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        with self._lock:
            self._set_status_locked("running", "日常_灵祖：当前仍在宗门内部，点击离开", phase="daily_lingzu_leave_internal_area", current_scene=85)
            self._log_locked("action", "日常_灵祖：点击 #85「离开」")
        yield from context.wait_click(85, "离开")

        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([34, 204, 69], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 34 and not self._daily_lingzu_world_text_is_internal_area(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_灵祖：已离开宗门内部并回到外层世界 #34 {score:.0f}%")
                return "success"
            if scene_id == 204:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_灵祖：离开后落到小助手清单，返回日常页",
                        phase="daily_lingzu_leave_assistant_return",
                        current_scene=204,
                    )
                    self._log_locked("action", "日常_灵祖：点击 #204「返回」")
                yield from context.wait_click(204, "返回")
                yield from context.wait_action_settle(2.0)
                continue
            if scene_id == 69:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_灵祖：从日常页退出到世界",
                        phase="daily_lingzu_leave_daily_exit",
                        current_scene=69,
                    )
                    self._log_locked("action", "日常_灵祖：点击 #69「退出」")
                yield from context.wait_click(69, "退出")
                yield from context.wait_action_settle(2.0)
                continue
            if time.monotonic() - start >= 20:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"日常_灵祖：离开宗门内部超时，最后 {scene_text} {last_score:.0f}%，文本：{last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵祖：等待离开宗门内部，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_lingzu_wait_outer_world",
                    current_scene=scene_id,
                )

    def _daily_lingzu_world_text_is_internal_area(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "离开" not in normalized:
            return False
        return any(fragment in normalized for fragment in ("社团管事", "贺圣朴", "创建队伍", "加入队伍", "组队"))

    def _next_daily_jianling_reset_time_text(self) -> str:
        return self._next_daily_boss_reset_time_text()

    def _record_daily_jianling_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_jianling_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-jianling")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_剑灵：{message}，下次 {next_time}")
        return next_time

    def _daily_jianling_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(re.search(r"(?:挑战或扫荡淬剑试炼|淬剑试炼).*(?:1/1|已完成)", normalized))

    def _daily_jianling_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(
            re.search(r"剩余次数[:：]?(?:0|O)(?:\\+)?", normalized, re.IGNORECASE)
            or "已通关" in normalized
            or "当前秘境已全通" in normalized
        )

    def _daily_jianling_text_is_result(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return (
            "扫荡奖励" in normalized
            or "点击屏幕继续" in normalized
            or "点击继续" in normalized
            or ("获得了" in normalized and ("仙侣神通" in normalized or "修为境界" in normalized))
        )

    def _daily_jianling_text_is_main(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "淬剑试炼" in normalized and ("通关进度" in normalized or "剩余次数" in normalized)

    def _execute_daily_jianling_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_剑灵资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([190, 192, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        current_text = context.ocr_text(frame)
        if scene_id == 192 or self._daily_jianling_text_is_result(current_text):
            yield from self._finish_daily_jianling_result(ctx, stop_event)
            scene_id = 190
        if self._daily_jianling_text_is_main(current_text):
            scene_id = 190
        if scene_id == 190:
            text = context.ocr_text(update=True)
            if self._daily_jianling_remaining_zero(text):
                self._record_daily_jianling_done(payload, message="淬剑试炼剩余次数为 0")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_jianling_to_world(ctx, stop_event),
                    label="日常_剑灵",
                    repeat_risk="重复扫荡",
                )
                return "success"
            yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
            return "success"

        if scene_id != 69:
            world_text = context.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                context,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_剑灵",
            )

        daily_status = yield from context.open_daily_entry(
            label="日常_剑灵",
            title_pattern=r"挑战或扫荡淬剑试炼|淬剑试炼|淬剑|剑试",
            max_scrolls=self._payload_int(payload, "max_scrolls", "jianling_max_scrolls", default=30),
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-jianling",
                task_type="daily_jianling",
                label="日常_剑灵",
                entry_label="淬剑试炼",
            )
            return "skipped"
        if daily_status == "done":
            self._record_daily_jianling_done(payload, message="日常列表显示已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_jianling_to_world(ctx, stop_event),
                label="日常_剑灵",
                repeat_risk="重复扫荡",
            )
            return "success"
        yield from context.wait_scene([190], label="日常_剑灵：等待淬剑试炼 #190")
        yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_jianling_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from context.open_daily_entry(
            label="日常_剑灵",
            title_pattern=r"挑战或扫荡淬剑试炼|淬剑试炼|淬剑|剑试",
            max_scrolls=self._payload_int(payload, "max_scrolls", "jianling_max_scrolls", default=30),
        )
        if status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-jianling",
                task_type="daily_jianling",
                label="日常_剑灵",
                entry_label="淬剑试炼",
            )
            return "skipped"
        if status != "open":
            return status
        yield from context.wait_scene([190], label="日常_剑灵：等待淬剑试炼 #190")
        return "open"

    def _run_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：点击扫荡", phase="daily_jianling_sweep", current_scene=190)
            self._log_locked("action", "日常_剑灵：点击 #190「扫荡」")
        yield from context.wait_click(190, "扫荡")
        # #191 is a Layer 0 popup.  The guard recognizes it as the declared
        # landing of「扫荡」and clicks「进行扫荡」before returning control.
        confirm_result = yield from self._confirm_daily_jianling_sweep(ctx, stop_event)
        if confirm_result == "result":
            yield from self._finish_daily_jianling_result(ctx, stop_event)
        self._record_daily_jianling_done(payload, message="淬剑试炼扫荡完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_jianling_to_world(ctx, stop_event),
            label="日常_剑灵",
            repeat_risk="重复扫荡",
        )

    def _confirm_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：等待 Layer 0 完成扫荡确认", phase="daily_jianling_confirm_sweep", current_scene=None)
            self._log_locked("detail", "日常_剑灵：#191 扫荡确认由 Layer 0 守护处理")
        start = time.monotonic()
        timeout = 18.0
        min_main_return_seconds = 8.0
        main_seen_count = 0
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([190, 192], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, float(score), text or last_text
            if scene_id == 192 or self._daily_jianling_text_is_result(text):
                return "result"
            if scene_id == 190 or self._daily_jianling_text_is_main(text):
                main_seen_count += 1
                if time.monotonic() - start >= min_main_return_seconds and main_seen_count >= 2:
                    return "main"
            else:
                main_seen_count = 0
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"日常_剑灵：等待扫荡结果超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_剑灵：等待扫荡结果，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_jianling_wait_result",
                    current_scene=scene_id,
                )

    def _finish_daily_jianling_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(8):
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([190, 192], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 190 or ("淬剑试炼" in text and "通关进度" in text):
                return "success"
            if scene_id == 192 or "点击" in text or self._daily_jianling_text_is_result(text):
                with self._lock:
                    self._set_status_locked("running", f"日常_剑灵：关闭扫荡结果 {index + 1}", phase="daily_jianling_continue_result", current_scene=192)
                    if scene_id == 192:
                        self._log_locked("action", "日常_剑灵：点击 #192「点击继续」")
                    else:
                        self._log_locked("action", "日常_剑灵：结果页未识别为 #192，按 OCR「点击屏幕继续」收口")
                if scene_id == 192:
                    yield from context.wait_click(192, "点击继续")
                else:
                    context.click_frame_point({"width": 900, "height": 1600}, 450, 1380)
                    yield from context.wait_action_settle()
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_剑灵：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_剑灵：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_jianling_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([190, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id is None and self._daily_jianling_text_is_main(text):
            scene_id = 190
        if scene_id == 190:
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：退出淬剑试炼", phase="daily_jianling_exit_main", current_scene=190)
                self._log_locked("action", "日常_剑灵：点击 #190「返回」")
            yield from context.wait_click(190, "返回")
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [69, 34],
                timeout=18.0,
                label="日常_剑灵：等待日常 #69 或世界 #34",
            )
        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：从日常列表返回世界", phase="daily_jianling_return_daily", current_scene=69)
                self._log_locked("action", "日常_剑灵：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            yield from context.wait_scene([34], wait=18.0, label="日常_剑灵：等待世界 #34")
        yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
        return "success"

    def _next_daily_lingta_reset_time_text(self) -> str:
        return self._next_daily_boss_reset_time_text()

    def _record_daily_lingta_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_lingta_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingta")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_灵塔：{message}，下次 {next_time}")
        return next_time

    def _daily_lingta_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(re.search(r"(?:挑战或扫荡混沌灵塔|混沌灵塔|灵塔).*(?:1/1|已完成)", normalized))

    def _daily_lingta_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(re.search(r"剩余次数[:：]?(?:0|O)(?:\\+)?", normalized, re.IGNORECASE))

    def _daily_lingta_text_is_main(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "混沌灵塔" in normalized and ("剩余次数" in normalized or "扫荡" in normalized)

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

    def _daily_lingta_text_is_green_bottle_like(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        markers = ("绿瓶", "炼丹", "丹炉", "丹药", "灵液", "世界")
        return sum(1 for marker in markers if marker in normalized) >= 2

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

    def _leave_daily_lingta_green_bottle(self, ctx: dict[str, Any], stop_event: threading.Event):
        return (yield from self._leave_green_bottle_to_world(ctx, stop_event, label="日常_灵塔"))

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

    def _execute_daily_lingta_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_灵塔资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([196, 194, 193, 69, 34, 20], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 20:
            yield from self._leave_daily_lingta_green_bottle(ctx, stop_event)
            _wait_scene_match = yield from context.wait_scene([196, 194, 193, 69, 34, 20], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        text = context.ocr_text(frame)
        if scene_id is None and self._daily_lingta_text_is_main(text):
            scene_id = 194
        if scene_id == 196:
            result_status = yield from self._finish_daily_lingta_result(ctx, stop_event)
            if result_status == "done":
                self._record_daily_lingta_done(payload, message="灵塔扫荡结果显示剩余次数为 0")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_lingta_to_world(ctx, stop_event),
                    label="日常_灵塔",
                    repeat_risk="重复扫荡",
                )
                return "success"
            scene_id = 194
        if scene_id == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
            scene_id = 194
        if scene_id == 194:
            text = context.ocr_text(update=True)
            if self._daily_lingta_remaining_zero(text):
                self._record_daily_lingta_done(payload, message="混沌灵塔剩余次数为 0")
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_lingta_to_world(ctx, stop_event),
                    label="日常_灵塔",
                    repeat_risk="重复扫荡",
                )
                return "success"
            yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
            return "success"

        if scene_id != 69:
            world_text = context.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                context,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_灵塔",
            )

        daily_status = yield from context.open_daily_entry(
            label="日常_灵塔",
            title_pattern=r"挑战或扫荡混沌灵塔|混沌灵塔|灵塔",
            max_scrolls=self._payload_int(payload, "max_scrolls", "lingta_max_scrolls", default=10),
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lingta",
                task_type="daily_lingta",
                label="日常_灵塔",
                entry_label="混沌灵塔",
            )
            return "skipped"
        if daily_status == "done":
            self._record_daily_lingta_done(payload, message="日常列表显示已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_lingta_to_world(ctx, stop_event),
                label="日常_灵塔",
                repeat_risk="重复扫荡",
            )
            return "success"
        view = yield from context.wait_scene([193, 194], label="日常_灵塔：等待区域入口 #193 或混沌灵塔 #194")
        if getattr(view, "id", view) == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
        yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_lingta_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from context.open_daily_entry(
            label="日常_灵塔",
            title_pattern=r"挑战或扫荡混沌灵塔|混沌灵塔|灵塔",
            max_scrolls=self._payload_int(payload, "max_scrolls", "lingta_max_scrolls", default=10),
        )
        if status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lingta",
                task_type="daily_lingta",
                label="日常_灵塔",
                entry_label="混沌灵塔",
            )
            return "skipped"
        if status != "open":
            return status
        scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
            ctx,
            stop_event,
            [193, 194],
            timeout=24.0,
            label="日常_灵塔：等待区域入口 #193 或混沌灵塔 #194",
        )
        if scene_id == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
        return "open"

    def _open_daily_lingta_main_from_entry(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(3):
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([194, 193], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id == 194:
                return "success"
            if self._daily_jianling_text_is_main(text):
                with self._lock:
                    self._log_locked("warning", "日常_灵塔：#193「进入」实际进入淬剑试炼，先返回后停止，等待修正灵塔入口标注")
                yield from self._return_daily_jianling_to_world(ctx, stop_event)
                raise RuntimeError("日常_灵塔：#193「进入」实际进入淬剑试炼，不是混沌灵塔；需要修正灵塔入口动作标注")
            if scene_id == 193:
                with self._lock:
                    self._set_status_locked("running", "日常_灵塔：点击区域入口「进入」", phase="daily_lingta_enter_area", current_scene=193)
                    self._log_locked("action", "日常_灵塔：点击 #193「进入」")
                yield from context.wait_click(193, "进入")
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([194], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_text = text or last_text
            if scene_id == 194:
                with self._lock:
                    self._status.update({"current_scene": 194, "updated_at": time.time()})
                    self._log_locked("success", f"日常_灵塔：等待混沌灵塔 #194：已到达 #194 {score:.0f}%")
                return "success"
            if self._daily_jianling_text_is_main(text):
                with self._lock:
                    self._log_locked("warning", "日常_灵塔：#193「进入」等待阶段落到淬剑试炼，先返回后停止，等待修正灵塔入口标注")
                yield from self._return_daily_jianling_to_world(ctx, stop_event)
                raise RuntimeError("日常_灵塔：#193「进入」实际进入淬剑试炼，不是混沌灵塔；需要修正灵塔入口动作标注")
            if time.monotonic() - start >= 18.0:
                raise RuntimeError(f"日常_灵塔：等待混沌灵塔 #194 超时，OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵塔：等待混沌灵塔 #194，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_lingta_wait_main",
                    current_scene=scene_id,
                )
        return "success"

    def _run_daily_lingta_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：点击扫荡", phase="daily_lingta_sweep", current_scene=194)
            self._log_locked("action", "日常_灵塔：点击 #194「扫荡」")
        yield from context.wait_click(194, "扫荡")
        # #195 is consumed by Layer 0 as the declared result of「扫荡」.
        yield from self._confirm_daily_lingta_sweep(ctx, stop_event)
        result_status = yield from self._finish_daily_lingta_result(ctx, stop_event)
        self._record_daily_lingta_done(payload, message="混沌灵塔扫荡完成")
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_lingta_to_world(ctx, stop_event),
            label="日常_灵塔",
            repeat_risk="重复扫荡",
        )

    def _confirm_daily_lingta_sweep(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：等待 Layer 0 完成扫荡确认", phase="daily_lingta_confirm_sweep", current_scene=None)
            self._log_locked("detail", "日常_灵塔：#195 扫荡确认由 Layer 0 守护处理")
        yield from context.wait_scene([196], label="日常_灵塔：等待扫荡结果 #196")

    def _finish_daily_lingta_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(8):
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([194, 196], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if self._daily_lingta_remaining_zero(text):
                return "done"
            if scene_id == 194 or ("混沌灵塔" in text and "剩余次数" in text):
                return "success"
            if scene_id == 196 or "点击" in text or "扫荡奖励" in text:
                with self._lock:
                    self._set_status_locked("running", f"日常_灵塔：关闭扫荡结果 {index + 1}", phase="daily_lingta_continue_result", current_scene=196)
                    self._log_locked("action", "日常_灵塔：点击 #196「点击继续」")
                yield from context.wait_click(196, "点击继续")
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_灵塔：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_灵塔：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_lingta_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([194, 69, 20, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id is None and self._daily_lingta_text_is_main(text):
            scene_id = 194
        if scene_id == 194:
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：退出混沌灵塔", phase="daily_lingta_exit_main", current_scene=194)
                self._log_locked("action", "日常_灵塔：点击 #194「返回」")
            yield from context.wait_click(194, "返回")
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [69, 20, 34],
                timeout=18.0,
                label="日常_灵塔：等待日常 #69、绿瓶 #20 或世界 #34",
            )
        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：从日常列表返回世界", phase="daily_lingta_return_daily", current_scene=69)
                self._log_locked("action", "日常_灵塔：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [20, 34],
                timeout=18.0,
                label="日常_灵塔：等待绿瓶 #20 或世界 #34",
            )
        if scene_id == 20:
            yield from self._leave_daily_lingta_green_bottle(ctx, stop_event)
        yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
        return "success"


    def daily_xianyuan_duel_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        task_id = str(payload.get("__scheduler_task_id") or "")
        if task_id != XIANYUAN_DUEL_TASK_ID:
            return None
        now = job_now()
        if xianyuan_duel_scheduler_in_window(now):
            return None
        window_text = xianyuan_duel_window_text(now)
        next_time = next_xianyuan_duel_trigger_at(now).strftime("%Y-%m-%d %H:%M:%S")
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": (
                f"仙缘斗法：当前不在 {window_text} 窗口，"
                "错过的场次作废，未执行游戏操作"
            ),
            "next_time": next_time,
            "current_scene": None,
            "scheduler_incident": {
                "kind": "window_expired",
                "cycle_kind": "daily",
                "window": window_text,
                "reason": "该日窗口已结束，禁止跨日补跑",
            },
        })

    def _handle_daily_xianyuan_duel_entry_not_found(
        self,
        payload: dict[str, Any],
        *,
        scheduler_task_id: str,
    ) -> str:
        """Require two same-day complete #69 traversals before closing the cycle."""

        now = job_now()
        today = now.date().isoformat()
        previous_not_found_date = str(
            self._get_scheduler_task_payload_flag(
                scheduler_task_id,
                XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            )
            or ""
        )
        if previous_not_found_date == today:
            next_time = next_xianyuan_duel_cycle_trigger_at(now).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            # Preserve the conservative two-scan evidence if the scheduling
            # write fails.  A later retry can then close the same cycle instead
            # of silently falling back to a new "first" miss.
            self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
            self._clear_scheduler_task_payload_flag(
                scheduler_task_id,
                XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            )
            self._log(
                "success",
                "仙缘斗法：同一日连续两轮归一并扫描完整日常列表仍无入口，"
                f"按本周期无可执行入口收口，下次 {next_time}",
            )
            return "skipped"

        if not self._set_scheduler_task_payload_flag(
            scheduler_task_id,
            XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            today,
        ):
            raise RuntimeError("仙缘斗法：首次未找到入口，但未能持久化复查标记")
        self._record_daily_entry_not_found_retry(
            payload,
            task_id=scheduler_task_id,
            task_type="daily_xianyuan_duel",
            label="仙缘斗法",
            entry_label="斗法",
            seconds=int(payload.get("retry_seconds") or 60),
        )
        return "skipped"

    def _execute_daily_xianyuan_duel_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        scheduler_task_id = str(payload.get("__scheduler_task_id") or XIANYUAN_DUEL_TASK_ID)
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙缘斗法资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([308, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id not in {308, 69}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label="仙缘斗法")
        if scene_id not in {308, 69}:
            raise RuntimeError("仙缘斗法：未能进入 #69 日常列表")
        if scene_id == 69:
            status = yield from context.open_daily_entry(
                label="仙缘斗法",
                title_pattern=r"斗\s*法",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
                return self._handle_daily_xianyuan_duel_entry_not_found(
                    payload,
                    scheduler_task_id=scheduler_task_id,
                )
            self._clear_scheduler_task_payload_flag(
                scheduler_task_id,
                XIANYUAN_DUEL_ENTRY_NOT_FOUND_DATE_FLAG,
            )
        if not bool(payload.get("skip_purchase")):
            yield from self._prepare_daily_xianyuan_duel_purchases(context, payload)
        max_runs = int(payload.get("max_runs") or 7)
        max_no_effect_retries = max(1, int(payload.get("max_no_effect_retries") or 3))
        no_effect_retries = 0
        refresh_used = False
        completed = 0
        pending_facts: dict[str, Any] | None = None
        stable_self_power: int | None = None
        for probe_index in range(max_runs + max_no_effect_retries + 2):
            facts = pending_facts
            try:
                remaining = yield from self._read_daily_xianyuan_duel_remaining(context, payload)
            except RuntimeError as exc:
                if "无法从 #308[次数] 识别剩余次数" not in str(exc):
                    raise
                facts = facts or (yield from self._wait_current_daily_xianyuan_duel_facts(
                    context,
                    payload,
                    reason=f"round-{completed + 1}-ocr-fallback",
                    self_power_hint=stable_self_power,
                ))
                if facts is None:
                    return (yield from self._defer_daily_xianyuan_duel_runtime(
                        context,
                        payload,
                        scheduler_task_id=scheduler_task_id,
                        reason="次数 OCR 与 Runtime 均未在等待窗口内就绪",
                    ))
                if stable_self_power is None:
                    stable_self_power = int(facts["self_power"])
                remaining = int(facts["remaining_challenges"])
                self._log(
                    "warning",
                    "仙缘斗法：#308[次数] 有界 OCR 仍为空，"
                    f"使用同页 Runtime 剩余次数 {remaining} 兜底",
                )
            if remaining <= 0:
                next_time = next_xianyuan_duel_cycle_trigger_at(job_now()).strftime("%Y-%m-%d %H:%M:%S")
                self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
                self._log(
                    "success",
                    f"仙缘斗法：#308[次数] 已确认剩余为 0，本周期完成，下次 {next_time or '按既有日程'}",
                )
                return "success"
            facts = facts or (yield from self._wait_current_daily_xianyuan_duel_facts(
                context,
                payload,
                reason=f"round-{completed + 1}",
                self_power_hint=stable_self_power,
            ))
            if facts is None:
                return (yield from self._defer_daily_xianyuan_duel_runtime(
                    context,
                    payload,
                    scheduler_task_id=scheduler_task_id,
                    reason="Runtime 未在等待窗口内取得完整当前事实",
                ))
            if stable_self_power is None:
                stable_self_power = int(facts["self_power"])
            facts["self_power"] = stable_self_power
            pending_facts = None
            runtime_remaining = int(facts["remaining_challenges"])
            if runtime_remaining != remaining:
                self._log(
                    "detail",
                    "仙缘斗法：#308[次数] 与 Runtime 次数不同，"
                    f"UI={remaining}、Runtime={runtime_remaining}；完成判据以 UI 为准",
                )
            if completed >= max_runs:
                break

            mapped = yield from self._map_daily_xianyuan_duel_targets(context, facts, payload)
            chosen = choose_xianyuan_duel_target(mapped["targets"], self_power=int(facts["self_power"]))
            if chosen is None:
                refreshes = int(facts.get("remaining_refreshes") or 0)
                if refreshes > 0 and not refresh_used:
                    self._log("action", "仙缘斗法：3 个候选均无法稳妥挑战，使用今日唯一一次刷新")
                    yield from context.wait_click(308, "刷新")
                    refreshed = yield from self._wait_current_daily_xianyuan_duel_facts(
                        context,
                        payload,
                        reason="after-refresh",
                        previous=facts,
                        self_power_hint=stable_self_power,
                    )
                    if refreshed is None:
                        return (yield from self._defer_daily_xianyuan_duel_runtime(
                            context,
                            payload,
                            scheduler_task_id=scheduler_task_id,
                            reason="刷新后 Runtime 候选未在等待窗口内推进",
                        ))
                    refresh_used = True
                    pending_facts = refreshed
                    continue
                chosen = choose_xianyuan_duel_target(
                    mapped["targets"],
                    self_power=int(facts["self_power"]),
                    allow_unbeatable_fallback=True,
                )
                if chosen is None:
                    raise RuntimeError("仙缘斗法：三个候选均缺少有效仙侣战力，不能可靠选择挑战目标")
                self._log(
                    "action",
                    "仙缘斗法：刷新已用尽且三人均强于我方，"
                    f"改为挑战仙侣战力最低的「{chosen['name']}」",
                )

            self._log(
                "action",
                "仙缘斗法：选择 "
                f"{chosen['challenge_shape']}「{chosen['name']}」，积分 {chosen['score']}，"
                f"仙侣战力 {chosen['team_power']}，关系 {chosen['relation_label']}，"
                f"映射 {mapped['method']}",
            )
            yield from context.wait_click_then_scene(308, str(chosen["challenge_shape"]), 309)
            formation_payload = {
                **payload,
                "__xianyuan_duel_facts": facts,
                "__xianyuan_duel_target": chosen,
            }
            yield from self._optimize_daily_xianyuan_duel_formation(context, formation_payload)
            # #308 is the preparation/opponent page, not a valid battle
            # landing.  It may flash during the transition, so the local
            # transaction must wait for the optional battle layer #345 or the
            # layer-0 result page #310.  Only clicking #310 may return to #308.
            try:
                view_after_start = yield from context.wait_click_then_scene(
                    309,
                    "开始挑战",
                    [345, 310],
                    timeout=float(payload.get("battle_result_timeout") or 60.0),
                )
            except TimeoutError:
                _wait_scene_match = yield from context.wait_scene([308], wait=5.0, required=False)
                (recovery_scene_id, recovery_score, _recovery_frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if recovery_scene_id != 308:
                    raise
                recovery_remaining = yield from self._read_daily_xianyuan_duel_remaining(
                    context,
                    payload,
                )
                if recovery_remaining >= remaining:
                    no_effect_retries += 1
                    if no_effect_retries > max_no_effect_retries:
                        raise RuntimeError(
                            "仙缘斗法：挑战后持续停在 #308 且剩余次数未变化，"
                            f"已重试 {max_no_effect_retries} 次"
                        )
                    self._log(
                        "warning",
                        "仙缘斗法：挑战后观察 60 秒仍在 #308，"
                        f"剩余次数仍为 {recovery_remaining}，判定本次点击未生效并重试 "
                        f"{no_effect_retries}/{max_no_effect_retries}",
                    )
                    continue
                completed += max(1, remaining - recovery_remaining)
                self._log(
                    "warning",
                    "仙缘斗法：未观察到 #310，但 #308 剩余次数已从 "
                    f"{remaining} 降为 {recovery_remaining}，按本轮已生效继续处理",
                )
                pending_facts = yield from self._wait_current_daily_xianyuan_duel_facts(
                    context,
                    payload,
                    reason=f"after-recovered-round-{completed}",
                    previous=facts,
                    self_power_hint=stable_self_power,
                )
                if pending_facts is None:
                    return (yield from self._defer_daily_xianyuan_duel_runtime(
                        context,
                        payload,
                        scheduler_task_id=scheduler_task_id,
                        reason=f"恢复第 {completed} 轮后 Runtime 未在等待窗口内推进",
                    ))
                continue
            if observed_scene_id(view_after_start) == 345:
                view_after_start = yield from context.wait_click_then_scene(345, "跳过", 310)
            yield from context.wait_click_then_scene(310, "点击继续", 308)
            completed += 1
            pending_facts = yield from self._wait_current_daily_xianyuan_duel_facts(
                context,
                payload,
                reason=f"after-round-{completed}",
                previous=facts,
                self_power_hint=stable_self_power,
            )
            if pending_facts is None:
                return (yield from self._defer_daily_xianyuan_duel_runtime(
                    context,
                    payload,
                    scheduler_task_id=scheduler_task_id,
                    reason=f"第 {completed} 轮后 Runtime 未在等待窗口内推进",
                ))
        raise RuntimeError(
            f"仙缘斗法：达到单轮安全上限 {max_runs}，已挑战 {completed} 次但 #308[次数] 仍大于 0"
        )

    def daily_mojie_raid_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Skip the weekly closed interval; Monday's first run is at 13:00."""
        now = job_now()
        if now.weekday() == 0 and now.time() < time_cls(13, 0):
            return self._persist_admission_decision(dict(payload or {}), {
                "result": "success",
                "message": "日常_奇袭魔界：周一首次运行时间为 13:00，未执行游戏操作",
                "next_time": now.replace(hour=13, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"),
                "current_scene": None,
            })
        if now.weekday() != 6 or now.time() < time_cls(22, 0):
            return None
        return self._persist_admission_decision(dict(payload or {}), {
            "result": "success",
            "message": "日常_奇袭魔界：周日 22:00 窗口已关闭，直接顺延下周一，未执行游戏操作",
            "next_time": self._next_mojie_raid_week_start_time_text(now),
            "current_scene": None,
        })

    def _execute_daily_mojie_raid_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})

        def terminal(message: str) -> str:
            set_completion_message = getattr(context, "set_completion_message", None)
            if callable(set_completion_message):
                set_completion_message(str(message))
            return "success"

        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_奇袭魔界资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        raid_scenes = {319, 320, 321, 322, 323, 324}
        settlement_only = self._mojie_raid_settlement_only()
        joined_existing_team = False
        _wait_scene_match = yield from context.wait_scene([331, 330, *sorted(raid_scenes), 69, 34, 20], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 331:
            if settlement_only:
                next_time = self._schedule_mojie_raid_settled_week(payload)
                yield from context.go_scene(34)
                return terminal(f"周日自动挑战已结束，已加入页面收尾完成；下次 {next_time}")
            # #331 is the already-joined team page.  Whether it was reached after
            # a committed #324 transaction or after replaying #320 against an
            # already-changed game state, the current trigger must not attack
            # again.  Persist the next legal trigger before low-risk cleanup and
            # report success: the business effect for this trigger already exists.
            next_time = self._schedule_next_mojie_raid_trigger(
                payload,
                reason="起点已处于 #331「已加入」状态，本轮业务已完成",
            )
            yield from context.go_scene(34)
            return terminal(f"#331 已确认入队，本轮幂等完成；下次 {next_time}")
        if scene_id == 330:
            scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(context)
        if scene_id not in {69, *raid_scenes}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label="日常_奇袭魔界")
        if scene_id not in {69, *raid_scenes}:
            raise RuntimeError("日常_奇袭魔界：未能进入 #69 日常列表")
        if scene_id == 69:
            debug_payload = payload.get("debug") if isinstance(payload.get("debug"), dict) else {}
            stop_after_daily_entry = bool(
                payload.get("stop_after_daily_entry")
                or payload.get("pause_after_daily_entry")
                or debug_payload.get("stop_after_daily_entry")
                or debug_payload.get("pause_after_daily_entry")
            )
            status = yield from context.open_daily_entry(
                label="日常_奇袭魔界",
                title_pattern=r"参与.{0,4}奇|奇.{0,4}魔|魔界",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
                if settlement_only:
                    next_time = self._schedule_mojie_raid_settled_week(payload)
                    return terminal(f"周日自动挑战已结束，日常入口已消失；下次 {next_time}")
                self._record_daily_entry_not_found_retry(
                    payload,
                    task_id="legacy-daily-mojie-raid",
                    task_type="daily_mojie_raid",
                    label="日常_奇袭魔界",
                    entry_label="魔界",
                )
                return "skipped"
            if status == "done":
                raise RuntimeError("日常_奇袭魔界：入口行完成态不能作为奇袭魔界完成判据")
            if stop_after_daily_entry:
                yield from context.wait_action_settle(float(payload.get("entry_pause_settle_seconds") or 1.5))
                _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
                (current_scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                scene_label = f"#{current_scene_id}" if current_scene_id is not None else "unknown"
                self._log(
                    "warning",
                    f"日常_奇袭魔界：已点击日常入口，按调试要求暂停；当前 {scene_label} {score:.0f}%，OCR={text[:120]}",
                )
                return "skipped"
            try:
                waited = yield from context.wait_scene([319, 330], label="日常_奇袭魔界：等待奇袭魔界 #319")
                if getattr(waited, "id", waited) == 330:
                    scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(context)
                else:
                    scene_id = 319
            except TimeoutError as exc:
                yield from self._handle_daily_mojie_raid_open_blocker_placeholder(context, payload)
                raise RuntimeError("日常_奇袭魔界：入口点击后未到达 #319，疑似遇到未实现的特殊弹窗") from exc
        if settlement_only:
            # 周日 13:00 是最后报名机会，21:30 自动挑战；此后仅收尾，
            # 即使页面仍显示剩余次数，也不能重新提交参与进攻。
            next_time = self._schedule_mojie_raid_settled_week(payload)
            yield from context.go_scene(34)
            return terminal(f"周日自动挑战已结束，已完成页面收尾；下次 {next_time}")
        if scene_id == 319:
            self._log("success", "日常_奇袭魔界：已到达 #319")
            shape_matches = getattr(context, "shape_matches", None)
            numbers, text = context.ocr_numbers_in_shapes(
                319,
                ("剩余次数",),
                padding=int(payload.get("mojie_raid_remaining_padding") or 16),
                max_attempts=1,
            )
            if not numbers and "确定" in str(text or ""):
                self._log(
                    "info",
                    "日常_奇袭魔界：底层 #319 被前置「确定」浮层覆盖；"
                    "先完成既有 #330 确认闭环，再重新检查「我的队伍」",
                )
                scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(context)
                if scene_id == 319:
                    numbers, text = context.ocr_numbers_in_shapes(
                        319,
                        ("剩余次数",),
                        padding=int(payload.get("mojie_raid_remaining_padding") or 16),
                        max_attempts=1,
                    )
            remaining, text = self.read_daily_mojie_raid_remaining(
                context, payload, initial_ocr=(numbers, text),
            )
            self._log("detail", f"日常_奇袭魔界：剩余次数 {remaining}，OCR={text[:80]}")
            if remaining <= 0:
                # 单帧 OCR 的 0 会直接把整个作业推进到下周，代价过高。
                # 等待后重新取一张真实帧确认；若第二帧恢复为正数，继续本周流程。
                confirm_seconds = float(payload.get("mojie_raid_zero_confirm_seconds") or 2.0)
                self._log("warning", "日常_奇袭魔界：首次读到剩余次数 0，等待新帧复核后再结束本周")
                yield from context.wait_action_settle(confirm_seconds)
                context.clear_frame()
                remaining, confirm_text = self.read_daily_mojie_raid_remaining(
                    context, payload,
                )
                self._log("detail", f"日常_奇袭魔界：剩余次数复核 {remaining}，OCR={confirm_text[:80]}")
            if remaining <= 0:
                confirmed_at = job_now()
                if not self._mojie_raid_completion_window_open(confirmed_at):
                    next_time = self._schedule_mojie_raid_thursday_verification(
                        payload,
                        reason="两次有效新帧确认剩余次数为 0，但尚未到周四，不能判定本周完成",
                        now=confirmed_at,
                    )
                    message = f"两次有效新帧确认剩余次数为 0，但未到周四；复核时间 {next_time}"
                else:
                    next_time = self._schedule_next_mojie_raid_week(
                        payload,
                        reason="周四起两次有效新帧确认剩余次数为 0，本周已完成",
                        confirmed_remaining=remaining,
                        confirmed_at=confirmed_at,
                    )
                    message = f"周四起两次有效新帧确认剩余次数为 0，本周完成；下次 {next_time}"
                yield from context.wait_click(319, "返回")
                return terminal(message)
            existing_team_match = shape_matches(319, "队伍") if callable(shape_matches) else None
            if existing_team_match:
                # 剩余次数大于 0 时，「我的队伍」OCR 才是本轮已配置的幂等事实。
                next_time = self._schedule_next_mojie_raid_trigger(
                    payload,
                    reason="#319 已显示「我的队伍」，本轮业务已完成",
                )
                yield from context.wait_click_then_scene(319, "返回", 34)
                return terminal(f"#319 OCR 已确认「我的队伍」，本轮幂等完成；下次 {next_time}")
            yield from context.wait_click_then_scene(319, "参与进攻", 320)
            scene_id = 320
        else:
            self._log("detail", f"日常_奇袭魔界：从 #{scene_id} 恢复后续流程")
        if scene_id == 320:
            # 「进攻倒计时」只限制战斗结算，不限制提前选择据点和配置队伍。
            # 本轮幂等事实仍然必须来自 #319「队伍」OCR，或建队/入队后到达
            # #324/#331；不能因为倒计时大于 0 就把 Job 延后并冒充完成。
            countdown_text = context.ocr_text_in_shapes(
                320,
                ("进攻倒计时标识",),
                padding=int(payload.get("mojie_raid_attack_countdown_padding") or 12),
            )
            countdown_seconds = self._daily_mojie_raid_attack_countdown_seconds(countdown_text)
            if countdown_seconds is not None and countdown_seconds > 0:
                self._log(
                    "detail",
                    "日常_奇袭魔界：#320 仍有进攻倒计时，继续进入据点配置队伍，"
                    f"OCR={countdown_text[:120]}",
                )
            scene_id = yield from self._click_daily_mojie_raid_top_attack_target(context, payload)
            if scene_id == 331:
                # A previous/current join can make the #320 target click land
                # directly on the already-joined page, skipping #321..#324.
                # This direct transition is authoritative transaction-local
                # evidence.  Commit scheduling before cleanup so a navigation
                # failure cannot make the non-replayable action due again.
                next_time = self._schedule_next_mojie_raid_trigger(
                    payload,
                    reason="点击据点后已处于 #331「已加入」状态，本轮业务已完成",
                )
                yield from context.click_shape_center_then_scene(331, "返回", 320)
                yield from context.wait_click(320, "返回")
                yield from context.wait_click_then_scene(319, "返回", 34)
                return terminal(f"点击据点后 #331 已确认入队，本轮幂等完成；下次 {next_time}")
        if scene_id == 321:
            # #322 is only a confirmation popup, but a persistent #321 can mean
            # the server-side weekly attack allowance is already exhausted or
            # another business guard rejected the request.  Re-clicking cannot
            # distinguish those states and may duplicate a delayed UI action.
            yield from context.wait_click_then_scene(321, "创建队伍", 322, max_clicks=1)
            scene_id = 322
        if scene_id == 322:
            # 业务语义：奇袭魔界的建队额度由整个同盟共享，并非每个玩家各有
            # 3 个名额；每个触发周期同盟最多只能建立 3 支队伍。作业触发较晚
            # 时，盟友可能已经把额度用完，因此 #322 显示 3/3 是“本周期同盟
            # 建队名额已满”，不是「确定」按钮失效，也不应靠重复点击恢复。
            # 此时不能再创建队伍，但仍可加入本联盟已经创建且未满员的队伍。
            # 队伍卡片的位置、队长名和人数都会变化；#321 的“加入”状态只会
            # 出现在本联盟且可加入的 roleItem 上，因此用它定位动态卡片，再按
            # 资产模板记录的相对位移点击真正绑定事件的整个人物卡片。
            team_numbers, team_text = context.ocr_numbers_in_shapes(
                322,
                ("队伍额度",),
                padding=int(payload.get("mojie_raid_team_count_padding") or 12),
            )
            team_count: int | None = None
            team_limit: int | None = None
            if len(team_numbers) >= 2:
                team_count, team_limit = int(team_numbers[0]), int(team_numbers[1])
            else:
                fraction = parse_ocr_values(team_text, expected_count=2, allow_extra_numbers=True)
                if fraction is not None:
                    team_count, team_limit = fraction
            self._log(
                "detail",
                f"日常_奇袭魔界：#322 队伍数 {team_count if team_count is not None else '?'}"
                f"/{team_limit if team_limit is not None else '?'}，OCR={str(team_text or '')[:80]}",
            )
            if team_count is not None and team_limit is not None and team_limit > 0 and team_count >= team_limit:
                self._log(
                    "action",
                    f"日常_奇袭魔界：#322 建队额度已满 {team_count}/{team_limit}，返回 #321 加入友方队伍",
                )
                yield from context.wait_click_then_scene(322, "返回", 321)
                joined_scene = yield from self._join_daily_mojie_raid_friendly_team(context, payload)
                if joined_scene is None:
                    yield from context.click_shape_center_then_scene(321, "返回", 320)
                    yield from context.wait_click(320, "返回")
                    yield from context.wait_click_then_scene(319, "返回", 34)
                    next_time = self._schedule_next_mojie_raid_trigger(
                        payload,
                        reason=f"#322 建队额度已满 {team_count}/{team_limit}，#321 暂无可加入友方队伍",
                    )
                    return terminal(
                        f"建队额度已满 {team_count}/{team_limit} 且暂无可加入队伍；下次 {next_time}"
                    )
                scene_id = joined_scene
                joined_existing_team = True
            else:
                yield from context.wait_click_then_scene(322, "下拉选项", 323)
                scene_id = 323
        if scene_id == 323:
            yield from context.wait_click_then_scene(323, "开启", 322)
            # 创建队伍的“确定”输入历史上经常不生效；#322 仍被可靠识别时，
            # 继续补点同一已标注按钮。重试必须有界，且一旦离开 #322 或
            # 进入 unknown，wait_click_then_scene 会立即停止，不能盲目连点。
            yield from context.wait_click_then_scene(
                322,
                "确定",
                324,
                timeout=float(payload.get("mojie_raid_confirm_wait_timeout") or 8),
                max_clicks=int(payload.get("mojie_raid_confirm_max_clicks") or 6),
            )
            scene_id = 324
        if scene_id == 324:
            # #324 is the authoritative commit evidence: the join/create action
            # has succeeded and the player is on the team page.  Persist the
            # next legal trigger before any cleanup.  A later return/navigation
            # failure must never make this non-replayable transaction due again.
            committed_reason = (
                "已确认加入友方队伍并进入 #324"
                if joined_existing_team
                else "已确认建队成功并进入 #324"
            )
            next_time = self._schedule_next_mojie_raid_trigger(payload, reason=committed_reason)
            terminal_message = f"{committed_reason}，本轮幂等完成；下次 {next_time}"
            # 建队成功后的队伍页会在短暂动画结束后让 #324 的「鼓舞」
            # 图像身份失效；此时再用 wait_click 会永远等不到源场景。
            # 「返回」本身是固定标注坐标，直接点击，并兼容实机可能跳到
            # 中间页 #331 或直接回到世界 #34 两种落点。
            landed = yield from context.click_shape_center_then_scene(324, "返回", 331, 34)
            scene_id = int(getattr(landed, "id", landed))
            if scene_id == 34:
                return terminal(terminal_message)
        if scene_id == 331:
            yield from context.click_shape_center_then_scene(331, "返回", 320)
            yield from context.wait_click(320, "返回")
            yield from context.wait_click_then_scene(319, "返回", 34)
        return terminal(locals().get("terminal_message", "已确认奇袭魔界队伍状态，本轮幂等完成"))

    def _daily_mojie_raid_join_click_delta(
        self,
        context: BehaviorTreeContext,
    ) -> tuple[float, float]:
        """Read the roleItem click offset from #321 assets instead of hard-coding pixels."""

        anchor = context.shape(321, "友方加入锚点")
        click_target = context.shape(321, "友方人物点击点")
        view = context.view(321)
        width, height = context.runner._frame_size(view.raw)

        def center(shape: Shape) -> tuple[float, float]:
            raw = shape.raw
            return (
                (float(raw.get("x") or 0) + float(raw.get("w") or 0) / 2) * width,
                (float(raw.get("y") or 0) + float(raw.get("h") or 0) / 2) * height,
            )

        anchor_x, anchor_y = center(anchor)
        click_x, click_y = center(click_target)
        return click_x - anchor_x, click_y - anchor_y

    def _join_daily_mojie_raid_friendly_team(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        """Join one visible friendly corps, horizontally loading #321 when needed.

        Static client logic (`UnionDemonBossTeamItem`) proves that “加入” is
        rendered only for our cross-union/club when the player has no team and
        the team is not full.  The text itself has no click handler: the handler
        belongs to `roleItem`, so the click point is derived from the two #321
        template shapes and translated to each current OCR anchor.
        """

        max_scrolls = max(0, int(payload.get("mojie_raid_join_max_scrolls") or 8))
        max_candidates = max(1, int(payload.get("mojie_raid_join_max_candidates") or 6))
        settle_seconds = float(payload.get("mojie_raid_join_scroll_settle_seconds") or 2.0)
        dx, dy = self._daily_mojie_raid_join_click_delta(context)
        attempted = 0
        seen_pages: set[tuple[str, ...]] = set()

        for scroll_index in range(max_scrolls + 1):
            frame = context.cur_frame(update=True)
            # Restrict both candidate discovery and page identity to the
            # annotated horizontal team window.  A tuple of only ``3/5`` /
            # ``5/5`` counters is not a page identity: different pages often
            # have the same member-count distribution and would stop scanning
            # too early.
            fragments = context.ocr_fragments_in_shapes(
                321,
                ("队伍窗口",),
                frame_data_url=frame,
                padding=0,
            )
            markers = [
                item
                for item in fragments
                if re.search(r"加[入人]", re.sub(r"\s+", "", str(item.get("text") or "")))
            ]
            markers.sort(key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))
            page_key = tuple(
                sorted(
                    re.sub(r"\s+", "", str(item.get("text") or ""))
                    for item in fragments
                    if re.sub(r"\s+", "", str(item.get("text") or ""))
                )
            )
            self._log(
                "detail",
                f"日常_奇袭魔界：#321 横向页 {scroll_index + 1} 发现 {len(markers)} 个友方加入标识，人数键={page_key}",
            )

            for marker in markers:
                if attempted >= max_candidates:
                    break
                x = float(marker.get("x") or 0) + float(marker.get("w") or 0) / 2 + dx
                y = float(marker.get("y") or 0) + float(marker.get("h") or 0) / 2 + dy
                attempted += 1
                self._log(
                    "action",
                    f"日常_奇袭魔界：按第 {attempted} 个“加入”锚点定位并点击友方人物卡片",
                )
                context.click_frame_point(321, x, y)
                try:
                    # #508 is the formally annotated "是否加入该队伍" business
                    # scene.  It must be the Layer-0 target of this transition;
                    # a one-shot full-frame OCR probe can miss the animated popup
                    # and used to leave it open while the task kept scrolling #321.
                    yield from context.wait_scene(
                        [508],
                        wait=float(payload.get("mojie_raid_join_popup_timeout_seconds") or 8.0),
                        label="日常_奇袭魔界：等待加入队伍确认 #508",
                    )
                except RuntimeError:
                    self._log(
                        "warning",
                        "日常_奇袭魔界：人物卡片点击后未识别到 #508 入队确认，尝试下一候选",
                    )
                    continue

                context.click_shape_center(508, "确认")
                try:
                    yield from context.wait_scene(
                        [324],
                        wait=float(payload.get("mojie_raid_join_result_timeout_seconds") or 12.0),
                        label="日常_奇袭魔界：确认加入后等待队伍页 #324",
                    )
                except RuntimeError as exc:
                    raise RuntimeError("日常_奇袭魔界：确认加入后未识别到队伍页 #324") from exc
                self._log("success", "日常_奇袭魔界：已加入友方队伍并进入队伍页 #324")
                return 324

            if attempted >= max_candidates or scroll_index >= max_scrolls:
                break
            if page_key and page_key in seen_pages:
                self._log("detail", "日常_奇袭魔界：#321 横向内容已重复，停止继续加载")
                break
            if page_key:
                seen_pages.add(page_key)
            changed = yield from context.scroll_shape_content(
                321,
                "队伍窗口",
                direction="right",
                ratio=float(payload.get("mojie_raid_join_scroll_ratio") or 0.5),
                duration=float(payload.get("mojie_raid_join_scroll_duration") or 1.2),
                settle_seconds=settle_seconds,
                stable_sample_count=1,
                unchanged_confirmations=2,
            )
            if not changed:
                break
        return None

    def _click_daily_mojie_raid_top_attack_target(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        target_shape = str(payload.get("mojie_raid_target_shape") or "检索区域/修罗").strip()
        match_timeout = float(
            payload.get("mojie_raid_target_match_timeout")
            or getattr(context, "default_wait_click_timeout", self._daily_default_wait_condition_timeout)
        )
        wait_timeout = float(
            payload.get("mojie_raid_target_wait_timeout")
            or self._daily_default_wait_condition_timeout
        )
        settle_seconds = float(payload.get("mojie_raid_target_click_settle_seconds") or 1.5)
        max_clicks = max(1, int(payload.get("mojie_raid_target_max_clicks") or 2))
        click_x_ratio = float(payload.get("mojie_raid_target_click_x_ratio") or 1.21875)
        click_y_ratio = float(payload.get("mojie_raid_target_click_y_ratio") or (5.0 / 3.0))
        last_error: TimeoutError | None = None

        for attempt in range(1, max_clicks + 1):
            self._log(
                "action",
                f"日常_奇袭魔界：定位并点击 #320「{target_shape}」 {attempt}/{max_clicks}",
            )
            yield from context.wait_click(
                320,
                target_shape,
                timeout=match_timeout,
                x_ratio=click_x_ratio,
                y_ratio=click_y_ratio,
            )
            yield from context.wait_action_settle(settle_seconds)
            try:
                waited = yield from context.wait_scene(
                    [321,
                    331],
                    wait=wait_timeout,
                    label="日常_奇袭魔界：点击 #320 修罗据点后等待 #321/#331",
                )
                return int(getattr(waited, "id", waited) or 0)
            except TimeoutError as exc:
                last_error = exc
                _wait_scene_match = yield from context.wait_scene([320, 321, 331], wait=5.0, required=False)
                (scene_id, score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if scene_id != 320 or attempt >= max_clicks:
                    raise
                self._log(
                    "warning",
                    f"日常_奇袭魔界：点击修罗据点后仍在 #320 {score:.0f}%，重新定位后重试",
                )

        raise last_error or TimeoutError("日常_奇袭魔界：点击 #320 修罗据点后未进入 #321")

    def read_daily_mojie_raid_remaining(
        self, context: Any, payload: dict[str, Any] | None = None, *, initial_ocr=None,
    ):
        """次数语义锚定留在业务；无数值时换帧重试由通用 OCR 接口负责。"""
        payload = payload or {}

        def parse_remaining(text):
            anchored = self._daily_mojie_raid_remaining_ocr_fallback(text)
            values = parse_ocr_values(text)
            if anchored is not None:
                return anchored
            return values[0] if values else None

        if initial_ocr is not None:
            _numbers, initial_text = initial_ocr
            remaining = parse_remaining(initial_text)
            if remaining is not None:
                return remaining, initial_text
        remaining, text = context.ocr_value_in_shapes(
            319, ("剩余次数",), parse_value=parse_remaining,
            padding=int(payload.get("mojie_raid_remaining_padding") or 16),
            max_attempts=max(1, int(payload.get("mojie_raid_remaining_ocr_attempts") or 5)),
            retry_interval=max(0.1, float(payload.get("mojie_raid_remaining_ocr_interval") or 2.0)),
        )
        if remaining is None:
            raise RuntimeError(f"日常_奇袭魔界：多次 OCR 未能读取 #319「剩余次数」，最后 OCR={text[:120]}")
        return remaining, text

    def _daily_mojie_raid_remaining_ocr_fallback(self, text: str) -> int | None:
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(
            r"(?:本周)?剩余(?:进攻)?次数\s*[:：]?\s*([0-9]+|[BO])(?:\D|$)",
            normalized,
            re.IGNORECASE,
        )
        if not match:
            return None
        token = str(match.group(1) or "").upper()
        if token == "B":
            return 8
        if token == "O":
            return 0
        return int(token)

    def _daily_mojie_raid_attack_countdown_seconds(self, text: str) -> int | None:
        """Parse the #320 pre-attack countdown without treating it as a click failure."""

        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        matches = re.findall(
            r"进攻倒计时\s*[:：]?\s*(-?)\s*([0-9]{1,3})\s*[:：]\s*([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})",
            normalized,
        )
        if len(matches) != 1:
            return None
        sign, hours_text, minutes_text, seconds_text = matches[0]
        hours, minutes, seconds = (
            int(hours_text),
            int(minutes_text),
            int(seconds_text),
        )
        if minutes >= 60 or seconds >= 60:
            return None
        if sign == "-":
            # The game keeps counting below zero after the attack window opens.
            # A valid negative countdown therefore means "open now", not an
            # unreadable timer and not a future delay.
            return 0
        return hours * 3600 + minutes * 60 + seconds

    def _next_mojie_raid_week_start_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        now = now or job_now()
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7
        next_monday = now + timedelta(days=days_until_next_monday)
        return next_monday.replace(
            hour=13,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _mojie_raid_completion_window_open(
        self,
        now: datetime | None = None,
    ) -> bool:
        """Only Thursday and later may close the current raid week."""

        current = now or job_now()
        return current.weekday() >= 3

    def _next_mojie_raid_thursday_verification_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        """Return this week's Thursday 10:00 verification boundary."""

        current = now or job_now()
        days_until_thursday = 3 - current.weekday()
        if days_until_thursday <= 0:
            raise ValueError("奇袭魔界周四复核时间只适用于周一至周三")
        return (current + timedelta(days=days_until_thursday)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _next_mojie_raid_followup_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        """Check at 13:00 and midnight, skipping Monday midnight."""

        current = now or job_now()
        if self._mojie_raid_settlement_only(current):
            return self._next_mojie_raid_week_start_time_text(current)
        candidate = current.replace(hour=13, minute=0, second=0, microsecond=0)
        if candidate > current:
            return candidate.strftime("%Y-%m-%d %H:%M:%S")
        if current.weekday() == 6:
            return self._next_mojie_raid_week_start_time_text(current)
        return (
            current + timedelta(days=1)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _mojie_raid_settlement_only(self, now: datetime | None = None) -> bool:
        """Sunday 21:30 automatic battle ends the week's registration cycle."""
        current = now or job_now()
        return current.weekday() == 6 and current.time() >= time_cls(21, 30)

    def _schedule_mojie_raid_settled_week(self, payload: dict[str, Any]) -> str:
        """Close by the weekly settlement boundary, without fabricating zero attempts."""
        if not self._mojie_raid_settlement_only():
            raise ValueError("奇袭魔界日历收尾仅适用于周日 21:30 后")
        next_time = self._next_mojie_raid_week_start_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：周日自动挑战已结束，下次 {next_time}")
        return next_time

    def _schedule_next_mojie_raid_week(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        confirmed_remaining: int,
        confirmed_at: datetime | None = None,
    ) -> str:
        current = confirmed_at or job_now()
        if int(confirmed_remaining) != 0:
            raise ValueError("奇袭魔界只有明确确认剩余次数为 0 才能推进到下周")
        if not self._mojie_raid_completion_window_open(current):
            raise ValueError("奇袭魔界只有周四起才能判定本周完成")
        next_time = self._next_mojie_raid_week_start_time_text(current)
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：{reason}，下次 {next_time}")
        return next_time

    def _schedule_mojie_raid_thursday_verification(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        now: datetime | None = None,
    ) -> str:
        next_time = self._next_mojie_raid_thursday_verification_time_text(now)
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：{reason}，本周周四复核 {next_time}")
        return next_time

    def _schedule_next_mojie_raid_trigger(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid")
        next_time = self._next_mojie_raid_followup_time_text()
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        self._log("success", f"日常_奇袭魔界：{reason}，本周仍需继续，下次 {next_time}")
        return next_time

    def _handle_daily_mojie_raid_open_blocker_placeholder(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        del context, payload
        self._log("warning", "日常_奇袭魔界：特殊弹窗处理占位，等待后续补标/补流程")
        if False:
            yield None
        return "not_implemented"

    def _confirm_daily_mojie_raid_reward_confirmation(
        self,
        context: BehaviorTreeContext,
    ):
        self._log("action", "日常_奇袭魔界：检测到 #330 前置奖励确认，点击「确定」后继续等待 #319")
        waited = yield from context.wait_click_then_scene(
            330,
            "确定",
            [319],
            settle_seconds=1.5,
            timeout=20.0,
        )
        scene_id = getattr(waited, "id", waited)
        if scene_id != 319:
            raise RuntimeError(
                "日常_奇袭魔界：#330 点击「确定」后未确认到 #319，"
                f"实际 #{scene_id if scene_id is not None else 'unknown'}"
            )
        return scene_id

    def _execute_daily_weekly_dungeon_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_周本资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([420, 419, 327, 326, 325, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        recorded_next_time = self._daily_weekly_dungeon_recorded_future(payload)
        if scene_id in {419, 420}:
            yield from self._wait_daily_weekly_dungeon_battle_completion(context, payload, battle_started=True)
            self._record_daily_weekly_dungeon_done(
                payload,
                message="从运行中的副本恢复并确认战斗结束，已回到 #34",
            )
            return "success"
        if recorded_next_time:
            self._log("success", f"日常_周本：本周期完成事实已记录，下次 {recorded_next_time}")
            return "success"
        if scene_id not in {327, 326, 325, 69}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, context, stop_event, frame, scene_id, text, label="日常_周本")
        if scene_id not in {327, 326, 325, 69}:
            raise RuntimeError("日常_周本：未能进入 #69 日常列表")
        if scene_id == 69:
            status = yield from context.open_daily_entry(
                label="日常_周本",
                title_pattern="周本",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
                self._record_daily_entry_not_found_retry(
                    payload,
                    task_id="daily-weekly-dungeon",
                    task_type="daily_weekly_dungeon",
                    label="日常_周本",
                    entry_label="周本",
                )
                return "skipped"
            scene_id = 325
        if scene_id == 325:
            scene_id = yield from self._open_daily_weekly_dungeon_tiangong_view(context, payload)
        if scene_id == 326:
            scene_id = yield from self._open_daily_weekly_dungeon_challenge_view(context, payload)
        if scene_id == -1:
            return "success"
        yield from context.wait_click(327, "挑战")
        yield from self._wait_daily_weekly_dungeon_battle_completion(context, payload)
        self._record_daily_weekly_dungeon_done(
            payload,
            message="战斗结束，已回到 #34",
        )
        return "success"

    def _daily_weekly_dungeon_recorded_future(self, payload: dict[str, Any]) -> str | None:
        task_id = str(payload.get("__scheduler_task_id") or "daily-weekly-dungeon").strip() or "daily-weekly-dungeon"
        task = next(
            (item for item in read_scheduler_tasks(now=job_now()) if str(item.get("id") or "") == task_id),
            None,
        )
        next_time = str(task.get("next_time") or "").strip() if isinstance(task, dict) else ""
        due_at = parse_data_annotation_task_time(next_time) if next_time else None
        if due_at is None or due_at <= time.time():
            return None
        return next_time if next_time == self._daily_weekly_dungeon_next_time_text(payload) else None

    def _wait_daily_weekly_dungeon_battle_completion(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        battle_started: bool = False,
    ):
        if not battle_started:
            yield from context.wait_scene(
                [419,
                420],
                wait=float(payload.get("battle_start_timeout") or 60.0),
                label="日常_周本：等待副本自动战斗真正启动 #419/#420",
            )
        yield from context.wait_scene(
            [34],
            wait=float(payload.get("battle_return_world_timeout") or 600.0),
            label="日常_周本：等待副本自动战斗结束并真正回到世界 #34",
        )

    def _daily_weekly_dungeon_next_time_text(self, payload: dict[str, Any]) -> str:
        now = job_now()
        days_until_next_monday = (7 - now.weekday()) % 7 or 7
        next_monday = now + timedelta(days=days_until_next_monday)
        return next_monday.replace(hour=5, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def _record_daily_weekly_dungeon_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._daily_weekly_dungeon_next_time_text(payload)
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-weekly-dungeon"),
            next_time,
        )
        self._log("success", f"日常_周本：{message}，下次 {next_time}")
        return next_time

    def _open_daily_weekly_dungeon_tiangong_view(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("weekly_tiangong_max_attempts") or 3))
        # 进入玉霄天宫会播放不可交互的金色传送动画。真实工程运行已观察到
        # 动画超过 8 秒；动画期间 Layer 0 正确返回 unknown，不能把它补成业务
        # scene，也不能因此重放「天宫」动作。给正式后继 #326 留出完整转场窗口。
        wait_timeout = float(payload.get("weekly_tiangong_wait_timeout") or 60.0)
        settle_seconds = float(payload.get("weekly_tiangong_settle_seconds") or 1.5)
        last_error: TimeoutError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                yield from context.wait_click_then_scene(
                    325,
                    "天宫",
                    326,
                    settle_seconds=settle_seconds,
                    timeout=wait_timeout,
                    label="日常_周本：等待进入 #326 玉霄天宫页",
                )
                return 326
            except TimeoutError as exc:
                last_error = exc
                _wait_scene_match = yield from context.wait_scene([326, 325, 69], wait=5.0, required=False)
                (scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 326:
                    return 326
                if scene_id == 325 and attempt < max_attempts:
                    self._log(
                        "warning",
                        f"日常_周本：点击 #325「天宫」后仍在 #325 {score:.0f}%，重试 {attempt + 1}/{max_attempts}",
                    )
                    continue
                raise TimeoutError(f"日常_周本：点击 #325「天宫」后未到达 #326，当前 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}") from exc
        if last_error is not None:
            raise last_error
        raise TimeoutError("日常_周本：未能进入 #326 玉霄天宫页")

    def _open_daily_weekly_dungeon_challenge_view(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("tiangong_challenge_max_attempts") or 3))
        wait_timeout = float(payload.get("tiangong_challenge_wait_timeout") or 10.0)
        settle_seconds = float(payload.get("tiangong_challenge_settle_seconds") or 1.5)
        pre_click_wait = max(0.0, float(payload.get("tiangong_challenge_pre_click_wait") or 6.0))
        last_error: TimeoutError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                yield from context.wait_scene([326], wait=wait_timeout, label="日常_周本：确认 #326 玉霄天宫页")
                if pre_click_wait > 0:
                    self._log("wait", f"日常_周本：等待 #326 浮动战报消失 {pre_click_wait:.1f}s")
                    yield from context.wait_action_settle(pre_click_wait)
                try:
                    text = context.ocr_text(update=True)
                except TypeError:
                    text = context.ocr_text(context.cur_frame(update=True) if hasattr(context, "cur_frame") else None)
                remaining = self._daily_weekly_dungeon_remaining_count(text)
                if remaining is not None:
                    self._log("detail", f"日常_周本：#326 本周剩余奖励次数 {remaining}，OCR={text[:80]}")
                    if remaining <= 0:
                        self._record_daily_weekly_dungeon_done(payload, message="#326 显示本周剩余奖励次数为 0")
                        return -1
                    if remaining < 3:
                        self._record_daily_weekly_dungeon_done(
                            payload,
                            message=f"#326 显示本周剩余奖励次数已降为 {remaining}/3，本周挑战已执行",
                        )
                        return -1
                yield from context.wait_click_then_scene(
                    326,
                    "挑战",
                    327,
                    settle_seconds=settle_seconds,
                    timeout=wait_timeout,
                    label="日常_周本：等待进入 #327 挑战准备页",
                )
                return 327
            except TimeoutError as exc:
                last_error = exc
                _wait_scene_match = yield from context.wait_scene([327, 326], wait=5.0, required=False)
                (scene_id, score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 327:
                    return 327
                if scene_id == 326 and attempt < max_attempts:
                    self._log(
                        "warning",
                        f"日常_周本：点击 #326「挑战」后仍在 #326 {score:.0f}%，重试 {attempt + 1}/{max_attempts}",
                    )
                    continue
                raise TimeoutError(f"日常_周本：点击 #326「挑战」后未进入 #327，当前 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}") from exc
        if last_error is not None:
            raise last_error
        raise TimeoutError("日常_周本：未能进入 #327 挑战准备页")

    def _daily_weekly_dungeon_remaining_count(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(str(text or "")).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("O", "0").replace("o", "0")
        compact = re.sub(r"\s+", "", normalized)
        if "本周剩余奖励次数" not in compact:
            return None
        match = re.search(r"本周剩余奖励次数[:：]?(.*)", compact)
        if not match:
            return None
        tail = match.group(1)
        fraction = parse_ocr_values(tail, expected_count=2, allow_extra_numbers=True)
        if fraction is not None:
            return fraction[0]
        single = parse_ocr_values(tail, expected_count=1)
        return single[0] if single is not None else None

    def _read_current_daily_xianyuan_duel_facts(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
        self_power_hint: int | float | None = None,
    ) -> dict[str, Any]:
        # Target selection only needs the authoritative totals and three target
        # summaries.  Decoding all 20 partner rows on every round used to cost
        # tens of seconds even when the 2x power rule immediately skipped
        # formation changes.  The detailed formation is loaded later only when
        # that rule actually needs it.
        runtime_facts = read_xianyuan_duel_runtime_snapshot(
            include_formations=False,
            self_power_hint=self_power_hint,
        )
        if (
            runtime_facts.get("available")
            and runtime_facts.get("complete")
            and len(runtime_facts.get("targets") or []) == 3
        ):
            self._log("detail", f"仙缘斗法：{reason} 已从游戏 Runtime 常驻模型取得当前事实")
            return runtime_facts

        del payload
        raise RuntimeError(
            "仙缘斗法：Runtime 当前事实不完整，等待模型加载；"
            f"reason={runtime_facts.get('reason') or 'runtime_incomplete'}"
        )

    def _wait_current_daily_xianyuan_duel_facts(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        reason: str,
        previous: dict[str, Any] | None = None,
        self_power_hint: int | float | None = None,
    ):
        timeout = max(0.0, float(payload.get("runtime_ready_timeout") or 45.0))
        poll_seconds = max(0.2, float(payload.get("runtime_ready_poll_seconds") or 2.0))
        started = time.monotonic()
        last_reason = "runtime_incomplete"
        while True:
            try:
                facts = self._read_current_daily_xianyuan_duel_facts(
                    payload,
                    reason=reason,
                    self_power_hint=self_power_hint,
                )
                if previous is None or xianyuan_duel_runtime_facts_advanced(previous, facts):
                    if self_power_hint is not None:
                        facts["self_power"] = self_power_hint
                    return facts
                last_reason = "动态事实尚未推进"
            except RuntimeError as exc:
                last_reason = str(exc)
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                return None
            self._log(
                "wait",
                f"仙缘斗法：{reason} 等待 Runtime {elapsed:.1f}/{timeout:.0f}s，{last_reason}",
            )
            yield from context.wait_action_settle(
                min(poll_seconds, max(0.2, timeout - elapsed))
            )

    def _defer_daily_xianyuan_duel_runtime(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        scheduler_task_id: str,
        reason: str,
    ):
        retry_seconds = max(60, int(payload.get("retry_seconds") or 60))
        next_time = (job_now() + timedelta(seconds=retry_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        self._log("skip", f"仙缘斗法：{reason}，{next_time} 安全复查")
        try:
            yield from context.go_scene(34)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            self._log("warning", f"仙缘斗法：已保存安全复查时间，但返回世界未完成：{exc}")
        return "skipped"

    def _map_daily_xianyuan_duel_targets(
        self,
        context: BehaviorTreeContext,
        facts: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _wait_scene_match = yield from context.wait_scene([308], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 308:
            raise RuntimeError(f"仙缘斗法：选人要求当前为 #308，实际 #{scene_id or 'unknown'} {score:.0f}%")
        ocr_names = [
            context.ocr_text_in_shapes(
                308,
                (f"姓名{slot}",),
                padding=int(payload.get("target_name_ocr_padding") or 4),
                frame_data_url=frame,
            )
            for slot in range(1, 4)
        ]
        targets: list[dict[str, Any]] = []
        for value in facts.get("targets") or []:
            item = dict(value)
            relation = classify_fanxiu_target_relation(
                is_npc=not bool(item.get("player")),
                server_id=item.get("server_id"),
            )
            item["camp"] = str(relation.get("camp") or "non_friendly")
            item["relation"] = str(relation.get("relation") or "")
            item["relation_label"] = str(relation.get("relation_label") or "未知关系")
            targets.append(item)
        mapped = map_xianyuan_duel_targets_to_slots(
            targets,
            ocr_names,
            minimum_pair_score=float(payload.get("target_name_min_similarity") or 0.35),
            minimum_assignment_margin=float(payload.get("target_name_min_margin") or 0.08),
        )
        if not mapped.get("ok"):
            raise RuntimeError(f"仙缘斗法：无法可靠映射候选姓名到 UI，{mapped.get('reason') or '匹配失败'}")
        return mapped

    def _read_daily_xianyuan_duel_remaining(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("remaining_ocr_attempts") or 6))
        padding = max(0, int(payload.get("remaining_ocr_padding") or 8))
        retry_seconds = max(0.2, float(payload.get("remaining_ocr_retry_seconds") or 1.0))
        max_remaining = max(0, int(payload.get("max_runs") or 7))
        last_text = ""
        for attempt in range(max_attempts):
            numbers, last_text = context.ocr_numbers_in_shapes(308, ("次数",), padding=padding, max_attempts=1)
            if not numbers:
                crop_numbers, crop_text = context.ocr_numbers_in_shapes(
                    308,
                    ("次数",),
                    padding=padding,
                    crop=True,
                    max_attempts=1,
                )
                if crop_numbers:
                    numbers, last_text = crop_numbers, crop_text
            if numbers and int(numbers[0]) >= 0:
                remaining = int(numbers[0])
                if remaining > max_remaining:
                    compact = re.sub(r"\s+", "", str(last_text or ""))
                    duplicated_digit = re.search(
                        r"剩余挑战次数[:：]?([0-9])\1\+?$",
                        compact,
                    )
                    if duplicated_digit and int(duplicated_digit.group(1)) <= max_remaining:
                        corrected = int(duplicated_digit.group(1))
                        self._log(
                            "warning",
                            "仙缘斗法：#308[次数] OCR 将加号误读成重复数字，"
                            f"{remaining}→{corrected}，OCR={last_text[:80]}",
                        )
                        remaining = corrected
                    else:
                        if attempt + 1 < max_attempts:
                            yield from context.wait_action_settle(0.8)
                            continue
                        raise RuntimeError(
                            "仙缘斗法：#308[次数] OCR 超出单周期安全上限，"
                            f"识别={remaining}、上限={max_remaining}、OCR={last_text[:120]}"
                        )
                self._log("detail", f"仙缘斗法：#308[次数] 剩余 {remaining}，OCR={last_text[:80]}")
                return remaining
            if attempt + 1 < max_attempts:
                yield from context.wait_action_settle(retry_seconds)
        raise RuntimeError(f"仙缘斗法：无法从 #308[次数] 识别剩余次数，最后 OCR={last_text[:120]}")

    def _open_daily_xianyuan_duel_purchase(
        self,
        context: BehaviorTreeContext,
        payload: dict[str, Any],
        *,
        reason: str,
    ):
        attempts = max(1, int(payload.get("purchase_open_attempts") or 2))
        timeout = float(payload.get("purchase_open_timeout") or 12.0)
        last_scene: tuple[int | None, float, str] | None = None
        for attempt in range(1, attempts + 1):
            wait_error: TimeoutError | None = None
            try:
                landing = yield from context.wait_click_then_scene(
                    308,
                    "购买",
                    311,
                    timeout=timeout,
                    max_clicks=1,
                )
            except TimeoutError as exc:
                wait_error = exc
            else:
                if int(getattr(landing, "id", landing) or 0) == 311:
                    return

            # wait_click_then_scene may return a newly recognized non-target
            # scene instead of raising. Re-sample before deciding whether the
            # purchase sheet opened; never treat underlying #308 as #311.
            _wait_scene_match = yield from context.wait_scene([311, 308], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_scene = (scene_id, score, context.ocr_text(frame))
            if scene_id == 311:
                return
            if scene_id != 308:
                if wait_error is not None:
                    raise wait_error
                raise RuntimeError(
                    f"仙缘斗法：{reason}后购买入口落到未知场景 "
                    f"#{scene_id or 'unknown'} {score:.0f}%"
                )
            if attempt >= attempts:
                text = last_scene[2]
                raise RuntimeError(
                    f"仙缘斗法：{reason}后 #308 购买入口仍未打开，"
                    "无法证明当日 100/200 灵石档已购。必须进入 #311 并确认"
                    "下一档价格为 300 灵石后才能幂等继续；"
                    f"当前 #308 {score:.0f}%，OCR={text[:80]}"
                ) from wait_error
            self._log(
                "warning",
                f"仙缘斗法：{reason}后等待 #311 未命中，但新帧仍确认 #308；"
                f"稳定等待后重试购买入口 {attempt + 1}/{attempts}",
            )
            yield from context.wait_action_settle(
                float(payload.get("purchase_open_retry_settle_seconds") or 1.0)
            )

    def _prepare_daily_xianyuan_duel_purchases(self, context: BehaviorTreeContext, payload: dict[str, Any]):
        yield from self._open_daily_xianyuan_duel_purchase(
            context,
            payload,
            reason="首次检查购买档位",
        )
        max_attempts = int(payload.get("purchase_max_attempts") or 6)
        stop_price = max(
            0,
            int(
                payload.get("purchase_max_price")
                or payload.get("purchase_price_limit")
                or 300
            ),
        )
        expected_purchase_prices = {100, 200}
        last_purchased_price: int | None = None
        for _index in range(max_attempts):
            for _retry in range(3):
                numbers, text = context.ocr_numbers_in_shapes(311, ("价格",), padding=16)
                if numbers:
                    break
                yield from context.wait_action_settle(0.8)
            if not numbers:
                continue
            price = numbers[0]
            if last_purchased_price is not None and price <= last_purchased_price:
                raise RuntimeError(
                    "仙缘斗法：购买动作后价格未向下一档推进，"
                    f"上一档 {last_purchased_price}，当前 {price}，拒绝重放灵石购买"
                )
            if price == stop_price:
                yield from context.wait_click_then_scene(311, "返回", 308)
                return
            if price not in expected_purchase_prices:
                raise RuntimeError(
                    "仙缘斗法：购买页价格不属于 100/200 可购档或 "
                    f"{stop_price} 灵石幂等停止档，当前价格 {price}"
                )
            self._log("action", f"仙缘斗法：购买斗法次数，价格 {price}")
            yield from context.wait_click(311, "购买")
            last_purchased_price = int(price)
            yield from context.wait_action_settle(1.0)
            _wait_scene_match = yield from context.wait_scene([311, 308], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 308:
                text = context.ocr_text(frame)
                self._log(
                    "detail",
                    "仙缘斗法：购买后入口关闭并返回 #308，"
                    f"重新打开购买页确认下一档为 {stop_price} 灵石；"
                    f"当前 #308 {score:.0f}%，OCR={text[:80]}",
                )
                yield from self._open_daily_xianyuan_duel_purchase(
                    context,
                    payload,
                    reason="购买后重新确认下一档",
                )
                continue
            if scene_id != 311:
                raise RuntimeError(
                    "仙缘斗法：购买后未停留在 #311 或返回 #308，"
                    f"实际 #{scene_id or 'unknown'} {score:.0f}%"
                )
        raise RuntimeError(f"仙缘斗法：购买页价格识别失败或未达到停止价格，最后识别文本：{text if 'text' in locals() else ''}")

    def _optimize_daily_xianyuan_duel_formation(self, context: BehaviorTreeContext, payload: dict[str, Any]):
        if bool(payload.get("skip_formation_optimize")):
            return
        facts = payload.get("__xianyuan_duel_facts")
        target = payload.get("__xianyuan_duel_target")
        if not isinstance(facts, dict) or not isinstance(target, dict):
            raise RuntimeError("仙缘斗法：缺少本轮结构化敌我事实，拒绝使用图片猜测阵容")
        self_power = int(facts.get("self_power") or 0)
        target_power = int(target.get("team_power") or 0)
        skip_ratio = float(payload.get("formation_skip_power_ratio") or 2.0)
        if target_power <= 0 or self_power <= 0:
            raise RuntimeError("仙缘斗法：敌我仙侣总战力不完整，无法执行 2 倍战力规则")
        power_ratio = self_power / target_power
        if power_ratio >= skip_ratio:
            self._log(
                "action",
                "仙缘斗法：我方仙侣战力 "
                f"{self_power} 为「{target.get('name') or '目标'}」{target_power} 的 {power_ratio:.2f} 倍，"
                f"达到 {skip_ratio:g} 倍，跳过阵容调整直接挑战",
            )
            return

        self_team = facts.get("self_team") if isinstance(facts.get("self_team"), dict) else {}
        enemy_team = target.get("team") if isinstance(target.get("team"), dict) else {}
        if not self_team.get("formation_complete") or not enemy_team.get("formation_complete"):
            detailed = read_xianyuan_duel_runtime_snapshot(include_formations=True)
            if not detailed.get("available") or not detailed.get("complete"):
                raise RuntimeError(
                    "仙缘斗法：摘要判断需要换阵，但完整 Runtime 阵容读取失败，"
                    f"reason={detailed.get('reason') or 'unknown'}"
                )
            target_id = target.get("target_id")
            detailed_target = next(
                (
                    item
                    for item in detailed.get("targets") or []
                    if isinstance(item, dict)
                    and item.get("target_id") == target_id
                ),
                None,
            )
            if not isinstance(detailed_target, dict):
                raise RuntimeError(
                    "仙缘斗法：完整 Runtime 阵容已刷新，无法按 target_id 对齐当前挑战对象"
                )
            if (
                str(detailed_target.get("name") or "") != str(target.get("name") or "")
                or int(detailed_target.get("team_power") or 0) != target_power
            ):
                raise RuntimeError(
                    "仙缘斗法：完整 Runtime 阵容与选人摘要不一致，拒绝使用跨版本阵容"
                )
            facts = detailed
            target = detailed_target
            self_team = facts.get("self_team") if isinstance(facts.get("self_team"), dict) else {}
            enemy_team = target.get("team") if isinstance(target.get("team"), dict) else {}

        start_ts = time.monotonic()
        _wait_scene_match = yield from context.wait_scene([309], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 309:
            raise RuntimeError(f"仙缘斗法：阵容优化要求当前为 #309，实际为 #{scene_id or 'unknown'} {score:.0f}%")
        if not self_team.get("formation_complete") or not enemy_team.get("formation_complete"):
            raise RuntimeError("仙缘斗法：敌我五个位置的结构化阵容不完整，拒绝退回 OCR 或图片相似匹配")
        my_partner_ids = [int(value) for value in self_team.get("partner_ids") or []]
        enemy_partner_ids = [int(value) for value in enemy_team.get("partner_ids") or []]
        try:
            best = best_xianyuan_partner_order(
                my_partner_ids,
                enemy_partner_ids,
                decay=float(payload.get("formation_decay") or 0.5),
            )
        except ValueError as exc:
            raise RuntimeError(f"仙缘斗法：结构化阵容无法计算，{exc}") from exc
        swaps = plan_swaps(my_partner_ids, best["partner_ids"])
        max_swaps = int(payload.get("formation_final_max_swaps") or 4)
        if len(swaps) > max_swaps:
            raise RuntimeError(f"仙缘斗法：结构化换位需要 {len(swaps)} 次，超过安全上限 {max_swaps}")
        settle_seconds = float(payload.get("formation_drag_settle_seconds") or 1.8)
        drag_duration = float(payload.get("formation_drag_duration_seconds") or 2.0)
        for start_slot, end_slot in swaps:
            context.drag_shape_to_shape(
                309,
                f"拖拽锚点{start_slot}",
                f"拖拽锚点{end_slot}",
                duration=drag_duration,
                frame_data_url=frame,
            )
            yield from context.wait_action_settle(settle_seconds)
        elapsed = time.monotonic() - start_ts
        my_labels = [XIANYUAN_CAREER_LABELS[int(value)] for value in best["careers"]]
        enemy_labels = [XIANYUAN_CAREER_LABELS[int(value)] for value in best["enemy_careers"]]
        self._log(
            "action",
            "仙缘斗法：按结构化阵容完成优化，"
            f"敌方={'/'.join(enemy_labels)}，我方={'/'.join(my_labels)}，"
            f"调整{len(swaps)}次，耗时{elapsed:.1f}s",
        )

    def _read_daily_xianyuan_duel_formation_state(self, context: BehaviorTreeContext) -> dict[str, Any]:
        from PIL import Image, ImageChops, ImageStat

        _wait_scene_match = yield from context.wait_scene([309], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 309:
            raise RuntimeError(f"仙缘斗法：阵容优化要求当前为 #309，实际为 #{scene_id or 'unknown'} {score:.0f}%")
        image309 = context.view(309).raw
        entry = context.ctx.get("entry") if isinstance(context.ctx, dict) else None
        entry_id = str(getattr(entry, "entry_id", "") or "")
        filename = str(image309.get("filename") or "")
        if not entry_id or not filename:
            raise RuntimeError("仙缘斗法：缺少 #309 参考图，无法识别阵容")
        ref_path = data_annotation_entry_image_dir(entry_id) / filename
        ref_image = Image.open(ref_path).convert("RGB")
        cur_image = Image.open(io.BytesIO(context.runner._decode_frame_data_url(frame))).convert("RGB")

        def crop(pil_image: Any, shape: dict[str, Any], *, pad: int = 1):
            width, height = pil_image.size
            x = float(shape.get("x") or 0) * width
            y = float(shape.get("y") or 0) * height
            w = float(shape.get("w") or 0) * width
            h = float(shape.get("h") or 0) * height
            return pil_image.crop((
                max(0, int(round(x - pad))),
                max(0, int(round(y - pad))),
                min(width, int(round(x + w + pad))),
                min(height, int(round(y + h + pad))),
            ))

        def similarity(left: Any, right: Any) -> float:
            right = right.resize(left.size)
            stat = ImageStat.Stat(ImageChops.difference(left, right))
            rmse = math.sqrt(sum(value * value for value in stat.rms) / len(stat.rms))
            return max(0.0, 100.0 * (1.0 - rmse / 255.0))

        career_shapes: list[tuple[int, str, dict[str, Any]]] = []
        for shape in image309.get("shapes") or []:
            if str(shape.get("title") or "") != "我方职业":
                continue
            for child in shape.get("children") or []:
                parsed = parse_slot_value_title(str(child.get("title") or ""), "职业")
                if parsed:
                    career_shapes.append((parsed[0], parsed[1], child))
        career_shapes.sort(key=lambda item: item[0])
        state_shapes: list[tuple[int, int, dict[str, Any]]] = []
        for shape in image309.get("shapes") or []:
            parsed = parse_slot_value_title(str(shape.get("title") or ""), "克制")
            if parsed:
                state_shapes.append((parsed[0], int(parsed[1]), shape))
        state_shapes.sort(key=lambda item: item[0])
        if len(career_shapes) != 5 or len(state_shapes) != 5:
            raise RuntimeError("仙缘斗法：#309 缺少职业或克制三态标注")

        career_templates: dict[str, list[Any]] = {}
        for _slot, career, shape in career_shapes:
            career_templates.setdefault(career, []).append(crop(ref_image, shape))
        state_templates: dict[int, list[Any]] = {}
        for _slot, state, shape in state_shapes:
            state_templates.setdefault(state, []).append(crop(ref_image, shape, pad=0))

        my_order: list[str] = []
        for _slot, _career, shape in career_shapes:
            slot_crop = crop(cur_image, shape)
            scores = {
                career: max(similarity(slot_crop, template) for template in templates)
                for career, templates in career_templates.items()
            }
            my_order.append(max(scores, key=scores.get))
        states: list[int] = []
        for _slot, _state, shape in state_shapes:
            slot_crop = crop(cur_image, shape, pad=0)
            scores = {
                state: max(similarity(slot_crop, template) for template in templates)
                for state, templates in state_templates.items()
            }
            states.append(int(max(scores, key=scores.get)))
        return {"frame": frame, "my_order": my_order, "states": states}

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

    def _daily_task_row_progress(
        self,
        lines: list[dict[str, Any]],
        title_y: float,
        *,
        y_tolerance: float = 130.0,
    ) -> tuple[int, int] | None:
        fragments: list[str] = []
        for line in lines:
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if abs(cy - title_y) > y_tolerance:
                continue
            text = _sanitize_ocr_text(line.get("text")).translate(FULLWIDTH_DIGIT_TRANSLATION)
            if text:
                fragments.append(text)
        row_text = "".join(fragments)
        fraction = parse_ocr_values(row_text, expected_count=2, allow_extra_numbers=True)
        if fraction is None:
            return None
        current_int, total_int = fraction
        current_text = str(current_int)
        if total_int > 0 and current_int > total_int and len(current_text) >= 2:
            suffix_int = int(current_text[-1])
            if suffix_int <= total_int:
                current_int = suffix_int
        return (current_int, total_int) if total_int > 0 else None

    def _daily_audit_task_identity(self, row_text: str) -> dict[str, str] | None:
        normalized = _sanitize_ocr_text(row_text)
        for task_type, task_id, pattern in _DAILY_AUDIT_TASK_PATTERNS:
            if re.search(pattern, normalized):
                return {"task_type": task_type, "task_id": task_id}
        return None

    def _daily_audit_normalize_title(self, row_text: str) -> str:
        text = _sanitize_ocr_text(row_text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        # Strip the bullet misread as O/0, preserving meaningful title digits.
        return re.sub(r"^[Oo0◎。·•●○\s]+", "", text).strip()

    def _daily_audit_row_done(
        self,
        *,
        task_type: str,
        current: int,
        total: int,
        row_text: str,
    ) -> bool:
        min_total = _DAILY_AUDIT_COMPLETION_MIN_TOTAL.get(task_type)
        if min_total is not None:
            return total >= min_total and current >= total
        return current >= total

    def _daily_audit_visible_rows(
        self,
        lines: list[dict[str, Any]],
        image69: dict[str, Any],
        *,
        y_tolerance: float = 150.0,
    ) -> list[dict[str, Any]]:
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法遍历日常列表")
        box = self._box(list_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)

        visible_lines: list[dict[str, Any]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text")).translate(FULLWIDTH_DIGIT_TRANSLATION)
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                next_line = dict(line)
                next_line["_text"] = text
                next_line["_cx"] = cx
                next_line["_cy"] = cy
                visible_lines.append(next_line)

        frame_width, _frame_height = self._frame_size(image69)
        scale = float(frame_width) / 900.0
        progress_rows: list[tuple[dict[str, Any], tuple[int, int]]] = []
        for line in visible_lines:
            text = str(line.get("_text") or "")
            # Independent fraction in the proven progress column. A title
            # such as '0完成双人修炼1次' is not a progress observation.
            fraction = re.fullmatch(r"[次活关]?\s*(\d+)\s*[/／丨|｜]\s*(\d+)", text)
            if fraction and 460 * scale <= float(line["_cx"]) <= 555 * scale:
                current, total = map(int, fraction.groups())
                if 0 <= current <= total and total > 0:
                    progress_rows.append((line, (current, total)))

        rows: list[dict[str, Any]] = []
        for progress_line, progress in sorted(progress_rows, key=lambda item: item[0]["_cy"]):
            center = float(progress_line["_cy"])
            title_lines = [
                line
                for line in visible_lines
                if -min(float(y_tolerance), 155.0) * scale <= float(line["_cy"]) - center <= -75 * scale
                and 395 * scale <= float(line.get("x") or 0) <= 550 * scale
                and float(line.get("y") or 0) >= top
                and not re.search(r"[:：！!]|功勋|榜单积分|经验加成|经验效率|触发|击杀.*修士", str(line["_text"]))
            ]
            if len(title_lines) != 1:
                continue
            title = self._daily_audit_normalize_title(str(title_lines[0]["_text"]))
            if not title or not re.search(r"[\u4e00-\u9fff]", title):
                continue
            row_text = title + " " + str(progress_line["_text"])
            current, total = progress
            identity = self._daily_audit_task_identity(title)
            task_type = (identity or {}).get("task_type") or ""
            rows.append({
                "title": title or row_text[:40],
                "text": row_text,
                "progress": {"current": current, "total": total},
                "done": self._daily_audit_row_done(task_type=task_type, current=current, total=total, row_text=row_text),
                "task_type": task_type,
                "task_id": (identity or {}).get("task_id") or "",
                "center_y": center,
                "row_complete": True,
            })
        return rows

    def _merge_daily_audit_rows(self, rows: list[dict[str, Any]], next_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        for row in [*rows, *next_rows]:
            key = self._daily_audit_normalize_title(str(row.get("title") or ""))
            if key and (key not in by_key or bool(row.get("row_complete"))):
                by_key[key] = row
        return list(by_key.values())

    def _record_daily_audit_result(self, audit: dict[str, Any]) -> None:
        facts = read_world_facts()
        discoveries = facts.setdefault("discoveries", {})
        if not isinstance(discoveries, dict):
            discoveries = {}
            facts["discoveries"] = discoveries
        discoveries["daily_audit"] = audit
        write_world_facts(facts)

    def _execute_daily_audit_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        image69 = (ctx.get("images") or {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法遍历日常列表")

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
                label="日常_复核",
            )
        if scene_id != 69:
            raise RuntimeError("日常_复核：未能进入 #69 日常页，无法读取次数")

        max_scrolls = self._payload_int(payload, "max_scrolls", default=30)

        view69 = context.view(69)
        list_shape = context.shape(69, "滚动窗口")
        rows: list[dict[str, Any]] = []
        reached_boundaries: dict[str, bool] = {}
        # Entry may retain any prior scroll offset. Collect toward both ends;
        # pixel similarity is not proof that sparse text rows stopped moving.
        for direction in ("up", "down"):
            previous_keys: set[str] = set()
            unchanged_count = 0
            reached_boundaries[direction] = False
            for index in range(max_scrolls + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked("running", f"日常_复核：读取日常列表 {direction} {index + 1}/{max_scrolls + 1}", phase="daily_audit_scan", current_scene=69)
                frame = context.cur_frame(update=True)
                lines = self._ocr_fragments_in_scene_shapes(ctx, frame, image69)
                self._ensure_daily_list_frame(ctx, frame, lines, task_label="日常_复核")
                visible_rows = self._daily_audit_visible_rows(lines, image69)
                rows = self._merge_daily_audit_rows(rows, visible_rows)
                keys = {
                    str(row.get("task_id") or row.get("task_type") or row.get("title") or "").strip()
                    for row in visible_rows
                } - {""}
                unchanged_count = unchanged_count + 1 if keys and keys == previous_keys else 0
                previous_keys = keys
                if unchanged_count >= 2:
                    reached_boundaries[direction] = True
                    break
                if index >= max_scrolls:
                    break
                # The next iteration must read the post-scroll frame even
                # when the visual change detector returns False.
                yield from context.scroll_shape_content(view69, list_shape, direction=direction)

        scan_complete = all(reached_boundaries.values())

        incomplete = [row for row in rows if not bool(row.get("done"))]
        completed = [row for row in rows if bool(row.get("done"))]
        mapped_incomplete = [row for row in incomplete if str(row.get("task_id") or "")]
        unmapped_incomplete = [row for row in incomplete if not str(row.get("task_id") or "")]
        mapped_completed = [row for row in completed if str(row.get("task_id") or "")]
        unmapped_completed = [row for row in completed if not str(row.get("task_id") or "")]
        audit = {
            "updated_at": time.time(),
            "updated_at_text": job_now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_scene": 69,
            "scan_complete": scan_complete,
            "scan_boundaries": reached_boundaries,
            "row_count": len(rows),
            "rows": rows,
            "incomplete": incomplete,
            "completed": completed,
            "mapped_incomplete": mapped_incomplete,
            "unmapped_incomplete": unmapped_incomplete,
            "mapped_completed": mapped_completed,
            "unmapped_completed": unmapped_completed,
            "incomplete_task_ids": [str(row.get("task_id") or "") for row in mapped_incomplete if str(row.get("task_id") or "")],
            "completed_task_ids": [str(row.get("task_id") or "") for row in mapped_completed if str(row.get("task_id") or "")],
            "message": f"日常页复核{'完整' if scan_complete else '未完整'}：读取 {len(rows)} 条，已完成 {len(completed)} 条，未完成 {len(incomplete)} 条，未完成已映射 {len(mapped_incomplete)} 条",
        }
        self._record_daily_audit_result(audit)
        if not scan_complete:
            raise RuntimeError(
                f"{audit['message']}；达到每方向 {max_scrolls} 次滚动上限，"
                f"未确认全部边界 {reached_boundaries}，已保留部分复核事实"
            )
        with self._lock:
            self._set_status_locked("success", audit["message"], phase="daily_audit_done", current_scene=69)
            self._log_locked("success", audit["message"])
        return "success"

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


    def _baiye_payload_target(self, payload: dict[str, Any]) -> str:
        args = payload.get("args")
        if isinstance(args, list) and args:
            text = str(args[0] or "").strip()
            if text:
                return text
        return str(payload.get("target") or payload.get("law") or "魔道").strip() or "魔道"

    def _baiye_text_is_rule_map(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text)
        return bool(("拜谒排行" in compact and ("大道" in compact or "跨法则" in compact)) or "跨法则" in compact)

    def _baiye_text_is_lord_map(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text)
        return bool(
            "法则之主" in compact
            and any(marker in compact for marker in ("可旋转", "进行拜谒", "魔道", "洗灵", "仙弈", "幻虚", "魔道"))
        )

    def _baiye_text_is_completed(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "已拜谒" in compact:
            return True
        match = re.search(r"剩余次数[:：]?(.*)", compact)
        fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True) if match else None
        return bool(fraction is not None and fraction[0] == 0 and fraction[1] > 0)

    def _baiye_text_can_worship(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "拜谒" not in compact:
            return False
        match = re.search(r"剩余次数[:：]?(.*)", compact)
        fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True) if match else None
        return bool(fraction is not None and fraction[0] > 0 and fraction[1] > 0)

    def _baiye_text_is_target_worship_page(self, text: Any, target: str) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        target_text = _sanitize_ocr_text(target).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(target_text and target_text in compact and self._baiye_text_can_worship(compact))

    def _baiye_detail_state(
        self,
        context: Any,
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ):
        """Read #266 through its formal scene and local Shapes only."""

        if isinstance(frame_data_url, str) and frame_data_url and not update:
            scene_id, _score, frame = context.recognize_scene_in_frame(
                [266], frame_data_url=frame_data_url
            )
        else:
            _wait_scene_match = yield from context.wait_scene([266], label='日常_拜谒：识别法则详情', wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        if scene_id != 266:
            return "absent", "", frame
        text = context.ocr_text_in_shapes(
            266,
            ["法则之主名称", "法则详情", "拜谒"],
            padding=8,
            frame_data_url=frame,
            # 全帧 OCR 会把横幅与低对比度的法则名称合并，按业务区域独立识别。
            crop=True,
        )
        if self._baiye_text_is_completed(text):
            return "completed", text, frame
        if self._baiye_text_can_worship(text):
            return "actionable", text, frame
        return "unknown", text, frame

    def _schedule_baiye_retry(self, payload: Mapping[str, Any], *, reason: str) -> str:
        retry_seconds = max(60, int(payload.get("baiye_lord_retry_seconds") or 300))
        next_time = (
            job_now() + timedelta(seconds=retry_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-baiye"),
            next_time,
        )
        self._log("warning", f"日常_拜谒：{reason}，于 {next_time} 重试")
        return next_time

    def _click_baiye_worship_button(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Iterator[Any]:
        self._log("action", f"日常_拜谒：{reason}，点击 #266「拜谒」")
        context.click_shape_center(266, "拜谒")
        timeout = max(2.0, float(payload.get("baiye_worship_confirm_timeout") or 20.0))
        poll_seconds = max(0.2, float(payload.get("baiye_worship_poll_seconds") or 1.0))
        state = "absent"
        worship_text = ""
        max_attempts = max(1, int(math.ceil(timeout / poll_seconds)))
        for attempt in range(1, max_attempts + 1):
            yield from context.wait_action_settle(poll_seconds)
            state, worship_text, _frame = yield from self._baiye_detail_state(
                context, update=True
            )
            if state == "completed":
                self._log("success", f"日常_拜谒：已完成拜谒，OCR={worship_text[:120]}")
                yield from self._finish_baiye_completed(
                    context,
                    payload,
                    reason="拜谒完成后收尾",
                )
                return "success"
            if attempt >= max_attempts:
                break
            self._log(
                "wait",
                f"日常_拜谒：等待拜谒完成态，state={state} OCR={worship_text[:80]}",
            )
        if state == "actionable":
            raise RuntimeError(
                f"日常_拜谒：点击 #266「拜谒」后 {timeout:.0f}s 仍未完成，OCR={worship_text[:120]}"
            )
        raise RuntimeError(
            f"日常_拜谒：点击 #266「拜谒」后 {timeout:.0f}s 未能确认完成；"
            f"禁止按未知状态返回 success，state={state} OCR={worship_text[:120]}"
        )

    def _finish_baiye_completed(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Iterator[Any]:
        """Persist proven completion before best-effort page cleanup."""

        if payload.get("__scheduler_task_id") and not payload.get("__baiye_completion_persisted"):
            self._record_daily_entry_done(
                payload,
                task_id="legacy-daily-baiye",
                task_type="daily_baiye",
                label="日常_拜谒",
                message="今日拜谒已确认完成",
            )
            payload["__baiye_completion_persisted"] = True
        try:
            yield from self._return_baiye_to_world(context, payload, reason=reason)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            self._log(
                "warning",
                f"日常_拜谒：今日拜谒已确认完成，收尾返回 #34 失败，"
                f"保留完成态并交由后续作业归一：{exc}",
            )
        return "success"

    def _return_baiye_to_world(
        self,
        context: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        reason: str,
    ) -> Iterator[Any]:
        settle_seconds = float((payload or {}).get("baiye_return_settle_seconds") or 1.0)
        self._log("action", f"日常_拜谒：{reason}，按拜谒页面栈返回 #34")
        for _attempt in range(8):
            _wait_scene_match = yield from context.wait_scene([266, 265, 264, 34], label='日常_拜谒：识别返回页面栈', wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 34:
                self._log("success", "日常_拜谒：已返回 #34 世界，闭环完成")
                return "success"
            if scene_id == 266:
                self._log("action", f"日常_拜谒：当前 #266 {score:.0f}%，点击「返回」回法则之主选择页")
                context.click_shape_center(266, "返回")
                yield from context.wait_action_settle(settle_seconds)
                continue
            if scene_id == 265:
                self._log("action", f"日常_拜谒：当前 #265 {score:.0f}%，点击「返回」回三千大道")
                context.click_shape_center(265, "返回")
                yield from context.wait_action_settle(settle_seconds)
                continue
            if scene_id == 264:
                self._log("action", f"日常_拜谒：当前 #264 {score:.0f}%，点击「返回」回世界")
                context.click_shape_center(264, "返回")
                yield from context.wait_action_settle(settle_seconds)
                continue
            self._log("warning", f"日常_拜谒：页面栈返回未识别到 #264/#265/#266/#34，当前 scene={scene_id}，回退通用 goto #34")
            yield from context.go_scene(34)
            yield from context.wait_action_settle(settle_seconds)
            _wait_scene_match = yield from context.wait_scene([34, 266, 265, 264], wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 34:
                self._log("success", "日常_拜谒：已通过通用 goto 返回 #34 世界，闭环完成")
                return "success"
            break
        raise RuntimeError(f"日常_拜谒：通用 goto 返回后未能确认 #34，当前 scene={scene_id}")

    def _execute_daily_baiye_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_拜谒资产树路径，无法执行作业")
        target = self._baiye_payload_target(payload)
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([266, 265, 264, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id == 266:
            result = yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target)
            if result == "success" and not payload.get("__baiye_completion_persisted"):
                self._record_daily_entry_done(
                    payload,
                    task_id="legacy-daily-baiye",
                    task_type="daily_baiye",
                    label="日常_拜谒",
                    message="今日拜谒已确认完成",
                )
            return result
        if scene_id != 265:
            if scene_id != 264:
                if scene_id != 69:
                    text = context.ocr_text(frame)
                    scene_id = yield from self._enter_daily_from_world_like(
                        ctx,
                        context,
                        stop_event,
                        frame,
                        scene_id,
                        text,
                        label="日常_拜谒",
                    )
                status = yield from self._open_daily_entry_from_daily(
                    ctx,
                    stop_event,
                    payload,
                    task_label="日常_拜谒",
                    title_pattern=r"拜\s*谒",
                    progress_can_mark_done=False,
                )
                if status == "done":
                    raise RuntimeError("日常_拜谒：日常列表进度不能作为拜谒完成证据")
                if status == "not_found":
                    self._record_daily_entry_not_found_retry(
                        payload,
                        task_id="legacy-daily-baiye",
                        task_type="daily_baiye",
                        label="日常_拜谒",
                        entry_label="拜谒",
                    )
                    return "skipped"
                yield from context.wait_any(
                    {"scene": context.scene_visible(264)},
                    timeout=20.0,
                    label="日常_拜谒：等待三千大道 #264",
                )
            yield from self._open_baiye_cross_rule(ctx, stop_event, payload, keyword=str(payload.get("cross_keyword") or "16"))
        result = yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target)
        if result == "success" and not payload.get("__baiye_completion_persisted"):
            self._record_daily_entry_done(
                payload,
                task_id="legacy-daily-baiye",
                task_type="daily_baiye",
                label="日常_拜谒",
                message="今日拜谒已确认完成",
            )
        return result

    def _execute_daily_green_bottle_baiye_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_绿瓶拜谒资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(20), dict):
            raise RuntimeError("缺少 #20「绿瓶」标注，无法进入绿瓶拜谒")
        rank_scene_id = 282
        if not isinstance(images.get(rank_scene_id), dict):
            raise RuntimeError("缺少 #282「掌天瓶」标注，无法点击境界排行")
        baiye_scene_id = 283
        if not isinstance(images.get(baiye_scene_id), dict):
            raise RuntimeError("缺少 #283「拜谒」标注，无法完成绿瓶拜谒")

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：前往绿瓶 #20", phase="daily_green_bottle_baiye_goto_20")
            self._log_locked("action", "日常_绿瓶拜谒：调用通用场景移动前往 #20")
        yield from context.go_scene(20)
        _wait_scene_match = yield from context.wait_scene([20], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id != 20:
            raise RuntimeError(f"日常_绿瓶拜谒：未能确认到达 #20，当前 scene={scene_id} score={score:.0f}%")
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：点击 #20「绿瓶」", phase="daily_green_bottle_baiye_click_bottle", current_scene=20)
            self._log_locked("success", f"日常_绿瓶拜谒：已到达 #20 {score:.0f}%")
            self._log_locked("action", "日常_绿瓶拜谒：点击 #20「绿瓶」")
        yield from context.wait_click(20, "绿瓶")
        yield from context.wait_action_settle(float(payload.get("green_bottle_settle_seconds") or 2.0))
        _wait_scene_match = yield from context.wait_scene([282, 301, 20], wait=5.0, required=False)
        (entry_scene_id, _entry_score, entry_frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if entry_scene_id == 301:
            entry_text = context.ocr_text(entry_frame)
            compact_entry_text = re.sub(r"\s+", "", _sanitize_ocr_text(entry_text))
            if "已达巅峰" in compact_entry_text:
                with self._lock:
                    self._log_locked("success", "日常_绿瓶拜谒：掌天瓶已达巅峰，今日绿瓶状态已确认")
                    self._log_locked("action", "日常_绿瓶拜谒：点击 #301「返回」退出掌天瓶详情")
                yield from context.wait_click(301, "返回")
                yield from context.wait_action_settle(float(payload.get("green_bottle_rank_back_settle_seconds") or 2.0))
                self._record_daily_entry_done(
                    payload,
                    task_id="legacy-daily-green-bottle-baiye",
                    task_type="daily_green_bottle_baiye",
                    label="日常_绿瓶拜谒",
                    message="掌天瓶已达巅峰，今日绿瓶状态已确认",
                )
                with self._lock:
                    self._set_status_locked("running", "日常_绿瓶拜谒：收尾回到世界 #34", phase="daily_green_bottle_baiye_return_world")
                    self._log_locked("action", "日常_绿瓶拜谒：调用通用场景移动回到 #34")
                final_scene_id, final_score = None, 0.0
                try:
                    yield from context.go_scene(34)
                    _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
                    (final_scene_id, final_score, _final_frame) = (
                        (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                        if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                    )
                except Exception as exc:
                    with self._lock:
                        self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，收尾返回 #34 失败：{exc}")
                if final_scene_id != 34:
                    with self._lock:
                        self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，但收尾未确认 #34，当前 scene={final_scene_id} score={final_score:.0f}%")
                with self._lock:
                    message = "日常_绿瓶拜谒完成，已回到 #34" if final_scene_id == 34 else "日常_绿瓶拜谒今日已完成，收尾场景待后续任务重新归一"
                    self._set_status_locked("success", message, phase="daily_green_bottle_baiye_done", current_scene=final_scene_id)
                    self._log_locked("success", "日常_绿瓶拜谒完成")
                return "success"
            raise RuntimeError(f"日常_绿瓶拜谒：进入 #301 但未识别为已达巅峰，OCR={entry_text[:120]}")
        with self._lock:
            self._set_status_locked("running", f"日常_绿瓶拜谒：点击 #{rank_scene_id}「境界排行」", phase="daily_green_bottle_baiye_click_rank", current_scene=rank_scene_id)
            self._log_locked("success", "日常_绿瓶拜谒：已点击 #20「绿瓶」")
            self._log_locked("action", f"日常_绿瓶拜谒：点击 #{rank_scene_id}「境界排行」")
        yield from context.wait_click(rank_scene_id, "境界排行")
        yield from context.wait_action_settle(float(payload.get("green_bottle_rank_settle_seconds") or 2.0))
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：点击 #283「拜谒」", phase="daily_green_bottle_baiye_click_baiye", current_scene=baiye_scene_id)
            self._log_locked("success", f"日常_绿瓶拜谒：已点击 #{rank_scene_id}「境界排行」")
            self._log_locked("action", "日常_绿瓶拜谒：确认天道魁首拜谒状态")
        _wait_scene_match = yield from context.wait_scene([baiye_scene_id], wait=5.0, required=False)
        (worship_scene_id, _worship_score, _worship_frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        remaining_numbers, remaining_text = yield from self._read_green_bottle_baiye_remaining(
            context,
            payload,
            scene_id=baiye_scene_id,
        )
        remaining = remaining_numbers[0]
        if remaining == 0:
            with self._lock:
                self._log_locked("success", f"日常_绿瓶拜谒：#283[剩余次数] 首个数值为 0，今日拜谒已完成，scene={worship_scene_id}")
        else:
            with self._lock:
                self._log_locked("action", f"日常_绿瓶拜谒：#283[剩余次数]={remaining}，点击 #283「拜谒」")
            context.click_shape_center(baiye_scene_id, "拜谒")
            yield from context.wait_action_settle(float(payload.get("green_bottle_baiye_settle_seconds") or 2.0))
            # A click is not completion: observe the receipt without clicking again.
            remaining_numbers, remaining_text = yield from self._read_green_bottle_baiye_remaining(
                context, payload, scene_id=baiye_scene_id, require_exhausted=True,
            )
        with self._lock:
            self._log_locked("success", "日常_绿瓶拜谒：今日拜谒已确认完成")
            self._set_status_locked("running", "日常_绿瓶拜谒：收尾回到世界 #34", phase="daily_green_bottle_baiye_return_world")
            self._log_locked("action", "日常_绿瓶拜谒：从当前场景调用通用场景移动回到 #34")
        self._record_daily_entry_done(
            payload,
            task_id="legacy-daily-green-bottle-baiye",
            task_type="daily_green_bottle_baiye",
            label="日常_绿瓶拜谒",
            message="今日拜谒已确认完成",
        )
        # 不在业务作业里手写 #283 -> #282 -> #20 -> #34。这里必须交给
        # 通用 goto；sceneJumpTarget 是可增量学习的历史落点频次，不是硬规则。
        final_scene_id, final_score = None, 0.0
        try:
            yield from context.go_scene(34)
            _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
            (final_scene_id, final_score, _final_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
        except Exception as exc:
            with self._lock:
                self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，收尾返回 #34 失败：{exc}")
        if final_scene_id != 34:
            with self._lock:
                self._log_locked("warning", f"日常_绿瓶拜谒：今日已完成，但收尾未确认 #34，当前 scene={final_scene_id} score={final_score:.0f}%")
        with self._lock:
            message = "日常_绿瓶拜谒完成，已回到 #34" if final_scene_id == 34 else "日常_绿瓶拜谒今日已完成，收尾场景待后续任务重新归一"
            self._set_status_locked("success", message, phase="daily_green_bottle_baiye_done", current_scene=final_scene_id)
            self._log_locked("success", "日常_绿瓶拜谒完成")
        return "success"

    def _read_green_bottle_baiye_remaining(
        self,
        context: Any,
        payload: dict[str, Any],
        *,
        scene_id: int = 283,
        require_exhausted: bool = False,
    ):
        """只读新帧复核次数；点击后必须有界等待已耗尽，绝不重复拜谒。"""

        max_attempts = max(1, int(payload.get("green_bottle_remaining_read_attempts") or 3))
        settle_seconds = max(0.1, float(payload.get("green_bottle_remaining_read_settle_seconds") or 0.8))
        timeout = max(2.0, float(payload.get("green_bottle_baiye_confirm_timeout") or 20.0))
        deadline = time.monotonic() + timeout
        last_text = ""
        attempt = 0
        while True:
            if require_exhausted:
                if time.monotonic() >= deadline:
                    break
            elif attempt >= max_attempts:
                break
            self._raise_if_stopped(context.stop_event)
            attempt += 1
            frame = context.cur_frame(update=True)
            observed_scene, _score, _frame = context.recognize_scene_in_frame(
                [scene_id], frame_data_url=frame,
            )
            last_text = (
                context.ocr_text_in_shapes(
                    scene_id, ["剩余次数"], padding=8,
                    frame_data_url=frame, crop=True,
                ) if observed_scene == scene_id else ""
            )
            numbers = parse_ocr_values(
                _sanitize_ocr_text(last_text).translate(FULLWIDTH_DIGIT_TRANSLATION)
            )
            if numbers is not None and (not require_exhausted or numbers[0] == 0):
                return numbers, last_text
            if require_exhausted or attempt < max_attempts:
                with self._lock:
                    self._log_locked(
                        "warning",
                        f"日常_绿瓶拜谒：#283[剩余次数] 第 {attempt} 帧未确认"
                        f"{'耗尽' if require_exhausted else '数值'}，OCR={last_text[:80]}，等待新帧复核",
                    )
                yield from context.wait_action_settle(settle_seconds)
        raise RuntimeError(
            f"日常_绿瓶拜谒：{attempt} 帧后未能从 #283[剩余次数] 确认"
            f"{'次数耗尽' if require_exhausted else '剩余次数'}，"
            f"最后 OCR={last_text[:80]}"
        )

    def _open_baiye_cross_rule(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        keyword: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        view264 = context.view(264)
        list_shape = context.shape(view264, "识别区")
        max_scrolls = self._payload_int(payload, "baiye_rule_max_scrolls", "max_scrolls", default=30)
        for direction, scroll_count in (("down", max_scrolls),):
            for scroll_index in range(max(0, int(scroll_count)) + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_拜谒：在 #264 查找包含 {keyword} 的法则 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_baiye_find_rule",
                        current_scene=264,
                    )
                frame = context.cur_frame(update=True)
                lines = context.ocr_fragments_in_shapes(264, ["识别区"], frame_data_url=frame) if hasattr(context, "ocr_fragments_in_shapes") else []
                tokens = context.ocr_tokens_in_shapes(264, ["识别区"], frame_data_url=frame)
                matches = [line for line in lines if keyword in _sanitize_ocr_text(line.get("text"))]
                if matches:
                    fragment = sorted(matches, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))[0]
                    parent_id = fragment.get("line_id")
                    line_tokens = [token for token in tokens if token.get("parent_line_id") == parent_id]
                    target_box = locate_text_box(line_tokens, keyword)
                    if target_box is None:
                        continue
                    x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
                    y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
                    text = _sanitize_ocr_text(fragment.get("text"))
                    self._log("action", f"日常_拜谒：点击 #264 OCR「{text}」")
                    context.click_frame_point(264, x, y)
                    yield from context.wait_any(
                        {"scene": context.scene_visible(265)},
                        timeout=20.0,
                        label="日常_拜谒：等待法则之主 #265",
                    )
                    return "open"
                if scroll_index >= int(scroll_count):
                    break
                self._log("action", f"日常_拜谒：#264 未找到 {keyword}，{direction} 滚动 {scroll_index + 1}")
                changed = yield from context.scroll_shape_content(view264, list_shape, direction=direction)
                if not changed:
                    break
        raise RuntimeError(f"日常_拜谒：#264 识别区未找到包含 {keyword} 的法则")

    def _baiye_target_box_from_tokens(
        self,
        tokens: list[dict[str, Any]],
        target: str,
        *,
        lines: list[dict[str, Any]] | None = None,
    ) -> dict[str, float] | None:
        if lines:
            for line in lines:
                text = _sanitize_ocr_text(line.get("text"))
                if "法则" in text or target not in text:
                    continue
                parent_id = line.get("line_id")
                line_tokens = [token for token in tokens if token.get("parent_line_id") == parent_id]
                target_box = locate_text_box(line_tokens, target)
                if target_box is not None:
                    return target_box
            return None
        # Observable legacy degradation: locate_text_box never crosses an
        # explicit parent_line_id, and unlinked tokens are accepted only for a
        # caller-provided local ROI.
        return locate_text_box(tokens, target)

    def _baiye_lord_click_point_from_box(
        self,
        box: Mapping[str, Any],
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[float, float]:
        options = payload or {}
        x = float(box.get("x") or 0)
        y = float(box.get("y") or 0)
        w = max(1.0, float(box.get("w") or 0))
        h = max(1.0, float(box.get("h") or 0))
        x_ratio = float(options.get("baiye_lord_icon_x_ratio") or 0.5)
        y_offset_ratio = float(options.get("baiye_lord_icon_y_offset_ratio") or 1.35)
        click_x = x + w * max(0.0, min(1.0, x_ratio))
        click_y = y - h * max(0.0, y_offset_ratio)
        return click_x, click_y

    def _select_baiye_law_lord(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        target: str,
    ) -> str:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        timeout = float(payload.get("baiye_lord_timeout") or 120.0)
        scene_recovery_timeout = float(payload.get("baiye_scene_recovery_timeout") or 8.0)
        poll_seconds = float(payload.get("baiye_lord_poll_seconds") or 0.75)
        start = time.monotonic()
        last_text = ""
        unrecognized_since: float | None = None
        while True:
            self._raise_if_stopped(stop_event)
            elapsed = time.monotonic() - start
            if elapsed >= timeout:
                next_time = self._schedule_baiye_retry(
                    payload,
                    reason=f"{timeout:.0f}s 未找到「{target}」",
                )
                self._log(
                    "warning",
                    f"日常_拜谒：{timeout:.0f}s 未找到「{target}」，"
                    f"点击返回并于 {next_time} 重试",
                )
                context.click_shape_center(265, "返回")
                yield from context.wait_any(
                    {"scene": context.scene_visible(264)},
                    timeout=20.0,
                    label="日常_拜谒：等待返回 #264",
                )
                return "skipped"
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_拜谒：在 #265 查找「{target}」",
                    phase="daily_baiye_find_lord",
                    current_scene=265,
                )
            _wait_scene_match = yield from context.wait_scene([266, 265], label='日常_拜谒：识别法则之主列表或详情', wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            detail_state, detail_text, _detail_frame = yield from self._baiye_detail_state(
                context,
                frame_data_url=frame,
            )
            if detail_state == "completed":
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_拜谒：当前已在法则详情完成态",
                        phase="daily_baiye_detail_done",
                        current_scene=266,
                    )
                self._log("success", f"日常_拜谒：当前已是完成态，OCR={detail_text[:120]}")
                yield from self._finish_baiye_completed(
                    context,
                    payload,
                    reason="检测到已完成态后收尾",
                )
                return "success"
            if detail_state == "actionable":
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_拜谒：当前已在法则详情可拜谒态",
                        phase="daily_baiye_detail_worship",
                        current_scene=266,
                    )
                if not self._baiye_text_is_target_worship_page(detail_text, target):
                    self._schedule_baiye_retry(
                        payload,
                        reason=f"#266 可拜谒但未确认目标「{target}」，禁止点击",
                    )
                    return "skipped"
                reason = f"当前已在「{target}」详情页"
                return (yield from self._click_baiye_worship_button(context, payload, reason=reason))
            if detail_state == "unknown":
                self._schedule_baiye_retry(
                    payload,
                    reason=f"#266 状态不确定，禁止返回 success，OCR={detail_text[:80]}",
                )
                return "skipped"

            if scene_id != 265:
                if unrecognized_since is None:
                    unrecognized_since = elapsed
                missing_seconds = elapsed - unrecognized_since
                if missing_seconds >= scene_recovery_timeout:
                    self._schedule_baiye_retry(
                        payload,
                        reason=(
                            f"连续 {missing_seconds:.1f}s 未识别到 #265/#266，"
                            f"禁止返回 success，scene={scene_id}"
                        ),
                    )
                    return "skipped"
                self._log(
                    "wait",
                    f"日常_拜谒：进入 #265 后画面暂未稳定，继续观察，scene={scene_id}",
                )
                yield from context.wait_action_settle(poll_seconds)
                continue
            unrecognized_since = None
            ocr_options = {
                "text_det_thresh": float(payload.get("baiye_text_det_thresh") or 0.25),
                "text_det_box_thresh": float(payload.get("baiye_text_det_box_thresh") or 0.45),
                "text_det_unclip_ratio": float(payload.get("baiye_text_det_unclip_ratio") or 1.2),
            }
            lines = (
                context.ocr_fragments_in_shapes(
                    265,
                    ["识别区"],
                    frame_data_url=frame,
                    options=ocr_options,
                )
                if hasattr(context, "ocr_fragments_in_shapes")
                else []
            )
            tokens = context.ocr_tokens_in_shapes(
                265,
                ["识别区"],
                frame_data_url=frame,
                options=ocr_options,
            )
            target_box = self._baiye_target_box_from_tokens(tokens, target, lines=lines)
            source_text = "".join(_sanitize_ocr_text(token.get("text")) for token in tokens)
            last_text = source_text or last_text
            if target_box is not None:
                click_x, click_y = self._baiye_lord_click_point_from_box(target_box, payload)
                self._log(
                    "action",
                    f"日常_拜谒：OCR 命中「{target}」({source_text[:40]})，词框=({float(target_box.get('x') or 0):.1f},"
                    f"{float(target_box.get('y') or 0):.1f},{float(target_box.get('w') or 0):.1f},"
                    f"{float(target_box.get('h') or 0):.1f})，点击图标估算点 ({click_x:.1f},{click_y:.1f})",
                )
                if bool(payload.get("baiye_lord_probe_only") or payload.get("probe_only")):
                    self._log("success", f"日常_拜谒：probe 已在 #265 OCR 命中「{target}」，未点击选择目标")
                    if bool(payload.get("baiye_lord_probe_return", True)):
                        context.click_shape_center(265, "返回")
                        yield from context.wait_any(
                            {"scene": context.scene_visible(264)},
                            timeout=20.0,
                            label="日常_拜谒：probe 等待返回 #264",
                        )
                    return "skipped"
                context.click_frame_point(265, click_x, click_y)
                yield from context.wait_action_settle(1.0)
                try:
                    yield from context.wait_scene(
                        [266],
                        wait=float(payload.get("baiye_detail_wait_seconds") or 12.0),
                        label=f"日常_拜谒：等待「{target}」详情 #266",
                    )
                except TimeoutError:
                    self._schedule_baiye_retry(
                        payload,
                        reason=f"点击「{target}」后未在时限内识别到 #266",
                    )
                    return "skipped"
                after_state, after_text, _after_frame = yield from self._baiye_detail_state(
                    context, update=True
                )
                if after_state == "completed":
                    self._log("success", f"日常_拜谒：已点击「{target}」，完成态 OCR={after_text[:120]}")
                    yield from self._finish_baiye_completed(
                        context,
                        payload,
                        reason=f"已点击「{target}」进入完成态后收尾",
                    )
                    return "success"
                if after_state == "actionable":
                    if not self._baiye_text_is_target_worship_page(after_text, target):
                        self._schedule_baiye_retry(
                            payload,
                            reason=f"选择「{target}」后详情页目标不确定，禁止点击拜谒",
                        )
                        return "skipped"
                    return (yield from self._click_baiye_worship_button(context, payload, reason=f"已选中「{target}」且显示可拜谒"))
                self._schedule_baiye_retry(
                    payload,
                    reason=f"点击「{target}」后未确认 #266 完成/可拜谒态，state={after_state}",
                )
                return "skipped"
            self._log("detail", f"日常_拜谒：暂未命中「{target}」，OCR={last_text[:80]}")
            yield from context.wait_action_settle(poll_seconds)

    def _daily_youli_current_state(self, context: Any, *, update: bool = False) -> tuple[int | None, float, str, str]:
        _wait_scene_match = yield from context.wait_scene([237, 236, 233, 229, 228, 71, 69, 34], wait=5.0, required=False)
        (scene_id, score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        if scene_id is None:
            scene_id, score, frame = context.recognize_scene_in_frame(frame_data_url=frame)
        return scene_id, float(score), frame, context.ocr_text(frame)

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
