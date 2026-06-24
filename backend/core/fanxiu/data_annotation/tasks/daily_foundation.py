from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation import runtime_runner as _runtime_runner
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.runtime_runner import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _now,
    _parse_daily_boss_hp_percent,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
    _read_data_annotation_scheduler_tasks,
    _read_data_annotation_world_facts,
    _write_data_annotation_world_facts,
)
from backend.core.fanxiu.data_annotation.state import parse_data_annotation_task_time


_DAILY_AUDIT_TASK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("daily_boss", "daily-boss", r"击败首领|首领"),
    ("daily_dungeon", "legacy-daily-dungeon", r"通关每日副本|每日副本|副本探险"),
    ("daily_shuangxiu", "legacy-daily-shuangxiu", r"完成双人修炼|双人修炼|双修"),
    ("daily_jianling", "legacy-daily-jianling", r"淬剑试炼|剑试"),
    ("daily_lingta", "legacy-daily-lingta", r"混沌灵塔|灵塔"),
    ("daily_youli", "legacy-daily-youli", r"完成修仙传游历|修仙传游历"),
    ("daily_xianyuan", "legacy-daily-xianyuan", r"挑战仙缘|仙缘"),
    ("daily_lingzu", "legacy-daily-lingzu", r"灵祖|圣雷龙"),
    ("daily_yaowang", "legacy-daily-yaowang", r"妖王来袭|妖王"),
    ("daily_yaozu", "legacy-daily-yaozu", r"妖族袭城|妖族"),
    ("daily_gongfeng", "legacy-daily-gongfeng", r"供奉"),
    ("daily_xianshi", "legacy-daily-xianshi", r"仙市"),
    ("daily_yihuo", "legacy-daily-yihuo", r"异火"),
)

_DAILY_AUDIT_COMPLETION_MIN_TOTAL: dict[str, int] = {
    "daily_dungeon": 6,
}


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
        else:
            current_cd = _parse_daily_boss_cd_seconds(current_text)
            if current_cd and current_cd > 0:
                next_time = self._record_daily_boss_recheck_time(payload, seconds=max(60, current_cd))
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：当前首领已在刷新 CD，未检测到 #181 完成态，{current_text}，{next_time} 复查",
                        phase="daily_boss_current_cd",
                    )
                    self._log_locked("skip", self._status["message"])
                return "skipped"

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
                yield from self._open_daily_boss_list_from_daily(ctx, stop_event)
                scene_id = 178
            detail_status = yield from self._open_watched_daily_boss_detail(ctx, stop_event, payload)
            if detail_status == "done":
                yield from self._return_daily_boss_to_world(ctx, stop_event)
                return "success"
            if detail_status == "skipped":
                yield from self._return_daily_boss_to_world(ctx, stop_event)
                return "skipped"

        return (yield from self._handle_daily_boss_detail(ctx, stop_event, payload))

    def _open_daily_boss_list_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from runtime.open_daily_entry(
            label="日常_首领",
            title_pattern=r"击\s*败\s*首\s*领",
            progress_can_mark_done=False,
            max_scrolls=10,
            reverse_scrolls=10,
        )
        if status == "not_found":
            raise RuntimeError("日常_首领：#69 日常列表未找到「击败首领」")
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
            next_time = self._next_daily_boss_reset_time_text()
            scheduler_task_id = "daily-boss"
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="daily_boss",
                label="日常_首领",
                last_result="success",
            )
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

        if remaining is None or remaining > 0:
            cd_seconds, cd_text = self._daily_boss_refresh_cd_from_list(ctx)
            if cd_seconds and cd_seconds > 0:
                next_time = self._record_daily_boss_recheck_time(payload, seconds=cd_seconds + 10)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：注视中首领尚未刷新，{cd_text or cd_seconds}，下次 {next_time}",
                        phase="daily_boss_list_cd",
                        current_scene=178,
                    )
                    self._log_locked("skip", self._status["message"])
                return "skipped"

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
            matches = runtime.ocr_centers_in_shape(178, "首领列表", include=("注视",), frame_data_url=frame)
            if matches:
                x, y, text = matches[0]
                click_x = max(x, float(self._box(list_shape, image178).get("x") or 0) + float(self._box(list_shape, image178).get("w") or 0) * 0.78)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：点击注视中首领 {text}",
                        phase="daily_boss_click_watched",
                        current_scene=178,
                    )
                    self._log_locked("action", f"日常_首领：点击 #178「{text}」")
                runtime.click_frame_point(178, click_x, y)
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
        raise RuntimeError("日常_首领：仙界首领列表未找到「注视中」目标")

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
        if cd_seconds and cd_seconds > 0:
            next_time = (_runtime_runner._now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            self._record_scheduler_task_discovered_retry_after(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
                last_result="skipped",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：首领尚未刷新，{detail_text}，下次 {next_time}",
                    phase="daily_boss_wait_cd",
                    current_scene=179,
                )
                self._log_locked("skip", self._status["message"])
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            return "skipped"

        if "前往挑战" not in detail_text:
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

        view179 = runtime.get_view(179)
        challenge_shape = view179.get_shape("前往挑战") if isinstance(view179, View) else None
        if challenge_shape is None:
            raise RuntimeError("缺少 #179「前往挑战」标注，无法挑战首领")
        with self._lock:
            self._set_status_locked("running", "日常_首领：点击前往挑战", phase="daily_boss_challenge", current_scene=179)
            self._log_locked("action", "日常_首领：点击 #179「前往挑战」")
        challenge_shape.click(runtime)
        post_result = yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload)
        return post_result

    def _wait_daily_boss_after_challenge(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        deadline = time.monotonic() + float(payload.get("post_challenge_wait_seconds") or 900)
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        image179 = ctx.get("images", {}).get(179)
        stuck_twenty_count = 0
        stuck_boss_map_count = 0
        stuck_twenty_threshold = max(2, int(payload.get("boss_twenty_percent_stuck_count") or 5))
        stuck_boss_map_threshold = max(2, int(payload.get("boss_map_stuck_count") or 5))
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            if stop_event.wait(3.0):
                self._raise_if_stopped(stop_event)
            scene_id, score, frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
            if scene_id == 181:
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
            if scene_id == 180:
                current_text = self._daily_boss_status_text_from_frame(ctx, frame)
                if self._daily_boss_done_text(current_text):
                    return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
                hp_percent = _parse_daily_boss_hp_percent(current_text)
                stuck_twenty_count = stuck_twenty_count + 1 if hp_percent == 20 else 0
                stuck_boss_map_count = stuck_boss_map_count + 1 if self._daily_boss_stuck_map_text(current_text) else 0
                if stuck_twenty_count >= stuck_twenty_threshold:
                    return (
                        yield from self._leave_daily_boss_fighting_and_recheck_rewards(
                            ctx,
                            stop_event,
                            payload,
                            reason=f"连续 {stuck_twenty_count} 次识别 #180 生命值 20%",
                        )
                    )
                if stuck_boss_map_count >= stuck_boss_map_threshold:
                    return (
                        yield from self._leave_daily_boss_fighting_and_recheck_rewards(
                            ctx,
                            stop_event,
                            payload,
                            reason=f"连续 {stuck_boss_map_count} 次停留在 #180 首领地图",
                        )
                    )
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：已识别 #180 战斗中 {score:.0f}%"
                        + (f"，生命值 {hp_percent}%" if hp_percent is not None else "")
                        + "，继续等待 #181 封印",
                        phase="daily_boss_wait_boss_done",
                        current_scene=180,
                    )
                yield BehaviorTreeStatus.RUNNING
                continue
            stuck_twenty_count = 0
            stuck_boss_map_count = 0
            if scene_id == 179 and isinstance(image179, dict):
                text = runtime.ocr_text_in_shapes(image179, ("剩余奖励次数", "挑战状态"), frame_data_url=frame, padding=20)
                cd_seconds = _parse_daily_boss_cd_seconds(text)
                remaining = _parse_daily_boss_reward_remaining(text)
                status_detail = "奖励次数变化" if remaining == 0 else f"刷新 CD {text}" if cd_seconds and cd_seconds > 0 else "详情状态未变化"
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：挑战后已回到详情 #179 {score:.0f}%，已读到{status_detail}，仍需等待 #181 封印",
                        phase="daily_boss_wait_post_detail",
                        current_scene=179,
                    )
            else:
                current_text = self._daily_boss_status_text_from_frame(ctx, frame)
                if self._daily_boss_done_text(current_text):
                    return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
                current_cd = _parse_daily_boss_cd_seconds(current_text)
                if current_cd and current_cd > 0:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_首领：挑战后已读到刷新 CD，但还未识别 #181 封印，继续等待：{current_text}",
                            phase="daily_boss_wait_done_after_cd",
                            current_scene=scene_id,
                        )
                    yield BehaviorTreeStatus.RUNNING
                    continue
                if self._daily_boss_combat_in_progress_text(current_text):
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
                        "日常_首领：挑战中，等待回到首领详情",
                        phase="daily_boss_wait_post_challenge",
                        current_scene=scene_id,
                    )
            yield BehaviorTreeStatus.RUNNING
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        self._log("skip", f"日常_首领：等待 #181「封印」超时，未确认挑战完成，{next_time} 重试确认")
        return "skipped"

    def _complete_daily_boss_from_done_frame(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        next_time, source = yield from self._record_daily_boss_next_time_after_done(ctx, stop_event, payload)
        result = "success" if "奖励次数已用尽" in str(source or "") else "skipped"
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：已识别 #181 封印完成，{source}，下次 {next_time}",
                phase="daily_boss_done",
                current_scene=181,
            )
            self._log_locked(result if result != "skipped" else "skip", self._status["message"])
        yield from self._return_daily_boss_to_world(ctx, stop_event)
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

    def _open_daily_boss_list_after_leaving_fight(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
        stop_event: threading.Event,
    ):
        try:
            yield from runtime.wait_view_id(178, timeout=8.0, label="日常_首领：等待首领列表 #178")
            return True
        except RuntimeError:
            pass
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
            yield from self._open_daily_boss_list_from_daily(ctx, stop_event)
            return True
        except Exception as exc:
            with self._lock:
                self._log_locked("warning", f"日常_首领：离开战斗后重新进入 #178 失败：{exc}")
            return False

    def _record_daily_boss_next_time_after_done(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        scene_id, _score, _frame, _text = self._fanxiu_runtime_scene_text(ctx, runtime, update=True)
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
                except Exception as exc:
                    with self._lock:
                        self._log_locked("warning", f"日常_首领：离开 #181 后未能回到 #178 读取刷新时间：{exc}")
            else:
                with self._lock:
                    self._log_locked("warning", "日常_首领：缺少 #181「离开」标注，无法回列表读取 #182 刷新时间")

        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "已识别 #181 封印完成；挑战后不直接判定当天完成，半小时后复查剩余奖励次数"

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
        next_time = (_runtime_runner._now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_retry_after(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
            task_type="daily_boss",
            label="日常_首领",
            last_result="skipped",
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
        if scheduler_task_id:
            tasks = _read_data_annotation_scheduler_tasks()
            self._mark_scheduler_task(tasks, scheduler_task_id, "success")
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
                text = runtime.ocr_text(update=True)
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
            if scene_id == 86 or self._leave_scene_confirm_text(text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_灵祖：确认离开当前场景",
                        phase="daily_lingzu_leave_confirm",
                        current_scene=86,
                    )
                    self._log_locked("action", "日常_灵祖：点击 #86「确认」离开场景")
                yield from runtime.wait_click(86, "确认")
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
            raise RuntimeError("日常_剑灵：日常列表未找到「淬剑试炼」任务")
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
            raise RuntimeError("日常_剑灵：日常列表未找到「淬剑试炼」任务")
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
        if require_world_like and not self._daily_assistant_text_is_world_like(text):
            return False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        ref_image = images.get(34) if isinstance(images.get(34), dict) else {"filename": "world_runtime.png", "width": 900, "height": 1600}
        runtime = self._fanxiu_observer(ctx, stop_event, frame_data_url=frame)
        width, height = self._frame_size(ref_image)
        lines = runtime.ocr_lines(frame)
        matches = self._world_scene_leave_matches(lines, width=width, height=height)
        click_image = ref_image
        if not matches:
            image85 = images.get(85) if isinstance(images.get(85), dict) else None
            leave_shape = self._find_shape(image85, "离开") if isinstance(image85, dict) else None
            if isinstance(image85, dict) and isinstance(leave_shape, dict):
                score = float(self._shape_score(ctx, image85, leave_shape, frame) or 0.0)
                if score >= float(getattr(self, "overlay_threshold", 80.0)):
                    x, y = ActionPlanner().shape_center(image85, leave_shape)
                    matches = [(float(x), float(y), f"#85「离开」{score:.0f}%")]
                    click_image = image85
            if not matches:
                return False
        x, y, matched_text = matches[0]
        with self._lock:
                    self._set_status_locked("running", f"{label}：当前在场景内，点击右侧「离开」", phase="world_side_scene_leave", current_scene=None)
                    self._log_locked("action", f"{label}：命中右侧「{matched_text}」，先离开场景")
        runtime.click_frame_point(View(click_image), x, y)
        yield from runtime.wait_action_settle(2.0)
        confirm_scene_id, _confirm_score, confirm_frame = runtime.current_scene([86], update=True)
        confirm_text = runtime.ocr_text(confirm_frame)
        if confirm_scene_id == 86 or "是否离开" in _sanitize_ocr_text(confirm_text):
            image86 = images.get(86) if isinstance(images.get(86), dict) else None
            confirm_shape = self._find_shape(image86, "确认") if isinstance(image86, dict) else None
            if isinstance(image86, dict) and confirm_shape is not None:
                with self._lock:
                    self._set_status_locked("running", f"{label}：确认离开当前场景", phase="world_side_scene_leave_confirm", current_scene=86)
                    self._log_locked("action", f"{label}：点击 #86「确认」离开场景")
                yield from runtime.wait_click(86, "确认")
                yield from runtime.wait_action_settle(2.0)
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
        world_like = self._daily_assistant_text_is_world_like(text)
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
            scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
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
                scene_id, _score, frame = runtime.current_scene([69, 34], update=True)
                text = runtime.ocr_text(frame)
                if scene_id == 69 and self._daily_text_is_daily_list(text):
                    return 69
                world_like = scene_id == 34 or self._daily_assistant_text_is_world_like(text)
            except Exception as exc:
                self._log("warning", f"{label}：修仙传游历页返回世界失败，继续尝试场景图恢复：{exc}")
        hidden_world_popup_like = scene_id == 59 or "封魔杀" in _sanitize_ocr_text(text)
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

    def _leave_daily_lingta_green_bottle(self, ctx: dict[str, Any], stop_event: threading.Event):
        image20 = ctx.get("images", {}).get(20)
        if not isinstance(image20, dict):
            raise RuntimeError("缺少 #20「绿瓶」标注，无法回到世界")
        back_shape = self._find_shape(image20, "回到世界")
        if back_shape is None:
            raise RuntimeError("缺少 #20「回到世界」标注，无法回到世界")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：退出绿瓶", phase="daily_lingta_exit_green_bottle", current_scene=20)
            self._log_locked("action", "日常_灵塔：点击 #20「回到世界」")
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
                    self._log_locked("success", "日常_灵塔：已从绿瓶回到世界")
                return "success"
            if not clicked_outer_world and self._daily_lingta_text_is_green_bottle_like(text):
                width, height = self._frame_size(image20)
                x = width * 0.105
                y = height * 0.91
                with self._lock:
                    self._set_status_locked("running", "日常_灵塔：绿瓶外层仍未回世界，点击左下角「世界」", phase="daily_lingta_exit_green_bottle_outer", current_scene=scene_id)
                    self._log_locked("action", "日常_灵塔：点击绿瓶左下角「世界」")
                runtime.click_frame_point(20, x, y)
                clicked_outer_world = True
                continue
            if time.monotonic() - start >= 18.0:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"日常_灵塔：退出绿瓶后未回到世界，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵塔：等待绿瓶返回世界，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_lingta_wait_green_bottle_world",
                    current_scene=scene_id,
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
            raise RuntimeError("日常_灵塔：日常列表未找到「混沌灵塔」任务")
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
            raise RuntimeError("日常_灵塔：日常列表未找到「混沌灵塔」任务")
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
        scene_id, _score, frame = runtime.current_scene([203, 202, 201, 200, 199, 198, 197, 69, 34], update=True)
        text = runtime.ocr_text(frame)
        if scene_id == 197 and not self._daily_xianyuan_text_is_people_list(text):
            if self._daily_xianyuan_text_is_daily_list(text):
                scene_id = 69
            else:
                scene_id = None
        if scene_id in {200, 201, 202, 203}:
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
        if scene_id in {200, 201, 202, 203}:
            return (yield from self._run_daily_xianyuan_from_challenge_state(ctx, stop_event, payload, int(scene_id)))
        if scene_id == 199:
            return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
        if scene_id == 198:
            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
        if scene_id == 197:
            return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
        raise RuntimeError(f"日常_挑战仙缘：入口点击后回到 #{scene_id or 'unknown'}，尚未完成挑战流程，不能按完成处理")

    def _daily_assistant_text_is_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if re.search(r"同游结果|同游消耗|总共获得宝物|查看下一个|点击空白处关闭", compact):
            return False
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
            lines = self._cached_ocr_lines(ctx, frame)
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
            raise RuntimeError(f"{task_label}：#69 日常列表未找到入口，不能按完成处理")

        scene_id, score, after_text = yield from self._wait_unsupported_daily_entry_after_click(ctx, stop_event, payload, task_label=task_label)
        raise RuntimeError(
            f"{task_label}：已点击 #69 入口，但后续业务状态机尚未迁移；"
            f"当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%，OCR={after_text[:120]}。"
            f"{missing_assets_message}"
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
        self._log("action", f"日常_拜谒：{reason}，调用通用场景移动返回 #34")
        yield from runtime.goto_view(34)
        scene_id, _score, _frame = runtime.current_scene([34], update=True)
        if scene_id == 34:
            self._log("success", "日常_拜谒：已通过通用 goto 返回 #34 世界，闭环完成")
            return "success"
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
                    raise RuntimeError("日常_拜谒：#69 日常列表未找到「拜谒」入口")
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
                lines = runtime.ocr_lines_in_shapes(264, ["识别区"], frame_data_url=frame)
                matches = [line for line in lines if keyword in _sanitize_ocr_text(line.get("text"))]
                if matches:
                    line = sorted(matches, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))[0]
                    target_box = self._ocr_match_resolved_box(line, keyword, "contains") or line
                    x = float(target_box.get("x") or 0) + float(target_box.get("w") or 0) / 2
                    y = float(target_box.get("y") or 0) + float(target_box.get("h") or 0) / 2
                    text = _sanitize_ocr_text(line.get("text"))
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

    def _baiye_target_box_from_words(self, words: list[dict[str, Any]], target: str) -> dict[str, float] | None:
        def resolved_sub_box(x: float, y: float, w: float, h: float, text: str, start: int, length: int) -> dict[str, float]:
            raw_width = max(1.0, w / max(1, len(text)))
            glyph_width = min(raw_width, max(1.0, h * 1.4))
            if raw_width > h * 2.0 and start + length >= len(text):
                left = x + w - glyph_width * length
            else:
                left = x + glyph_width * start
            return {"x": left, "y": y, "w": glyph_width * length, "h": h}

        fragments: list[dict[str, Any]] = []
        for word in sorted(words, key=lambda item: (int(item.get("line_index") or 0), float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(word.get("text"))
            if not text:
                continue
            if "法则" in text:
                continue
            x = float(word.get("x") or 0)
            y = float(word.get("y") or 0)
            w = float(word.get("w") or 0)
            h = float(word.get("h") or 0)
            if target in text:
                start = text.index(target)
                return resolved_sub_box(x, y, w, h, text, start, len(target))
            fragments.append({"text": text, "x": x, "y": y, "w": w, "h": h})
        joined = "".join(fragment["text"] for fragment in fragments)
        start = joined.find(target)
        if start < 0:
            return None
        end = start + len(target)
        cursor = 0
        boxes: list[dict[str, float]] = []
        for fragment in fragments:
            text = str(fragment["text"])
            next_cursor = cursor + len(text)
            if next_cursor <= start:
                cursor = next_cursor
                continue
            if cursor >= end:
                break
            local_start = max(0, start - cursor)
            local_end = min(len(text), end - cursor)
            boxes.append(resolved_sub_box(
                float(fragment["x"]),
                float(fragment["y"]),
                float(fragment["w"]),
                float(fragment["h"]),
                text,
                local_start,
                max(1, local_end - local_start),
            ))
            cursor = next_cursor
        if not boxes:
            return None
        left = min(box["x"] for box in boxes)
        top = min(box["y"] for box in boxes)
        right = max(box["x"] + box["w"] for box in boxes)
        bottom = max(box["y"] + box["h"] for box in boxes)
        return {"x": left, "y": top, "w": max(1.0, right - left), "h": max(1.0, bottom - top)}

    def _baiye_target_box_from_lines(self, lines: list[dict[str, Any]], target: str) -> tuple[dict[str, float], str] | None:
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if "法则" in text:
                continue
            if target not in text:
                continue
            box = self._ocr_match_resolved_box(line, target, "contains")
            if box is not None:
                return box, text
        return None

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
            words = runtime.ocr_words_in_shapes(
                265,
                ["识别区"],
                frame_data_url=frame,
                options={
                    "text_det_thresh": float(payload.get("baiye_text_det_thresh") or 0.25),
                    "text_det_box_thresh": float(payload.get("baiye_text_det_box_thresh") or 0.45),
                    "text_det_unclip_ratio": float(payload.get("baiye_text_det_unclip_ratio") or 1.2),
                },
            )
            target_box = self._baiye_target_box_from_words(words, target)
            source_text = "".join(_sanitize_ocr_text(word.get("text")) for word in words)
            if target_box is None:
                lines = runtime.ocr_lines_in_shapes(265, ["识别区"], frame_data_url=frame)
                line_match = self._baiye_target_box_from_lines(lines, target)
                if line_match is not None:
                    target_box, source_text = line_match
                elif lines:
                    source_text = "".join(_sanitize_ocr_text(line.get("text")) for line in lines)
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
            raise RuntimeError(f"{task_label}：#69 日常列表未找到入口")

        yield from self._wait_daily_youli_home(ctx, stop_event, timeout=18.0, label="日常_游历：等待修仙传游历 #228")
        yield from self._open_daily_youli_purchase(ctx, stop_event, payload, image228, image229, image233, task_label=task_label)
        return (yield from self._click_daily_youli_last_region(ctx, stop_event, payload, image228, image236, image237, task_label=task_label))
