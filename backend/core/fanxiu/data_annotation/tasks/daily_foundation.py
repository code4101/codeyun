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
from typing import Any, Callable, Iterator, Mapping

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens, locate_text_box, query_spatial_ocr
from backend.core.fanxiu.data_annotation.duel_strategy import (
    best_order_for_enemy_candidates,
    infer_enemy_candidate_order,
    parse_slot_value_title,
    plan_swaps,
)
from backend.core.fanxiu.data_annotation import runtime_runner as _runtime_runner
from backend.core.fanxiu.data_annotation.storage import data_annotation_entry_image_dir
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.runtime_runner import (
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
    LUNDAO_DALUO_ROOM_ID,
    LUNDAO_SANQING_ROOM_ID,
    current_lundao_player_profile,
    evaluate_lundao_room_opportunity,
    lundao_safety_threshold,
    next_lundao_daily_trigger,
    next_lundao_recheck,
    plan_lundao_strategy,
    read_current_lundao_facts,
    refresh_and_select_lundao_kick_target,
)
from backend.core.fanxiu.data_annotation.tasks.lingmai import refresh_and_select_lingmai_seat_action
from backend.core.fanxiu.data_annotation.state import (
    next_data_annotation_scheduler_time,
    parse_data_annotation_daily_clock,
    parse_data_annotation_task_time,
)
from backend.core.fanxiu.packet.service_runtime import (
    request_fanxiu_packet_service_catch_up,
    start_fanxiu_packet_service,
)


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
    ("daily_gongfeng", "legacy-daily-gongfeng", r"供奉"),
    ("daily_xianshi", "legacy-daily-xianshi", r"仙市"),
)

_DAILY_AUDIT_COMPLETION_MIN_TOTAL: dict[str, int] = {
    "daily_dungeon": 6,
}

# “进入论道”最终要收敛到四种互斥的稳定业务状态。这里只记录用户已经
# 确认的正式场景；未知编号保持空元组，避免用猜测制造虚假路由。
_DAILY_LUNDAO_STABLE_STATE_SCENE_IDS: dict[str, tuple[int, ...]] = {
    "ready": (),
    "in_progress": (304,),
    "kicked": (391,),
    "completed": (),
}
_DAILY_LUNDAO_ENTRY_LAYER0_SCENE_IDS: tuple[int, ...] = (296, 304, 391)

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
    def _payload_int(self, payload: dict[str, Any], *keys: str, default: int) -> int:
        for key in keys:
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None or value == "":
                continue
            return int(value)
        return int(default)

    def _execute_daily_boss_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_首领资产树路径，无法执行作业")
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
            reverse_scrolls=10,
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
        remaining = self._daily_boss_reward_remaining_from_scene(ctx, image178)
        if remaining is None:
            remaining = _parse_daily_boss_reward_remaining(runtime.ocr_text(update=True))
        if remaining == 0:
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
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
                last_result="success",
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
            next_time = (_runtime_runner._now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
            self._record_scheduler_task_discovered_retry_after(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
                last_result="skipped",
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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        view181 = View(images[181]) if isinstance(images.get(181), dict) else None
        if view181 is None and hasattr(runtime, "get_view"):
            try:
                view181 = runtime.get_view(181)
            except Exception as exc:
                with self._lock:
                    self._log_locked("warning", f"日常_首领：读取 #181 视图失败，直接走通用回世界：{exc}")
        if isinstance(view181, View):
            leave_shape = view181.get_shape("离开")
            if leave_shape is not None:
                with self._lock:
                    self._set_status_locked("running", "日常_首领：#181 封印完成，点击离开", phase="daily_boss_leave_done", current_scene=181)
                    self._log_locked("action", "日常_首领：点击 #181「离开」")
                leave_shape.click(runtime)
                yield from runtime.wait_action_settle(2.0)
            else:
                with self._lock:
                    self._log_locked("warning", "日常_首领：#181 缺少「离开」标注，直接走通用回世界")
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：本轮挑战已结束，{next_time} 后复查首领次数/CD",
                phase="daily_boss_done",
                current_scene=181,
            )
            self._log_locked("skip", self._status["message"])
        yield from self._return_daily_boss_to_world(ctx, stop_event)
        return "skipped"

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
        if scene_id == 34:
            yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
            return "success"
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
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
            if scene_id == 34 or self._daily_assistant_text_is_world_like(_text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                return "success"
        with self._lock:
            self._set_status_locked("running", "日常_首领：收尾回到世界 #34", phase="daily_boss_return_world", current_scene=scene_id)
            self._log_locked("action", "日常_首领：完成后按场景图回到 #34 世界")
        try:
            self._clear_tick_frame(ctx)
            runtime.clear_frame()
            yield from runtime.goto_view(34)
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
        runtime: FanxiuRuntime,
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
        runtime: FanxiuRuntime,
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
        runtime: FanxiuRuntime,
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
                if scene_id == 34 or self._daily_assistant_text_is_world_like(_text):
                    try:
                        yield from self._close_world_reward_tip_stack_if_present(ctx, runtime, stop_event, label="日常_首领")
                    except Exception as exc:
                        with self._lock:
                            self._log_locked("warning", f"日常_首领：离开战斗后清理世界奖励提示失败，继续尝试进入日常：{exc}")
                with self._lock:
                    self._set_status_locked("running", "日常_首领：离开战斗后重新进入日常 #69", phase="daily_boss_reopen_daily_after_leave")
                    self._log_locked("action", "日常_首领：离开战斗后按场景图跳转到 #69")
                yield from runtime.goto_view(69)
            status = yield from self._open_daily_boss_list_from_daily(ctx, stop_event)
            return "done" if status == "done" else True
        except Exception as exc:
            scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id == 34 or self._daily_assistant_text_is_world_like(_text):
                with self._lock:
                    self._log_locked("warning", f"日常_首领：离开战斗后复核 #178 失败，但已回到世界，转为稍后复查：{exc}")
                return False
            with self._lock:
                self._log_locked("warning", f"日常_首领：离开战斗后重新进入 #178 失败：{exc}")
            return False

    def _close_daily_boss_reward_result_if_present(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
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
                    self._log_locked("warning", "日常_首领：缺少 #181「离开」标注，无法回列表读取 #182 刷新时间")
        if returned_to_list:
            next_time, source = self._record_daily_boss_next_time_from_current_list(ctx, payload)
            return next_time, f"已识别 #181 封印完成；{source}"

        challenge_remaining = payload.get("_daily_boss_challenge_remaining")
        try:
            challenge_remaining_int = int(challenge_remaining) if challenge_remaining is not None else None
        except (TypeError, ValueError):
            challenge_remaining_int = None
        if challenge_remaining_int is not None and challenge_remaining_int <= 1:
            next_time = self._next_daily_boss_reset_time_text()
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
                last_result="success",
            )
            return next_time, "挑战前剩余奖励次数为 1，已识别 #181 封印完成，奖励次数已用尽"
        if challenge_remaining_int is not None:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            return next_time, f"已识别 #181 封印完成；挑战前剩余奖励次数为 {challenge_remaining_int}，半小时后复查刷新 CD"
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "已识别 #181 封印完成；挑战前奖励次数未知，半小时后复查刷新 CD"

    def _record_daily_boss_next_time_from_current_list(self, ctx: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
        remaining = self._daily_boss_reward_remaining_from_scene(ctx, ctx.get("images", {}).get(178) or {})
        if remaining == 0:
            next_time = self._next_daily_boss_reset_time_text()
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
                last_result="success",
            )
            return next_time, "奖励次数已用尽"
        cd_seconds, cd_text = self._daily_boss_refresh_cd_from_list(ctx)
        if cd_seconds and cd_seconds > 0:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=cd_seconds + 10)
            return next_time, f"按 #182 刷新时间读取 {cd_text or str(cd_seconds) + ' 秒'}"
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "奖励次数未用尽但未读到 #182 刷新时间，半小时后复查"

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
        next_time = (_runtime_runner._now() + timedelta(seconds=recheck_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_retry_after(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
            task_type="daily_boss",
            label="日常_首领",
            last_result="skipped",
        )
        return next_time

    def _record_daily_boss_done_for_today(self, payload: dict[str, Any]) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
            task_type="daily_boss",
            label="日常_首领",
            last_result="success",
        )
        return next_time

    def _daily_boss_refresh_cd_from_list(self, ctx: dict[str, Any]) -> tuple[int | None, str]:
        images = ctx.get("images", {}) if isinstance(ctx.get("images"), dict) else {}
        image182 = images.get(182)
        image178 = images.get(178)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        frame = runtime.cur_frame(update=True)
        texts: list[str] = []
        if isinstance(image182, dict):
            text = runtime.ocr_text_in_shapes(View(image182), ("刷新时间",), padding=20, frame_data_url=frame)
            if text:
                texts.append(text)
                cd_seconds = _parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0:
                    return cd_seconds, text
        if isinstance(image178, dict):
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

    def _next_daily_boss_reset_time_text(self) -> str:
        now = _runtime_runner._now()
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

    def _daily_lingzu_text_is_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "灵祖挑战" in normalized and "今日剩余次数" in normalized and ("前往" in normalized or "奖励预览" in normalized)

    def _daily_lingzu_text_is_elder(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "灵祖挑战" in normalized and (
            "战灵长老" in normalized
            or "战灵" in normalized
            or "每日能够挑战一次妖灵之祖" in normalized
            or "灵祖魂息" in normalized
        )

    def _daily_lingzu_text_is_boss(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "圣雷龙妖祖" in normalized or ("剩余奖励次数" in normalized and ("快速挑战" in normalized or "前往" in normalized))

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
        if self._daily_lingzu_text_is_detail(text):
            scene_id = 184
        elif self._daily_lingzu_text_is_elder(text):
            scene_id = 187
        elif self._daily_lingzu_text_is_boss(text):
            scene_id = 188
        elif "点击退出" in text or "挑战结算" in text:
            scene_id = 189
        elif (
            "点击查看" in text
            and ("灵环" in text or "宝魄" in text)
            and not self._daily_assistant_text_is_world_like(text)
        ):
            scene_id = 186
        return scene_id, score, text

    def _record_daily_lingzu_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_lingzu_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingzu")
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_lingzu",
            label="日常_灵祖",
            last_result="success",
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
        return "#186" in message or "奖励浮层" in message or "宝魄" in message or "点击查看" in message

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

    def _daily_lingzu_discovered_next_time_is_future(self, payload: dict[str, Any]) -> str | None:
        task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingzu").strip() or "legacy-daily-lingzu"
        facts = _read_data_annotation_world_facts()
        discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
        task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
        fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
        next_time = str(fact.get("discovered_next_time") or fact.get("next_time") or "").strip()
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
        discovered_next_time = self._daily_lingzu_discovered_next_time_is_future(payload)
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
        scene_id, _score, current_text = self._daily_lingzu_scene_from_frame(ctx, frame, scene_id, _score)
        if discovered_next_time and scene_id not in {183, 184, 185, 186, 187, 188, 189}:
            with self._lock:
                self._set_status_locked(
                    "done",
                    f"日常_灵祖：已记录今日完成，下次 {discovered_next_time}",
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
                try:
                    text = runtime.ocr_text(update=True)
                except TypeError:
                    text = runtime.ocr_text(runtime.cur_frame(update=True) if hasattr(runtime, "cur_frame") else None)
                if "点击查看" not in text and "灵环" not in text and "宝魄" not in text:
                    scene_id, _score, _frame = runtime.current_scene([34, 183, 184, 187, 188])
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
        if scene_id != 34 and not self._daily_lingta_text_is_world_like(text):
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
            if scene_id in {289, 86} or self._leave_scene_confirm_text(text):
                confirm_id = int(scene_id) if scene_id in {289, 86} else 86
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
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_jianling",
            label="日常_剑灵",
            last_result="success",
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
            reverse_scrolls=self._payload_int(
                payload,
                "reverse_scrolls",
                "jianling_reverse_scrolls",
                "max_scrolls",
                "jianling_max_scrolls",
                default=30,
            ),
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
            reverse_scrolls=self._payload_int(
                payload,
                "reverse_scrolls",
                "jianling_reverse_scrolls",
                "max_scrolls",
                "jianling_max_scrolls",
                default=30,
            ),
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
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_lingta",
            label="日常_灵塔",
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

    def _daily_lingta_text_is_world_like(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "绿瓶" in normalized:
            return False
        markers = ("储物袋", "大地图", "仙市", "仙府", "天机阁", "角色", "装备", "功法书")
        return sum(1 for marker in markers if marker in normalized) >= 2

    def _world_reward_tip_text_matches(self, text: str) -> bool:
        compact = _sanitize_ocr_text(text).replace(" ", "")
        if any(token in compact for token in ("供奉总览", "接受供奉", "今日接受供奉次数")):
            return False
        if "点击查看" not in compact and "点击使用" not in compact:
            return False
        return any(token in compact for token in ("宝魄", "丹药", "炼化", "获得", "奖励", "灵玉", "天尊", "仙玉", "随机匣", "兽渊"))

    def _world_reward_tip_detected(
        self,
        ctx: dict[str, Any],
        frame: str,
        text: str = "",
        *,
        menu_ocr_fragments: list[dict[str, Any]] | None = None,
    ) -> bool:
        full_frame_matches = self._world_reward_tip_text_matches(text)
        image35 = ctx.get("images", {}).get(35)
        if not isinstance(image35, dict) or not self._find_shape(image35, "菜单"):
            return full_frame_matches
        lines = menu_ocr_fragments
        if lines is None:
            try:
                lines = self._ocr_fragments_in_shapes(frame, image35, ("菜单",), padding=8)
            except Exception as exc:
                self._log("detail", f"世界提示清理：#35 菜单 OCR 失败：{exc}")
                return False
        menu_text = self._ocr_text(lines or [])
        # 全屏 OCR 会同时读到世界页活动卡片里的“奖励/点击查看”和
        # 正常底部菜单；只有点击提示实际覆盖菜单区域时，才把它当成
        # 挡住菜单的奖励卡。否则会在普通世界页反复点击关闭坐标。
        menu_compact = _sanitize_ocr_text(menu_text).replace(" ", "")
        return "点击查看" in menu_compact or "点击使用" in menu_compact

    def _close_world_reward_tip_if_present(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
        frame: str,
        text: str,
        *,
        label: str,
    ) -> bool:
        if not self._world_reward_tip_detected(ctx, frame, text):
            return False
        image34 = ctx.get("images", {}).get(34)
        if not isinstance(image34, dict):
            return False
        width, height = self._frame_size(image34)
        close_x = width * 0.767
        close_y = height * 0.605
        with self._lock:
            self._set_status_locked("running", f"{label}：关闭世界页奖励提示", phase="close_world_reward_tip", current_scene=34)
            self._log_locked("action", f"{label}：检测到世界页奖励提示，点击奖励卡关闭按钮")
        runtime.click_frame_point(image34, close_x, close_y)
        return True

    def _close_world_reward_tip_stack_if_present(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
        stop_event: threading.Event,
        *,
        label: str,
        max_attempts: int = 15,
    ):
        closed_count = 0
        tip_still_present = False
        for _attempt in range(max(1, int(max_attempts))):
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if not self._close_world_reward_tip_if_present(ctx, runtime, frame, text, label=label):
                tip_still_present = False
                break
            tip_still_present = True
            closed_count += 1
            yield from runtime.wait_action_settle(0.6)
        if closed_count:
            self._log("success", f"{label}：已关闭 {closed_count} 张世界奖励提示")
        if tip_still_present:
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            if self._world_reward_tip_detected(ctx, frame, text):
                self._log("warning", f"{label}：世界奖励提示关闭达到上限 {max_attempts}，仍可能残留提示 OCR={text[:120]}")

    def _world_scene_leave_matches(
        self,
        lines: list[dict[str, Any]],
        *,
        width: float = 900.0,
        height: float = 1600.0,
    ) -> list[tuple[float, float, str]]:
        matches: list[tuple[float, float, str]] = []
        split_chars: list[tuple[str, float, float, float, float]] = []
        for line in lines:
            text = re.sub(r"\s+", "", _sanitize_ocr_text(line.get("text")))
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if text in {"离", "开"} and cx >= width * 0.72 and height * 0.30 <= cy <= height * 0.70:
                split_chars.append((text, x, y, w, h))
            if text != "离开":
                continue
            if cx >= width * 0.72 and height * 0.30 <= cy <= height * 0.70:
                click_x = max(0.0, min(width, cx - min(16.0, w * 0.25)))
                click_y = max(0.0, min(height, cy - max(56.0, h * 1.75)))
                matches.append((click_x, click_y, text))
        for left in split_chars:
            if left[0] != "离":
                continue
            _left_text, lx, ly, lw, lh = left
            lcx = lx + lw / 2
            lcy = ly + lh / 2
            for right in split_chars:
                if right[0] != "开":
                    continue
                _right_text, rx, ry, rw, rh = right
                rcx = rx + rw / 2
                rcy = ry + rh / 2
                max_x_gap = max(48.0, lw + rw)
                max_y_gap = max(48.0, (lh + rh) * 3.0)
                if abs(lcx - rcx) > max_x_gap:
                    continue
                if not (0 < rcy - lcy <= max_y_gap):
                    continue
                x1 = min(lx, rx)
                y1 = min(ly, ry)
                x2 = max(lx + lw, rx + rw)
                y2 = max(ly + lh, ry + rh)
                bw = max(1.0, x2 - x1)
                bh = max(1.0, y2 - y1)
                cx = x1 + bw / 2
                cy = y1 + bh / 2
                click_x = max(0.0, min(width, cx - min(16.0, bw * 0.25)))
                click_y = max(0.0, min(height, cy - max(56.0, bh * 0.5)))
                matches.append((click_x, click_y, "离开"))
        return sorted(matches, key=lambda item: (item[0], item[1]), reverse=True)

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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if self._is_daily_weekly_dungeon_tiangong_page_text(text):
            action_runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
            self._log("action", f"{label}：当前在周本玉霄天宫页，点击 #326「返回」恢复起点")
            action_runtime.click_shape_center(326, "返回")
            yield from action_runtime.wait_action_settle(1.0)
            target = yield from action_runtime.wait_view(325, 69, 34, timeout=18.0, label=f"{label}：等待离开周本玉霄天宫页")
            target_id = int(target.id) if isinstance(target, View) and target.id is not None else int(target)
            if target_id == 325:
                self._log("action", f"{label}：从 #325 周本入口返回日常页")
                action_runtime.click_shape_center(325, "返回")
                yield from action_runtime.wait_action_settle(1.0)
                target = yield from action_runtime.wait_view(69, 34, timeout=18.0, label=f"{label}：等待离开周本入口页")
                target_id = int(target.id) if isinstance(target, View) and target.id is not None else int(target)
            if target_id == 34:
                ctx["_go_scene_known_scene_id"] = 34
            return True
        compact_text = re.sub(r"\s+", "", _sanitize_ocr_text(str(text or "")))
        if "拜仙台" in compact_text and ("入驻仙侣" in compact_text or "全属性加成" in compact_text):
            action_runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
            ref_image = (ctx.get("images") or {}).get(34) if isinstance(ctx.get("images"), dict) else None
            click_view = View(ref_image if isinstance(ref_image, dict) else {"filename": "world_runtime.png", "width": 900, "height": 1600})
            self._log("action", f"{label}：当前在仙府拜仙台页，点击左下返回恢复起点")
            action_runtime.click_frame_point(click_view, 76, 1490)
            yield from action_runtime.wait_action_settle(1.5)
            target = yield from action_runtime.wait_view(69, 34, timeout=25.0, label=f"{label}：等待离开仙府拜仙台页")
            target_id = int(target.id) if isinstance(target, View) and target.id is not None else int(target)
            if target_id == 34:
                ctx["_go_scene_known_scene_id"] = 34
            return True
        if require_world_like and not self._daily_assistant_text_is_world_like(text):
            return False
        ref_image = images.get(34) if isinstance(images.get(34), dict) else {"filename": "world_runtime.png", "width": 900, "height": 1600}
        runtime = self._fanxiu_observer(ctx, stop_event, frame_data_url=frame)
        baiye_scene_id = self._baiye_stack_scene_from_text(text)
        baiye_score = 100.0 if baiye_scene_id is not None else 0.0
        if baiye_scene_id is None:
            candidate_scene_id, candidate_score = self._identify_scene_number(ctx, frame, [266, 265, 264])
            if candidate_scene_id in {266, 265, 264} and self._scene_matches_id(int(candidate_scene_id), float(candidate_score or 0.0)):
                baiye_scene_id = int(candidate_scene_id)
                baiye_score = float(candidate_score or 0.0)
        if baiye_scene_id in {266, 265, 264}:
            action_runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
            if baiye_scene_id == 266:
                self._log("action", f"{label}：当前实际为 #266 {baiye_score:.0f}%，优先点击拜谒详情「返回」")
                action_runtime.click_shape_center(266, "返回")
                yield from action_runtime.wait_view(265, 264, 34, timeout=18.0, label=f"{label}：等待 #266 返回")
            elif baiye_scene_id == 265:
                self._log("action", f"{label}：当前实际为 #265 {baiye_score:.0f}%，优先点击法则之主「返回」")
                action_runtime.click_shape_center(265, "返回")
                yield from action_runtime.wait_view(264, 34, timeout=18.0, label=f"{label}：等待 #265 返回")
            else:
                self._log("action", f"{label}：当前实际为 #264 {baiye_score:.0f}%，优先点击三千大道「返回」")
                action_runtime.click_shape_center(264, "返回")
                yield from action_runtime.wait_view(34, timeout=18.0, label=f"{label}：等待 #264 返回世界")
                ctx["_go_scene_known_scene_id"] = 34
            yield from action_runtime.wait_action_settle(1.0)
            return True
        side_popup_scene_id, side_popup_score = self._identify_scene_number(ctx, frame, [233, 225])
        if side_popup_scene_id in {233, 225} and self._scene_matches_id(int(side_popup_scene_id), float(side_popup_score or 0.0)):
            action_runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
            popup_id = int(side_popup_scene_id)
            self._log("action", f"{label}：当前实际为 #{popup_id} {float(side_popup_score or 0.0):.0f}%，点击「空白」关闭资源不足提示")
            yield from action_runtime.wait_click(popup_id, "空白", timeout=8.0)
            yield from action_runtime.wait_action_settle(1.0)
            scene_after_popup, _score_after_popup, frame_after_popup = action_runtime.current_scene([228, 223, 69, 34], update=True)
            if scene_after_popup == 34:
                ctx["_go_scene_known_scene_id"] = 34
                return True
            if scene_after_popup == 69:
                return True
            text_after_popup = action_runtime.ocr_text(frame_after_popup)
            recovered = yield from self._leave_world_side_scene_if_present(
                ctx,
                stop_event,
                frame_after_popup,
                text_after_popup,
                label=label,
                require_world_like=False,
            )
            if recovered:
                return True
        width, height = self._frame_size(ref_image)
        click_image = ref_image
        matches: list[tuple[float, float, str]] = []
        image85 = images.get(85) if isinstance(images.get(85), dict) else None
        leave_shape = self._find_shape(image85, "离开") if isinstance(image85, dict) else None
        if isinstance(image85, dict) and isinstance(leave_shape, dict):
            score = float(self._shape_score(ctx, image85, leave_shape, frame) or 0.0)
            if score >= float(self.scene_threshold):
                x, y = ActionPlanner().shape_center(image85, leave_shape)
                matches = [(float(x), float(y), f"#85「离开」{score:.0f}%")]
                click_image = image85
        if not matches:
            lines = runtime.ocr_fragments(frame)
            matches = self._world_scene_leave_matches(lines, width=width, height=height)
            click_image = ref_image
        if not matches:
            return False
        x, y, matched_text = matches[0]
        with self._lock:
                    self._set_status_locked("running", f"{label}：当前在场景内，点击右侧「离开」", phase="world_side_scene_leave", current_scene=None)
                    self._log_locked("action", f"{label}：命中右侧「{matched_text}」，先离开场景")
        runtime.click_frame_point(View(click_image), x, y)
        yield from runtime.wait_action_settle(2.0)
        confirm_scene_id, _confirm_score, confirm_frame = runtime.current_scene([289, 86], update=True)
        confirm_text = runtime.ocr_text(confirm_frame)
        if confirm_scene_id in {289, 86} or self._leave_scene_confirm_text(confirm_text):
            confirm_id = int(confirm_scene_id) if confirm_scene_id in {289, 86} else (289 if isinstance(images.get(289), dict) else 86)
            confirm_image = images.get(confirm_id) if isinstance(images.get(confirm_id), dict) else None
            confirm_shape = self._find_shape(confirm_image, "确认") if isinstance(confirm_image, dict) else None
            if isinstance(confirm_image, dict) and confirm_shape is not None:
                with self._lock:
                    self._set_status_locked("running", f"{label}：确认离开当前场景", phase="world_side_scene_leave_confirm", current_scene=confirm_id)
                    self._log_locked("action", f"{label}：点击 #{confirm_id}「确认」离开场景")
                yield from runtime.wait_click(confirm_id, "确认")
                yield from runtime.wait_action_settle(2.0)
        with self._lock:
            self._set_status_locked("running", f"{label}：等待返回世界 #34", phase="world_side_scene_leave_wait_world", current_scene=34)
        wait_runtime = runtime if hasattr(runtime, "wait_view") else self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from wait_runtime.wait_view(34, timeout=12.0, label=f"{label}：等待返回世界 #34")
        if wait_runtime is runtime and hasattr(wait_runtime, "cur_frame"):
            self._set_tick_frame(ctx, wait_runtime.cur_frame(update=False))
        ctx["_go_scene_known_scene_id"] = 34
        return True

    def _enter_daily_from_world_like(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
        stop_event: threading.Event,
        frame: str,
        scene_id: int | None,
        text: str,
        *,
        label: str,
    ):
        if scene_id == 69 and self._daily_text_is_daily_list(text):
            return 69
        if scene_id is None:
            scene_id, _score, frame = runtime.current_scene(frame_data_url=frame)
            text = runtime.ocr_text(frame)
            if scene_id == 69 and self._daily_text_is_daily_list(text):
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
            if scene_id == 69 and self._daily_text_is_daily_list(text):
                return 69
            if scene_id == 34 or self._daily_assistant_text_is_world_like(text):
                world_like = True
            else:
                world_like = False
        else:
            world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
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
            if scene_id == 69 and self._daily_text_is_daily_list(text):
                return 69
            world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
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
            if scene_id == 69 and self._daily_text_is_daily_list(text):
                return 69
            world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
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
                if scene_id == 69 and self._daily_text_is_daily_list(text):
                    return 69
                world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
            except Exception as exc:
                self._log("warning", f"{label}：修仙传游历页返回世界失败，继续尝试场景图恢复：{exc}")
        hidden_world_popup_like = scene_id == 59
        if scene_id is None and not world_like and not hidden_world_popup_like:
            try:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{label}：当前场景 unknown，先尝试关闭安全弹层",
                        phase="daily_recover_unknown_popup",
                        current_scene=None,
                    )
                    self._log_locked("action", f"{label}：unknown 起点，调用弹窗守护安全关闭")
                if self._auto_close_popup_guard_step(runtime, allow_confirm_actions=False, during_task=True):
                    yield from runtime.wait_action_settle(2.0)
                    scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                    text = runtime.ocr_text(frame)
                    popup_view = runtime.find_view("弹窗")
                    if scene_id == 59 or (popup_view is not None and popup_view.id == 59):
                        raise RuntimeError("#59「封魔杀」弹层点击安全关闭后仍未离开，当前「空白」标注无效，需要人工补有效关闭/返回/前往路径")
                    if scene_id == 69 and self._daily_text_is_daily_list(text):
                        return 69
                    world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
            except Exception as exc:
                if "标注无效" in str(exc):
                    raise RuntimeError(f"{label}：{exc}") from exc
                self._log("warning", f"{label}：unknown 起点安全弹层关闭探测失败，继续尝试场景图恢复：{exc}")
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
                if scene_id == 69 and self._daily_text_is_daily_list(text):
                    return 69
                world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
            except Exception as exc:
                if "标注无效" in str(exc):
                    raise RuntimeError(f"{label}：{exc}") from exc
                self._log("warning", f"{label}：封魔杀活动弹层关闭失败，继续尝试场景图恢复：{exc}")
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
            if recovered and scene_id == 69 and self._daily_text_is_daily_list(text):
                return 69
        world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
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
                    if scene_id == 69 and self._daily_text_is_daily_list(text):
                        return 69
                    if scene_id == 34 or self._daily_assistant_text_is_world_like(text):
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
                if scene_after == 69 and self._daily_text_is_daily_list(text_after):
                    return 69
                if scene_after == 34 or self._daily_assistant_text_is_world_like(text_after):
                    scene_id, frame, text, world_like = scene_after, frame_after, text_after, True
                else:
                    raise RuntimeError(
                        f"恢复后仍未确认日常/世界，当前 "
                        f"{'#' + str(scene_after) if scene_after is not None else 'unknown'} {score_after:.0f}% "
                        f"OCR={text_after[:120]}"
                    )
            except Exception as exc:
                raise RuntimeError(f"{label}：当前不在可识别的世界或日常页，且无法通过场景图恢复到 #69：{exc}") from exc
        if world_like and (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=label)):
            start = time.monotonic()
            while True:
                self._raise_if_stopped(stop_event)
                yield BehaviorTreeStatus.RUNNING
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 69 and self._daily_text_is_daily_list(text):
                    return 69
                if scene_id == 34 or self._daily_assistant_text_is_world_like(text):
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
                if scene_after == 69 and self._daily_text_is_daily_list(text_after):
                    return 69
                last_error = RuntimeError(
                    f"{label}：跳转后未确认进入日常列表，当前 "
                    f"{'#' + str(scene_after) if scene_after is not None else 'unknown'} {score_after:.0f}% "
                    f"OCR={text_after[:120]}"
                )
                if attempt > 0:
                    break
                if scene_after == 34 or self._daily_assistant_text_is_world_like(text_after):
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
        runtime: FanxiuRuntime,
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
            if probe_scene == 69 and self._daily_text_is_daily_list(probe_text):
                return False, probe_scene, probe_frame, probe_text
            if probe_scene == 34 or self._daily_assistant_text_is_world_like(probe_text):
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
            if scene_id == 34 or self._daily_lingta_text_is_world_like(text):
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
        if scene_id in {289, 86} or self._leave_scene_confirm_text(text):
            confirm_id = int(scene_id) if scene_id in {289, 86} else 86
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
        if scene_id == 34 or self._daily_assistant_text_is_world_like(text):
            yield from self._close_world_reward_tip_stack_if_present(ctx, runtime, stop_event, label=label)
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
            reverse_scrolls=self._payload_int(
                payload,
                "reverse_scrolls",
                "lingta_reverse_scrolls",
                "max_scrolls",
                "lingta_max_scrolls",
                default=10,
            ),
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
            reverse_scrolls=self._payload_int(
                payload,
                "reverse_scrolls",
                "lingta_reverse_scrolls",
                "max_scrolls",
                "lingta_max_scrolls",
                default=10,
            ),
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
        match = re.search(r"(?:挑战\s*仙缘|仙缘人物).*?(\d{1,2})/(\d{1,2})", normalized)
        if match:
            current = int(match.group(1))
            total = int(match.group(2))
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
        fractions = re.findall(r"(\d{1,2})/(\d{1,2})", row_text)
        if not fractions:
            return None
        current, total = fractions[-1]
        total_int = int(total)
        current_int = int(current)
        if total_int > 0 and current_int > total_int and len(current) >= 2:
            suffix_int = int(current[-1])
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
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_xianyuan",
            label="日常_挑战仙缘",
        )
        self._log("success", f"日常_挑战仙缘：{message}，下次 {next_time}")
        return next_time

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

    def _execute_daily_xianyuan_duel_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙缘_斗法资产树路径，无法执行作业")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([308, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id not in {308, 69}:
            scene_id = yield from self._enter_daily_from_world_like(ctx, runtime, stop_event, frame, scene_id, text, label="仙缘_斗法")
        if scene_id not in {308, 69}:
            raise RuntimeError("仙缘_斗法：未能进入 #69 日常列表")
        if scene_id == 69:
            status = yield from runtime.open_daily_entry(
                label="仙缘_斗法",
                title_pattern=r"斗\s*法",
                progress_can_mark_done=False,
                max_scrolls=int(payload.get("max_scrolls") or 30),
                reverse_scrolls=int(payload.get("reverse_scrolls") or 8),
            )
            if status == "not_found":
                self._record_daily_entry_not_found_retry(
                    payload,
                    task_id="legacy-daily-xianyuan",
                    task_type="daily_xianyuan_duel",
                    label="仙缘_斗法",
                    entry_label="斗法",
                )
                return "skipped"
        if not bool(payload.get("skip_purchase")):
            yield from self._prepare_daily_xianyuan_duel_purchases(runtime, payload)
        max_runs = int(payload.get("max_runs") or 7)
        for index in range(max_runs):
            self._log("action", f"仙缘_斗法：斗法挑战 {index + 1}/{max_runs}")
            yield from runtime.wait_click_then_view(308, "挑战1", 309)
            yield from self._optimize_daily_xianyuan_duel_formation(runtime, payload)
            view_after_start = yield from runtime.wait_click_then_view(309, "开始挑战", [310, 308])
            if int(getattr(view_after_start, "id", 0) or 0) == 310:
                yield from runtime.wait_click_then_view(310, "点击继续", [308, 316])
        self._log("success", f"仙缘_斗法：已完成斗法挑战 {max_runs} 次")
        return "success"

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
        raid_scenes = {319, 320, 321, 322, 323, 324, 331}
        scene_id, _score, frame = runtime.current_scene([330, *sorted(raid_scenes), 69, 34, 20], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 330:
            self._log("action", "日常_奇袭魔界：起点检测到 #330 前置奖励确认，点击「确定」后继续等待 #319")
            yield from runtime.wait_click(330, "确定")
            yield from runtime.wait_view(319, label="日常_奇袭魔界：等待 #330 后的奇袭魔界 #319")
            scene_id = 319
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
                reverse_scrolls=int(payload.get("reverse_scrolls") or 8),
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
                    self._log("action", "日常_奇袭魔界：检测到 #330 前置奖励确认，点击「确定」后继续等待 #319")
                    yield from runtime.wait_click(330, "确定")
                    yield from runtime.wait_view(319, label="日常_奇袭魔界：等待 #330 后的奇袭魔界 #319")
            except TimeoutError as exc:
                yield from self._handle_daily_mojie_raid_open_blocker_placeholder(runtime, payload)
                raise RuntimeError("日常_奇袭魔界：入口点击后未到达 #319，疑似遇到未实现的特殊弹窗") from exc
            scene_id = 319
        if scene_id == 319:
            self._log("success", "日常_奇袭魔界：已到达 #319")
            numbers, text = runtime.ocr_numbers_in_shapes(
                319,
                ("剩余次数",),
                padding=int(payload.get("mojie_raid_remaining_padding") or 16),
            )
            if not numbers:
                fallback_remaining = self._daily_mojie_raid_remaining_ocr_fallback(text)
                if fallback_remaining is None:
                    raise RuntimeError(f"日常_奇袭魔界：未能读取 #319「剩余次数」，OCR={text[:120]}")
                numbers = [fallback_remaining]
            remaining = int(numbers[0])
            self._log("detail", f"日常_奇袭魔界：剩余次数 {remaining}，OCR={text[:80]}")
            if remaining <= 0:
                next_time = self._next_mojie_raid_week_start_time_text()
                self._record_scheduler_task_discovered_next_time(
                    str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid"),
                    next_time,
                    task_type="daily_mojie_raid",
                    label="日常_奇袭魔界",
                    last_result="success",
                )
                self._log("success", f"日常_奇袭魔界：剩余次数为 0，本周已完成，下次 {next_time}")
                yield from runtime.wait_click(319, "返回")
                return "success"
            yield from runtime.wait_click_then_view(319, "参与进攻", 320)
            scene_id = 320
        else:
            self._log("detail", f"日常_奇袭魔界：从 #{scene_id} 恢复后续流程")
        if scene_id == 320:
            scene_id = yield from self._click_daily_mojie_raid_top_attack_target(runtime, payload)
        if scene_id == 321:
            yield from runtime.wait_click_then_view(321, "创建队伍", 322)
            scene_id = 322
        if scene_id == 322:
            # 业务语义：奇袭魔界的建队额度由整个同盟共享，并非每个玩家各有
            # 3 个名额；每个触发周期同盟最多只能建立 3 支队伍。作业触发较晚
            # 时，盟友可能已经把额度用完，因此 #322 显示 3/3 是“本周期同盟
            # 建队名额已满”，不是「确定」按钮失效，也不应靠重复点击恢复。
            # 此时本轮应跳过并回到稳定世界，等待下一调度周期重新尝试。
            team_numbers, team_text = runtime.ocr_numbers_in_shapes(
                322,
                ("队伍数",),
                padding=int(payload.get("mojie_raid_team_count_padding") or 12),
            )
            team_count: int | None = None
            team_limit: int | None = None
            if len(team_numbers) >= 2:
                team_count, team_limit = int(team_numbers[0]), int(team_numbers[1])
            else:
                match = re.search(r"(\d+)\s*[/／]\s*(\d+)", str(team_text or ""))
                if match:
                    team_count, team_limit = int(match.group(1)), int(match.group(2))
            self._log(
                "detail",
                f"日常_奇袭魔界：#322 队伍数 {team_count if team_count is not None else '?'}"
                f"/{team_limit if team_limit is not None else '?'}，OCR={str(team_text or '')[:80]}",
            )
            if team_count is not None and team_limit is not None and team_limit > 0 and team_count >= team_limit:
                scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-mojie-raid")
                next_time = (
                    self._scheduler_task_next_time_from_schedule(scheduler_task_id, "daily_mojie_raid")
                    or (_runtime_runner._now() + timedelta(seconds=600)).strftime("%Y-%m-%d %H:%M:%S")
                )
                self._record_scheduler_task_discovered_retry_after(
                    scheduler_task_id,
                    next_time,
                    task_type="daily_mojie_raid",
                    label="日常_奇袭魔界",
                    last_result="skipped",
                )
                self._log("skip", f"日常_奇袭魔界：#322 队伍数已满 {team_count}/{team_limit}，本轮不再创建，下次 {next_time}")
                return "skipped"
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
            yield from runtime.wait_click_then_view(324, "返回", 331)
            scene_id = 331
        if scene_id == 331:
            yield from runtime.click_shape_center_then_view(331, "返回", 320)
            yield from runtime.wait_click(320, "返回")
            yield from runtime.wait_click_then_view(319, "返回", 34)
        return "success"

    def _click_daily_mojie_raid_top_attack_target(
        self,
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
    ):
        target_shape = str(payload.get("mojie_raid_target_shape") or "").strip()
        match_timeout = float(
            payload.get("mojie_raid_target_match_timeout")
            or getattr(runtime, "default_wait_click_timeout", self._daily_default_wait_condition_timeout)
        )
        if target_shape:
            self._log("action", f"日常_奇袭魔界：点击 #320「{target_shape}」")
            yield from runtime.wait_click(
                320,
                target_shape,
                timeout=match_timeout,
            )
        else:
            yield from runtime.wait_view(320, timeout=match_timeout, label="日常_奇袭魔界：等待 #320 据点列表")
            frame = runtime.cur_frame(update=True)
            candidates = self._daily_mojie_raid_attack_count_candidates(runtime, frame, payload)
            if not candidates:
                raise RuntimeError("日常_奇袭魔界：#320 未识别到可进攻队伍数")
            click_x, click_y, text = candidates[0]
            self._log("action", f"日常_奇袭魔界：点击队伍数「{text}」上方据点")
            runtime.click_frame_point(320, click_x, click_y)
        yield from runtime.wait_action_settle(float(payload.get("mojie_raid_target_click_settle_seconds") or 1.5))
        waited = yield from runtime.wait_view(
            321,
            timeout=float(payload.get("mojie_raid_target_wait_timeout") or self._daily_default_wait_condition_timeout),
            label="日常_奇袭魔界：点击 #320 顶部据点计数后等待 #321",
        )
        return int(getattr(waited, "id", waited) or 0)

    def _daily_mojie_raid_attack_count_candidates(
        self,
        runtime: FanxiuRuntimeSession,
        frame: str,
        payload: dict[str, Any],
    ) -> list[tuple[float, float, str]]:
        view = runtime.view(320)
        width, height = runtime.runner._frame_size(view.raw)
        min_y = height * float(payload.get("mojie_raid_target_min_y_ratio") or 0.08)
        max_y = height * float(payload.get("mojie_raid_target_max_y_ratio") or 0.78)
        target_offset_x, target_offset_y = self._daily_mojie_raid_annotated_target_offset(
            runtime,
            view,
            height=height,
            payload=payload,
        )
        candidates: list[tuple[float, float, str]] = []
        tokens = runtime.ocr_tokens(frame)
        for fragment in group_ocr_tokens(tokens):
            text = str(fragment.get("text") or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
            fragment_tokens = query_spatial_ocr(tokens, fragment)["tokens"]
            for match in re.finditer(r"([0-9]+)\s*/\s*30", text):
                if int(match.group(1)) <= 0:
                    continue
                count_box = locate_text_box(fragment_tokens, match.group(0))
                if count_box is None:
                    continue
                x = float(count_box.get("x") or 0) + float(count_box.get("w") or 0) / 2
                count_y = float(count_box.get("y") or 0) + float(count_box.get("h") or 0) / 2
                if not (0 <= x <= width and min_y <= count_y <= max_y):
                    continue
                click_x = max(0.0, min(width, x + target_offset_x))
                click_y = max(min_y, min(height, count_y + target_offset_y))
                candidates.append((click_x, click_y, match.group(0)))
        return sorted(candidates, key=lambda item: (item[1], item[0]))

    def _daily_mojie_raid_annotated_target_offset(
        self,
        runtime: FanxiuRuntimeSession,
        view: View,
        *,
        height: float,
        payload: dict[str, Any],
    ) -> tuple[float, float]:
        """Return the annotated Y offset from 队伍数 center to 修罗 center."""
        try:
            target_shape = runtime.shape(view, "检索区域/修罗")
            count_shape = runtime.shape(view, "检索区域/队伍数")
            _target_x, target_y = ActionPlanner().shape_center(view.raw, target_shape.raw)
            _count_x, count_y = ActionPlanner().shape_center(view.raw, count_shape.raw)
            return 0.0, float(target_y - count_y)
        except (RuntimeError, AttributeError, KeyError, TypeError):
            fallback_y = -height * float(payload.get("mojie_raid_target_icon_offset_ratio") or 0.14)
            return 0.0, fallback_y

    def _daily_mojie_raid_remaining_ocr_fallback(self, text: str) -> int | None:
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        if not re.search(r"(?:剩余次数|进攻次数)", normalized):
            return None
        match = re.search(r"(?:剩余次数|进攻次数)\s*[:：]?\s*([B8])(?:\D|$)", normalized, re.IGNORECASE)
        if not match:
            return None
        token = str(match.group(1) or "").upper()
        return 8 if token == "B" else int(token)

    def _next_mojie_raid_week_start_time_text(self) -> str:
        now = _runtime_runner._now()
        days_until_next_monday = (7 - now.weekday()) % 7
        if days_until_next_monday == 0:
            days_until_next_monday = 7
        next_monday = now + timedelta(days=days_until_next_monday)
        return next_monday.replace(hour=13, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

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
        if scene_id is None and self._is_daily_weekly_dungeon_tiangong_page_text(text):
            scene_id = 326
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
                reverse_scrolls=int(payload.get("reverse_scrolls") or 8),
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
        self._log("success", "日常_周本：战斗结束，已回到 #34")
        return "success"

    def _daily_weekly_dungeon_next_time_text(self, payload: dict[str, Any]) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "daily-weekly-dungeon")
        task_type = "daily_weekly_dungeon"
        for task in _read_data_annotation_scheduler_tasks():
            if str(task.get("id") or "") == scheduler_task_id or str(task.get("task_type") or "") == task_type:
                next_time = next_data_annotation_scheduler_time(task, _now())
                if next_time:
                    return next_time
        now = _now()
        days_until_next_monday = (7 - now.weekday()) % 7 or 7
        next_monday = now + timedelta(days=days_until_next_monday)
        return next_monday.replace(hour=5, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def _record_daily_weekly_dungeon_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._daily_weekly_dungeon_next_time_text(payload)
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "daily-weekly-dungeon"),
            next_time,
            task_type="daily_weekly_dungeon",
            label="日常_周本",
            last_result="success",
        )
        self._log("success", f"日常_周本：{message}，下次 {next_time}")
        return next_time

    def _open_daily_weekly_dungeon_tiangong_view(
        self,
        runtime: FanxiuRuntimeSession,
        payload: dict[str, Any],
    ):
        max_attempts = max(1, int(payload.get("weekly_tiangong_max_attempts") or 3))
        wait_timeout = float(payload.get("weekly_tiangong_wait_timeout") or 8.0)
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
                if scene_id is None and self._is_daily_weekly_dungeon_tiangong_page_text(text):
                    return 326
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

    def _is_daily_weekly_dungeon_tiangong_page_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(str(text or "")))
        return "玉霄天宫" in compact and "本周剩余奖励次数" in compact and "挑战" in compact

    def _daily_weekly_dungeon_remaining_count(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(str(text or "")).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("O", "0").replace("o", "0")
        compact = re.sub(r"\s+", "", normalized)
        if "本周剩余奖励次数" not in compact:
            return None
        match = re.search(r"本周剩余奖励次数[:：]?([0-9])(?:/([0-9]))?", compact)
        if not match:
            match = re.search(r"剩余奖励次数[:：]?([0-9])(?:/([0-9]))?", compact)
        if not match:
            match = re.search(r"次数[:：]?([0-9])(?:/([0-9]))?", compact)
        if not match:
            return None
        return int(match.group(1))

    def _prepare_daily_xianyuan_duel_purchases(self, runtime: FanxiuRuntimeSession, payload: dict[str, Any]):
        yield from runtime.wait_click_then_view(308, "购买", 311)
        max_attempts = int(payload.get("purchase_max_attempts") or 6)
        for _index in range(max_attempts):
            for _retry in range(3):
                numbers, text = runtime.ocr_numbers_in_shapes(311, ("价格",), padding=16)
                if numbers:
                    break
                yield from runtime.wait_action_settle(0.8)
            if not numbers:
                continue
            price = numbers[0]
            if price >= 300:
                yield from runtime.wait_click_then_view(311, "返回", 308)
                return
            self._log("action", f"仙缘_斗法：购买斗法次数，价格 {price}")
            yield from runtime.wait_click(311, "购买")
            yield from runtime.wait_action_settle(1.0)
        raise RuntimeError(f"仙缘_斗法：购买页价格识别失败或未达到停止价格，最后识别文本：{text if 'text' in locals() else ''}")

    def _optimize_daily_xianyuan_duel_formation(self, runtime: FanxiuRuntimeSession, payload: dict[str, Any]):
        if bool(payload.get("skip_formation_optimize")):
            return
        start_ts = time.monotonic()
        state = self._read_daily_xianyuan_duel_formation_state(runtime)
        max_probe_swaps = int(payload.get("formation_probe_max_swaps") or 2)
        settle_seconds = float(payload.get("formation_drag_settle_seconds") or 1.8)
        drag_duration = float(payload.get("formation_drag_duration_seconds") or 2.0)
        probe_actions = 0

        # 不要把克制结果改成“识别双方职业后自行计算”。
        # 对方职业位于易遮挡区域，公告喇叭等浮层会挡住内容；且可能出现体/魔/剑/法之外的特殊职业。
        # #309 上游戏已经渲染出的克制结果三态，才是本界面的权威输入。
        # 探测拖拽本身也是有效换阵操作，拖完必须重新读当前阵容；如果已经最优，不要为了“还原探测”而回滚。
        for _ in range(max_probe_swaps):
            enemy_candidates = infer_enemy_candidate_order(state["my_order"], state["states"])
            if all(len(candidates) == 1 for candidates in enemy_candidates):
                break
            best = best_order_for_enemy_candidates(
                state["my_order"],
                enemy_candidates,
                current_order=state["my_order"],
                decay=float(payload.get("formation_decay") or 0.5),
            )
            swaps = plan_swaps(state["my_order"], best["order"])
            if not swaps:
                break
            start_slot, end_slot = swaps[0]
            runtime.drag_shape_to_shape(309, f"拖拽锚点{start_slot}", f"拖拽锚点{end_slot}", duration=drag_duration, frame_data_url=state["frame"])
            probe_actions += 1
            yield from runtime.wait_action_settle(settle_seconds)
            state = self._read_daily_xianyuan_duel_formation_state(runtime)

        enemy_candidates = infer_enemy_candidate_order(state["my_order"], state["states"])
        best = best_order_for_enemy_candidates(
            state["my_order"],
            enemy_candidates,
            current_order=state["my_order"],
            decay=float(payload.get("formation_decay") or 0.5),
        )
        final_swaps = plan_swaps(state["my_order"], best["order"])
        max_final_swaps = int(payload.get("formation_final_max_swaps") or 4)
        for start_slot, end_slot in final_swaps[:max_final_swaps]:
            runtime.drag_shape_to_shape(309, f"拖拽锚点{start_slot}", f"拖拽锚点{end_slot}", duration=drag_duration, frame_data_url=state["frame"])
            yield from runtime.wait_action_settle(settle_seconds)
            state = self._read_daily_xianyuan_duel_formation_state(runtime)
        elapsed = time.monotonic() - start_ts
        self._log(
            "action",
            "仙缘_斗法：阵容优化 "
            f"{'/'.join(state['my_order'])}，克制={state['states']}，"
            f"探测{probe_actions}次，调整{min(len(final_swaps), max_final_swaps)}次，耗时{elapsed:.1f}s",
        )

    def _read_daily_xianyuan_duel_formation_state(self, runtime: FanxiuRuntimeSession) -> dict[str, Any]:
        from PIL import Image, ImageChops, ImageStat

        scene_id, score, frame = runtime.current_scene([309], update=True)
        if scene_id != 309:
            raise RuntimeError(f"仙缘_斗法：阵容优化要求当前为 #309，实际为 #{scene_id or 'unknown'} {score:.0f}%")
        image309 = runtime.view(309).raw
        entry = runtime.ctx.get("entry") if isinstance(runtime.ctx, dict) else None
        entry_id = str(getattr(entry, "entry_id", "") or "")
        filename = str(image309.get("filename") or "")
        if not entry_id or not filename:
            raise RuntimeError("仙缘_斗法：缺少 #309 参考图，无法识别阵容")
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
            raise RuntimeError("仙缘_斗法：#309 缺少职业或克制三态标注")

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

    def _daily_assistant_text_is_world_like(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return self._daily_lingta_text_is_world_like(text) or bool(
            re.search(r"角色.*装备.*功法书", compact)
            and ("修为" in compact or "战斗" in compact or "邮件" in compact or "邮常" in compact)
        )

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
        fractions = re.findall(r"(\d{1,2})/(\d{1,2})", row_text)
        if not fractions:
            return None
        current, total = fractions[-1]
        total_int = int(total)
        current_int = int(current)
        if total_int > 0 and current_int > total_int and len(current) >= 2:
            suffix_int = int(current[-1])
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
        text = re.sub(r"\d{1,2}\s*/\s*\d{1,2}", " ", text)
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
            if re.search(r"\d{1,2}\s*/\s*\d{1,2}", text):
                cy = float(line.get("_cy") or 0)
                if all(abs(cy - existing) > 45.0 for existing in progress_centers):
                    progress_centers.append(cy)

        rows: list[dict[str, Any]] = []
        for center in sorted(progress_centers):
            fragments = [
                str(line.get("_text") or "")
                for line in visible_lines
                if -float(y_tolerance) <= float(line.get("_cy") or 0) - center <= 45.0
            ]
            row_text = "".join(fragments)
            progress = self._daily_task_row_progress(visible_lines, center, y_tolerance=y_tolerance)
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

        view69 = runtime.view(69)
        list_shape = runtime.shape(view69, "滚动窗口")
        max_scrolls = self._payload_int(payload, "max_scrolls", default=12)
        for index in range(max_scrolls):
            with self._lock:
                self._set_status_locked("running", f"日常_复核：回滚到列表顶部 {index + 1}/{max_scrolls}", phase="daily_audit_seek_top", current_scene=69)
            changed = yield from runtime.scroll_shape_content(view69, list_shape, direction="up")
            if not changed:
                break

        rows: list[dict[str, Any]] = []
        for index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked("running", f"日常_复核：读取日常列表 {index + 1}/{max_scrolls + 1}", phase="daily_audit_scan", current_scene=69)
            frame = runtime.cur_frame(update=True)
            lines = self._cached_ocr_fragments(ctx, frame)
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

    def _daily_text_is_daily_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if (
            "日常" in compact
            and "活跃度" in compact
            and ("活动报名" in compact or "周常" in compact or "奖励找回" in compact)
        ):
            return True
        daily_row_markers = (
            "完成双人修炼",
            "双人修炼",
            "双修",
            "副本探险",
            "每日副本",
            "小助手",
            "完成修仙传游历",
            "击败首领",
            "活动报名",
            "混沌灵塔",
            "淬剑试炼",
            "灵祖",
            "挑战仙缘",
            "论道",
        )
        return bool(any(marker in compact for marker in daily_row_markers) and re.search(r"\d+/\d+", compact))

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
        if scene_id == 69 and self._daily_text_is_daily_list(text):
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
            reverse_scrolls=self._payload_int(payload, "reverse_scrolls", "max_scrolls", default=max_scrolls),
            initial_checks=initial_checks,
        ))

    def _record_daily_entry_done(self, payload: dict[str, Any], *, task_id: str, task_type: str, label: str, message: str) -> str:
        scheduler_task_id = str(payload.get("__scheduler_task_id") or task_id)
        next_time = (
            self._scheduler_task_next_time_from_schedule(scheduler_task_id, task_type)
            or self._next_daily_boss_reset_time_text()
        )
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type=task_type,
            label=label,
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
    ) -> str:
        next_time = (_runtime_runner._now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_retry_after(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
            task_type=task_type,
            label=label,
            last_result="skipped",
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
            f"{task_label}：已点击 #69 入口，但后续业务状态机尚未迁移；"
            f"当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%，OCR={after_text[:120]}。"
            f"{missing_assets_message}"
        )

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
        now = _runtime_runner._now()
        if lundao_safety_threshold(now) is None:
            next_time = next_lundao_daily_trigger(now).strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason="当前不在 15:55-22:00 运行窗口")
            return "success"

        scene_id, _score, frame = runtime.current_scene([69, 34, 296, 297, 298, 371, 372, 373, 375, 295, 329, 301, 302, 303, 304, 391, 52, 53, 54], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 54 or self._daily_lundao_text_is_exit_confirm(text):
            return (yield from self._confirm_daily_lundao_exit_to_world(runtime))
        if self._daily_lundao_text_is_reward(text):
            scene_id = 52
        if scene_id in {297, 298, 371, 372, 373, 375, 295}:
            return (yield from self._run_daily_lundao_seat_and_leave(runtime, stop_event, payload=payload))
        if scene_id in {329, 301, 302, 303, 52, 53}:
            return (yield from self._complete_daily_lundao_seat_and_leave(runtime, stop_event, scene_id))
        if scene_id == 391:
            entry_result = yield from self._dismiss_daily_lundao_kicked(runtime)
            scene_id = entry_result.get("scene_id")
        if scene_id == 304:
            return (yield from self._run_daily_lundao_dynamic_strategy(runtime, stop_event, payload))
        if scene_id == 296:
            return (yield from self._run_daily_lundao_dynamic_strategy(runtime, stop_event, payload))
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
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lundao",
                task_type="daily_lundao",
                label="论道_座位",
                entry_label="论道",
            )
            return "skipped"
        if entry_result["status"] == "kicked":
            entry_result = yield from self._dismiss_daily_lundao_kicked(runtime)
        scene_id = entry_result.get("scene_id")
        score = float(entry_result.get("score") or 0.0)
        if entry_result["status"] == "in_progress":
            return (yield from self._run_daily_lundao_dynamic_strategy(runtime, stop_event, payload))
        if entry_result["status"] == "dojo_selection":
            return (yield from self._run_daily_lundao_dynamic_strategy(runtime, stop_event, payload))
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
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "daily-lundao-seat"),
            next_time,
            task_type="daily_lundao",
            label="论道_座位",
            last_result="success",
        )
        self._log("success", f"论道_座位：{reason}，下次 {next_time}")
        return next_time

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

    def _click_daily_lundao_dojo(self, runtime: Any, target: str) -> None:
        order = ("至尊", "大罗", "三清", "御界")
        first = self._daily_lundao_first_visible_dojo(runtime)
        slot = order.index(target) - order.index(first) + 1
        slot_shapes = {1: "至尊道场", 2: "大罗道场", 3: "三清道场"}
        shape = slot_shapes.get(slot)
        if shape is None:
            raise RuntimeError(f"论道_座位：#296 当前首行为「{first}」，目标「{target}」不在可见三行，已停止")
        runtime.click_shape_center(296, shape)

    def _refresh_daily_lundao_packet_facts(self, *, reason: str, wait_seconds: float = 20.0) -> dict[str, Any]:
        started_at = time.perf_counter()
        start_result = start_fanxiu_packet_service()
        catch_up = request_fanxiu_packet_service_catch_up(reason=reason, wait_seconds=max(1.0, float(wait_seconds)))
        facts = read_current_lundao_facts()
        facts["packet_start"] = start_result
        facts["packet_catch_up"] = catch_up
        facts["elapsed_seconds"] = time.perf_counter() - started_at
        self._log(
            "info",
            f"论道_座位：抓包追平 {reason} 用时 {facts['elapsed_seconds']:.2f}s",
        )
        return facts

    def _daily_lundao_room_available_count(self, status: Mapping[str, Any], room_id: int) -> int | None:
        values = status.get("room_available_counts") if isinstance(status.get("room_available_counts"), Mapping) else {}
        value = values.get(str(room_id))
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _return_daily_lundao_to_selection(self, runtime: Any, scene_id: int) -> None:
        # #298 is the empty-seat variant of the same room list; its full-screen
        # reference has no duplicate return annotation, so use the shared #297
        # room-list return control already present at the same stable location.
        runtime.click_shape_center(297, "返回")
        yield from runtime.wait_scene(296, 304, timeout=15.0, label="论道_座位：返回道场选择")

    def _run_daily_lundao_room_action(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        opportunity: Mapping[str, Any],
    ) -> str:
        scene_id, score, _frame = runtime.current_scene([297, 298], update=True)
        action = str(opportunity.get("action") or "")
        if action == "empty" and scene_id == 298:
            scene_id, score = yield from self._run_daily_lundao_empty_seat_strategy(runtime)
        elif action == "kick" and scene_id == 297:
            target = opportunity.get("target") if isinstance(opportunity.get("target"), Mapping) else None
            if target is None:
                raise RuntimeError("论道_座位：策略要求踢人但没有合法目标，已停止")
            kick_result = yield from self._run_daily_lundao_kick_for_seat_strategy(
                runtime,
                stop_event,
                target_player=target,
            )
            scene_id = int(kick_result.get("scene_id") or 52)
            score = float(kick_result.get("score") or 0.0)
        else:
            raise RuntimeError(f"论道_座位：策略动作 {action!r} 与当前场景 #{scene_id} 不一致，已停止")
        return (yield from self._complete_daily_lundao_seat_and_leave(runtime, stop_event, scene_id, score))

    def _run_daily_lundao_dynamic_strategy(
        self,
        runtime: Any,
        stop_event: threading.Event,
        payload: Mapping[str, Any],
    ) -> str:
        now = _runtime_runner._now()
        # Opening Daluo is read-only and produces both SM_RoomList and the room
        # roster.  Capture once after that click; doing a separate catch-up on
        # the selection page doubled latency and could return before ingestion.
        initial = read_current_lundao_facts()
        baseline = initial.get("roster") if isinstance(initial.get("roster"), dict) else {}
        baseline_key = tuple((baseline.get("evidence") or {}).get("order_key") or ())
        self._click_daily_lundao_dojo(runtime, "大罗")
        yield from runtime.wait_scene(297, 298, timeout=15.0, label="论道_座位：等待大罗座位列表")
        refreshed = self._refresh_daily_lundao_packet_facts(reason="daily-lundao-daluo-roster", wait_seconds=120.0)
        status = refreshed.get("status") if isinstance(refreshed.get("status"), dict) else {}
        roster = refreshed.get("roster") if isinstance(refreshed.get("roster"), dict) else {}
        catch_up = refreshed.get("packet_catch_up") if isinstance(refreshed.get("packet_catch_up"), dict) else {}
        roster_key = tuple((roster.get("evidence") or {}).get("order_key") or ())
        if catch_up.get("status") != "completed":
            raise RuntimeError("论道_座位：抓包追平未在 120 秒内完成，已停止且未点击座位")
        if baseline_key and roster_key and roster_key <= baseline_key:
            raise RuntimeError("论道_座位：进入大罗后未获得更新的座位名单，已停止且未点击座位")
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
            next_time = (now + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason=f"大罗事实不足：{opportunity.get('reason')}")
            return "success"
        if decision.get("action") == "done":
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            next_time = decision["next_time"].strftime("%Y-%m-%d %H:%M:%S")
            self._record_daily_lundao_next_time(payload, next_time, reason=str(decision.get("reason")))
            return "success"
        if decision.get("action") == "seat_daluo":
            result = yield from self._run_daily_lundao_room_action(runtime, stop_event, opportunity=opportunity)
            after = self._refresh_daily_lundao_packet_facts(reason="daily-lundao-after-seat", wait_seconds=120.0)
            after_status = after.get("status") if isinstance(after.get("status"), dict) else {}
            after_strength = after_status.get("strength")
            next_at = next_lundao_daily_trigger(now) if after_strength is not None and int(after_strength) <= 0 else next_lundao_recheck(now)
            self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="已完成大罗入座")
            return result

        current_room = int(status.get("room_id") or 0)
        yield from self._return_daily_lundao_to_selection(runtime, 297)
        if current_room == LUNDAO_SANQING_ROOM_ID:
            yield from runtime.goto_view(34)
            next_at = decision.get("next_time") if isinstance(decision.get("next_time"), datetime) else next_lundao_recheck(now)
            self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="大罗条件不足，保留三清")
            return "success"

        self._click_daily_lundao_dojo(runtime, "三清")
        yield from runtime.wait_scene(297, 298, timeout=15.0, label="论道_座位：等待三清座位列表")
        sanqing_facts = self._refresh_daily_lundao_packet_facts(reason="daily-lundao-sanqing-roster", wait_seconds=120.0)
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
        if not sanqing.get("actionable"):
            yield from self._return_daily_lundao_to_selection(runtime, 297)
            yield from runtime.goto_view(34)
            next_at = next_lundao_recheck(now, protect_end_time_ms=sanqing.get("earliest_protect_end_time"))
            self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="三清暂无合法座位")
            return "success"
        result = yield from self._run_daily_lundao_room_action(runtime, stop_event, opportunity=sanqing)
        after = self._refresh_daily_lundao_packet_facts(reason="daily-lundao-after-sanqing-seat", wait_seconds=120.0)
        after_status = after.get("status") if isinstance(after.get("status"), dict) else {}
        after_strength = after_status.get("strength")
        next_at = next_lundao_daily_trigger(now) if after_strength is not None and int(after_strength) <= 0 else next_lundao_recheck(now)
        self._record_daily_lundao_next_time(payload, next_at.strftime("%Y-%m-%d %H:%M:%S"), reason="已完成三清入座")
        return result

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
        scene_id, score, _frame = runtime.current_scene([297, 298, 371, 372, 373, 375, 295], update=True)
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
                retry_after = self._record_daily_lundao_retry(
                    dict(payload or {}),
                    message="大罗满座且没有可击败的非友军",
                )
                rejected = selection.get("rejected") if isinstance(selection.get("rejected"), dict) else {}
                self._log("skip", f"论道_座位：无可用目标，已返回 #34，{retry_after} 重试，排除={rejected}")
                return "skipped"
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
        elif scene_id == 295:
            dialogue_result = yield from self._advance_daily_lundao_kick_dialogue(runtime, start_scene=295)
            scene_id = int(dialogue_result.get("scene_id") or 52)
            score = float(dialogue_result.get("score") or 0.0)
        else:
            raise RuntimeError(
                f"论道_座位：抢座节点入口只接受 #297/#298/#371/#372/#373/#375/#295，当前 "
                f"#{scene_id if scene_id is not None else 'unknown'} {score:.0f}%"
            )
        return (yield from self._complete_daily_lundao_seat_and_leave(runtime, stop_event, scene_id, score))

    def _select_daily_lundao_kick_target(self) -> dict[str, Any]:
        """Refresh the current Daluo roster and apply the shared relation policy."""

        return refresh_and_select_lundao_kick_target()

    def _record_daily_lundao_retry(
        self,
        payload: dict[str, Any],
        *,
        message: str,
        seconds: int = 1800,
    ) -> str:
        retry_after = (_runtime_runner._now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_retry_after(
            str(payload.get("__scheduler_task_id") or "daily-lundao-seat"),
            retry_after,
            task_type="daily_lundao",
            label="论道_座位",
            last_result="skipped",
        )
        self._log("skip", f"论道_座位：{message}，{retry_after} 重试")
        return retry_after

    def _run_daily_lundao_kick_for_seat_strategy(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        target_player: Mapping[str, Any] | None = None,
    ):
        """#297 满座策略：只对上游明确指定的目标玩家执行同条目让座。

        `target_player` 是策略必需输入，由上游给出稳定玩家 ID 和/或精确玩家名；
        正式链路由前置抓包座位清单与选人策略生成该结构，稳定 ID/seat_id 是
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
                match_mode="contains",
            )
            if len(items) > 1:
                raise RuntimeError(f"论道_座位：#297[窗口] 内目标姓名「{target_name}」不唯一，已停止且未点击")
            if len(items) == 1:
                item = items[0]
                if not runtime.floating_item_is_fully_inside(item, "窗口"):
                    raise RuntimeError(f"论道_座位：目标「{target_name}」模板实例位于窗口裁剪边缘，已停止且未点击")
                if not runtime.floating_item_field_is_inside(item, "按钮", "窗口"):
                    raise RuntimeError(f"论道_座位：目标「{target_name}」预测按钮中心不在窗口内，已停止且未点击")
                protect_end_time = int(target.get("protect_end_time") or 0)
                if protect_end_time > int(time.time() * 1000):
                    raise RuntimeError(f"论道_座位：目标「{target_name}」仍在保护时间，已停止且未点击")
                role_id = int(target.get("role_id") or 0)
                if role_id:
                    latest = read_current_lundao_facts()
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
                        raise RuntimeError(f"论道_座位：目标「{target_name}」已不在原座位，已停止且未点击")
                runtime.click_floating_item_field(item, "按钮")
                # 点击条目右侧莲花[按钮]后会经过若干无业务语义的过渡画面。
                # 这些画面既不能算成功，也不应加入业务状态分支；只在有界时间内
                # 等待正式确认场景 #371，再点击其[请他让座]完成本步闭环。
                dialogue_result = yield from self._confirm_daily_lundao_kick_request(runtime)
                return {
                    "status": "request_sent",
                    "target": {"id": target_id, "name": target_name},
                    "scene_id": dialogue_result.get("scene_id"),
                    "score": dialogue_result.get("score"),
                }
            changed = yield from runtime.scroll_shape_content(297, "窗口")
            if not changed:
                break
        raise RuntimeError(f"论道_座位：向下滚动 #297[窗口] 后仍未找到目标「{target_name}」，已停止且未点击")

    def _confirm_daily_lundao_kick_request(self, runtime: Any, *, start_scene: int | None = None):
        """忽略过渡帧，依次在 #371 发起请离、在 #372 确认。"""
        if start_scene is None:
            yield from runtime.wait_scene(
                371,
                timeout=20.0,
                label="论道_座位：等待 #371 请他让座确认页",
            )
            start_scene = 371
        if start_scene == 371:
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
            yield from runtime.wait_scene(
                372,
                timeout=20.0,
                label="论道_座位：等待 #372 请离玩家确认框",
            )
            start_scene = 372
        if start_scene != 372:
            raise RuntimeError(f"论道_座位：请离确认节点只接受 #371/#372，当前 #{start_scene}")
        runtime.click_shape_center(372, "确定")
        # #372 后会进入若干段同坐标对话。无需把每一段都识别成 #373；
        # 先给首段画面稳定时间，随后只以正式终点 #375 是否出现作为循环条件。
        yield from runtime.wait_action_settle(1.5)
        return (yield from self._advance_daily_lundao_kick_dialogue(runtime))

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
                timeout=30.0,
                label="论道_座位：战前对话结束后等待战斗/胜利/入座",
            )
            if battle_scene == 52:
                return {
                    "status": "dialogue_finished",
                    "clicks": pre_battle_clicks,
                    "pre_battle_clicks": pre_battle_clicks,
                    "post_battle_clicks": 0,
                    "scene_id": 52,
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
                label="论道_座位：等待战斗结束后的对话/#52/#375/#295",
            )
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
                186,
                timeout=30.0,
                label="论道_座位：关闭胜利浮层后等待战后对话/入座",
            )
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
                186,
                timeout=30.0,
                label="论道_座位：战后对话结束后等待入座",
            )
        if after_battle_scene == 52:
            return {
                "status": "dialogue_finished",
                "clicks": pre_battle_clicks + post_battle_clicks,
                "pre_battle_clicks": pre_battle_clicks,
                "post_battle_clicks": post_battle_clicks,
                "scene_id": 52,
                "score": 100.0,
            }
        scene_id, score, _frame = runtime.current_scene([329, 301, 302, 303, 186], update=True)
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

    def _run_daily_lundao_empty_seat_strategy(self, runtime: Any):
        """#298 空位策略；复用既有入座及后续确认落点，不复制收尾。"""
        yield from runtime.wait_click_then_view(298, "入座", [329, 301, 302, 303], timeout=18.0)
        scene_id, score, _frame = runtime.current_scene([329, 301, 302, 303], update=True)
        return scene_id, score

    def _complete_daily_lundao_seat_and_leave(
        self,
        runtime: Any,
        stop_event: threading.Event,
        scene_id: int | None,
        score: float = 0.0,
    ) -> str:
        """两种抢座策略共用的确认入座、结果处理、离场和闭环收尾。"""
        if scene_id == 329:
            yield from runtime.wait_click_then_view(329, "确认", [301, 302, 303, 52, 53, 186], settle_seconds=1.5, timeout=20.0)
            scene_id, score, _frame = runtime.current_scene([301, 302, 303, 52, 53, 186], update=True)
        if scene_id in {301, 302}:
            scene_id, score = yield from self._advance_daily_lundao_seat_confirmation(runtime, stop_event, scene_id)
        if scene_id == 186:
            frame = runtime.cur_frame(update=True)
            if self._daily_lundao_text_is_seated(runtime.ocr_text(frame)):
                yield from self._leave_daily_lundao_seated_for_daily_entry(runtime, 186)
                self._log("success", "论道_座位：已进入道场听道中并退出回世界")
                return "success"
        if scene_id == 303:
            yield from runtime.wait_click_then_view(
                303,
                "对话",
                [373, 52, 53, 329, 301, 302, 186],
                settle_seconds=1.5,
                timeout=20.0,
            )
            scene_id, score, _frame = runtime.current_scene([373, 52, 53, 329, 301, 302, 186], update=True)
            if scene_id == 373:
                yield from runtime.advance_dialogue(
                    373,
                    "聊天按钮",
                    label="论道_座位：推进战后对话",
                )
                scene_id = yield from runtime.wait_scene(
                    52,
                    53,
                    329,
                    301,
                    302,
                    303,
                    186,
                    timeout=30.0,
                    label="论道_座位：战后对话结束后等待入座",
                )
                scene_id, score, _frame = runtime.current_scene([52, 53, 329, 301, 302, 303, 186], update=True)
        if scene_id == 52:
            yield from runtime.wait_click_then_view(52, "确认", wait_leave=True)
            scene_id, score, frame_after = runtime.current_scene([186, 53, 69, 34, 85, 52], update=True)
            text_after = runtime.ocr_text(frame_after)
            if self._daily_lundao_text_is_seated(text_after):
                yield from self._leave_daily_lundao_seated_for_daily_entry(runtime, 186)
                self._log("success", "论道_座位：已确认听道收益并退出回世界")
                return "success"
        if scene_id in {69, 34}:
            self._log("success", f"论道_座位：已确认听道收益并返回 #{scene_id}")
            return "success"
        if scene_id == 53:
            yield from self._leave_daily_lundao_seated_for_daily_entry(runtime, 53)
            self._log("success", "论道_座位：已完成听道并退出回世界")
            return "success"
        if scene_id == 54:
            return (yield from self._confirm_daily_lundao_exit_to_world(runtime))
        raise RuntimeError(f"论道_座位：抢座或收尾落点尚未实现，当前 #{scene_id if scene_id is not None else 'unknown'} {score:.0f}%")

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
        return (
            ("闻道剩余时间" in compact and ("道场闻道收益" in compact or "累积获得" in compact))
            or ("闻道感悟" in compact and "剩余座位" in compact and "离开" in compact)
        )

    def _daily_lundao_text_is_exit_confirm(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return "是否要退出道场" in compact and "继续闻道" in compact

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
            and ("当前道场" in compact or "论道收益" in compact or "剩余时间" in compact)
        )

    def _daily_lundao_text_is_unrelated_runtime_page(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return (
            "一键执行" in compact
            or "游戏公告" in compact
            or "进入游戏" in compact
            or "服务器维护" in compact
            or "适龄提示" in compact
        )

    def _confirm_daily_lundao_exit_to_world(self, runtime: Any) -> str:
        yield from runtime.wait_click_then_view(54, "确认", [34, 69], settle_seconds=1.5, timeout=20.0)
        self._log("success", "论道_座位：已确认退出道场，神识分身继续闻道")
        return "success"

    def _leave_daily_lundao_seated_for_daily_entry(self, runtime: Any, scene_id: int | None):
        click_view = 53
        if scene_id == 186:
            click_view = 186
        runtime.click_shape_center(click_view, "离开")
        yield from runtime.wait_action_settle(1.5)
        next_scene_id, _score, frame = runtime.current_scene([54, 34, 69, 53, 59, 186], update=True)
        next_text = runtime.ocr_text(frame)
        if next_scene_id == 54 or self._daily_lundao_text_is_exit_confirm(next_text):
            yield from self._confirm_daily_lundao_exit_to_world(runtime)
            return "success"
        if next_scene_id in {34, 69}:
            return "success"
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
            if scene_id == 329:
                yield from runtime.wait_click_then_view(329, "确认", [301, 302, 303, 52, 53, 186], settle_seconds=1.5, timeout=20.0)
            if scene_id == 301:
                scene_id, score, frame = runtime.current_scene([301, 302, 303, 329, 52, 53, 186, 237, 18, 14, 69, 34], update=True)
                last_scene_id, last_score = scene_id, float(score)
                text = runtime.ocr_text(frame)
                if scene_id != 301:
                    continue
                if not self._daily_lundao_text_is_seat_choice_prompt(text):
                    if self._daily_lundao_text_is_unrelated_runtime_page(text):
                        raise RuntimeError(f"论道_座位：#301 入座确认疑似误判到非论道页面，已停止避免误点，OCR={text[:160]}")
                    raise RuntimeError(f"论道_座位：命中 #301 但 OCR 不像论道听道座位确认，已停止避免误点，OCR={text[:160]}")
                runtime.click_shape_center(301, "入座")
                yield from runtime.wait_action_settle(2.0)
            if scene_id == 302:
                scene_id, score, frame = runtime.current_scene([302, 301, 303, 329, 52, 53, 186, 237, 18, 14, 69, 34], update=True)
                last_scene_id, last_score = scene_id, float(score)
                text = runtime.ocr_text(frame)
                if scene_id != 302:
                    continue
                if not self._daily_lundao_text_is_seat_confirm_prompt(text):
                    if self._daily_lundao_text_is_unrelated_runtime_page(text):
                        raise RuntimeError(f"论道_座位：#302 入座确认疑似误判到非论道页面，已停止避免误点，OCR={text[:160]}")
                    raise RuntimeError(f"论道_座位：命中 #302 但 OCR 不像论道空位入座确认，已停止避免误点，OCR={text[:160]}")
                runtime.click_shape_center(302, "确定")
                yield from runtime.wait_action_settle(2.0)
            scene_id, score, _frame = runtime.current_scene([303, 301, 302, 329, 52, 53, 186, 237, 18, 14, 69, 34], update=True)
            last_scene_id, last_score = scene_id, float(score)
            if self._daily_lundao_text_is_seated(runtime.ocr_text(_frame)):
                return 186, float(score)
            if scene_id in {303, 52, 53}:
                return scene_id, float(score)
            if scene_id in {237, 18, 14}:
                raise RuntimeError(f"论道_座位：入座确认后落到非论道页面 #{scene_id}，已停止避免误点")
            if scene_id is None:
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
            self._scheduler_task_next_time_from_schedule("legacy-daily-dongtian", "daily_dongtian")
            or self._next_daily_boss_reset_time_text()
        )
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian"),
            next_time,
            task_type="daily_dongtian",
            label="洞天_领取",
        )
        self._log("success", f"洞天_领取：{message}，下次 {next_time}")
        return next_time

    def _daily_dongtian_sleep_window_next_time(self, now: datetime | None = None) -> str | None:
        now = now or _runtime_runner._now()
        clock = now.time()
        if not (clock >= time_cls(22, 0) or clock < time_cls(10, 0)):
            return None
        task = next(
            (
                item
                for item in _read_data_annotation_scheduler_tasks()
                if str(item.get("id") or "") == "legacy-daily-dongtian"
                or str(item.get("task_type") or "") == "daily_dongtian"
            ),
            None,
        )
        if not isinstance(task, dict):
            return None
        return _runtime_runner._next_data_annotation_scheduler_time(
            task,
            now.replace(hour=23, minute=59, second=59, microsecond=999999),
        )

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
        if start_scene_id == 279:
            yield from runtime.wait_click_then_view(
                279,
                "收益",
                284,
                label=f"{task_label}：点击 #279「收益」后等待 #284 领取页",
                settle_seconds=float(payload.get("dongtian_profit_settle_seconds") or 2.0),
            )
        elif start_scene_id == 284:
            self._log("detail", f"{task_label}：当前已在 #284 收益领取页，直接领取，不重复点击 #279「收益」")
        else:
            raise RuntimeError(f"{task_label}：领取收益前应在 #279/#284，实际 #{start_scene_id if start_scene_id is not None else 'unknown'}")
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
        yield from runtime.wait_click_then_view(
            279,
            "返回",
            34,
            label=f"{task_label}：点击 #279「返回」后等待 #34 世界",
            settle_seconds=float(payload.get("dongtian_return_settle_seconds") or 2.0),
        )
        scene_return, score_return, frame_return = runtime.current_scene([34], update=True)
        text_return = runtime.ocr_text(frame_return)
        self._log("success", f"{task_label}：已从 #279 返回 #34，当前 #{scene_return if scene_return is not None else 'unknown'} {score_return:.0f}%，OCR={text_return[:160]}")

    def _execute_daily_dongtian_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = {"max_scrolls": 24, "reverse_scrolls": 6, **dict(payload or {})}
        sleep_window_next_time = self._daily_dongtian_sleep_window_next_time()
        if sleep_window_next_time:
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian"),
                sleep_window_next_time,
                task_type="daily_dongtian",
                label="洞天_领取",
                last_result="skipped",
            )
            self._log("skip", f"洞天_领取：当前在 22:00-次日10:00不可操作窗口，本轮按完成处理，下次 {sleep_window_next_time}")
            return "skipped"
        outside_window_next_time = self._runtime_daily_window_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian"),
            "daily_dongtian",
        )
        if outside_window_next_time:
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian"),
                outside_window_next_time,
                task_type="daily_dongtian",
                label="洞天_领取",
                last_result="skipped",
            )
            self._log("skip", f"洞天_领取：当前不在运行窗口或触发时间内，下次 {outside_window_next_time}")
            return "skipped"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_洞天福地资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(279), dict):
            raise RuntimeError("缺少 #279「洞天福地」标注，无法确认洞天主页")

        task_label = "洞天_领取"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = runtime.current_scene([284, 279, 69, 34, 47], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 284:
            yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=284)
            self._record_daily_dongtian_done(payload, message="已领取洞天福地收益")
            with self._lock:
                self._set_status_locked("success", "洞天_领取：已领取洞天福地收益", phase="daily_dongtian_done", current_scene=279)
            return "success"
        if scene_id == 279 or self._daily_dongtian_text_is_home(text):
            yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=279)
            self._record_daily_dongtian_done(payload, message="已领取洞天福地收益")
            with self._lock:
                self._set_status_locked("success", "洞天_领取：已领取洞天福地收益", phase="daily_dongtian_done", current_scene=279)
            return "success"

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, _score, frame = runtime.current_scene([284, 279, 69, 34, 47], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 284:
                    yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=284)
                    self._record_daily_dongtian_done(payload, message="已领取洞天福地收益")
                    with self._lock:
                        self._set_status_locked("success", "洞天_领取：已领取洞天福地收益", phase="daily_dongtian_done", current_scene=279)
                    return "success"
                if scene_id == 279 or self._daily_dongtian_text_is_home(text):
                    yield from self._claim_daily_dongtian_profit(ctx, stop_event, payload, task_label=task_label, start_scene_id=279)
                    self._record_daily_dongtian_done(payload, message="已领取洞天福地收益")
                    with self._lock:
                        self._set_status_locked("success", "洞天_领取：已领取洞天福地收益", phase="daily_dongtian_done", current_scene=279)
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
        yield from self._claim_daily_dongtian_profit(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            start_scene_id=int(landing_scene_id),
        )
        self._record_daily_dongtian_done(payload, message="已从日常进入洞天福地并领取收益")
        with self._lock:
            self._set_status_locked("success", "洞天_领取：已进入洞天福地并领取收益", phase="daily_dongtian_done", current_scene=279)
        return "success"

    def _execute_daily_lingmai_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = {"max_scrolls": 30, "reverse_scrolls": 8, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少灵脉_座位资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(285), dict):
            raise RuntimeError("灵脉_座位：缺少 #285「造化灵脉」标注，无法确认入口后的场景锚点")

        task_label = "灵脉_座位"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, score, frame = runtime.current_scene([318, 305, 288, 286, 285, 69, 34], update=True)
        text = runtime.ocr_text(frame)
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

        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                scene_id, score, frame = runtime.current_scene([318, 305, 288, 286, 285, 69, 34], update=True)
                text = runtime.ocr_text(frame)
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
        payload = {"max_scrolls": 30, "reverse_scrolls": 8, **dict(payload or {})}
        outside_window_next_time = self._runtime_daily_window_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-lingmai-clear"),
            "daily_lingmai_clear",
        )
        if outside_window_next_time:
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-lingmai-clear"),
                outside_window_next_time,
                task_type="daily_lingmai_clear",
                label="灵脉_清体力",
                last_result="skipped",
            )
            self._log("skip", f"灵脉_清体力：当前不在运行窗口内，下次 {outside_window_next_time}")
            return "skipped"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少灵脉_清体力资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(285), dict):
            raise RuntimeError("灵脉_清体力：缺少 #285「造化灵脉」标注，无法确认入口后的场景锚点")

        task_label = "灵脉_清体力"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, score, frame = runtime.current_scene([315, 314, 313, 312, 285, 69, 34], update=True)
        text = runtime.ocr_text(frame)
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
        yield from runtime.wait_click_then_view(285, "探索", 313)
        return (yield from self._continue_daily_lingmai_clear_from_explore(runtime, payload, task_label=task_label))

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
                return "success"
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
        match = re.search(r"(\d{1,5})\s*[/／]\s*(\d{1,5})", str(text or ""))
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))

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
            return "success"
        self._log("success", f"{task_label}：#315 未出现或已自动消失，已回到 #285 造化灵脉")
        return "success"

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
                return "success"
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
                return "success"
        self._log("success", f"{task_label}：体力已清理并回到 #285 造化灵脉")
        return "success"

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
        恢复业务进度。新一轮正式作业仍必须从稳定起点 #34 整单执行。
        """
        payload = {"max_scrolls": 24, "reverse_scrolls": 6, **dict(payload or {})}
        if not bool(payload.get("ignore_schedule_window")):
            outside_window_next_time = self._runtime_daily_window_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian-clear"),
                "daily_dongtian_clear",
            )
            if outside_window_next_time:
                self._record_scheduler_task_discovered_next_time(
                    str(payload.get("__scheduler_task_id") or "legacy-daily-dongtian-clear"),
                    outside_window_next_time,
                    task_type="daily_dongtian_clear",
                    label="洞天_行动力",
                    last_result="skipped",
                )
                self._log("skip", f"洞天_行动力：当前不在 10:00-22:00 可操作窗口内，下次 {outside_window_next_time}")
                return "skipped"
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

    def _daily_dongtian_action_power(self, runtime: Any) -> tuple[int, str]:
        """读取当前洞天 HUD 的行动力，并返回数值和 OCR 原文。

        #279 与 #341 使用同一套固定 HUD。标注只在 #279 保存一份，本函数把
        #279「行动力」的区域投影到当前真实帧上识别，避免为每个洞天子场景
        重复标注同一控件；标注位置调整后，两处识别会同时生效。
        """
        frame = runtime.cur_frame(update=True)
        numbers, text = runtime.ocr_numbers_in_shapes(
            279,
            ("行动力",),
            padding=6,
            frame_data_url=frame,
        )
        if not numbers:
            # 单个“0”在窄 HUD 裁剪里偶尔会被 OCR 当成空白；全帧仍能稳定读到
            # “我的编队0”。只接受这个明确上下文，不能把任意空 OCR 猜成 0。
            full_text = _sanitize_ocr_text(runtime.ocr_text(frame))
            match = re.search(r"我的编队(\d{1,4})", full_text)
            if match:
                numbers = [int(match.group(1))]
                text = match.group(0)
        if not numbers:
            raise RuntimeError(f"洞天_行动力：行动力区域未识别到数字，OCR={text!r}")
        return int(numbers[0]), str(text or "")

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
        - 落在 #279 且行动力仍不少于 100：重新从最新抓包选择敌对地点；
        - 任一场景识别到行动力小于 100：清理完成，返回 ``success``；
        - 其它落点、OCR 无数字或超过安全轮数：失败并保留明确证据。

        ``max_action_power_rounds`` 只是防止识别异常导致无限循环的安全上限，
        不是业务次数；业务终止条件始终是行动力 ``< 100``。
        """
        rounds = 0
        max_rounds = max(1, int(payload.get("max_action_power_rounds") or 100))
        while rounds < max_rounds:
            scene_id, score, _frame = runtime.current_scene([341, 279], update=True)
            if scene_id not in {341, 279}:
                raise RuntimeError(f"洞天_行动力：循环只接受 #341/#279，当前 #{scene_id} {score:.0f}%")

            action_power, ocr_text = self._daily_dongtian_action_power(runtime)
            self._log("detail", f"洞天_行动力：当前 #{scene_id} 行动力={action_power}，OCR={ocr_text!r}")
            if action_power < 100:
                self._log("success", f"洞天_行动力：行动力已低于 100（当前 {action_power}），共挑战 {rounds} 次")
                return "success"

            if scene_id == 279:
                enemy_places = [str(item).strip() for item in payload.get("enemy_places") or [] if str(item).strip()]
                if not enemy_places:
                    enemy_places = self._daily_dongtian_enemy_places_from_latest_packet(payload)
                if not enemy_places:
                    raise RuntimeError("洞天_行动力：最新洞天抓包未解析出敌对地点")
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

        raise RuntimeError(f"洞天_行动力：挑战达到安全上限 {max_rounds} 次，行动力仍未低于 100")

    def _daily_dongtian_enemy_places_from_latest_packet(self, payload: dict[str, Any]) -> list[str]:
        own_union_id = int(payload.get("own_union_id") or 0)
        own_union_name = str(payload.get("own_union_name") or "").strip()

        from sqlmodel import Session

        from backend.core.fanxiu.packet.current_facts import catch_up_and_list_fanxiu_packet_decoded_records
        from backend.core.fanxiu.packet.decoded_store import list_fanxiu_packet_decoded_records
        from backend.db import engine

        with Session(engine) as session:
            facts = catch_up_and_list_fanxiu_packet_decoded_records(
                session,
                names=["SM_XianLvMineEnterSync"],
                pro_ids=[95102],
                since_seconds=max(30, int(payload.get("dongtian_packet_since_seconds") or 300)),
                limit=3,
                reason="daily-dongtian-clear",
                wait_seconds=max(1.0, min(30.0, float(payload.get("dongtian_packet_wait_seconds") or 15.0))),
            )
            if own_union_id <= 0 and not own_union_name:
                own_union_facts = list_fanxiu_packet_decoded_records(
                    session,
                    names=["SM_SelfCrossUnionInfo"],
                    limit=5,
                )
                for record in own_union_facts.get("records") or []:
                    record_payload = record.get("payload") if isinstance(record, dict) else {}
                    record_parsed = record_payload.get("parsed") if isinstance(record_payload, dict) else {}
                    cross_union = record_parsed.get("crossUnionVO") if isinstance(record_parsed, dict) else {}
                    if not isinstance(cross_union, dict):
                        continue
                    simple_union = cross_union.get("_super") if isinstance(cross_union.get("_super"), dict) else cross_union
                    own_union_id = int(simple_union.get("id") or 0)
                    own_union_name = str(simple_union.get("name") or "").strip()
                    if own_union_id > 0 or own_union_name:
                        payload.setdefault("own_union_id", own_union_id)
                        payload.setdefault("own_union_name", own_union_name)
                        self._log(
                            "detail",
                            f"洞天_行动力：从抓包识别我方联盟 {own_union_name or '-'}({own_union_id or '-'})",
                        )
                        break
        if own_union_id <= 0 and not own_union_name:
            raise RuntimeError("洞天_行动力：抓包中缺少自己的跨服联盟信息，无法判断敌我")
        records = ((facts.get("decoded_records") or {}).get("records") or []) if isinstance(facts, dict) else []
        if not records:
            raise RuntimeError("洞天_行动力：进入 #279 后未获得最近 5 分钟的 SM_XianLvMineEnterSync 抓包")

        parsed: dict[str, Any] = {}
        mines: list[Any] = []
        # 同一次进入洞天可能解码出时间戳相同的空壳记录和完整记录。记录按新到旧
        # 返回，不能让排在首位的空 mines 遮住同批后续完整事实。
        for record in records:
            if not isinstance(record, dict):
                continue
            candidate_payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            candidate_parsed = (candidate_payload.get("parsed") or {}) if isinstance(candidate_payload, dict) else {}
            candidate_mines = ((candidate_parsed.get("mines") or {}).get("items") or []) if isinstance(candidate_parsed, dict) else []
            if candidate_mines:
                parsed = candidate_parsed
                mines = candidate_mines
                break
        if not mines:
            raise RuntimeError("洞天_行动力：最近抓包记录均未包含有效 mines，拒绝把空壳记录当作无敌方地点")
        place_by_id = {index + 1: name for index, name in enumerate(_DONGTIAN_PLACE_ANCHORS)}
        enemies: list[str] = []
        union_summary: list[tuple[int, str, str]] = []
        for mine in mines:
            if not isinstance(mine, dict):
                continue
            mine_id = int(mine.get("id") or 0)
            union = mine.get("crossUnion") if isinstance(mine.get("crossUnion"), dict) else {}
            union_id = int(union.get("id") or 0)
            union_name = str(union.get("name") or "").strip()
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
            f"洞天_行动力：抓包解析敌对地点 {enemies}，已解码 {len(mines)}/{int(((parsed.get('mines') or {}).get('_count') or len(mines)) if isinstance(parsed, dict) else len(mines))} 个，unions={union_summary}",
        )
        return enemies

    def _daily_dongtian_validate_enemy_detail(
        self,
        runtime: Any,
        clicked_place: str,
        payload: dict[str, Any],
    ):
        yield from runtime.wait_view(341, label=f"洞天_行动力：核对地点「{clicked_place}」详情")
        detail_text = _sanitize_ocr_text(runtime.ocr_text(update=True))
        expected_place = re.sub(r"^\[(?:洞天|福地)\]", "", str(clicked_place)).strip()
        own_union_name = _sanitize_ocr_text(payload.get("own_union_name"))
        wrong_place = bool(expected_place and expected_place not in detail_text)
        own_place = bool(own_union_name and own_union_name in detail_text)
        if not wrong_place and not own_place:
            return detail_text

        reason = "地点不一致" if wrong_place else f"占领方仍是我方「{own_union_name}」"
        self._log("warning", f"洞天_行动力：点击后安全核验失败（{reason}），立即返回 #279；OCR={detail_text!r}")
        yield from runtime.wait_click_then_view(341, "返回", 279)
        raise RuntimeError(f"洞天_行动力：敌方地点安全核验失败（{reason}），已返回洞天主页")

    def _daily_dongtian_continue_enemy_occupation(self, runtime: Any):
        yield from runtime.wait_click_then_view(341, "位置1", 342)
        yield from runtime.wait_click_then_view(342, "占领", 343)
        yield from runtime.wait_click_then_view(343, "占领", 344)
        yield from runtime.wait_click(344, "战斗")
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
        view279 = runtime.view(279)
        window_shape = runtime.shape(279, "窗口")
        if window_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「窗口」标注，无法查找敌对地点")

        roster_shape = runtime.shape(279, "我的编队")
        if roster_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「我的编队」禁点区标注，拒绝点击地点")
        roster_box = roster_shape.box()

        click_offset_x, click_offset_y = self._daily_dongtian_place_icon_offset(runtime)

        def point_in_box(x: float, y: float, box: dict[str, Any]) -> bool:
            left = float(box.get("x") or 0)
            top = float(box.get("y") or 0)
            return left <= x <= left + float(box.get("w") or 0) and top <= y <= top + float(box.get("h") or 0)

        normalized_targets = {re.sub(r"^\[(?:洞天|福地)\]", "", item).strip(): item for item in enemy_places}
        for scroll_index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            yield from runtime.wait_view(279, label="洞天_行动力：等待 #279 洞天福地")
            frame = runtime.cur_frame(update=True)
            lines = runtime.ocr_fragments_in_shapes(279, ["窗口"], frame_data_url=frame)
            matches: list[tuple[float, float, str, dict[str, Any], float, float]] = []
            for line in lines:
                text = _sanitize_ocr_text(line.get("text"))
                location_text = re.sub(r"^\[(?:洞天|福地)\]", "", text).strip()
                line_center_x = float(line.get("x") or 0) + float(line.get("w") or 0) * 0.5
                line_center_y = float(line.get("y") or 0) + float(line.get("h") or 0) * 0.5
                click_x = line_center_x + click_offset_x
                click_y = line_center_y + click_offset_y
                # 「我的编队」既不能提供地点 OCR 候选，也绝不允许成为最终点击落点。
                if point_in_box(line_center_x, line_center_y, roster_box) or point_in_box(click_x, click_y, roster_box):
                    continue
                for normalized, original in normalized_targets.items():
                    if normalized and location_text == normalized:
                        matches.append((float(line.get("y") or 0), float(line.get("x") or 0), original, line, click_x, click_y))
                        break
            if matches:
                _y, _x, place, line, click_x, click_y = min(matches, key=lambda item: (item[0], item[1]))
                if click_x <= 0 or click_y <= 0:
                    raise RuntimeError(f"洞天_行动力：地点「{place}」 OCR 坐标无效，line={line}")
                self._log(
                    "click",
                    f"洞天_行动力：命中敌对地点「{place}」，按地点模板动态偏移=({click_offset_x:.0f},{click_offset_y:.0f})，点击地图主体=({click_x:.0f},{click_y:.0f})",
                )
                runtime.click_frame_point(279, click_x, click_y)
                yield from runtime.wait_action_settle(float(runtime.payload.get("place_click_settle_seconds") or 2.0))
                return place
            if scroll_index >= max_scrolls:
                break
            self._log("action", f"洞天_行动力：当前窗口未找到敌对地点，向下滚动 {scroll_index + 1}/{max_scrolls}")
            changed = yield from runtime.scroll_shape_content(view279, window_shape, direction="down")
            if not changed:
                break
        raise RuntimeError(f"洞天_行动力：#279 窗口未找到敌对地点，candidates={enemy_places}")

    def _daily_dongtian_place_icon_offset(self, runtime: Any) -> tuple[float, float]:
        """动态计算地点文字中心到可点击图标中心的像素位移。

        #279 的 OCR 返回「地点名称」文字框，但真正需要点击的是同一地点上方的
        「地点图标」。资产树的「模板」内同时标注了这两个子区域，因此每次加载
        #279 数据时都用 `图标中心 - 名称中心` 计算 (dx, dy)。调整任一标注后，
        点击位置会随之更新；缺少标注时直接失败，禁止用历史固定像素值猜测。
        """
        name_shape = runtime.shape(279, "地点名称")
        icon_shape = runtime.shape(279, "地点图标")
        if name_shape is None or icon_shape is None:
            raise RuntimeError("洞天_行动力：缺少 #279「地点名称」或「地点图标」模板标注，拒绝猜测点击偏移")
        name_box = name_shape.box()
        icon_box = icon_shape.box()

        name_center_x = float(name_box.get("x") or 0) + float(name_box.get("w") or 0) * 0.5
        name_center_y = float(name_box.get("y") or 0) + float(name_box.get("h") or 0) * 0.5
        icon_center_x = float(icon_box.get("x") or 0) + float(icon_box.get("w") or 0) * 0.5
        icon_center_y = float(icon_box.get("y") or 0) + float(icon_box.get("h") or 0) * 0.5
        return icon_center_x - name_center_x, icon_center_y - name_center_y

    def _runtime_daily_window_next_time(self, task_id: str, task_type: str, now: datetime | None = None) -> str | None:
        now = now or _runtime_runner._now()
        task = next(
            (
                item
                for item in _read_data_annotation_scheduler_tasks()
                if str(item.get("id") or "") == str(task_id or "")
                or str(item.get("task_type") or "") == str(task_type or "")
            ),
            None,
        )
        if not isinstance(task, dict):
            return None
        window = task.get("window")
        if not isinstance(window, list) or len(window) != 2:
            return None
        start_clock = parse_data_annotation_daily_clock(window[0])
        end_clock = parse_data_annotation_daily_clock(window[1])
        if start_clock is None or end_clock is None:
            return None
        start_at = datetime.combine(now.date(), start_clock)
        end_at = datetime.combine(now.date(), end_clock)
        if end_at <= start_at:
            if now < end_at:
                start_at -= timedelta(days=1)
            else:
                end_at += timedelta(days=1)
        if start_at <= now < end_at:
            schedule_next_time = self._scheduler_task_next_time_from_schedule(task_id, task_type)
            schedule_next_ts = parse_data_annotation_task_time(schedule_next_time)
            if schedule_next_time and schedule_next_ts is not None:
                schedule_next_at = datetime.fromtimestamp(schedule_next_ts)
                if schedule_next_at.date() == now.date() and schedule_next_at > now:
                    return schedule_next_time
            return None
        if now < start_at:
            next_start = start_at
        else:
            next_start = start_at + timedelta(days=1)
        schedule_next_time = self._scheduler_task_next_time_from_schedule(task_id, task_type)
        schedule_next_ts = parse_data_annotation_task_time(schedule_next_time)
        if schedule_next_time and schedule_next_ts is not None:
            schedule_next_at = datetime.fromtimestamp(schedule_next_ts)
            if schedule_next_at >= next_start:
                return schedule_next_time
        return next_start.strftime("%Y-%m-%d %H:%M:%S")

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
            title_pattern=r"参\s*与?.*灵\s*脉.*(?:争|夺).*?(?:1|一)?.*?(?:小\s*时|时)?|灵\s*脉.*(?:争|夺)|灵\s*脉",
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
        while True:
            scene_id = yield from runtime.wait_view(
                285,
                312,
                timeout=max(1.0, min(8.0, deadline - time.monotonic())),
                label=f"{task_label}：点击 #69 入口后等待 #285 造化灵脉",
            )
            if int(scene_id.id if isinstance(scene_id, View) else scene_id) == 285:
                return scene_id
            yield from runtime.wait_click_then_view(312, "确认", [285, 312], timeout=8.0)
            scene_after, _score_after, _frame_after = runtime.current_scene([285, 312], update=True)
            if scene_after == 285:
                return runtime.view(285)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{task_label}：处理 #312 确认弹窗后仍未到达 #285")

    def _record_daily_lingmai_retry(
        self,
        payload: dict[str, Any],
        *,
        message: str,
        seconds: int = 1800,
        retry_at_ms: int | None = None,
    ) -> str:
        if retry_at_ms is None:
            retry_at = _runtime_runner._now() + timedelta(seconds=max(5, int(seconds)))
        else:
            retry_at = datetime.fromtimestamp(max(int(retry_at_ms), int(time.time() * 1000) + 5000) / 1000)
        retry_after = retry_at.strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_retry_after(
            str(payload.get("__scheduler_task_id") or "legacy-daily-lingmai"),
            retry_after,
            task_type="daily_lingmai",
            label="灵脉_座位",
            last_result="skipped",
        )
        self._log("skip", f"灵脉_座位：{message}，{retry_after} 重试")
        return retry_after

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
        scene_after, score_after, frame_after = runtime.current_scene([318, 306, 285, 288, 289, 305, 34], update=True)
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
        scene_after, score_after, frame_after = runtime.current_scene([302, 306, 303, 285, 34, 318, 59], update=True)
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
            scene_id, _score, frame = runtime.current_scene([34, 302, 306, 318, 285, 286, 287, 288, 289, 305, 59], update=True)
        text = runtime.ocr_text(frame) if isinstance(frame, str) and frame else runtime.ocr_text(update=True)
        if scene_id in {302, 306} or ("灵脉" in text and "确认" in text):
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
        if scene_id == 34:
            self._log("success", f"{task_label}：已回到 #34 世界")
            return "success"
        self._log("action", f"{task_label}：完成后按场景图回到 #34 世界")
        yield from runtime.goto_view(34)
        self._log("success", f"{task_label}：完成后已回到 #34 世界")
        return "success"

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
        tokens = self._cached_ocr_tokens(runtime.ctx, frame)
        target_box: dict[str, float] | None = None
        for fragment in group_ocr_tokens(tokens):
            text = _sanitize_ocr_text(fragment.get("text"))
            if "确认" not in text and "确定" not in text:
                continue
            keyword = "确认" if "确认" in text else "确定"
            box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], keyword)
            if box is None:
                continue
            if float(box.get("y") or 0) < 850:
                continue
            target_box = box
        if target_box is None:
            raise RuntimeError(f"{task_label}：灵脉聚灵收益确认浮层缺少可点击「确认」OCR")
        click_x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
        click_y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
        self._log("action", f"{task_label}：点击灵脉聚灵收益确认浮层「确认」")
        runtime.click_frame_point(scene_id or 302, click_x, click_y)
        yield from runtime.wait_action_settle(float(payload.get("lingmai_summary_confirm_settle_seconds") or 2.0))
        return "success"

    def _continue_daily_lingmai_from_zaohua(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: FanxiuRuntime,
        frame: str | None,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image285 = images.get(285)
        if not isinstance(image285, dict):
            raise RuntimeError(f"{task_label}：缺少 #285「造化灵脉」标注，无法进入神脉")
        if self._find_shape(image285, "空位") is None:
            raise RuntimeError(f"{task_label}：缺少 #285「空位」shape 标注，无法进入神脉")

        self._raise_if_stopped(stop_event)
        self._log("action", f"{task_label}：#285 固定点击「空位」进入神脉，不读取或搜索其它灵脉行")
        yield from runtime.wait_click(285, "空位")
        if bool(payload.get("stop_after_click_285_empty")):
            self._log("success", f"{task_label}：试运行已点击 #285「空位」，按 payload 停止")
            return "success"
        yield from runtime.wait_view(
            286,
            timeout=float(payload.get("lingmai_select_slot_timeout") or 12.0),
            label=f"{task_label}：点击 #285「空位」后等待 #286 神脉座位页",
        )
        scene_next, score_next, frame_next = runtime.current_scene([286], update=True)
        text_next = runtime.ocr_text(frame_next)
        self._log("success", f"{task_label}：已到达 #286 神脉座位页，当前 #{scene_next if scene_next is not None else 'unknown'} {score_next:.0f}%，OCR={text_next[:160]}")
        return (yield from self._continue_daily_lingmai_from_select_slot(ctx, stop_event, payload, runtime, frame_next, task_label=task_label))

    def _continue_daily_lingmai_from_select_slot(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: FanxiuRuntime,
        frame: str | None,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image286 = images.get(286)
        if not isinstance(image286, dict):
            raise RuntimeError(f"{task_label}：缺少 #286 神脉座位页标注，无法选择座位动作")

        self._raise_if_stopped(stop_event)
        selection = refresh_and_select_lingmai_seat_action(
            since_seconds=int(payload.get("lingmai_packet_since_seconds") or 1200),
        )
        if not selection.get("ok"):
            raise RuntimeError(
                f"{task_label}：读取神脉座位或自身战力失败，"
                f"status={selection.get('status')} reason={selection.get('reason')}"
            )
        action = str(selection.get("action") or "")
        if action == "already_seated":
            self_seat = selection.get("self_seat") if isinstance(selection.get("self_seat"), dict) else {}
            self._log(
                "success",
                f"{task_label}：抓包确认自己已在神脉座位 {self_seat.get('seat_id')}，不再占空位或驱离玩家",
            )
            yield from runtime.goto_view(34)
            return "success"
        if action == "retry":
            retry_at_ms = selection.get("retry_at_ms")
            retry_reason = str(selection.get("retry_reason") or "no_target")
            yield from runtime.goto_view(34)
            retry_after = self._record_daily_lingmai_retry(
                payload,
                message=(
                    "当前无可驱离目标，等待最早可击败目标保护结束"
                    if retry_reason == "earliest_beatable_protection_end"
                    else "当前及保护期内均无可击败的非友军"
                ),
                seconds=int(payload.get("lingmai_no_target_retry_seconds") or 1800),
                retry_at_ms=int(retry_at_ms) if retry_at_ms is not None else None,
            )
            self._log("skip", f"{task_label}：已返回 #34，{retry_after} 重新抓取座位清单")
            return "skipped"
        if action == "kick":
            target = selection.get("target") if isinstance(selection.get("target"), dict) else None
            if target is None:
                raise RuntimeError(f"{task_label}：选人策略返回 kick 但缺少目标，已停止且未点击")
            self._log(
                "info",
                f"{task_label}：选择最低战力可驱离非友军「{target.get('name')}」，"
                f"战力 {float(target.get('battle_score') or 0):.3e}",
            )
            return (yield from self._click_daily_lingmai_kick_target(
                ctx,
                stop_event,
                payload,
                runtime,
                target_player=target,
                task_label=task_label,
            ))
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

        threshold = float(payload.get("lingmai_select_empty_slot_threshold") or self.overlay_threshold)
        if not frame:
            frame = runtime.cur_frame(update=True)
        score = runtime.shape_score(286, "选择空位", frame_data_url=frame)
        self._log("detail", f"{task_label}：二次校验 #286「选择空位」score={score:.0f}% threshold={threshold:.0f}%")
        if score < threshold:
            self._log("warning", f"{task_label}：#286「选择空位」匹配不足 {score:.0f}%，点击返回")
            yield from runtime.wait_click(286, "返回")
            yield from runtime.wait_action_settle(float(payload.get("lingmai_select_return_settle_seconds") or 2.0))
            raise RuntimeError(f"{task_label}：#286「选择空位」二次校验失败 {score:.0f}%<{threshold:.0f}%，已返回")

        self._log("success", f"{task_label}：#286「选择空位」二次校验通过 {score:.0f}%，点击「占领」")
        yield from runtime.wait_click(286, "占领")
        yield from runtime.wait_action_settle(float(payload.get("lingmai_occupy_click_settle_seconds") or 2.0))
        scene_next, score_next, frame_next = runtime.current_scene([287, 285, 286, 47], update=True)
        text_next = runtime.ocr_text(frame_next)
        text_compact = _sanitize_ocr_text(text_next)
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

        scene_transit, score_transit, frame_transit = runtime.current_scene([288, 85, 186], update=True)
        text_transit = runtime.ocr_text(frame_transit)
        if scene_transit != 288 and "聚灵位" in _sanitize_ocr_text(text_transit):
            self._log(
                "success",
                f"{task_label}：确认后已进入灵脉区域，当前 #{scene_transit if scene_transit is not None else 'unknown'} "
                f"{score_transit:.0f}%，点击 OCR「聚灵位」进入占领页",
            )
            self._click_daily_lingmai_slot_entry(runtime, frame_transit, task_label=task_label)
            yield from runtime.wait_action_settle(float(payload.get("lingmai_slot_entry_settle_seconds") or 2.0))

        yield from runtime.wait_view(
            288,
            timeout=float(payload.get("lingmai_after_confirm_timeout") or 90.0),
            label=f"{task_label}：点击 #287「确认」后等待 #288 占领页",
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
        runtime: FanxiuRuntime,
        *,
        target_player: Mapping[str, Any],
        task_label: str,
    ):
        """Find the selected #286 player row and click its aligned kick button.

        The packet-selected seat id is the identity evidence.  The exact player
        name is used only to find its visible row.  The click point is derived
        from the user-annotated [姓名] -> [驱离按钮] offset, never from a global
        fixed coordinate or an OCR-discovered button belonging to another row.
        """

        target_id = str(target_player.get("seat_id") or target_player.get("id") or "").strip()
        target_name = _sanitize_ocr_text(target_player.get("name"))
        if not target_id or not target_name:
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

        max_scrolls = max(0, int(payload.get("lingmai_kick_max_scrolls") or 12))
        for index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            tokens = self._cached_ocr_tokens(ctx, frame)
            matches: list[dict[str, float]] = []
            for fragment in group_ocr_tokens(tokens):
                if target_name not in _sanitize_ocr_text(fragment.get("text")):
                    continue
                target_box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], target_name)
                if target_box is not None:
                    matches.append(target_box)
            if len(matches) > 1:
                raise RuntimeError(f"{task_label}：#286 目标姓名「{target_name}」不唯一，已停止且未点击")
            if len(matches) == 1:
                target_box = matches[0]
                target_y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
                # The button column is fixed by the [驱离按钮] annotation.  OCR
                # name widths vary with titles and glyphs, so deriving X from
                # the name center can drift into the page edge.  Only the row
                # Y follows the matched name; X always stays in the annotated
                # kick-button column.
                click_x = button_center_x
                click_y = target_y + offset_y
                if not (0 < click_x < frame_width and 0 < click_y < frame_height):
                    raise RuntimeError(
                        f"{task_label}：目标「{target_name}」对应驱离按钮中心超出画面，已停止且未点击"
                    )
                self._log(
                    "action",
                    f"{task_label}：在 #286 第 {index + 1} 屏找到目标「{target_name}」，点击同条目「驱离按钮」",
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
            if index >= max_scrolls:
                break
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
        raise RuntimeError(f"{task_label}：滚动 #286 后仍未找到目标「{target_name}」，已停止且未点击")

    def _complete_daily_lingmai_kick(
        self,
        runtime: FanxiuRuntime,
        payload: dict[str, Any],
        *,
        task_label: str,
    ) -> str:
        """Complete #380 -> battle -> #306 and reuse the normal Lingmai tail."""

        yield from runtime.wait_click_then_view(
            380,
            "驱离",
            381,
            timeout=float(payload.get("lingmai_kick_confirm_timeout") or 30.0),
        )
        yield from runtime.wait_click_then_view(
            381,
            "确定",
            318,
            timeout=float(payload.get("lingmai_kick_battle_dialogue_timeout") or 45.0),
        )
        yield from runtime.advance_dialogue(318, "确认", label=f"{task_label}：推进战前对话")
        battle_scene = yield from runtime.wait_scene(
            374,
            382,
            timeout=float(payload.get("lingmai_kick_battle_start_timeout") or 60.0),
            label=f"{task_label}：等待 #374 战斗或 #382 胜利",
        )
        battle_scene_id = int(battle_scene.id if isinstance(battle_scene, View) else battle_scene)
        if battle_scene_id == 374:
            yield from runtime.wait_scene(
                382,
                timeout=float(payload.get("lingmai_kick_battle_finish_timeout") or 180.0),
                label=f"{task_label}：等待战斗结束到 #382",
            )
        yield from runtime.wait_click_then_view(
            382,
            "关闭",
            318,
            timeout=float(payload.get("lingmai_kick_victory_close_timeout") or 30.0),
        )
        yield from runtime.advance_dialogue(318, "确认", label=f"{task_label}：推进战后对话")
        yield from runtime.wait_scene(
            306,
            timeout=float(payload.get("lingmai_kick_summary_timeout") or 45.0),
            label=f"{task_label}：等待 #306 灵脉收益确认",
        )
        scene_id, _score, frame = runtime.current_scene([306], update=True)
        return (yield from self._finish_daily_lingmai_to_world(
            runtime,
            payload,
            task_label=task_label,
            scene_id=scene_id,
            frame=frame,
        ))

    def _click_daily_lingmai_go_button(
        self,
        runtime: FanxiuRuntime,
        frame: str | None,
        *,
        task_label: str,
    ) -> None:
        frame = frame if isinstance(frame, str) and frame else runtime.cur_frame(update=True)
        tokens = self._cached_ocr_tokens(runtime.ctx, frame)
        target_box: dict[str, float] | None = None
        for fragment in group_ocr_tokens(tokens):
            text = _sanitize_ocr_text(fragment.get("text"))
            if "前往灵脉" not in text:
                continue
            box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], "前往灵脉")
            if box is None:
                continue
            target_box = box
            break
        if target_box is None:
            raise RuntimeError(f"{task_label}：#286 已出现「前往灵脉」文本，但未取到可点击 OCR 坐标")
        click_x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
        click_y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
        self._log("action", f"{task_label}：点击 #286 OCR「前往灵脉」")
        runtime.click_frame_point(286, click_x, click_y)

    def _click_daily_lingmai_slot_entry(
        self,
        runtime: FanxiuRuntime,
        frame: str | None,
        *,
        task_label: str,
    ) -> None:
        frame = frame if isinstance(frame, str) and frame else runtime.cur_frame(update=True)
        tokens = self._cached_ocr_tokens(runtime.ctx, frame)
        target_box: dict[str, float] | None = None
        for fragment in group_ocr_tokens(tokens):
            text = _sanitize_ocr_text(fragment.get("text"))
            if "聚灵位" not in text:
                continue
            box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], "聚灵位")
            if box is None:
                continue
            target_box = box
            break
        if target_box is None:
            raise RuntimeError(f"{task_label}：已进入灵脉区域，但 OCR「聚灵位」缺少可点击坐标")
        click_x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
        click_y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
        self._log("action", f"{task_label}：点击区域内部 OCR「聚灵位」")
        runtime.click_frame_point(85, click_x, click_y)

    def _continue_daily_lingmai_from_final_occupy(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime: FanxiuRuntime,
        *,
        task_label: str,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image288 = images.get(288)
        if not isinstance(image288, dict):
            raise RuntimeError(f"{task_label}：缺少 #288「占领」过渡后场景标注，无法确认灵脉占领")
        if self._find_shape(image288, "占领") is None:
            raise RuntimeError(f"{task_label}：缺少 #288「占领」shape 标注，无法点击过渡后的占领按钮")
        yield from runtime.wait_click(288, "占领")
        yield from runtime.wait_action_settle(float(payload.get("lingmai_final_occupy_settle_seconds") or 2.0))
        scene_final, score_final, frame_final = runtime.current_scene([318, 306, 305, 288, 285, 286, 287, 47], update=True)
        text_final = runtime.ocr_text(frame_final)
        if scene_final == 318:
            return (yield from self._confirm_daily_lingmai_reward(runtime, payload, task_label=task_label))
        if scene_final == 305:
            return (yield from self._confirm_daily_lingmai_gather(runtime, payload, task_label=task_label))
        if scene_final == 306:
            return (yield from self._finish_daily_lingmai_to_world(runtime, payload, task_label=task_label, scene_id=scene_final, frame=frame_final))
        raise RuntimeError(
            f"{task_label}：已点击 #288「占领」，但后续业务状态机尚未迁移；"
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
        return bool("已拜谒" in compact or re.search(r"剩余次数[:：]?0/1", compact))

    def _baiye_text_can_worship(self, text: Any) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool("拜谒" in compact and re.search(r"剩余次数[:：]?1/1", compact))

    def _baiye_text_is_target_worship_page(self, text: Any, target: str) -> bool:
        compact = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        target_text = _sanitize_ocr_text(target).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(target_text and target_text in compact and self._baiye_text_can_worship(compact))

    def _baiye_stack_scene_from_text(self, text: Any) -> int | None:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        if not compact:
            return None
        if "剩余次数" in compact or "本次拜谒" in compact or "已拜谒" in compact:
            return 266
        if "可旋转" in compact or "选择法则之主" in compact or "进行拜谒" in compact:
            return 265
        if "三千大道" in compact or "跨法则" in compact or "跨洪则" in compact:
            return 264
        return None

    def _click_baiye_worship_button(
        self,
        runtime: Any,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> Iterator[Any]:
        self._log("action", f"日常_拜谒：{reason}，点击 #266「拜谒」")
        runtime.click_shape_center(266, "拜谒")
        yield from runtime.wait_action_settle(float(payload.get("baiye_worship_settle_seconds") or 2.0))
        worship_text = runtime.ocr_text(update=True)
        if self._baiye_text_is_completed(worship_text):
            self._log("success", f"日常_拜谒：已完成拜谒，OCR={worship_text[:120]}")
            yield from self._return_baiye_to_world(runtime, payload, reason="拜谒完成后收尾")
            return "success"
        if self._baiye_text_can_worship(worship_text) or self._baiye_text_is_lord_map(worship_text):
            raise RuntimeError(f"日常_拜谒：点击 #266「拜谒」后仍未完成，OCR={worship_text[:120]}")
        self._log("success", f"日常_拜谒：已点击 #266「拜谒」，点击后 OCR={worship_text[:120]}")
        yield from self._return_baiye_to_world(runtime, payload, reason="拜谒点击后收尾")
        return "success"

    def _return_baiye_to_world(
        self,
        runtime: Any,
        payload: Mapping[str, Any] | None = None,
        *,
        reason: str,
    ) -> Iterator[Any]:
        wait_timeout = float((payload or {}).get("baiye_return_wait_timeout") or 18.0)
        settle_seconds = float((payload or {}).get("baiye_return_settle_seconds") or 1.0)
        self._log("action", f"日常_拜谒：{reason}，按拜谒页面栈返回 #34")
        for _attempt in range(4):
            frame = runtime.cur_frame(update=True)
            text = runtime.ocr_text(frame)
            scene_id = self._baiye_stack_scene_from_text(text)
            score = 100.0 if scene_id is not None else 0.0
            if scene_id is None:
                scene_id, score, _frame = runtime.current_scene([266, 265, 264, 34], frame_data_url=frame)
            if scene_id == 34:
                self._log("success", "日常_拜谒：已返回 #34 世界，闭环完成")
                return "success"
            if scene_id == 266:
                self._log("action", f"日常_拜谒：当前 #266 {score:.0f}%，点击「返回」回法则之主选择页")
                runtime.click_shape_center(266, "返回")
                yield from runtime.wait_view(265, 264, 34, timeout=wait_timeout, label="日常_拜谒：等待 #266 返回")
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if scene_id == 265:
                self._log("action", f"日常_拜谒：当前 #265 {score:.0f}%，点击「返回」回三千大道")
                runtime.click_shape_center(265, "返回")
                yield from runtime.wait_view(264, 34, timeout=wait_timeout, label="日常_拜谒：等待 #265 返回")
                yield from runtime.wait_action_settle(settle_seconds)
                continue
            if scene_id == 264:
                self._log("action", f"日常_拜谒：当前 #264 {score:.0f}%，点击「返回」回世界")
                runtime.click_shape_center(264, "返回")
                yield from runtime.wait_view(34, timeout=wait_timeout, label="日常_拜谒：等待 #264 返回世界")
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
        text = runtime.ocr_text(frame)
        if self._baiye_text_is_completed(text) or self._baiye_text_can_worship(text):
            return (yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target))
        if scene_id != 265 and not self._baiye_text_is_lord_map(text):
            if scene_id != 264 and not self._baiye_text_is_rule_map(text):
                if scene_id != 69:
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
                    {
                        "scene": runtime.view_visible(264),
                        "text": runtime.ocr_matches(self._baiye_text_is_rule_map, label="日常_拜谒：三千大道 OCR"),
                    },
                    timeout=20.0,
                    label="日常_拜谒：等待三千大道 #264",
                )
            yield from self._open_baiye_cross_rule(ctx, stop_event, payload, keyword=str(payload.get("cross_keyword") or "16"))
        return (yield from self._select_baiye_law_lord(ctx, stop_event, payload, target=target))

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
        remaining_numbers = re.findall(r"\d+", _sanitize_ocr_text(remaining_text).translate(FULLWIDTH_DIGIT_TRANSLATION))
        if not remaining_numbers:
            raise RuntimeError(f"日常_绿瓶拜谒：未能从 #283[剩余次数] 读取剩余次数，OCR={remaining_text[:80]}")
        remaining = int(remaining_numbers[0])
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
        reverse_scrolls = self._payload_int(payload, "baiye_rule_reverse_scrolls", "reverse_scrolls", default=max_scrolls)
        for direction, scroll_count in (("down", max_scrolls), ("up", reverse_scrolls)):
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
                tokens = runtime.ocr_tokens_in_shapes(264, ["识别区"], frame_data_url=frame)
                matches = [fragment for fragment in group_ocr_tokens(tokens) if keyword in _sanitize_ocr_text(fragment.get("text"))]
                if matches:
                    fragment = sorted(matches, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))[0]
                    target_box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], keyword)
                    if target_box is None:
                        continue
                    x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
                    y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
                    text = _sanitize_ocr_text(fragment.get("text"))
                    self._log("action", f"日常_拜谒：点击 #264 OCR「{text}」")
                    runtime.click_frame_point(264, x, y)
                    yield from runtime.wait_any(
                        {
                            "scene": runtime.view_visible(265),
                            "text": runtime.ocr_matches(self._baiye_text_is_lord_map, label="日常_拜谒：法则之主 OCR"),
                        },
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

    def _baiye_target_box_from_tokens(self, tokens: list[dict[str, Any]], target: str) -> dict[str, float] | None:
        candidate_tokens: list[dict[str, Any]] = []
        for fragment in group_ocr_tokens(tokens):
            if "法则" in _sanitize_ocr_text(fragment.get("text")):
                continue
            candidate_tokens.extend(query_spatial_ocr(tokens, fragment)["tokens"])
        return locate_text_box(candidate_tokens, target)

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
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            if time.monotonic() - start >= timeout:
                self._log("warning", f"日常_拜谒：{timeout:.0f}s 未找到「{target}」，点击返回本次失败")
                runtime.click_shape_center(265, "返回")
                yield from runtime.wait_any(
                    {
                        "scene": runtime.view_visible(264),
                        "text": runtime.ocr_matches(self._baiye_text_is_rule_map, label="日常_拜谒：返回三千大道 OCR"),
                    },
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
            current_text = runtime.ocr_text(frame)
            if self._baiye_text_is_completed(current_text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_拜谒：当前已在法则详情完成态",
                        phase="daily_baiye_detail_done",
                        current_scene=266,
                    )
                self._log("success", f"日常_拜谒：当前已是完成态，OCR={current_text[:120]}")
                yield from self._return_baiye_to_world(runtime, payload, reason="检测到已完成态后收尾")
                return "success"
            if self._baiye_text_can_worship(current_text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_拜谒：当前已在法则详情可拜谒态",
                        phase="daily_baiye_detail_worship",
                        current_scene=266,
                    )
                if self._baiye_text_is_target_worship_page(current_text, target):
                    reason = f"当前已在「{target}」详情页"
                else:
                    reason = f"当前详情页已显示可拜谒状态，但 OCR 未稳定包含目标「{target}」"
                return (yield from self._click_baiye_worship_button(runtime, payload, reason=reason))
            tokens = runtime.ocr_tokens_in_shapes(
                265,
                ["识别区"],
                frame_data_url=frame,
                options={
                    "text_det_thresh": float(payload.get("baiye_text_det_thresh") or 0.25),
                    "text_det_box_thresh": float(payload.get("baiye_text_det_box_thresh") or 0.45),
                    "text_det_unclip_ratio": float(payload.get("baiye_text_det_unclip_ratio") or 1.2),
                },
            )
            target_box = self._baiye_target_box_from_tokens(tokens, target)
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
                            {
                                "scene": runtime.view_visible(264),
                                "text": runtime.ocr_matches(self._baiye_text_is_rule_map, label="日常_拜谒：probe 返回三千大道 OCR"),
                            },
                            timeout=20.0,
                            label="日常_拜谒：probe 等待返回 #264",
                        )
                    return "skipped"
                runtime.click_frame_point(265, click_x, click_y)
                yield from runtime.wait_action_settle(1.0)
                after_text = runtime.ocr_text(update=True)
                if self._baiye_text_is_completed(after_text):
                    self._log("success", f"日常_拜谒：已点击「{target}」，完成态 OCR={after_text[:120]}")
                    yield from self._return_baiye_to_world(runtime, payload, reason=f"已点击「{target}」进入完成态后收尾")
                    return "success"
                if self._baiye_text_can_worship(after_text):
                    return (yield from self._click_baiye_worship_button(runtime, payload, reason=f"已选中「{target}」且显示可拜谒"))
                if self._baiye_text_is_lord_map(after_text):
                    if not self._baiye_text_can_worship(after_text):
                        self._log("skip", f"日常_拜谒：已点击「{target}」，但仍停留在法则之主选择页且未确认可拜谒，OCR={after_text[:120]}")
                        return "skipped"
                self._log("success", f"日常_拜谒：已点击「{target}」，点击后 OCR={after_text[:120]}")
                return "success"
            self._log("detail", f"日常_拜谒：暂未命中「{target}」，OCR={last_text[:80]}")
            yield from runtime.wait_action_settle(float(payload.get("baiye_lord_poll_seconds") or 0.75))

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
