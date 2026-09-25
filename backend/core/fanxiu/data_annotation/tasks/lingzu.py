"""灵祖挑战：日常入口、挑战推进、奖励完成判据与离场。

跨玩法的世界收尾及日常列表能力仍由宿主执行器提供；本模块集中灵祖
专属场景和重试规则，任务入口为 _execute_daily_lingzu_task。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyxllib.prog import BehaviorTreeStatus

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from ..effective_time import job_now
from ..kernel_scheduler_control import read_scheduler_tasks
from ..ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from ..state import parse_data_annotation_task_time

if TYPE_CHECKING:
    from ..game_context import BehaviorTreeContext


class DailyLingzuTaskMixin:
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
            scene_id, _score = yield from self._wait_expected_scene(
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
        yield from self._ensure_outer_world(ctx, stop_event, label="日常_灵祖")
        with self._lock:
            self._status.update({"current_scene": 34, "updated_at": time.time()})
        return "success"

    def _run_daily_lingzu_challenge(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image184 = ctx.get("images", {}).get(184)
        image185 = ctx.get("images", {}).get(185)
        image187 = ctx.get("images", {}).get(187)
        image188 = ctx.get("images", {}).get(188)
        image189 = ctx.get("images", {}).get(189)
        if not all(isinstance(item, dict) for item in (image184, image185, image187, image188, image189)):
            raise RuntimeError("缺少 #184/#185/#187/#188/#189 灵祖挑战标注，无法挑战灵祖")

        if not hasattr(context, "current_scene") or not hasattr(context, "ocr_text"):
            raise RuntimeError("日常_灵祖要求正式 BehaviorTreeContext 场景接口")
        observer = context
        action_context = context if hasattr(context, "wait_click") else self._behavior_tree_context(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        _wait_scene_match = yield from observer.wait_scene([184, 185, 186, 187, 188, 189, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
        )
        if scene_id == 184:
            go_shape = self._find_shape(image184, "前往")
            if go_shape is None:
                raise RuntimeError("缺少 #184「前往」标注，无法前往战灵长老")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：前往战灵长老", phase="daily_lingzu_go_elder", current_scene=184)
                self._log_locked("action", "日常_灵祖：点击 #184「前往」")
            box = self._box(go_shape, image184)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            action_context.click_frame_point(image184, x, y)
            yield from action_context.wait_action_settle(1.0)
            landing = yield from action_context.wait_scene(
                [187],
                wait=float(payload.get("lingzu_elder_timeout") or 45.0),
                label="日常_灵祖：等待战灵长老 #187",
            )
            scene_id, _score = int(getattr(landing, "id", landing)), 100.0
            frame = observer.cur_frame(update=True)

        if scene_id == 187:
            challenge_shape = self._find_shape(image187, "灵祖挑战")
            if challenge_shape is None:
                raise RuntimeError("缺少 #187「灵祖挑战」标注，无法进入圣雷龙妖祖")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：进入圣雷龙妖祖", phase="daily_lingzu_open_boss", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「灵祖挑战」")
            yield from action_context.wait_click(187, "灵祖挑战")
            landing = yield from action_context.wait_scene(
                [188],
                wait=float(payload.get("lingzu_boss_timeout") or 30.0),
                label="日常_灵祖：等待圣雷龙妖祖 #188",
            )
            scene_id, _score = int(getattr(landing, "id", landing)), 100.0
            frame = observer.cur_frame(update=True)

        if scene_id == 188:
            text = observer.ocr_text(frame)
            if self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示剩余奖励次数 0/1")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            go_shape = self._find_shape(image188, "前往")
            if go_shape is None:
                raise RuntimeError("缺少 #188「前往」标注，无法开始灵祖挑战")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：开始圣雷龙妖祖挑战", phase="daily_lingzu_start_boss", current_scene=188)
                self._log_locked("action", "日常_灵祖：点击 #188「前往」")
            yield from action_context.wait_click(188, "前往")
        elif scene_id == 186:
            self._record_daily_lingzu_done(payload, message="当前已在灵祖奖励完成态")
            yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
            return "success"

        start = time.monotonic()
        skipped = False
        while True:
            self._raise_if_stopped(stop_event)
            observer.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            _wait_scene_match = yield from observer.wait_scene([34, 185, 186, 188, 189], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, observer.frame_data_url or "")
            )
            text = observer.ocr_text(frame)
            if scene_id == 185 or "跳过" in text:
                skip_shape = self._find_shape(image185, "跳过")
                if skip_shape is not None:
                    with self._lock:
                        self._set_status_locked("running", "日常_灵祖：跳过挑战过场", phase="daily_lingzu_skip_cutscene", current_scene=185)
                        self._log_locked("action", "日常_灵祖：点击 #185「跳过」")
                    yield from action_context.wait_click(185, "跳过")
                    skipped = True
                    continue
            if scene_id == 189 or "点击退出" in text:
                exit_shape = self._find_shape(image189, "点击退出")
                if exit_shape is None:
                    raise RuntimeError("缺少 #189「点击退出」标注，无法离开灵祖挑战结算")
                with self._lock:
                    self._set_status_locked("running", "日常_灵祖：退出挑战结算", phase="daily_lingzu_exit_result", current_scene=189)
                    self._log_locked("action", "日常_灵祖：点击 #189「点击退出」")
                yield from action_context.wait_click(189, "点击退出")
                continue
            if scene_id == 186:
                self._record_daily_lingzu_done(payload, message="已回到世界并出现灵祖奖励")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            if scene_id == 188 and self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示挑战次数已消耗")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            if scene_id == 34:
                self._record_daily_lingzu_done(payload, message="挑战后已回到世界")
                yield from self._safe_return_daily_lingzu_to_world_after_done(ctx, stop_event)
                return "success"
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵祖：等待挑战完成，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_lingzu_wait_done",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= 90:
                detail = "，已点击跳过" if skipped else ""
                raise RuntimeError(f"日常_灵祖：等待挑战完成超时{detail}，最后文本：{text[:120]}")

    def _open_daily_lingzu_activity_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找灵祖挑战")
        status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            {
                **payload,
                "max_scrolls": payload.get("lingzu_max_scrolls") or payload.get("max_scrolls") or 10,
            },
            task_label="日常_灵祖",
            title_pattern=r"灵祖",
            progress_can_mark_done=True,
        )
        if status == "open":
            yield from self._wait_scene_id(ctx, stop_event, 183, timeout=18.0, label="日常_灵祖：等待灵祖活动列表 #183")
        if status == "not_found":
            self._record_daily_entry_not_found_retry(
                payload,
                task_id="legacy-daily-lingzu",
                task_type="daily_lingzu",
                label="日常_灵祖",
                entry_label="灵祖",
            )
            return "skipped"
        return status

    def _open_daily_lingzu_detail(
        self,
        ctx: dict[str, Any],
        context: BehaviorTreeContext,
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image183 = ctx.get("images", {}).get(183)
        if not isinstance(image183, dict):
            raise RuntimeError("缺少 #183「灵祖活动列表」标注，无法进入灵祖详情")
        activity_shape = self._find_shape(image183, "灵祖挑战")
        if activity_shape is None:
            raise RuntimeError("缺少 #183「灵祖挑战」标注，无法进入灵祖详情")
        with self._lock:
            self._set_status_locked("running", "日常_灵祖：打开灵祖挑战详情", phase="daily_lingzu_open_detail", current_scene=183)
            self._log_locked("action", "日常_灵祖：点击 #183「灵祖挑战」")
        self._click_shape(ctx, image183, activity_shape)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([184], label='日常_灵祖：等待灵祖挑战详情 #184', wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = self._recognized_scene_ocr_text(ctx, frame, [184])
            last_text = text or last_text
            if scene_id == 184:
                with self._lock:
                    self._status.update({"current_scene": 184, "updated_at": time.time()})
                    self._log_locked("success", f"日常_灵祖：等待灵祖挑战详情 #184：已到达 #184 {score:.0f}%")
                break
            if time.monotonic() - start >= 18.0:
                raise TimeoutError(f"日常_灵祖：等待灵祖挑战详情 #184 超时，OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_灵祖：等待灵祖挑战详情 #184，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_lingzu_wait_detail",
                    current_scene=scene_id,
                )
        frame = self._screencap(ctx)
        detail_text = self._recognized_scene_ocr_text(ctx, frame, [184])
        if self._daily_lingzu_remaining_zero(detail_text):
            self._record_daily_lingzu_done(payload, message="详情页显示今日剩余次数 0/1")
            return "done"
        return "open"
