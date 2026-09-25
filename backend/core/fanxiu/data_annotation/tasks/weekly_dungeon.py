"""周本挑战、战斗结束确认与下周调度。

通用日常入口、识别和离场由组合后的执行器提供；导入不执行游戏动作。
"""
from __future__ import annotations
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_scheduler_tasks
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from backend.core.fanxiu.data_annotation.state import parse_data_annotation_task_time


class WeeklyDungeonTaskMixin:
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
