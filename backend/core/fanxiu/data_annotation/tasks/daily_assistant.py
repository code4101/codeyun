"""日常助手业务：进入总览、一键执行、进度与结果复核、返回世界及历练触发。

执行能力由宿主 Executor 提供；助手流程集中在本模块，日常挑战保留组合入口。
"""

from __future__ import annotations
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.kernel_scheduler_control import read_world_facts, record_world_discovery
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from pyxllib.autogui import View
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation.job_times import next_business_time
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION

_DAILY_ASSISTANT_LILIAN_TRIGGER_FACT = "daily_assistant_lilian_event_trigger"

def _daily_assistant_business_date(now: datetime) -> str | None:
    """Return the calendar day after its 05:00 daily boundary."""

    boundary = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if now < boundary:
        return None
    return now.date().isoformat()


class DailyAssistantTaskMixin:
    def _schedule_lilian_event_after_daily_assistant_success(self, now: datetime) -> str | None:
        """Schedule Lilian once after the first successful assistant run after 05:00."""

        business_date = _daily_assistant_business_date(now)
        if business_date is None:
            return None

        facts = read_world_facts()
        discoveries = facts.setdefault("discoveries", {})
        if not isinstance(discoveries, dict):
            discoveries = {}
            facts["discoveries"] = discoveries
        previous = discoveries.get(_DAILY_ASSISTANT_LILIAN_TRIGGER_FACT)
        if isinstance(previous, dict) and previous.get("business_date") == business_date:
            return None

        next_time = (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time("lilian-event", next_time)
        record_world_discovery(_DAILY_ASSISTANT_LILIAN_TRIGGER_FACT, {
            "business_date": business_date,
            "assistant_succeeded_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "lilian_next_time": next_time,
        })
        return next_time

    def _daily_assistant_entry_matches(self, lines: list[dict[str, Any]], image69: dict[str, Any]) -> list[tuple[float, float, str]]:
        scroll_shape = self._find_shape(image69, "滚动窗口")
        if scroll_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法查找小助手入口")
        image_width, image_height = self._frame_size(image69)
        box = self._box(scroll_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        tab_matches: list[tuple[float, float, str]] = []
        list_matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not re.search(r"小\s*助手|助手", text):
                continue
            line_x = float(line.get("x") or 0)
            line_y = float(line.get("y") or 0)
            line_w = float(line.get("w") or 0)
            line_h = float(line.get("h") or 0)
            cx = line_x + line_w / 2
            cy = line_y + line_h / 2
            compact = re.sub(r"\s+", "", text)
            tab_match = re.search(r"小助手|助手", compact)
            if tab_match and cy >= image_height * 0.78:
                text_len = max(1, len(compact))
                click_x = line_x + line_w * ((tab_match.start() + tab_match.end()) / 2) / text_len
                if compact.startswith("活动报名") and "奖励找回" in compact:
                    click_x = max(image_width * 0.33, min(click_x, image_width * 0.40))
                click_y = cy
                if 0 <= click_x <= image_width and 0 <= click_y <= image_height:
                    tab_matches.append((click_x, click_y, text))
                    continue
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            list_matches.append((cx, cy, text))
        return sorted(tab_matches, key=lambda item: (item[1], item[0])) + sorted(list_matches, key=lambda item: (item[1], item[0]))

    def _open_daily_assistant_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找小助手")
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        max_scrolls = int(payload.get("assistant_max_scrolls") or payload.get("max_scrolls") or 8)
        for direction, scroll_count in (("down", max_scrolls),):
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：查找小助手入口 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_assistant_find_entry",
                        current_scene=69,
                    )
                frame = context.cur_frame(update=True)
                lines = context.ocr_fragments(frame)
                matches = self._daily_assistant_entry_matches(lines, image69)
                if matches:
                    x, y, matched_text = matches[0]
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_助手：点击入口 {matched_text}",
                            phase="daily_assistant_click_entry",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_助手：点击 #69「{matched_text}」")
                    context.click_frame_point(69, x, y)
                    yield from context.wait_action_settle(float(payload.get("assistant_entry_click_settle_seconds") or 2.0))
                    return "open"
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_助手：未找到小助手入口，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
                context.clear_frame()
        return "not_found"

    def _wait_daily_assistant_after_entry(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("post_click_timeout") or payload.get("assistant_post_click_timeout") or 20.0)
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([204, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            last_scene_id, last_score = scene_id, score
            if scene_id == 204:
                return 204, float(score)
            text = context.ocr_text(frame)
            last_text = text or last_text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, 100.0
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待小助手入口点击结果，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_wait_after_entry",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_助手：等待入口点击结果超时，未检测到小助手清单，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:120]}"
                )
            yield from context.wait_action_settle(0.5)

    def _wait_daily_assistant_list_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float,
        label: str,
        allow_daily_or_world: bool = False,
    ) -> tuple[int, float]:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([275, 237, 204, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, float(score or 100.0)
            if allow_daily_or_world and scene_id in {69, 34}:
                return int(scene_id), float(score or 100.0)
            if scene_id == 237:
                yield from self._daily_assistant_close_youli_result(context, payload)
                continue
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                self._daily_assistant_close_one_key_result(ctx, context, frame, label=label)
                yield from context.wait_action_settle(float(payload.get("assistant_result_reclick_settle_seconds") or 1.0))
                continue
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"{label} 超时，未检测到 #204，最后 {scene_text} {last_score:.0f}% OCR={last_text[:160]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_wait_list_state",
                    current_scene=scene_id,
            )
            yield from context.wait_action_settle(0.5)

    def _daily_assistant_scene_or_text_is_list(self, scene_id: int | None, text: str) -> bool:
        if scene_id == 204:
            return True
        if scene_id in {34, 69}:
            return False
        return self._daily_assistant_text_is_list(text)

    def _daily_assistant_text_is_one_key_confirm(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(re.search(r"本次执行预计消耗.*灵石.*是否继续|是否继续执行", compact))

    def _daily_assistant_text_is_one_key_result(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            (re.search(r"神物园自动收取|仙府资源助手|本次获得的道具|自动兑换", compact) and "退出" in compact)
            or (
                "退出" in compact
                and "今日已完成" in compact
                and any(marker in compact for marker in ("宗门任务", "宗门祈福", "宗门俸禄", "宗门请安", "宗门资源"))
            )
        )

    def _daily_assistant_click_visible_exit(self, context: Any, frame: Any, *, scene_hint: int = 275) -> bool:
        try:
            lines = context.ocr_fragments(frame)
        except TypeError:
            lines = context.ocr_fragments()
        except Exception:
            lines = []
        candidates: list[tuple[float, float, float]] = []
        for line in lines or []:
            if not isinstance(line, dict):
                continue
            text = _sanitize_ocr_text(str(line.get("text") or ""))
            if "退出" not in text:
                continue
            try:
                x = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
                y = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            except (TypeError, ValueError):
                continue
            candidates.append((y, x, y))
        if not candidates:
            return False
        _sort_y, x, y = max(candidates, key=lambda item: item[0])
        context.click_frame_point(scene_hint, x, y)
        return True

    def _daily_assistant_close_one_key_result(self, ctx: dict[str, Any], context: Any, frame: Any, *, label: str) -> None:
        with self._lock:
            self._set_status_locked(
                "running",
                f"{label}：关闭小助手一键执行结果汇总",
                phase="daily_assistant_close_one_key_result",
                current_scene=275,
            )
        if self._daily_assistant_click_visible_exit(context, frame, scene_hint=275):
            with self._lock:
                self._log_locked("action", f"{label}：OCR 点击结果页「退出」")
            return
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image275 = images.get(275)
        if not isinstance(image275, dict) or self._find_shape(image275, "退出") is None:
            raise RuntimeError(f"{label}：结果汇总仍在前台，但缺少 #275「退出」标注，且 OCR 未定位到「退出」")
        with self._lock:
            self._log_locked("action", f"{label}：点击 #275「退出」")
        context.click_shape_center(image275, "退出")

    def _daily_assistant_text_is_one_key_progress(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            ("执行进度" in compact and ("剩余时间" in compact or "正在" in compact))
            or ("助手正在" in compact and ("执行进度" in compact or "寻路" in compact))
        )

    def _daily_assistant_one_key_progress_seconds(self, text: str) -> int | None:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)).translate(FULLWIDTH_DIGIT_TRANSLATION)
        matches = re.findall(r"(\d{2})[:：]?(\d{2})", compact)
        for minutes, seconds in reversed(matches):
            second_value = int(seconds)
            if second_value < 60:
                return int(minutes) * 60 + second_value
        return None

    def _ensure_daily_assistant_list_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        timeout: float,
        label: str,
    ) -> tuple[int, float]:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        start = time.monotonic()
        poll_seconds = float(payload.get("assistant_list_state_poll_seconds") or 0.5)
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        yield from context.wait_action_settle(float(payload.get("assistant_list_state_initial_settle_seconds") or 1.0))
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([275, 237, 204, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                return 204, float(score or 100.0)
            if scene_id == 237:
                yield from self._daily_assistant_close_youli_result(context, payload)
                continue
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                self._daily_assistant_close_one_key_result(ctx, context, frame, label=label)
                yield from context.wait_action_settle(float(payload.get("assistant_result_reclick_settle_seconds") or 1.0))
                continue
            if scene_id == 69:
                opened = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                if opened != "open":
                    raise RuntimeError(f"{label}：回到 #69 后未找到小助手入口，无法继续")
                return (yield from self._wait_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=timeout,
                    label=label,
                ))
            if scene_id == 34:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    context,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=label,
                )
                if scene_id == 69:
                    opened = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
                    if opened != "open":
                        raise RuntimeError(f"{label}：回到 #69 后未找到小助手入口，无法继续")
                    return (yield from self._wait_daily_assistant_list_state(
                        ctx,
                        stop_event,
                        payload,
                        timeout=timeout,
                        label=label,
                    ))
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"{label}：等待小助手清单超时，最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：等待回到小助手清单，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_ensure_list_state",
                    current_scene=scene_id,
                )
            yield from context.wait_action_settle(poll_seconds)

    def _run_daily_assistant_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image204 = images.get(204)
        if not isinstance(image204, dict):
            image69 = images.get(69)
            exit_shape = self._find_shape(image69, "退出") if isinstance(image69, dict) else None
            if isinstance(image69, dict) and exit_shape is not None:
                asset_tree_path = ctx.get("asset_tree_path")
                context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
                with self._lock:
                    self._set_status_locked("running", "日常_助手：缺少新版小助手总览标注，先退出小助手页", phase="daily_assistant_missing_assets_return", current_scene=69)
                    self._log_locked("action", "日常_助手：缺少新版小助手总览标注，点击 #69「退出」恢复到日常页")
                yield from context.wait_click(69, "退出")
                yield from context.wait_action_settle(2.0)
            raise RuntimeError("日常_助手：已进入小助手，但资产树缺少新版 #204「小助手总览」标注，无法执行一键流程")

        one_key_shape = self._find_shape(image204, "一键执行")
        if one_key_shape is None:
            raise RuntimeError("日常_助手：旧版逐项小助手流程已下线；#204 必须标注新版「一键执行」入口")
        if any(key in payload for key in ("assistant_items", "assistant_execute_shapes", "assistant_groups", "assistant_group")):
            self._log("detail", "日常_助手：忽略旧版 assistant_items/assistant_group 参数，改用新版一键执行闭环")
        return (yield from self._run_daily_assistant_one_key_from_overview(ctx, stop_event, payload, image204))

    def _run_daily_assistant_one_key_from_overview(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ) -> str:
        asset_tree_path = ctx.get("asset_tree_path")
        context = self._behavior_tree_context(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with context.expect_views(276, 277, 275, 237):
            return (yield from self._run_daily_assistant_one_key_claimed(
                ctx,
                stop_event,
                payload,
                image204,
                context,
            ))

    def _run_daily_assistant_one_key_claimed(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
        context: Any,
    ) -> str:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image275 = images.get(275)
        image276 = images.get(276)
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_助手：新版小助手总览点击一键执行",
                phase="daily_assistant_one_key_execute",
                current_scene=204,
            )
            self._log_locked("action", "日常_助手：点击 #204「一键执行」")
        yield from context.wait_click(204, "一键执行")
        yield from context.wait_action_settle(float(payload.get("assistant_one_key_click_settle_seconds") or 1.5))

        confirm_timeout = float(payload.get("assistant_one_key_confirm_timeout") or 20.0)
        start = time.monotonic()
        execution_evidence = ""
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([276, 277, 275, 237, 204, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            if scene_id in {69, 34}:
                raise RuntimeError(
                    "日常_助手：不能把未确认执行结果标记为成功；"
                    f"点击一键执行后直接回到 #{scene_id}，未看到 #276/#277/#275/#237"
                )
            if scene_id == 276 or self._daily_assistant_text_is_one_key_confirm(text):
                if not isinstance(image276, dict) or self._find_shape(image276, "是") is None:
                    raise RuntimeError("日常_助手：检测到一键执行消耗确认，但缺少 #276「是」标注")
                execution_evidence = "confirm"
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：确认一键执行消耗",
                        phase="daily_assistant_one_key_confirm",
                        current_scene=276,
                    )
                    self._log_locked("action", "日常_助手：点击 #276「是」")
                yield from context.wait_click(276, "是")
                yield from context.wait_action_settle(float(payload.get("assistant_one_key_confirm_settle_seconds") or 2.0))
                break
            if scene_id == 277 or self._daily_assistant_text_is_one_key_progress(text):
                execution_evidence = "progress"
                break
            if scene_id in {275, 237} or self._daily_assistant_text_is_one_key_result(text):
                execution_evidence = "result"
                break
            if time.monotonic() - start >= confirm_timeout:
                if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                    raise RuntimeError(
                        "日常_助手：不能把未确认执行结果标记为成功；"
                        "点击一键执行后仍在小助手总览，未看到 #276/#277/#275/#237"
                    )
                raise TimeoutError(
                    "日常_助手：点击一键执行后未检测到消耗确认或执行落点，"
                    f"最后 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待一键执行确认，当前 #{scene_id or 'unknown'} {score:.0f}%",
                    phase="daily_assistant_one_key_wait_confirm",
                    current_scene=scene_id,
            )
            yield from context.wait_action_settle(0.5)

        yield from self._wait_daily_assistant_one_key_progress(ctx, stop_event, payload, context)

        result_timeout = float(payload.get("assistant_one_key_result_timeout") or 45.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            _wait_scene_match = yield from context.wait_scene([275, 237, 204, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 237:
                yield from self._daily_assistant_close_youli_result(context, payload)
                yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, context, current_scene=34)
                self._log("success", "日常_助手：已关闭游历结果页并返回世界")
                return "success"
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                self._daily_assistant_close_one_key_result(ctx, context, frame, label="日常_助手")
                landed_scene_id, _landed_score = yield from self._wait_daily_assistant_list_state(
                    ctx,
                    stop_event,
                    payload,
                    timeout=float(payload.get("assistant_one_key_result_close_timeout") or 15.0),
                    label="日常_助手：等待结果汇总返回小助手总览",
                    allow_daily_or_world=True,
                )
                yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, context, current_scene=landed_scene_id)
                self._log("success", "日常_助手：新版小助手一键执行结果已关闭")
                return "success"
            if self._daily_assistant_scene_or_text_is_list(scene_id, text):
                if not execution_evidence:
                    raise RuntimeError(
                        "日常_助手：不能把未确认执行结果标记为成功；"
                        "当前已在小助手总览，但没有确认/进度/结果证据"
                    )
                yield from self._return_after_daily_assistant_one_key(ctx, stop_event, payload, context, current_scene=204)
                self._log("success", f"日常_助手：新版小助手一键执行已确认，当前已在总览，证据={execution_evidence}")
                return "success"
            if scene_id in {69, 34}:
                raise RuntimeError(
                    "日常_助手：不能把未确认执行结果标记为成功；"
                    f"一键执行后回到 #{scene_id}，但未经过 #275/#237 结果页或 #204 总览复核"
                )
            if time.monotonic() - start >= result_timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    "日常_助手：确认一键执行后未回到小助手总览，也未进入执行结果页，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}"
                )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待一键执行结果，当前 #{scene_id or 'unknown'} {score:.0f}%",
                    phase="daily_assistant_one_key_wait_result",
                    current_scene=scene_id,
            )
            yield from context.wait_action_settle(0.5)

    def _wait_daily_assistant_one_key_progress(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: Any,
    ):
        # 一键执行是服务器侧长事务，真实运行从 #277 到 #275 可超过 15 分钟。
        # 观察窗过短会释放全局运行权，让后续 Job 与仍在执行的助手事务交叉。
        timeout = float(payload.get("assistant_one_key_progress_timeout") or 1200.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    "日常_助手：等待 #277 进度或 #275 结果超时，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}"
                )
            _wait_scene_match = yield from context.wait_scene([277, 275, 204, 69, 34], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            text = context.ocr_text(frame)
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                return
            if scene_id == 277 or self._daily_assistant_text_is_one_key_progress(text):
                seconds = self._daily_assistant_one_key_progress_seconds(text)
                if seconds is None:
                    wait_seconds = 10.0
                    message = "日常_助手：等待 #277 进度，未解析到时间，10 秒后重试 OCR"
                else:
                    wait_seconds = float(min(10, max(0, seconds)))
                    message = f"日常_助手：等待 #277 进度剩余 {seconds} 秒，本轮等待 {wait_seconds:g} 秒"
                    if wait_seconds <= 0:
                        wait_seconds = 0.5
                with self._lock:
                    self._set_status_locked(
                        "running",
                        message,
                        phase="daily_assistant_one_key_wait_progress",
                        current_scene=277,
                    )
                    self._log_locked("detail", message)
                yield from context.wait_action_settle(wait_seconds)
                continue
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待进入 #277 进度，当前 #{scene_id or 'unknown'} {score:.0f}%",
                    phase="daily_assistant_one_key_wait_progress_enter",
                    current_scene=scene_id,
                )
            yield from context.wait_action_settle(0.5)

    def _return_after_daily_assistant_one_key(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        context: Any,
        *,
        current_scene: int | None,
    ):
        if not bool(payload.get("assistant_return_after_items", True)):
            if False:
                yield None
            return
        _wait_scene_match = yield from context.wait_scene([275, 237, 204, 69, 34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
            self._daily_assistant_close_one_key_result(ctx, context, frame, label="日常_助手")
            landed_scene_id, _landed_score = yield from self._wait_daily_assistant_list_state(
                ctx,
                stop_event,
                payload,
                timeout=float(payload.get("assistant_one_key_result_close_timeout") or 15.0),
                label="日常_助手：等待结果汇总返回小助手总览",
                allow_daily_or_world=True,
            )
            current_scene = int(landed_scene_id)
        elif scene_id in {237, 204, 69, 34}:
            current_scene = int(scene_id)
        if current_scene == 204:
            for _attempt in range(3):
                _wait_scene_match = yield from context.wait_scene([275, 237, 204, 69, 34], wait=5.0, required=False)
                (scene_id, _score, frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                text = context.ocr_text(frame)
                if scene_id == 275 or self._daily_assistant_text_is_one_key_result(text):
                    self._daily_assistant_close_one_key_result(ctx, context, frame, label="日常_助手")
                    landed_scene_id, _landed_score = yield from self._wait_daily_assistant_list_state(
                        ctx,
                        stop_event,
                        payload,
                        timeout=float(payload.get("assistant_one_key_result_close_timeout") or 15.0),
                        label="日常_助手：等待结果汇总返回小助手总览",
                        allow_daily_or_world=True,
                    )
                    current_scene = int(landed_scene_id)
                    continue
                if scene_id == 237:
                    yield from self._daily_assistant_close_youli_result(context, payload)
                    current_scene = 34
                    break
                if scene_id in {69, 34}:
                    current_scene = int(scene_id)
                    break
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_助手：一键执行后返回日常页",
                        phase="daily_assistant_one_key_return_daily",
                        current_scene=204,
                    )
                    self._log_locked("action", "日常_助手：点击 #204「返回」")
                images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
                image204 = images.get(204)
                if not isinstance(image204, dict) or self._find_shape(image204, "返回") is None:
                    raise RuntimeError("日常_助手：缺少 #204「返回」标注，无法退出小助手总览")
                context.click_shape_center(image204, "返回")
                landed = yield from context.wait_scene(
                    [69,
                    34,
                    275,
                    237,
                    204],
                    wait=float(payload.get("assistant_one_key_return_daily_timeout") or 15.0),
                    label="日常_助手：等待返回日常页",
                )
                current_scene = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
                if current_scene in {69, 34}:
                    break
        if current_scene == 237:
            yield from self._daily_assistant_close_youli_result(context, payload)
            current_scene = 34
        if current_scene == 69 and bool(payload.get("assistant_return_world", True)):
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_助手：一键执行后返回世界",
                    phase="daily_assistant_one_key_return_world",
                    current_scene=69,
                )
                self._log_locked("action", "日常_助手：点击 #69「退出」")
            yield from context.wait_click(69, "退出")
            yield from context.wait_scene([34], wait=float(payload.get("assistant_one_key_return_world_timeout") or 25.0), label="日常_助手：等待返回世界")

    def _daily_assistant_close_youli_result(self, context: Any, payload: dict[str, Any]):
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_助手：关闭游历结果页",
                phase="daily_assistant_close_youli_result",
                current_scene=237,
            )
            self._log_locked("action", "日常_助手：点击 #237「确定」关闭游历结果")
        yield from context.wait_click(237, "确定")
        landed = yield from context.wait_scene([228, 204, 69, 34], wait=float(payload.get("assistant_youli_result_close_timeout") or 15.0), label="日常_助手：等待游历结果关闭")
        landed_scene_id = int(landed.id) if isinstance(landed, View) and landed.id is not None else int(landed)
        if landed_scene_id == 228:
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_助手：从游历页返回世界",
                    phase="daily_assistant_return_from_youli",
                    current_scene=228,
                )
                self._log_locked("action", "日常_助手：点击 #228「返回」")
            yield from context.wait_click(228, "返回")
            yield from context.wait_scene([34, 69], wait=float(payload.get("assistant_youli_return_timeout") or 18.0), label="日常_助手：等待离开游历页")

    def _execute_daily_assistant_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_助手资产树路径，无法执行作业")
        context = self._behavior_tree_context(ctx, asset_tree_path, stop_event=stop_event)
        _wait_scene_match = yield from context.wait_scene([34], wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        text = context.ocr_text(frame)
        if scene_id != 69:
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_助手")):
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
                    label="日常_助手",
                )

        daily_status = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
        if daily_status == "not_found":
            self._persist_scheduler_task_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-assistant"),
                (job_now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
            )
            raise RuntimeError("日常_助手：未找到小助手入口，已记录 30 分钟后重试")
        scene_id, _score = yield from self._wait_daily_assistant_after_entry(ctx, stop_event, payload)
        if scene_id == 204:
            result = yield from self._run_daily_assistant_from_list(ctx, stop_event, payload)
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-assistant")
            completed_at = job_now()
            lilian_next_time = self._schedule_lilian_event_after_daily_assistant_success(completed_at)
            next_time = next_business_time(("00:00", "05:00", "12:00", "18:00"))
            self._persist_scheduler_task_next_time(scheduler_task_id, next_time)
            if lilian_next_time:
                self._log("success", f"日常_助手：今日 05:00 后首次成功，历练_事件已安排至 {lilian_next_time}")
            self._log("success", f"日常_助手：本轮完成，下次 {next_time}")
            return result
        raise RuntimeError(f"日常_助手：入口点击后回到 #{scene_id or 'unknown'}，尚未进入新版小助手总览，不能按完成处理")
