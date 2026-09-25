"""日常首领的独立执行组件。

首领准入、挑战循环、奖励耗尽、刷新等待及后续任务触发集中在本模块。
DailyFoundationTaskMixin 只组合此组件；场景入口与通用清理由宿主提供。
拆分保留原有方法和真实执行语义，不建立另一套运行或恢复协议。
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import View
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.ocr_spatial import group_ocr_tokens
from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from backend.core.fanxiu.data_annotation.tasks.daily_observations import (
    parse_daily_boss_cd_seconds,
    parse_daily_boss_cd_seconds_from_six_digits,
    parse_daily_boss_reward_remaining,
)
from backend.core.fanxiu.data_annotation.tasks.daily_boss_scan import (
    DAILY_BOSS_FIND_MAX_SCROLLS,
    DAILY_BOSS_FIND_TIMEOUT_SECONDS,
    daily_boss_list_page_fingerprint as _daily_boss_list_page_fingerprint,
    daily_boss_scan_bound_reason as _daily_boss_scan_bound_reason,
)
from backend.core.fanxiu.data_annotation.tasks.daily_boss_cd_wait import (
    DAILY_BOSS_CD_LEAD_SECONDS,
    DAILY_BOSS_CD_MIN_RECHECK_SECONDS,
    DAILY_BOSS_CD_UNREADABLE_TIMEOUT_SECONDS,
    DAILY_BOSS_CD_WAIT_CAP_SECONDS,
    daily_boss_cd_early_recheck_seconds,
    daily_boss_cd_wait_decision,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.fanxiu.data_annotation.behavior_tree_executor import BehaviorTreeContext


def _daily_boss_cd_seconds_from_text(text: Any) -> int | None:
    """Parse a watched-boss refresh countdown from OCR text without guessing."""

    seconds = parse_daily_boss_cd_seconds_from_six_digits(text)
    if seconds is None:
        seconds = parse_daily_boss_cd_seconds(text)
    return seconds


class DailyBossTaskMixin:
    """首领流程与完成判据；宿主提供日常入口、执行上下文和调度写回。"""

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
            self._trigger_daily_boss_followups(
                completed_at=job_now(),
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

    def _trigger_daily_boss_followups(self, *, completed_at: datetime) -> None:
        """Boss completion wakes rewards now and experience one minute later.

        Completed successors are not repeated within the same 05:00 game day.
        The scheduler owns execution; this callback only installs triggers.
        """
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import (
            read_scheduler_tasks, set_scheduler_task_next_time,
        )

        cycle_start = completed_at.replace(hour=5, minute=0, second=0, microsecond=0)
        if completed_at < cycle_start:
            cycle_start -= timedelta(days=1)
        tasks = {str(t.get("task_type") or ""): t for t in read_scheduler_tasks(now=job_now())}
        # A successful observation may only schedule another boss check.
        # Only its persisted next-day trigger proves the whole daily quota done.
        boss = tasks.get("daily_boss", {})
        if self._parse_scheduler_datetime(boss.get("next_time")) != cycle_start + timedelta(days=1):
            return
        for task_type, label, delay in (
            ("daily_task_rewards", "日常_任务奖励", 0),
            ("daily_experience", "日常_经验", 1),
        ):
            task = tasks.get(task_type, {})
            finished_at = self._parse_scheduler_datetime(task.get("finished_at"))
            if (task.get("last_result") == "success" and finished_at is not None
                    and cycle_start <= finished_at <= completed_at):
                continue
            trigger_time = set_scheduler_task_next_time(
                label, completed_at + timedelta(minutes=delay), now=job_now(),
            )
            self._log("success", f"日常_首领：已设置{label}触发时间 {trigger_time}")

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

        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        current_text = context.ocr_text(_frame)
        if (yield from self._close_daily_boss_item_detail_if_present(ctx, context, stop_event, _frame, current_text)):
            _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            current_text = context.ocr_text(_frame)
        if (yield from self._close_daily_boss_storage_bag_if_present(ctx, context, stop_event, _frame, current_text)):
            _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
            (scene_id, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            current_text = context.ocr_text(_frame)
        if self._daily_boss_done_text(current_text):
            return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
        if self._daily_boss_combat_in_progress_text(current_text):
            return (yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload))
        # Boss-list/detail business OCR is stronger than an unrelated scene
        # identity.  The live boss list can be falsely classified as #336
        # because both pages contain a similar top-level ``首领`` image.  Do
        # not hand that proven boss page to generic scene navigation.
        if self._daily_boss_text_is_detail(current_text):
            scene_id = 179
        elif self._daily_boss_text_is_list(current_text):
            scene_id = 178
        elif scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if scene_id == 180:
                return (yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload))
            if scene_id == 181:
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))

        if scene_id != 179:
            if scene_id != 178:
                if scene_id != 69:
                    world_text = context.ocr_text(_frame)
                    scene_id = yield from self._enter_daily_from_world_like(
                        ctx,
                        context,
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        status = yield from context.open_daily_entry(
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
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
                remaining = parse_daily_boss_reward_remaining(context.ocr_text(update=True))
        if remaining == 0 and not runtime_remaining_authoritative:
            # A single OCR ``0`` must not suppress the whole day's boss job.  The
            # counter is small and animated; a bad crop/read previously wrote a
            # cross-day retry even while the daily ledger still showed 0/3.
            # Confirm it from a newly captured frame before treating it as the
            # authoritative completion signal.
            confirm_frame = context.cur_frame(update=True)
            confirm_text = context.ocr_text_in_shapes(
                View(image178),
                ("剩余奖励次数",),
                padding=12,
                frame_data_url=confirm_frame,
            )
            confirmed_remaining = parse_daily_boss_reward_remaining(confirm_text)
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
        if (
            runtime_snapshot.get("complete") is True
            and runtime_snapshot.get("big_boss_dead") is True
            and int(runtime_snapshot.get("normal_boss_alive_count") or 0) > 0
        ):
            return (
                yield from self._open_available_normal_daily_boss_detail(
                    ctx,
                    context,
                    stop_event,
                    payload,
                )
            )
        if xianjie_shape is not None:
            with self._lock:
                self._set_status_locked("running", "日常_首领：确认仙界页签", phase="daily_boss_open_xianjie", current_scene=178)
                self._log_locked("action", "日常_首领：点击 #178「仙界」页签")
            box = self._box(xianjie_shape, image178)
            context.click_frame_point(
                View(image178),
                float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
                float(box.get("y") or 0) + float(box.get("h") or 0) / 2,
            )
            yield from context.wait_action_settle(1.5)
            yield from self._wait_daily_boss_list(ctx, stop_event, timeout=12.0, label="日常_首领：等待仙界首领列表 #178")

        max_scrolls = max(
            0,
            min(
                50,
                self._payload_int(
                    payload,
                    "daily_boss_find_max_scrolls",
                    default=DAILY_BOSS_FIND_MAX_SCROLLS,
                ),
            ),
        )
        find_timeout_seconds = max(
            10.0,
            min(
                600.0,
                float(payload.get("daily_boss_find_timeout_seconds") or DAILY_BOSS_FIND_TIMEOUT_SECONDS),
            ),
        )
        find_deadline = time.monotonic() + find_timeout_seconds
        seen_page_fingerprints: set[str] = set()
        scroll_index = 0
        while True:
            self._raise_if_stopped(stop_event)
            bound_reason = _daily_boss_scan_bound_reason(
                now=time.monotonic(),
                deadline=find_deadline,
                scroll_count=scroll_index,
                max_scrolls=max_scrolls,
            )
            if bound_reason == "deadline":
                raise TimeoutError(
                    "日常_首领：查找仙界注视中首领超过绝对截止时间 "
                    f"{find_timeout_seconds:g} 秒（已滚动 {scroll_index}/{max_scrolls} 次）"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：查找仙界注视中首领 {scroll_index}/{max_scrolls}",
                    phase="daily_boss_find_watched",
                    current_scene=178,
                )
            frame = context.cur_frame(update=True)
            page_text = context.ocr_text_in_shapes(
                View(image178),
                ("首领列表",),
                frame_data_url=frame,
            )
            page_fingerprint = _daily_boss_list_page_fingerprint(page_text)
            if not page_fingerprint:
                page_fingerprint = context.image_signature_in_shape(
                    View(image178),
                    "首领列表",
                    frame_data_url=frame,
                )
            bound_reason = _daily_boss_scan_bound_reason(
                now=time.monotonic(),
                deadline=find_deadline,
                scroll_count=scroll_index,
                max_scrolls=max_scrolls,
                page_fingerprint=page_fingerprint,
                seen_fingerprints=seen_page_fingerprints,
            )
            if bound_reason == "deadline":
                raise TimeoutError(
                    "日常_首领：查找仙界注视中首领超过绝对截止时间 "
                    f"{find_timeout_seconds:g} 秒（已滚动 {scroll_index}/{max_scrolls} 次）"
                )
            if bound_reason == "repeated_page":
                raise RuntimeError(
                    "日常_首领：查找仙界注视中首领检测到重复列表页指纹，"
                    f"已滚动 {scroll_index}/{max_scrolls} 次；停止扫描以避免振荡"
                )
            if page_fingerprint:
                seen_page_fingerprints.add(page_fingerprint)
            item = context.find_floating_item_by_anchor(
                178,
                "条目",
                "注视中",
                container_shape="首领列表",
                frame_data_url=frame,
            )
            if item is not None:
                cd_status = yield from self._daily_boss_handle_watched_item_cd(
                    context,
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
                context.click_floating_item_field(item, "注视中")
                yield from context.wait_any(
                    {
                        "scene": context.scene_visible(179),
                        "detail": context.ocr_matches(
                            self._daily_boss_text_is_detail,
                            label="日常_首领：首领详情 OCR",
                            preview_chars=120,
                        ),
                    },
                    timeout=45.0,
                    label="日常_首领：等待首领详情 #179",
                )
                return "opened"

            bound_reason = _daily_boss_scan_bound_reason(
                now=time.monotonic(),
                deadline=find_deadline,
                scroll_count=scroll_index,
                max_scrolls=max_scrolls,
                before_scroll=True,
            )
            if bound_reason == "deadline":
                raise TimeoutError(
                    "日常_首领：查找仙界注视中首领超过绝对截止时间 "
                    f"{find_timeout_seconds:g} 秒（已滚动 {scroll_index}/{max_scrolls} 次）"
                )
            if bound_reason == "max_scrolls":
                raise RuntimeError(
                    "日常_首领：查找仙界注视中首领达到最大滚动次数 "
                    f"{max_scrolls}，仍未找到目标；本次作业失败"
                )
            with self._lock:
                self._log_locked(
                    "action",
                    f"日常_首领：未找到「注视中」，滚动首领列表 {scroll_index + 1}/{max_scrolls}",
                )
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

    def _open_available_normal_daily_boss_detail(
        self,
        ctx: dict[str, Any],
        context: Any,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        """Open one equivalent low-realm boss after Runtime proves availability."""

        image178 = ctx.get("images", {}).get(178)
        if not isinstance(image178, dict):
            raise RuntimeError("日常_首领：缺少 #178 首领列表，无法选择普通首领")
        tabs_shape = self._find_shape(image178, "界面页签")
        list_shape = self._find_shape(image178, "首领列表")
        if tabs_shape is None or list_shape is None:
            raise RuntimeError("日常_首领：缺少 #178 页签或首领列表标注")

        frame = context.cur_frame(update=True)
        human_line = next(
            (
                line
                for line in self._daily_boss_ocr_lines_in_shape(
                    context, image178, tabs_shape, frame
                )
                if "人界"
                in re.sub(r"\s+", "", str(line.get("text") or ""))
            ),
            None,
        )
        if human_line is None:
            raise RuntimeError("日常_首领：#178 页签区未读到「人界」")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_首领：大首领已死亡，切到人界选择存活普通首领",
                phase="daily_boss_open_human_normal",
                current_scene=178,
            )
            self._log_locked("action", self._status["message"])
        context.click_frame_point(
            View(image178),
            float(human_line["x"]) + float(human_line["w"]) / 2,
            float(human_line["y"]) + float(human_line["h"]) / 2,
        )

        deadline = time.monotonic() + 25.0
        map_lines: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            yield from context.wait_action_settle(1.5)
            frame = context.cur_frame(update=True)
            map_lines = self._daily_boss_ocr_lines_in_shape(
                context, image178, list_shape, frame
            )
            if any(
                "当前首领" in str(line.get("text") or "")
                for line in map_lines
            ):
                break
        else:
            raise RuntimeError("日常_首领：切到人界后 25 秒未加载地图卡片")

        for scroll_index in range(5):
            candidate = self._daily_boss_available_map_line(map_lines)
            if candidate is not None:
                title = str(candidate.get("text") or "").strip()
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：选择无刷新 CD 的普通地图「{title}」",
                        phase="daily_boss_click_normal_map",
                        current_scene=178,
                    )
                    self._log_locked("action", self._status["message"])
                context.click_frame_point(
                    View(image178),
                    float(candidate["x"]) + float(candidate["w"]) / 2,
                    float(candidate["y"]) + float(candidate["h"]) / 2,
                )
                landed = yield from context.wait_scene(
                    [179],
                    wait=45.0,
                    label="日常_首领：等待普通首领详情 #179",
                )
                if landed.id != 179:
                    raise RuntimeError(
                        f"日常_首领：普通地图落点为 #{landed.id}，不是 #179，停止挑战"
                    )
                return "opened"

            if scroll_index >= 4:
                break
            changed = yield from self._scroll_shape_content_changed(
                ctx, image178, list_shape, stop_event
            )
            if not changed:
                break
            frame = context.cur_frame(update=True)
            map_lines = self._daily_boss_ocr_lines_in_shape(
                context, image178, list_shape, frame
            )
        raise RuntimeError(
            "日常_首领：Runtime 仍有存活普通首领，但人界可见地图均为未知或刷新 CD"
        )

    def _daily_boss_ocr_lines_in_shape(
        self,
        context: Any,
        image: dict[str, Any],
        shape: dict[str, Any],
        frame: str,
    ) -> list[dict[str, Any]]:
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        lines: list[dict[str, Any]] = []
        for line in group_ocr_tokens(context.full_frame_ocr_tokens(frame)):
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            if left <= x + w / 2 <= right and top <= y + h / 2 <= bottom:
                lines.append(dict(line))
        return sorted(lines, key=lambda line: float(line.get("y") or 0))

    def _daily_boss_available_map_line(
        self,
        lines: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        # Click the card's explicit boss field, never an arbitrary preceding
        # line: a scrolling world announcement can sit above the first card.
        for index, line in enumerate(lines):
            leader = re.sub(r"\s+", "", str(line.get("text") or ""))
            if "当前首领" not in leader:
                continue
            following = [
                re.sub(r"\s+", "", str(item.get("text") or ""))
                for item in lines[index + 1 : index + 4]
            ]
            realm = next(
                (text for text in following if "首领境界" in text), ""
            )
            if not realm:
                continue
            if "未知" in leader or "后刷新" in leader:
                continue
            return line
        return None

    def _daily_boss_handle_watched_item_cd(
        self,
        context: Any,
        item: Any,
        payload: dict[str, Any],
        stop_event: threading.Event,
        *,
        frame_data_url: str | None = None,
    ):
        refresh_text = context.read_floating_item_field(item, "刷新时间", frame_data_url=frame_data_url, padding=12)
        if not re.search(r"刷新|时间", _sanitize_ocr_text(refresh_text)):
            return "ready"

        lead_seconds = self._payload_int(
            payload,
            "daily_boss_cd_lead_seconds",
            default=DAILY_BOSS_CD_LEAD_SECONDS,
        )
        wait_cap_seconds = max(
            0.0,
            min(DAILY_BOSS_CD_WAIT_CAP_SECONDS, float(
                payload.get("daily_boss_cd_wait_cap_seconds")
                or DAILY_BOSS_CD_WAIT_CAP_SECONDS
            )),
        )
        unreadable_timeout_seconds = max(
            0.0,
            float(
                payload.get("cd_ocr_timeout_seconds")
                or DAILY_BOSS_CD_UNREADABLE_TIMEOUT_SECONDS
            ),
        )

        last_text = refresh_text
        last_frame_has_content = True
        initial_cd = _daily_boss_cd_seconds_from_text(last_text)
        if initial_cd is not None and initial_cd > lead_seconds:
            # A countdown still lies beyond the lead window: schedule the next
            # check ``lead_seconds`` early so the follow-up run arrives inside
            # the bounded in-list wait instead of naturally landing after the
            # refresh.  Otherwise the caller keeps its return-to-world flow.
            next_time = self._record_daily_boss_recheck_time(
                payload,
                seconds=daily_boss_cd_early_recheck_seconds(
                    initial_cd,
                    lead_seconds=lead_seconds,
                ),
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：注视中首领处于刷新 CD，{last_text}，下次 {next_time}",
                    phase="daily_boss_list_cd",
                    current_scene=178,
                )
                self._log_locked("skip", self._status["message"])
            return "skipped"

        wait_started = time.monotonic()
        unreadable_started = None if initial_cd is not None else wait_started
        missing_streak = 0
        while True:
            self._raise_if_stopped(stop_event)
            wait_elapsed = time.monotonic() - wait_started
            unreadable_elapsed = (
                None
                if unreadable_started is None
                else time.monotonic() - unreadable_started
            )
            identifier_present = bool(
                re.search(r"刷新|时间", _sanitize_ocr_text(last_text))
            )
            parsed_cd = _daily_boss_cd_seconds_from_text(last_text)
            if identifier_present:
                missing_streak = 0
                if parsed_cd is not None:
                    unreadable_started = None
                elif unreadable_started is None:
                    unreadable_started = time.monotonic()
            elif last_frame_has_content:
                missing_streak += 1
            else:
                # A blank/failed OCR frame must never be mistaken for a real
                # disappearance of the refresh field.
                missing_streak = 0
            action, seconds = daily_boss_cd_wait_decision(
                refresh_identifier_present=identifier_present,
                missing_refresh_streak=missing_streak,
                cd_seconds=parsed_cd,
                wait_elapsed_seconds=wait_elapsed,
                unreadable_elapsed_seconds=unreadable_elapsed,
                wait_cap_seconds=wait_cap_seconds,
                unreadable_timeout_seconds=unreadable_timeout_seconds,
                lead_seconds=lead_seconds,
            )
            if action == "ready":
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_首领：注视中首领刷新标识已在列表内确认消失，继续挑战",
                        phase="daily_boss_list_cd_ready",
                        current_scene=178,
                    )
                    self._log_locked("success", self._status["message"])
                return "ready"
            if action in {"recheck_early", "recheck_unreadable", "recheck_floor"}:
                next_time = self._record_daily_boss_recheck_time(
                    payload,
                    seconds=int(seconds or DAILY_BOSS_CD_MIN_RECHECK_SECONDS),
                )
                if action == "recheck_unreadable":
                    message = (
                        "日常_首领：注视中条目有刷新时间但 30 秒未读到 6 位 CD，"
                        f"{last_text}，下次 {next_time}"
                    )
                    phase = "daily_boss_list_cd_unreadable"
                elif action == "recheck_floor":
                    message = (
                        "日常_首领：列表内等待到达 "
                        f"{int(wait_cap_seconds)} 秒且未读到刷新 CD，"
                        f"{last_text}，下次 {next_time}"
                    )
                    phase = "daily_boss_list_cd"
                else:
                    message = (
                        f"日常_首领：注视中首领仍在刷新 CD，{last_text}，下次 {next_time}"
                    )
                    phase = "daily_boss_list_cd"
                with self._lock:
                    self._set_status_locked(
                        "running",
                        message,
                        phase=phase,
                        current_scene=178,
                    )
                    self._log_locked("skip", self._status["message"])
                return "skipped"
            # Stay in the list, but never renew the absolute budget: the cap is
            # anchored to ``wait_started``.  Read the same item from a freshly
            # captured frame every round.
            yield BehaviorTreeStatus.RUNNING
            yield from context.wait_action_settle(2.5)
            context.clear_frame()
            match = yield from context.wait_scene(
                [178], wait=5.0, label="日常_首领：列表内等待刷新"
            )
            if match.scene_id != 178:
                raise RuntimeError("日常_首领：等待刷新时已离开首领列表，保留现场")
            frame = match.frame_data_url or context.cur_frame(update=True)
            last_text = context.read_floating_item_field(
                item,
                "刷新时间",
                frame_data_url=frame,
                padding=12,
            )
            last_frame_has_content = bool(frame) and bool(
                re.sub(r"\s+", "", context.ocr_text(frame))
            )

    def _wait_daily_boss_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from context.wait_scene([178], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
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
        context = self._behavior_tree_context(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        detail_text = context.ocr_text_in_shapes(179, ("神识注视", "剩余奖励次数", "挑战状态"), padding=20)
        remaining = parse_daily_boss_reward_remaining(detail_text)
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
            # Navigation status text ("等待场景") would otherwise overwrite the
            # business conclusion.  Restore the terminal message after the
            # successfully returned trip so the Scheduler last_message reports
            # "次数用尽" instead of a scene wait.
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：今日奖励次数已用尽，下次 {next_time}",
                    phase="daily_boss_no_reward_detail",
                    current_scene=34,
                )
                self._log_locked("success", self._status["message"])
            return "success"

        cd_seconds = parse_daily_boss_cd_seconds(detail_text)
        if cd_seconds is not None:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=max(60, cd_seconds))
            self._log("skip", f"日常_首领：首领详情仍在 CD，{cd_seconds}s 后复查，下次 {next_time}")
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            # Preserve this run's deferral semantics after the trip home.  The
            # remaining count is taken from this frame only (unknown stays
            # unknown); CD seconds and next_time describe a recheck, not a
            # completion, and must survive the navigation status write.
            remaining_text = "未知" if remaining is None else str(int(remaining))
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_首领：首领仍在冷却，本次未完成，等待复查；"
                    f"剩余奖励次数 {remaining_text}，冷却 {int(cd_seconds)}s，"
                    f"下次复查 {next_time}",
                    phase="daily_boss_cooldown",
                    current_scene=34,
                )
                self._log_locked("skip", self._status["message"])
            return "skipped"

        view179 = context.get_view(179)
        challenge_shape = view179.get_shape("前往挑战") if isinstance(view179, View) else None
        if challenge_shape is None:
            raise RuntimeError("缺少 #179「前往挑战」标注，无法挑战首领")

        if remaining is None and "前往挑战" not in detail_text:
            fallback_seconds = int(payload.get("fallback_seconds") or 300)
            next_time = (job_now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
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
        context.click_frame_point(179, click_x, click_y)
        post_result = yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload)
        return post_result

    def _wait_daily_boss_after_challenge(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        deadline = time.monotonic() + float(payload.get("post_challenge_wait_seconds") or 300)
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
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
            scene_id, score, frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
            if scene_id == 181:
                self._log(
                    "detail",
                    "首领结算判据：scene=181 场景终态直接判定本轮完成（来源=场景编号）",
                )
                return (yield from self._finish_daily_boss_round_after_done(ctx, context, stop_event, payload))
            if scene_id == 180:
                saw_fighting = True
                current_text = self._daily_boss_status_text_from_frame(ctx, frame)
                if self._daily_boss_done_text(current_text):
                    self._log(
                        "detail",
                        "首领结算判据："
                        f"scene={scene_id} text={current_text[:300]}",
                    )
                    return (yield from self._finish_daily_boss_round_after_done(ctx, context, stop_event, payload))
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
                self._log(
                    "detail",
                    "首领结算判据："
                    f"scene={scene_id} text={current_text[:300]}",
                )
                return (yield from self._finish_daily_boss_round_after_done(ctx, context, stop_event, payload))
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
                    f"big_boss_reward_remaining={runtime_snapshot.get('big_boss_reward_remaining')} "
                    f"kill_reward_remaining={runtime_snapshot.get('kill_reward_remaining')} "
                    f"big_boss_dead={runtime_snapshot.get('big_boss_dead')} "
                    f"normal_boss_alive_count={runtime_snapshot.get('normal_boss_alive_count')} "
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
                    continued = yield from self._continue_daily_boss_rounds_if_available(
                        ctx,
                        stop_event,
                        payload,
                        runtime_snapshot,
                    )
                    if continued is not None:
                        return continued
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
                    lambda: self._return_daily_boss_to_world(
                        ctx,
                        stop_event,
                        allow_post_boss_transition=True,
                    ),
                    label="日常_首领",
                    repeat_risk="重复挑战",
                )
                # Same normal-cleanup overwrite as the done branch: restore this
                # branch's verified runtime-delta message/phase, keeping the
                # post-cleanup scene.
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：{source}，确认本轮已经结算；下次 {next_time}",
                        phase="daily_boss_done_by_runtime_delta",
                        current_scene=self._status.get("current_scene"),
                    )
                    self._log_locked(result if result == "success" else "skip", self._status["message"])
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

    def _continue_daily_boss_rounds_if_available(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        runtime_snapshot: dict[str, Any],
    ):
        try:
            remaining = int(runtime_snapshot.get("reward_remaining"))
            normal_alive = int(
                runtime_snapshot.get("normal_boss_alive_count") or 0
            )
        except (TypeError, ValueError):
            return None
        rounds = int(payload.get("_daily_boss_rounds_completed") or 0)
        if remaining <= 0 or normal_alive <= 0 or rounds >= 3:
            return None
        payload["_daily_boss_rounds_completed"] = rounds + 1
        payload.pop("_daily_boss_challenge_remaining", None)
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：本轮结算后仍有 {remaining} 次奖励，继续选择下一个普通首领",
                phase="daily_boss_continue_normal_round",
            )
            self._log_locked("success", self._status["message"])
        yield from self._return_daily_boss_to_world(
            ctx,
            stop_event,
            allow_post_boss_transition=True,
        )
        return (
            yield from self._execute_daily_boss_task_flow(
                ctx,
                stop_event,
                payload,
            )
        )

    def _complete_daily_boss_from_done_frame(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        context = self._behavior_tree_context(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        return (yield from self._finish_daily_boss_round_after_done(ctx, context, stop_event, payload))

    def _finish_daily_boss_round_after_done(self, ctx: dict[str, Any], context: Any, stop_event: threading.Event, payload: dict[str, Any]) -> str:
        next_time, source, completed = yield from self._record_daily_boss_next_time_after_done(
            ctx,
            stop_event,
            payload,
        )
        if not completed:
            continued = yield from self._continue_daily_boss_rounds_if_available(
                ctx,
                stop_event,
                payload,
                self._daily_boss_runtime_snapshot(payload),
            )
            if continued is not None:
                return continued
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
            lambda: self._return_daily_boss_to_world(
                ctx,
                stop_event,
                allow_post_boss_transition=True,
            ),
            label="日常_首领",
            repeat_risk="重复挑战",
        )
        # The trip home publishes transient navigation status ("读取场景及
        # 文本/等待全局场景").  The safe-cleanup wrapper restores business status
        # only when cleanup raises; after any normal cleanup it leaves that
        # navigation text as the final last_message, hiding the verified
        # conclusion.  Restore the already-computed done message, keeping the
        # post-cleanup scene (the wrapper may have succeeded without reaching
        # #34).
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：本轮挑战已结束；{source}；下次 {next_time}",
                phase="daily_boss_done",
                current_scene=self._status.get("current_scene"),
            )
            self._log_locked(result, self._status["message"])
        return result

    def _leave_daily_boss_fighting_and_recheck_rewards(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        reason: str,
    ) -> str:
        context = self._behavior_tree_context(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        view180 = context.get_view(180)
        leave_shape = view180.get_shape("离开") if isinstance(view180, View) else None
        if leave_shape is None:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            self._log("skip", f"日常_首领：{reason}，但缺少 #180「离开」标注，{next_time} 复查")
            return "skipped"
        with self._lock:
            self._set_status_locked("running", f"日常_首领：{reason}，离开后复核奖励次数", phase="daily_boss_leave_stuck_20", current_scene=180)
            self._log_locked("action", "日常_首领：点击 #180「离开」")
        leave_shape.click(context)
        opened = yield from self._open_daily_boss_list_after_leaving_fight(ctx, context, stop_event)
        if opened == "done":
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            return "success"
        if not opened:
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
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

    def _return_daily_boss_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        allow_post_boss_transition: bool = False,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            with self._lock:
                self._log_locked("warning", "日常_首领：缺少资产树路径，无法收尾回世界 #34")
            return "skipped"
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        if (yield from self._close_daily_boss_item_detail_if_present(ctx, context, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        if (yield from self._close_daily_boss_storage_bag_if_present(ctx, context, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        transition_forward_point = (
            self._daily_boss_transition_forward_point(context, _frame)
            if scene_id is None and allow_post_boss_transition
            else None
        )
        if transition_forward_point is not None:
            x, y = transition_forward_point
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            reference = images.get(314) if isinstance(images, dict) else None
            if not isinstance(reference, dict):
                raise RuntimeError("日常_首领：结算后过渡页已确认，但缺少 #314 尺寸参考，拒绝点击")
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_首领：结算后过渡页唯一命中 OCR「前往」，确认进入活动入口再回世界",
                    phase="daily_boss_transition_forward",
                    current_scene=None,
                )
                self._log_locked("action", self._status["message"])
            context.click_frame_point(reference, x, y)
            deadline = time.monotonic() + 60.0
            while True:
                self._raise_if_stopped(stop_event)
                yield from context.wait_action_settle(2.0)
                scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(
                    ctx,
                    context,
                    update=True,
                )
                if scene_id in {34, 661}:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "日常_首领：点击结算过渡页「前往」后 60 秒内未落到 #661/#34；"
                        "为防止转场期间重复点击，已中断"
                    )
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
                yield from context.wait_action_settle(3.0)
                scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(
                    ctx, context, update=True
                )
                if scene_id == 34:
                    yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
                    return "success"
                if scene_id is not None:
                    break
        if scene_id == 34:
            yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
            return "success"
        if allow_post_boss_transition and scene_id in {186, 678}:
            result_view = context.get_view(scene_id)
            leave_shape = (
                result_view.get_shape("离开")
                if isinstance(result_view, View)
                else None
            )
            if leave_shape is None:
                raise RuntimeError(
                    f"日常_首领：战后 #{scene_id} 缺少正式「离开」标注"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：从战后 #{scene_id} 点击「离开」",
                    phase="daily_boss_leave_result_scene",
                    current_scene=scene_id,
                )
                self._log_locked("action", self._status["message"])
            leave_shape.click(context)
            landing = yield from context.wait_scene(
                [34,
                178],
                wait=30.0,
                label="日常_首领：等待战后离开稳定场景（确认弹窗由 Layer 0 处理）",
            )
            landing_id = getattr(landing, "id", landing)
            if landing_id == 34:
                with self._lock:
                    self._status.update(
                        {"current_scene": 34, "updated_at": time.time()}
                    )
                return "success"
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(
                ctx, context, update=True
            )
        image178 = images.get(178)
        back_shape = self._find_shape(image178, "返回") if isinstance(image178, dict) else None
        if (
            self._daily_boss_exit_list_evidence(context, scene_id, _frame, _text)
            and isinstance(image178, dict)
            and back_shape is not None
        ):
            box = self._box(back_shape, image178)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            with self._lock:
                self._set_status_locked("running", "日常_首领：从首领列表返回世界", phase="daily_boss_return_from_list", current_scene=178)
                self._log_locked("action", "日常_首领：点击 #178「返回」")
            context.click_frame_point(image178, x, y)
            landing = yield from context.wait_scene(
                [34],
                wait=30.0,
                label="日常_首领：从首领列表返回世界 #34",
            )
            if getattr(landing, "id", landing) == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                return "success"
        with self._lock:
            self._set_status_locked("running", "日常_首领：收尾回到世界 #34", phase="daily_boss_return_world", current_scene=scene_id)
            self._log_locked("action", "日常_首领：完成后按场景图回到 #34 世界")
        try:
            self._clear_tick_frame(ctx)
            context.clear_frame()
            ctx["_go_scene_unknown_transition_guard"] = {
                "reference_scene_id": 314,
                "similarity_threshold": 94.0,
                "wait_seconds": 120.0,
                "phase": "daily_boss_wait_mozu_world_transition",
                "label": "魔道入侵回城动画",
            }
            try:
                # Battle exit can hide all HUD controls while loading longer
                # than the generic 30-second navigation budget.
                yield from context.go_scene(34, wait=90.0)
            finally:
                ctx.pop("_go_scene_unknown_transition_guard", None)
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
            if scene_id != 34:
                raise RuntimeError(f"回世界后仍识别为 #{scene_id or 'unknown'}")
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
        except Exception as exc:
            with self._lock:
                self._log_locked("warning", f"日常_首领：收尾回世界 #34 失败：{exc}")
            # Leaving #186/#181 can enter a long, control-free transition.
            # ``go_scene`` may exhaust its generic unknown budget after the
            # formal Leave/Confirm actions have already succeeded.  From this
            # point a second click is unsafe; only wait for the authoritative
            # world identity to settle before surfacing the original error.
            try:
                landing = yield from context.wait_scene(
                    [34],
                    wait=120.0,
                    label="日常_首领：离开确认后等待长加载落到世界 #34",
                )
                if getattr(landing, "id", landing) == 34:
                    with self._lock:
                        self._status.update({"current_scene": 34, "updated_at": time.time()})
                        self._log_locked("success", "日常_首领：长加载结束，已回到世界 #34")
                    return "success"
            except Exception as settle_exc:
                with self._lock:
                    self._log_locked("warning", f"日常_首领：继续等待长加载仍未回世界：{settle_exc}")
                # The long transition can settle on the boss list while its
                # weak top-title image is misidentified as the event card
                # #336.  Re-read business semantics after the wait: the boss
                # list OCR plus its existing #178 Return shape is stronger
                # evidence than that conflicting scene identity.  This is a
                # safe exit from the current page, not a scene-graph repair.
                scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(
                    ctx, context, update=True
                )
                if (
                    self._daily_boss_exit_list_evidence(context, scene_id, _frame, _text)
                    and isinstance(image178, dict)
                    and back_shape is not None
                ):
                    box = self._box(back_shape, image178)
                    x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
                    y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            "日常_首领：长等待后 OCR 确认为首领列表，使用 #178「返回」回世界",
                            phase="daily_boss_recover_list_after_transition",
                            current_scene=178,
                        )
                        self._log_locked("action", self._status["message"])
                    context.click_frame_point(image178, x, y)
                    landing = yield from context.wait_scene(
                        [34],
                        wait=30.0,
                        label="日常_首领：从长等待后的首领列表返回世界 #34",
                    )
                    if getattr(landing, "id", landing) == 34:
                        with self._lock:
                            self._status.update(
                                {"current_scene": 34, "updated_at": time.time()}
                            )
                        return "success"
            raise
        return "success"

    def _daily_boss_transition_forward_point(
        self,
        context: BehaviorTreeContext,
        frame: str | None,
    ) -> tuple[float, float] | None:
        """Locate the unique post-boss ``前往`` button from the live OCR frame.

        The boss completion flow can land on an unannotated activity splash
        before the known #661 entrance.  Requiring the splash identity text and
        exactly one standalone ``前往`` line keeps this narrower than a generic
        OCR click and avoids teaching the scene graph a transient frame.
        """

        if (
            not isinstance(frame, str)
            or not frame
            or not hasattr(context, "full_frame_ocr_tokens")
        ):
            return None
        lines = group_ocr_tokens(context.full_frame_ocr_tokens(frame))
        normalized_lines = [
            re.sub(r"\s+", "", str(item.get("text") or ""))
            for item in lines
        ]
        compact = "".join(normalized_lines)
        if (
            "活动规则" not in compact
            or "雁行布陈" not in compact
            or not any(re.search(r"天地.局", text) for text in normalized_lines)
        ):
            return None
        matches = [
            item
            for item, text in zip(lines, normalized_lines)
            if text == "前往"
            and 300 <= float(item.get("x") or -1) <= 600
            and 1200 <= float(item.get("y") or -1) <= 1450
            and float(item.get("w") or 0) > 0
            and float(item.get("h") or 0) > 0
        ]
        if len(matches) != 1:
            return None
        match = matches[0]
        return (
            float(match.get("x") or 0) + float(match.get("w") or 0) / 2,
            float(match.get("y") or 0) + float(match.get("h") or 0) / 2,
        )

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
        context: BehaviorTreeContext,
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
        context.click_frame_point(image250, x, y)
        yield from context.wait_action_settle(2.0)
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
        context: BehaviorTreeContext,
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
        context.click_frame_point(image249, x, y)
        yield from context.wait_action_settle(2.0)
        return True

    def _open_daily_boss_list_after_leaving_fight(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
    ):
        try:
            yield from context.wait_scene([178], wait=8.0, label="日常_首领：等待首领列表 #178")
            return True
        except Exception:
            pass
        if (yield from self._close_daily_boss_reward_result_if_present(ctx, context, stop_event)):
            try:
                yield from context.wait_scene([178], wait=8.0, label="日常_首领：等待奖励页关闭后回到首领列表 #178")
                return True
            except Exception:
                pass
        scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        if (yield from self._close_daily_boss_item_detail_if_present(ctx, context, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        if (yield from self._close_daily_boss_storage_bag_if_present(ctx, context, stop_event, _frame, _text)):
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        if scene_id == 178:
            return True
        if scene_id == 181:
            return False
        try:
            if scene_id != 69:
                with self._lock:
                    self._set_status_locked("running", "日常_首领：离开战斗后重新进入日常 #69", phase="daily_boss_reopen_daily_after_leave")
                    self._log_locked("action", "日常_首领：离开战斗后按场景图跳转到 #69")
                yield from context.go_scene(69)
            status = yield from self._open_daily_boss_list_from_daily(ctx, stop_event)
            return "done" if status == "done" else True
        except Exception as exc:
            scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
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
        context: BehaviorTreeContext,
        stop_event: threading.Event,
    ):
        try:
            scene_id, _score, frame, text = yield from self._behavior_tree_context_scene_text(ctx, context, [177, 178, 34], update=True)
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
        context.click_frame_point(image177 if isinstance(image177, dict) else {"width": width, "height": height}, width * 0.5, height * 0.86)
        yield from context.wait_action_settle(2.0)
        return True

    def _record_daily_boss_next_time_after_done(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        context = self._behavior_tree_context(ctx, ctx["asset_tree_path"], stop_event=stop_event)
        scene_id, _score, _frame, _text = yield from self._behavior_tree_context_scene_text(ctx, context, update=True)
        returned_to_list = scene_id == 178
        if scene_id != 178:
            view181 = context.get_view(181)
            leave_shape = view181.get_shape("离开") if isinstance(view181, View) else None
            if leave_shape is not None:
                with self._lock:
                    self._set_status_locked("running", "日常_首领：挑战完成，点击离开回列表读取刷新时间", phase="daily_boss_leave_done", current_scene=181)
                    self._log_locked("action", "日常_首领：点击 #181「离开」")
                leave_shape.click(context)
                try:
                    landing = yield from context.wait_scene(
                        [178,
                        34],
                        wait=120.0,
                        label="日常_首领：离开完成页后等待首领列表 #178 或世界 #34",
                    )
                    returned_to_list = getattr(landing, "id", landing) == 178
                except Exception as exc:
                    with self._lock:
                        self._log_locked("warning", f"日常_首领：离开 #181 后未能回到 #178 读取刷新时间：{exc}")
            else:
                with self._lock:
                    self._log_locked("warning", "日常_首领：缺少 #181「离开」标注，无法回 #178 首领列表读取刷新时间")
        if returned_to_list:
            # The list scheduler decision and the completion decision consume
            # the same post-battle fact.  Keep that one fresh snapshot local to
            # this branch instead of traversing BossData twice back-to-back.
            runtime_snapshot = self._daily_boss_runtime_snapshot(payload)
            payload["_daily_boss_current_list_runtime_snapshot"] = dict(
                runtime_snapshot
            )
            try:
                next_time, source = self._record_daily_boss_next_time_from_current_list(
                    ctx,
                    payload,
                )
            finally:
                payload.pop("_daily_boss_current_list_runtime_snapshot", None)
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
        runtime_snapshot = payload.pop(
            "_daily_boss_current_list_runtime_snapshot",
            None,
        )
        if not isinstance(runtime_snapshot, dict):
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
                maximum_seconds=None,
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        current_frame = frame if isinstance(frame, str) and frame else context.cur_frame(update=True)
        scene_text = context.ocr_text(current_frame)
        # #186's formal shapes identify the page and the Leave control, but
        # the live refresh countdown is a floating combat field outside those
        # shapes.  Reuse the same frame's shared OCR result and admit only the
        # narrow boss HUD regions proven by the real 900x1600 frame.  This
        # preserves scene identity while avoiding a generic full-screen
        # countdown interpretation.
        hud_texts: list[str] = []
        for item in group_ocr_tokens(context.full_frame_ocr_tokens(current_frame)):
            text = re.sub(r"\s+", "", str(item.get("text") or ""))
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            if (
                250 <= x <= 650
                and 250 <= y <= 550
                and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?后刷新", text)
            ):
                hud_texts.append(text)
            elif 450 <= x <= 800 and 80 <= y <= 650 and any(
                token in text for token in ("伤害", "数据统计", "自动战斗中", "封印")
            ):
                hud_texts.append(text)
            elif 750 <= x <= 900 and 50 <= y <= 350 and "首领" in text:
                hud_texts.append(text)
        return " ".join([scene_text, *hud_texts]).strip()

    def _daily_boss_text_is_list(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return (
            "首领" in normalized
            and "首领境界" in normalized
            and ("剩余奖励次数" in normalized or "掉落记录" in normalized)
        )

    def _daily_boss_exit_list_evidence(
        self,
        context: BehaviorTreeContext,
        scene_id: int | None,
        frame: str | None,
        text: str,
    ) -> bool:
        """Recognize the boss list conservatively enough for its safe Back action.

        The #336 title image can cover the list and reduce scene OCR to only
        ``首领``.  In that narrow conflict, use full-frame OCR as secondary
        navigation evidence.  One realm tab plus one list cue is sufficient;
        Runtime reward/CD facts remain authoritative for business decisions.
        """
        if self._daily_boss_text_is_list(text):
            return True
        normalized = re.sub(
            r"\s+",
            "",
            _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION),
        )
        if scene_id != 336 or "首领" not in normalized or not frame:
            return False
        try:
            rows = group_ocr_tokens(context.full_frame_ocr_tokens(frame))
        except Exception:
            return False
        candidates = [
            re.sub(r"\s+", "", _sanitize_ocr_text(str(row.get("text") or "")))
            for row in rows
        ]
        candidates = [item for item in candidates if item]
        full_text = "".join(candidates)

        def matches(term: str) -> bool:
            return term in full_text or any(
                ocr_name_similarity(term, candidate) >= 0.50
                for candidate in candidates
            )

        has_realm_tab = any(matches(term) for term in ("人界", "灵界", "仙界"))
        has_list_cue = any(
            matches(term)
            for term in ("首领境界", "境界", "剩余奖励", "掉落记录", "神识注视")
        )
        return has_realm_tab and has_list_cue

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
        normalized = _sanitize_ocr_text(text)
        if "封印" in normalized:
            return True
        has_refresh_countdown = bool(
            re.search(r"\d{1,2}:\d{2}(?::\d{2})?后刷新", normalized)
        )
        return has_refresh_countdown and "首领" in normalized and any(
            token in normalized for token in ("伤害", "数据统计")
        )

    def _daily_boss_stuck_map_text(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "离开" in normalized and "数据统计" in normalized and "自动战斗中" not in normalized

    def _record_daily_boss_recheck_time(
        self,
        payload: dict[str, Any],
        *,
        seconds: int,
        maximum_seconds: int | None = 1800,
    ) -> str:
        """Persist the next boss check without over-polling uncertain UI state.

        OCR and incomplete Runtime branches retain the historical 30-minute
        ceiling.  A complete Runtime snapshot may opt out of that ceiling and
        schedule the exact authoritative refresh countdown instead.
        """

        recheck_seconds = max(60, int(seconds))
        if maximum_seconds is not None:
            recheck_seconds = min(max(60, int(maximum_seconds)), recheck_seconds)
        next_time = (job_now() + timedelta(seconds=recheck_seconds)).strftime("%Y-%m-%d %H:%M:%S")
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
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        frame = context.cur_frame(update=True)
        texts: list[str] = []
        if isinstance(image178, dict):
            item = context.find_floating_item_by_anchor(
                178,
                "条目",
                "注视中",
                container_shape="首领列表",
                frame_data_url=frame,
            )
            if item is not None:
                text = context.read_floating_item_field(
                    item,
                    "刷新时间",
                    frame_data_url=frame,
                    padding=12,
                )
            else:
                text = ""
            if text:
                texts.append(text)
                cd_seconds = parse_daily_boss_cd_seconds_from_six_digits(text)
                if cd_seconds is None:
                    cd_seconds = parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0:
                    return cd_seconds, text
            text = context.ocr_text_in_shapes(View(image178), ("首领列表",), padding=8, frame_data_url=frame)
            if text:
                texts.append(text)
                cd_seconds = parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0 and "刷新" in text:
                    return cd_seconds, text
        return None, " ".join(texts)

    def _daily_boss_reward_remaining_from_scene(self, ctx: dict[str, Any], image: dict[str, Any]) -> int | None:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None)
        value, _text = context.ocr_value_in_shapes(
            View(image), ("剩余奖励次数",), padding=12,
            parse_value=parse_daily_boss_reward_remaining,
        )
        return value

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
        now = job_now()
        reset_at = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if reset_at <= now:
            reset_at += timedelta(days=1)
        return reset_at.strftime("%Y-%m-%d %H:%M:%S")
