from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import threading
import time
from datetime import datetime, time as time_cls, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    find_text_matches,
    group_ocr_tokens,
    locate_text_box,
    query_spatial_ocr,
)
from backend.core.fanxiu.runtime_gui import (
    DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
    normalize_ocr_name,
    ocr_name_similarity,
    rank_ocr_name_matches,
)
from backend.core.fanxiu.data_annotation.duel_strategy import (
    XIANYUAN_CAREER_LABELS,
    best_xianyuan_partner_order,
    best_order_for_enemy_candidates,
    infer_enemy_candidate_order,
    parse_slot_value_title,
    plan_swaps,
)
from backend.core.fanxiu.data_annotation import behavior_tree_runtime as _behavior_tree_runtime
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
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.behavior_tree_runtime import (
    DEFAULT_SCROLL_UNCHANGED_THRESHOLD,
    FULLWIDTH_DIGIT_TRANSLATION,
    _now,
    _parse_daily_boss_hp_percent,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_cd_seconds_from_six_digits,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
    _read_data_annotation_scheduler_tasks,
    _read_data_annotation_world_facts,
    _write_data_annotation_world_facts,
)
from backend.core.fanxiu.data_annotation.tasks.scene_candidates import (
    DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS,
    DAILY_XIANYUAN_LAYER0_SCENE_IDS,
)


from backend.core.fanxiu.data_annotation.tasks.lundao import (
    LUNDAO_CLOSE_TIME,
    LUNDAO_DALUO_ROOM_ID,
    LUNDAO_FIRST_TRIGGER,
    LUNDAO_SANQING_ROOM_ID,
    current_lundao_player_profile,
    evaluate_lundao_room_opportunity,
    lundao_player_profile_from_runtime,
    lundao_purchase_allowed,
    lundao_safety_threshold,
    next_lundao_daily_trigger,
    next_lundao_recheck,
    next_lundao_unseated_retry,
    plan_lundao_strategy,
    refresh_and_select_lundao_kick_target,
)
from backend.core.fanxiu.data_annotation.tasks.lingmai import (
    LINGMAI_SHENGMAI_MIN_STRENGTH,
    LINGMAI_UNION_SHENGMAI_ROOM_ID,
    LINGMAI_UNION_SHENMAI_ROOM_ID,
    lingmai_facts_retry_seconds,
    refresh_and_select_lingmai_seat_action,
    refresh_lingmai_daily_status,
)
from backend.core.fanxiu.data_annotation.tasks.xianyuan_duel import (
    choose_xianyuan_duel_target,
    map_xianyuan_duel_targets_to_slots,
)
from backend.core.fanxiu.catalog.server_relations import classify_fanxiu_target_relation
from backend.core.fanxiu.data_annotation.state import (
    parse_data_annotation_daily_clock,
    parse_data_annotation_task_time,
)


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


class _DailyLingmaiKickTargetLost(RuntimeError):
    """Raised when a Runtime-selected Lingmai target cannot be trusted in #286 GUI OCR."""


class _DailyMojieRaidAttackCountdown(RuntimeError):
    def __init__(self, seconds: int, text: str) -> None:
        super().__init__(text)
        self.seconds = int(seconds)
        self.text = str(text)


def _lingmai_name_variants(value: Any) -> list[str]:
    name = _sanitize_ocr_text(value)
    return list(dict.fromkeys(
        variant
        for variant in [
            name,
            *(part.strip() for part in re.split(r"[|｜]+", name)),
        ]
        if len(normalize_ocr_name(variant)) >= 2
    ))


def select_visible_lingmai_target(
    eligible_targets: list[dict[str, Any]],
    visible_text: str,
    *,
    threshold: float = DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
) -> dict[str, Any] | None:
    """Return the weakest Runtime-authorized target still visible in the GUI."""

    for target in eligible_targets:
        variants = _lingmai_name_variants(target.get("name"))
        if variants and max(ocr_name_similarity(item, visible_text) for item in variants) >= threshold:
            return target
    return None


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
)

_DAILY_AUDIT_COMPLETION_MIN_TOTAL: dict[str, int] = {
    "daily_dungeon": 6,
}

_DAILY_LINGMAI_ENTRY_PATTERN = (
    r"参\s*与?.*灵\s*脉.*(?:争|夺).*?(?:1|一)?.*?(?:小\s*时|时)?|"
    r"灵\s*脉.*(?:争|夺)|灵\s*脉"
)


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


def read_daily_task_runtime_snapshot(task_id: int) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.daily_task import read_daily_task_snapshot

    return read_daily_task_snapshot(task_id)


# “进入论道”最终要收敛到四种互斥的稳定业务状态。这里只记录用户已经
# 确认的正式场景；未知编号保持空元组，避免用猜测制造虚假路由。
_DAILY_LUNDAO_STABLE_STATE_SCENE_IDS: dict[str, tuple[int, ...]] = {
    "ready": (),
    "in_progress": (304,),
    "kicked": (391,),
    "completed": (),
}
_DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS: tuple[int, ...] = (296, 304, 391)


def _lundao_waited_scene_id(value: Any) -> int | None:
    if isinstance(value, View):
        return int(value.id) if value.id is not None else None
    if hasattr(value, "id"):
        value = getattr(value, "id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None

_DONGTIAN_PLACE_LEVELS: tuple[dict[str, Any], ...] = (
    {"level": 1, "prefix": "", "places": ("白玉京",)},
    {"level": 2, "prefix": "", "places": ("大罗天墟", "太明玉墟")},
    {
        "level": 3,
        "prefix": "[洞天]",
        "places": ("紫琅阕", "璇霄崖", "云天柱", "青冥台", "月虹梁", "星岩廊"),
    },
    {
        "level": 4,
        "prefix": "[福地]",
        "places": (
            "蛰龙窟",
            "琅霜涧",
            "镇岳台",
            "坠星滩",
            "莲舟矾",
            "芝云巢",
            "八色圃",
            "晦明渡",
            "月胎穴",
            "沉剑津",
            "朽龙骨",
            "太素窟",
            "紫庭山",
            "月虹窟",
            "劫波礁",
            "焚轮井",
            "幽阳泉",
            "罡煞渊",
            "玉兵冢",
            "霞金脉",
            "蟠龙窟",
            "青烟崖",
            "天鬼廊",
            "赤鼎洞",
            "天符狱",
            "斗罡峡",
            "五光坛",
            "巡天阁",
            "天罗门",
            "盖竹山",
        ),
    },
)

_DONGTIAN_PLACE_ANCHORS: tuple[str, ...] = tuple(
    f"{level['prefix']}{place}" if level["prefix"] else place
    for level in _DONGTIAN_PLACE_LEVELS
    for place in level["places"]
)


class DailyFoundationTaskMixin:
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
        now = _behavior_tree_runtime._now()
        next_at = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
        return next_at.strftime("%Y-%m-%d %H:%M:%S")

    def daily_activity_flow(self, runtime: Any):
        yield from runtime.go_scene(69)

        attempt = 0
        while True:
            attempt += 1
            frame = runtime.cur_frame(update=True)
            scene_id, score, _frame = runtime.current_scene([69], frame_data_url=frame)
            if scene_id != 69:
                raise RuntimeError(f"日常_活跃度：读取总活跃度时已不在 #69：#{scene_id or 'unknown'} {score:.0f}%")

            values, text = runtime.ocr_numbers_in_shapes(
                69,
                ["总活跃度"],
                padding=0,
                frame_data_url=frame,
            )
            if values:
                total_activity = int(values[0])
                self._log("detail", f"日常_活跃度：第 {attempt} 次读取总活跃度={total_activity}，OCR={text!r}")
                break

            self._log("detail", f"日常_活跃度：第 {attempt} 次未读到总活跃度数值，OCR={text!r}，继续识别")
            yield from runtime.wait_action_settle(0.8)

        if total_activity < 500:
            yield from runtime.go_scene(34)
            runtime.set_next_time(
                (_behavior_tree_runtime._now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            )
            return {
                "result": "success",
                "message": f"日常_活跃度：总活跃度 {total_activity} < 500，已回到世界，1 小时后重试",
                "current_scene": 34,
            }

        runtime.click_shape_center(69, "奖励")
        yield from runtime.wait_action_settle(1.5)
        yield from runtime.go_scene(34)
        runtime.set_next_time(self._next_daily_activity_time_text())
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
        activity_completed = False

        def tracked_flow(runtime: Any):
            nonlocal activity_completed
            flow_result = yield from self.daily_activity_flow(runtime)
            activity_completed = (
                isinstance(flow_result, dict)
                and str(flow_result.get("result") or "success") == "success"
            )
            return flow_result

        result = yield from self._execute_daily_runtime_task(
            ctx,
            stop_event,
            normalized_payload,
            task_type="daily_activity",
            label="日常_活跃度",
            flow=tracked_flow,
        )
        if activity_completed:
            self._trigger_daily_experience_after_prerequisites(
                completed_task_type="daily_activity",
                completed_at=_behavior_tree_runtime._now(),
            )
        return result

    def _next_weekly_activity_time_text(
        self,
        *,
        completed: bool,
        now: datetime | None = None,
    ) -> str:
        current = now or _behavior_tree_runtime._now()
        if not completed and current.weekday() in {3, 4}:
            next_at = current + timedelta(days=1)
        else:
            days_until_thursday = (3 - current.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
            next_at = current + timedelta(days=days_until_thursday)
        return next_at.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def weekly_activity_flow(self, runtime: Any):
        yield from runtime.go_scene(69)
        runtime.click_shape_center(69, "周常")
        yield from runtime.wait_action_settle(float(runtime.payload.get("weekly_tab_settle_seconds") or 1.5))

        attempt = 0
        while True:
            attempt += 1
            frame = runtime.cur_frame(update=True)
            scene_id, score, _frame = runtime.current_scene([402], frame_data_url=frame)
            if scene_id != 402:
                self._log(
                    "detail",
                    f"周常_活跃度：第 {attempt} 次尚未识别到 #402：#{scene_id or 'unknown'} {score:.0f}%，继续识别",
                )
                yield from runtime.wait_action_settle(0.8)
                continue

            values, text = runtime.ocr_numbers_in_shapes(
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
            yield from runtime.wait_action_settle(0.8)

        threshold = max(1, int(runtime.payload.get("weekly_activity_threshold") or 2400))
        now = _behavior_tree_runtime._now()
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
            yield from runtime.go_scene(34)
            runtime.set_next_time(next_time)
            return {
                "result": "success",
                "message": message,
                "current_scene": 34,
            }

        reward_layout = weekly_activity_reward_layout_from_ocr(
            runtime.full_frame_ocr_tokens(frame),
            frame_width=900,
            frame_height=1600,
        )
        if total_activity not in reward_layout:
            raise RuntimeError(
                f"周常_活跃度：当前总活跃度 {total_activity} 未出现在可见奖励轨道 "
                f"{list(reward_layout)}，拒绝点击"
            )
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
            invisible_claimable = sorted(claimable - set(states))
            if invisible_claimable:
                raise RuntimeError(
                    f"周常_活跃度：Runtime 可领档 {invisible_claimable} 不在当前可见轨道，拒绝猜滑动"
                )
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
        claimable_thresholds = [int(value) for value in runtime_snapshot.get("claimable_thresholds") or []]

        claimed_now: list[int] = []
        for milestone in claimable_thresholds:
            before = reward_states[milestone]
            click_x, click_y = before["point"]
            runtime.click_frame_point(402, click_x, click_y)
            yield from runtime.wait_action_settle(float(runtime.payload.get("reward_settle_seconds") or 1.5))
            after_frame = runtime.cur_frame(update=True)
            after_scene_id, after_score, _ = runtime.current_scene([402], frame_data_url=after_frame)
            if after_scene_id != 402:
                raise RuntimeError(
                    f"周常_活跃度：点击 {milestone} 档后未留在 #402："
                    f"#{after_scene_id or 'unknown'} {after_score:.0f}%"
                )
            after_layout = weekly_activity_reward_layout_from_ocr(
                runtime.full_frame_ocr_tokens(after_frame),
                frame_width=900,
                frame_height=1600,
            )
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
        remaining_claimable = [int(value) for value in runtime_snapshot.get("claimable_thresholds") or []]
        if remaining_claimable:
            raise RuntimeError(f"周常_活跃度：领取后仍有 Runtime 可领档：{remaining_claimable}")
        if reward_states.get(total_activity, {}).get("state") != "claimed":
            raise RuntimeError(f"周常_活跃度：右边界 {total_activity} 档未显示绿色勾")
        final_frame = after_frame if claimed_now else frame
        final_tokens = runtime.full_frame_ocr_tokens(final_frame)
        if weekly_activity_pending_badge_present(
            final_tokens,
            frame_width=900,
            frame_height=1600,
        ):
            raise RuntimeError("周常_活跃度：Runtime 无可领档但周常页签仍显示“领”，拒绝写入下周")
        yield from runtime.go_scene(34)
        next_time = self._next_weekly_activity_time_text(completed=True, now=now)
        runtime.set_next_time(next_time)
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
        return (yield from self._execute_daily_runtime_task(
            ctx,
            stop_event,
            payload,
            task_type="weekly_activity",
            label="周常_活跃度",
            flow=self.weekly_activity_flow,
        ))

    def _execute_daily_boss_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        task_payload = dict(payload or {})
        result = yield from self._execute_daily_boss_task_flow(
            ctx,
            stop_event,
            task_payload,
        )
        if result == "success":
            self._trigger_daily_experience_after_prerequisites(
                completed_task_type="daily_boss",
                completed_at=_behavior_tree_runtime._now(),
            )
        return result

    @staticmethod
    def _parse_scheduler_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @classmethod
    def _daily_prerequisite_completion_at(
        cls,
        task: Mapping[str, Any] | None,
        *,
        task_type: str,
        cycle_now: datetime,
    ) -> datetime | None:
        """Return a same-cycle business completion, proven by Job-owned next_time."""

        if not isinstance(task, Mapping) or str(task.get("last_result") or "") != "success":
            return None
        finished_at = cls._parse_scheduler_datetime(task.get("finished_at"))
        next_time = cls._parse_scheduler_datetime(task.get("next_time"))
        if finished_at is None or next_time is None:
            return None

        cycle_start = cycle_now.replace(hour=5, minute=0, second=0, microsecond=0)
        if cycle_now < cycle_start:
            cycle_start -= timedelta(days=1)
        cycle_end = cycle_start + timedelta(days=1)
        if not cycle_start <= finished_at < cycle_end:
            return None

        if task_type == "daily_boss":
            expected_next_time = finished_at.replace(hour=5, minute=0, second=0, microsecond=0)
            if expected_next_time <= finished_at:
                expected_next_time += timedelta(days=1)
        elif task_type == "daily_activity":
            expected_next_time = (finished_at + timedelta(days=1)).replace(
                hour=7,
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            return None
        return finished_at if next_time == expected_next_time else None

    def _trigger_daily_experience_after_prerequisites(
        self,
        *,
        completed_task_type: str,
        completed_at: datetime,
    ) -> str | None:
        """Trigger experience only after boss and activity both completed this game day."""

        tasks = _read_data_annotation_scheduler_tasks()
        tasks_by_type = {
            str(task.get("task_type") or ""): task
            for task in tasks
            if isinstance(task, dict)
        }
        completion_times: dict[str, datetime | None] = {}
        for task_type in ("daily_boss", "daily_activity"):
            if task_type == completed_task_type:
                completion_times[task_type] = completed_at
            else:
                completion_times[task_type] = self._daily_prerequisite_completion_at(
                    tasks_by_type.get(task_type),
                    task_type=task_type,
                    cycle_now=completed_at,
                )

        missing = [
            "日常_首领" if task_type == "daily_boss" else "日常_活跃度"
            for task_type, completion_at in completion_times.items()
            if completion_at is None
        ]
        source_label = "日常_首领" if completed_task_type == "daily_boss" else "日常_活跃度"
        if missing:
            self._log("detail", f"{source_label}：日常_经验等待{'、'.join(missing)}完成")
            return None

        latest_prerequisite = max(value for value in completion_times.values() if value is not None)
        experience_task = tasks_by_type.get("daily_experience")
        experience_finished_at = self._parse_scheduler_datetime(
            experience_task.get("finished_at") if isinstance(experience_task, Mapping) else None
        )
        if (
            isinstance(experience_task, Mapping)
            and str(experience_task.get("last_result") or "") == "success"
            and experience_finished_at is not None
            and experience_finished_at >= latest_prerequisite
        ):
            self._log("detail", f"{source_label}：日常_经验已在两项前置完成后执行，本轮不重复触发")
            return None

        trigger_time = _behavior_tree_runtime.set_data_annotation_scheduler_task_trigger_time(
            "日常_经验",
            completed_at,
        )
        self._log(
            "success",
            f"{source_label}：日常_首领与日常_活跃度均已完成，已设置日常_经验触发时间 {trigger_time}",
        )
        return trigger_time

    def _execute_daily_boss_task_flow(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_首领资产树路径，无法执行作业")

        # ``BossMgr.Model.BossData`` is the authoritative same-process fact for
        # today's remaining reward count.  Check it before paying the #69 list
        # navigation cost: a complete zero is an idempotent business terminal,
        # while unavailable/incomplete/non-zero snapshots still follow the
        # original GUI flow and its fail-closed guards.
        preflight_snapshot = self._daily_boss_runtime_snapshot(payload)
        if (
            preflight_snapshot.get("complete") is True
            and preflight_snapshot.get("list_loaded") is True
        ):
            # The remaining count cannot change before this task starts a
            # challenge.  Consume this snapshot once after reaching #178 so a
            # non-zero preflight does not immediately repeat the same Runtime
            # read.  Post-challenge probes never reuse it.
            payload["_daily_boss_preflight_snapshot"] = dict(preflight_snapshot)
        if preflight_snapshot.get("complete") is True:
            try:
                preflight_remaining = int(preflight_snapshot.get("reward_remaining"))
            except (TypeError, ValueError):
                preflight_remaining = None
            if preflight_remaining == 0:
                next_time = self._record_daily_boss_done_for_today(payload)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_首领：只读 Runtime 已确认今日剩余奖励次数为 0，"
                        f"跳过日常列表；下次 {next_time}",
                        phase="daily_boss_done_runtime_preflight",
                    )
                    self._log_locked("success", self._status["message"])
                yield from self._return_daily_boss_to_world(ctx, stop_event)
                return "success"

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, _frame = runtime.current_scene(update=True)
        current_text = runtime.ocr_text(_frame)
        if (yield from self._close_daily_boss_item_detail_if_present(ctx, runtime, stop_event, _frame, current_text)):
            scene_id, _score, _frame = runtime.current_scene(update=True)
            current_text = runtime.ocr_text(_frame)
        if (yield from self._close_daily_boss_storage_bag_if_present(ctx, runtime, stop_event, _frame, current_text)):
            scene_id, _score, _frame = runtime.current_scene(update=True)
            current_text = runtime.ocr_text(_frame)
        if self._daily_boss_done_text(current_text):
            return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
        if self._daily_boss_combat_in_progress_text(current_text):
            return (yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload))
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if scene_id == 180:
                return (yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload))
            if scene_id == 181:
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
        elif self._daily_boss_text_is_detail(current_text):
            scene_id = 179
        elif self._daily_boss_text_is_list(current_text):
            scene_id = 178

        if scene_id != 179:
            if scene_id != 178:
                if scene_id != 69:
                    world_text = runtime.ocr_text(_frame)
                    scene_id = yield from self._enter_daily_from_world_like(
                        ctx,
                        runtime,
                        stop_event,
                        _frame,
                        scene_id,
                        world_text,
                        label="日常_首领",
                    )
                list_status = yield from self._open_daily_boss_list_from_daily(ctx, stop_event, payload)
                if list_status == "done":
                    yield from self._return_daily_boss_to_world(ctx, stop_event)
                    return "success"
                if list_status == "skipped":
                    yield from self._return_daily_boss_to_world(ctx, stop_event)
                    return "skipped"
                scene_id = 178
            detail_status = yield from self._open_watched_daily_boss_detail(ctx, stop_event, payload)
            if detail_status == "done":
                yield from self._return_daily_boss_to_world(ctx, stop_event)
                return "success"
            if detail_status == "skipped":
                yield from self._return_daily_boss_to_world(ctx, stop_event)
                return "skipped"

        return (yield from self._handle_daily_boss_detail(ctx, stop_event, payload))

    def _open_daily_boss_list_from_daily(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from runtime.open_daily_entry(
            label="日常_首领",
            title_pattern=r"击\s*败\s*首\s*领",
            progress_can_mark_done=False,
            max_scrolls=10,
        )
        if status == "done":
            raise RuntimeError("日常_首领：日常列表进度不能作为首领奖励完成证据")
        if status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload or {},
                task_id="daily-boss",
                task_type="daily_boss",
                label="日常_首领",
                entry_label="击败首领",
            )
            return "skipped"
        yield from self._wait_daily_boss_list(ctx, stop_event, timeout=20.0, label="日常_首领：等待首领列表 #178")
        return "success"

    def _open_watched_daily_boss_detail(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image178 = ctx.get("images", {}).get(178)
        if not isinstance(image178, dict):
            raise RuntimeError("缺少 #178「首领列表」标注，无法查找注视中首领")
        list_shape = self._find_shape(image178, "首领列表")
        if list_shape is None:
            raise RuntimeError("缺少 #178「首领列表」滚动区域标注，无法查找注视中首领")
        xianjie_shape = self._find_shape(image178, "仙界")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        runtime_snapshot = self._daily_boss_runtime_snapshot_for_list(payload)
        runtime_remaining_authoritative = bool(
            runtime_snapshot.get("complete")
            and runtime_snapshot.get("list_loaded")
        )
        remaining = (
            runtime_snapshot.get("reward_remaining")
            if runtime_remaining_authoritative
            else None
        )
        if remaining is not None:
            remaining = int(remaining)
            payload["_daily_boss_challenge_remaining"] = remaining
            with self._lock:
                self._log_locked(
                    "detail",
                    (
                        "日常_首领：Runtime 读取剩余奖励次数 "
                        f"{remaining}（BossMgr.Model.BossData）"
                    ),
                )
        else:
            remaining = self._daily_boss_reward_remaining_from_scene(ctx, image178)
            if remaining is None:
                remaining = _parse_daily_boss_reward_remaining(runtime.ocr_text(update=True))
        if remaining == 0 and not runtime_remaining_authoritative:
            # A single OCR ``0`` must not suppress the whole day's boss job.  The
            # counter is small and animated; a bad crop/read previously wrote a
            # cross-day retry even while the daily ledger still showed 0/3.
            # Confirm it from a newly captured frame before treating it as the
            # authoritative completion signal.
            confirm_frame = runtime.cur_frame(update=True)
            confirm_text = runtime.ocr_text_in_shapes(
                View(image178),
                ("剩余奖励次数",),
                padding=12,
                frame_data_url=confirm_frame,
            )
            confirmed_remaining = _parse_daily_boss_reward_remaining(confirm_text)
            if confirmed_remaining != 0:
                with self._lock:
                    self._log_locked(
                        "warning",
                        "日常_首领：首次读到剩余奖励次数 0，但新帧未确认，继续查找首领",
                    )
                remaining = confirmed_remaining
        if remaining == 0:
            next_time = self._record_daily_boss_done_for_today(payload)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：剩余奖励次数为 0，下次 {next_time}",
                    phase="daily_boss_no_reward",
                    current_scene=178,
                )
                self._log_locked("success", self._status["message"])
            return "done"
        if xianjie_shape is not None:
            with self._lock:
                self._set_status_locked("running", "日常_首领：确认仙界页签", phase="daily_boss_open_xianjie", current_scene=178)
                self._log_locked("action", "日常_首领：点击 #178「仙界」页签")
            box = self._box(xianjie_shape, image178)
            runtime.click_frame_point(
                View(image178),
                float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
                float(box.get("y") or 0) + float(box.get("h") or 0) / 2,
            )
            yield from runtime.wait_action_settle(1.5)
            yield from self._wait_daily_boss_list(ctx, stop_event, timeout=12.0, label="日常_首领：等待仙界首领列表 #178")

        scroll_index = 0
        while True:
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：查找仙界注视中首领 {scroll_index}",
                    phase="daily_boss_find_watched",
                    current_scene=178,
                )
            frame = runtime.cur_frame(update=True)
            item = runtime.find_floating_item_by_anchor(
                178,
                "条目",
                "注视中",
                container_shape="首领列表",
                frame_data_url=frame,
            )
            if item is not None:
                cd_status = yield from self._daily_boss_handle_watched_item_cd(
                    runtime,
                    item,
                    payload,
                    stop_event,
                    frame_data_url=frame,
                )
                if cd_status == "skipped":
                    yield from self._return_daily_boss_to_world(ctx, stop_event)
                    return "skipped"
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：点击注视中首领 {item.text}",
                        phase="daily_boss_click_watched",
                        current_scene=178,
                    )
                    self._log_locked("action", f"日常_首领：点击 #178「{item.text or '注视中'}」")
                runtime.click_floating_item_field(item, "注视中")
                yield from runtime.wait_any(
                    {
                        "scene": runtime.view_visible(179),
                        "detail": runtime.ocr_matches(
                            self._daily_boss_text_is_detail,
                            label="日常_首领：首领详情 OCR",
                            preview_chars=120,
                        ),
                    },
                    timeout=45.0,
                    label="日常_首领：等待首领详情 #179",
                )
                return "opened"

            with self._lock:
                self._log_locked("action", f"日常_首领：未找到「注视中」，滚动首领列表 {scroll_index + 1}")
            changed = yield from self._scroll_shape_content_changed(ctx, image178, list_shape, stop_event)
            if not changed:
                break
            scroll_index += 1
        next_time, source = self._record_daily_boss_next_time_from_current_list(ctx, payload)
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：仙界首领列表未找到「注视中」目标，{source}，下次 {next_time}",
                phase="daily_boss_no_watched_item",
                current_scene=178,
            )
            self._log_locked("skip", self._status["message"])
        return "skipped"

    def _daily_boss_handle_watched_item_cd(
        self,
        runtime: Any,
        item: Any,
        payload: dict[str, Any],
        stop_event: threading.Event,
        *,
        frame_data_url: str | None = None,
    ):
        refresh_text = runtime.read_floating_item_field(item, "刷新时间", frame_data_url=frame_data_url, padding=12)
        if not re.search(r"刷新|时间", _sanitize_ocr_text(refresh_text)):
            return "ready"
        timeout_seconds = float(payload.get("cd_ocr_timeout_seconds") or 30.0)
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        last_text = refresh_text
        while True:
            self._raise_if_stopped(stop_event)
            cd_seconds = _parse_daily_boss_cd_seconds_from_six_digits(last_text)
            if cd_seconds is not None:
                next_time = self._record_daily_boss_recheck_time(payload, seconds=cd_seconds + 10)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：注视中首领处于刷新 CD，{last_text}，下次 {next_time}",
                        phase="daily_boss_list_cd",
                        current_scene=178,
                    )
                    self._log_locked("skip", self._status["message"])
                return "skipped"
            if time.monotonic() >= deadline:
                next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：注视中条目有刷新时间但 30 秒未读到 6 位 CD，{last_text}，下次 {next_time}",
                        phase="daily_boss_list_cd_unreadable",
                        current_scene=178,
                    )
                    self._log_locked("skip", self._status["message"])
                return "skipped"
            yield BehaviorTreeStatus.RUNNING
            if stop_event.wait(1.0):
                self._raise_if_stopped(stop_event)
            last_text = runtime.read_floating_item_field(item, "刷新时间", padding=12)

    def _wait_daily_boss_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([178], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 178 or self._daily_boss_text_is_list(text):
                with self._lock:
                    self._status.update({"current_scene": 178, "updated_at": time.time()})
                    self._log_locked(
                        "success",
                        f"{label}：已到达首领列表，识别 {'#178' if scene_id == 178 else 'OCR'} {score:.0f}%",
                    )
                return "success"
            if time.monotonic() - start >= float(timeout):
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到 #178，最后 {scene_text} {last_score:.0f}% OCR={last_text[:160]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_boss_wait_list",
                    current_scene=scene_id,
                )

    def _handle_daily_boss_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        image179 = ctx.get("images", {}).get(179)
        if not isinstance(image179, dict):
            raise RuntimeError("缺少 #179「首领详情」标注，无法处理首领挑战")
        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        detail_text = runtime.ocr_text_in_shapes(179, ("神识注视", "剩余奖励次数", "挑战状态"), padding=20)
        remaining = _parse_daily_boss_reward_remaining(detail_text)
        if remaining == 0:
            next_time = self._next_daily_boss_reset_time_text()
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：奖励次数已用尽，下次 {next_time}",
                    phase="daily_boss_no_reward_detail",
                    current_scene=179,
                )
                self._log_locked("success", self._status["message"])
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            return "success"

        cd_seconds = _parse_daily_boss_cd_seconds(detail_text)
        if cd_seconds is not None:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=max(60, cd_seconds))
            self._log("skip", f"日常_首领：首领详情仍在 CD，{cd_seconds}s 后复查，下次 {next_time}")
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            return "skipped"

        view179 = runtime.get_view(179)
        challenge_shape = view179.get_shape("前往挑战") if isinstance(view179, View) else None
        if challenge_shape is None:
            raise RuntimeError("缺少 #179「前往挑战」标注，无法挑战首领")

        if remaining is None and "前往挑战" not in detail_text:
            fallback_seconds = int(payload.get("fallback_seconds") or 300)
            next_time = (_behavior_tree_runtime._now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
            )
            self._log("skip", f"日常_首领：未识别到「前往挑战」或 CD，当前文本：{detail_text or '空'}；{next_time} 兜底重试")
            return "skipped"

        if remaining is not None:
            payload["_daily_boss_challenge_remaining"] = int(remaining)
        with self._lock:
            self._set_status_locked("running", "日常_首领：点击前往挑战", phase="daily_boss_challenge", current_scene=179)
            self._log_locked("action", "日常_首领：点击 #179「前往挑战」")
        box = challenge_shape.box()
        click_x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
        click_y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        runtime.click_frame_point(179, click_x, click_y)
        post_result = yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload)
        return post_result

    def _wait_daily_boss_after_challenge(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        deadline = time.monotonic() + float(payload.get("post_challenge_wait_seconds") or 300)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        saw_fighting = False
        challenge_remaining = payload.get("_daily_boss_challenge_remaining")
        try:
            challenge_remaining_int = (
                int(challenge_remaining)
                if challenge_remaining is not None
                else None
            )
        except (TypeError, ValueError):
            challenge_remaining_int = None
        runtime_probe_interval = max(
            3.0,
            float(payload.get("daily_boss_runtime_probe_seconds") or 60.0),
        )
        last_runtime_probe_at = float("-inf")
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            if stop_event.wait(3.0):
                self._raise_if_stopped(stop_event)
            scene_id, score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id == 181:
                return (yield from self._finish_daily_boss_round_after_done(ctx, runtime, stop_event, payload))
            if scene_id == 180:
                saw_fighting = True
                current_text = self._daily_boss_status_text_from_frame(ctx, frame)
                if self._daily_boss_done_text(current_text):
                    return (yield from self._finish_daily_boss_round_after_done(ctx, runtime, stop_event, payload))
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：已识别 #180 战斗中 {score:.0f}%，继续等待 #181 封印",
                        phase="daily_boss_wait_boss_done",
                        current_scene=180,
                    )
                yield BehaviorTreeStatus.RUNNING
                continue
            current_text = self._daily_boss_status_text_from_frame(ctx, frame)
            if self._daily_boss_done_text(current_text):
                return (yield from self._finish_daily_boss_round_after_done(ctx, runtime, stop_event, payload))
            probe_now = time.monotonic()
            runtime_snapshot = (
                self._daily_boss_runtime_snapshot(payload)
                if challenge_remaining_int is not None
                and probe_now - last_runtime_probe_at >= runtime_probe_interval
                else {}
            )
            if runtime_snapshot:
                last_runtime_probe_at = probe_now
                self._log(
                    "detail",
                    "日常_首领：战后只读 Runtime 探针 "
                    f"complete={runtime_snapshot.get('complete') is True} "
                    f"remaining={runtime_snapshot.get('reward_remaining')} "
                    f"elapsed={float(runtime_snapshot.get('elapsed_seconds') or 0.0):.2f}s",
                )
            runtime_remaining = (
                runtime_snapshot.get("reward_remaining")
                if runtime_snapshot.get("complete") is True
                else None
            )
            try:
                runtime_remaining_int = (
                    int(runtime_remaining)
                    if runtime_remaining is not None
                    else None
                )
            except (TypeError, ValueError):
                runtime_remaining_int = None
            if (
                challenge_remaining_int is not None
                and runtime_remaining_int is not None
                and runtime_remaining_int < challenge_remaining_int
            ):
                if runtime_remaining_int <= 0:
                    next_time = self._record_daily_boss_done_for_today(payload)
                    result = "success"
                    source = "Runtime 剩余奖励次数已降为 0"
                else:
                    next_time = self._record_daily_boss_recheck_time(payload, seconds=60)
                    result = "skipped"
                    source = (
                        "Runtime 剩余奖励次数"
                        f"由 {challenge_remaining_int} 降为 {runtime_remaining_int}"
                    )
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：{source}，确认本轮已经结算；下次 {next_time}",
                        phase="daily_boss_done_by_runtime_delta",
                        current_scene=scene_id,
                    )
                    self._log_locked(result if result == "success" else "skip", self._status["message"])
                yield from self._safe_daily_done_cleanup(
                    lambda: self._return_daily_boss_to_world(ctx, stop_event),
                    label="日常_首领",
                    repeat_risk="重复挑战",
                )
                return result
            if self._daily_boss_combat_in_progress_text(current_text):
                saw_fighting = True
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_首领：首领战斗页已出现，继续等待 #181 封印",
                        phase="daily_boss_combat_started",
                        current_scene=scene_id,
                    )
                yield BehaviorTreeStatus.RUNNING
                continue
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_首领：挑战中，等待 #181 封印",
                    phase="daily_boss_wait_post_challenge",
                    current_scene=scene_id,
                )
            yield BehaviorTreeStatus.RUNNING
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        self._log("skip", f"日常_首领：等待 #181「封印」超时{'，已见 #180' if saw_fighting else ''}，{next_time} 重试")
        yield from self._return_daily_boss_to_world(ctx, stop_event)
        return "skipped"

    def _complete_daily_boss_from_done_frame(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        return (yield from self._finish_daily_boss_round_after_done(ctx, runtime, stop_event, payload))

    def _finish_daily_boss_round_after_done(self, ctx: dict[str, Any], runtime: Any, stop_event: threading.Event, payload: dict[str, Any]) -> str:
        next_time, source, completed = yield from self._record_daily_boss_next_time_after_done(
            ctx,
            stop_event,
            payload,
        )
        result = "success" if completed else "skipped"
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：本轮挑战已结束；{source}；下次 {next_time}",
                phase="daily_boss_done",
                current_scene=181,
            )
            self._log_locked(result, self._status["message"])
        yield from self._safe_daily_done_cleanup(
            lambda: self._return_daily_boss_to_world(ctx, stop_event),
            label="日常_首领",
            repeat_risk="重复挑战",
        )
        return result

    def _leave_daily_boss_fighting_and_recheck_rewards(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> str:
        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        view180 = runtime.get_view(180)
        leave_shape = view180.get_shape("离开") if isinstance(view180, View) else None
        if leave_shape is None:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            self._log("skip", f"日常_首领：{reason}，但缺少 #180「离开」标注，{next_time} 复查")
            return "skipped"
        with self._lock:
            self._set_status_locked("running", f"日常_首领：{reason}，离开后复核奖励次数", phase="daily_boss_leave_stuck_20", current_scene=180)
            self._log_locked("action", "日常_首领：点击 #180「离开」")
        leave_shape.click(runtime)
        opened = yield from self._open_daily_boss_list_after_leaving_fight(ctx, runtime, stop_event)
        if opened == "done":
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            return "success"
        if not opened:
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id == 181:
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            self._log("skip", f"日常_首领：{reason}，离开后未能回到 #178 复核奖励次数，{next_time} 复查")
            return "skipped"

        next_time, source = self._record_daily_boss_next_time_from_current_list(ctx, payload)
        result = "success" if "奖励次数已用尽" in str(source or "") else "skipped"
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：{reason}，已回列表复核，{source}，下次 {next_time}",
                phase="daily_boss_done_after_stuck_20",
                current_scene=178,
            )
            self._log_locked(result if result != "skipped" else "skip", self._status["message"])
        yield from self._return_daily_boss_to_world(ctx, stop_event)
        return result

    def _return_daily_boss_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            with self._lock:
                self._log_locked("warning", "日常_首领：缺少资产树路径，无法收尾回世界 #34")
            return "skipped"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        if (yield from self._close_daily_boss_item_detail_if_present(ctx, runtime, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        if (yield from self._close_daily_boss_storage_bag_if_present(ctx, runtime, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image314 = images.get(314) if isinstance(images, dict) else None
        loading_similarity = (
            self._scene_reference_similarity(ctx, image314, _frame)
            if scene_id is None and isinstance(image314, dict) and _frame
            else None
        )
        if loading_similarity is not None and loading_similarity >= 94.0:
            # 魔道入侵日的首领结算会进入一个没有控件的超长回城动画。
            # 它与 #314 的全帧背景高度相似，但没有 #314 身份；通用左下
            # 返回在这里不会生效，只会耗尽 unknown fallback。进入该窄分支
            # 后只等待可靠场景自然落地，绝不猜坐标或点击动画。
            deadline = time.monotonic() + 120.0
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：检测到魔道入侵回城动画（与 #314 全帧相似 {loading_similarity:.0f}%），等待自然落到 #34",
                    phase="daily_boss_wait_mozu_world_transition",
                    current_scene=None,
                )
                self._log_locked("wait", self._status["message"])
            while time.monotonic() < deadline:
                self._raise_if_stopped(stop_event)
                yield from runtime.wait_action_settle(3.0)
                scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(
                    ctx, runtime, update=True
                )
                if scene_id == 34:
                    yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
                    return "success"
                if scene_id is not None:
                    break
        if scene_id == 34:
            yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
            return "success"
        image178 = images.get(178)
        back_shape = self._find_shape(image178, "返回") if isinstance(image178, dict) else None
        if self._daily_boss_text_is_list(_text) and isinstance(image178, dict) and back_shape is not None:
            box = self._box(back_shape, image178)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            with self._lock:
                self._set_status_locked("running", "日常_首领：从首领列表返回世界", phase="daily_boss_return_from_list", current_scene=178)
                self._log_locked("action", "日常_首领：点击 #178「返回」")
            runtime.click_frame_point(image178, x, y)
            yield from runtime.wait_action_settle(2.0)
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                return "success"
        with self._lock:
            self._set_status_locked("running", "日常_首领：收尾回到世界 #34", phase="daily_boss_return_world", current_scene=scene_id)
            self._log_locked("action", "日常_首领：完成后按场景图回到 #34 世界")
        try:
            self._clear_tick_frame(ctx)
            runtime.clear_frame()
            ctx["_go_scene_unknown_transition_guard"] = {
                "reference_scene_id": 314,
                "similarity_threshold": 94.0,
                "wait_seconds": 120.0,
                "phase": "daily_boss_wait_mozu_world_transition",
                "label": "魔道入侵回城动画",
            }
            try:
                yield from runtime.goto_view(34)
            finally:
                ctx.pop("_go_scene_unknown_transition_guard", None)
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id != 34:
                raise RuntimeError(f"回世界后仍识别为 #{scene_id or 'unknown'}")
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
        except Exception as exc:
            with self._lock:
                self._log_locked("warning", f"日常_首领：收尾回世界 #34 失败：{exc}")
            raise
        return "success"

    def _daily_boss_item_detail_text_matches(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if "描述" not in compact:
            return False
        if "境界要求" not in compact and "获取途径" not in compact:
            return False
        return any(token in compact for token in ("合成", "获取途径", "使用"))

    def _close_daily_boss_item_detail_if_present(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        stop_event: threading.Event,
        frame: str | None,
        text: str,
    ):
        if not self._daily_boss_item_detail_text_matches(text):
            return False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image250 = images.get(250)
        back_shape = self._find_shape(image250, "返回") if isinstance(image250, dict) else None
        if not isinstance(image250, dict) or back_shape is None:
            raise RuntimeError("日常_首领：道具详情弹窗已出现，但缺少 #250「返回」标注，无法安全收尾")
        box = self._box(back_shape, image250)
        x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
        y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        with self._lock:
            self._set_status_locked("running", "日常_首领：关闭奖励道具详情弹窗", phase="daily_boss_close_item_detail")
            self._log_locked("action", "日常_首领：检测到道具详情弹窗，点击 #250「返回」")
        runtime.click_frame_point(image250, x, y)
        yield from runtime.wait_action_settle(2.0)
        return True

    def _daily_boss_storage_bag_text_matches(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if "储物袋" not in compact:
            return False
        tab_hits = sum(1 for token in ("全部", "书籍", "丹药", "礼物", "日程") if token in compact)
        return tab_hits >= 3 or "快捷操作" in compact

    def _close_daily_boss_storage_bag_if_present(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        stop_event: threading.Event,
        frame: str | None,
        text: str,
    ):
        if not self._daily_boss_storage_bag_text_matches(text):
            return False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image249 = images.get(249)
        back_shape = self._find_shape(image249, "返回") if isinstance(image249, dict) else None
        if not isinstance(image249, dict) or back_shape is None:
            raise RuntimeError("日常_首领：储物袋页已出现，但缺少 #249「返回」标注，无法安全收尾")
        box = self._box(back_shape, image249)
        x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
        y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
        with self._lock:
            self._set_status_locked("running", "日常_首领：关闭储物袋页", phase="daily_boss_close_storage_bag")
            self._log_locked("action", "日常_首领：检测到储物袋页，点击 #249「返回」")
        runtime.click_frame_point(image249, x, y)
        yield from runtime.wait_action_settle(2.0)
        return True

    def _open_daily_boss_list_after_leaving_fight(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        stop_event: threading.Event,
    ):
        try:
            yield from runtime.wait_view_id(178, timeout=8.0, label="日常_首领：等待首领列表 #178")
            return True
        except Exception:
            pass
        if (yield from self._close_daily_boss_reward_result_if_present(ctx, runtime, stop_event)):
            try:
                yield from runtime.wait_view_id(178, timeout=8.0, label="日常_首领：等待奖励页关闭后回到首领列表 #178")
                return True
            except Exception:
                pass
        scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        if (yield from self._close_daily_boss_item_detail_if_present(ctx, runtime, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        if (yield from self._close_daily_boss_storage_bag_if_present(ctx, runtime, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        if scene_id == 178:
            return True
        if scene_id == 181:
            return False
        try:
            if scene_id != 69:
                with self._lock:
                    self._set_status_locked("running", "日常_首领：离开战斗后重新进入日常 #69", phase="daily_boss_reopen_daily_after_leave")
                    self._log_locked("action", "日常_首领：离开战斗后按场景图跳转到 #69")
                yield from runtime.goto_view(69)
            status = yield from self._open_daily_boss_list_from_daily(ctx, stop_event)
            return "done" if status == "done" else True
        except Exception as exc:
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id == 34:
                with self._lock:
                    self._log_locked("warning", f"日常_首领：离开战斗后复核 #178 失败，但已回到世界，转为稍后复查：{exc}")
                return False
            with self._lock:
                self._log_locked("warning", f"日常_首领：离开战斗后重新进入 #178 失败：{exc}")
            return False

    def _close_daily_boss_reward_result_if_present(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        stop_event: threading.Event,
    ):
        try:
            scene_id, _score, frame, text = self._fanxiu_runtime_scene_text(ctx, runtime, [177, 178, 34], update=True)
        except Exception as exc:
            self._log("detail", f"日常_首领：奖励结果页探测失败，跳过奖励页收口：{exc}")
            return False
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        is_reward_result = (
            scene_id == 177
            or "点击屏幕继续" in compact
            or "点击继续" in compact
            or "恭喜获得" in compact
            or ("恭喜获得" in compact and "自动关闭" in compact)
        )
        if not is_reward_result:
            return False
        width = 900
        height = 1600
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image177 = images.get(177)
        if isinstance(image177, dict):
            width, height = self._frame_size(image177)
        with self._lock:
            self._set_status_locked("running", "日常_首领：关闭挑战奖励结果页", phase="daily_boss_close_reward_result", current_scene=scene_id)
            self._log_locked("action", "日常_首领：点击奖励结果页「点击屏幕继续」")
        runtime.click_frame_point(image177 if isinstance(image177, dict) else {"width": width, "height": height}, width * 0.5, height * 0.86)
        yield from runtime.wait_action_settle(2.0)
        return True

    def _record_daily_boss_next_time_after_done(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        returned_to_list = scene_id == 178
        if scene_id != 178:
            view181 = runtime.get_view(181)
            leave_shape = view181.get_shape("离开") if isinstance(view181, View) else None
            if leave_shape is not None:
                with self._lock:
                    self._set_status_locked("running", "日常_首领：挑战完成，点击离开回列表读取刷新时间", phase="daily_boss_leave_done", current_scene=181)
                    self._log_locked("action", "日常_首领：点击 #181「离开」")
                leave_shape.click(runtime)
                try:
                    yield from runtime.wait_view_id(178, timeout=20.0, label="日常_首领：等待首领列表 #178")
                    returned_to_list = True
                except Exception as exc:
                    with self._lock:
                        self._log_locked("warning", f"日常_首领：离开 #181 后未能回到 #178 读取刷新时间：{exc}")
            else:
                with self._lock:
                    self._log_locked("warning", "日常_首领：缺少 #181「离开」标注，无法回 #178 首领列表读取刷新时间")
        if returned_to_list:
            next_time, source = self._record_daily_boss_next_time_from_current_list(ctx, payload)
            runtime_snapshot = self._daily_boss_runtime_snapshot(payload)
            runtime_remaining = (
                runtime_snapshot.get("reward_remaining")
                if runtime_snapshot.get("complete")
                else None
            )
            try:
                completed = (
                    runtime_remaining is not None
                    and int(runtime_remaining) <= 0
                )
            except (TypeError, ValueError):
                completed = False
            return next_time, f"已识别 #181 封印完成；{source}", completed

        runtime_snapshot = self._daily_boss_runtime_snapshot(payload)
        runtime_remaining = (
            runtime_snapshot.get("reward_remaining")
            if runtime_snapshot.get("complete")
            else None
        )
        try:
            runtime_remaining_int = (
                int(runtime_remaining)
                if runtime_remaining is not None
                else None
            )
        except (TypeError, ValueError):
            runtime_remaining_int = None
        if runtime_remaining_int is not None and runtime_remaining_int <= 0:
            next_time = self._next_daily_boss_reset_time_text()
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
            )
            return next_time, "战后 Runtime 剩余奖励次数为 0，奖励次数已用尽", True
        if runtime_remaining_int is not None:
            next_time = self._record_daily_boss_recheck_time(
                payload,
                seconds=60,
            )
            return (
                next_time,
                f"战后 Runtime 仍有 {runtime_remaining_int} 次奖励，60 秒后复核首领/CD",
                False,
            )

        challenge_remaining = payload.get("_daily_boss_challenge_remaining")
        try:
            challenge_remaining_int = int(challenge_remaining) if challenge_remaining is not None else None
        except (TypeError, ValueError):
            challenge_remaining_int = None
        if challenge_remaining_int is not None and challenge_remaining_int <= 1:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            return next_time, "战后 Runtime 不完整，不能根据挑战前剩余 1 次推断奖励已用尽", False
        if challenge_remaining_int is not None:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            return next_time, f"已识别 #181 封印完成；挑战前剩余奖励次数为 {challenge_remaining_int}，半小时后复查刷新 CD", False
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "已识别 #181 封印完成；挑战前奖励次数未知，半小时后复查刷新 CD", False

    def _record_daily_boss_next_time_from_current_list(self, ctx: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
        runtime_snapshot = self._daily_boss_runtime_snapshot(payload)
        remaining = (
            runtime_snapshot.get("reward_remaining")
            if runtime_snapshot.get("complete")
            else None
        )
        if remaining is not None:
            remaining = int(remaining)
        else:
            remaining = self._daily_boss_reward_remaining_from_scene(ctx, ctx.get("images", {}).get(178) or {})
        if remaining == 0:
            next_time = self._next_daily_boss_reset_time_text()
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
            )
            source = (
                "Runtime 奖励次数已用尽"
                if runtime_snapshot.get("complete")
                else "奖励次数已用尽"
            )
            return next_time, source
        runtime_cd_seconds = runtime_snapshot.get(
            "refresh_remaining_seconds"
        )
        if (
            runtime_snapshot.get("complete")
            and runtime_snapshot.get("big_boss_dead") is True
            and isinstance(runtime_cd_seconds, int)
            and runtime_cd_seconds > 0
        ):
            next_time = self._record_daily_boss_recheck_time(
                payload,
                seconds=runtime_cd_seconds + 10,
            )
            return (
                next_time,
                (
                    "按 Runtime 大首领刷新时间读取 "
                    f"{runtime_cd_seconds} 秒"
                ),
            )
        cd_seconds, cd_text = self._daily_boss_refresh_cd_from_list(ctx)
        if cd_seconds and cd_seconds > 0:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=cd_seconds + 10)
            return next_time, f"按 #178 注视中条目刷新时间读取 {cd_text or str(cd_seconds) + ' 秒'}"
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "奖励次数未用尽但未读到 #178 注视中条目刷新时间，半小时后复查"

    def _daily_boss_status_text_from_frame(self, ctx: dict[str, Any], frame: str | None = None) -> str:
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        return runtime.ocr_text(frame) if isinstance(frame, str) and frame else runtime.ocr_text(update=True)

    def _daily_boss_text_is_list(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return (
            "首领" in normalized
            and "首领境界" in normalized
            and ("剩余奖励次数" in normalized or "掉落记录" in normalized)
        )

    def _daily_boss_text_is_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return (
            "首领规则" in normalized
            and (
                "剩余奖励次数" in normalized
                or "前往挑战" in normalized
                or "神识注视" in normalized
            )
        )

    def _daily_boss_combat_in_progress_text(self, text: str) -> bool:
        return "首领" in text and any(fragment in text for fragment in ("自动战斗中", "后刷新", "数据统计", "伤害"))

    def _daily_boss_done_text(self, text: str) -> bool:
        return "封印" in _sanitize_ocr_text(text)

    def _daily_boss_stuck_map_text(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "离开" in normalized and "数据统计" in normalized and "自动战斗中" not in normalized

    def _record_daily_boss_recheck_time(self, payload: dict[str, Any], *, seconds: int) -> str:
        recheck_seconds = min(1800, max(60, int(seconds)))
        next_time = (_behavior_tree_runtime._now() + timedelta(seconds=recheck_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
        )
        return next_time

    def _record_daily_boss_done_for_today(self, payload: dict[str, Any]) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
        )
        return next_time

    def _daily_boss_refresh_cd_from_list(self, ctx: dict[str, Any]) -> tuple[int | None, str]:
        images = ctx.get("images", {}) if isinstance(ctx.get("images"), dict) else {}
        image178 = images.get(178)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        frame = runtime.cur_frame(update=True)
        texts: list[str] = []
        if isinstance(image178, dict):
            item = runtime.find_floating_item_by_anchor(
                178,
                "条目",
                "注视中",
                container_shape="首领列表",
                frame_data_url=frame,
            )
            if item is not None:
                text = runtime.read_floating_item_field(
                    item,
                    "刷新时间",
                    frame_data_url=frame,
                    padding=12,
                )
            else:
                text = ""
            if text:
                texts.append(text)
                cd_seconds = _parse_daily_boss_cd_seconds_from_six_digits(text)
                if cd_seconds is None:
                    cd_seconds = _parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0:
                    return cd_seconds, text
            text = runtime.ocr_text_in_shapes(View(image178), ("首领列表",), padding=8, frame_data_url=frame)
            if text:
                texts.append(text)
                cd_seconds = _parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0 and "刷新" in text:
                    return cd_seconds, text
        return None, " ".join(texts)

    def _daily_boss_reward_remaining_from_scene(self, ctx: dict[str, Any], image: dict[str, Any]) -> int | None:
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        text = runtime.ocr_text_in_shapes(View(image), ("剩余奖励次数",), padding=12)
        return _parse_daily_boss_reward_remaining(text)

    def _daily_boss_runtime_snapshot(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        override = (payload or {}).get(
            "__daily_boss_runtime_snapshot_override"
        )
        if isinstance(override, dict):
            return dict(override)
        try:
            from backend.core.fanxiu.instrumentation.boss import (
                read_boss_snapshot,
            )

            return read_boss_snapshot()
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "complete": False,
                "source": "runtime_memory",
                "reason": f"{type(exc).__name__}: {exc}",
            }

    def _daily_boss_runtime_snapshot_for_list(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        preflight_snapshot = payload.pop("_daily_boss_preflight_snapshot", None)
        if isinstance(preflight_snapshot, dict):
            return dict(preflight_snapshot)
        return self._daily_boss_runtime_snapshot(payload)

    def _next_daily_boss_reset_time_text(self) -> str:
        now = _behavior_tree_runtime._now()
        reset_at = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if reset_at <= now:
            reset_at += timedelta(days=1)
        return reset_at.strftime("%Y-%m-%d %H:%M:%S")

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, frame_data_url=frame)
        text = runtime.ocr_text(frame)
        if scene_id is None:
            scene_id, score, _frame = runtime.current_scene([34, 69, 183, 184, 185, 186, 187, 188, 189], frame_data_url=frame)
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
        try:
            yield from cleanup_factory()
        except Exception as exc:
            self._log("warning", f"{label}：业务已完成，但{action}失败，按已完成处理避免{repeat_risk}：{exc}")
        return "success"

    def _daily_lingzu_next_time_is_future(self, payload: dict[str, Any]) -> str | None:
        task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingzu").strip() or "legacy-daily-lingzu"
        task = next(
            (item for item in _read_data_annotation_scheduler_tasks() if str(item.get("id") or "") == task_id),
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
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
            return (yield from self._run_daily_lingzu_challenge(ctx, runtime, stop_event, payload))
        if scene_id == 184:
            return (yield from self._run_daily_lingzu_challenge(ctx, runtime, stop_event, payload))
        if scene_id == 183:
            detail_status = yield from self._open_daily_lingzu_detail(ctx, runtime, stop_event, payload)
            if detail_status == "done":
                return "success"
            return (yield from self._run_daily_lingzu_challenge(ctx, runtime, stop_event, payload))
        if scene_id != 69:
            world_text = runtime.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                runtime,
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

        detail_status = yield from self._open_daily_lingzu_detail(ctx, runtime, stop_event, payload)
        if detail_status == "done":
            yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
            return "success"

        return (yield from self._run_daily_lingzu_challenge(ctx, runtime, stop_event, payload))

    def _return_daily_lingzu_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            with self._lock:
                self._log_locked("warning", "日常_灵祖：缺少资产树路径，无法收尾回世界 #34")
            return "skipped"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
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
            yield from runtime.wait_click(186, str(close_shape.get("title") or "关闭"))
            start = time.monotonic()
            while True:
                self._raise_if_stopped(stop_event)
                yield from runtime.wait_action_settle(1.0)
                scene_id, _score, _frame = runtime.current_scene([34, 183, 184, 187, 188], update=True)
                if scene_id is not None:
                    break
                if time.monotonic() - start >= 12:
                    raise RuntimeError("日常_灵祖：点击 #186 关闭动作后奖励浮层仍未消失")

        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：从日常列表返回世界", phase="daily_lingzu_return_daily", current_scene=69)
                self._log_locked("action", "日常_灵祖：点击 #69「退出」")
            yield from runtime.wait_click(69, "退出")
            yield from runtime.wait_action_settle(2.0)
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_灵祖")):
                yield from runtime.wait_action_settle(2.0)
            yield from runtime.wait_view_id(34, timeout=18.0, label="日常_灵祖：等待世界 #34")

        if scene_id == 188:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：从圣雷龙妖祖返回战灵长老", phase="daily_lingzu_return_elder", current_scene=188)
                self._log_locked("action", "日常_灵祖：点击 #188「返回」")
            yield from runtime.wait_click(188, "返回")
            scene_id, _score = yield from runtime.wait_view_id(187, timeout=18.0, label="日常_灵祖：等待战灵长老 #187")

        if scene_id == 187:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：关闭战灵长老对话", phase="daily_lingzu_close_elder", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「空白」")
            yield from runtime.wait_click(187, "空白")
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
            yield from runtime.wait_click(184, "空白")
            scene_id, _score = yield from runtime.wait_view_id(183, timeout=18.0, label="日常_灵祖：等待灵祖活动列表 #183")

        if scene_id == 183:
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：返回世界", phase="daily_lingzu_return_world_click", current_scene=183)
                self._log_locked("action", "日常_灵祖：点击 #183「返回」")
            yield from runtime.wait_click(183, "返回")
            yield from runtime.wait_view_id(34, timeout=18.0, label="日常_灵祖：等待世界 #34")

        scene_id, _score, frame = runtime.current_scene(update=True)
        text = runtime.ocr_text(frame)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        text = runtime.ocr_text(update=True)
        if not self._daily_lingzu_world_text_is_internal_area(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        with self._lock:
            self._set_status_locked("running", "日常_灵祖：当前仍在宗门内部，点击离开", phase="daily_lingzu_leave_internal_area", current_scene=85)
            self._log_locked("action", "日常_灵祖：点击 #85「离开」")
        yield from runtime.wait_click(85, "离开")

        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([34, 86, 204, 69], update=True)
            text = runtime.ocr_text(frame)
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
                yield from runtime.wait_click(204, "返回")
                yield from runtime.wait_action_settle(2.0)
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
                yield from runtime.wait_click(69, "退出")
                yield from runtime.wait_action_settle(2.0)
                continue
            if scene_id in {289, 86}:
                confirm_id = int(scene_id)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_灵祖：确认离开当前场景",
                        phase="daily_lingzu_leave_confirm",
                        current_scene=confirm_id,
                    )
                    self._log_locked("action", f"日常_灵祖：点击 #{confirm_id}「确认」离开场景")
                yield from runtime.wait_click(confirm_id, "确认")
                yield from runtime.wait_action_settle(2.0)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([190, 191, 192, 69, 34], update=True)
        current_text = runtime.ocr_text(frame)
        if scene_id == 192 or self._daily_jianling_text_is_result(current_text):
            yield from self._finish_daily_jianling_result(ctx, stop_event)
            scene_id = 190
        if self._daily_jianling_text_is_main(current_text):
            scene_id = 190
        if scene_id == 191:
            confirm_result = yield from self._confirm_daily_jianling_sweep(ctx, stop_event)
            if confirm_result == "result":
                yield from self._finish_daily_jianling_result(ctx, stop_event)
            scene_id = 190
        if scene_id == 190:
            text = runtime.ocr_text(update=True)
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
            world_text = runtime.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                runtime,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_剑灵",
            )

        daily_status = yield from runtime.open_daily_entry(
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
        yield from runtime.wait_view(190, label="日常_剑灵：等待淬剑试炼 #190")
        yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_jianling_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from runtime.open_daily_entry(
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
        yield from runtime.wait_view(190, label="日常_剑灵：等待淬剑试炼 #190")
        return "open"

    def _run_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：点击扫荡", phase="daily_jianling_sweep", current_scene=190)
            self._log_locked("action", "日常_剑灵：点击 #190「扫荡」")
        yield from runtime.wait_click(190, "扫荡")
        yield from runtime.wait_view(191, label="日常_剑灵：等待扫荡确认 #191")
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：确认进行扫荡", phase="daily_jianling_confirm_sweep", current_scene=191)
            self._log_locked("action", "日常_剑灵：点击 #191「进行扫荡」")
        yield from runtime.wait_click(191, "进行扫荡")
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
            scene_id, score, frame = runtime.current_scene([190, 192], update=True)
            text = runtime.ocr_text(frame)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(8):
            self._raise_if_stopped(stop_event)
            scene_id, _score, frame = runtime.current_scene([190, 192], update=True)
            text = runtime.ocr_text(frame)
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
                    yield from runtime.wait_click(192, "点击继续")
                else:
                    runtime.click_frame_point({"width": 900, "height": 1600}, 450, 1380)
                    yield from runtime.wait_action_settle()
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_剑灵：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_剑灵：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_jianling_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([190, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id is None and self._daily_jianling_text_is_main(text):
            scene_id = 190
        if scene_id == 190:
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：退出淬剑试炼", phase="daily_jianling_exit_main", current_scene=190)
                self._log_locked("action", "日常_剑灵：点击 #190「返回」")
            yield from runtime.wait_click(190, "返回")
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
            yield from runtime.wait_click(69, "退出")
            yield from runtime.wait_view_id(34, timeout=18.0, label="日常_剑灵：等待世界 #34")
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
        del text, require_world_like
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event, frame_data_url=frame)
        scene_id, score, _matched_frame = runtime.current_scene(
            [477, 66, 326, 325, 266, 265, 264, 233, 225, 85, 186, 289, 86, 69, 34],
            frame_data_url=frame,
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
            yield from runtime.wait_click(477, "返回", timeout=8.0)
            scene_id = yield from runtime.wait_view(
                66,
                34,
                timeout=12.0,
                label=f"{label}：等待离开 #477",
            )
            if scene_id == 34:
                ctx["_go_scene_known_scene_id"] = 34
                return True

        if scene_id == 66:
            self._log("action", f"{label}：从日程 #66 点击正式「返回」回世界")
            yield from runtime.wait_click(66, "返回", timeout=8.0)
            yield from runtime.wait_view(
                34,
                timeout=12.0,
                label=f"{label}：等待日程返回世界 #34",
            )
            ctx["_go_scene_known_scene_id"] = 34
            return True

        if scene_id in {233, 225}:
            self._log("action", f"{label}：识别 #{scene_id} {score:.0f}%，点击正式标注「空白」关闭提示")
            yield from runtime.wait_click(scene_id, "空白", timeout=8.0)
            yield from runtime.wait_action_settle(1.0)
            return True

        if scene_id in {326, 325, 266, 265, 264}:
            self._log("action", f"{label}：识别 #{scene_id} {score:.0f}%，点击正式标注「返回」")
            yield from runtime.wait_click(scene_id, "返回")
            yield from runtime.wait_action_settle(1.0)
            if scene_id == 326:
                yield from runtime.wait_view(325, 69, 34, timeout=18.0, label=f"{label}：等待离开 #326")
            elif scene_id == 325:
                yield from runtime.wait_view(69, 34, timeout=18.0, label=f"{label}：等待离开 #325")
            elif scene_id == 266:
                yield from runtime.wait_view(265, 264, 34, timeout=18.0, label=f"{label}：等待离开 #266")
            elif scene_id == 265:
                yield from runtime.wait_view(264, 34, timeout=18.0, label=f"{label}：等待离开 #265")
            else:
                yield from runtime.wait_view(34, timeout=18.0, label=f"{label}：等待离开 #264")
                ctx["_go_scene_known_scene_id"] = 34
            return True

        if scene_id in {289, 86}:
            self._log("action", f"{label}：识别正式确认场景 #{scene_id}，点击「确认」")
            yield from runtime.wait_click(scene_id, "确认")
            yield from runtime.wait_action_settle(1.0)
        elif scene_id in {85, 186}:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：从正式场景 #{scene_id} 离开",
                    phase="world_side_scene_leave",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"{label}：点击 #{scene_id}「离开」")
            yield from runtime.wait_click(scene_id, "离开")
            yield from runtime.wait_action_settle(1.0)
            confirm_id, _confirm_score, _confirm_frame = runtime.current_scene([289, 86, 34], update=True)
            if confirm_id in {289, 86}:
                self._log("action", f"{label}：识别正式确认场景 #{confirm_id}，点击「确认」")
                yield from runtime.wait_click(confirm_id, "确认")
                yield from runtime.wait_action_settle(1.0)

        with self._lock:
            self._set_status_locked(
                "running",
                f"{label}：等待返回世界 #34",
                phase="world_side_scene_leave_wait_world",
                current_scene=scene_id,
            )
        yield from runtime.wait_view(34, timeout=12.0, label=f"{label}：等待返回世界 #34")
        ctx["_go_scene_known_scene_id"] = 34
        return True

    def _enter_daily_from_world_like(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        stop_event: threading.Event,
        frame: str,
        scene_id: int | None,
        text: str,
        *,
        label: str,
    ):
        if scene_id == 69:
            return 69
        if scene_id is None:
            scene_id, _score, frame = runtime.current_scene(frame_data_url=frame)
            text = runtime.ocr_text(frame)
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
            yield from self._leave_daily_lundao_seated_for_daily_entry(runtime, scene_id)
            scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
            text = runtime.ocr_text(frame)
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
            scene_id, _score, frame = runtime.current_scene([69, 34, 20], update=True)
            text = runtime.ocr_text(frame)
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
            yield from self._daily_yihuo_return_best_effort(runtime)  # type: ignore[attr-defined]
            scene_id, _score, frame = runtime.current_scene([69, 34, 20], update=True)
            text = runtime.ocr_text(frame)
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
                yield from runtime.wait_click(228, "返回")
                yield from runtime.wait_action_settle(2.0)
                scene_id, _score, frame = runtime.current_scene([69, 34, 20], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 69:
                    return 69
                world_like = scene_id == 34
            except Exception as exc:
                self._log("warning", f"{label}：修仙传游历页返回世界失败，继续尝试场景图恢复：{exc}")
        hidden_world_popup_like = scene_id == 59
        if hidden_world_popup_like:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前停在封魔杀活动弹层，先点击空白关闭",
                    phase="daily_recover_from_hidden_world_popup",
                    current_scene=59 if scene_id == 59 else None,
                )
                self._log_locked("action", f"{label}：命中 #59「封魔杀」弹层，点击「空白」关闭")
            try:
                yield from runtime.wait_click(59, "空白", label=f"{label}：关闭封魔杀活动弹层")
                yield from runtime.wait_action_settle(2.0)
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
                popup_view = runtime.find_view("弹窗")
                if scene_id == 59 or (popup_view is not None and popup_view.id == 59):
                    raise RuntimeError("#59「封魔杀」弹层点击「空白」后仍未离开，当前「空白」标注无效，需要人工补有效关闭/返回/前往路径")
                if scene_id == 69:
                    return 69
                world_like = scene_id == 34
            except Exception as exc:
                if "标注无效" in str(exc):
                    raise RuntimeError(f"{label}：{exc}") from exc
                self._log("warning", f"{label}：封魔杀活动弹层关闭失败，继续尝试场景图恢复：{exc}")
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
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 69:
                    return 69
                world_like = scene_id == 34
        if scene_id is None and not world_like:
            recovered, scene_id, frame, text = yield from self._recover_daily_youli_result_before_daily_entry(
                ctx,
                runtime,
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
                    scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                    text = runtime.ocr_text(frame)
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
                yield from runtime.goto_view(69)
                scene_after, score_after, frame_after = runtime.current_scene([69, 34], update=True)
                text_after = runtime.ocr_text(frame_after)
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
                    yield from runtime.goto_view(34)
                    scene_after, score_after, frame_after = runtime.current_scene([69, 34], update=True)
                    text_after = runtime.ocr_text(frame_after)
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
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
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
                yield from runtime.goto_view(69)
                scene_after, score_after, frame_after = runtime.current_scene([69, 34], update=True)
                text_after = runtime.ocr_text(frame_after)
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
                    yield from runtime.wait_action_settle(1.0)
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
                yield from runtime.wait_action_settle(2.0)
            raise last_error or RuntimeError(f"{label}：跳转后未确认进入日常列表")
        except Exception as exc:
            raise RuntimeError(f"{label}：无法通过场景图跳转到 #69；需要补当前场景到日常页的路由/返回/离开标注：{exc}") from exc

    def _recover_daily_youli_result_before_daily_entry(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
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
            probe_scene, _probe_score, probe_frame = runtime.current_scene([237, 69, 34], update=True)
            probe_text = runtime.ocr_text(probe_frame)
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
        scene_after, _score_after, frame_after = runtime.current_scene([69, 34], update=True)
        text_after = runtime.ocr_text(frame_after)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", f"{label}：退出绿瓶", phase="exit_green_bottle", current_scene=20)
            self._log_locked("action", f"{label}：点击 #20「回到世界」")
        yield from runtime.wait_click(20, "回到世界")
        yield BehaviorTreeStatus.RUNNING

        start = time.monotonic()
        clicked_outer_world = False
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([34, 20], update=True)
            text = runtime.ocr_text(frame)
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
                runtime.click_frame_point(20, x, y)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_id, score, frame = runtime.current_scene([275, 237, 204, 69, 289, 86, 58, 20, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
            self._daily_assistant_close_one_key_result(ctx, runtime, frame, label=label)
            yield from runtime.wait_action_settle(1.0)
            scene_id, score, frame = runtime.current_scene([237, 204, 69, 289, 86, 58, 20, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id == 237:
            yield from self._daily_assistant_close_youli_result(runtime, {})
            yield from runtime.wait_action_settle(1.0)
            scene_id, score, frame = runtime.current_scene([204, 69, 289, 86, 58, 20, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id == 204 or self._daily_assistant_text_is_list(text):
            with self._lock:
                self._set_status_locked("running", f"{label}：小助手总览仍在前台，先返回日常页", phase="cleanup_exit_daily_assistant", current_scene=204)
                self._log_locked("action", f"{label}：点击 #204「返回」")
            yield from runtime.wait_click(204, "返回")
            landed = yield from runtime.wait_view(
                69,
                34,
                275,
                237,
                timeout=15.0,
                label=f"{label}：等待退出小助手总览",
            )
            scene_id = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
            score = 100.0
            frame = runtime.cur_frame(update=True) if hasattr(runtime, "cur_frame") else None
            text = runtime.ocr_text(frame)
        if scene_id == 69:
            with self._lock:
                self._set_status_locked("running", f"{label}：从日常页返回世界", phase="cleanup_exit_daily_page", current_scene=69)
                self._log_locked("action", f"{label}：点击 #69「退出」")
            yield from runtime.wait_click(69, "退出")
            landed = yield from runtime.wait_view(34, timeout=25.0, label=f"{label}：等待日常页返回世界")
            scene_id = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
            score = 100.0
            frame = runtime.cur_frame(update=True) if hasattr(runtime, "cur_frame") else None
            text = runtime.ocr_text(frame)
        if scene_id in {289, 86}:
            confirm_id = int(scene_id)
            with self._lock:
                self._set_status_locked("running", f"{label}：确认离开场景后继续收尾", phase="cleanup_confirm_leave", current_scene=confirm_id)
                self._log_locked("action", f"{label}：点击 #{confirm_id}「确认」")
            yield from runtime.wait_click(confirm_id, "确认")
            yield from runtime.wait_action_settle(2.0)
            scene_id, score, frame = runtime.current_scene([58, 20, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id == 58:
            with self._lock:
                self._set_status_locked("running", f"{label}：隐藏浮动窗后确认世界", phase="cleanup_hide_floating_window", current_scene=58)
                self._log_locked("action", f"{label}：检测到 #58 浮动窗，先执行隐藏浮动窗")
            self._execute_hide_floating_window(ctx, stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([20, 34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id == 20:
            yield from self._leave_green_bottle_to_world(ctx, stop_event, label=label)
            scene_id, score, frame = runtime.current_scene([34], update=True)
            text = runtime.ocr_text(frame)
        if scene_id == 34:
            scene_id, score, frame = runtime.current_scene([34], update=True)
            text = runtime.ocr_text(frame)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([196, 195, 194, 193, 69, 34, 20], update=True)
        if scene_id == 20:
            yield from self._leave_daily_lingta_green_bottle(ctx, stop_event)
            scene_id, _score, frame = runtime.current_scene([196, 195, 194, 193, 69, 34, 20], update=True)
        text = runtime.ocr_text(frame)
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
        if scene_id == 195:
            yield from self._confirm_daily_lingta_sweep(ctx, stop_event)
            yield from self._finish_daily_lingta_result(ctx, stop_event)
            scene_id = 194
        if scene_id == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
            scene_id = 194
        if scene_id == 194:
            text = runtime.ocr_text(update=True)
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
            world_text = runtime.ocr_text(frame)
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                runtime,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_灵塔",
            )

        daily_status = yield from runtime.open_daily_entry(
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
        view = yield from runtime.wait_view(193, 194, label="日常_灵塔：等待区域入口 #193 或混沌灵塔 #194")
        if getattr(view, "id", view) == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
        yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_lingta_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from runtime.open_daily_entry(
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(3):
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, _score, frame = runtime.current_scene([194, 193], update=True)
            text = runtime.ocr_text(frame)
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
                yield from runtime.wait_click(193, "进入")
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([194], update=True)
            text = runtime.ocr_text(frame)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：点击扫荡", phase="daily_lingta_sweep", current_scene=194)
            self._log_locked("action", "日常_灵塔：点击 #194「扫荡」")
        yield from runtime.wait_click(194, "扫荡")
        yield from runtime.wait_view(195, label="日常_灵塔：等待扫荡确认 #195")
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：确认进行扫荡", phase="daily_lingta_confirm_sweep", current_scene=195)
            self._log_locked("action", "日常_灵塔：点击 #195「进行扫荡」")
        yield from runtime.wait_click(195, "进行扫荡")
        yield from runtime.wait_view(196, label="日常_灵塔：等待扫荡结果 #196")

    def _finish_daily_lingta_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        for index in range(8):
            self._raise_if_stopped(stop_event)
            scene_id, _score, frame = runtime.current_scene([194, 196], update=True)
            text = runtime.ocr_text(frame)
            if self._daily_lingta_remaining_zero(text):
                return "done"
            if scene_id == 194 or ("混沌灵塔" in text and "剩余次数" in text):
                return "success"
            if scene_id == 196 or "点击" in text or "扫荡奖励" in text:
                with self._lock:
                    self._set_status_locked("running", f"日常_灵塔：关闭扫荡结果 {index + 1}", phase="daily_lingta_continue_result", current_scene=196)
                    self._log_locked("action", "日常_灵塔：点击 #196「点击继续」")
                yield from runtime.wait_click(196, "点击继续")
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_灵塔：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_灵塔：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_lingta_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([194, 69, 20, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id is None and self._daily_lingta_text_is_main(text):
            scene_id = 194
        if scene_id == 194:
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：退出混沌灵塔", phase="daily_lingta_exit_main", current_scene=194)
                self._log_locked("action", "日常_灵塔：点击 #194「返回」")
            yield from runtime.wait_click(194, "返回")
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
            yield from runtime.wait_click(69, "退出")
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

    def _daily_xianyuan_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if re.search(r"仙缘斗法|斗法", normalized):
            return False
        match = re.search(r"(?:挑战\s*仙缘|仙缘人物)(.*)", normalized)
        if match:
            fraction = parse_ocr_values(match.group(1), expected_count=2, allow_extra_numbers=True)
            if fraction is None:
                return bool(re.search(r"(?:已完成|完成)", match.group(1)))
            current, total = fraction
            return total > 0 and current >= total
        return bool(re.search(r"(?:挑战\s*仙缘|仙缘人物).*?(?:已完成|完成)", normalized))

    def _daily_xianyuan_row_progress(
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
        if re.search(r"仙缘斗法|斗法", row_text):
            return None
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

    def _daily_xianyuan_entry_matches(self, lines: list[dict[str, Any]], image69: dict[str, Any]) -> list[tuple[float, float, str]]:
        matches: list[tuple[float, float, str]] = []
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            return matches
        box = self._box(list_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or re.search(r"仙缘斗法|斗法", text):
                continue
            if not re.search(r"(?:挑战\s*仙缘|仙缘人物)", text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and top <= cy <= bottom:
                progress = self._daily_xianyuan_row_progress(lines, cy)
                if progress is not None and progress[0] >= progress[1]:
                    continue
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _record_daily_xianyuan_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-xianyuan")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
        )
        self._log("success", f"日常_挑战仙缘：{message}，下次 {next_time}")
        return next_time

    def _daily_xianyuan_runtime_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        override = payload.get("runtime_task_snapshot")
        if isinstance(override, dict):
            return dict(override)
        return read_daily_task_runtime_snapshot(1008)

    def _execute_daily_xianyuan_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_挑战仙缘资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        task_snapshot = self._daily_xianyuan_runtime_snapshot(payload)
        self._log(
            "detail",
            "日常_挑战仙缘：Runtime 日常任务 "
            f"complete={bool(task_snapshot.get('complete'))} "
            f"status={task_snapshot.get('status')} "
            f"progress={task_snapshot.get('turn')}/{task_snapshot.get('target_turn')} "
            f"done={bool(task_snapshot.get('done'))} "
            f"elapsed={float(task_snapshot.get('elapsed_seconds') or 0):.2f}s",
        )
        if task_snapshot.get("complete") is True and task_snapshot.get("task_id") == 1008 and task_snapshot.get("done") is True:
            self._record_daily_xianyuan_done(payload, message="Runtime 已证明挑战仙缘任务完成")
            yield from runtime.goto_view(34)
            return "success"
        scene_id, _score, frame = runtime.current_scene(DAILY_XIANYUAN_LAYER0_SCENE_IDS, update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 197 and not self._daily_xianyuan_text_is_people_list(text):
            if self._daily_xianyuan_text_is_daily_list(text):
                scene_id = 69
            else:
                scene_id = None
        if scene_id in DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS:
            return (yield from self._run_daily_xianyuan_from_challenge_state(ctx, stop_event, payload, int(scene_id)))
        if scene_id == 199:
            return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
        if scene_id == 198:
            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
        if scene_id == 197:
            return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
        if scene_id != 69:
            if self._daily_xianyuan_text_is_dialogue(text):
                return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
            if self._daily_xianyuan_text_is_detail(text):
                return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
            if scene_id != 34:
                if self._daily_xianyuan_text_is_people_list(text):
                    return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_挑战仙缘")):
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
                    label="日常_挑战仙缘",
                )

        daily_status = yield from self._open_daily_xianyuan_from_daily(ctx, stop_event, payload)
        if daily_status == "done":
            self._record_daily_xianyuan_done(payload, message="日常列表显示已完成")
            yield from self._safe_daily_done_cleanup(
                lambda: self._return_daily_xianyuan_to_world(ctx, stop_event),
                label="日常_挑战仙缘",
                repeat_risk="重复挑战",
            )
            return "success"
        if daily_status == "not_found":
            raise RuntimeError("日常_挑战仙缘：未找到未完成入口，不能按完成处理")

        scene_id, _score = yield from self._wait_daily_xianyuan_after_entry(ctx, stop_event, payload)
        if scene_id in DAILY_XIANYUAN_CHALLENGE_LAYER0_SCENE_IDS:
            return (yield from self._run_daily_xianyuan_from_challenge_state(ctx, stop_event, payload, int(scene_id)))
        if scene_id == 199:
            return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
        if scene_id == 198:
            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
        if scene_id == 197:
            return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
        raise RuntimeError(f"日常_挑战仙缘：入口点击后回到 #{scene_id or 'unknown'}，尚未完成挑战流程，不能按完成处理")

    def daily_xianyuan_duel_admission(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        payload = dict(payload or {})
        task_id = str(payload.get("__scheduler_task_id") or "")
        if task_id != XIANYUAN_DUEL_TASK_ID:
            return None
        now = _now()
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([308, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id not in {308, 69}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, runtime, stop_event, frame, scene_id, text, label="仙缘斗法")
        if scene_id not in {308, 69}:
            raise RuntimeError("仙缘斗法：未能进入 #69 日常列表")
        if scene_id == 69:
            status = yield from runtime.open_daily_entry(
                label="仙缘斗法",
                title_pattern=r"斗\s*法",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
                self._record_daily_entry_not_found_retry(
                    payload,
                    task_id=scheduler_task_id,
                    task_type="daily_xianyuan_duel",
                    label="仙缘斗法",
                    entry_label="斗法",
                    seconds=int(payload.get("retry_seconds") or 60),
                )
                return "skipped"
        if not bool(payload.get("skip_purchase")):
            yield from self._prepare_daily_xianyuan_duel_purchases(runtime, payload)
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
                remaining = yield from self._read_daily_xianyuan_duel_remaining(runtime, payload)
            except RuntimeError as exc:
                if "无法从 #308[次数] 识别剩余次数" not in str(exc):
                    raise
                facts = facts or (yield from self._wait_current_daily_xianyuan_duel_facts(
                    runtime,
                    payload,
                    reason=f"round-{completed + 1}-ocr-fallback",
                    self_power_hint=stable_self_power,
                ))
                if facts is None:
                    return (yield from self._defer_daily_xianyuan_duel_runtime(
                        runtime,
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
                next_time = next_xianyuan_duel_cycle_trigger_at(_now()).strftime("%Y-%m-%d %H:%M:%S")
                self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
                self._log(
                    "success",
                    f"仙缘斗法：#308[次数] 已确认剩余为 0，本周期完成，下次 {next_time or '按既有日程'}",
                )
                return "success"
            facts = facts or (yield from self._wait_current_daily_xianyuan_duel_facts(
                runtime,
                payload,
                reason=f"round-{completed + 1}",
                self_power_hint=stable_self_power,
            ))
            if facts is None:
                return (yield from self._defer_daily_xianyuan_duel_runtime(
                    runtime,
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

            mapped = self._map_daily_xianyuan_duel_targets(runtime, facts, payload)
            chosen = choose_xianyuan_duel_target(mapped["targets"], self_power=int(facts["self_power"]))
            if chosen is None:
                refreshes = int(facts.get("remaining_refreshes") or 0)
                if refreshes > 0 and not refresh_used:
                    self._log("action", "仙缘斗法：3 个候选均无法稳妥挑战，使用今日唯一一次刷新")
                    yield from runtime.wait_click(308, "刷新")
                    refreshed = yield from self._wait_current_daily_xianyuan_duel_facts(
                        runtime,
                        payload,
                        reason="after-refresh",
                        previous=facts,
                        self_power_hint=stable_self_power,
                    )
                    if refreshed is None:
                        return (yield from self._defer_daily_xianyuan_duel_runtime(
                            runtime,
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
            yield from runtime.wait_click_then_view(308, str(chosen["challenge_shape"]), 309)
            formation_payload = {
                **payload,
                "__xianyuan_duel_facts": facts,
                "__xianyuan_duel_target": chosen,
            }
            yield from self._optimize_daily_xianyuan_duel_formation(runtime, formation_payload)
            # #308 is the preparation/opponent page, not a valid battle
            # landing.  It may flash during the transition, so the local
            # transaction must wait for the optional battle layer #345 or the
            # layer-0 result page #310.  Only clicking #310 may return to #308.
            try:
                view_after_start = yield from runtime.wait_click_then_view(
                    309,
                    "开始挑战",
                    [345, 310],
                    timeout=float(payload.get("battle_result_timeout") or 60.0),
                )
            except TimeoutError:
                recovery_scene_id, recovery_score, _recovery_frame = runtime.current_scene(
                    [308],
                    update=True,
                )
                if recovery_scene_id != 308:
                    raise
                recovery_remaining = yield from self._read_daily_xianyuan_duel_remaining(
                    runtime,
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
                    runtime,
                    payload,
                    reason=f"after-recovered-round-{completed}",
                    previous=facts,
                    self_power_hint=stable_self_power,
                )
                if pending_facts is None:
                    return (yield from self._defer_daily_xianyuan_duel_runtime(
                        runtime,
                        payload,
                        scheduler_task_id=scheduler_task_id,
                        reason=f"恢复第 {completed} 轮后 Runtime 未在等待窗口内推进",
                    ))
                continue
            if _lundao_waited_scene_id(view_after_start) == 345:
                view_after_start = yield from runtime.wait_click_then_view(345, "跳过", 310)
            yield from runtime.wait_click_then_view(310, "点击继续", 308)
            completed += 1
            pending_facts = yield from self._wait_current_daily_xianyuan_duel_facts(
                runtime,
                payload,
                reason=f"after-round-{completed}",
                previous=facts,
                self_power_hint=stable_self_power,
            )
            if pending_facts is None:
                return (yield from self._defer_daily_xianyuan_duel_runtime(
                    runtime,
                    payload,
                    scheduler_task_id=scheduler_task_id,
                    reason=f"第 {completed} 轮后 Runtime 未在等待窗口内推进",
                ))
        raise RuntimeError(
            f"仙缘斗法：达到单轮安全上限 {max_runs}，已挑战 {completed} 次但 #308[次数] 仍大于 0"
        )

    def _execute_daily_mojie_raid_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_奇袭魔界资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        raid_scenes = {319, 320, 321, 322, 323, 324}
        joined_existing_team = False
        scene_id, _score, frame = runtime.current_scene([331, 330, *sorted(raid_scenes), 69, 34, 20], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 331:
            # #331 is the already-joined team page.  Whether it was reached after
            # a committed #324 transaction or after replaying #320 against an
            # already-changed game state, the current trigger must not attack
            # again.  Persist the next legal trigger before low-risk cleanup and
            # report success: the business effect for this trigger already exists.
            self._schedule_next_mojie_raid_trigger(
                payload,
                reason="起点已处于 #331「已加入」状态，本轮业务已完成",
            )
            yield from runtime.goto_view(34)
            return "success"
        if scene_id == 330:
            scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(runtime)
        if scene_id not in {69, *raid_scenes}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, runtime, stop_event, frame, scene_id, text, label="日常_奇袭魔界")
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
            status = yield from runtime.open_daily_entry(
                label="日常_奇袭魔界",
                title_pattern=r"参与.{0,4}奇|奇.{0,4}魔|魔界",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
            )
            if status == "not_found":
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
                yield from runtime.wait_action_settle(float(payload.get("entry_pause_settle_seconds") or 1.5))
                current_scene_id, score, frame = runtime.current_scene(update=True)
                text = runtime.ocr_text(frame)
                scene_label = f"#{current_scene_id}" if current_scene_id is not None else "unknown"
                self._log(
                    "warning",
                    f"日常_奇袭魔界：已点击日常入口，按调试要求暂停；当前 {scene_label} {score:.0f}%，OCR={text[:120]}",
                )
                return "skipped"
            try:
                waited = yield from runtime.wait_view(319, 330, label="日常_奇袭魔界：等待奇袭魔界 #319")
                if getattr(waited, "id", waited) == 330:
                    scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(runtime)
                else:
                    scene_id = 319
            except TimeoutError as exc:
                yield from self._handle_daily_mojie_raid_open_blocker_placeholder(runtime, payload)
                raise RuntimeError("日常_奇袭魔界：入口点击后未到达 #319，疑似遇到未实现的特殊弹窗") from exc
        if scene_id == 319:
            self._log("success", "日常_奇袭魔界：已到达 #319")
            existing_team_match = None
            shape_matches = getattr(runtime, "shape_matches", None)
            if callable(shape_matches):
                existing_team_match = shape_matches(319, "队伍")
            if existing_team_match:
                # “我的队伍”只证明本轮已经入队，不证明本周次数耗尽。
                # 先提交下一轮触发，再低风险返回世界；不得继续点击“参与进攻”。
                self._schedule_next_mojie_raid_trigger(
                    payload,
                    reason="#319 已显示「我的队伍」，本轮业务已完成",
                )
                yield from runtime.wait_click_then_view(319, "返回", 34)
                return "success"
            numbers, text = runtime.ocr_numbers_in_shapes(
                319,
                ("剩余次数",),
                padding=int(payload.get("mojie_raid_remaining_padding") or 16),
            )
            if not numbers and "确定" in str(text or ""):
                self._log(
                    "info",
                    "日常_奇袭魔界：底层 #319 被前置「确定」浮层覆盖；"
                    "先完成既有 #330 确认闭环，再重新检查「我的队伍」",
                )
                scene_id = yield from self._confirm_daily_mojie_raid_reward_confirmation(runtime)
                if scene_id == 319:
                    existing_team_match = shape_matches(319, "队伍") if callable(shape_matches) else None
                    if existing_team_match:
                        self._schedule_next_mojie_raid_trigger(
                            payload,
                            reason="#330 确认后 #319 已显示「我的队伍」，本轮业务已完成",
                        )
                        yield from runtime.wait_click_then_view(319, "返回", 34)
                        return "success"
                    numbers, text = runtime.ocr_numbers_in_shapes(
                        319,
                        ("剩余次数",),
                        padding=int(payload.get("mojie_raid_remaining_padding") or 16),
                    )
            anchored_remaining = self._daily_mojie_raid_remaining_ocr_fallback(text)
            if anchored_remaining is not None:
                if numbers and int(numbers[0]) != anchored_remaining:
                    self._log(
                        "warning",
                        "日常_奇袭魔界：#319 数字裁剪受邻近属性值污染，"
                        f"通用数字={int(numbers[0])}，按「剩余进攻次数」语义锚定={anchored_remaining}",
                    )
                remaining = anchored_remaining
            elif numbers:
                remaining = int(numbers[0])
            else:
                raise RuntimeError(f"日常_奇袭魔界：未能读取 #319「剩余次数」，OCR={text[:120]}")
            self._log("detail", f"日常_奇袭魔界：剩余次数 {remaining}，OCR={text[:80]}")
            if remaining <= 0:
                # 单帧 OCR 的 0 会直接把整个作业推进到下周，代价过高。
                # 等待后重新取一张真实帧确认；若第二帧恢复为正数，继续本周流程。
                confirm_seconds = float(payload.get("mojie_raid_zero_confirm_seconds") or 2.0)
                self._log("warning", "日常_奇袭魔界：首次读到剩余次数 0，等待新帧复核后再结束本周")
                yield from runtime.wait_action_settle(confirm_seconds)
                confirm_numbers, confirm_text = runtime.ocr_numbers_in_shapes(
                    319,
                    ("剩余次数",),
                    padding=int(payload.get("mojie_raid_remaining_padding") or 16),
                )
                anchored_confirm = self._daily_mojie_raid_remaining_ocr_fallback(confirm_text)
                if anchored_confirm is not None:
                    remaining = anchored_confirm
                elif confirm_numbers:
                    remaining = int(confirm_numbers[0])
                else:
                    raise RuntimeError(
                        f"日常_奇袭魔界：首次读到 0，但新帧未能复核 #319「剩余次数」，OCR={confirm_text[:120]}"
                    )
                self._log("detail", f"日常_奇袭魔界：剩余次数复核 {remaining}，OCR={confirm_text[:80]}")
            if remaining <= 0:
                self._schedule_next_mojie_raid_week(
                    payload,
                    reason="连续两帧确认剩余次数为 0，本周已完成",
                )
                yield from runtime.wait_click(319, "返回")
                return "success"
            yield from runtime.wait_click_then_view(319, "参与进攻", 320)
            scene_id = 320
        else:
            self._log("detail", f"日常_奇袭魔界：从 #{scene_id} 恢复后续流程")
        if scene_id == 320:
            countdown_text = runtime.ocr_text_in_shapes(
                320,
                ("进攻倒计时标识",),
                padding=int(payload.get("mojie_raid_attack_countdown_padding") or 12),
            )
            countdown_seconds = self._daily_mojie_raid_attack_countdown_seconds(countdown_text)
            if countdown_seconds is None:
                raise RuntimeError(
                    "日常_奇袭魔界：#320 未能唯一解析「进攻倒计时」HH:MM:SS，"
                    f"拒绝猜测点击，OCR={countdown_text[:120]}"
                )
            if countdown_seconds > 0:
                return (yield from self._defer_daily_mojie_raid_attack_countdown(
                    runtime,
                    payload,
                    countdown_seconds=countdown_seconds,
                    countdown_text=countdown_text,
                ))
            try:
                scene_id = yield from self._click_daily_mojie_raid_top_attack_target(runtime, payload)
            except _DailyMojieRaidAttackCountdown as countdown:
                return (yield from self._defer_daily_mojie_raid_attack_countdown(
                    runtime,
                    payload,
                    countdown_seconds=countdown.seconds,
                    countdown_text=countdown.text,
                ))
            if scene_id == 331:
                # A previous/current join can make the #320 target click land
                # directly on the already-joined page, skipping #321..#324.
                # This direct transition is authoritative transaction-local
                # evidence.  Commit scheduling before cleanup so a navigation
                # failure cannot make the non-replayable action due again.
                self._schedule_next_mojie_raid_trigger(
                    payload,
                    reason="点击据点后已处于 #331「已加入」状态，本轮业务已完成",
                )
                yield from runtime.click_shape_center_then_view(331, "返回", 320)
                yield from runtime.wait_click(320, "返回")
                yield from runtime.wait_click_then_view(319, "返回", 34)
                return "success"
        if scene_id == 321:
            # #322 is only a confirmation popup, but a persistent #321 can mean
            # the server-side weekly attack allowance is already exhausted or
            # another business guard rejected the request.  Re-clicking cannot
            # distinguish those states and may duplicate a delayed UI action.
            yield from runtime.wait_click_then_view(321, "创建队伍", 322, max_clicks=1)
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
            team_numbers, team_text = runtime.ocr_numbers_in_shapes(
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
                yield from runtime.wait_click_then_view(322, "返回", 321)
                joined_scene = yield from self._join_daily_mojie_raid_friendly_team(runtime, payload)
                if joined_scene is None:
                    yield from runtime.click_shape_center_then_view(321, "返回", 320)
                    yield from runtime.wait_click(320, "返回")
                    yield from runtime.wait_click_then_view(319, "返回", 34)
                    self._schedule_next_mojie_raid_trigger(
                        payload,
                        reason=f"#322 建队额度已满 {team_count}/{team_limit}，#321 暂无可加入友方队伍",
                    )
                    return "skipped"
                scene_id = joined_scene
                joined_existing_team = True
            else:
                yield from runtime.wait_click_then_view(322, "下拉选项", 323)
                scene_id = 323
        if scene_id == 323:
            yield from runtime.wait_click_then_view(323, "开启", 322)
            # 创建队伍的“确定”输入历史上经常不生效；#322 仍被可靠识别时，
            # 继续补点同一已标注按钮。重试必须有界，且一旦离开 #322 或
            # 进入 unknown，wait_click_then_view 会立即停止，不能盲目连点。
            yield from runtime.wait_click_then_view(
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
            self._schedule_next_mojie_raid_trigger(payload, reason=committed_reason)
            # 建队成功后的队伍页会在短暂动画结束后让 #324 的「鼓舞」
            # 图像身份失效；此时再用 wait_click 会永远等不到源场景。
            # 「返回」本身是固定标注坐标，直接点击，并兼容实机可能跳到
            # 中间页 #331 或直接回到世界 #34 两种落点。
            landed = yield from runtime.click_shape_center_then_view(324, "返回", 331, 34)
            scene_id = int(getattr(landed, "id", landed))
            if scene_id == 34:
                return "success"
        if scene_id == 331:
            yield from runtime.click_shape_center_then_view(331, "返回", 320)
            yield from runtime.wait_click(320, "返回")
            yield from runtime.wait_click_then_view(319, "返回", 34)
        return "success"

    def _daily_mojie_raid_join_click_delta(
        self,
        runtime: FanxiuRuntimeSession,
    ) -> tuple[float, float]:
        """Read the roleItem click offset from #321 assets instead of hard-coding pixels."""

        anchor = runtime.shape(321, "友方加入锚点")
        click_target = runtime.shape(321, "友方人物点击点")
        view = runtime.view(321)
        width, height = runtime.runner._frame_size(view.raw)

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
        runtime: FanxiuRuntimeSession,
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
        dx, dy = self._daily_mojie_raid_join_click_delta(runtime)
        attempted = 0
        seen_pages: set[tuple[str, ...]] = set()

        for scroll_index in range(max_scrolls + 1):
            frame = runtime.cur_frame(update=True)
            # Restrict both candidate discovery and page identity to the
            # annotated horizontal team window.  A tuple of only ``3/5`` /
            # ``5/5`` counters is not a page identity: different pages often
            # have the same member-count distribution and would stop scanning
            # too early.
            fragments = runtime.ocr_fragments_in_shapes(
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
                runtime.click_frame_point(321, x, y)
                try:
                    # #508 is the formally annotated "是否加入该队伍" business
                    # scene.  It must be the Layer-0 target of this transition;
                    # a one-shot full-frame OCR probe can miss the animated popup
                    # and used to leave it open while the task kept scrolling #321.
                    yield from runtime.wait_view(
                        508,
                        timeout=float(payload.get("mojie_raid_join_popup_timeout_seconds") or 8.0),
                        label="日常_奇袭魔界：等待加入队伍确认 #508",
                    )
                except RuntimeError:
                    self._log(
                        "warning",
                        "日常_奇袭魔界：人物卡片点击后未识别到 #508 入队确认，尝试下一候选",
                    )
                    continue

                runtime.click_shape_center(508, "确认")
                try:
                    yield from runtime.wait_view(
                        324,
                        timeout=float(payload.get("mojie_raid_join_result_timeout_seconds") or 12.0),
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
            changed = yield from runtime.scroll_shape_content(
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
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
    ):
        target_shape = str(payload.get("mojie_raid_target_shape") or "检索区域/修罗").strip()
        match_timeout = float(
            payload.get("mojie_raid_target_match_timeout")
            or getattr(runtime, "default_wait_click_timeout", self._daily_default_wait_condition_timeout)
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
            yield from runtime.wait_click(
                320,
                target_shape,
                timeout=match_timeout,
                x_ratio=click_x_ratio,
                y_ratio=click_y_ratio,
            )
            yield from runtime.wait_action_settle(settle_seconds)
            try:
                waited = yield from runtime.wait_view(
                    321,
                    331,
                    timeout=wait_timeout,
                    label="日常_奇袭魔界：点击 #320 修罗据点后等待 #321/#331",
                )
                return int(getattr(waited, "id", waited) or 0)
            except TimeoutError as exc:
                last_error = exc
                scene_id, score, _frame = runtime.current_scene(
                    [320, 321, 331],
                    update=True,
                    handle_interruptions=False,
                )
                if scene_id == 320:
                    countdown_text = runtime.ocr_text_in_shapes(
                        320,
                        ("进攻倒计时标识",),
                        padding=int(payload.get("mojie_raid_attack_countdown_padding") or 12),
                    )
                    countdown_seconds = self._daily_mojie_raid_attack_countdown_seconds(countdown_text)
                    if countdown_seconds is None:
                        raise RuntimeError(
                            "日常_奇袭魔界：目标点击后仍在 #320，但未能唯一解析「进攻倒计时」"
                            f"HH:MM:SS，拒绝第二次点击，OCR={countdown_text[:120]}"
                        ) from exc
                    if countdown_seconds > 0:
                        raise _DailyMojieRaidAttackCountdown(
                            countdown_seconds,
                            countdown_text,
                        ) from exc
                if scene_id != 320 or attempt >= max_clicks:
                    raise
                self._log(
                    "warning",
                    f"日常_奇袭魔界：点击修罗据点后仍在 #320 {score:.0f}%，重新定位后重试",
                )

        raise last_error or TimeoutError("日常_奇袭魔界：点击 #320 修罗据点后未进入 #321")

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
            r"进攻倒计时\s*[:：]?\s*([0-9]{1,3})\s*[:：]\s*([0-9]{1,2})\s*[:：]\s*([0-9]{1,2})",
            normalized,
        )
        if len(matches) != 1:
            return None
        hours, minutes, seconds = (int(item) for item in matches[0])
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds

    def _defer_daily_mojie_raid_attack_countdown(
        self,
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
        *,
        countdown_seconds: int,
        countdown_text: str,
    ):
        self._schedule_next_mojie_raid_countdown(
            payload,
            countdown_seconds=countdown_seconds,
            reason=(
                "#320 仍处于进攻开放倒计时 "
                f"{countdown_text.strip()}，据点当前不可交互"
            ),
        )
        yield from runtime.wait_click(320, "返回")
        yield from runtime.wait_click_then_view(319, "返回", 34)
        return "skipped"

    def _next_mojie_raid_week_start_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        now = now or _behavior_tree_runtime._now()
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7
        next_monday = now + timedelta(days=days_until_next_monday)
        return next_monday.replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _next_mojie_raid_followup_time_text(
        self,
        now: datetime | None = None,
    ) -> str:
        """Choose the next 13:00/21:30 check until rewards reach zero."""

        current = now or _behavior_tree_runtime._now()
        for hour, minute in ((13, 0), (21, 30)):
            candidate = current.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate > current:
                return candidate.strftime("%Y-%m-%d %H:%M:%S")
        return (
            current + timedelta(days=1)
        ).replace(
            hour=13,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M:%S")

    def _schedule_next_mojie_raid_week(
        self,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> str:
        next_time = self._next_mojie_raid_week_start_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
            next_time,
        )
        self._log("success", f"日常_奇袭魔界：{reason}，下次 {next_time}")
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

    def _schedule_next_mojie_raid_countdown(
        self,
        payload: dict[str, Any],
        *,
        countdown_seconds: int,
        reason: str,
    ) -> str:
        """Schedule from the authoritative #320 countdown, plus a small UI safety margin."""

        safety_seconds = max(1, int(payload.get("mojie_raid_countdown_safety_seconds") or 60))
        next_time = (
            _behavior_tree_runtime._now()
            + timedelta(seconds=max(1, int(countdown_seconds)) + safety_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid")
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        self._log("success", f"日常_奇袭魔界：{reason}，按真实倒计时复查，下次 {next_time}")
        return next_time

    def _handle_daily_mojie_raid_open_blocker_placeholder(
        self,
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
    ):
        del runtime, payload
        self._log("warning", "日常_奇袭魔界：特殊弹窗处理占位，等待后续补标/补流程")
        if False:
            yield None
        return "not_implemented"

    def _confirm_daily_mojie_raid_reward_confirmation(
        self,
        runtime: FanxiuRuntimeSession,
    ):
        self._log("action", "日常_奇袭魔界：检测到 #330 前置奖励确认，点击「确定」后继续等待 #319")
        waited = yield from runtime.wait_click_then_view(
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([327, 326, 325, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id not in {327, 326, 325, 69}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, runtime, stop_event, frame, scene_id, text, label="日常_周本")
        if scene_id not in {327, 326, 325, 69}:
            raise RuntimeError("日常_周本：未能进入 #69 日常列表")
        if scene_id == 69:
            status = yield from runtime.open_daily_entry(
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
            scene_id = yield from self._open_daily_weekly_dungeon_tiangong_view(runtime, payload)
        if scene_id == 326:
            scene_id = yield from self._open_daily_weekly_dungeon_challenge_view(runtime, payload)
        if scene_id == -1:
            return "success"
        yield from runtime.wait_click(327, "挑战")
        yield from runtime.wait_view(
            34,
            timeout=float(payload.get("battle_return_world_timeout") or 600.0),
            label="日常_周本：等待战斗结束回到世界 #34",
        )
        self._record_daily_weekly_dungeon_done(
            payload,
            message="战斗结束，已回到 #34",
        )
        return "success"

    def _daily_weekly_dungeon_next_time_text(self, payload: dict[str, Any]) -> str:
        now = _now()
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
        runtime: FanxiuRuntimeSession,
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
                yield from runtime.wait_click_then_view(
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
                scene_id, score, frame = runtime.current_scene([326, 325, 69], update=True)
                text = runtime.ocr_text(frame)
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
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("tiangong_challenge_max_attempts") or 3))
        wait_timeout = float(payload.get("tiangong_challenge_wait_timeout") or 10.0)
        settle_seconds = float(payload.get("tiangong_challenge_settle_seconds") or 1.5)
        pre_click_wait = max(0.0, float(payload.get("tiangong_challenge_pre_click_wait") or 6.0))
        last_error: TimeoutError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                yield from runtime.wait_view(326, timeout=wait_timeout, label="日常_周本：确认 #326 玉霄天宫页")
                if pre_click_wait > 0:
                    self._log("wait", f"日常_周本：等待 #326 浮动战报消失 {pre_click_wait:.1f}s")
                    yield from runtime.wait_action_settle(pre_click_wait)
                try:
                    text = runtime.ocr_text(update=True)
                except TypeError:
                    text = runtime.ocr_text(runtime.cur_frame(update=True) if hasattr(runtime, "cur_frame") else None)
                remaining = self._daily_weekly_dungeon_remaining_count(text)
                if remaining is not None:
                    self._log("detail", f"日常_周本：#326 本周剩余奖励次数 {remaining}，OCR={text[:80]}")
                    if remaining <= 0:
                        self._record_daily_weekly_dungeon_done(payload, message="#326 显示本周剩余奖励次数为 0")
                        return -1
                yield from runtime.wait_click_then_view(
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
                scene_id, score, frame = runtime.current_scene([327, 326], update=True)
                text = runtime.ocr_text(frame)
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
        runtime: FanxiuRuntimeSession,
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
            yield from runtime.wait_action_settle(
                min(poll_seconds, max(0.2, timeout - elapsed))
            )

    def _defer_daily_xianyuan_duel_runtime(
        self,
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
        *,
        scheduler_task_id: str,
        reason: str,
    ):
        retry_seconds = max(60, int(payload.get("retry_seconds") or 60))
        next_time = (_now() + timedelta(seconds=retry_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
        self._log("skip", f"仙缘斗法：{reason}，{next_time} 安全复查")
        try:
            yield from runtime.goto_view(34)
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as exc:
            self._log("warning", f"仙缘斗法：已保存安全复查时间，但返回世界未完成：{exc}")
        return "skipped"

    def _map_daily_xianyuan_duel_targets(
        self,
        runtime: FanxiuRuntimeSession,
        facts: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        scene_id, score, frame = runtime.current_scene([308], update=True)
        if scene_id != 308:
            raise RuntimeError(f"仙缘斗法：选人要求当前为 #308，实际 #{scene_id or 'unknown'} {score:.0f}%")
        ocr_names = [
            runtime.ocr_text_in_shapes(
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
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("remaining_ocr_attempts") or 6))
        padding = max(0, int(payload.get("remaining_ocr_padding") or 8))
        retry_seconds = max(0.2, float(payload.get("remaining_ocr_retry_seconds") or 1.0))
        max_remaining = max(0, int(payload.get("max_runs") or 7))
        last_text = ""
        for attempt in range(max_attempts):
            numbers, last_text = runtime.ocr_numbers_in_shapes(308, ("次数",), padding=padding)
            if not numbers:
                crop_numbers, crop_text = runtime.ocr_numbers_in_shapes(
                    308,
                    ("次数",),
                    padding=padding,
                    crop=True,
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
                            yield from runtime.wait_action_settle(0.8)
                            continue
                        raise RuntimeError(
                            "仙缘斗法：#308[次数] OCR 超出单周期安全上限，"
                            f"识别={remaining}、上限={max_remaining}、OCR={last_text[:120]}"
                        )
                self._log("detail", f"仙缘斗法：#308[次数] 剩余 {remaining}，OCR={last_text[:80]}")
                return remaining
            if attempt + 1 < max_attempts:
                yield from runtime.wait_action_settle(retry_seconds)
        raise RuntimeError(f"仙缘斗法：无法从 #308[次数] 识别剩余次数，最后 OCR={last_text[:120]}")

    def _prepare_daily_xianyuan_duel_purchases(self, runtime: FanxiuRuntimeSession, payload: dict[str, Any]):
        try:
            yield from runtime.wait_click_then_view(
                308,
                "购买",
                311,
                timeout=float(payload.get("purchase_open_timeout") or 8.0),
                max_clicks=int(payload.get("purchase_open_max_clicks") or 1),
            )
        except TimeoutError:
            scene_id, score, frame = runtime.current_scene([311, 308], update=True)
            if scene_id == 311:
                pass
            elif scene_id == 308:
                text = runtime.ocr_text(frame)
                raise RuntimeError(
                    "仙缘斗法：#308 购买入口未打开，无法证明当日 100/200 灵石档已购。"
                    "必须进入 #311 并确认下一档价格为 300 灵石后才能幂等继续；"
                    f"当前 #308 {score:.0f}%，OCR={text[:80]}"
                )
            else:
                raise
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
                numbers, text = runtime.ocr_numbers_in_shapes(311, ("价格",), padding=16)
                if numbers:
                    break
                yield from runtime.wait_action_settle(0.8)
            if not numbers:
                continue
            price = numbers[0]
            if last_purchased_price is not None and price <= last_purchased_price:
                raise RuntimeError(
                    "仙缘斗法：购买动作后价格未向下一档推进，"
                    f"上一档 {last_purchased_price}，当前 {price}，拒绝重放灵石购买"
                )
            if price == stop_price:
                yield from runtime.wait_click_then_view(311, "返回", 308)
                return
            if price not in expected_purchase_prices:
                raise RuntimeError(
                    "仙缘斗法：购买页价格不属于 100/200 可购档或 "
                    f"{stop_price} 灵石幂等停止档，当前价格 {price}"
                )
            self._log("action", f"仙缘斗法：购买斗法次数，价格 {price}")
            yield from runtime.wait_click(311, "购买")
            last_purchased_price = int(price)
            yield from runtime.wait_action_settle(1.0)
            scene_id, score, frame = runtime.current_scene([311, 308], update=True)
            if scene_id == 308:
                text = runtime.ocr_text(frame)
                self._log(
                    "detail",
                    "仙缘斗法：购买后入口关闭并返回 #308，"
                    f"重新打开购买页确认下一档为 {stop_price} 灵石；"
                    f"当前 #308 {score:.0f}%，OCR={text[:80]}",
                )
                try:
                    yield from runtime.wait_click_then_view(
                        308,
                        "购买",
                        311,
                        timeout=float(payload.get("purchase_open_timeout") or 8.0),
                        max_clicks=int(payload.get("purchase_open_max_clicks") or 1),
                    )
                except TimeoutError as exc:
                    raise RuntimeError(
                        "仙缘斗法：购买后返回 #308，但重新打开 #311 失败，"
                        f"无法确认下一档为 {stop_price} 灵石"
                    ) from exc
                continue
            if scene_id != 311:
                raise RuntimeError(
                    "仙缘斗法：购买后未停留在 #311 或返回 #308，"
                    f"实际 #{scene_id or 'unknown'} {score:.0f}%"
                )
        raise RuntimeError(f"仙缘斗法：购买页价格识别失败或未达到停止价格，最后识别文本：{text if 'text' in locals() else ''}")

    def _optimize_daily_xianyuan_duel_formation(self, runtime: FanxiuRuntimeSession, payload: dict[str, Any]):
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
        scene_id, score, frame = runtime.current_scene([309], update=True)
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
            runtime.drag_shape_to_shape(
                309,
                f"拖拽锚点{start_slot}",
                f"拖拽锚点{end_slot}",
                duration=drag_duration,
                frame_data_url=frame,
            )
            yield from runtime.wait_action_settle(settle_seconds)
        elapsed = time.monotonic() - start_ts
        my_labels = [XIANYUAN_CAREER_LABELS[int(value)] for value in best["careers"]]
        enemy_labels = [XIANYUAN_CAREER_LABELS[int(value)] for value in best["enemy_careers"]]
        self._log(
            "action",
            "仙缘斗法：按结构化阵容完成优化，"
            f"敌方={'/'.join(enemy_labels)}，我方={'/'.join(my_labels)}，"
            f"调整{len(swaps)}次，耗时{elapsed:.1f}s",
        )

    def _read_daily_xianyuan_duel_formation_state(self, runtime: FanxiuRuntimeSession) -> dict[str, Any]:
        from PIL import Image, ImageChops, ImageStat

        scene_id, score, frame = runtime.current_scene([309], update=True)
        if scene_id != 309:
            raise RuntimeError(f"仙缘斗法：阵容优化要求当前为 #309，实际为 #{scene_id or 'unknown'} {score:.0f}%")
        image309 = runtime.view(309).raw
        entry = runtime.ctx.get("entry") if isinstance(runtime.ctx, dict) else None
        entry_id = str(getattr(entry, "entry_id", "") or "")
        filename = str(image309.get("filename") or "")
        if not entry_id or not filename:
            raise RuntimeError("仙缘斗法：缺少 #309 参考图，无法识别阵容")
        ref_path = data_annotation_entry_image_dir(entry_id) / filename
        ref_image = Image.open(ref_path).convert("RGB")
        cur_image = Image.open(io.BytesIO(runtime.runner._decode_frame_data_url(frame))).convert("RGB")

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
        # Progress is a two-value OCR group; its visual separator is irrelevant.
        if parse_ocr_values(text, expected_count=2) is not None:
            text = re.sub(r"\d+", " ", text)
        text = re.sub(r"[◎。·•●○\s]+", " ", text)
        text = re.sub(r"(?:活|次|次数|活跃度|未占领|未入座|前往|扫荡|挑战)", " ", text)
        return re.sub(r"\s+", "", text).strip()[:40]

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
        return current >= total or "已完成" in _sanitize_ocr_text(row_text)

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

        progress_centers: list[float] = []
        for line in visible_lines:
            text = str(line.get("_text") or "")
            if parse_ocr_values(text, expected_count=2) is not None:
                cy = float(line.get("_cy") or 0)
                if all(abs(cy - existing) > 45.0 for existing in progress_centers):
                    progress_centers.append(cy)

        rows: list[dict[str, Any]] = []
        for center in sorted(progress_centers):
            progress_lines = [
                line
                for line in visible_lines
                if -float(y_tolerance) <= float(line.get("_cy") or 0) - center <= 45.0
            ]
            fragments = [str(line.get("_text") or "") for line in progress_lines]
            row_text = "".join(fragments)
            # The next task title may contain its own count (for example
            # “完成…1次”).  Limit this audit call to the visual block ending at
            # the current progress line before invoking the shared parser.
            progress = self._daily_task_row_progress(progress_lines, center, y_tolerance=y_tolerance)
            if progress is None:
                continue
            current, total = progress
            title = self._daily_audit_normalize_title(row_text)
            identity = self._daily_audit_task_identity(row_text)
            task_type = (identity or {}).get("task_type") or ""
            rows.append({
                "title": title or row_text[:40],
                "text": row_text,
                "progress": {"current": current, "total": total},
                "done": self._daily_audit_row_done(task_type=task_type, current=current, total=total, row_text=row_text),
                "task_type": task_type,
                "task_id": (identity or {}).get("task_id") or "",
                "center_y": center,
            })
        return rows

    def _merge_daily_audit_rows(self, rows: list[dict[str, Any]], next_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_key: dict[str, dict[str, Any]] = {}
        for row in [*rows, *next_rows]:
            task_key = str(row.get("task_id") or row.get("task_type") or "").strip()
            progress = row.get("progress") if isinstance(row.get("progress"), dict) else {}
            fallback_key = f"{row.get('title') or row.get('text') or ''}:{progress.get('total') or ''}"
            key = task_key or fallback_key
            if key and key not in by_key:
                by_key[key] = row
        return list(by_key.values())

    def _record_daily_audit_result(self, audit: dict[str, Any]) -> None:
        facts = _read_data_annotation_world_facts()
        discoveries = facts.setdefault("discoveries", {})
        if not isinstance(discoveries, dict):
            discoveries = {}
            facts["discoveries"] = discoveries
        discoveries["daily_audit"] = audit
        _write_data_annotation_world_facts(facts)

    def _execute_daily_audit_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        image69 = (ctx.get("images") or {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法遍历日常列表")

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
                label="日常_复核",
            )
        if scene_id != 69:
            raise RuntimeError("日常_复核：未能进入 #69 日常页，无法读取次数")

        max_scrolls = self._payload_int(payload, "max_scrolls", default=12)

        rows: list[dict[str, Any]] = []
        for index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked("running", f"日常_复核：读取日常列表 {index + 1}/{max_scrolls + 1}", phase="daily_audit_scan", current_scene=69)
            frame = runtime.cur_frame(update=True)
            lines = self._ocr_fragments_in_scene_shapes(ctx, frame, image69)
            self._ensure_daily_list_frame(ctx, frame, lines, task_label="日常_复核")
            rows = self._merge_daily_audit_rows(rows, self._daily_audit_visible_rows(lines, image69))
            if index >= max_scrolls:
                break
            changed = yield from runtime.scroll_shape_content(view69, list_shape, direction="down")
            if not changed:
                break

        incomplete = [row for row in rows if not bool(row.get("done"))]
        completed = [row for row in rows if bool(row.get("done"))]
        mapped_incomplete = [row for row in incomplete if str(row.get("task_id") or "")]
        unmapped_incomplete = [row for row in incomplete if not str(row.get("task_id") or "")]
        mapped_completed = [row for row in completed if str(row.get("task_id") or "")]
        unmapped_completed = [row for row in completed if not str(row.get("task_id") or "")]
        audit = {
            "updated_at": time.time(),
            "updated_at_text": _now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_scene": 69,
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
            "message": f"日常页复核：读取 {len(rows)} 条，已完成 {len(completed)} 条，未完成 {len(incomplete)} 条，未完成已映射 {len(mapped_incomplete)} 条",
        }
        self._record_daily_audit_result(audit)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, frame_data_url=frame)
        scene_id, score, _frame = runtime.current_scene([69, 34], frame_data_url=frame)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_scrolls = self._payload_int(payload, "max_scrolls", default=30)
        return (yield from runtime.open_daily_entry(
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
        now = _behavior_tree_runtime._now()
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, frame = runtime.current_scene([69, 34], update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text or last_text
            if scene_id in {69, 34}:
                return scene_id, float(score), last_text
            if time.monotonic() - start >= timeout:
                if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                    scene_id, score, frame = runtime.current_scene([69, 34], update=True)
                    text = runtime.ocr_text(frame)
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

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
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

    def daily_lundao_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = _behavior_tree_runtime._now()
        if lundao_safety_threshold(now) is not None:
            return None
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": (
                "论道_座位：当前不在 "
                f"{LUNDAO_FIRST_TRIGGER.strftime('%H:%M')}-{LUNDAO_CLOSE_TIME.strftime('%H:%M')} "
                "运行窗口，未执行游戏操作"
            ),
            "next_time": next_lundao_daily_trigger(now).strftime("%Y-%m-%d %H:%M:%S"),
            "current_scene": None,
        })

    def _execute_daily_lundao_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少论道_座位资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)

        scene_id, _score, frame = runtime.current_scene([69, 34, 296, 297, 298, 371, 372, 373, 375, 329, 301, 302, 303, 304, 391, 52, 53, 54], update=True)
        # Candidate-set scoring can project a real world frame onto #69 when
        # current-scene closure candidates are included.  Before treating that broad result as
        # authorization to scroll the daily list, arbitrate #69 vs #34 again
        # on the exact same frame.  The list guard remains the final barrier.
        if scene_id == 69:
            anchored_scene_id, anchored_score, _ = runtime.current_scene(
                [69, 34],
                frame_data_url=frame,
            )
            if anchored_scene_id in {69, 34}:
                scene_id, _score = anchored_scene_id, anchored_score
        text = runtime.ocr_text(frame)
        if scene_id in {34, 69}:
            runtime_guard = yield from self._daily_lundao_world_runtime_guard(
                runtime,
                payload,
                scene_id=scene_id,
            )
            if runtime_guard is not None:
                return runtime_guard
        if scene_id == 54 or self._daily_lundao_text_is_exit_confirm(text):
            result = yield from self._confirm_daily_lundao_exit_to_world(runtime)
            return self._finish_daily_lundao_current_scene_action(
                payload,
                result,
                reason="从当前退出确认状态收口完成",
            )
        if self._daily_lundao_text_is_reward(text):
            scene_id = 52
        if scene_id in {297, 298, 371, 372, 373, 375}:
            result = yield from self._run_daily_lundao_seat_and_leave(runtime, stop_event, payload=payload)
            return self._finish_daily_lundao_current_scene_action(
                payload,
                result,
                reason=f"从当前中间场景 #{scene_id} 收口完成",
            )
        if scene_id in {329, 301, 302, 303, 52, 53}:
            result = yield from self._complete_daily_lundao_seat_and_leave(runtime, stop_event, scene_id)
            return self._finish_daily_lundao_current_scene_action(
                payload,
                result,
                reason=f"从当前入座收尾场景 #{scene_id} 收口完成",
            )
        if scene_id == 391:
            entry_result = yield from self._dismiss_daily_lundao_kicked(runtime)
            scene_id = entry_result.get("scene_id")
        if scene_id == 304:
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(
                runtime,
                stop_event,
                payload,
                daluo_source_scene_id=304,
            ))
        if scene_id == 296:
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(runtime, stop_event, payload))
        if scene_id != 69:
            scene_id = yield from self._enter_daily_from_world_like(ctx, runtime, stop_event, frame, scene_id, text, label="论道_座位")
        if scene_id != 69:
            raise RuntimeError("论道_座位：未能进入 #69 日常列表")
        entry_result = yield from self._enter_daily_lundao_and_route_state(
            ctx,
            stop_event,
            payload,
            runtime,
        )
        if entry_result["status"] == "not_found":
            return self._finish_daily_lundao_unseated_retry(
                payload,
                reason="#69 暂未找到论道入口，首次落座前立即重试",
            )
        if entry_result["status"] == "kicked":
            entry_result = yield from self._dismiss_daily_lundao_kicked(runtime)
        scene_id = entry_result.get("scene_id")
        score = float(entry_result.get("score") or 0.0)
        if entry_result["status"] == "in_progress":
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(
                runtime,
                stop_event,
                payload,
                daluo_source_scene_id=304,
            ))
        if entry_result["status"] == "dojo_selection":
            return (yield from self._run_daily_lundao_dynamic_strategy_timed(runtime, stop_event, payload))
        if entry_result["status"] in {"unknown", "unimplemented"}:
            raise RuntimeError(
                f"论道_座位：进入后的落点分支尚未实现，当前 "
                f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        raise RuntimeError(f"论道_座位：进入论道返回了未处理状态 {entry_result['status']!r}")

    def _enter_daily_lundao_and_route_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: Any,
    ) -> dict[str, Any]:
        """节点 1：从 #69 打开论道并判定四态或道场选择页。

        输入只能是已确认的 #69 日常列表；输出是纯路由结果，不点击道场级别，
        也不执行抢座、结果处理或离场。unknown/未映射落点不得算成功。
        """
        status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label="论道_座位",
            title_pattern="论道",
            progress_can_mark_done=False,
        )
        if status != "open":
            return {"status": status, "scene_id": None, "score": 0.0}

        yield from runtime.wait_scene(
            *_DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS,
            timeout=20.0,
            label="论道_座位：等待道场选择/闻道中/被踢状态",
        )
        scene_id, score, frame = runtime.current_scene(_DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS, update=True)
        return self._route_daily_lundao_entry_scene(scene_id, score, frame)

    def _route_daily_lundao_entry_scene(
        self,
        scene_id: int | None,
        score: float,
        frame: str,
    ) -> dict[str, Any]:
        if scene_id in _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS["ready"]:
            return {"status": "ready", "stable_state": "ready", "scene_id": scene_id, "score": float(score)}
        if scene_id in _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS["in_progress"]:
            return {"status": "in_progress", "stable_state": "in_progress", "scene_id": scene_id, "score": float(score)}
        if scene_id in _DAILY_LUNDAO_STABLE_STATE_SCENE_IDS["kicked"]:
            return {"status": "kicked", "stable_state": "kicked", "scene_id": scene_id, "score": float(score)}
        if scene_id == 296:
            return {"status": "dojo_selection", "scene_id": scene_id, "score": float(score)}
        if scene_id is None:
            return {"status": "unknown", "scene_id": None, "score": float(score)}
        # 新的正式落点先显式失败；待用户给出业务语义后再加入四态映射或独立路由。
        return {"status": "unimplemented", "scene_id": scene_id, "score": float(score)}

    def _dismiss_daily_lundao_kicked(self, runtime: Any) -> dict[str, Any]:
        """确认 #391「被踢了」提示，再按同一组稳定状态重新路由。"""

        post_kick_scene_ids = [296, 304]
        yield from runtime.wait_click_then_view(
            391,
            "确认",
            post_kick_scene_ids,
            settle_seconds=1.5,
            timeout=20.0,
        )
        scene_id, score, frame = runtime.current_scene(post_kick_scene_ids, update=True)
        return self._route_daily_lundao_entry_scene(scene_id, score, frame)

    def _record_daily_lundao_next_time(
        self,
        payload: Mapping[str, Any],
        next_time: str,
        *,
        reason: str,
    ) -> str:
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lundao-seat"),
            next_time,
        )
        self._log("success", f"论道_座位：{reason}，下次 {next_time}")
        return next_time

    def _finish_daily_lundao_current_scene_action(
        self,
        payload: Mapping[str, Any],
        result: str,
        *,
        reason: str,
    ) -> str:
        """Close Scheduler intent after consuming a provable current scene."""

        status = self._read_daily_lundao_runtime_status(payload)
        now = _behavior_tree_runtime._now()
        left_time = status.get("current_left_listen_time")
        room_id = status.get("room_id")
        next_at = (
            next_lundao_daily_trigger(now)
            if (
                left_time is not None
                and (
                    int(left_time) <= 0
                    or int(room_id or 0) == LUNDAO_DALUO_ROOM_ID
                )
            )
            else next_lundao_recheck(now)
        )
        room_text = f"，当前道场 room_id={room_id}" if room_id is not None else ""
        self._record_daily_lundao_next_time(
            payload,
            next_at.strftime("%Y-%m-%d %H:%M:%S"),
            reason=f"{reason}{room_text}",
        )
        return result

    def _finish_daily_lundao_unseated_retry(
        self,
        payload: Mapping[str, Any],
        *,
        reason: str,
    ) -> str:
        """Record a business miss as due-now, then finish the trigger normally.

        ``success`` here means the Scheduler-triggered Cell completed its
        contract.  It does not claim that the first Lundao seat was obtained.
        """

        retry_at = next_lundao_unseated_retry(
            _behavior_tree_runtime._now()
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._record_daily_lundao_next_time(
            payload,
            retry_at,
            reason=reason,
        )
        return "success"

    def _finish_daily_lundao_changed_sanqing_target(
        self,
        runtime: Any,
        payload: Mapping[str, Any],
    ):
        """Finish safely when the selected Sanqing occupant moved before click."""

        yield from self._return_daily_lundao_to_selection(runtime, 297)
        yield from runtime.goto_view(34)
        return self._finish_daily_lundao_unseated_retry(
            payload,
            reason="三清目标已变化，未执行入座，10分钟后继续检查",
        )

    def _daily_lundao_first_visible_dojo(self, runtime: Any) -> str:
        frame = runtime.cur_frame(update=True)
        tokens = runtime.ocr_tokens_in_shapes(296, ["至尊道场"], frame_data_url=frame)
        fragments = group_ocr_tokens(tokens)
        hits: list[str] = []
        for fragment in fragments:
            text = re.sub(r"\s+", "", _sanitize_ocr_text(fragment.get("text")))
            for name in ("至尊", "大罗", "三清"):
                if name in text:
                    hits.append(name)
        unique = list(dict.fromkeys(hits))
        if len(unique) != 1:
            raise RuntimeError(f"论道_座位：#296 第一行道场 OCR 不唯一，命中={unique or '空'}，已停止且未点击")
        return unique[0]

    def _click_daily_lundao_dojo(
        self,
        runtime: Any,
        target: str,
        *,
        source_scene_id: int = 296,
    ) -> None:
        if source_scene_id == 304:
            # #304 is already an independently identified Lundao in-progress
            # scene.  Its stylized dojo label is commonly OCR'd as the stable
            # fragment ``大罗`` without the optional ``道场`` suffix.  Keep
            # the action bound to #304 and require exactly one spatial line;
            # the fragment only recovers a candidate and never promotes an
            # unknown frame or permits an ambiguous click.
            stable_target = target.removesuffix("道场") or target
            frame = runtime.cur_frame(update=True)
            # The dojo tabs are controls, not #304 identity shapes.  Prefer
            # the shared full-frame token cache so candidate collection does
            # not silently exclude them; this does not launch another OCR
            # pass.  Legacy/test adapters fall back to their token method.
            ocr_tokens = getattr(runtime, "full_frame_ocr_tokens", None)
            if not callable(ocr_tokens):
                ocr_tokens = getattr(runtime, "ocr_tokens", None)
            if callable(ocr_tokens):
                # Consume the real token boxes so ``大`` and ``罗`` may be
                # reconstructed without assuming OCR returns one full line.
                # Exact token matches also enumerate duplicate labels instead
                # of silently selecting the first one.
                candidates = [
                    {
                        "x": match.x,
                        "y": match.y,
                        "w": match.w,
                        "h": match.h,
                    }
                    for match in find_text_matches(
                        ocr_tokens(frame),
                        stable_target,
                    )
                ]
            else:
                # Test/legacy adapter fallback; production Runtime exposes
                # token-level OCR and therefore takes the branch above.
                lines = runtime.ocr_lines(frame_data_url=frame)
                candidates = [
                    line
                    for line in lines
                    if stable_target in re.sub(r"\s+", "", _sanitize_ocr_text(line.get("text")))
                    and float(line.get("w") or 0) > 0
                    and float(line.get("h") or 0) > 0
                ]
            if len(candidates) != 1:
                raise RuntimeError(
                    f"论道_座位：#304 未唯一识别「{target}」，"
                    f"候选={len(candidates)}，已停止且未点击"
                )
            line = candidates[0]
            runtime.click_frame_point(
                304,
                float(line.get("x") or 0) + float(line.get("w") or 0) / 2,
                float(line.get("y") or 0) + float(line.get("h") or 0) / 2,
            )
            return
        if source_scene_id != 296:
            raise RuntimeError(f"论道_座位：不支持从 #{source_scene_id} 选择「{target}」")
        order = ("至尊", "大罗", "三清", "御界")
        first = self._daily_lundao_first_visible_dojo(runtime)
        slot = order.index(target) - order.index(first) + 1
        slot_shapes = {1: "至尊道场", 2: "大罗道场", 3: "三清道场"}
        shape = slot_shapes.get(slot)
        if shape is None:
            raise RuntimeError(f"论道_座位：#296 当前首行为「{first}」，目标「{target}」不在可见三行，已停止")
        runtime.click_shape_center(296, shape)

    def _click_daily_lundao_dojo_with_stable_ocr(
        self,
        runtime: Any,
        stop_event: threading.Event,
        target: str,
        *,
        source_scene_id: int,
        attempts: int = 4,
        retry_seconds: float = 1.0,
    ):
        """Retry only a transient zero-candidate OCR result on identified #304.

        The independently identified scene remains the action boundary.  A
        duplicate candidate is an ambiguity and therefore fails immediately;
        repeated zero-candidate frames fail closed after the bounded local
        retry instead of forcing the Scheduler to restart the whole Job.
        """

        started_at = time.perf_counter()
        bounded_attempts = max(1, min(int(attempts), 8))
        for attempt in range(1, bounded_attempts + 1):
            self._raise_if_stopped(stop_event)
            try:
                self._click_daily_lundao_dojo(
                    runtime,
                    target,
                    source_scene_id=source_scene_id,
                )
            except RuntimeError as exc:
                if "候选=0" not in str(exc) or attempt >= bounded_attempts:
                    self._log(
                        "detail",
                        "论道_座位：阶段[#304稳定识别大罗]结束 "
                        f"elapsed={time.perf_counter() - started_at:.2f}s "
                        f"attempt={attempt}/{bounded_attempts} result=failed",
                    )
                    raise
                self._log(
                    "wait",
                    "论道_座位：#304 本帧未读到大罗，保持当前场景且不点击，"
                    f"等待新鲜帧 {attempt}/{bounded_attempts}",
                )
                yield from runtime.wait_action_settle(max(0.0, float(retry_seconds)))
                continue
            self._log(
                "detail",
                "论道_座位：阶段[#304稳定识别大罗]完成 "
                f"elapsed={time.perf_counter() - started_at:.2f}s "
                f"attempt={attempt}/{bounded_attempts}",
            )
            return

    def _refresh_daily_lundao_runtime_facts(self, *, reason: str, room_id: int | None = None) -> dict[str, Any]:
        started_at = time.perf_counter()
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        status = read_lundao_snapshot()
        selected_room_id = int(room_id or status.get("room_id") or 0)
        roster_key = "daluo_roster" if selected_room_id == LUNDAO_DALUO_ROOM_ID else "sanqing_roster"
        roster = status.get(roster_key) if isinstance(status.get(roster_key), dict) else {}
        facts = {"status": status, "roster": roster, "source": "runtime_memory"}
        facts["elapsed_seconds"] = time.perf_counter() - started_at
        self._log(
            "detail",
            f"论道_座位：按需读取 Runtime {reason} 用时 {facts['elapsed_seconds']:.2f}s",
        )
        return facts

    def _wait_daily_lundao_room_facts(
        self,
        runtime: Any,
        *,
        reason: str,
        room_id: int,
        baseline_key: tuple[Any, ...] = (),
        wait_seconds: float = 120.0,
        settle_retries: int = 15,
    ):
        """等待 Runtime 模型切换到刚点击的论道道场。"""

        facts = self._refresh_daily_lundao_runtime_facts(
            reason=reason,
            room_id=room_id,
        )
        for retry in range(max(0, int(settle_retries)) + 1):
            roster = facts.get("roster") if isinstance(facts.get("roster"), dict) else {}
            roster_key = tuple((roster.get("evidence") or {}).get("order_key") or ())
            if (
                bool(roster.get("available"))
                and bool(roster.get("complete"))
                and int(roster.get("room_id") or 0) == int(room_id)
                and bool(roster_key)
                and (not baseline_key or roster_key > baseline_key)
            ):
                return facts
            if retry >= max(0, int(settle_retries)):
                break
            yield from runtime.wait_action_settle(1.0)
            facts = self._refresh_daily_lundao_runtime_facts(reason=reason, room_id=room_id)
        return facts

    def _daily_lundao_room_available_count(self, status: Mapping[str, Any], room_id: int) -> int | None:
        values = status.get("room_available_counts") if isinstance(status.get("room_available_counts"), Mapping) else {}
        value = values.get(str(room_id))
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _daily_lundao_remaining_attempts(self, runtime: Any) -> int:
        text = runtime.ocr_text_in_shapes(296, ["次数"], padding=8)
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        values = parse_ocr_values(normalized)
        if values is None:
            raise RuntimeError(f"论道_座位：未能从 #296[次数] 读取首个数值，OCR={normalized[:80]}")
        return values[0]

    def _buy_one_daily_lundao_attempt(
        self,
        runtime: Any,
        *,
        before: int,
        at: datetime,
    ) -> dict[str, Any]:
        """Buy at most one attempt and verify the #296 counter increased."""

        if before > 0:
            return {"ready": True, "purchased": False, "before": before, "after": before}
        if not lundao_purchase_allowed(at):
            self._log(
                "skip",
                "论道_座位：#296[次数]分子为 0 且已到 21:00，禁止购买次数，本轮不再入座或升级",
            )
            return {
                "ready": False,
                "purchased": False,
                "before": before,
                "after": before,
                "reason": "purchase_cutoff",
            }
        self._log("action", "论道_座位：座位动作已确认且次数为 0，点击 #296[购买]")
        runtime.click_shape_center(296, "购买")
        yield from runtime.wait_scene(392, timeout=15.0, label="论道_座位：等待购买次数页 #392")
        self._log("action", "论道_座位：只购买 1 次")
        runtime.click_shape_center(392, "购买")
        yield from runtime.wait_action_settle(1.5)
        scene_id, score, _frame = runtime.current_scene([392], update=True)
        scene_id = _lundao_waited_scene_id(scene_id)
        if scene_id != 392:
            raise RuntimeError(
                f"论道_座位：点击 #392[购买] 后出现未知结果，scene={scene_id} score={float(score):.0f}%，"
                "已停止且不会重复购买"
            )
        runtime.click_shape_center(392, "返回")
        yield from runtime.wait_scene(296, timeout=15.0, label="论道_座位：购买后返回 #296")
        after = self._daily_lundao_remaining_attempts(runtime)
        if after <= before:
            self._log(
                "warning",
                f"论道_座位：购买后次数未增加（{before}->{after}），按购买额度不足处理，本轮不再购买",
            )
            return {"ready": False, "purchased": False, "before": before, "after": after, "reason": "purchase_unavailable"}
        self._log("success", f"论道_座位：购买 1 次成功，剩余次数 {before}->{after}")
        return {"ready": True, "purchased": True, "before": before, "after": after}

    def _return_daily_lundao_to_selection(self, runtime: Any, scene_id: int) -> int:
        # #298 is the empty-seat variant of the same room list; its full-screen
        # reference has no duplicate return annotation, so use the shared #297
        # room-list return control already present at the same stable location.
        runtime.click_shape_center(297, "返回")
        return (yield from runtime.wait_scene(296, 304, timeout=15.0, label="论道_座位：返回道场选择"))

    def _confirm_daily_lundao_seated_after_return(self, runtime: Any, initial_scene: int) -> int:
        """Resolve the short #296 -> #304 transition without trusting packet state."""

        scene_id = _lundao_waited_scene_id(initial_scene)
        if scene_id == 304:
            return 304
        for _attempt in range(3):
            yield from runtime.wait_action_settle(2.0)
            detected, _score, frame = runtime.current_scene([296, 304], update=True)
            scene_id = _lundao_waited_scene_id(detected)
            if scene_id == 304:
                return 304
            ocr_text = getattr(runtime, "ocr_text", None)
            if callable(ocr_text) and self._daily_lundao_text_is_seated(ocr_text(frame)):
                return 304
        return scene_id

    def _run_daily_lundao_room_action(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        opportunity: Mapping[str, Any],
    ) -> str:
        scene_id, score, _frame = runtime.current_scene([297, 298], update=True)
        action = str(opportunity.get("action") or "")
        # The Runtime roster can change between planning and the final fresh
        # layer-0 read.  #298 is itself authoritative evidence that an empty
        # seat is currently available, so it is safe to discard an older kick
        # target and rebuild the action as the target-free empty-seat flow.
        # The inverse is not safe: #297 does not identify a player target, so
        # an older ``empty`` decision must abort this room attempt rather than
        # being upgraded to a kick from stale facts.
        if scene_id == 298 and action in {"empty", "kick"}:
            scene_id, score = yield from self._run_daily_lundao_empty_seat_strategy(runtime)
        elif scene_id == 297 and action == "empty":
            return "target_changed"
        elif action == "kick" and scene_id == 297:
            target = opportunity.get("target") if isinstance(opportunity.get("target"), Mapping) else None
            if target is None:
                raise RuntimeError("论道_座位：策略要求踢人但没有合法目标，已停止")
            kick_result = yield from self._run_daily_lundao_kick_for_seat_strategy(
                runtime,
                stop_event,
                target_player=target,
                room_id=int(
                    opportunity.get("room_id") or LUNDAO_DALUO_ROOM_ID
                ),
            )
            if kick_result.get("status") == "target_changed":
                return "target_changed"
            scene_id = int(kick_result.get("scene_id") or 52)
            score = float(kick_result.get("score") or 0.0)
        else:
            raise RuntimeError(f"论道_座位：策略动作 {action!r} 与当前场景 #{scene_id} 不一致，已停止")
        return (yield from self._complete_daily_lundao_seat_and_leave(runtime, stop_event, scene_id, score))

    def _run_daily_lundao_dynamic_strategy_timed(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
        **kwargs: Any,
    ):
        """Record one complete strategy phase, including failed resumptions."""

        started_at = time.perf_counter()
        result = "failed"
        try:
            value = yield from self._run_daily_lundao_dynamic_strategy(
                runtime,
                stop_event,
                payload,
                **kwargs,
            )
            result = str(value)
            return value
        finally:
            self._log(
                "detail",
                "论道_座位：阶段[动态策略]结束 "
                f"elapsed={time.perf_counter() - started_at:.2f}s result={result}",
            )

    def _rebuild_daily_lundao_strategy_after_attempt_check(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
        *,
        source_scene_id: int,
        purchase_used: bool,
    ):
        """Rebuild the roster without losing the actual selection-page kind."""

        if source_scene_id not in {296, 304}:
            raise RuntimeError(
                "论道_座位：确认挑战次数后未回到道场选择/闻道中页面，"
                f"当前 #{source_scene_id}，已停止且未点击"
            )
        return (
            yield from self._run_daily_lundao_dynamic_strategy(
                runtime,
                stop_event,
                payload,
                attempt_ready=True,
                purchase_used=purchase_used,
                daluo_source_scene_id=source_scene_id,
            )
        )

    def _run_daily_lundao_dynamic_strategy(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
        *,
        attempt_ready: bool = False,
        purchase_used: bool = False,
        daluo_source_scene_id: int = 296,
    ) -> str:
        now = _behavior_tree_runtime._now()
        from backend.core.fanxiu.instrumentation.lundao import (
            read_lundao_snapshot,
        )

        runtime_override = payload.get("__lundao_runtime_snapshot_override")
        runtime_status = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else read_lundao_snapshot()
        )
        if runtime_status.get("available") and runtime_status.get("complete"):
            self._log(
                "detail",
                "论道_座位：已从游戏 Runtime 读取剩余闻道时间、"
                "自身座位与房间空位",
            )
            runtime_decision = plan_lundao_strategy(
                runtime_status,
                daluo_opportunity=None,
                at=now,
            )
            if runtime_decision.get("action") in {"done", "stay_daluo"}:
                yield from runtime.goto_view(34)
                next_time = runtime_decision["next_time"].strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                self._record_daily_lundao_next_time(
                    payload,
                    next_time,
                    reason=(
                        f"Runtime：{runtime_decision.get('reason')}"
                    ),
                )
                return "success"
        else:
            self._log(
                "detail",
                "论道_座位：Runtime 状态尚未加载，先打开道场触发模型初始化，"
                f"reason={runtime_status.get('reason') or 'incomplete'}",
            )
        # Opening Daluo is read-only and initializes the Runtime room roster.
        initial = self._refresh_daily_lundao_runtime_facts(
            reason="daily-lundao-before-daluo",
            room_id=LUNDAO_DALUO_ROOM_ID,
        )
        baseline = initial.get("roster") if isinstance(initial.get("roster"), dict) else {}
        baseline_key = tuple((baseline.get("evidence") or {}).get("order_key") or ())
        if daluo_source_scene_id == 304:
            yield from self._click_daily_lundao_dojo_with_stable_ocr(
                runtime,
                stop_event,
                "大罗道场",
                source_scene_id=304,
                attempts=int(payload.get("scene304_ocr_attempts") or 4),
                retry_seconds=float(payload.get("scene304_ocr_retry_seconds") or 1.0),
            )
        else:
            self._click_daily_lundao_dojo(runtime, "大罗")
        try:
            yield from runtime.wait_scene(297, 298, timeout=15.0, label="论道_座位：等待大罗座位列表")
        except TimeoutError as exc:
            # A player already seated in Daluo has a `离座` control on their own
            # row, so the ordinary #297 identity (which expects `请他让座`) can
            # be only a partial visual match.  Opening the page is still enough
            # to initialize the Runtime model. Continue read-only and let
            # Runtime decide whether to stay/finish; no seat click is
            # allowed unless the normal actionable list is identified later.
            self._log("warning", f"论道_座位：大罗名单页未完整匹配，继续只读 Runtime 核对：{exc}")
        runtime_after_open = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else read_lundao_snapshot()
        )
        runtime_roster = (
            runtime_after_open.get("daluo_roster")
            if isinstance(runtime_after_open.get("daluo_roster"), dict)
            else {}
        )
        runtime_roster_used = bool(
            runtime_after_open.get("available")
            and runtime_after_open.get("complete")
            and runtime_roster.get("available")
            and runtime_roster.get("complete")
        )
        if runtime_roster_used:
            self._log(
                "detail",
                "论道_座位：已从游戏 Runtime 读取完整大罗座位名单，"
                "直接进入决策",
            )
            refreshed = {
                "status": runtime_after_open,
                "roster": runtime_roster,
            }
        else:
            self._log(
                "detail",
                "论道_座位：Runtime 大罗名单尚未加载，等待 Runtime 模型完成，"
                f"reason={runtime_roster.get('reason') or runtime_after_open.get('reason') or 'incomplete'}",
            )
            refreshed = yield from self._wait_daily_lundao_room_facts(
                runtime,
                reason="daily-lundao-daluo-roster",
                room_id=LUNDAO_DALUO_ROOM_ID,
                baseline_key=baseline_key,
                wait_seconds=120.0,
            )
        status = refreshed.get("status") if isinstance(refreshed.get("status"), dict) else {}
        authoritative_runtime = (
            runtime_after_open
            if runtime_after_open.get("available") and runtime_after_open.get("complete")
            else runtime_status
        )
        if authoritative_runtime.get("available") and authoritative_runtime.get("complete"):
            status = {
                **status,
                **{
                    key: authoritative_runtime.get(key)
                    for key in (
                        "available",
                        "complete",
                        "strength",
                        "room_id",
                        "seat_id",
                        "seated",
                        "left_listen_time",
                        "current_left_listen_time",
                        "sit_down_time",
                        "rooms",
                        "room_available_counts",
                        "source",
                        "protocol",
                        "evidence",
                    )
                },
            }
        roster = refreshed.get("roster") if isinstance(refreshed.get("roster"), dict) else {}
        roster_key = tuple((roster.get("evidence") or {}).get("order_key") or ())
        fresh_status_and_roster = (
            bool(status.get("available"))
            and int(roster.get("room_id") or 0) == LUNDAO_DALUO_ROOM_ID
            and bool(roster_key)
            and (
            not baseline_key or roster_key > baseline_key
            )
        )
        if not fresh_status_and_roster:
            return_scene = yield from self._return_daily_lundao_to_selection(runtime, 297)
            # #296 can be a transient match while the already-seated view is
            # settling to #304. Runtime incompleteness must not gate the
            # independent visual confirmation.
            return_scene = yield from self._confirm_daily_lundao_seated_after_return(runtime, return_scene)
            yield from runtime.goto_view(34)
            if return_scene == 304:
                next_at = next_lundao_recheck(_behavior_tree_runtime._now())
                next_time = next_at.strftime("%Y-%m-%d %H:%M:%S")
                self._record_daily_lundao_next_time(
                    payload,
                    next_time,
                    reason="已在闻道中，Runtime 名单暂不完整，按正常半小时复查",
                )
                return "success"
            raise RuntimeError(
                "论道_座位：Runtime 状态或座位名单不完整，保留原触发时间立即整单重试"
            )

        # Completion and an existing Daluo seat need only fresh status facts;
        # neither branch should be blocked by the seated-row visual variant or
        # by a roster that is irrelevant to a no-op decision.
        status_decision = plan_lundao_strategy(status, daluo_opportunity=None, at=now)
        if status_decision.get("action") in {"done", "stay_daluo"}:
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            next_time = status_decision["next_time"].strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason=str(status_decision.get("reason")))
            return "success"
        if (
            not runtime_roster_used
            and baseline_key
            and roster_key
            and roster_key <= baseline_key
        ):
            raise RuntimeError("论道_座位：进入大罗后未获得更新的座位名单，已停止且未点击座位")
        profile = lundao_player_profile_from_runtime(authoritative_runtime)
        if not profile.get("available"):
            profile = current_lundao_player_profile()
        opportunity = evaluate_lundao_room_opportunity(
            roster,
            player_profile=profile,
            available_count=self._daily_lundao_room_available_count(status, LUNDAO_DALUO_ROOM_ID),
            at=now,
            room_id=LUNDAO_DALUO_ROOM_ID,
            require_safety_threshold=True,
        )
        decision = plan_lundao_strategy(status, daluo_opportunity=opportunity, at=now)
        self._log(
            "info",
            f"论道_座位：大罗 x={opportunity.get('safety_score')}/{opportunity.get('threshold')}，"
            f"空位={opportunity.get('available_count')}，合法目标={opportunity.get('eligible_count')}，决策={decision.get('action')}",
        )
        if not opportunity.get("ok"):
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            raise RuntimeError(
                "论道_座位：大罗事实不足，保留原触发时间立即整单重试，"
                f"reason={opportunity.get('reason') or 'unknown'}"
            )
        if decision.get("action") == "done":
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            next_time = decision["next_time"].strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason=str(decision.get("reason")))
            return "success"
        returned_to_selection = False
        fallback_without_attempt = False
        if decision.get("action") == "seat_daluo":
            if not attempt_ready:
                selection_source_scene_id = yield from self._return_daily_lundao_to_selection(runtime, 297)
                returned_to_selection = True
                remaining = self._daily_lundao_remaining_attempts(runtime)
                attempt = {"ready": remaining > 0, "purchased": False, "before": remaining, "after": remaining}
                if remaining <= 0:
                    if purchase_used:
                        attempt = {"ready": False, "reason": "purchase_already_attempted"}
                    else:
                        attempt = yield from self._buy_one_daily_lundao_attempt(runtime, before=remaining, at=now)
                if not attempt.get("ready"):
                    if attempt.get("reason") == "purchase_cutoff":
                        yield from runtime.goto_view(34)
                        next_at = next_lundao_daily_trigger(now)
                        self._record_daily_lundao_next_time(
                            payload,
                            next_at.strftime("%Y-%m-%d %H:%M:%S"),
                            reason="21点后免费次数为0，不再购买或争取最后一小时收益",
                        )
                        return "success"
                    # Challenge attempts are required for kicking a player, not
                    # for occupying a visible empty seat. Before the cost cutoff,
                    # a non-cutoff purchase failure may still fall back to a
                    # visible Sanqing empty seat without spending an attempt.
                    fallback_without_attempt = True
                    status = {**status, "seated": False, "room_id": None, "seat_id": None}
                    self._log("warning", "论道_座位：大罗需要挑战次数但当前不可购买，本轮继续检查三清空位")
                else:
                    # Purchasing or merely confirming an available attempt is not
                    # permission to use the old roster. Re-open Daluo and rebuild
                    # the decision from a new packet baseline before any seat click.
                    return (
                        yield from self._rebuild_daily_lundao_strategy_after_attempt_check(
                            runtime,
                            stop_event,
                            payload,
                            source_scene_id=int(_lundao_waited_scene_id(selection_source_scene_id)),
                            purchase_used=purchase_used or bool(attempt.get("purchased")),
                        )
                    )
            if attempt_ready:
                result = yield from self._run_daily_lundao_room_action(runtime, stop_event, opportunity=opportunity)
                if result == "target_changed":
                    # The selected Daluo player disappeared before the click.  No
                    # seat transaction happened, so do not pretend the previous
                    # Sanqing seat still exists and do not end this Job.  Continue
                    # below into the normal Sanqing fallback in the same Cell.
                    status = {**status, "seated": False, "room_id": None, "seat_id": None}
                    self._log("warning", "论道_座位：大罗目标已变化，当前无座，本轮立即降级尝试三清")
                else:
                    after_status = self._daily_lundao_post_seat_status(
                        payload,
                        reason="daily-lundao-after-seat",
                    )
                    self._require_daily_lundao_expected_room(
                        after_status,
                        LUNDAO_DALUO_ROOM_ID,
                        label="大罗入座",
                    )
                    after_left_time = after_status.get("current_left_listen_time")
                    completed_at = _behavior_tree_runtime._now()
                    # Daluo is the terminal target seat.  A same-day rerun is
                    # now requested only by a newly observed eviction mail.
                    next_at = next_lundao_daily_trigger(completed_at)
                    self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="已完成大罗入座")
                    return result

        current_room = int(status.get("room_id") or 0)
        if not returned_to_selection:
            yield from self._return_daily_lundao_to_selection(runtime, 297)
        if current_room == LUNDAO_SANQING_ROOM_ID:
            yield from runtime.goto_view(34)
            next_at = decision.get("next_time") if isinstance(decision.get("next_time"), datetime) else next_lundao_recheck(now)
            self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="大罗条件不足，保留三清")
            return "success"

        if not attempt_ready and not fallback_without_attempt:
            remaining = self._daily_lundao_remaining_attempts(runtime)
            attempt = {"ready": remaining > 0, "purchased": False, "before": remaining, "after": remaining}
            if remaining <= 0:
                if purchase_used:
                    attempt = {"ready": False, "reason": "purchase_already_attempted"}
                else:
                    attempt = yield from self._buy_one_daily_lundao_attempt(runtime, before=remaining, at=now)
            if not attempt.get("ready"):
                yield from runtime.goto_view(34)
                purchase_cutoff = attempt.get("reason") == "purchase_cutoff"
                next_at = (
                    next_lundao_daily_trigger(now)
                    if purchase_cutoff
                    else datetime.combine(now.date(), time_cls(22, 0))
                )
                self._record_daily_lundao_next_time(
                    payload,
                    next_at.strftime("%Y-%m-%d %H:%M:%S"),
                    reason=(
                        "21点后免费次数为0，禁止购买并放弃本轮三清入座"
                        if purchase_cutoff
                        else "确需三清入座，但今日已无可用次数"
                    ),
                )
                return "success"
            attempt_ready = True

        before_sanqing = self._refresh_daily_lundao_runtime_facts(
            reason="daily-lundao-before-sanqing",
            room_id=LUNDAO_SANQING_ROOM_ID,
        )
        before_sanqing_roster = (
            before_sanqing.get("roster")
            if isinstance(before_sanqing.get("roster"), dict)
            else {}
        )
        before_sanqing_key = tuple(
            (before_sanqing_roster.get("evidence") or {}).get("order_key") or ()
        )
        self._click_daily_lundao_dojo(runtime, "三清")
        sanqing_scene = _lundao_waited_scene_id(
            (yield from runtime.wait_scene(297, 298, timeout=15.0, label="论道_座位：等待三清座位列表"))
        )
        if sanqing_scene == 298:
            result = yield from self._run_daily_lundao_room_action(
                runtime,
                stop_event,
                opportunity={"action": "empty"},
            )
            after_status = self._daily_lundao_post_seat_status(
                payload,
                reason="daily-lundao-after-sanqing-empty-seat",
            )
            next_at = next_lundao_recheck(_behavior_tree_runtime._now())
            self._record_confirmed_daily_lundao_seat(
                payload,
                after_status,
                expected_room_id=LUNDAO_SANQING_ROOM_ID,
                next_time=next_at.strftime("%Y-%m-%d %H:%M:%S"),
                label="三清入座",
                reason="三清画面确认有空位，已直接入座",
            )
            return result
        if fallback_without_attempt:
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            return self._finish_daily_lundao_unseated_retry(
                payload,
                reason="三清当前满座且没有可用挑战次数，10分钟后继续检查空位",
            )
        sanqing_facts = yield from self._wait_daily_lundao_room_facts(
            runtime,
            reason="daily-lundao-sanqing-roster",
            room_id=LUNDAO_SANQING_ROOM_ID,
            baseline_key=before_sanqing_key,
            wait_seconds=120.0,
        )
        sanqing_status = sanqing_facts.get("status") if isinstance(sanqing_facts.get("status"), dict) else {}
        sanqing_roster = sanqing_facts.get("roster") if isinstance(sanqing_facts.get("roster"), dict) else {}
        sanqing = evaluate_lundao_room_opportunity(
            sanqing_roster,
            player_profile=profile,
            available_count=self._daily_lundao_room_available_count(sanqing_status, LUNDAO_SANQING_ROOM_ID),
            at=now,
            room_id=LUNDAO_SANQING_ROOM_ID,
            require_safety_threshold=False,
        )
        if not sanqing.get("ok"):
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            raise RuntimeError(
                "论道_座位：三清事实不足，不能误判为无座位，保留原触发时间重试，"
                f"reason={sanqing.get('reason') or 'unknown'}"
            )
        if not sanqing.get("actionable"):
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            return self._finish_daily_lundao_unseated_retry(
                payload,
                reason="尚未落座，保持立即到期并继续争取三清座位",
            )
        result = yield from self._run_daily_lundao_room_action(runtime, stop_event, opportunity=sanqing)
        if result == "target_changed":
            return (
                yield from self._finish_daily_lundao_changed_sanqing_target(
                    runtime,
                    payload,
                )
            )
        after_status = self._daily_lundao_post_seat_status(
            payload,
            reason="daily-lundao-after-sanqing-seat",
        )
        after_left_time = after_status.get("current_left_listen_time")
        completed_at = _behavior_tree_runtime._now()
        next_at = (
            next_lundao_daily_trigger(completed_at)
            if after_left_time is not None and int(after_left_time) <= 0
            else next_lundao_recheck(completed_at)
        )
        self._record_confirmed_daily_lundao_seat(
            payload,
            after_status,
            expected_room_id=LUNDAO_SANQING_ROOM_ID,
            next_time=next_at.strftime("%Y-%m-%d %H:%M:%S"),
            label="三清入座",
            reason="已完成三清入座",
        )
        return result

    def _daily_lundao_post_seat_status(
        self,
        payload: Mapping[str, Any],
        *,
        reason: str,
    ) -> dict[str, Any]:
        """Prefer the live seated model after a successful seat transaction."""

        runtime_status = self._read_daily_lundao_runtime_status(payload)
        if (
            runtime_status.get("available")
            and runtime_status.get("complete")
            and runtime_status.get("seated") is True
        ):
            self._log(
                "detail",
                "论道_座位：入座后 Runtime 已确认 seated=true 与"
                "剩余闻道时间",
            )
            return runtime_status
        self._log("detail", "论道_座位：入座后按需重读 Runtime，" f"reason={reason}")
        after = self._refresh_daily_lundao_runtime_facts(reason=reason)
        return (
            dict(after["status"])
            if isinstance(after.get("status"), dict)
            else {}
        )

    def _require_daily_lundao_expected_room(
        self,
        status: Mapping[str, Any],
        expected_room_id: int,
        *,
        label: str,
    ) -> None:
        actual_room_id = int(status.get("room_id") or 0)
        if not (
            status.get("available")
            and status.get("complete")
            and status.get("seated") is True
            and actual_room_id == int(expected_room_id)
        ):
            raise RuntimeError(
                f"论道_座位：{label}动作结束后 Runtime 未确认目标道场，"
                f"expected_room_id={int(expected_room_id)}，actual_room_id={actual_room_id or 'unknown'}"
            )

    def _record_confirmed_daily_lundao_seat(
        self,
        payload: Mapping[str, Any],
        status: Mapping[str, Any],
        *,
        expected_room_id: int,
        next_time: str,
        label: str,
        reason: str,
    ) -> None:
        """Only persist seat success after Runtime proves the business postcondition."""

        self._require_daily_lundao_expected_room(
            status,
            expected_room_id,
            label=label,
        )
        self._record_daily_lundao_next_time(payload, next_time, reason=reason)

    def _read_daily_lundao_runtime_status(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.lundao import (
            read_lundao_snapshot,
        )

        override = payload.get(
            "__lundao_post_seat_runtime_snapshot_override",
            payload.get("__lundao_runtime_snapshot_override"),
        )
        runtime_status = (
            dict(override)
            if isinstance(override, Mapping)
            else read_lundao_snapshot()
        )
        return runtime_status

    def _daily_lundao_world_runtime_guard(
        self,
        runtime: Any,
        payload: Mapping[str, Any],
        *,
        scene_id: int,
    ):
        """Skip a vanished daily entry when Runtime already proves the state."""

        runtime_status = self._read_daily_lundao_runtime_status(payload)
        if not (
            runtime_status.get("available")
            and runtime_status.get("complete")
        ):
            return None
        current_left = runtime_status.get("current_left_listen_time")
        seated = runtime_status.get("seated") is True
        if current_left is None or (int(current_left) > 0 and not seated):
            return None
        # 三清已入座只证明当前有座位，不能证明无需升级。继续进入论道
        # 动态策略，刷新大罗名册、检查免费次数并判断是否可以换座。
        if (
            int(current_left) > 0
            and int(runtime_status.get("room_id") or 0) != LUNDAO_DALUO_ROOM_ID
        ):
            return None
        if scene_id != 34:
            yield from runtime.goto_view(34)
        now = _behavior_tree_runtime._now()
        if int(current_left) <= 0:
            next_at = next_lundao_daily_trigger(now)
            self._record_daily_lundao_next_time(
                payload,
                next_at.strftime("%Y-%m-%d %H:%M:%S"),
                reason="Runtime 已确认今日闻道时间归零",
            )
            return "success"
        next_at = next_lundao_daily_trigger(now)
        self._record_daily_lundao_next_time(
            payload,
            next_at.strftime("%Y-%m-%d %H:%M:%S"),
            reason="Runtime 已确认仍在闻道中",
        )
        self._log(
            "skip",
            "论道_座位：Runtime 已确认 seated=true，跳过已消失的日常入口",
        )
        return "skipped"

    def _select_daily_lundao_dojo_level(self, runtime: Any, scene_id: int | None) -> dict[str, Any]:
        """节点 2：选择论道道场级别，输出抢座节点的起始场景。

        输入是节点 1 路由出的 #294「准备开始」或 #296 道场选择页。该节点
        只选择“大罗道场”，不得执行请人让座、入座、结果处理或离场。
        已真实确认的边界仅为 #296[大罗道场] -> 等待 5 秒 -> #297；
        #294[大罗道场] 的点击已确认，但真实落点尚未提供，必须显式暂停。

        真实证据（2026-07-18，AI Cell execution_count=13）：点击前 #296 100%，
        点击 #296[大罗道场] 并等待 5 秒后为 #297 85%。这只证明本轮进入
        #297「踢人抢座」入口，不证明 #297 后续动作。
        """
        if scene_id == 294:
            runtime.click_shape_center(294, "大罗道场")
            return {"status": "target_pending", "source_scene_id": 294, "scene_id": None, "score": 0.0}
        if scene_id != 296:
            return {"status": "unimplemented", "source_scene_id": scene_id, "scene_id": None, "score": 0.0}
        runtime.click_shape_center(296, "大罗道场")
        yield from runtime.wait_action_settle(5.0)
        next_scene_id, score, _frame = runtime.current_scene([297], update=True)
        if next_scene_id != 297:
            return {"status": "unknown", "source_scene_id": 296, "scene_id": next_scene_id, "score": float(score)}
        return {"status": "selected", "source_scene_id": 296, "scene_id": 297, "score": float(score)}

    def _continue_after_daily_lundao_dojo_selection(
        self,
        runtime: Any,
        stop_event: threading.Event,
        selection_result: dict[str, Any],
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        if selection_result.get("status") == "selected" and selection_result.get("scene_id") == 297:
            return (yield from self._run_daily_lundao_seat_and_leave(runtime, stop_event, payload=payload))
        if selection_result.get("status") == "target_pending":
            raise RuntimeError("论道_座位：#294 已点击「大罗道场」，真实落点与后续路由尚未提供")
        raise RuntimeError(
            f"论道_座位：选择道场级别后未到达抢座起点 #297，"
            f"当前 #{selection_result.get('scene_id') if selection_result.get('scene_id') is not None else 'unknown'} "
            f"{float(selection_result.get('score') or 0.0):.0f}%"
        )

    def _run_daily_lundao_seat_and_leave(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        """节点 3：从道场选择落点开始，完成一轮抢座、结果处理与离场。

        输入是节点 2 完成选择后的真实画面。进入节点后先用精确 Layer 0
        区分 #297「满座」与 #298「有空位」；输出只能是完整闭环后的 success
        或明确失败。该节点不得返回 #69 查入口，也不得重新选择道场级别。
        尚未给全的抢座分支继续显式失败，不能猜流程或提前报成功。

        #297 进入“踢人抢座”策略，#298 进入“直接坐空位”策略。两条策略
        只负责推进到共同后续场景，随后统一交给确认入座、离场和完成收尾，
        禁止各自复制一套离场逻辑。
        """
        # A generic #295 victory overlay is not a valid top-level entry: it may
        # belong to the previously running job.  #295 is accepted only inside
        # _advance_daily_lundao_kick_dialogue after this job has initiated its
        # own kick/battle transaction.
        scene_id, score, _frame = runtime.current_scene([297, 298, 371, 372, 373, 375], update=True)
        if scene_id == 297:
            selection = self._select_daily_lundao_kick_target()
            if not selection.get("ok"):
                raise RuntimeError(
                    f"论道_座位：读取大罗座位或自身法则失败，"
                    f"status={selection.get('status')} reason={selection.get('reason')}"
                )
            target = selection.get("target") if isinstance(selection.get("target"), dict) else None
            if target is None:
                yield from runtime.goto_view(34)
                next_time = self._schedule_daily_lundao_next_check(
                    dict(payload or {}),
                    message="大罗满座且没有可击败的非友军",
                )
                rejected = selection.get("rejected") if isinstance(selection.get("rejected"), dict) else {}
                self._log("skip", f"论道_座位：无可用目标，已返回 #34，{next_time} 重试，排除={rejected}")
                return "success"
            self._log(
                "info",
                f"论道_座位：选择非友军「{target.get('name')}」，"
                f"{target.get('faze_cross')}跨，战力 {float(target.get('battle_score') or 0):.3e}",
            )
            kick_result = yield from self._run_daily_lundao_kick_for_seat_strategy(
                runtime,
                stop_event,
                target_player=target,
            )
            if kick_result.get("status") == "target_changed":
                yield from runtime.goto_view(34)
                next_time = self._schedule_daily_lundao_next_check(
                    dict(payload or {}),
                    message="大罗目标已变化，保持当前座位",
                )
                self._log("skip", f"论道_座位：目标变化，未点击，{next_time} 复查")
                return "success"
            if kick_result.get("status") == "prerequisite_required":
                raise RuntimeError(
                    "论道_座位：#297 动态条目按钮返回法则前置缺失，"
                    "已安全返回 #34；法则邮件补偿尚未执行"
                )
            scene_id = int(kick_result.get("scene_id") or 52)
            score = float(kick_result.get("score") or 0.0)
        elif scene_id == 298:
            scene_id, score = yield from self._run_daily_lundao_empty_seat_strategy(runtime)
        elif scene_id == 371:
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(runtime, start_scene=371)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id == 372:
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(runtime, start_scene=372)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id == 373:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(runtime, start_scene=373)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id == 375:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(runtime, start_scene=375)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        else:
            raise RuntimeError(
                f"论道_座位：抢座节点入口只接受 #297/#298/#371/#372/#373/#375，当前 "
                f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        return (yield from self._complete_daily_lundao_seat_and_leave(runtime, stop_event, scene_id, score))

    def _leave_daily_lundao_rule_block_to_world(self, runtime: Any) -> dict[str, Any]:
        """Close the rule-prerequisite notice and suspend Lundao at world #34."""

        scene_id, score, _frame = runtime.current_scene([564], update=True)
        if scene_id != 564:
            raise RuntimeError(
                "论道_座位：法则前置缺失退出只接受已确认的 #564，"
                f"当前 #{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        self._log("action", "论道_座位：#564 仅作为法则前置缺失信号，关闭提示后退回世界")
        landed = yield from runtime.wait_click_then_view(
            564,
            "确认",
            297,
            timeout=12.0,
            label="论道_座位：关闭法则提示后等待座位列表 #297",
        )
        if not isinstance(landed, View) or landed.id != 297:
            raise RuntimeError("论道_座位：关闭 #564 后未可靠回到座位列表 #297")
        yield from runtime.goto_view(34)
        final_scene, final_score, _final_frame = runtime.current_scene([34], update=True)
        if final_scene != 34:
            raise RuntimeError(
                "论道_座位：法则前置缺失退出后未确认世界 #34，"
                f"当前 #{final_scene if final_scene is not None else 'unknown'} {final_score:.0f}%"
            )
        self._log("success", "论道_座位：已从 #564 安全退出论道并确认世界 #34")
        return {
            "status": "prerequisite_required",
            "prerequisite": "law_mail",
            "source_scene_id": 564,
            "scene_id": 34,
            "score": float(final_score or 0.0),
        }

    def _select_daily_lundao_kick_target(self) -> dict[str, Any]:
        """Refresh the current Daluo roster and apply the shared relation policy."""

        return refresh_and_select_lundao_kick_target()

    def _schedule_daily_lundao_next_check(
        self,
        payload: dict[str, Any],
        *,
        message: str,
        seconds: int | None = None,
    ) -> str:
        now = _behavior_tree_runtime._now()
        next_at = (
            next_lundao_unseated_retry(now)
            if seconds is None
            else now + timedelta(seconds=max(60, int(seconds)))
        )
        next_at = clip_daily_retry_to_window(
            next_at,
            now=now,
            start=LUNDAO_FIRST_TRIGGER,
            end=LUNDAO_CLOSE_TIME,
        )
        next_time = next_at.strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lundao-seat"),
            next_time,
        )
        self._log("skip", f"论道_座位：{message}，{next_time} 重试")
        return next_time

    def _run_daily_lundao_kick_for_seat_strategy(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        target_player: Mapping[str, Any] | None = None,
        room_id: int = LUNDAO_DALUO_ROOM_ID,
    ):
        """#297 满座策略：只对上游明确指定的目标玩家执行同条目让座。

        `target_player` 是策略必需输入，由上游给出稳定玩家 ID 和/或精确玩家名；
        正式链路由前置 Runtime 座位清单与选人策略生成该结构，稳定 ID/seat_id 是
        身份依据，name 只用于界面 OCR 匹配与校验；同盟排除也由上游完成。
        本节点禁止读取手工固定姓名、自行排序、猜测或改选目标。必须先限定在
        正式 `#297[窗口]` 内定位目标姓名所属动态条目，再通过空间关联找到
        同一行/同一卡片的 `[请他让座]`，绝不能点击固定全局坐标、窗口外按钮
        或其它玩家条目的同名按钮。目标缺失、条目不唯一、按钮关联不唯一或
        任一证据不足时均须在点击前停止，不得误点，也不得算任务成功。

        定位使用 floating `[模板]` 的 `[区服姓名]` 与 `[按钮]` 正式标注；顶层
        `#297[请他让座]` 只作视觉/场景身份参考，不作为动态条目的点击坐标。
        """
        self._raise_if_stopped(stop_event)
        target = dict(target_player or {})
        target_name = _sanitize_ocr_text(target.get("name"))
        target_id = str(target.get("seat_id") or target.get("id") or "").strip()
        if not target_id:
            raise RuntimeError("论道_座位：#297 踢人抢座缺少上游确定的目标玩家，已停止且未点击")
        if not target_name:
            raise RuntimeError("论道_座位：#297 目标缺少用于界面校验的玩家姓名，已停止且未点击")
        if bool(target.get("excluded") or target.get("is_ally")):
            raise RuntimeError("论道_座位：#297 上游目标已标记为排除/同盟，已停止且未点击")

        window = runtime.shape(297, "窗口")
        if str(window.raw.get("loadDirection") or "").strip().lower() != "down":
            raise RuntimeError("论道_座位：#297[窗口] loadDirection 不是 down，已停止且未点击")

        def request_kick(item: Any):
            if not runtime.floating_item_is_fully_inside(item, "窗口"):
                raise RuntimeError(f"论道_座位：目标「{target_name}」模板实例位于窗口裁剪边缘，已停止且未点击")
            if not runtime.floating_item_field_is_inside(item, "按钮", "窗口"):
                raise RuntimeError(f"论道_座位：目标「{target_name}」预测按钮中心不在窗口内，已停止且未点击")
            protect_end_time = int(target.get("protect_end_time") or 0)
            if protect_end_time > int(time.time() * 1000):
                raise RuntimeError(f"论道_座位：目标「{target_name}」仍在保护时间，已停止且未点击")
            role_id = int(target.get("role_id") or 0)
            if role_id:
                latest = self._refresh_daily_lundao_runtime_facts(
                    reason="daily-lundao-before-kick",
                    room_id=int(room_id),
                )
                latest_roster = latest.get("roster") if isinstance(latest.get("roster"), dict) else {}
                target_seat_id = int(target.get("seat_id") or target.get("id") or 0)
                still_present = any(
                    isinstance(seat, dict)
                    and int(seat.get("seat_id") or 0) == target_seat_id
                    and isinstance(seat.get("owner"), dict)
                    and int(seat["owner"].get("role_id") or 0) == role_id
                    for seat in latest_roster.get("seats") or []
                )
                if not still_present:
                    self._log(
                        "skip",
                        f"论道_座位：目标「{target_name}」已不在原座位，未点击并延后复查",
                    )
                    return {
                        "status": "target_changed",
                        "target": {"id": target_id, "name": target_name},
                        "scene_id": 297,
                        "score": 0.0,
                    }
            runtime.click_floating_item_field(item, "按钮")
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(runtime)
            if dialogue_result.get("status") == "prerequisite_required":
                return dialogue_result
            return {
                "status": "request_sent",
                "target": {"id": target_id, "name": target_name},
                "scene_id": dialogue_result.get("scene_id"),
                "score": dialogue_result.get("score"),
            }

        fallback_text = ""
        fallback_similarity = -1.0
        for _index in range(31):
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            items = runtime.find_floating_items_by_anchor_text(
                297,
                "模板",
                "区服姓名",
                target_name,
                container_shape="窗口",
                frame_data_url=frame,
                match_mode="name",
            )
            if items:
                best = items[0]
                if best.name_similarity > fallback_similarity:
                    fallback_text = best.text
                    fallback_similarity = best.name_similarity
                if best.name_similarity >= DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD:
                    if (
                        runtime.floating_item_is_fully_inside(best, "窗口")
                        and runtime.floating_item_field_is_inside(best, "按钮", "窗口")
                    ):
                        return (yield from request_kick(best))
                    self._log(
                        "detail",
                        f"论道_座位：目标「{target_name}」当前贴近窗口裁剪边缘，继续滚动后重新定位",
                    )
            changed = yield from runtime.scroll_shape_content(297, "窗口")
            if not changed:
                break
        if not fallback_text:
            raise RuntimeError(f"论道_座位：滚动完整个 #297[窗口] 后没有可用 OCR 姓名，已停止且未点击")
        raise RuntimeError(
            f"论道_座位：全列表最高相似度 OCR「{fallback_text}」仅 "
            f"{fallback_similarity:.0%}，低于姓名可信阈值 "
            f"{DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD:.0%}，已停止且未点击"
        )

    def _confirm_daily_lundao_kick_request(self, runtime: Any, *, start_scene: int | None = None):
        """忽略过渡帧，依次在 #371 发起请离、在 #372 确认。"""
        if start_scene is None:
            start_scene = yield from self._wait_daily_lundao_kick_request_result(runtime)
        if start_scene == 564:
            return (yield from self._leave_daily_lundao_rule_block_to_world(runtime))
        if start_scene == 371:
            # 这类人物选项偶尔会吞掉第一次点击。点击后必须同时观察来源页与
            # 目标页；只等待 #372 会把仍然可靠存在的 #371 错报成 unknown，
            # 然后无意义地盲等到超时。
            for attempt in range(1, 4):
                frame = runtime.cur_frame(update=True)
                tokens = runtime.ocr_tokens_in_shapes(371, ["请他让座"], frame_data_url=frame)
                candidates: list[dict[str, float]] = []
                for fragment in group_ocr_tokens(tokens):
                    text = re.sub(r"\s+", "", _sanitize_ocr_text(fragment.get("text")))
                    if text.count("请他让座") != 1:
                        continue
                    fragment_tokens = query_spatial_ocr(tokens, fragment)["tokens"]
                    box = locate_text_box(fragment_tokens, "请他让座")
                    if box is not None:
                        candidates.append(box)
                if len(candidates) != 1:
                    raise RuntimeError(f"论道_座位：#371 未唯一定位「请他让座」文字行，候选={len(candidates)}，已停止且未点击")
                box = candidates[0]
                runtime.click_frame_point(371, float(box["x"]) + float(box["w"]) / 2, float(box["y"]) + float(box["h"]) / 2)
                yield from runtime.wait_action_settle(1.5)
                observed = yield from runtime.wait_scene(
                    371,
                    372,
                    373,
                    375,
                    295,
                    52,
                    timeout=8.0,
                    label=f"论道_座位：第 {attempt}/3 次点击请他让座后确认来源或落点",
                )
                start_scene = _lundao_waited_scene_id(observed)
                if start_scene != 371:
                    break
                self._log("warning", f"论道_座位：第 {attempt}/3 次点击「请他让座」未离开 #371，重新定位后重试")
            if start_scene == 371:
                raise RuntimeError("论道_座位：连续 3 次点击「请他让座」仍停留 #371，已停止避免无限重试")
            if start_scene in {373, 375, 295, 52}:
                return (yield from self._advance_daily_lundao_kick_dialogue(runtime, start_scene=start_scene))
        if start_scene != 372:
            raise RuntimeError(f"论道_座位：请离确认节点只接受 #371/#372，当前 #{start_scene}")
        runtime.click_shape_center(372, "确定")
        # #372 后会进入若干段同坐标对话。无需把每一段都识别成 #373；
        # 先给首段画面稳定时间，随后只以正式终点 #375 是否出现作为循环条件。
        yield from runtime.wait_action_settle(1.5)
        return (yield from self._advance_daily_lundao_kick_dialogue(runtime))

    def _wait_daily_lundao_kick_request_result(
        self,
        runtime: Any,
        *,
        timeout: float = 180.0,
    ):
        """Wait through the dojo auto-route for the row button outcome."""

        scene = yield from runtime.wait_scene(
            371,
            564,
            timeout=timeout,
            label="论道_座位：等待 #297 动态条目按钮的局部结果 #371/#564",
        )
        return _lundao_waited_scene_id(scene)

    def _advance_daily_lundao_kick_dialogue(
        self,
        runtime: Any,
        *,
        start_scene: int | None = None,
    ) -> dict[str, Any]:
        """推进战前与战后两段对话，并兼容胜利浮层后直接进入入座链路。"""
        pre_battle_clicks = 0
        battle_scene = start_scene
        if battle_scene not in {373, 375, 295, 52}:
            battle_scene = yield from runtime.wait_scene(
                373,
                375,
                295,
                52,
                timeout=20.0,
                label="论道_座位：等待战前对话/战斗/结束",
            )
            battle_scene = _lundao_waited_scene_id(battle_scene)
        if battle_scene == 52:
            return {
                "status": "dialogue_finished",
                "clicks": 0,
                "pre_battle_clicks": 0,
                "post_battle_clicks": 0,
                "scene_id": 52,
                "score": 100.0,
            }
        if battle_scene == 373:
            pre_battle_clicks = yield from runtime.advance_dialogue(
                373,
                "聊天按钮",
                label="论道_座位：推进战前对话",
            )
            battle_scene = yield from runtime.wait_scene(
                375,
                295,
                52,
                186,
                303,
                timeout=30.0,
                label="论道_座位：战前对话结束后等待战斗/胜利/入座对话",
            )
            battle_scene = _lundao_waited_scene_id(battle_scene)
            if battle_scene in {52, 186, 303}:
                return {
                    "status": "dialogue_finished",
                    "clicks": pre_battle_clicks,
                    "pre_battle_clicks": pre_battle_clicks,
                    "post_battle_clicks": 0,
                    "scene_id": int(battle_scene),
                    "score": 100.0,
                }

        # #375 是论道自己的胜利浮层，#295 是兼容的通用胜利浮层；两者都
        # 必须点击各自正式标注的「关闭」，随后只等待论道战后对话或入座节点。
        # 看见胜利只能表示战斗结束，不能提前报作业成功。
        after_battle_scene = battle_scene
        if after_battle_scene not in {375, 295}:
            after_battle_scene = yield from runtime.wait_scene(
                373,
                52,
                375,
                295,
                timeout=180.0,
                layer0_wait_seconds=180.0,
                label="论道_座位：等待战斗结束后的对话/#52/#375/#295",
            )
            after_battle_scene = _lundao_waited_scene_id(after_battle_scene)
        if after_battle_scene in {375, 295}:
            victory_scene_id = int(after_battle_scene)
            runtime.click_shape_center(victory_scene_id, "关闭")
            yield from runtime.wait_action_settle(1.5)
            after_battle_scene = yield from runtime.wait_scene(
                373,
                52,
                329,
                301,
                302,
                303,
                timeout=30.0,
                label="论道_座位：关闭胜利浮层后等待战后对话/入座",
            )
            after_battle_scene = _lundao_waited_scene_id(after_battle_scene)
        if after_battle_scene == 52:
            return {
                "status": "dialogue_finished",
                "clicks": pre_battle_clicks,
                "pre_battle_clicks": pre_battle_clicks,
                "post_battle_clicks": 0,
                "scene_id": 52,
                "score": 100.0,
            }
        post_battle_clicks = 0
        if after_battle_scene == 373:
            post_battle_clicks = yield from runtime.advance_dialogue(
                373,
                "聊天按钮",
                label="论道_座位：推进战后对话",
            )
            after_battle_scene = yield from runtime.wait_scene(
                52,
                329,
                301,
                302,
                303,
                timeout=30.0,
                label="论道_座位：战后对话结束后等待入座",
            )
            after_battle_scene = _lundao_waited_scene_id(after_battle_scene)
        if after_battle_scene == 52:
            return {
                "status": "dialogue_finished",
                "clicks": pre_battle_clicks + post_battle_clicks,
                "pre_battle_clicks": pre_battle_clicks,
                "post_battle_clicks": post_battle_clicks,
                "scene_id": 52,
                "score": 100.0,
            }
        scene_id, score, _frame = runtime.current_scene([329, 301, 302, 303], update=True)
        if scene_id is None:
            raise RuntimeError("论道_座位：关闭胜利浮层后未确认战后对话或入座落点")
        return {
            "status": "battle_won",
            "clicks": pre_battle_clicks + post_battle_clicks,
            "pre_battle_clicks": pre_battle_clicks,
            "post_battle_clicks": post_battle_clicks,
            "scene_id": scene_id,
            "score": score,
        }

    def _run_daily_lundao_empty_seat_strategy(
        self,
        runtime: Any,
        *,
        transition_timeout: float = 45.0,
    ):
        """#298 空位策略；复用既有入座及后续确认落点，不复制收尾。"""
        yield from runtime.wait_click(298, "入座")
        yield from runtime.wait_action_settle(1.5)
        transition_timeout = max(20.0, min(120.0, float(transition_timeout)))
        deadline = time.monotonic() + transition_timeout
        last_text = ""
        while time.monotonic() < deadline:
            frame = runtime.cur_frame(update=True)
            last_text = runtime.ocr_text(frame)
            scene_id, score, _frame = runtime.current_scene(
                [300, 329, 301, 302, 303],
                frame_data_url=frame,
            )
            if scene_id in {300, 329, 301, 302, 303}:
                return scene_id, score
            if self._daily_lundao_text_is_dojo_travel_prompt(last_text):
                # #300 and #329 are two visual variants of the same business
                # confirmation.  Full-frame OCR is only a fallback for this
                # exact, low-risk prompt; generic seat text must not be mapped
                # to either scene.
                self._log(
                    "detail",
                    "论道_座位：图模型未稳定区分 #300/#329，但 OCR 已确认前往道场弹窗",
                )
                return 300, float(score or 0.0)
            yield from runtime.wait_action_settle(1.0)
        raise TimeoutError(
            f"论道_座位：点击 #298「入座」后 {transition_timeout:g} 秒内未确认空位链 "
            "#300/#329/#301/#302/#303，"
            f"OCR={last_text[:160]}"
        )

    def _advance_daily_lundao_dojo_travel_confirmation(
        self,
        runtime: Any,
        scene_id: int | None,
    ):
        """Consume the equivalent #300/#329 prompt without stale-scene races."""

        prompt_ids = (300, 329)
        # Do not accept #186 immediately after confirming the room.  The old
        # seated page remains visible briefly underneath the transition and can
        # match #186 before the new #301 seat-choice prompt appears.  #186 is a
        # valid terminal only after the explicit #301/#302 confirmation chain.
        # #53 has a broad identity shared by the stable seated page and the
        # dynamic auto-navigation transition.  Keep it inside this local
        # transaction's observation domain, but accept it only after both the
        # transition text has disappeared and Runtime proves ``seated``.
        target_ids = (301, 302, 303, 52, 53, 34, 69)
        preferred_prompt_id = int(scene_id) if scene_id in prompt_ids else 300
        last_scene_id = scene_id
        last_score = 0.0
        last_text = ""
        deadline = time.monotonic() + 60.0
        confirm_attempts = 0
        while time.monotonic() < deadline:
            detected, score, frame = runtime.current_scene(
                [*prompt_ids, *target_ids],
                update=True,
            )
            last_scene_id, last_score = detected, float(score or 0.0)
            last_text = runtime.ocr_text(frame)
            if detected == 53:
                if (
                    self._daily_lundao_text_is_seated(last_text)
                    and self._daily_lundao_runtime_confirms_seated()
                ):
                    return 53, float(score or 0.0)
                yield from runtime.wait_action_settle(1.0)
                continue
            if detected in target_ids:
                return int(detected), float(score or 0.0)
            if detected in prompt_ids:
                preferred_prompt_id = int(detected)
            elif not self._daily_lundao_text_is_dojo_travel_prompt(last_text):
                yield from runtime.wait_action_settle(1.0)
                continue

            if confirm_attempts >= 3:
                yield from runtime.wait_action_settle(1.0)
                continue
            confirm_attempts += 1
            self._log(
                "action",
                f"论道_座位：确认前往道场弹窗 #{preferred_prompt_id}",
            )
            runtime.click_shape_center(preferred_prompt_id, "确认")
            yield from runtime.wait_action_settle(1.5)
            remaining = max(0.1, deadline - time.monotonic())
            try:
                landed = yield from runtime.wait_scene(
                    *prompt_ids,
                    *target_ids,
                    timeout=min(20.0, remaining),
                    label="论道_座位：确认前往道场后等待入座链",
                )
            except TimeoutError:
                continue
            landed = _lundao_waited_scene_id(landed)
            if landed in target_ids and landed != 53:
                return int(landed), 100.0
            if landed in prompt_ids:
                preferred_prompt_id = int(landed)
                continue
        raise RuntimeError(
            "论道_座位：前往道场确认弹窗连续处理后仍未进入入座链，"
            f"最后 #{last_scene_id if last_scene_id is not None else 'unknown'} "
            f"{last_score:.0f}%，OCR={last_text[:160]}"
        )

    def _prefer_daily_lundao_seat_choice_scene(
        self,
        runtime: Any,
        scene_id: int | None,
        score: float = 0.0,
        *,
        frame_data_url: str | None = None,
    ) -> tuple[int | None, float]:
        """Disambiguate broad #53 with the official #301[入座] shape."""

        if scene_id != 53:
            return scene_id, float(score or 0.0)
        frame = frame_data_url or runtime.cur_frame(update=True)
        seat_score = float(runtime.shape_score(301, "入座", frame_data_url=frame) or 0.0)
        if seat_score >= 80.0:
            self._log(
                "detail",
                "论道_座位：宽泛 #53 同帧命中正式 #301[入座]，"
                "按尚未入座的座位确认链继续",
            )
            return 301, seat_score
        return 53, float(score or 0.0)

    def _complete_daily_lundao_seat_and_leave(
        self,
        runtime: Any,
        stop_event: threading.Event,
        scene_id: int | None,
        score: float = 0.0,
    ) -> str:
        """两种抢座策略共用的确认入座、结果处理、离场和闭环收尾。"""
        # A fresh full Job Cell may start while a kick transaction is already
        # visible.  Keep this closure idempotent instead of requiring its caller
        # to normalize every dialogue/battle scene first.
        if scene_id in {371, 372}:
            dialogue_result = yield from self._confirm_daily_lundao_kick_request(
                runtime,
                start_scene=scene_id,
            )
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        elif scene_id in {373, 375, 295}:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(
                runtime,
                start_scene=scene_id,
            )
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        scene_id, score = self._prefer_daily_lundao_seat_choice_scene(
            runtime,
            scene_id,
            score,
        )
        if scene_id in {300, 329}:
            scene_id, score = yield from self._advance_daily_lundao_dojo_travel_confirmation(
                runtime,
                scene_id,
            )
            scene_id, score = self._prefer_daily_lundao_seat_choice_scene(
                runtime,
                scene_id,
                score,
            )
        if scene_id in {301, 302}:
            scene_id, score = yield from self._advance_daily_lundao_seat_confirmation(runtime, stop_event, scene_id)
        if scene_id in {303, 373}:
            scene_id, score = yield from self._advance_daily_lundao_post_seat_dialogue(
                runtime,
                scene_id,
            )
        if scene_id == 52:
            yield from runtime.wait_click_then_view(52, "确认", wait_leave=True)
            scene_id, score, frame_after = runtime.current_scene([53, 69, 34, 85, 186, 52], update=True)
            text_after = runtime.ocr_text(frame_after)
            if self._daily_lundao_text_is_seated(text_after):
                if scene_id == 53:
                    yield from self._leave_daily_lundao_seated_for_daily_entry(runtime, 53)
                    self._log("success", "论道_座位：已确认听道收益并退出回世界")
                    return "success"
                if scene_id == 186:
                    from backend.core.fanxiu.instrumentation.lundao import (
                        read_lundao_snapshot,
                    )

                    seated_status = read_lundao_snapshot()
                    if (
                        seated_status.get("available")
                        and seated_status.get("complete")
                        and seated_status.get("seated") is True
                    ):
                        yield from self._leave_shared_scene_186_to_world(
                            runtime,
                            label="论道_座位",
                        )
                        self._log(
                            "success",
                            "论道_座位：OCR 与 Runtime 均确认已入座，"
                            "已从共享 #186 点击「离开」并退出回世界",
                        )
                        return "success"
                    raise RuntimeError(
                        "论道_座位：OCR 显示闻道中且当前为共享 #186，"
                        "但 Runtime 未确认 seated=true，已停止避免误点离开"
                    )
                raise RuntimeError(
                    f"论道_座位：OCR 已确认闻道中，但图模型未识别到正式闻道场景 #53，"
                    f"当前 #{scene_id if scene_id is not None else 'unknown'}，已停止避免借用其它场景坐标"
                )
        if scene_id in {69, 34}:
            self._log("success", f"论道_座位：已确认听道收益并返回 #{scene_id}")
            return "success"
        if scene_id == 53:
            yield from self._leave_daily_lundao_seated_for_daily_entry(runtime, 53)
            self._log("success", "论道_座位：已完成听道并退出回世界")
            return "success"
        if scene_id == 186:
            yield from self._leave_shared_scene_186_to_world(runtime, label="论道_座位")
            self._log("success", "论道_座位：已从 #186 点击「离开」并退出回世界")
            return "success"
        if scene_id == 54:
            return (yield from self._confirm_daily_lundao_exit_to_world(runtime))
        raise RuntimeError(f"论道_座位：抢座或收尾落点尚未实现，当前 #{scene_id if scene_id is not None else 'unknown'} {score:.0f}%")

    def _advance_daily_lundao_post_seat_dialogue(
        self,
        runtime: Any,
        start_scene: int,
        *,
        max_cycles: int = 8,
    ) -> tuple[int, float]:
        """Advance the bounded #303/#373 post-seat dialogue transaction."""

        terminal_scenes = (52, 53, 186, 329, 301, 302)
        candidates = [303, 373, *terminal_scenes]
        scene_id = int(start_scene)
        score = 100.0
        for cycle in range(1, max_cycles + 1):
            observed_scene, observed_score, _frame = runtime.current_scene(
                candidates,
                update=True,
            )
            if observed_scene in terminal_scenes:
                return int(observed_scene), float(observed_score or 0.0)
            if observed_scene not in {303, 373}:
                raise RuntimeError(
                    "论道_座位：战后对话出现未声明落点，"
                    f"当前 #{observed_scene if observed_scene is not None else 'unknown'} "
                    f"{float(observed_score or 0.0):.0f}%"
                )
            scene_id = int(observed_scene)
            score = float(observed_score or 0.0)
            dialogue_shape = "对话" if scene_id == 303 else "聊天按钮"
            yield from runtime.advance_dialogue(
                scene_id,
                dialogue_shape,
                label=(
                    f"论道_座位：推进 #{scene_id} 连续人物对话"
                    f"（第 {cycle}/{max_cycles} 段）"
                ),
            )

            waited_scene = yield from runtime.wait_scene(
                *candidates,
                timeout=30.0,
                label="论道_座位：人物对话后等待入座/下一段对话",
            )
            if isinstance(waited_scene, View):
                if waited_scene.id is None:
                    raise RuntimeError("论道_座位：人物对话后 View 缺少场景编号")
                scene_id = int(waited_scene.id)
            else:
                scene_id = int(waited_scene)
            if scene_id in terminal_scenes:
                # wait_scene 已经在真实稳定帧上证明了终点。这里若立即再截一帧，
                # 可能正好采到 UI 切换过渡而得到 unknown，反而推翻刚取得的
                # 强证据。终点直接消费 wait_scene 的结论；只有下一轮人物对话
                # 才重新取帧确认。
                return int(scene_id), 100.0
            if scene_id not in {303, 373}:
                raise RuntimeError(
                    "论道_座位：人物对话后无法证明进入已知后继，"
                    f"当前 #{scene_id if scene_id is not None else 'unknown'}"
                )

        raise RuntimeError(
            "论道_座位：#303/#373 人物对话超过有界推进次数，"
            f"最后 #{scene_id} {score:.0f}%"
        )

    def _finish_daily_lundao_in_progress(self, runtime: Any, *, continue_to_selection: bool = False) -> str | int:
        """Leave #304; dynamic strategy may continue on the dojo selection page."""
        runtime.click_shape_center(304, "返回")
        if continue_to_selection:
            next_scene = yield from runtime.wait_scene(296, 34, 69, timeout=15.0, label="论道_座位：#304 返回后等待道场选择")
            if isinstance(next_scene, View):
                if next_scene.id is None:
                    raise RuntimeError("论道_座位：#304 返回后的 View 缺少场景编号")
                return int(next_scene.id)
            return int(next_scene)
        yield from runtime.wait_action_settle(1.5)
        self._log("success", "论道_座位：#304 论道中，点击「返回」")
        return "success"

    def _daily_lundao_text_is_reward(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            ("听道收益" in compact and ("今日闻道剩余" in compact or "基础保护时间" in compact))
            or ("本次论道主题" in compact and "今日闻道剩余" in compact and "基础保护时间" in compact)
        )

    def _daily_lundao_text_is_seated(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if "自动寻路中" in compact:
            return False
        return (
            ("闻道剩余时间" in compact and ("道场闻道收益" in compact or "累积获得" in compact))
            or ("闻道感悟" in compact and "剩余座位" in compact and "离开" in compact)
        )

    def _daily_lundao_runtime_confirms_seated(self) -> bool:
        from backend.core.fanxiu.instrumentation.lundao import read_lundao_snapshot

        status = read_lundao_snapshot()
        return bool(
            status.get("available")
            and status.get("complete")
            and status.get("seated") is True
        )

    def _daily_lundao_text_is_exit_confirm(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return "是否要退出道场" in compact and "继续闻道" in compact

    def _daily_lundao_text_is_dojo_travel_prompt(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            "道场" in compact
            and ("前往" in compact or "是否要前往" in compact)
            and "确认" in compact
            and ("取消" in compact or "是否" in compact)
        )

    def _daily_lundao_text_is_seat_choice_prompt(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            ("听道座位" in compact and "入座" in compact)
            or "再看看别的座位" in compact
            or "座位甚佳" in compact
        )

    def _daily_lundao_text_is_seat_confirm_prompt(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            ("是否在该空位入座" in compact or ("是否" in compact and "入座" in compact))
            and (
                "当前道场" in compact
                or "论道收益" in compact
                or "剩余时间" in compact
                # 真实的简版确认框只显示这句正文和「确定」。场景模型已
                # 独立命中 #302 时，这两个语义足以确认，不能强求详情字段。
                or ("是否在该空位入座" in compact and "确定" in compact)
            )
        )

    def _confirm_daily_lundao_exit_to_world(self, runtime: Any) -> str:
        yield from runtime.wait_click_then_view(54, "确认", [34, 69], settle_seconds=1.5, timeout=20.0)
        self._log("success", "论道_座位：已确认退出道场，神识分身继续闻道")
        return "success"

    def _leave_shared_scene_186_to_world(self, runtime: Any, *, label: str) -> str:
        """Leave a shared internal scene without letting the guard consume its confirmation."""
        scene_id = 186
        score = 100.0
        for attempt in range(1, 5):
            if scene_id in {34, 69}:
                return "success"
            if scene_id in {375, 295}:
                self._log(
                    "action",
                    f"{label}：离开道场后出现已知胜利浮层 #{scene_id}，"
                    "继续按场景图返回 #34",
                )
                yield from runtime.goto_view(34)
                return "success"
            if scene_id == 386:
                self._log(
                    "action",
                    f"{label}：已离开道场并落到已知活动浮层 #386，继续按场景图返回 #34",
                )
                yield from runtime.goto_view(34)
                return "success"
            # #47 is the generic parent match briefly visible before the more
            # specific #86 leave-confirmation OCR becomes ready. Keep it claimed
            # by the business flow so the popup guard cannot click its background.
            with runtime.expect_views(47, 86, 289, 34, 69, 186, 85, 386, 375, 295):
                if scene_id in {289, 86}:
                    self._log("action", f"{label}：识别正式确认场景 #{scene_id}，点击「确认」")
                    waited_scene = yield from runtime.wait_click_then_view(
                        scene_id,
                            "确认",
                            [34, 69, 186, 85, 386, 375, 295],
                        settle_seconds=1.5,
                        timeout=15.0,
                        max_clicks=1,
                    )
                elif scene_id in {186, 85}:
                    self._log(
                        "action",
                        f"{label}：收尾识别 #{scene_id}，点击正式标注「离开」"
                        f"（第 {attempt}/4 次）",
                    )
                    try:
                        waited_scene = yield from runtime.wait_click_then_view(
                            scene_id,
                            "离开",
                            [47, 34, 69, 289, 86, 85, 386, 375, 295],
                            settle_seconds=1.5,
                            timeout=12.0,
                            max_clicks=1,
                        )
                    except TimeoutError:
                        scene_id, score, _frame = runtime.current_scene(
                            [34, 69, 289, 86, 186, 85, 386, 375, 295],
                            update=True,
                        )
                        continue
                    landed_id = (
                        int(waited_scene.id)
                        if isinstance(waited_scene, View)
                        else int(waited_scene)
                    )
                    if landed_id == 47:
                        self._log(
                            "action",
                            f"{label}：#186[离开] 后命中父级确认层 #47，"
                            "按操作上下文点击已有 #86[确认]",
                        )
                        runtime.click_shape_center(86, "确认")
                        yield from runtime.wait_action_settle(1.5)
                        waited_scene = yield from runtime.wait_view(
                            34,
                            69,
                            186,
                            85,
                            53,
                            386,
                            375,
                            295,
                            timeout=15.0,
                            label=f"{label}：确认离开后等待落点",
                        )
                else:
                    raise RuntimeError(
                        f"{label}：离开内部场景落到未声明状态 "
                        f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
                    )
            scene_id = (
                int(waited_scene.id)
                if isinstance(waited_scene, View)
                else int(waited_scene)
            )
            score = 100.0
        if scene_id in {34, 69}:
            return "success"
        if scene_id in {289, 86}:
            # The bounded leave budget counts page-level actions.  A slow #85
            # self-loop may expose its legitimate confirmation only on the
            # final action; do not discard that already-proven business state
            # merely because there is no next loop iteration left.
            self._log(
                "action",
                f"{label}：离场预算末步已出现正式确认场景 #{scene_id}，点击「确认」收口",
            )
            waited_scene = yield from runtime.wait_click_then_view(
                scene_id,
                "确认",
                [34, 69, 186, 85, 386, 375, 295],
                settle_seconds=1.5,
                timeout=15.0,
                max_clicks=1,
            )
            scene_id = (
                int(waited_scene.id)
                if isinstance(waited_scene, View)
                else int(waited_scene)
            )
            if scene_id in {34, 69}:
                return "success"
        raise RuntimeError(
            f"{label}：离开内部场景有界重试耗尽，最后 "
            f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
        )

    def _leave_daily_lundao_seated_for_daily_entry(self, runtime: Any, scene_id: int | None):
        if scene_id != 53:
            raise RuntimeError(
                f"论道闻道中只接受正式场景 #53，当前 #{scene_id if scene_id is not None else 'unknown'}，"
                "禁止借用其它场景的「离开」坐标"
            )
        runtime.click_shape_center(53, "离开")
        yield from runtime.wait_action_settle(1.5)
        next_scene_id = yield from runtime.wait_scene(
            54,
            34,
            69,
            186,
            timeout=30.0,
            label="论道_座位：点击 #53「离开」后等待退出确认/世界",
        )
        next_scene_id = _lundao_waited_scene_id(next_scene_id)
        if next_scene_id == 54:
            yield from self._confirm_daily_lundao_exit_to_world(runtime)
            return "success"
        if next_scene_id in {34, 69}:
            return "success"
        if next_scene_id == 186:
            return (yield from self._leave_shared_scene_186_to_world(runtime, label="论道_座位"))
        raise RuntimeError(
            f"论道闻道中点击「离开」后未到退出确认/世界/日常，当前 #{next_scene_id if next_scene_id is not None else 'unknown'}"
        )

    def _advance_daily_lundao_seat_confirmation(
        self,
        runtime: Any,
        stop_event: threading.Event,
        scene_id: int | None,
    ):
        last_scene_id = scene_id
        last_score = 0.0
        for _index in range(4):
            self._raise_if_stopped(stop_event)
            if scene_id in {300, 329}:
                scene_id, last_score = yield from self._advance_daily_lundao_dojo_travel_confirmation(
                    runtime,
                    scene_id,
                )
            if scene_id == 301:
                # #301[入座] itself carries the required OCR constraint.  Do
                # not re-run whole-scene recognition (which also injects the
                # generic #47 popup domain) as a second "safety gate".  The
                # #302 action itself is intentionally unconstrained; current
                # game versions may say either “空位入座” or “更换到该座位”,
                # so an old #302 identity string must not become a new gate.
                yield from runtime.wait_click(301, "入座")
                yield from runtime.wait_action_settle(2.0)
                scene_id = 302
            if scene_id == 302:
                # The preceding #301 action established the business chain.
                # #302[确定] deliberately has no visual constraint, so
                # wait_click performs a direct fixed action rather than
                # repeating scene/OCR recognition.
                yield from runtime.wait_click(302, "确定")
                yield from runtime.wait_action_settle(2.0)
            scene_id, score, _frame = runtime.current_scene(
                [303, 301, 302, 329, 52, 53, 186, 237, 18, 14, 69, 34],
                update=True,
                handle_interruptions=False,
            )
            last_scene_id, last_score = scene_id, float(score)
            current_text = runtime.ocr_text(_frame)
            if self._daily_lundao_text_is_seated(current_text):
                if scene_id in {53, 186}:
                    return scene_id, float(score)
                if scene_id is None:
                    from backend.core.fanxiu.instrumentation.lundao import (
                        read_lundao_snapshot,
                    )

                    seated_status = read_lundao_snapshot()
                    if (
                        seated_status.get("available")
                        and seated_status.get("complete")
                        and seated_status.get("seated") is True
                    ):
                        self._log(
                            "info",
                            "论道_座位：图模型未稳定识别 #186，但 OCR 与 Runtime "
                            "均确认已入座，按共享 #186 的正式离场链收尾",
                        )
                        return 186, float(score)
                    self._log(
                        "detail",
                        "论道_座位：OCR 已显示闻道中但 #186 身份尚未稳定，"
                        "继续等待，不点击通用返回",
                    )
                    yield from runtime.wait_action_settle(1.0)
                    continue
                raise RuntimeError(
                    f"论道_座位：OCR 已确认闻道中，但图模型落点为 "
                    f"#{scene_id}，禁止映射成其它场景"
                )
            scene_id, score = self._prefer_daily_lundao_seat_choice_scene(
                runtime,
                scene_id,
                score,
                frame_data_url=_frame,
            )
            last_scene_id, last_score = scene_id, float(score)
            if scene_id in {303, 52, 53}:
                return scene_id, float(score)
            if scene_id in {237, 18, 14}:
                raise RuntimeError(f"论道_座位：入座确认后落到非论道页面 #{scene_id}，已停止避免误点")
            if scene_id is None:
                if self._daily_lundao_text_is_seat_choice_prompt(current_text):
                    scene_id = 301
                    continue
                if self._daily_lundao_text_is_seat_confirm_prompt(current_text):
                    scene_id = 302
                    continue
                yield from runtime.wait_action_settle(1.0)
                continue
            if scene_id == 301:
                continue
            if scene_id == 302:
                continue
            if scene_id == 329:
                continue
            return scene_id, float(score)
        raise RuntimeError(f"论道_座位：#301/#302 入座确认循环超过上限，最后 #{last_scene_id if last_scene_id is not None else 'unknown'} {last_score:.0f}%")

    def _daily_dongtian_text_is_home(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text)
        return bool("洞天福地" in compact and ("我的编队" in compact or "收益" in compact or "联盟占领" in compact))

    def _wait_daily_dongtian_home(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
        allow_claim_page: bool = False,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        timeout = float(payload.get("dongtian_home_timeout") or 25.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            scene_candidates = [284, 279] if allow_claim_page else [279]
            scene_id, score, frame = runtime.current_scene(scene_candidates, update=True)
            text = runtime.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, float(score), text
            if allow_claim_page and scene_id == 284:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：已直接到收益领取页",
                        phase="daily_dongtian_claim",
                        current_scene=284,
                    )
                    self._log_locked("success", f"{task_label}：入口直接落到 #284 {score:.0f}%，跳过 #279「收益」")
                return 284
            if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：已到洞天福地主页",
                        phase="daily_dongtian_home",
                        current_scene=279,
                    )
                    self._log_locked("success", f"{task_label}：识别 #279 {score:.0f}%，OCR={text[:120]}")
                return 279
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{task_label}：等待 #279 洞天福地主页超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:180]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待洞天福地主页，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_dongtian_wait_home",
                    current_scene=scene_id,
                )

    def _record_daily_dongtian_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = (
            next_business_time(("14:00",))
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian"),
            next_time,
        )
        self._log("success", f"洞天_领取：{message}，下次 {next_time}")
        return next_time

    def _daily_dongtian_has_shape(self, ctx: dict[str, Any], scene_id: int, title: str) -> bool:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image = images.get(scene_id)
        if not isinstance(image, dict):
            return False
        return any(str(shape.get("title") or "") == title for shape in image.get("shapes") or [])

    def _claim_daily_dongtian_profit(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_label: str,
        start_scene_id: int | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(284), dict):
            raise RuntimeError(f"{task_label}：缺少 #284 收益领取页标注，无法把 #279「收益」后的下一步作为场景锚点")
        if not self._daily_dongtian_has_shape(ctx, 284, "领取"):
            raise RuntimeError(f"{task_label}：缺少 #284「领取」shape 标注，无法执行下一步领取")

        if start_scene_id is None:
            start_scene_id, _score, _frame = runtime.current_scene([284, 279], update=True)
        claimed = start_scene_id == 284
        if start_scene_id == 279:
            outcome = yield from runtime.wait_click_then_any(
                279,
                "收益",
                {
                    "claim_page": runtime.view_visible(284),
                    "no_reward": runtime.view_visible(279),
                },
                label=f"{task_label}：点击 #279「收益」后识别领取页或无收益主页",
                settle_seconds=float(payload.get("dongtian_profit_settle_seconds") or 2.0),
            )
            claimed = outcome == "claim_page"
            if not claimed:
                self._log(
                    "success",
                    f"{task_label}：#279「收益」点击后仍为可靠洞天主页，当前没有待领取收益",
                )
        elif start_scene_id == 284:
            self._log("detail", f"{task_label}：当前已在 #284 收益领取页，直接领取，不重复点击 #279「收益」")
        else:
            raise RuntimeError(f"{task_label}：领取收益前应在 #279/#284，实际 #{start_scene_id if start_scene_id is not None else 'unknown'}")
        if claimed:
            yield from runtime.wait_click_then_view(
                284,
                "领取",
                279,
                label=f"{task_label}：点击 #284「领取」后等待 #279 洞天主页",
                settle_seconds=max(2.0, float(payload.get("dongtian_claim_settle_seconds") or 2.0)),
            )
            scene_after, score_after, frame_after = runtime.current_scene([279], update=True)
            text_after = runtime.ocr_text(frame_after)
            self._log("success", f"{task_label}：已点击 #284「领取」并回到 #279，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return_landing = yield from runtime.wait_click_then_view(
            279,
            "返回",
            34,
            20,
            label=f"{task_label}：点击 #279「返回」后等待 #34 世界或 #20 绿瓶",
            settle_seconds=float(payload.get("dongtian_return_settle_seconds") or 2.0),
        )
        if int(getattr(return_landing, "id", return_landing)) == 20:
            self._log(
                "detail",
                f"{task_label}：#279「返回」落到 #20，沿正式场景图继续返回世界",
            )
            result = runtime.go_scene(34)
            if hasattr(result, "send"):
                yield from result
        scene_return, score_return, frame_return = runtime.current_scene([34], update=True)
        if scene_return != 34 or score_return < 90:
            raise RuntimeError(
                f"{task_label}：离开洞天后未到可靠 #34，当前 "
                f"#{scene_return if scene_return is not None else 'unknown'} {score_return:.0f}%"
            )
        text_return = runtime.ocr_text(frame_return)
        self._log("success", f"{task_label}：已从 #279 返回 #34，当前 #{scene_return} {score_return:.0f}%，OCR={text_return[:160]}")
        return claimed

    def daily_dongtian_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        decision = self._daily_window_admission(
            now=_behavior_tree_runtime._now(),
            trigger=time_cls(14, 0),
            cutoff=time_cls(22, 0),
            label="洞天_领取",
            window_text="14:00-22:00",
        )
        return self._persist_admission_decision(payload, decision)

    def _execute_daily_dongtian_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = {"max_scrolls": 24, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_洞天福地资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(279), dict):
            raise RuntimeError("缺少 #279「洞天福地」标注，无法确认洞天主页")

        task_label = "洞天_领取"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        # Reward-memory discovery can require a full LuaJIT heap scan when its
        # cached roots have drifted.  That snapshot is only an optional GUI
        # short-circuit for this claim job, so do not let it block the normal,
        # idempotent #279/#284 flow for minutes.  Explicit overrides remain
        # available to tests and callers that already hold a fresh snapshot.
        if isinstance(payload.get("__dongtian_runtime_snapshot_override"), dict):
            reward_snapshot = self._daily_dongtian_runtime_snapshot(payload)
        else:
            reward_snapshot = {}
            self._log(
                "detail",
                "洞天_领取：跳过可能触发全量内存扫描的收益预判，直接执行 GUI 幂等领取流程",
            )
        reward_available = reward_snapshot.get("reward_available")
        if reward_available is False and bool(reward_snapshot.get("complete")):
            self._record_daily_dongtian_done(
                payload,
                message="Runtime 已确认当前没有待领取的洞天收益",
            )
            with self._lock:
                self._set_status_locked(
                    "success",
                    "洞天_领取：Runtime 已确认当前没有待领取收益",
                    phase="daily_dongtian_done",
                )
            return "success"
        if reward_available is True and bool(reward_snapshot.get("complete")):
            self._log(
                "success",
                "洞天_领取：Runtime 已确认存在待领取收益，继续执行 GUI 领取动作",
            )
        else:
            self._log(
                "detail",
                "洞天_领取：Runtime 收益状态不可用，保留原 GUI 流程兜底",
            )
        scene_id, _score, frame = runtime.current_scene([284, 279, 69, 34, 47], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 284:
            claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=284)
            self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
            with self._lock:
                self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
            return "success"
        if scene_id == 279 or self._daily_dongtian_text_is_home(text):
            claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=279)
            self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
            with self._lock:
                self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
            return "success"

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, _score, frame = runtime.current_scene([284, 279, 69, 34, 47], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 284:
                    claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=284)
                    self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
                    with self._lock:
                        self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
                    return "success"
                if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                    claimed = yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=279)
                    self._record_daily_dongtian_done(payload, message="已领取洞天福地收益" if claimed is not False else "当前没有待领取的洞天收益")
                    with self._lock:
                        self._set_status_locked("success", "洞天_领取：已领取洞天福地收益" if claimed is not False else "洞天_领取：当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
                    return "success"
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
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
            title_pattern=r"收取\s*两?万\s*九|九曜\s*玄墨",
            progress_can_mark_done=False,
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-dongtian",
                task_type="daily_dongtian",
                label=task_label,
                entry_label="收取两万九曜玄墨",
            )
            return "skipped"
        landing_scene_id = yield from self._wait_daily_dongtian_home(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            allow_claim_page=True,
        )
        claimed = yield from self._claim_daily_dongtian_profit(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            start_scene_id=int(landing_scene_id),
        )
        self._record_daily_dongtian_done(
            payload,
            message=(
                "已从日常进入洞天福地并领取收益"
                if claimed is not False
                else "已从日常进入洞天福地，当前没有待领取收益"
            ),
        )
        with self._lock:
            self._set_status_locked("success", "洞天_领取：已进入洞天福地并领取收益" if claimed is not False else "洞天_领取：已进入洞天福地，当前没有待领取收益", phase="daily_dongtian_done", current_scene=279)
        return "success"

    def _record_daily_lingmai_done(self, payload: dict[str, Any], *, message: str) -> str:
        now = _behavior_tree_runtime._now()
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

        now = _behavior_tree_runtime._now()
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
            now=_behavior_tree_runtime._now(),
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
        result = yield from self._run_daily_lingmai_task(ctx, stop_event, payload)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, score, frame = runtime.current_scene([443, 318, 305, 288, 286, 285, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id in {34, 69}:
            runtime_guard = yield from self._daily_lingmai_world_runtime_guard(
                runtime,
                ctx,
                payload,
                scene_id=scene_id,
            )
            if runtime_guard is not None:
                return runtime_guard
        if scene_id == 318:
            self._log("success", f"{task_label}：当前已在 #318 灵脉奖励确认，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._confirm_daily_lingmai_reward(runtime, payload, task_label=task_label))
        if scene_id == 443:
            self._log("success", f"{task_label}：当前已在 #443 灵脉更换确认，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._confirm_daily_lingmai_switch_popup(
                runtime,
                payload,
                task_label=task_label,
                scene_id=443,
                frame=frame,
            ))
        if scene_id == 305:
            self._log("success", f"{task_label}：当前已在 #305 灵脉聚灵确认弹窗，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._confirm_daily_lingmai_gather(runtime, payload, task_label=task_label))
        if scene_id == 288:
            self._log("success", f"{task_label}：当前已在 #288，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_from_final_occupy(ctx, stop_event, payload, runtime, task_label=task_label))
        if scene_id == 286:
            self._log("success", f"{task_label}：当前已在 #286 选择空位，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, runtime, frame, task_label=task_label))
        if scene_id == 285:
            self._log("success", f"{task_label}：当前已在 #285 造化灵脉，场景分 {score:.0f}%，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_from_zaohua(ctx, stop_event, payload, runtime, frame, task_label=task_label))

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, score, frame = runtime.current_scene([443, 318, 305, 288, 286, 285, 69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 443:
                    self._log("success", f"{task_label}：当前已在 #443 灵脉更换确认，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._confirm_daily_lingmai_switch_popup(
                        runtime,
                        payload,
                        task_label=task_label,
                        scene_id=443,
                        frame=frame,
                    ))
                if scene_id == 318:
                    self._log("success", f"{task_label}：当前已在 #318 灵脉奖励确认，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._confirm_daily_lingmai_reward(runtime, payload, task_label=task_label))
                if scene_id == 305:
                    self._log("success", f"{task_label}：当前已在 #305 灵脉聚灵确认弹窗，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._confirm_daily_lingmai_gather(runtime, payload, task_label=task_label))
                if scene_id == 288:
                    self._log("success", f"{task_label}：当前已在 #288，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._continue_daily_lingmai_from_final_occupy(ctx, stop_event, payload, runtime, task_label=task_label))
                if scene_id == 286:
                    self._log("success", f"{task_label}：当前已在 #286 选择空位，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, runtime, frame, task_label=task_label))
                if scene_id == 285:
                    self._log("success", f"{task_label}：当前已在 #285 造化灵脉，场景分 {score:.0f}%，OCR={text[:160]}")
                    return (yield from self._continue_daily_lingmai_from_zaohua(ctx, stop_event, payload, runtime, frame, task_label=task_label))

        _scene_after, _score_after, frame_after = yield from self._enter_daily_lingmai_zaohua_from_world_or_daily(
            ctx,
            stop_event,
            payload,
            runtime,
            scene_id,
            frame,
            text,
            task_label=task_label,
        )
        if _scene_after is None:
            return "skipped"
        return (yield from self._continue_daily_lingmai_from_zaohua(ctx, stop_event, payload, runtime, frame_after, task_label=task_label))

    def _daily_lingmai_world_runtime_guard(
        self,
        runtime: Any,
        ctx: dict[str, Any],
        payload: dict[str, Any],
        *,
        scene_id: int,
    ):
        """Resolve completed/seated Lingmai state before opening the daily UI."""

        runtime_override = payload.get(
            "__lingmai_runtime_snapshot_override"
        )
        runtime_status = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else refresh_lingmai_daily_status()
        )
        if (
            runtime_status.get("available")
            and runtime_status.get("complete")
        ):
            ctx["_daily_lingmai_status"] = runtime_status
        else:
            return None
        try:
            remaining_ms = int(
                runtime_status.get("remaining_milliseconds")
            )
        except (TypeError, ValueError):
            return None
        self_seat = (
            runtime_status.get("self_seat_facts")
            if isinstance(
                runtime_status.get("self_seat_facts"),
                Mapping,
            )
            else {}
        )
        seated = self_seat.get("seated") is True
        if remaining_ms > 0 and not seated:
            return None
        level_plan = self._daily_lingmai_level_plan(runtime_status)
        if remaining_ms > 0 and seated and level_plan.get("action") == "try_shengmai":
            self._log(
                "info",
                "灵脉_座位：当前已坐神脉且体力 "
                f"{float(level_plan.get('strength') or 0):.0f}≥{LINGMAI_SHENGMAI_MIN_STRENGTH}，"
                "继续打开灵脉检查圣脉升级机会",
            )
            return None
        if scene_id != 34:
            yield from runtime.goto_view(34)
        if remaining_ms <= 0 or runtime_status.get("completed") is True:
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
            now=_behavior_tree_runtime._now(),
            trigger=time_cls(21, 30),
            cutoff=time_cls(22, 0),
            label="灵脉_清体力",
            window_text="21:30-22:00",
        )
        return self._persist_admission_decision(payload, decision)

    def _complete_daily_clear_task(
        self,
        payload: dict[str, Any],
        *,
        task_id: str,
        label: str,
    ) -> str:
        """Persist tomorrow's run when a nightly clear job succeeds."""

        now = _behavior_tree_runtime._now()
        next_date = (
            now.date()
            if now.time() < time_cls(21, 30)
            else now.date() + timedelta(days=1)
        )
        next_time = datetime.combine(next_date, time_cls(21, 30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
        )
        self._log("success", f"{label}：今日清理完成，下次 {next_time}")
        return "success"

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, score, frame = runtime.current_scene([589, 315, 314, 313, 312, 285, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 589:
            yield from self._check_daily_lingmai_guiyuan_upgrade(
                runtime,
                payload,
                task_label=task_label,
                already_open=True,
            )
            yield from runtime.wait_click_then_view(285, "探索", 313)
            return (yield from self._continue_daily_lingmai_clear_from_explore(runtime, payload, task_label=task_label))
        if scene_id == 315:
            return (yield from self._continue_daily_lingmai_clear_from_transient(runtime, payload, task_label=task_label))
        if scene_id == 314:
            return (yield from self._continue_daily_lingmai_clear_from_amount(runtime, payload, task_label=task_label))
        if scene_id == 313:
            return (yield from self._continue_daily_lingmai_clear_from_explore(runtime, payload, task_label=task_label))
        if scene_id == 312:
            yield from runtime.wait_click_then_view(312, "确认", 285)
            frame = runtime.cur_frame(update=True)
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(runtime, payload, task_label=task_label))
        if scene_id == 285:
            self._log("success", f"{task_label}：已在 #285 造化灵脉，继续清理体力，OCR={text[:160]}")
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(runtime, payload, task_label=task_label))
        scene_after, _score_after, frame_after = yield from self._enter_daily_lingmai_zaohua_from_world_or_daily(
            ctx,
            stop_event,
            payload,
            runtime,
            scene_id,
            frame,
            text,
            task_label=task_label,
        )
        if scene_after == 285:
            return (yield from self._continue_daily_lingmai_clear_from_zaohua(runtime, payload, task_label=task_label))
        return "skipped"

    def _continue_daily_lingmai_clear_from_zaohua(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        yield from self._check_daily_lingmai_guiyuan_upgrade(
            runtime,
            payload,
            task_label=task_label,
        )
        yield from runtime.wait_click_then_view(285, "探索", 313)
        return (yield from self._continue_daily_lingmai_clear_from_explore(runtime, payload, task_label=task_label))

    def _check_daily_lingmai_guiyuan_upgrade(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        already_open: bool = False,
    ):
        """在 #285 检查一次归元凝神；资源不足时零升级并返回入口页。"""

        if not already_open:
            yield from runtime.wait_click_then_view(
                285,
                "归元凝神",
                589,
                timeout=float(payload.get("lingmai_guiyuan_open_timeout_seconds") or 15.0),
                label=f"{task_label}：打开 #589 归元凝神",
            )
        frame = runtime.cur_frame(update=True)
        resource_text = runtime.ocr_text_in_shapes(
            589,
            ("凝神资源",),
            frame_data_url=frame,
        )
        values = parse_ocr_values(
            resource_text,
            expected_count=2,
            allow_extra_numbers=False,
        )
        if values is None:
            self._log(
                "detail",
                f"{task_label}：#589 未可靠读取凝神资源「{resource_text}」，本次不升级",
            )
            yield from runtime.wait_click_then_view(589, "返回", 285)
            return "unknown"

        available, cost = values
        if cost <= 0:
            self._log(
                "detail",
                f"{task_label}：#589 凝神消耗无效 {available}/{cost}，本次不升级",
            )
            yield from runtime.wait_click_then_view(589, "返回", 285)
            return "unknown"
        if available < cost:
            self._log(
                "success",
                f"{task_label}：归元凝神资源 {available}/{cost}，当前不可升级，直接通过",
            )
            yield from runtime.wait_click_then_view(589, "返回", 285)
            return "not_upgradable"

        self._log(
            "action",
            f"{task_label}：归元凝神资源 {available}/{cost}，执行一次凝神升级",
        )
        yield from runtime.wait_click_then_view(
            589,
            "凝神",
            346,
            timeout=float(payload.get("lingmai_guiyuan_upgrade_timeout_seconds") or 15.0),
            label=f"{task_label}：等待归元凝神升级成功层",
        )
        yield from runtime.wait_click_then_view(
            346,
            "继续",
            589,
            timeout=float(payload.get("lingmai_guiyuan_continue_timeout_seconds") or 15.0),
            label=f"{task_label}：关闭归元凝神成功层",
        )
        after_frame = runtime.cur_frame(update=True)
        after_text = runtime.ocr_text_in_shapes(
            589,
            ("凝神资源",),
            frame_data_url=after_frame,
        )
        after_values = parse_ocr_values(
            after_text,
            expected_count=2,
            allow_extra_numbers=False,
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
        yield from runtime.wait_click_then_view(589, "返回", 285)
        return "upgraded"

    def _continue_daily_lingmai_clear_from_explore(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        """在 #313 勾选一键探索，再进入 #314 选择消耗体力。"""
        frame = runtime.cur_frame(update=True)
        stamina_text = runtime.ocr_text_in_shapes(313, ("体力",), frame_data_url=frame)
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
                yield from runtime.goto_view(34)
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-lingmai-clear",
                    label=task_label,
                )
        else:
            self._log("detail", f"{task_label}：未可靠解析 #313[体力]「{stamina_text}」，继续固定一轮探索")
        unchecked_score = runtime.shape_score(313, "一键探索", frame_data_url=frame)
        # #313「一键探索」参考图表示未勾选状态。真实调试中未勾选为 100，
        # 勾选后仍可能因大部分背景相同而达到 81；因此这里使用独立的严格
        # 状态阈值，而不能沿用普通 overlay_threshold=55。
        unchecked_threshold = float(payload.get("lingmai_one_click_unchecked_threshold") or 95.0)
        self._log(
            "detail",
            f"{task_label}：#313「一键探索」未勾选图像 score={unchecked_score:.0f}% threshold={unchecked_threshold:.0f}%",
        )
        if unchecked_score >= unchecked_threshold:
            yield from runtime.wait_click(313, "一键探索")
            yield from runtime.wait_action_settle(float(payload.get("lingmai_one_click_settle_seconds") or 1.0))
        yield from runtime.wait_click_then_view(313, "确定", 314)
        return (yield from self._continue_daily_lingmai_clear_from_amount(runtime, payload, task_label=task_label))

    @staticmethod
    def _parse_daily_lingmai_clear_stamina(text: str) -> tuple[int, int] | None:
        """Parse #313 stamina as (single-run cost, currently available)."""
        values = parse_ocr_values(text, expected_count=2, allow_extra_numbers=True)
        return (values[0], values[1]) if values is not None else None

    def _continue_daily_lingmai_clear_from_amount(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        """在 #314 通过已标注滚动条选择最大体力并确认。"""
        self._log("action", f"{task_label}：拖动 #314「滚动条」到最右端选择最大体力")
        runtime.drag_shape_to_frame_edge(
            314,
            "滚动条",
            direction="right",
            duration=float(payload.get("lingmai_amount_drag_seconds") or 0.6),
        )
        yield from runtime.wait_action_settle(float(payload.get("lingmai_amount_settle_seconds") or 1.0))
        landing = yield from runtime.wait_click_then_view(
            314,
            "确定",
            [315, 313, 285],
            timeout=float(payload.get("lingmai_finish_timeout_seconds") or 15.0),
            label=f"{task_label}：等待 #314 确定后的 #315/#313/#285",
        )
        landing_id = int(getattr(landing, "id", landing) or 0)
        if landing_id == 315:
            return (yield from self._continue_daily_lingmai_clear_from_transient(runtime, payload, task_label=task_label))
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
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ):
        """尽快消费有时效性的 #315；错过它时仍以回到 #285 为完成。"""
        self._log("action", f"{task_label}：检测到有时效性的 #315，尝试点击「继续」")
        try:
            landing = yield from runtime.wait_click_then_view(
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
            landing = yield from runtime.wait_view(
                313,
                285,
                timeout=float(payload.get("lingmai_finish_timeout_seconds") or 15.0),
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

    def daily_dongtian_clear_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        if bool(payload.get("ignore_schedule_window")):
            return None
        decision = self._daily_window_admission(
            now=_behavior_tree_runtime._now(),
            trigger=time_cls(21, 30),
            cutoff=time_cls(22, 0),
            label="洞天_行动力",
            window_text="21:30-22:00",
        )
        return self._persist_admission_decision(payload, decision)

    def _execute_daily_dongtian_clear_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """执行“洞天_行动力”的业务部分。

        完整工程作业由 ``run_task('daily_dongtian_clear')`` 组织成原子闭环：
        外层先用通用 ``goto_view(34)`` 归一到世界，本函数从 #34 进入日常和
        洞天并清理行动力；本函数返回 ``success`` 后，外层再用同一个通用
        ``goto_view(34)`` 按 ``#341 -> #279 -> #34`` 等真实落点动态收尾。
        因而这里不应复制一条洞天专用返回链。

        内层也允许从 #279 洞天主页或 #341 地点详情直接开始，供同一 Cell 内
        的连续流程和 AI 开发调试复用；这不代表工程 Scheduler 可以跨 Cell
        续接业务进度。新一轮正式作业仍必须从稳定起点 #34 整单执行。
        """
        payload = {"max_scrolls": 24, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少洞天_行动力资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(279), dict):
            raise RuntimeError("洞天_行动力：缺少 #279「洞天福地」标注，无法确认洞天主页")

        task_label = "洞天_行动力"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([341, 279, 69, 34, 47], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 341:
            return (yield from self._daily_dongtian_clear_action_power_loop(runtime, stop_event, payload))
        if scene_id == 279 or self._daily_dongtian_text_is_home(text):
            return (yield from self._continue_daily_dongtian_clear_from_home(runtime, stop_event, payload))

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, _score, frame = runtime.current_scene([279, 69, 34, 47], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                    return (yield from self._continue_daily_dongtian_clear_from_home(runtime, stop_event, payload))
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
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
            title_pattern=r"收取\s*两?万\s*九|九曜\s*玄墨",
            progress_can_mark_done=False,
        )
        if daily_status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-dongtian-clear",
                task_type="daily_dongtian_clear",
                label=task_label,
                entry_label="收取两万九曜玄墨",
                daily_start_time=time_cls(21, 30),
                daily_end_time=time_cls(22, 0),
            )
            return "skipped"
        yield from self._wait_daily_dongtian_home(ctx, stop_event, payload, task_label=task_label)
        return (yield from self._continue_daily_dongtian_clear_from_home(runtime, stop_event, payload))

    def _continue_daily_dongtian_clear_from_home(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        return (yield from self._daily_dongtian_clear_action_power_loop(runtime, stop_event, payload))

    def _daily_dongtian_runtime_snapshot(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        override = payload.get("__dongtian_runtime_snapshot_override")
        if isinstance(override, dict):
            snapshot = dict(override)
        else:
            from backend.core.fanxiu.instrumentation.dongtian import (
                read_dongtian_snapshot,
            )

            snapshot = read_dongtian_snapshot()
        payload["__dongtian_runtime_snapshot"] = snapshot
        evidence = snapshot.get("evidence") if isinstance(snapshot.get("evidence"), dict) else {}
        self._log(
            "detail",
            "洞天_行动力：Runtime 快照 "
            f"available={bool(snapshot.get('available'))}，"
            f"elapsed={float(snapshot.get('elapsed_seconds') or 0):.3f}s，"
            f"mines_root_cache_hit={evidence.get('mines_root_cache_hit')}，"
            f"club_root_cache_hit={evidence.get('club_root_cache_hit')}，"
            f"phase_timings={evidence.get('phase_timings_seconds')}"
        )
        return snapshot

    def _daily_dongtian_action_power(
        self,
        runtime: Any,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, str]:
        """Read authoritative action power from game memory only."""

        _ = runtime
        if not isinstance(payload, dict):
            raise RuntimeError("洞天_行动力：缺少 Runtime payload，拒绝降级 OCR")
        snapshot = self._daily_dongtian_runtime_snapshot(payload)
        runtime_action_power = snapshot.get("action_power")
        if (
            snapshot.get("available")
            and isinstance(runtime_action_power, int)
            and runtime_action_power >= 0
        ):
            return runtime_action_power, "runtime:XianLvMinesMgr.Model.Data.V_AttackFatigueValue"
        reason = str(snapshot.get("reason") or "V_AttackFatigueValue 缺失")
        raise RuntimeError(f"洞天_行动力：Runtime 行动力不可用，拒绝降级 OCR：{reason}")

    def _daily_dongtian_complete_from_runtime_if_proven(
        self,
        payload: dict[str, Any],
    ) -> str | None:
        """Short-circuit a retry when authoritative action power proves completion."""

        snapshot = self._daily_dongtian_runtime_snapshot(payload)
        action_power = snapshot.get("action_power")
        if not (
            snapshot.get("available")
            and isinstance(action_power, int)
            and action_power >= 0
        ):
            return None
        self._log(
            "detail",
            "洞天_行动力：启动前 Runtime "
            f"行动力={action_power}，来源='runtime:XianLvMinesMgr.Model.Data.V_AttackFatigueValue'",
        )
        if action_power >= 100:
            return None
        self._log("success", f"洞天_行动力：启动前已确认行动力低于 100（当前 {action_power}）")
        return self._complete_daily_clear_task(
            payload,
            task_id="legacy-daily-dongtian-clear",
            label="洞天_行动力",
        )

    def _daily_dongtian_clear_action_power_loop(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """持续执行“洞天挑战一次”，直到行动力不足 100。

        一轮挑战不以胜负作为完成条件：挑战失败通常消耗 100 点并回到 #341，
        挑战成功通常消耗 20 点并可能回到 #279。两种结果都达成“消耗行动力”
        的业务目标，因此每轮只重新识别真实落点和 HUD 行动力：

        - 落在 #341 且行动力仍不少于 100：直接挑战当前地点下一次；
        - 落在 #279 且行动力仍不少于 100：重新从最新 Runtime 快照选择敌对地点；
        - 任一场景识别到行动力小于 100：清理完成，返回 ``success``；
        - 其它落点、Runtime 字段缺失或超过安全轮数：失败并保留明确证据。

        ``max_action_power_rounds`` 只是防止识别异常导致无限循环的安全上限，
        不是业务次数；业务终止条件始终是行动力 ``< 100``。
        """
        rounds = 0
        max_rounds = max(1, int(payload.get("max_action_power_rounds") or 100))
        while rounds < max_rounds:
            scene_id, score, _frame = runtime.current_scene([341, 279], update=True)
            if scene_id not in {341, 279}:
                raise RuntimeError(f"洞天_行动力：循环只接受 #341/#279，当前 #{scene_id} {score:.0f}%")

            action_power, evidence = self._daily_dongtian_action_power(runtime, payload)
            self._log("detail", f"洞天_行动力：当前 #{scene_id} 行动力={action_power}，来源={evidence!r}")
            if action_power < 100:
                self._log("success", f"洞天_行动力：行动力已低于 100（当前 {action_power}），共挑战 {rounds} 次")
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-dongtian-clear",
                    label="洞天_行动力",
                )

            if scene_id == 279:
                enemy_places = [str(item).strip() for item in payload.get("enemy_places") or [] if str(item).strip()]
                if not enemy_places:
                    enemy_places = self._daily_dongtian_enemy_places_from_runtime(payload)
                if not enemy_places:
                    raise RuntimeError("洞天_行动力：Runtime 未解析出敌对地点")
                clicked_place = yield from self._daily_dongtian_click_first_enemy_place(
                    runtime,
                    stop_event,
                    enemy_places,
                    max_scrolls=max(0, int(payload.get("place_max_scrolls") or 12)),
                )
                yield from self._daily_dongtian_validate_enemy_detail(runtime, clicked_place, payload)
                if bool(payload.get("pause_after_enemy_place_click")):
                    self._log("success", f"洞天_行动力：已点击敌对地点「{clicked_place}」，按调试参数暂停")
                    return "manual_check_pending"

            yield from self._daily_dongtian_continue_enemy_occupation(runtime)
            rounds += 1
            # 一轮战斗无论胜负都会消耗行动力；进入战斗前只剩 100 时，
            # 正常走到 #346「继续」就已经能确定剩余行动力低于 100。
            # 此时 HUD 的单个“0”偶尔会被 OCR 识别为空，不能因此把已经
            # 完成的业务闭环误判为失败并重新跑整项作业。
            if action_power == 100:
                self._log("success", f"洞天_行动力：最后 100 行动力已完成战斗，共挑战 {rounds} 次")
                return self._complete_daily_clear_task(
                    payload,
                    task_id="legacy-daily-dongtian-clear",
                    label="洞天_行动力",
                )

        raise RuntimeError(f"洞天_行动力：挑战达到安全上限 {max_rounds} 次，行动力仍未低于 100")

    def _daily_dongtian_enemy_places_from_runtime(
        self,
        payload: dict[str, Any],
    ) -> list[str]:
        snapshot = payload.get("__dongtian_runtime_snapshot")
        if not isinstance(snapshot, dict):
            snapshot = self._daily_dongtian_runtime_snapshot(payload)
        mines = snapshot.get("mines")
        own_union_id = int(snapshot.get("own_union_id") or 0)
        own_union_name = str(snapshot.get("own_union_name") or "").strip()
        if (
            not snapshot.get("available")
            or not snapshot.get("complete")
            or not isinstance(mines, list)
            or not mines
            or (own_union_id <= 0 and not own_union_name)
        ):
            reason = str(snapshot.get("reason") or "Runtime 洞天字段不完整")
            raise RuntimeError(f"洞天_行动力：Runtime 快照不可用，等待模型修复：{reason}")

        place_by_id = {index + 1: name for index, name in enumerate(_DONGTIAN_PLACE_ANCHORS)}
        enemies: list[str] = []
        union_summary: list[tuple[int, str, str]] = []
        for mine in mines:
            if not isinstance(mine, dict):
                continue
            mine_id = int(mine.get("id") or 0)
            union_id = int(mine.get("cross_union_id") or 0)
            union_name = str(mine.get("cross_union_name") or "").strip()
            place = place_by_id.get(mine_id, "")
            union_summary.append((mine_id, place, union_name))
            if not place or (union_id <= 0 and not union_name):
                continue
            if own_union_id > 0 and union_id == own_union_id:
                continue
            if own_union_name and union_name == own_union_name:
                continue
            enemies.append(place)
        self._log(
            "detail",
            f"洞天_行动力：Runtime 解析敌对地点 {enemies}，"
            f"已解码 {len(mines)} 个，unions={union_summary}",
        )
        return enemies

    def _daily_dongtian_validate_enemy_detail(
        self,
        runtime: Any,
        clicked_place: str,
        payload: dict[str, Any],
    ):
        yield from runtime.wait_view(341, label=f"洞天_行动力：核对地点「{clicked_place}」详情")
        expected_place = self._daily_dongtian_normalize_place_name(clicked_place)
        if expected_place not in {
            self._daily_dongtian_normalize_place_name(item)
            for item in _DONGTIAN_PLACE_ANCHORS
        }:
            raise RuntimeError(f"洞天_行动力：Runtime 授权了未知地点 {clicked_place!r}")
        title_shape = runtime.shape(341, "地点名称")
        if title_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #341「地点名称」区域，无法核对点击落点")

        frame = runtime.cur_frame(update=True)
        title_fragments = runtime.ocr_fragments_in_shapes(
            341,
            ["地点名称"],
            frame_data_url=frame,
        )
        observed_titles = [
            self._daily_dongtian_normalize_place_name(fragment.get("text"))
            for fragment in title_fragments
            if self._daily_dongtian_normalize_place_name(fragment.get("text"))
        ]
        ranked = rank_ocr_name_matches(expected_place, observed_titles)
        best = ranked[0] if ranked else None
        if best is not None and best.passed_threshold:
            self._log(
                "detail",
                f"洞天_行动力：#341 顶部地点标题已对齐"
                f"「{expected_place}」~「{best.observed}」({best.similarity:.2f})",
            )
            # Return the Runtime-authorized canonical name.  OCR is evidence
            # for the landing, not a second business identity; callers must
            # not fail again merely because the accepted glyph rendering or
            # whitespace differs from the canonical label.
            return expected_place

        observed = "、".join(observed_titles) or "<空>"
        self._log(
            "warning",
            f"洞天_行动力：点击后安全核验失败（顶部地点标题不一致），立即返回 #279；"
            f"expected={expected_place!r}, observed={observed!r}",
        )
        yield from runtime.wait_click_then_view(341, "返回", 279)
        raise RuntimeError("洞天_行动力：敌方地点安全核验失败（顶部地点标题不一致），已返回洞天主页")

    def _daily_dongtian_continue_enemy_occupation(self, runtime: Any):
        yield from runtime.wait_click_then_view(341, "位置1", 342)
        yield from runtime.wait_click_then_view(342, "占领", 343)
        yield from runtime.wait_click(343, "占领")
        yield from runtime.wait_action_settle(0.3)
        scene_id, _score, frame = runtime.current_scene([344, 343], update=True)
        if scene_id == 343:
            # The transition can begin a few frames after the click.  One
            # bounded second sample distinguishes a delayed transition from
            # an inert button without requiring the transient battle frame to
            # have a stable scene identity.
            yield from runtime.wait_action_settle(0.8)
            scene_id, _score, frame = runtime.current_scene([344, 343], update=True)
        if scene_id == 344:
            runtime.click_shape(344, "战斗", frame_data_url=frame)
            runtime.clear_frame()
        elif scene_id == 343:
            raise RuntimeError("洞天_行动力：点击 #343「占领」后仍停在队伍确认页")
        # Any other reliable observation means #343 has been left and the
        # direct battle transition is already in flight.  The battle finisher
        # owns the next stable business anchors.
        yield from self._daily_dongtian_finish_battle(runtime)

    def _daily_dongtian_finish_battle(
        self,
        runtime: Any,
        *,
        tick_seconds: float = 1.0,
        max_ticks: int = 180,
    ):
        """等待洞天战斗结束，并消费可选的跳过页和最终继续页。

        #345 是可能不出现的中间态；#346 才是战斗结束的必要终态。循环按
        固定 tick 观察真实画面，避免把“没有出现跳过”误判为流程失败。
        """
        skip_clicked = False
        for _tick in range(max(1, int(max_ticks))):
            yield from runtime.wait_action_settle(max(0.1, float(tick_seconds)))
            scene_id, _score, _frame = runtime.current_scene([345, 346], update=True)
            if scene_id == 345:
                if not skip_clicked:
                    yield from runtime.wait_click(345, "跳过")
                    skip_clicked = True
                continue
            if scene_id == 346:
                yield from runtime.wait_click(346, "继续")
                # 继续后通常回到当前地点详情 #341，也可能直接回洞天主页 #279；
                # 两者都表示本轮战斗闭环完成，不把其中一个误设成唯一成功终点。
                yield from runtime.wait_view(341, 279, label="洞天_行动力：确认战斗后的正常落点")
                return
        raise RuntimeError("洞天_行动力：战斗等待超时，未出现最终场景 #346「继续」")

    def _daily_dongtian_click_first_enemy_place(
        self,
        runtime: Any,
        stop_event: threading.Event,
        enemy_places: list[str],
        *,
        max_scrolls: int,
    ):
        return (yield from self._daily_dongtian_click_place(
            runtime,
            stop_event,
            enemy_places,
            max_scrolls=max_scrolls,
            scroll_directions=("down", "up"),
            task_label="洞天_行动力",
        ))

    def _daily_dongtian_click_place(
        self,
        runtime: Any,
        stop_event: threading.Event,
        place_names: list[str],
        *,
        max_scrolls: int,
        scroll_directions: tuple[str, ...] = ("down", "up"),
        task_label: str = "洞天_地点定位",
    ):
        """Locate one caller-selected #279 place from the scroll window.

        A search may start at any prior scroll offset.  It therefore walks to
        one boundary and, if necessary, reverses toward the other boundary.
        This low-level locator grants no seating authority.  OCR is used only
        for an exact normalized place identity; #341 remains the independent
        post-click assertion performed by the caller.
        """
        view279 = runtime.view(279)
        window_shape = runtime.shape(279, "窗口")
        if window_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「窗口」标注，无法查找敌对地点")
        window_box = window_shape.box()

        roster_shape = runtime.shape(279, "我的编队")
        if roster_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「我的编队」禁点区标注，拒绝点击地点")
        roster_box = roster_shape.box()

        def point_in_box(x: float, y: float, box: dict[str, Any]) -> bool:
            left = float(box.get("x") or 0)
            top = float(box.get("y") or 0)
            return left <= x <= left + float(box.get("w") or 0) and top <= y <= top + float(box.get("h") or 0)

        normalized_targets = {
            self._daily_dongtian_normalize_place_name(item): item
            for item in place_names
            if self._daily_dongtian_normalize_place_name(item)
        }
        known_places = {
            self._daily_dongtian_normalize_place_name(item)
            for item in _DONGTIAN_PLACE_ANCHORS
        }
        unknown = sorted(set(normalized_targets) - known_places)
        if unknown:
            raise RuntimeError(f"{task_label}：调用方传入未知地点 {unknown}")

        directions = tuple(dict.fromkeys(scroll_directions))
        if not directions or any(item not in {"up", "down"} for item in directions):
            raise ValueError(f"洞天地点滚动方向非法：{scroll_directions!r}")

        for direction_index, direction in enumerate(directions):
            for scroll_index in range(max_scrolls + 1):
                self._raise_if_stopped(stop_event)
                yield from runtime.wait_view(279, label=f"{task_label}：等待 #279 洞天福地")
                frame = runtime.cur_frame(update=True)
                lines = runtime.ocr_fragments_in_shapes(279, ["窗口"], frame_data_url=frame)
                tokens = (
                    runtime.ocr_tokens_in_shapes(279, ["窗口"], frame_data_url=frame)
                    if hasattr(runtime, "ocr_tokens_in_shapes")
                    else []
                )
                matches: list[tuple[float, float, str, dict[str, Any], float, float]] = []
                for line in lines:
                    for normalized, original in normalized_targets.items():
                        location_box = self._daily_dongtian_location_box(line, tokens, normalized)
                        if location_box is None:
                            continue

                        location_center_x = float(location_box.get("x") or 0) + float(location_box.get("w") or 0) * 0.5
                        location_center_y = float(location_box.get("y") or 0) + float(location_box.get("h") or 0) * 0.5
                        click_point = self._daily_dongtian_location_click_point(location_box, window_box)
                        if click_point is None:
                            # A title can be only partly visible at the top of the
                            # scroll viewport.  Its historical "100 px above"
                            # card hot spot then falls into the fixed header (the
                            # real 2026-08-24 failure opened #340 rules).  Keep
                            # scrolling until the same exact title has a hot spot
                            # inside the authoritative list viewport.
                            continue
                        click_x, click_y = click_point
                        if point_in_box(location_center_x, location_center_y, roster_box) or point_in_box(click_x, click_y, roster_box):
                            continue
                        matches.append((float(line.get("y") or 0), float(line.get("x") or 0), original, line, click_x, click_y))
                        break
                if matches:
                    _y, _x, place, line, click_x, click_y = min(matches, key=lambda item: (item[0], item[1]))
                    normalized_clicked = self._daily_dongtian_normalize_place_name(place)
                    if normalized_clicked not in normalized_targets or normalized_targets[normalized_clicked] != place:
                        raise RuntimeError(f"{task_label}：地点名称内部一致性校验失败：place={place!r}")
                    if click_x <= 0 or click_y <= 0:
                        raise RuntimeError(f"{task_label}：地点「{place}」 OCR 坐标无效，line={line}")
                    self._log(
                        "click",
                        f"{task_label}：调用方目标地点「{place}」，"
                        f"点击同一 OCR 地点上方热区=({click_x:.0f},{click_y:.0f})",
                    )
                    runtime.click_frame_point(279, click_x, click_y)
                    yield from runtime.wait_action_settle(float(runtime.payload.get("place_click_settle_seconds") or 2.0))
                    return place
                if scroll_index >= max_scrolls:
                    break
                direction_text = "向下" if direction == "down" else "向上"
                self._log("action", f"{task_label}：当前窗口未找到地点，{direction_text}滚动 {scroll_index + 1}/{max_scrolls}")
                changed = yield from runtime.scroll_shape_content(view279, window_shape, direction=direction)
                if not changed:
                    break
            if direction_index + 1 < len(directions):
                self._log("detail", f"{task_label}：已到{direction}方向边界，反向继续查找")
        raise RuntimeError(f"{task_label}：#279 窗口未找到地点，candidates={place_names}")

    def _daily_dongtian_click_seating_target(
        self,
        runtime: Any,
        stop_event: threading.Event,
        authorization: Mapping[str, Any],
        *,
        max_scrolls: int,
        probe_reader: Callable[..., Mapping[str, Any]] | None = None,
    ):
        """Click one friendly seating place only after a fresh Runtime gate."""

        from backend.core.fanxiu.data_annotation.dongtian_seating_click import (
            validate_dongtian_seating_place_authorization,
        )
        from backend.core.fanxiu.instrumentation.dongtian import (
            read_dongtian_seating_probe,
        )

        if not isinstance(authorization, Mapping):
            raise RuntimeError("洞天_座位研究：裸地点名不能作为 Runtime 上座授权")
        excluded_mine_ids = {
            int(item)
            for item in authorization.get("excluded_mine_ids") or []
            if not isinstance(item, bool) and str(item).isdigit() and int(item) > 0
        }
        reader = probe_reader or read_dongtian_seating_probe
        fresh_probe = reader(excluded_mine_ids=excluded_mine_ids)
        gate = validate_dongtian_seating_place_authorization(
            authorization,
            fresh_probe,
        )
        if not gate.get("ok"):
            raise RuntimeError(
                "洞天_座位研究：点击前 Runtime 授权失败，"
                f"reason={gate.get('reason') or 'unknown'}"
            )
        self._log(
            "detail",
            "洞天_座位研究：fresh Runtime 已授权友军地点"
            f" mine_id={gate['mine_id']} place={gate['place_name']!r}",
        )
        return (yield from self._daily_dongtian_click_place(
            runtime,
            stop_event,
            [str(gate["place_name"])],
            max_scrolls=max_scrolls,
            scroll_directions=("down", "up"),
            task_label="洞天_座位研究",
        ))

    @staticmethod
    def _daily_dongtian_normalize_place_name(value: Any) -> str:
        text = _sanitize_ocr_text(value)
        text = re.sub(r"^\[(?:洞天|福地)\]", "", text).strip()
        return re.sub(r"\s+", "", text)

    @staticmethod
    def _daily_dongtian_location_click_point(
        location_box: Mapping[str, Any],
        window_box: Mapping[str, Any],
    ) -> tuple[float, float] | None:
        """Return the native card hot spot only when it remains in #279's list."""

        center_x = float(location_box.get("x") or 0) + float(location_box.get("w") or 0) * 0.5
        center_y = float(location_box.get("y") or 0) + float(location_box.get("h") or 0) * 0.5
        click_x = center_x + 1.5
        click_y = center_y - 100.0
        left = float(window_box.get("x") or 0)
        top = float(window_box.get("y") or 0)
        right = left + float(window_box.get("w") or 0)
        bottom = top + float(window_box.get("h") or 0)
        if not (left <= click_x <= right and top <= click_y <= bottom):
            return None
        return click_x, click_y

    def _daily_dongtian_location_box(
        self,
        line: dict[str, Any],
        tokens: list[dict[str, Any]],
        target: str,
    ) -> dict[str, float] | None:
        """Resolve one place inside one authoritative Paddle line.

        The line decides object identity. Linked word boxes only refine a
        substring within that same line; tokens from neighboring UI objects are
        never concatenated or searched together.
        """

        compact_location = self._daily_dongtian_normalize_place_name(line.get("text"))
        compact_target = self._daily_dongtian_normalize_place_name(target)
        if not compact_location or not compact_target:
            return None

        if compact_location != compact_target:
            return None
        matched_text = compact_target

        line_id = line.get("line_id")
        line_tokens = [token for token in tokens if line_id is not None and token.get("parent_line_id") == line_id]
        token_box = locate_text_box(line_tokens, matched_text)
        if token_box is not None:
            return token_box

        # Without linked tokens only an exact/partial standalone native line is
        # safe. A line containing suffix/prefix text cannot be proportionally
        # sliced because that would recreate the discarded legacy heuristic.
        if compact_location == matched_text:
            return {
                "x": float(line.get("x") or 0),
                "y": float(line.get("y") or 0),
                "w": float(line.get("w") or 0),
                "h": float(line.get("h") or 0),
            }
        return None

    def _enter_daily_lingmai_zaohua_from_world_or_daily(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: FanxiuRuntimeSession,
        scene_id: int | None,
        frame: str | None,
        text: str,
        *,
        task_label: str,
    ) -> tuple[int | None, float, str | None]:
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, score, frame = runtime.current_scene([285, 69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 285:
                    self._log("success", f"{task_label}：已到达 #285 造化灵脉，当前 #{scene_id} {score:.0f}%，OCR={text[:160]}")
                    return scene_id, score, frame
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(ctx, runtime, stop_event, frame, scene_id, text, label=task_label)

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
        yield from self._wait_daily_lingmai_zaohua_after_entry(runtime, payload, task_label=task_label)
        scene_after, score_after, frame_after = runtime.current_scene([285], update=True)
        text_after = runtime.ocr_text(frame_after)
        self._log("success", f"{task_label}：已到达 #285 造化灵脉，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return scene_after, score_after, frame_after

    def _wait_daily_lingmai_zaohua_after_entry(
        self,
        runtime: FanxiuRuntimeSession,
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
                scene_id = yield from runtime.wait_view(
                    285,
                    312,
                    timeout=max(1.0, min(8.0, remaining)) if not retried_entry else max(1.0, remaining),
                    label=f"{task_label}：点击 #69 入口后等待 #285 造化灵脉",
                )
            except TimeoutError:
                scene_after, _score_after, _frame_after = runtime.current_scene([285, 312, 69], update=True)
                if scene_after in {285, 312}:
                    scene_id = runtime.view(scene_after)
                elif scene_after == 69 and not retried_entry and time.monotonic() < deadline:
                    retried_entry = True
                    self._log(
                        "action",
                        f"{task_label}：入口点击后的瞬态弹层已清理但仍在 #69，原等待预算内重开入口一次",
                    )
                    reopen_status = yield from runtime.open_daily_entry(
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
            yield from runtime.wait_click_then_view(312, "确认", [285, 312], timeout=8.0)
            scene_after, _score_after, _frame_after = runtime.current_scene([285, 312], update=True)
            if scene_after == 285:
                return runtime.view(285)
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
        now = _behavior_tree_runtime._now()
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
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        yield from runtime.wait_click_then_view(
            305,
            "确定",
            wait_leave=True,
            timeout=float(payload.get("lingmai_gather_confirm_timeout") or 20.0),
        )
        yield from runtime.wait_action_settle(float(payload.get("lingmai_gather_confirm_settle_seconds") or 3.0))
        scene_after, score_after, frame_after = runtime.current_scene([318, 306, 285, 288, 289, 305, 186, 34], update=True)
        text_after = runtime.ocr_text(frame_after)
        if scene_after == 305:
            raise RuntimeError(f"{task_label}：点击 #305「确定」后仍停留在灵脉聚灵确认弹窗，OCR={text_after[:160]}")
        if scene_after == 318:
            return (yield from self._confirm_daily_lingmai_reward(runtime, payload, task_label=task_label))
        if scene_after == 306:
            return (yield from self._finish_daily_lingmai_to_world(runtime, payload, task_label=task_label, scene_id=scene_after, frame=frame_after))
        self._log("success", f"{task_label}：已确认聚灵，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return (yield from self._finish_daily_lingmai_to_world(runtime, payload, task_label=task_label))

    def _confirm_daily_lingmai_reward(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        yield from runtime.wait_click_then_view(
            318,
            "确认",
            wait_leave=True,
            timeout=float(payload.get("lingmai_reward_confirm_timeout") or 20.0),
        )
        yield from runtime.wait_action_settle(float(payload.get("lingmai_reward_confirm_settle_seconds") or 2.0))
        scene_after, score_after, frame_after = runtime.current_scene([306, 303, 285, 186, 34, 318, 59], update=True)
        text_after = runtime.ocr_text(frame_after)
        if scene_after == 318:
            raise RuntimeError(f"{task_label}：点击 #318「确认」后仍停留在灵脉奖励确认，OCR={text_after[:160]}")
        self._log("success", f"{task_label}：已关闭 #318 灵脉奖励确认，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，OCR={text_after[:160]}")
        return (yield from self._finish_daily_lingmai_to_world(runtime, payload, task_label=task_label, scene_id=scene_after, frame=frame_after))

    def _finish_daily_lingmai_to_world(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int | None = None,
        frame: str | None = None,
    ) -> str:
        if scene_id is None:
            scene_id, _score, frame = runtime.current_scene([34, 306, 318, 285, 286, 287, 288, 289, 305, 186, 59, 588], update=True)
        text = runtime.ocr_text(frame) if isinstance(frame, str) and frame else runtime.ocr_text(update=True)
        daily_remaining_seconds = self._parse_daily_lingmai_remaining_seconds(text)
        if scene_id == 588:
            # #588 is the stable Lingmai room page observed after a successful
            # kick battle.  It is a real terminal state, not the visually
            # similar #340 offering page.  Leave through its own annotated
            # action before handing the result to the common Runtime verifier.
            landed = yield from runtime.wait_click_then_view(
                588,
                "离开",
                [289, 186, 85, 34, 285],
                timeout=float(payload.get("lingmai_occupied_leave_timeout") or 30.0),
            )
            landed_id = int(landed.id if isinstance(landed, View) else landed)
            if landed_id == 289:
                yield from runtime.wait_click_then_view(
                    289,
                    "确认",
                    [186, 85, 34, 285],
                    timeout=float(payload.get("lingmai_occupied_leave_confirm_timeout") or 30.0),
                )
            scene_id, _score, frame = runtime.current_scene([186, 85, 34, 285], update=True)
        if scene_id == 306:
            yield from self._confirm_daily_lingmai_summary_popup(runtime, payload, task_label=task_label, scene_id=scene_id, frame=frame)
            landed = yield from runtime.wait_scene(
                312,
                285,
                85,
                186,
                34,
                timeout=float(payload.get("lingmai_summary_landing_timeout") or 30.0),
                label=f"{task_label}：#306 确认后等待可选 #312 或灵脉内部/世界",
            )
            scene_id = int(landed.id if isinstance(landed, View) else landed)
            if scene_id == 312:
                yield from runtime.wait_click_then_view(
                    312,
                    "确认",
                    [285, 85, 186, 34],
                    timeout=float(payload.get("lingmai_summary_312_timeout") or 20.0),
                )
                scene_id, _score, frame = runtime.current_scene([285, 85, 186, 34], update=True)
        if scene_id == 186:
            yield from self._leave_shared_scene_186_to_world(runtime, label=task_label)
            scene_id = 34
        if (
            scene_id is not None
            and scene_id != 34
            and isinstance(getattr(runtime, "ctx", None), dict)
        ):
            # Preserve the just-resolved business Layer 0 result for the first
            # generic goto step.  Without this hand-off, goto immediately runs
            # an unrelated global recognition pass and can replace a valid
            # #186 exit scene with another business frame before clicking.
            runtime.ctx["_go_scene_known_scene_id"] = int(scene_id)
        if daily_remaining_seconds == 0:
            if scene_id != 34:
                yield from runtime.goto_view(34)
            self._log("success", f"{task_label}：今日聚灵剩余已为 00:00:00，结算后回到 #34，不再尝试占座或驱离")
            return "success"
        if scene_id != 34:
            self._log("action", f"{task_label}：本轮操作后按场景图回到 #34 世界")
            yield from runtime.goto_view(34)
        self._log("success", f"{task_label}：本轮操作后已回到 #34 世界")

        runtime_override = payload.get(
            "__lingmai_post_action_runtime_snapshot_override"
        )
        runtime_status = (
            dict(runtime_override)
            if isinstance(runtime_override, Mapping)
            else refresh_lingmai_daily_status()
        )
        if (
            runtime_status.get("available")
            and runtime_status.get("complete")
        ):
            try:
                remaining_ms = int(
                    runtime_status.get("remaining_milliseconds")
                )
            except (TypeError, ValueError):
                remaining_ms = None
            if runtime_status.get("completed") is True or remaining_ms == 0:
                self._log(
                    "success",
                    f"{task_label}：战后 Runtime 确认 leftListenTime=0，"
                    "今日聚灵真正完成",
                )
                return "success"
            if remaining_ms is not None and remaining_ms > 0:
                self_seat = (
                    runtime_status.get("self_seat_facts")
                    if isinstance(
                        runtime_status.get("self_seat_facts"),
                        Mapping,
                    )
                    else {}
                )
                try:
                    expected_room_id = int(payload.get("__lingmai_expected_room_id") or 0)
                    actual_room_id = int(
                        self_seat.get("room_id")
                        or runtime_status.get("own_room_id")
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
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int | None = None,
        frame: str | None = None,
    ) -> str:
        frame = frame if isinstance(frame, str) and frame else runtime.cur_frame(update=True)
        if scene_id is None:
            scene_id, _score, _frame = runtime.current_scene([306], frame_data_url=frame)
        if scene_id != 306:
            raise RuntimeError(f"{task_label}：未识别到正式灵脉收益确认 scene，拒绝按 OCR 猜按钮")
        view = runtime.view(scene_id)
        action = view.get_shape("确认") or view.get_shape("确定")
        if action is None:
            raise RuntimeError(f"{task_label}：#{scene_id} 缺少「确认/确定」动作 shape")
        self._log("action", f"{task_label}：点击 #{scene_id}「{action.title}」")
        runtime.click_shape_center(view, action)
        yield from runtime.wait_action_settle(float(payload.get("lingmai_summary_confirm_settle_seconds") or 2.0))
        return "success"

    def _confirm_daily_lingmai_switch_popup(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        task_label: str,
        scene_id: int = 443,
        frame: str | None = None,
    ) -> str:
        """Confirm #443 and wait through dialogue until take-seat success."""

        yield from self._click_daily_lingmai_switch_confirm(
            runtime,
            payload,
            task_label=task_label,
            scene_id=scene_id,
            frame=frame,
        )

        landed = yield from runtime.wait_scene(
            318,
            303,
            289,
            305,
            306,
            443,
            47,
            timeout=float(payload.get("lingmai_switch_landing_timeout") or 20.0),
            label=f"{task_label}：确认更换灵脉后等待对话或入座成功页",
        )
        landed_id = int(landed.id if isinstance(landed, View) else landed)
        if landed_id in {443, 47}:
            raise RuntimeError(
                f"{task_label}：点击灵脉更换「确定」后仍停留在 #{landed_id}，"
                f"OCR={runtime.ocr_text(update=True)[:160]}"
            )
        if landed_id in {318, 303}:
            landed_id = yield from self._advance_daily_lingmai_kick_dialogue(
                runtime,
                terminal_scene_ids=(289, 305, 306),
                timeout=float(payload.get("lingmai_switch_dialogue_timeout") or 60.0),
                label=f"{task_label}：推进更换灵脉对话直到入座成功页",
            )
        if landed_id == 289:
            yield from runtime.wait_click_then_view(
                289,
                "确认",
                [186, 85, 34, 285],
                timeout=float(payload.get("lingmai_switch_success_timeout") or 30.0),
            )
            scene_after, _score_after, frame_after = runtime.current_scene([186, 85, 34, 285], update=True)
            return (yield from self._finish_daily_lingmai_to_world(
                runtime,
                payload,
                task_label=task_label,
                scene_id=scene_after,
                frame=frame_after,
            ))
        if landed_id == 305:
            return (yield from self._confirm_daily_lingmai_gather(runtime, payload, task_label=task_label))
        if landed_id == 306:
            scene_after, _score_after, frame_after = runtime.current_scene([306], update=True)
            return (yield from self._finish_daily_lingmai_to_world(
                runtime,
                payload,
                task_label=task_label,
                scene_id=scene_after,
                frame=frame_after,
            ))
        raise RuntimeError(
            f"{task_label}：确认更换灵脉后未到达 #289/#305/#306；当前 #{landed_id}"
        )

    def _click_daily_lingmai_switch_confirm(
        self,
        runtime: Any,
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
        frame = frame if isinstance(frame, str) and frame else runtime.cur_frame(update=True)
        text = runtime.ocr_text_in_shapes(
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
            runtime.ctx.get("images", {}).get(scene_id)
            if isinstance(getattr(runtime, "ctx", None), dict)
            else None
        )
        if isinstance(image, dict) and self._find_shape(image, "确定") is not None:
            runtime.click_shape_center(scene_id, "确定")
        else:
            raise RuntimeError(f"{task_label}：#{scene_id} 缺少「确定」动作 shape，拒绝按 OCR 猜按钮")
        yield from runtime.wait_action_settle(float(payload.get("lingmai_switch_confirm_settle_seconds") or 2.0))

    def _enter_daily_lingmai_level(
        self,
        runtime: Any,
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
        match = yield from runtime.wait_click_ocr_text(
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
        return (yield from runtime.wait_view(
            286,
            timeout=float(payload.get("lingmai_select_slot_timeout") or 12.0),
            label=f"{task_label}：点击「{target}」后等待 #286 座位页",
        ))

    def _continue_daily_lingmai_from_zaohua(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        frame: str | None,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image285 = images.get(285)
        if not isinstance(image285, dict):
            raise RuntimeError(f"{task_label}：缺少 #285「造化灵脉」标注，无法进入神脉")
        self._raise_if_stopped(stop_event)
        frame = frame if isinstance(frame, str) and frame else runtime.cur_frame(update=True)
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
            yield from runtime.goto_view(34)
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
            yield from runtime.goto_view(34)
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
            runtime.shape_score(285, "聚灵中", frame_data_url=frame)
            if gathering_shape is not None
            else 0.0
        )
        gathering_ocr_hit = False
        if gathering_score < gathering_threshold:
            gathering_ocr_hit = "聚灵中" in _sanitize_ocr_text(runtime.ocr_text(frame))
        self._log(
            "detail",
            f"{task_label}：#285 幂等校验「聚灵中」score={gathering_score:.0f}% "
            f"threshold={gathering_threshold:.0f}%，full_ocr={'命中' if gathering_ocr_hit else '未命中'}",
        )
        if (
            gathering_score >= gathering_threshold or gathering_ocr_hit
        ) and level_plan.get("action") != "try_shengmai":
            self._log("success", f"{task_label}：#285 已在聚灵中，不再重复抢座，退出后定时复查")
            yield from runtime.goto_view(34)
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
                runtime,
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
            runtime,
            payload,
            level_name=target_level_name,
            search_direction=search_direction,
            frame=frame,
            task_label=task_label,
        )
        scene_next, score_next, frame_next = runtime.current_scene([286], update=True)
        text_next = runtime.ocr_text(frame_next)
        self._log("success", f"{task_label}：已到达 #286「{target_level_name}」座位页，当前 #{scene_next if scene_next is not None else 'unknown'} {score_next:.0f}%，OCR={text_next[:160]}")
        return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, runtime, frame_next, task_label=task_label))

    def _fallback_daily_lingmai_to_shenmai(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: Any,
        *,
        task_label: str,
    ):
        """Leave a failed Shengmai attempt and enter Shenmai as the fallback."""

        self._raise_if_stopped(stop_event)
        self._log("action", f"{task_label}：圣脉暂无合法座位，返回 #285 并进入仙煌神脉保底")
        yield from runtime.wait_click_then_view(
            286,
            "返回",
            285,
            timeout=float(payload.get("lingmai_select_return_timeout") or 15.0),
        )
        ctx["_daily_lingmai_target_room_id"] = LINGMAI_UNION_SHENMAI_ROOM_ID
        ctx["_daily_lingmai_target_level_name"] = "仙煌神脉"
        yield from self._enter_daily_lingmai_level(
            runtime,
            payload,
            level_name="仙煌神脉",
            task_label=task_label,
        )
        # The seat list may retain Shengmai's scroll offset across this tier
        # switch, so the fallback search must explicitly start from the top.
        payload["lingmai_kick_reset_to_top"] = True
        _scene, _score, frame = runtime.current_scene([286], update=True)
        return (yield from self._continue_daily_lingmai_from_select_slot(
            ctx,
            stop_event,
            payload,
            runtime,
            frame,
            task_label=task_label,
        ))

    def _continue_daily_lingmai_from_select_slot(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: BehaviorTreeRuntime,
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
            frame = runtime.cur_frame(update=True)
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
            yield from runtime.goto_view(34)
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
            runtime.shape_score(286, "选择空位", frame_data_url=frame)
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
                yield from runtime.goto_view(34)
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
                runtime,
                task_label=task_label,
            ))
        if action == "already_seated":
            self_seat = selection.get("self_seat") if isinstance(selection.get("self_seat"), dict) else {}
            self._log(
                "success",
                f"{task_label}：只读状态确认自己已在「{target_level_name}」座位 {self_seat.get('seat_id')}，"
                "不再占空位或驱离玩家",
            )
            yield from runtime.goto_view(34)
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
                    runtime,
                    task_label=task_label,
                ))
            yield from runtime.goto_view(34)
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
            visible_roster_text = runtime.ocr_text(update=True)
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
                    runtime,
                    target_player=target,
                    task_label=task_label,
                ))
            except _DailyLingmaiKickTargetLost as exc:
                yield from runtime.goto_view(34)
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
            fresh_empty_frame = yield from runtime.wait_shape(
                286,
                "选择空位",
                timeout=float(payload.get("lingmai_select_empty_slot_timeout") or 10.0),
                threshold=threshold,
                label=f"{task_label}：等待 #286 空位行完成渲染",
            )
            score = runtime.shape_score(
                286,
                "选择空位",
                frame_data_url=fresh_empty_frame,
            )
        except TimeoutError:
            score = 0.0
        self._log("detail", f"{task_label}：新鲜帧复核 #286「选择空位」score={score:.0f}% threshold={threshold:.0f}%")
        if score < threshold:
            self._log("warning", f"{task_label}：#286「选择空位」新鲜帧匹配不足 {score:.0f}%，点击返回")
            yield from runtime.wait_click(286, "返回")
            yield from runtime.wait_action_settle(float(payload.get("lingmai_select_return_settle_seconds") or 2.0))
            raise RuntimeError(f"{task_label}：#286「选择空位」新鲜帧校验失败 {score:.0f}%<{threshold:.0f}%，已返回")

        self._log("success", f"{task_label}：#286「选择空位」新鲜帧校验通过 {score:.0f}%，点击「占领」")
        yield from runtime.wait_click(286, "占领")
        yield from runtime.wait_action_settle(float(payload.get("lingmai_occupy_click_settle_seconds") or 2.0))
        scene_next, score_next, frame_next = runtime.current_scene([287, 285, 286, 47], update=True)
        text_next = runtime.ocr_text(frame_next)
        text_compact = _sanitize_ocr_text(text_next)
        if re.search(r"聚灵体力符持有数量[:：]?0(?:\D|$)", text_compact):
            return self._record_daily_lingmai_resource_insufficient(
                payload,
                message="聚灵体力符持有数量为 0",
            )
        if scene_next == 287:
            self._log("success", f"{task_label}：已到达 #287 前往灵脉确认弹窗，当前 #{scene_next if scene_next is not None else 'unknown'} {score_next:.0f}%，点击「确认」")
            yield from runtime.wait_click(287, "确认")
            yield from runtime.wait_action_settle(float(payload.get("lingmai_confirm_settle_seconds") or 2.0))
        elif "前往灵脉" in text_compact:
            self._click_daily_lingmai_go_button(runtime, frame_next, task_label=task_label)
            yield from runtime.wait_action_settle(float(payload.get("lingmai_go_button_settle_seconds") or 2.0))
        else:
            self._log(
                "warning",
                f"{task_label}：点击 #286「占领」后未直接识别到 #287，继续等待 #288；"
                f"当前 {'#' + str(scene_next) if scene_next is not None else 'unknown'} {score_next:.0f}%，OCR={text_next[:120]}",
            )

        yield from runtime.wait_view(
            288,
            timeout=float(payload.get("lingmai_after_confirm_timeout") or 90.0),
            label=f"{task_label}：点击 #287「确认」后等待真实 #288 占领页",
        )
        scene_after, score_after, frame_after = runtime.current_scene([288], update=True)
        text_after = runtime.ocr_text(frame_after)
        self._log("success", f"{task_label}：已到达 #288，当前 #{scene_after if scene_after is not None else 'unknown'} {score_after:.0f}%，点击「占领」")
        return (yield from self._continue_daily_lingmai_from_final_occupy(ctx, stop_event, payload, runtime, task_label=task_label))

    def _click_daily_lingmai_kick_target(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: BehaviorTreeRuntime,
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
        target_name_variants = _lingmai_name_variants(target_name)
        if not target_id or not target_name_match:
            raise RuntimeError(f"{task_label}：驱离目标缺少稳定座位 ID 或姓名，已停止且未点击")
        if bool(target_player.get("excluded") or target_player.get("is_ally")):
            raise RuntimeError(f"{task_label}：驱离目标被标记为同盟/排除，已停止且未点击")

        name_shape = runtime.shape(286, "姓名")
        button_shape = runtime.shape(286, "驱离按钮")
        view286 = runtime.view(286)
        name_box = runtime.runner._box(name_shape.raw, view286.raw)
        button_box = runtime.runner._box(button_shape.raw, view286.raw)
        name_center_y = float(name_box.get("y") or 0) + float(name_box.get("h") or 0) / 2
        button_center_x = float(button_box.get("x") or 0) + float(button_box.get("w") or 0) / 2
        button_center_y = float(button_box.get("y") or 0) + float(button_box.get("h") or 0) / 2
        offset_y = button_center_y - name_center_y
        frame_width, frame_height = runtime.runner._frame_size(view286.raw)
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
            runtime.click_frame_point(286, click_x, click_y)
            yield from runtime.wait_scene(
                380,
                timeout=float(payload.get("lingmai_kick_to_380_timeout") or 60.0),
                label=f"{task_label}：点击「{target_name}」驱离按钮后等待 #380",
            )
            scene_id, score, frame380 = runtime.current_scene([380], update=True)
            if scene_id != 380:
                raise RuntimeError(f"{task_label}：驱离后未确认到达 #380，已停止后续点击")
            self._log("success", f"{task_label}：已到达 #380（{score:.0f}%），OCR={runtime.ocr_text(frame380)[:120]}")
            return (yield from self._complete_daily_lingmai_kick(runtime, payload, task_label=task_label))

        # The game preserves the seat-list scroll offset when switching
        # between Shengmai and Shenmai.  A down-only search from that retained
        # offset can therefore declare "bottom" while the selected target is
        # above the viewport.  Normalize to the top before scanning downward.
        if bool(payload.get("lingmai_kick_reset_to_top", False)):
            runtime.drag_shape_to_frame_edge(
                286,
                "姓名",
                direction="down",
                duration=float(payload.get("lingmai_kick_scroll_seconds") or 0.8),
            )
            yield from runtime.wait_action_settle(
                float(payload.get("lingmai_kick_scroll_settle_seconds") or 1.0)
            )
            scene_id, _score, _frame = runtime.current_scene([286], update=True)
            if scene_id != 286:
                raise RuntimeError(f"{task_label}：复位座位列表到顶部时离开 #286，已停止且未点击")
            self._log("detail", f"{task_label}：已先复位 #286 座位列表到顶部，再向下查找目标")

        reached_list_end = False
        best_below_threshold: dict[str, Any] | None = None
        for index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
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
                    direction = yield from runtime.nudge_shape_content_for_box(
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
            before_signature = runtime.image_signature_bytes_in_shape(
                name_shape,
                frame_data_url=frame,
            )
            runtime.drag_shape_to_frame_edge(
                286,
                "姓名",
                direction="up",
                duration=float(payload.get("lingmai_kick_scroll_seconds") or 0.8),
            )
            yield from runtime.wait_action_settle(float(payload.get("lingmai_kick_scroll_settle_seconds") or 1.0))
            scene_id, _score, _frame = runtime.current_scene([286], update=True)
            if scene_id != 286:
                raise RuntimeError(f"{task_label}：滚动寻找目标时离开 #286，已停止且未点击")
            after_signature = runtime.image_signature_bytes_in_shape(
                name_shape,
                frame_data_url=_frame,
            )
            similarity = runtime.image_signature_similarity(before_signature, after_signature)
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
        self,
        runtime: BehaviorTreeRuntime,
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
        yield from runtime.wait_action_settle(
            float(payload.get("lingmai_kick_button_ready_seconds") or 3.0)
        )
        for open_attempt in range(1, open_attempts + 1):
            # A successful click can open #381 only after the preceding
            # settle/sample has completed.  Re-authorize every retry from a
            # fresh scene first: if the delayed confirmation (or a direct
            # battle landing) is already present, consume it instead of
            # waiting for the now-absent #380 button and reporting a false
            # timeout.
            scene_id, _score, _frame = runtime.current_scene(
                [381, 318, 443, 380, 588],
                update=True,
            )
            if scene_id in {381, 318, 443}:
                confirmation_scene_id = int(scene_id)
                break
            if scene_id not in {380, 588}:
                raise RuntimeError(
                    f"{task_label}：重试点击 #380「驱离」前场景身份不可靠：scene={scene_id}"
                )
            yield from runtime.wait_click(380, "驱离")
            yield from runtime.wait_action_settle(
                float(payload.get("lingmai_kick_open_confirm_settle_seconds") or 3.0)
            )
            scene_id, _score, _frame = runtime.current_scene(
                [381, 318, 443, 380, 588],
                update=True,
            )
            if scene_id in {381, 318, 443}:
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
                    confirmation = yield from runtime.wait_scene(
                        381,
                        318,
                        443,
                        timeout=float(
                            payload.get("lingmai_kick_confirm_appear_timeout") or 20.0
                        ),
                        label=(
                            f"{task_label}：#380「驱离」点击后等待业务确认层/战前对白"
                        ),
                    )
                except TimeoutError:
                    scene_id, _score, _frame = runtime.current_scene(
                        [381, 318, 443, 380, 588],
                        update=True,
                    )
                    if scene_id in {381, 318, 443}:
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
        # matching also keeps the generic #47 popup guard from closing this
        # business confirmation before the intended click.
        if confirmation_scene_id == 381:
            yield from runtime.wait_click(
                381,
                "确定",
                timeout=float(payload.get("lingmai_kick_confirm_timeout") or 20.0),
            )
            pre_battle_scene = yield from runtime.wait_scene(
                318,
                443,
                timeout=float(payload.get("lingmai_kick_battle_dialogue_timeout") or 45.0),
                label=f"{task_label}：点击 #381「确定」后等待更换确认或战前对白",
            )
            pre_battle_scene_id = int(
                pre_battle_scene.id if isinstance(pre_battle_scene, View) else pre_battle_scene
            )
        else:
            pre_battle_scene_id = int(confirmation_scene_id or 0)
        if pre_battle_scene_id == 443:
            yield from self._click_daily_lingmai_switch_confirm(
                runtime,
                payload,
                task_label=task_label,
                scene_id=443,
            )
            yield from runtime.wait_view(
                318,
                timeout=float(payload.get("lingmai_kick_battle_dialogue_timeout") or 45.0),
                label=f"{task_label}：确认更换灵脉后等待 #318 战前对白",
            )
        battle_scene_id = yield from self._advance_daily_lingmai_kick_dialogue(
            runtime,
            terminal_scene_ids=(374, 382, 375, 588),
            timeout=float(payload.get("lingmai_kick_battle_start_timeout") or 60.0),
            label=f"{task_label}：推进战前对白直到战斗或胜利",
        )
        victory_scene_id = battle_scene_id
        if battle_scene_id == 374:
            victory_scene = yield from runtime.wait_scene(
                382,
                375,
                588,
                timeout=float(payload.get("lingmai_kick_battle_finish_timeout") or 180.0),
                label=f"{task_label}：等待战斗结束到胜利浮层 #382/#375",
            )
            victory_scene_id = int(victory_scene.id if isinstance(victory_scene, View) else victory_scene)
        if victory_scene_id == 588:
            scene_id, _score, frame = runtime.current_scene([588], update=True)
            return (yield from self._finish_daily_lingmai_to_world(
                runtime,
                payload,
                task_label=task_label,
                scene_id=scene_id,
                frame=frame,
            ))
        if victory_scene_id == 382:
            yield from self._close_daily_lingmai_victory_layers(
                runtime,
                payload,
                task_label=task_label,
            )
        else:
            yield from runtime.wait_click(victory_scene_id, "关闭")
            yield from runtime.wait_action_settle(
                float(payload.get("lingmai_kick_victory_close_settle_seconds") or 2.0)
            )
        post_battle_scene_id = yield from self._advance_daily_lingmai_kick_dialogue(
            runtime,
            terminal_scene_ids=(443, 289, 305, 306, 85, 186, 285, 588),
            timeout=float(payload.get("lingmai_kick_summary_timeout") or 45.0),
            label=f"{task_label}：推进战后对白直到换座/入座确认或稳定灵脉场景",
        )
        if post_battle_scene_id == 443:
            return (yield from self._confirm_daily_lingmai_switch_popup(
                runtime,
                payload,
                task_label=task_label,
                scene_id=443,
            ))
        if post_battle_scene_id == 289:
            yield from runtime.wait_click_then_view(
                289,
                "确认",
                [186, 85, 34, 285],
                timeout=float(payload.get("lingmai_kick_success_timeout") or 30.0),
            )
            scene_id, _score, frame = runtime.current_scene([186, 85, 34, 285], update=True)
        elif post_battle_scene_id == 305:
            return (yield from self._confirm_daily_lingmai_gather(
                runtime,
                payload,
                task_label=task_label,
            ))
        else:
            scene_id, _score, frame = runtime.current_scene(
                [post_battle_scene_id],
                update=True,
            )
        return (yield from self._finish_daily_lingmai_to_world(
            runtime,
            payload,
            task_label=task_label,
            scene_id=scene_id,
            frame=frame,
        ))

    def _close_daily_lingmai_victory_layers(
        self,
        runtime: BehaviorTreeRuntime,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> int:
        """Close every materialized #382 result layer with fresh scene proof."""

        successors = [382, 318, 303, 443, 289, 305, 306, 85, 186, 285, 588]
        max_layers = max(1, int(payload.get("lingmai_kick_victory_max_layers") or 4))
        for layer_index in range(max_layers):
            yield from runtime.wait_click(382, "关闭")
            # Multiple materialized victory sheets reuse the same #382 scene
            # identity.  A successful click may therefore reveal a fresh
            # #382 rather than leave the scene.  Waiting for identity loss
            # here deadlocks before the bounded layer loop can do its job.
            yield from runtime.wait_action_settle(
                float(payload.get("lingmai_kick_victory_settle_seconds") or 1.0)
            )
            landed = yield from runtime.wait_scene(
                *successors,
                timeout=float(payload.get("lingmai_kick_victory_layer_timeout") or 60.0),
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
        runtime: BehaviorTreeRuntime,
        *,
        terminal_scene_ids: tuple[int, ...],
        timeout: float,
        label: str,
        max_clicks: int = 16,
    ) -> int:
        """Advance alternating #318/#303 Lingmai dialogue to a real terminal scene."""

        terminals = tuple(int(scene_id) for scene_id in terminal_scene_ids)
        for _index in range(max(1, int(max_clicks))):
            scene = yield from runtime.wait_scene(
                318,
                303,
                *terminals,
                timeout=timeout,
                label=label,
            )
            scene_id = int(scene.id if isinstance(scene, View) else scene)
            if scene_id in terminals:
                return scene_id
            if scene_id == 318:
                runtime.click_shape_center(318, "确认")
            elif scene_id == 303:
                runtime.click_shape_center(303, "对话")
            else:
                raise RuntimeError(f"{label}：出现未声明对白场景 #{scene_id}")
            yield from runtime.wait_action_settle(1.0)
        raise RuntimeError(f"{label}：连续推进 {max_clicks} 次仍未到达 {terminals}")

    def _click_daily_lingmai_go_button(
        self,
        runtime: BehaviorTreeRuntime,
        frame: str | None,
        *,
        task_label: str,
    ) -> None:
        frame = frame if isinstance(frame, str) and frame else runtime.cur_frame(update=True)
        view = runtime.view(287)
        action = view.get_shape("前往灵脉") or view.get_shape("确认")
        if action is None:
            raise RuntimeError(f"{task_label}：#287 缺少「前往灵脉/确认」动作 shape")
        self._log("action", f"{task_label}：点击 #287「{action.title}」")
        runtime.click_shape_center(view, action)

    def _continue_daily_lingmai_from_final_occupy(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image288 = images.get(288)
        if not isinstance(image288, dict):
            raise RuntimeError(f"{task_label}：缺少 #288「占领」过渡后场景标注，无法确认灵脉占领")
        if self._find_shape(image288, "占领") is None:
            raise RuntimeError(f"{task_label}：缺少 #288「占领」shape 标注，无法点击过渡后的占领按钮")
        terminal_scene_ids = [443, 318, 306, 305, 285, 286, 287, 186, 47]
        try:
            waited_scene = yield from runtime.wait_click_then_view(
                288,
                "占领",
                terminal_scene_ids,
                settle_seconds=float(payload.get("lingmai_final_occupy_settle_seconds") or 2.0),
                timeout=float(payload.get("lingmai_final_occupy_timeout") or 20.0),
                max_clicks=int(payload.get("lingmai_final_occupy_max_clicks") or 2),
            )
        except TimeoutError:
            scene_final, score_final, frame_final = runtime.current_scene(
                [288, *terminal_scene_ids],
                update=True,
            )
            text_final = runtime.ocr_text(frame_final)
            raise RuntimeError(
                f"{task_label}：点击 #288「占领」后未进入合法后继；"
                f"当前 {'#' + str(scene_final) if scene_final is not None else 'unknown'} "
                f"{score_final:.0f}%，OCR={text_final[:160]}"
            )
        waited_scene_id = int(
            waited_scene.id if isinstance(waited_scene, View) else waited_scene
        )
        scene_final, score_final, frame_final = runtime.current_scene(
            terminal_scene_ids,
            update=True,
        )
        if scene_final is None and waited_scene_id in terminal_scene_ids:
            scene_final = waited_scene_id
            score_final = 100.0
            frame_final = runtime.cur_frame(update=True)
        text_final = runtime.ocr_text(frame_final)
        if scene_final == 318:
            return (yield from self._confirm_daily_lingmai_reward(runtime, payload, task_label=task_label))
        if scene_final == 305:
            return (yield from self._confirm_daily_lingmai_gather(runtime, payload, task_label=task_label))
        if scene_final in {443, 47}:
            return (yield from self._confirm_daily_lingmai_switch_popup(
                runtime,
                payload,
                task_label=task_label,
                scene_id=int(scene_final),
                frame=frame_final,
            ))
        if scene_final in {306, 186}:
            return (yield from self._finish_daily_lingmai_to_world(runtime, payload, task_label=task_label, scene_id=scene_final, frame=frame_final))
        raise RuntimeError(
            f"{task_label}：点击 #288「占领」后出现未实现的业务后继；"
            f"当前 {'#' + str(scene_final) if scene_final is not None else 'unknown'} {score_final:.0f}%，OCR={text_final[:160]}"
        )

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
        runtime: Any,
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ) -> tuple[str, str, str]:
        """Read #266 through its formal scene and local Shapes only."""

        scene_id, _score, frame = runtime.current_scene(
            [266],
            frame_data_url=frame_data_url,
            update=update,
        )
        if scene_id != 266:
            return "absent", "", frame
        text = runtime.ocr_text_in_shapes(
            266,
            ["法则之主名称", "法则详情", "拜谒"],
            padding=8,
            frame_data_url=frame,
        )
        if self._baiye_text_is_completed(text):
            return "completed", text, frame
        if self._baiye_text_can_worship(text):
            return "actionable", text, frame
        return "unknown", text, frame

    def _schedule_baiye_retry(self, payload: Mapping[str, Any], *, reason: str) -> str:
        retry_seconds = max(60, int(payload.get("baiye_lord_retry_seconds") or 300))
        next_time = (
            _behavior_tree_runtime._now() + timedelta(seconds=retry_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-baiye"),
            next_time,
        )
        self._log("warning", f"日常_拜谒：{reason}，于 {next_time} 重试")
        return next_time

    def _click_baiye_worship_button(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Iterator[Any]:
        self._log("action", f"日常_拜谒：{reason}，点击 #266「拜谒」")
        runtime.click_shape_center(266, "拜谒")
        timeout = max(2.0, float(payload.get("baiye_worship_confirm_timeout") or 20.0))
        poll_seconds = max(0.2, float(payload.get("baiye_worship_poll_seconds") or 1.0))
        state = "absent"
        worship_text = ""
        max_attempts = max(1, int(math.ceil(timeout / poll_seconds)))
        for attempt in range(1, max_attempts + 1):
            yield from runtime.wait_action_settle(poll_seconds)
            state, worship_text, _frame = self._baiye_detail_state(runtime, update=True)
            if state == "completed":
                self._log("success", f"日常_拜谒：已完成拜谒，OCR={worship_text[:120]}")
                yield from self._return_baiye_to_world(runtime, payload, reason="拜谒完成后收尾")
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

    def _return_baiye_to_world(
        self,
        runtime: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        reason: str,
    ) -> Iterator[Any]:
        settle_seconds = float((payload or {}).get("baiye_return_settle_seconds") or 1.0)
        self._log("action", f"日常_拜谒：{reason}，按拜谒页面栈返回 #34")
        for _attempt in range(8):
            frame = runtime.cur_frame(update=True)
            scene_id, score, _frame = runtime.current_scene(
                [266, 265, 264, 34],
                frame_data_url=frame,
            )
            if scene_id == 34:
                self._log("success", "日常_拜谒：已返回 #34 世界，闭环完成")
                return "success"
            if scene_id == 266:
                self._log("action", f"日常_拜谒：当前 #266 {score:.0f}%，点击「返回」回法则之主选择页")
                runtime.click_shape_center(266, "返回")
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if scene_id == 265:
                self._log("action", f"日常_拜谒：当前 #265 {score:.0f}%，点击「返回」回三千大道")
                runtime.click_shape_center(265, "返回")
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if scene_id == 264:
                self._log("action", f"日常_拜谒：当前 #264 {score:.0f}%，点击「返回」回世界")
                runtime.click_shape_center(264, "返回")
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            self._log("warning", f"日常_拜谒：页面栈返回未识别到 #264/#265/#266/#34，当前 scene={scene_id}，回退通用 goto #34")
            yield from runtime.goto_view(34)
            yield from runtime.wait_action_settle(settle_seconds)
            scene_id, _score, _frame = runtime.current_scene([34, 266, 265, 264], update=True)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([266, 265, 264, 69, 34], update=True)
        if scene_id == 266:
            result = yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target)
            if result == "success":
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
                    text = runtime.ocr_text(frame)
                    scene_id = yield from self._enter_daily_from_world_like(
                        ctx,
                        runtime,
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
                yield from runtime.wait_any(
                    {"scene": runtime.view_visible(264)},
                    timeout=20.0,
                    label="日常_拜谒：等待三千大道 #264",
                )
            yield from self._open_baiye_cross_rule(ctx, stop_event, payload, keyword=str(payload.get("cross_keyword") or "16"))
        result = yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target)
        if result == "success":
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

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：前往绿瓶 #20", phase="daily_green_bottle_baiye_goto_20")
            self._log_locked("action", "日常_绿瓶拜谒：调用通用场景移动前往 #20")
        yield from runtime.goto_view(20)
        scene_id, score, _frame = runtime.current_scene([20], update=True)
        if scene_id != 20:
            raise RuntimeError(f"日常_绿瓶拜谒：未能确认到达 #20，当前 scene={scene_id} score={score:.0f}%")
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：点击 #20「绿瓶」", phase="daily_green_bottle_baiye_click_bottle", current_scene=20)
            self._log_locked("success", f"日常_绿瓶拜谒：已到达 #20 {score:.0f}%")
            self._log_locked("action", "日常_绿瓶拜谒：点击 #20「绿瓶」")
        yield from runtime.wait_click(20, "绿瓶")
        yield from runtime.wait_action_settle(float(payload.get("green_bottle_settle_seconds") or 2.0))
        entry_scene_id, _entry_score, entry_frame = runtime.current_scene([282, 301, 20], update=True)
        if entry_scene_id == 301:
            entry_text = runtime.ocr_text(entry_frame)
            compact_entry_text = re.sub(r"\s+", "", _sanitize_ocr_text(entry_text))
            if "已达巅峰" in compact_entry_text:
                with self._lock:
                    self._log_locked("success", "日常_绿瓶拜谒：掌天瓶已达巅峰，今日绿瓶状态已确认")
                    self._log_locked("action", "日常_绿瓶拜谒：点击 #301「返回」退出掌天瓶详情")
                yield from runtime.wait_click(301, "返回")
                yield from runtime.wait_action_settle(float(payload.get("green_bottle_rank_back_settle_seconds") or 2.0))
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
                    yield from runtime.goto_view(34)
                    final_scene_id, final_score, _final_frame = runtime.current_scene([34], update=True)
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
        yield from runtime.wait_click(rank_scene_id, "境界排行")
        yield from runtime.wait_action_settle(float(payload.get("green_bottle_rank_settle_seconds") or 2.0))
        with self._lock:
            self._set_status_locked("running", "日常_绿瓶拜谒：点击 #283「拜谒」", phase="daily_green_bottle_baiye_click_baiye", current_scene=baiye_scene_id)
            self._log_locked("success", f"日常_绿瓶拜谒：已点击 #{rank_scene_id}「境界排行」")
            self._log_locked("action", "日常_绿瓶拜谒：确认天道魁首拜谒状态")
        worship_scene_id, _worship_score, _worship_frame = runtime.current_scene([baiye_scene_id], update=True)
        remaining_text = runtime.ocr_text_in_shapes(
            baiye_scene_id,
            ["剩余次数"],
            padding=8,
        )
        remaining_numbers = parse_ocr_values(_sanitize_ocr_text(remaining_text).translate(FULLWIDTH_DIGIT_TRANSLATION))
        if remaining_numbers is None:
            raise RuntimeError(f"日常_绿瓶拜谒：未能从 #283[剩余次数] 读取剩余次数，OCR={remaining_text[:80]}")
        remaining = remaining_numbers[0]
        if remaining == 0:
            with self._lock:
                self._log_locked("success", f"日常_绿瓶拜谒：#283[剩余次数] 首个数值为 0，今日拜谒已完成，scene={worship_scene_id}")
        else:
            with self._lock:
                self._log_locked("action", f"日常_绿瓶拜谒：#283[剩余次数]={remaining}，点击 #283「拜谒」")
            runtime.click_shape_center(baiye_scene_id, "拜谒")
            yield from runtime.wait_action_settle(float(payload.get("green_bottle_baiye_settle_seconds") or 2.0))
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
            yield from runtime.goto_view(34)
            final_scene_id, final_score, _final_frame = runtime.current_scene([34], update=True)
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

    def _open_baiye_cross_rule(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        keyword: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        view264 = runtime.view(264)
        list_shape = runtime.shape(view264, "识别区")
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
                frame = runtime.cur_frame(update=True)
                lines = runtime.ocr_fragments_in_shapes(264, ["识别区"], frame_data_url=frame) if hasattr(runtime, "ocr_fragments_in_shapes") else []
                tokens = runtime.ocr_tokens_in_shapes(264, ["识别区"], frame_data_url=frame)
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
                    runtime.click_frame_point(264, x, y)
                    yield from runtime.wait_any(
                        {"scene": runtime.view_visible(265)},
                        timeout=20.0,
                        label="日常_拜谒：等待法则之主 #265",
                    )
                    return "open"
                if scroll_index >= int(scroll_count):
                    break
                self._log("action", f"日常_拜谒：#264 未找到 {keyword}，{direction} 滚动 {scroll_index + 1}")
                changed = yield from runtime.scroll_shape_content(view264, list_shape, direction=direction)
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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
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
                runtime.click_shape_center(265, "返回")
                yield from runtime.wait_any(
                    {"scene": runtime.view_visible(264)},
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
            frame = runtime.cur_frame(update=True)
            detail_state, detail_text, _detail_frame = self._baiye_detail_state(
                runtime,
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
                yield from self._return_baiye_to_world(runtime, payload, reason="检测到已完成态后收尾")
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
                return (yield from self._click_baiye_worship_button(runtime, payload, reason=reason))
            if detail_state == "unknown":
                self._schedule_baiye_retry(
                    payload,
                    reason=f"#266 状态不确定，禁止返回 success，OCR={detail_text[:80]}",
                )
                return "skipped"

            scene_id, _score, _scene_frame = runtime.current_scene(
                [265],
                frame_data_url=frame,
            )
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
                yield from runtime.wait_action_settle(poll_seconds)
                continue
            unrecognized_since = None
            ocr_options = {
                "text_det_thresh": float(payload.get("baiye_text_det_thresh") or 0.25),
                "text_det_box_thresh": float(payload.get("baiye_text_det_box_thresh") or 0.45),
                "text_det_unclip_ratio": float(payload.get("baiye_text_det_unclip_ratio") or 1.2),
            }
            lines = (
                runtime.ocr_fragments_in_shapes(
                    265,
                    ["识别区"],
                    frame_data_url=frame,
                    options=ocr_options,
                )
                if hasattr(runtime, "ocr_fragments_in_shapes")
                else []
            )
            tokens = runtime.ocr_tokens_in_shapes(
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
                        runtime.click_shape_center(265, "返回")
                        yield from runtime.wait_any(
                            {"scene": runtime.view_visible(264)},
                            timeout=20.0,
                            label="日常_拜谒：probe 等待返回 #264",
                        )
                    return "skipped"
                runtime.click_frame_point(265, click_x, click_y)
                yield from runtime.wait_action_settle(1.0)
                try:
                    yield from runtime.wait_view(
                        266,
                        timeout=float(payload.get("baiye_detail_wait_seconds") or 12.0),
                        label=f"日常_拜谒：等待「{target}」详情 #266",
                    )
                except TimeoutError:
                    self._schedule_baiye_retry(
                        payload,
                        reason=f"点击「{target}」后未在时限内识别到 #266",
                    )
                    return "skipped"
                after_state, after_text, _after_frame = self._baiye_detail_state(runtime, update=True)
                if after_state == "completed":
                    self._log("success", f"日常_拜谒：已点击「{target}」，完成态 OCR={after_text[:120]}")
                    yield from self._return_baiye_to_world(runtime, payload, reason=f"已点击「{target}」进入完成态后收尾")
                    return "success"
                if after_state == "actionable":
                    if not self._baiye_text_is_target_worship_page(after_text, target):
                        self._schedule_baiye_retry(
                            payload,
                            reason=f"选择「{target}」后详情页目标不确定，禁止点击拜谒",
                        )
                        return "skipped"
                    return (yield from self._click_baiye_worship_button(runtime, payload, reason=f"已选中「{target}」且显示可拜谒"))
                self._schedule_baiye_retry(
                    payload,
                    reason=f"点击「{target}」后未确认 #266 完成/可拜谒态，state={after_state}",
                )
                return "skipped"
            self._log("detail", f"日常_拜谒：暂未命中「{target}」，OCR={last_text[:80]}")
            yield from runtime.wait_action_settle(poll_seconds)

    def _daily_youli_current_state(self, runtime: Any, *, update: bool = False) -> tuple[int | None, float, str, str]:
        scene_id, score, frame = runtime.current_scene([237, 236, 233, 229, 228, 71, 69, 34], update=update)
        if scene_id is None:
            scene_id, score, frame = runtime.current_scene(frame_data_url=frame)
        return scene_id, float(score), frame, runtime.ocr_text(frame)

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
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, text = self._daily_youli_current_state(runtime, update=True)
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
                scene_id, _score, frame, text = self._daily_youli_current_state(runtime, update=True)
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
                        runtime,
                        stop_event,
                        payload,
                        image34,
                        image228,
                        task_label=task_label,
                    )
                ):
                    scene_id, _score, frame = runtime.current_scene([71, 228], update=True)
                    if scene_id == 71:
                        yield from self._select_daily_youli_from_xiuxianzhuan_menu(ctx, stop_event, payload, image71, task_label=task_label)
                        yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label="日常_游历：等待修仙传游历 #228")
                    yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
                    return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
                scene_id = yield from self._enter_daily_from_world_like(
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
