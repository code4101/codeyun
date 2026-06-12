from __future__ import annotations

import base64
import difflib
import io
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable

from pyxllib.prog import BehaviorTreeStatus, scheduled_task_payload_with_meta, select_due_scheduled_tasks

from backend.core.fanxiu_behavior_tree import (
    acquire_fanxiu_job_group_isolation,
    data_annotation_asset_tree_path as _core_data_annotation_asset_tree_path,
    ensure_fanxiu_runtime_jobs_registered,
    fanxiu_data_annotation_mail_scan_state_path as _core_data_annotation_mail_scan_state_path,
    fanxiu_data_annotation_manual_job_state_path as _core_data_annotation_manual_job_state_path,
    fanxiu_data_annotation_runtime_state_path as _core_data_annotation_runtime_state_path,
    fanxiu_data_annotation_scheduler_settings_path as _core_data_annotation_scheduler_settings_path,
    fanxiu_data_annotation_scheduler_state_path as _core_data_annotation_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path as _core_data_annotation_world_facts_path,
    fanxiu_behavior_tree_control_path as _core_behavior_tree_control_path,
    fanxiu_job_group_isolated,
    fanxiu_job_group_isolation_path as _core_data_annotation_job_group_isolation_path,
    fanxiu_runtime_runner_running,
    fanxiu_runtime_runner_status,
    fanxiu_runtime_runner_wake,
    fanxiu_runtime_task_label,
    release_fanxiu_job_group_isolation,
    start_fanxiu_manual_runtime_task,
)
from backend.core.fanxiu_capture_runtime import fanxiu_capture_runtime_service
from backend.core.fanxiu_data_annotation_jobs import (
    data_annotation_manual_jobs_state,
    get_fanxiu_data_annotation_manual_job_definition as _data_annotation_manual_job_definition,
    pop_next_data_annotation_manual_job,
    read_data_annotation_manual_jobs,
    requeue_running_data_annotation_manual_jobs,
)
from backend.core.fanxiu_data_annotation_runtime import DataAnnotationRuntimeContainer as _DataAnnotationRuntimeContainer
from backend.core.fanxiu_data_annotation_scheduler import (
    data_annotation_world_facts_summary,
)
from backend.core.fanxiu_data_annotation_scheduler_defaults import default_data_annotation_scheduler_tasks as _default_data_annotation_scheduler_tasks
from backend.core.fanxiu_data_annotation_state import (
    append_data_annotation_runtime_status_log,
    data_annotation_scheduler_task_state as _data_annotation_scheduler_task_state,
    data_annotation_task_due as _data_annotation_task_due,
    initial_data_annotation_runtime_status,
    next_data_annotation_scheduler_time as _core_next_data_annotation_scheduler_time,
    normalize_data_annotation_scheduler_settings,
    parse_data_annotation_task_time,
    persist_data_annotation_runtime_status as _persist_data_annotation_runtime_status_core,
    read_data_annotation_json as _read_data_annotation_json,
    read_data_annotation_runtime_status as _read_data_annotation_runtime_status_core,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json as _write_data_annotation_json,
)
from backend.core.fanxiu_mail_policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_desired_status_for_rewards,
    fanxiu_mail_desired_status_for_record,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
    fanxiu_mail_visible_group_action_policy,
)
from backend.core.fanxiu_mail_runtime_store import (
    find_packet_mail_record_by_raw_title,
    find_packet_mail_record_exact,
    mark_packet_mail_record_missing_from_list,
    packet_mail_records_by_normalized_title,
    packet_mail_records_for_visible_row_exact,
    packet_mail_records_for_visible_row_same_time,
    packet_mail_records_same_time,
    packet_mail_records_same_title,
    pending_packet_mail_action_candidates,
    pending_packet_mail_records,
    recent_packet_mail_records,
    trace_packet_mail_gap,
    update_packet_mail_action,
)
from backend.core.fanxiu_mumu_control import screencap_mumu_adb_png
from backend.core.fanxiu_ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu_runtime_errors import FanxiuRuntimeError
from backend.core.temp_paths import codeyun_temp_root
from pyxllib.autogui import (
    ActionPlanner,
    CloseActionPlanner,
    SceneNavigator,
    SceneRecognizer,
    SceneScorer,
    Runtime,
    Shape,
    ShapeMatchPlanner,
    View,
    flatten_shapes as _flatten_runtime_shapes,
    frame_size as _runtime_frame_size,
    image_number as _runtime_image_number,
    index_images as _index_runtime_images,
)

FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
_default_engine: Any | None = None
_ACTION_TRACE_DEFAULT_MAX_FILES = 10000


def _parse_xianfu_visit_cd_seconds(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    normalized = normalized.replace("：", ":").replace("O", "0").replace("o", "0")
    match = re.search(r"(\d{1,2}):(\d{1,2}):(\d{1,2})", normalized)
    if match:
        hours, minutes, seconds = (int(match.group(index)) for index in range(1, 4))
        return hours * 3600 + minutes * 60 + seconds
    match = re.search(r"(\d{1,2}):(\d{1,2})", normalized)
    if match:
        minutes, seconds = (int(match.group(index)) for index in range(1, 3))
        return minutes * 60 + seconds
    hours = minutes = seconds = 0
    matched = False
    for value, unit in re.findall(r"(\d{1,3})(时|小时|分|分钟|秒)", normalized):
        matched = True
        if unit in {"时", "小时"}:
            hours = int(value)
        elif unit in {"分", "分钟"}:
            minutes = int(value)
        elif unit == "秒":
            seconds = int(value)
    if matched:
        return hours * 3600 + minutes * 60 + seconds
    if "免费" in normalized and not re.search(r"\d", normalized):
        return 0
    return None


def _parse_xianfu_skill_cd_seconds(text: Any) -> int | None:
    return _parse_xianfu_visit_cd_seconds(text)


def _parse_daily_boss_cd_seconds(text: Any) -> int | None:
    return _parse_xianfu_visit_cd_seconds(text)


def _parse_daily_boss_reward_remaining(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    match = re.search(r"剩余奖励次数[:：]?(\d{1,3})(?:/\d{1,3})?", normalized)
    if match:
        return int(match.group(1))
    if "剩余奖励次数" not in normalized:
        return None
    match = re.search(r"(\d{1,3})(?:/\d{1,3})?", normalized)
    return int(match.group(1)) if match else None


def _parse_daily_boss_hp_percent(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    matches = [int(value) for value in re.findall(r"(\d{1,3})%", normalized)]
    valid = [value for value in matches if 0 <= value <= 100]
    return min(valid) if valid else None


def _parse_first_int(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    match = re.search(r"\d+", normalized)
    return int(match.group(0)) if match else None


@dataclass(frozen=True)
class _FanxiuMatchedView:
    view: View
    score: float
    folder_path: str
    action_shape: dict[str, Any] | None


class FanxiuRuntime(Runtime):
    """Fanxiu 行为树运行时上下文。

    业务层只感知 runtime；ctx、当前帧、资产树路径和底层点击/匹配实现都收敛在这里。
    """

    default_wait_click_timeout = 18.0

    def __init__(
        self,
        runner: Any,
        ctx: dict[str, Any],
        asset_tree_path: Path | None = None,
        frame_data_url: str | None = None,
        candidates: list[dict[str, Any]] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.runner = runner
        self.ctx = ctx
        self.asset_tree_path = asset_tree_path
        self.frame_data_url = frame_data_url
        self.candidates = candidates
        self.stop_event = stop_event
        self.matched_view: _FanxiuMatchedView | None = None
        self.last_clicked_shape: Shape | None = None
        self._shape_match_results: dict[int, dict[str, Any]] = {}
        attrs = ctx.get("attrs")
        if not isinstance(attrs, dict):
            attrs = {}
            ctx["attrs"] = attrs
        self.attrs = attrs

    def cur_frame(self, update: bool = False) -> str:
        if update:
            self.clear_frame()
        if isinstance(self.frame_data_url, str) and self.frame_data_url:
            self.runner._set_tick_frame(self.ctx, self.frame_data_url)
            return self.frame_data_url
        self.frame_data_url = self.runner._screencap(self.ctx)
        return self.frame_data_url

    def clear_frame(self) -> None:
        self.frame_data_url = None
        self.runner._clear_tick_frame(self.ctx)

    def popup_candidates(self) -> list[dict[str, Any]]:
        if self.candidates is not None:
            return self.candidates
        if not isinstance(self.asset_tree_path, Path):
            self.candidates = []
            return self.candidates
        self.candidates = self.runner._auto_close_guard_candidates_for_path(self.asset_tree_path)
        return self.candidates

    def get_cur_view(self, update: bool = False) -> View | None:
        if update:
            self.clear_frame()
        return self.find_view("弹窗")

    def get_views(self, group: str = "", recursive: bool = False) -> list[View]:
        if group != "弹窗":
            images = self.ctx.get("images")
            if isinstance(images, dict):
                return [View(image) for image in images.values() if isinstance(image, dict)]
        return [
            View(candidate["image"])
            for candidate in self.popup_candidates()
            if isinstance(candidate.get("image"), dict)
        ]

    def find_view(self, group: str = "") -> View | None:
        if group != "弹窗":
            for view in self.get_views(group):
                if view.is_match(self, include_descendants=bool(group)):
                    self.matched_view = _FanxiuMatchedView(
                        view=view,
                        score=0.0,
                        folder_path=str(group or ""),
                        action_shape=None,
                    )
                    return view
            self.matched_view = None
            return None
        candidate, score = self.runner._auto_close_popup_first_match(self.ctx, self.popup_candidates(), self.cur_frame())
        if not isinstance(candidate, dict) or not isinstance(candidate.get("image"), dict):
            self.matched_view = None
            return None
        self.matched_view = _FanxiuMatchedView(
            view=View(candidate["image"]),
            score=score,
            folder_path=str(candidate.get("folder_path") or ""),
            action_shape=candidate.get("action_shape") if isinstance(candidate.get("action_shape"), dict) else None,
        )
        return self.matched_view.view

    def get_view(self, view_id: int, *, root: View | None = None) -> View | None:
        if isinstance(view_id, View):
            return view_id
        if root is None:
            images = self.ctx.get("images")
            if isinstance(images, dict):
                image = images.get(int(view_id))
                if isinstance(image, dict):
                    return View(image)
        roots = [root.raw] if isinstance(root, View) and isinstance(root.raw, dict) else [
            candidate.get("image")
            for candidate in self.popup_candidates()
            if isinstance(candidate.get("image"), dict)
        ]
        for item in roots:
            found = self._find_image_by_number(item, view_id)
            if found is not None:
                return View(found)
        return None

    def goto_view(self, view: View | int) -> Any:
        target_scene_id = view.id if isinstance(view, View) else int(view)
        if not isinstance(self.asset_tree_path, Path):
            raise RuntimeError("缺少场景移动资产树路径")
        stop_event = self.stop_event or threading.Event()
        result = self.runner._go_scene_task(self.ctx, self.asset_tree_path, target_scene_id, stop_event)
        return (yield from result) if isinstance(result, GeneratorType) else result

    def wait_view(
        self,
        *views: View | int,
        timeout: float | None = None,
        label: str = "等待场景",
    ):
        view_ids = [view.id if isinstance(view, View) else int(view) for view in views]
        view_ids = [view_id for view_id in view_ids if view_id is not None]
        images = self.ctx.get("images")
        target_views: list[View] = [view for view in views if isinstance(view, View)]
        if isinstance(images, dict):
            for view_id in view_ids:
                image = images.get(view_id)
                if isinstance(image, dict):
                    target_views.append(View(image))
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.runner._clear_tick_frame(self.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self.cur_frame(update=True)
            elapsed = time.monotonic() - start
            for view in target_views:
                if view.is_match(self):
                    scene_id = view.id
                    with self.runner._lock:
                        self.runner._status.update({
                            "current_scene": scene_id,
                            "updated_at": time.time(),
                        })
                    self.runner._log("success", f"{label}：View 已匹配 #{scene_id}")
                    return view
            scene_id, score = self.runner._identify_scene_number(self.ctx, frame, view_ids)
            last_scene_id, last_score = scene_id, score
            if scene_id in view_ids:
                with self.runner._lock:
                    self.runner._status.update({
                        "current_scene": scene_id,
                        "updated_at": time.time(),
                    })
                self.runner._log("success", f"{label}：已到达 #{scene_id} {score:.0f}%")
                return self.get_view(scene_id) or scene_id
            if timeout is not None and elapsed >= float(timeout):
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                expected = "/".join(f"#{view_id}" for view_id in view_ids)
                raise TimeoutError(f"{label} 超时，未检测到 {expected}，最后 {scene_text} {last_score:.0f}%")
            if self.runner._auto_close_popup_guard_step(self):
                self.clear_frame()
                continue
            with self.runner._lock:
                self.runner._status.update({
                    "phase": "wait_scene",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })

    def match_shape(self, shape: Shape) -> bool:
        view = shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            return False
        frame = self.cur_frame()
        entry = self.ctx.get("entry")
        if hasattr(entry, "mode"):
            result: dict[str, Any] = {"matched": False}
            for condition in self.runner._shape_match_conditions(shape.raw):
                result = self.runner._match_shape(self.ctx, view.raw, shape.raw, frame, condition=condition)
                if bool(result.get("matched")):
                    break
            self._shape_match_results[id(shape.raw)] = result
            return bool(result.get("matched"))

        score = float(self.runner._shape_score(self.ctx, view.raw, shape.raw, frame) or 0)
        matched = score >= float(self.runner.overlay_threshold)
        if not matched:
            self._shape_match_results.pop(id(shape.raw), None)
        return matched

    def click_shape(self, view: View, shape: Shape) -> Any:
        if not isinstance(view.raw, dict):
            raise RuntimeError("缺少可点击 view")
        self.last_clicked_shape = shape
        match_result = self._shape_match_results.get(id(shape.raw))
        frame = self.cur_frame() if match_result is not None or self.runner._shape_click_needs_frame(shape.raw) else None
        try:
            result = self.runner._click_shape(
                self.ctx,
                view.raw,
                shape.raw,
                frame,
                match_result=match_result,
            )
        except RuntimeError as exc:
            if not self.runner._scene_route_fixed_click_fallback_allowed(view.raw, shape.raw, exc):
                raise
            x, y = ActionPlanner().shape_center(view.raw, shape.raw)
            self.runner._log(
                "info",
                f"Runtime View：#{self.runner._image_number(view.raw) or '?'}「{shape.raw.get('title') or shape.raw.get('id')}」图像定位失败，改按固定标注点击 ({x:.0f},{y:.0f})",
            )
            result = self.runner._click_frame_point(self.ctx, view.raw, x, y)
        self.clear_frame()
        return result

    def on_wait_click_poll(self, view: View, shape: Shape, matched: bool) -> None:
        result = self._shape_match_results.get(id(shape.raw))
        if not isinstance(result, dict):
            return
        self.runner._log(
            "detail",
            (
                f"等待点击「{shape.title or shape.raw.get('id')}」："
                f"matched={bool(matched)}，"
                f"similarity={float(result.get('similarity') or 0):.0f}，"
                f"ocr={str(result.get('ocr_text') or '')[:20]}，"
                f"fixed_box={result.get('fixed_box')}"
            ),
        )

    def drag_shape_content(self, shape: Shape, *, ratio: float = 0.5, duration: float = 1.5) -> Any:
        view = shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError("shape 缺少 parent_view，无法滚动加载")
        start_x, start_y, end_x, end_y = ActionPlanner().drag_shape_content_points(
            view.raw,
            shape.raw,
            direction=shape.content_direction or "down",
            ratio=ratio,
        )
        self.runner._drag_frame_point(
            self.ctx,
            view.raw,
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms=max(0, int(float(duration) * 1000)),
        )
        self.clear_frame()

    def popup_score(self, view: View | None) -> float:
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            return 0.0
        return float(self.runner._popup_score(self.ctx, view.raw, self.cur_frame()) or 0.0)

    def _find_image_by_number(self, image: Any, view_id: int) -> dict[str, Any] | None:
        if not isinstance(image, dict):
            return None
        if self.runner._image_number(image) == view_id:
            return image
        children = image.get("children")
        if isinstance(children, list):
            for child in children:
                found = self._find_image_by_number(child, view_id)
                if found is not None:
                    return found
        return None


@dataclass
class _RuntimeMailRow:
    raw: dict[str, Any]
    title_shape: Shape

    @property
    def title(self) -> str:
        return str(self.raw.get("title") or "")

    @property
    def time_text(self) -> str:
        return str(self.raw.get("time_text") or "")

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "无")


def _db_engine() -> Any:
    if _default_engine is not None:
        return _default_engine
    from backend.db import engine

    return engine


def _now() -> datetime:
    return datetime.now()


def ensure_fanxiu_mail_table() -> None:
    from backend.core.fanxiu_mail_store import ensure_fanxiu_mail_table as _ensure_fanxiu_mail_table

    _ensure_fanxiu_mail_table()


def normalize_fanxiu_mail_title(value: Any) -> str:
    from backend.core.fanxiu_mail_store import normalize_fanxiu_mail_title as _normalize_fanxiu_mail_title

    return _normalize_fanxiu_mail_title(value)


def normalize_fanxiu_mail_time_text(value: Any) -> str:
    from backend.core.fanxiu_mail_store import normalize_fanxiu_mail_time_text as _normalize_fanxiu_mail_time_text

    return _normalize_fanxiu_mail_time_text(value)


def sync_fanxiu_capture_paths(pcap_paths: list[str], *, max_streams: int = 4) -> dict[str, Any]:
    from backend.core.fanxiu_packet_insight_worker import sync_fanxiu_capture_paths as _sync_fanxiu_capture_paths

    return _sync_fanxiu_capture_paths(pcap_paths, max_streams=max_streams)


def _recognize_data_annotation_ocr_frame(frame_data_url: str) -> dict[str, Any]:
    from backend.core.fanxiu_game_macro_annotation import _recognize_data_annotation_ocr_frame as _recognize_frame

    return _recognize_frame(frame_data_url)


def _screencap_game_window2_service() -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import screencap_game_window2_service

    return screencap_game_window2_service()


def _remote_game_window2_screencap(entry: Any) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import remote_game_window2_screencap

    return remote_game_window2_screencap(entry)


def _match_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import match_game_window2_service

    return match_game_window2_service(payload)


def _match_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import match_remote_game_window2

    return match_remote_game_window2(entry, payload)


def _click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import click_game_window2_service

    return click_game_window2_service(payload)


def _click_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import click_remote_game_window2

    return click_remote_game_window2(entry, payload)


def _drag_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import drag_game_window2_service

    return drag_game_window2_service(payload)


def _drag_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import drag_remote_game_window2

    return drag_remote_game_window2(entry, payload)


def _keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import keyevent_game_window2_service

    return keyevent_game_window2_service(payload)


def _keyevent_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import keyevent_remote_game_window2

    return keyevent_remote_game_window2(entry, payload)


def _text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import text_game_window2_service

    return text_game_window2_service(payload)


def _text_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu_game_window_actions import text_remote_game_window2

    return text_remote_game_window2(entry, payload)


def _data_annotation_asset_tree_path(entry_id: str) -> Path:
    return _core_data_annotation_asset_tree_path(entry_id)


def _data_annotation_runtime_state_path() -> Path:
    return _core_data_annotation_runtime_state_path()


def _data_annotation_world_facts_path() -> Path:
    return _core_data_annotation_world_facts_path()


def _data_annotation_scheduler_state_path() -> Path:
    return _core_data_annotation_scheduler_state_path()


def _data_annotation_scheduler_settings_path() -> Path:
    return _core_data_annotation_scheduler_settings_path()


def _data_annotation_manual_job_state_path() -> Path:
    return _core_data_annotation_manual_job_state_path()


def _data_annotation_mail_scan_state_path() -> Path:
    return _core_data_annotation_mail_scan_state_path()


def _data_annotation_job_group_isolation_path() -> Path:
    return _core_data_annotation_job_group_isolation_path()


def _behavior_tree_control_path() -> Path:
    return _core_behavior_tree_control_path()


def _persist_data_annotation_runtime_status(status: dict[str, Any]) -> None:
    _persist_data_annotation_runtime_status_core(
        _data_annotation_runtime_state_path(),
        _data_annotation_world_facts_path(),
        status,
    )


def _read_data_annotation_runtime_status() -> dict[str, Any]:
    return _read_data_annotation_runtime_status_core(_data_annotation_runtime_state_path())


def _record_data_annotation_scheduler_task_fact(task: dict[str, Any], result: str) -> None:
    record_data_annotation_scheduler_task_fact(_data_annotation_world_facts_path(), task, result)


def _read_data_annotation_world_facts() -> dict[str, Any]:
    return _read_data_annotation_json(_data_annotation_world_facts_path(), {})


def _write_data_annotation_world_facts(facts: dict[str, Any]) -> None:
    _write_data_annotation_json(_data_annotation_world_facts_path(), facts)


def _read_data_annotation_scheduler_tasks() -> list[dict[str, Any]]:
    return list(_read_data_annotation_json(_data_annotation_scheduler_state_path(), []) or [])


def _write_data_annotation_scheduler_tasks(tasks: list[dict[str, Any]]) -> None:
    _write_data_annotation_json(
        _data_annotation_scheduler_state_path(),
        [_data_annotation_scheduler_task_state(task) for task in tasks],
    )


def _read_data_annotation_scheduler_settings() -> dict[str, Any]:
    return normalize_data_annotation_scheduler_settings(
        _read_data_annotation_json(_data_annotation_scheduler_settings_path(), None)
    )


def _read_data_annotation_manual_jobs() -> list[dict[str, Any]]:
    raw = _read_data_annotation_json(_data_annotation_manual_job_state_path(), [])
    return read_data_annotation_manual_jobs(raw)


def _write_data_annotation_manual_jobs(jobs: list[dict[str, Any]]) -> None:
    _write_data_annotation_json(_data_annotation_manual_job_state_path(), data_annotation_manual_jobs_state(jobs))


def _requeue_running_data_annotation_manual_jobs() -> int:
    jobs = _read_data_annotation_manual_jobs()
    updated, changed_count = requeue_running_data_annotation_manual_jobs(jobs)
    if changed_count:
        _write_data_annotation_manual_jobs(updated)
    return changed_count


def _remove_data_annotation_manual_job(job_id: str) -> None:
    job_id = str(job_id or "")
    if not job_id:
        return
    jobs = [job for job in _read_data_annotation_manual_jobs() if str(job.get("id") or "") != job_id]
    _write_data_annotation_manual_jobs(jobs)


def _pop_next_data_annotation_manual_job() -> dict[str, Any] | None:
    jobs = _read_data_annotation_manual_jobs()
    selected, claimed_jobs = pop_next_data_annotation_manual_job(jobs)
    if selected is None:
        return None
    _write_data_annotation_manual_jobs(claimed_jobs)
    return selected


def _start_next_data_annotation_manual_job_if_idle(entry: Any, entry_id: str) -> dict[str, Any] | None:
    if fanxiu_runtime_runner_running():
        return None
    task = _pop_next_data_annotation_manual_job()
    if task is None:
        return None
    return start_fanxiu_manual_runtime_task(
        entry=entry,
        entry_id=entry_id,
        task=task,
        asset_tree_path=_data_annotation_asset_tree_path(entry_id),
    )


def _next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    return _core_next_data_annotation_scheduler_time(task, now if now is not None else _now())


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    task_type = str(task.get("task_type") or "")
    if task_type == "mail_claim_check":
        task_type = "mail_cleanup"
    definition = _data_annotation_manual_job_definition(task_type)
    return bool(definition and definition.scheduler_supported)


def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_payload_with_meta(task)

class DataAnnotationRuntimeRunner:
    default_guard_enabled = True
    default_guard_interval_seconds = 2.0
    default_guard_items = {
        "wanling_invite": {"enabled": False, "entry_id": "", "updated_at": 0.0},
    }
    guard_definitions = {
        "close_popups": {
            "id": "close_popups",
            "label": "关闭弹窗",
            "message": "常驻处理已标注弹窗和遮挡",
        },
        "wanling_invite": {
            "id": "wanling_invite",
            "label": "万灵切磋邀请",
            "message": "占位触发守护，当前仅保留开关",
        },
    }
    scene_ids = {
        "world": 34,
        "world_menu": 35,
        "settings": 49,
        "hide_floating": 58,
        "daily": 69,
        "daily_boss_list": 178,
        "daily_boss_detail": 179,
        "daily_boss_fighting": 180,
        "daily_boss_done": 181,
        "daily_boss_list_cd": 182,
        "daily_lingzu_activity": 183,
        "daily_lingzu_detail": 184,
        "daily_lingzu_cutscene": 185,
        "daily_lingzu_world_reward": 186,
        "daily_lingzu_elder": 187,
        "daily_lingzu_boss": 188,
        "daily_lingzu_result": 189,
        "daily_jianling_main": 190,
        "daily_jianling_confirm": 191,
        "daily_jianling_result": 192,
        "daily_lingta_entry": 193,
        "daily_lingta_main": 194,
        "daily_lingta_confirm": 195,
        "daily_lingta_result": 196,
        "daily_xianyuan_list": 197,
        "daily_xianyuan_detail": 198,
        "daily_xianyuan_dialogue": 199,
        "daily_xianyuan_challenge_dialogue": 200,
        "daily_xianyuan_challenge_confirm": 201,
        "daily_xianyuan_challenge_result": 202,
        "daily_xianyuan_leave_confirm": 203,
        "daily_assistant_list": 204,
        "daily_assistant_detail": 205,
        "daily_assistant_no_action": 208,
        "daily_assistant_teaching_result": 209,
        "wanling_invite": 70,
        "youli": 71,
        "youli_explore": 72,
        "youli_result": 73,
        "daily_activity": 75,
        "signup": 23,
        "signup_reward": 24,
        "gift": 78,
        "reward": 81,
        "duplicated": 82,
    }
    scene_threshold = 80
    scene_thresholds = {"gift": 60, "daily": 60, "hide_floating": 55}
    overlay_threshold = 55

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._service_thread: threading.Thread | None = None
        self._service_control_thread: threading.Thread | None = None
        self._service_stop_event: threading.Event | None = None
        self._service_wake_event = threading.Event()
        self._service_entry: Any | None = None
        self._service_entry_id = ""
        self._service_asset_tree_path: Path | None = None
        self._service_runtime_state_path: Path | None = None
        self._service_manual_job_state_path: Path | None = None
        self._service_scheduler_state_path: Path | None = None
        self._service_world_facts_path: Path | None = None
        self._service_generation = 0
        self._service_heartbeat_at = 0.0
        self._service_step = ""
        self._service_owner_token = ""
        self._service_owner_path: Path | None = None
        self._service_last_control_id = ""
        self._local_run_token = ""
        self._stop_event: threading.Event | None = None
        self._guard_group_enabled = True
        self._guard_enabled = self.default_guard_enabled
        self._guard_entry_id = ""
        self._guard_interval_seconds = self.default_guard_interval_seconds
        self._guard_items: dict[str, dict[str, Any]] = json.loads(json.dumps(self.default_guard_items, ensure_ascii=False))
        self._auto_close_candidates_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
        self._log_scope = ""
        self._log_item_id = ""
        self._status: dict[str, Any] = self._initial_status()

    def _initial_status(self) -> dict[str, Any]:
        return initial_data_annotation_runtime_status()

    def _status_base_preserving_guard_locked(self) -> dict[str, Any]:
        base = self._initial_status()
        base.update({
            "guard_group_enabled": bool(self._guard_group_enabled),
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(self._status.get("guard_running")),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": json.loads(json.dumps(self._guard_items, ensure_ascii=False)),
            "last_guard_event": self._status.get("last_guard_event") if isinstance(self._status.get("last_guard_event"), dict) else {},
            "service_running": bool(self._service_thread is not None and self._service_thread.is_alive()),
        })
        return base

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._sync_guard_status_locked()
            self._sync_service_status_locked()
            return json.loads(json.dumps(self._status, ensure_ascii=False))

    def replace_logs(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._status["logs"] = list(logs)
            self._status["updated_at"] = time.time()
            self._sync_guard_status_locked()
            self._sync_service_status_locked()
            return json.loads(json.dumps(self._status, ensure_ascii=False))

    def wait_until_idle(self, timeout_seconds: float = 5.0) -> bool:
        deadline = time.time() + max(0.0, timeout_seconds)
        while time.time() < deadline:
            with self._lock:
                running = bool(self._status.get("running"))
                stopping = str(self._status.get("status") or "") == "stopping"
            if not running and not stopping:
                return True
            time.sleep(0.1)
        with self._lock:
            return not bool(self._status.get("running"))

    def _sync_guard_status_locked(self) -> None:
        service_running = self._service_thread is not None and self._service_thread.is_alive()
        guard_group_running = bool(self._guard_group_enabled and service_running)
        guard_running = bool(self._guard_group_enabled and self._guard_enabled and service_running)
        guard_items: dict[str, dict[str, Any]] = {}
        for guard_id, definition in self.guard_definitions.items():
            state = self._guard_items.get(guard_id)
            if not isinstance(state, dict):
                state = {}
            enabled = bool(state.get("enabled"))
            entry_id = str(state.get("entry_id") or "")
            running = False
            message = str(definition.get("message") or "")
            if guard_id == "close_popups":
                enabled = bool(self._guard_enabled)
                entry_id = self._guard_entry_id
                running = bool(guard_running)
                last_guard_event = self._status.get("last_guard_event")
                if isinstance(last_guard_event, dict) and last_guard_event.get("title"):
                    message = str(last_guard_event.get("title") or "")
            guard_items[guard_id] = {
                **definition,
                "enabled": enabled,
                "running": running,
                "entry_id": entry_id,
                "updated_at": float(state.get("updated_at") or 0),
                "message": message,
            }
        self._status.update({
            "guard_group_enabled": bool(self._guard_group_enabled),
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(guard_running),
            "guard_group_running": bool(guard_group_running),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": guard_items,
        })

    def _sync_service_status_locked(self) -> None:
        self._status["service_running"] = bool(self._service_thread is not None and self._service_thread.is_alive())

    def _pending_manual_job_count(self) -> int:
        return sum(
            1
            for job in _read_data_annotation_manual_jobs()
            if str(job.get("status") or "") in {"pending", "queued", "running"}
        )

    def _service_should_restart_for_pending_jobs_locked(self) -> bool:
        if self._service_thread is None or not self._service_thread.is_alive():
            return False
        if self._status.get("running"):
            return False
        try:
            pending_count = self._pending_manual_job_count()
        except Exception:
            return False
        if pending_count <= 0:
            return False
        heartbeat_at = float(self._service_heartbeat_at or 0.0)
        if heartbeat_at <= 0:
            return True
        return time.time() - heartbeat_at > max(10.0, self._guard_interval_seconds * 3)

    def _mark_service_heartbeat(self, step: str) -> None:
        with self._lock:
            self._service_heartbeat_at = time.time()
            self._service_step = str(step or "")
            token = self._service_owner_token
            owner_path = self._service_owner_path
            entry_id = self._service_entry_id
            generation = self._service_generation
        if token and owner_path is not None:
            self._write_service_owner(owner_path, token, entry_id, generation, self._service_step)

    def _service_owner_stale(self, owner: dict[str, Any]) -> bool:
        updated_at = float(owner.get("updated_at") or 0.0)
        if updated_at <= 0:
            return True
        return time.time() - updated_at > max(120.0, self._guard_interval_seconds * 30)

    def _write_service_owner(self, owner_path: Path, token: str, entry_id: str, generation: int, step: str) -> None:
        try:
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            owner_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "token": token,
                        "entry_id": entry_id,
                        "generation": generation,
                        "step": step,
                        "updated_at": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _acquire_service_owner(self, entry_id: str, generation: int) -> tuple[bool, str]:
        owner_path = _data_annotation_runtime_state_path().parent / "behavior_tree_service_owner.json"
        token = uuid.uuid4().hex
        try:
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            if owner_path.exists():
                try:
                    owner = json.loads(owner_path.read_text(encoding="utf-8"))
                except Exception:
                    owner = {}
                if isinstance(owner, dict) and not self._service_owner_stale(owner):
                    pid = owner.get("pid")
                    step = owner.get("step") or "unknown"
                    return False, f"行为树执行器已由后端进程 {pid} 持有：{step}"
                try:
                    owner_path.unlink()
                except FileNotFoundError:
                    pass
            fd = os.open(str(owner_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "token": token,
                        "entry_id": entry_id,
                        "generation": generation,
                        "step": "starting",
                        "updated_at": time.time(),
                    },
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            self._service_owner_token = token
            self._service_owner_path = owner_path
            return True, ""
        except FileExistsError:
            return False, "行为树执行器已由另一个后端实例抢先启动"
        except Exception as exc:
            return False, f"行为树执行器单例锁获取失败：{exc}"

    def _release_service_owner(self) -> None:
        with self._lock:
            owner_path = self._service_owner_path
            token = self._service_owner_token
            self._service_owner_path = None
            self._service_owner_token = ""
        if owner_path is None or not token:
            return
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except Exception:
            owner = {}
        if isinstance(owner, dict) and str(owner.get("token") or "") == token:
            try:
                owner_path.unlink()
            except FileNotFoundError:
                pass

    def _job_group_isolated(self) -> bool:
        return fanxiu_job_group_isolated(_data_annotation_job_group_isolation_path())

    def _acquire_job_group_isolation(self, *, reason: str, ttl_seconds: float = 300.0) -> str:
        token = acquire_fanxiu_job_group_isolation(
            reason=reason,
            ttl_seconds=ttl_seconds,
            path=_data_annotation_job_group_isolation_path(),
        )
        with self._lock:
            self._local_run_token = token
        self._service_wake_event.set()
        return token

    def _release_job_group_isolation(self, token: str) -> None:
        token = str(token or "")
        with self._lock:
            if self._local_run_token == token:
                self._local_run_token = ""
        release_fanxiu_job_group_isolation(token, path=_data_annotation_job_group_isolation_path())

    def ensure_service(
        self,
        *,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        tick_seconds: float = 1.0,
    ) -> dict[str, Any]:
        entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        with self._lock:
            self._restore_persisted_config_locked()
            self._service_entry = entry
            self._service_entry_id = entry_id
            self._service_asset_tree_path = asset_tree_path
            self._service_runtime_state_path = _data_annotation_runtime_state_path()
            self._service_manual_job_state_path = _data_annotation_manual_job_state_path()
            self._service_scheduler_state_path = _data_annotation_scheduler_state_path()
            self._service_world_facts_path = _data_annotation_world_facts_path()
            if self._guard_enabled:
                self._guard_entry_id = entry_id
            if not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            if self._service_thread is not None and self._service_thread.is_alive():
                if not self._service_should_restart_for_pending_jobs_locked():
                    self._service_wake_event.set()
                    self._sync_service_status_locked()
                    return json.loads(json.dumps(self._status, ensure_ascii=False))
                if self._service_stop_event is not None:
                    self._service_stop_event.set()
                self._service_generation += 1
                self._log_locked("stop", f"行为树常驻服务心跳停滞，准备重启：{self._service_step or 'unknown'}")
            else:
                self._service_generation += 1
            stop_event = threading.Event()
            self._service_stop_event = stop_event
            generation = self._service_generation
            acquired, owner_message = self._acquire_service_owner(entry_id, generation)
            if not acquired:
                self._sync_service_status_locked()
                self._set_status_locked("idle", owner_message, phase="service_owned_by_other")
                self._log_locked("info", owner_message)
                return json.loads(json.dumps(self._status, ensure_ascii=False))
            requeued_count = _requeue_running_data_annotation_manual_jobs()
            self._service_heartbeat_at = time.time()
            self._service_step = "starting"
            thread = threading.Thread(
                target=self._run_service_loop,
                kwargs={"stop_event": stop_event, "tick_seconds": tick_seconds, "generation": generation},
                name="fanxiu-data-annotation-runtime-service",
                daemon=True,
            )
            control_thread = threading.Thread(
                target=self._run_service_control_loop,
                kwargs={"stop_event": stop_event, "generation": generation},
                name="fanxiu-data-annotation-runtime-control",
                daemon=True,
            )
            self._service_thread = thread
            self._service_control_thread = control_thread
            self._sync_service_status_locked()
            self._log_locked("info", "行为树常驻服务已启动")
            if requeued_count:
                self._log_locked("stop", f"已重置 {requeued_count} 个残留 running 作业为 queued")
            thread.start()
            control_thread.start()
        self._service_wake_event.set()
        return self.status()

    def stop_service(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        thread: threading.Thread | None = None
        with self._lock:
            stop_event = self._service_stop_event
            thread = self._service_thread
            if stop_event is not None:
                stop_event.set()
            self._service_wake_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout_seconds or 0.1)))
        with self._lock:
            self._sync_service_status_locked()
            if self._service_thread is not None and not self._service_thread.is_alive():
                self._service_thread = None
                self._service_control_thread = None
                self._service_stop_event = None
                self._status["service_running"] = False
                self._status["guard_running"] = False
                self._status["updated_at"] = time.time()
        self._persist_status()
        return self.status()

    def _restore_persisted_config_locked(self) -> None:
        if self._status.get("service_running") or self._status.get("running"):
            return
        persisted = _read_data_annotation_runtime_status()
        if not persisted:
            return
        self._guard_group_enabled = bool(persisted.get("guard_group_enabled", True))
        self._guard_enabled = bool(persisted.get("guard_enabled"))
        self._guard_entry_id = str(persisted.get("guard_entry_id") or "")
        self._guard_interval_seconds = float(persisted.get("guard_interval_seconds") or self._guard_interval_seconds)
        raw_items = persisted.get("guard_items")
        if isinstance(raw_items, dict):
            self._guard_items = {
                str(key): dict(value)
                for key, value in raw_items.items()
                if isinstance(value, dict)
            }
        current_logs = [item for item in self._status.get("logs") or [] if isinstance(item, dict)]
        persisted_logs = [item for item in persisted.get("logs") or [] if isinstance(item, dict)]
        kept_logs = (current_logs or persisted_logs)[-500:]
        self._status.update({
            **self._status,
            "entry_id": persisted.get("entry_id") or persisted.get("guard_entry_id") or self._status.get("entry_id") or "",
            "current_scene": persisted.get("current_scene"),
            "message": "行为树常驻服务恢复配置",
            "logs": kept_logs,
            "updated_at": time.time(),
        })

    def _service_context(self) -> tuple[Any, str, Path] | None:
        with self._lock:
            entry = self._service_entry
            entry_id = self._service_entry_id
            asset_tree_path = self._service_asset_tree_path
        if entry is None or not entry_id or asset_tree_path is None:
            return None
        return entry, entry_id, asset_tree_path

    def _service_paths_still_current(self) -> bool:
        with self._lock:
            runtime_state_path = self._service_runtime_state_path
            manual_job_state_path = self._service_manual_job_state_path
            scheduler_state_path = self._service_scheduler_state_path
            world_facts_path = self._service_world_facts_path
        return (
            runtime_state_path == _data_annotation_runtime_state_path()
            and manual_job_state_path == _data_annotation_manual_job_state_path()
            and scheduler_state_path == _data_annotation_scheduler_state_path()
            and world_facts_path == _data_annotation_world_facts_path()
        )

    def _run_service_control_loop(self, *, stop_event: threading.Event, generation: int) -> None:
        while not stop_event.is_set():
            with self._lock:
                if generation != self._service_generation:
                    break
            try:
                self._consume_service_control_request()
            except Exception as exc:
                with self._lock:
                    self._log_locked("error", f"行为树控制请求处理失败：{exc}")
                self._persist_status()
            stop_event.wait(0.2)

    def _consume_service_control_request(self) -> None:
        control_path = _behavior_tree_control_path()
        try:
            request = json.loads(control_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            request = {}
        if not isinstance(request, dict):
            return
        request_id = str(request.get("id") or "")
        if not request_id or request_id == self._service_last_control_id:
            return
        command = str(request.get("command") or "").strip()
        if command != "stop_current_task":
            return
        self._service_last_control_id = request_id
        entry_id = str(request.get("entry_id") or "")
        status = self.stop_current_task(entry_id)
        with self._lock:
            self._log_locked(
                "stop",
                f"已处理本地控制请求：stop_current_task reason={request.get('reason') or ''} status={status.get('status') or ''}",
            )
        try:
            control_path.unlink()
        except FileNotFoundError:
            pass

    def _run_service_loop(self, *, stop_event: threading.Event, tick_seconds: float, generation: int) -> None:
        last_idle_guard_at = 0.0
        while not stop_event.is_set():
            with self._lock:
                if generation != self._service_generation:
                    break
            self._mark_service_heartbeat("loop")
            context = self._service_context()
            if context is None or not self._service_paths_still_current():
                self._mark_service_heartbeat("waiting_context")
                self._service_wake_event.wait(max(0.2, float(tick_seconds or 1.0)))
                self._service_wake_event.clear()
                continue
            entry, entry_id, asset_tree_path = context
            try:
                if not self.status().get("running"):
                    self._mark_service_heartbeat("manual_job_poll")
                    if _start_next_data_annotation_manual_job_if_idle(entry, entry_id) is not None:
                        self._mark_service_heartbeat("manual_job_started")
                        self._service_wake_event.wait(0.1)
                        self._service_wake_event.clear()
                        continue
                    if self._job_group_isolated():
                        self._mark_service_heartbeat("scheduler_isolated")
                    else:
                        self._mark_service_heartbeat("scheduler_poll")
                        started_due = self._start_due_scheduler_tasks_if_idle(entry, entry_id, asset_tree_path)
                        if started_due:
                            self._mark_service_heartbeat("scheduler_started")
                            self._service_wake_event.wait(0.1)
                            self._service_wake_event.clear()
                            continue
                    interval = self._guard_interval_seconds
                    now = time.time()
                    if (
                        self._guard_group_enabled
                        and self._guard_enabled
                        and now - last_idle_guard_at >= max(0.5, interval)
                        and (
                            not bool(_read_data_annotation_scheduler_settings().get("job_group_enabled", True))
                            or not self._scheduler_task_due_soon(within_seconds=180.0)
                        )
                    ):
                        last_idle_guard_at = now
                        self._mark_service_heartbeat("idle_guard")
                        self._run_idle_guard_tick(entry, entry_id, asset_tree_path)
                        self._mark_service_heartbeat("idle_guard_done")
            except Exception as exc:
                with self._lock:
                    self._log_locked("error", f"行为树 tick 失败：{exc}")
                    self._status.update({"ok": False, "status": "error", "message": str(exc), "error": str(exc), "updated_at": time.time()})
                self._persist_status()
            self._service_wake_event.wait(max(0.2, float(tick_seconds or 1.0)))
            self._service_wake_event.clear()
        with self._lock:
            self._sync_service_status_locked()
            self._log_locked("stop", "行为树常驻服务已停止")
        self._release_service_owner()
        self._persist_status()

    def _start_due_scheduler_tasks_if_idle(self, entry: Any, entry_id: str, asset_tree_path: Path) -> bool:
        if self.status().get("running"):
            return False
        if self._job_group_isolated():
            return False
        if not bool(_read_data_annotation_scheduler_settings().get("job_group_enabled", True)):
            self._mark_service_heartbeat("scheduler_job_group_disabled")
            return False
        tasks = _read_data_annotation_scheduler_tasks()
        due_tasks = select_due_scheduled_tasks(
            tasks,
            task_due=_data_annotation_task_due,
            task_supported=_data_annotation_task_supported,
        )
        if not due_tasks:
            return False
        self.start_scheduler_tasks(
            entry=entry,
            entry_id=entry_id,
            tasks=due_tasks,
            all_tasks=tasks,
            asset_tree_path=asset_tree_path,
            run_label="执行全部到期任务",
        )
        return True

    def _scheduler_task_due_soon(self, *, within_seconds: float = 180.0) -> bool:
        try:
            tasks = _read_data_annotation_scheduler_tasks()
        except Exception:
            return False
        now_ts = time.time()
        threshold = max(0.0, float(within_seconds or 0.0))
        for task in tasks:
            if not bool(task.get("enabled")):
                continue
            if not _data_annotation_task_supported(task):
                continue
            if _data_annotation_task_due(task):
                return True
            for key in ("retry_after", "next_time"):
                due_at = parse_data_annotation_task_time(task.get(key))
                if due_at is not None and 0 <= due_at - now_ts <= threshold:
                    return True
        return False

    def _run_idle_guard_tick(self, entry: Any, entry_id: str, asset_tree_path: Path) -> None:
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            self._runtime_guard_service_tick("close_popups", ctx, asset_tree_path, threading.Event())
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame)
            scene_id = self.scene_ids.get(key)
            if scene_id is not None and self._scene_matches(key, score):
                with self._lock:
                    self._status.update({
                        "entry_id": entry_id,
                        "current_scene": scene_id,
                        "status": "idle",
                        "phase": "idle_tick",
                        "message": f"空转识别：#{scene_id} {key} {score:.0f}%",
                        "updated_at": time.time(),
                    })
            self._clear_tick_frame(ctx)
            self._persist_status()
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"守护空转失败：{exc}", scope="guard", item_id="close_popups")

    def stop_current_task(self, entry_id: str) -> dict[str, Any]:
        with self._lock:
            if entry_id and self._status.get("entry_id") not in {"", entry_id}:
                return self.status()
            if not self._status.get("running"):
                self._sync_guard_status_locked()
                self._sync_service_status_locked()
                service_running = bool(self._status.get("service_running"))
                self._set_status_locked("idle", "当前没有正在运行的任务" if service_running else "行为树常驻服务未初始化")
                return json.loads(json.dumps(self._status, ensure_ascii=False))
            if self._stop_event is not None:
                self._stop_event.set()
            self._set_status_locked("stopping", "当前任务停止请求已发送")
        return self.status()

    def set_guard(
        self,
        *,
        entry: Any,
        entry_id: str,
        enabled: bool,
        interval_seconds: float,
        guard_id: str = "close_popups",
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        guard_id = str(guard_id or "close_popups").strip() or "close_popups"
        interval_seconds = max(0.5, min(30.0, float(interval_seconds or 2.0)))
        with self._lock:
            guard_item = self._guard_items.setdefault(guard_id, {})
            guard_item.update({
                "enabled": bool(enabled),
                "entry_id": entry_id if enabled else "",
                "updated_at": time.time(),
            })
            if guard_id != "close_popups":
                self._set_status_locked(str(self._status.get("status") or "idle"), f"守护{'已开启' if enabled else '已关闭'}：{guard_id}")
                self._sync_guard_status_locked()
                self._sync_service_status_locked()
                self._log_locked("info", self._status["message"], scope="guard", item_id=guard_id)
            else:
                self._guard_enabled = bool(enabled)
                self._guard_entry_id = entry_id if enabled else ""
                self._guard_interval_seconds = interval_seconds
                if not enabled:
                    self._set_status_locked("idle" if not self._status.get("running") else str(self._status.get("status") or "running"), "守护已关闭")
                    self._sync_guard_status_locked()
                    self._sync_service_status_locked()
                else:
                    self._set_status_locked(str(self._status.get("status") or "idle"), "守护已开启")
                    self._sync_guard_status_locked()
                    self._sync_service_status_locked()
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
        self._service_wake_event.set()
        return self.status()

    def set_guard_group_enabled(
        self,
        *,
        entry: Any,
        entry_id: str,
        enabled: bool,
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        with self._lock:
            self._guard_group_enabled = bool(enabled)
            self._set_status_locked(
                "idle" if not self._status.get("running") else str(self._status.get("status") or "running"),
                "守护组已开启" if enabled else "守护组已关闭",
            )
            self._sync_guard_status_locked()
            self._sync_service_status_locked()
            self._log_locked("info", self._status["message"], scope="guard", item_id="guard_group")
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
        self._service_wake_event.set()
        return self.status()

    def start_runtime_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        task_type = self._canonical_runtime_task_type(task_type)
        payload = dict(payload or {})
        ensure_fanxiu_runtime_jobs_registered()
        definition = _data_annotation_manual_job_definition(task_type)
        if definition is None:
            raise FanxiuRuntimeError(f"暂不支持的任务类型：{task_type}", status_code=400)
        if definition.normalize_payload is not None:
            payload = definition.normalize_payload(payload)
        return self._run_inline_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload=payload,
            asset_tree_path=asset_tree_path,
        )

    def start_local_runtime_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
        isolate_jobs: bool = True,
    ) -> dict[str, Any]:
        task_type = self._canonical_runtime_task_type(task_type)
        payload = dict(payload or {})
        ensure_fanxiu_runtime_jobs_registered()
        definition = _data_annotation_manual_job_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        if definition.normalize_payload is not None:
            payload = definition.normalize_payload(payload)
        token = ""
        if isolate_jobs:
            token = self._acquire_job_group_isolation(
                reason=f"local_run:{task_type}",
                ttl_seconds=self._task_timeout_seconds(payload) + 30.0,
            )
        try:
            return self._run_inline_runtime_task(
                entry=entry,
                entry_id=entry_id,
                task_type=task_type,
                payload={**payload, "__local_run": True},
                asset_tree_path=asset_tree_path,
            )
        finally:
            if token:
                self._release_job_group_isolation(token)

    def _run_inline_runtime_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        with self._lock:
            if self._status.get("running"):
                raise FanxiuRuntimeError("数据标注 Runtime 正在运行任务", status_code=409)
            stop_event = threading.Event()
            self._stop_event = stop_event
            now = time.time()
            label = self._runtime_task_label(task_type, payload)
            self._status = {
                **self._status_base_preserving_guard_locked(),
                "running": True,
                "status": "running",
                "entry_id": entry_id,
                "task_type": task_type,
                "current_task": label,
                "phase": "local_run" if payload.get("__local_run") else "start",
                "message": "本地任务已启动" if payload.get("__local_run") else "任务已启动",
                "total": 1,
                "current_task_id": str(payload.get("__scheduler_task_id") or ""),
                "interruptible": bool(payload.get("__scheduler_interruptible", True)),
                "started_at": now,
                "updated_at": now,
            }
            prefix = "本地 " if payload.get("__local_run") else ""
            self._log_locked("info", f"启动{prefix}Runtime 任务：{label}")
        self._run_generic_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload=dict(payload),
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
        )
        return self.status()

    def start_manual_runtime_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task: dict[str, Any],
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        ensure_fanxiu_runtime_jobs_registered()
        task_id = str(task.get("id") or uuid.uuid4().hex)
        task_type = str(task.get("task_type") or "detect_scene")
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        label = str(task.get("label") or self._runtime_task_label(task_type, payload) or task_type)
        with self._lock:
            if self._status.get("running"):
                raise FanxiuRuntimeError("数据标注 Runtime 正在运行任务", status_code=409)
            stop_event = threading.Event()
            self._stop_event = stop_event
            now = time.time()
            self._status = {
                **self._status_base_preserving_guard_locked(),
                "running": True,
                "status": "running",
                "entry_id": entry_id,
                "task_type": task_type,
                "current_task": label,
                "phase": "manual_job",
                "message": f"手动作业已启动：{label}",
                "total": 1,
                "current_task_id": task_id,
                "interruptible": bool(task.get("interruptible", True)),
                "started_at": now,
                "updated_at": now,
            }
            self._log_locked(
                "info",
                self._manual_job_log_message(task_id, self._status["message"]),
                scope="manual_job",
                item_id="manual_job",
            )
        self._run_manual_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task=dict(task),
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
        )
        return self.status()

    def start_scheduler_tasks(
        self,
        *,
        entry: Any,
        entry_id: str,
        tasks: list[dict[str, Any]],
        all_tasks: list[dict[str, Any]],
        asset_tree_path: Path,
        run_label: str = "执行全部到期任务",
    ) -> dict[str, Any]:
        if not tasks:
            raise FanxiuRuntimeError("没有可执行的到期任务", status_code=400)
        is_run_due = run_label == "执行全部到期任务"
        with self._lock:
            if self._status.get("running"):
                raise FanxiuRuntimeError("数据标注 Runtime 正在运行任务", status_code=409)
            stop_event = threading.Event()
            self._stop_event = stop_event
            now = time.time()
            self._status = {
                **self._status_base_preserving_guard_locked(),
                "running": True,
                "status": "running",
                "entry_id": entry_id,
                "task_type": "scheduler_run_due" if is_run_due else "scheduler_run_now",
                "current_task": run_label,
                "phase": "start",
                "message": f"Scheduler 已启动：{run_label}，共 {len(tasks)} 个任务",
                "total": len(tasks),
                "current_task_id": "scheduler_run_due" if is_run_due else str(tasks[0].get("id") or "scheduler_run_now"),
                "interruptible": all(bool(item.get("interruptible", True)) for item in tasks),
                "started_at": now,
                "updated_at": now,
            }
            self._log_locked("info", f"启动 Scheduler：{run_label}，共 {len(tasks)} 个")
        self._run_scheduler_tasks(
            entry=entry,
            entry_id=entry_id,
            tasks=[dict(item) for item in tasks],
            all_tasks=[dict(item) for item in all_tasks],
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
            run_label=run_label,
        )
        return self.status()

    def _set_status_locked(self, status: str, message: str = "", **extra: Any) -> None:
        self._status.update({"status": status, "updated_at": time.time(), **extra})
        if message:
            self._status["message"] = message

    def _clear_current_task_locked(self) -> None:
        self._status.update({
            "running": False,
            "task_type": "",
            "current_task": "",
            "current_task_id": "",
            "interruptible": True,
        })

    def _canonical_runtime_task_type(self, task_type: str) -> str:
        task_type = str(task_type or "").strip()
        if task_type == "mail_claim_check":
            return "mail_cleanup"
        return task_type

    def _set_log_context(self, scope: str, item_id: str) -> tuple[str, str]:
        with self._lock:
            previous = (self._log_scope, self._log_item_id)
            self._log_scope = str(scope or "")
            self._log_item_id = str(item_id or "")
            return previous

    def _restore_log_context(self, previous: tuple[str, str]) -> None:
        with self._lock:
            self._log_scope, self._log_item_id = previous

    def _log_locked(self, kind: str, message: str, *, scope: str | None = None, item_id: str | None = None) -> None:
        log_scope = self._log_scope if scope is None else str(scope or "")
        log_item_id = self._log_item_id if item_id is None else str(item_id or "")
        append_data_annotation_runtime_status_log(
            self._status,
            kind,
            message,
            scope=log_scope,
            item_id=log_item_id,
            time_text=_now().strftime("%H:%M:%S"),
            updated_at=time.time(),
        )

    def _log(self, kind: str, message: str) -> None:
        with self._lock:
            self._log_locked(kind, message)

    def _manual_job_log_message(self, task_id: str, message: str) -> str:
        task_id = str(task_id or "").strip()
        return f"[{task_id}] {message}" if task_id else message

    def _persist_status(self) -> None:
        try:
            _persist_data_annotation_runtime_status(self.status())
        except Exception:
            pass

    def _runtime_task_label(self, task_type: str, payload: dict[str, Any] | None = None) -> str:
        task_type = self._canonical_runtime_task_type(task_type)
        definition = _data_annotation_manual_job_definition(task_type)
        label = definition.label if definition is not None else task_type
        if task_type == "go_scene":
            target = (payload or {}).get("target_scene_id") or (payload or {}).get("target")
            if target:
                label = f"到场景 #{target}"
        if task_type == "mail_cleanup" and (payload or {}).get("observe_only"):
            label = "邮件_清理"
        return label

    def _fanxiu_runtime(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path | None = None,
        frame_data_url: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> FanxiuRuntime:
        return FanxiuRuntime(self, ctx, asset_tree_path=asset_tree_path, frame_data_url=frame_data_url, stop_event=stop_event)

    def _runtime_guard_enabled(self, guard_id: str) -> bool:
        guard_id = str(guard_id or "").strip()
        with self._lock:
            if not self._guard_group_enabled:
                return False
            if guard_id == "close_popups":
                return bool(self._guard_enabled)
            state = self._guard_items.get(guard_id)
            return bool(state.get("enabled")) if isinstance(state, dict) else False

    def _runtime_guard_service_tick(
        self,
        guard_id: str,
        runtime_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> BehaviorTreeStatus:
        self._raise_if_stopped(stop_event)
        guard_id = str(guard_id or "").strip()
        if not self._runtime_guard_enabled(guard_id):
            return BehaviorTreeStatus.SKIP
        if guard_id != "close_popups":
            return BehaviorTreeStatus.SKIP
        with self._lock:
            if bool(self._status.get("running")) or str(self._status.get("phase") or "") in {"manual_job", "local_run"}:
                return BehaviorTreeStatus.SKIP
        previous_log_context = self._set_log_context("guard", "close_popups")
        try:
            runtime = self._fanxiu_runtime(runtime_ctx, asset_tree_path)
            if not self._auto_close_popup_guard_step(runtime):
                return BehaviorTreeStatus.SKIP
            self._persist_status()
            runtime.clear_frame()
            return BehaviorTreeStatus.RUNNING
        finally:
            self._restore_log_context(previous_log_context)

    def _run_runtime_behavior_tree(
        self,
        *,
        runtime_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        action: Callable[[], Any],
        label: str,
        tick_seconds: float = 1.0,
        max_runtime_seconds: float | None = None,
    ) -> Any:
        return _DataAnnotationRuntimeContainer(
            self,
            runtime_ctx=runtime_ctx,
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
        ).run_job_until_complete(
            action=action,
            label=label,
            tick_seconds=tick_seconds,
            max_runtime_seconds=max_runtime_seconds,
        )

    def _task_timeout_seconds(self, payload: dict[str, Any] | None = None) -> float:
        payload = payload if isinstance(payload, dict) else {}
        raw_value = payload.get("max_runtime_seconds", payload.get("timeout_seconds", 600))
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 600.0
        return max(30.0, min(3600.0, value))

    def _run_direct_runtime_action(
        self,
        action: Callable[[], Any],
        *,
        stop_event: threading.Event,
        tick_seconds: float = 1.0,
        max_runtime_seconds: float | None = None,
    ) -> Any:
        result = action()
        if not isinstance(result, GeneratorType):
            return result
        started_at = time.monotonic()
        while True:
            self._raise_if_stopped(stop_event)
            if max_runtime_seconds is not None and time.monotonic() - started_at > max_runtime_seconds:
                stop_event.set()
                raise RuntimeError(f"行为树任务超时：超过 {max_runtime_seconds:.0f} 秒")
            try:
                status = next(result)
            except StopIteration as stop:
                return stop.value
            if status == BehaviorTreeStatus.FAILURE:
                raise RuntimeError("行为树节点失败")
            stop_event.wait(max(0.1, float(tick_seconds or 1.0)))

    def _run_generic_runtime_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task_type: str,
        payload: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> None:
        task_id = str(payload.get("__scheduler_task_id") or "")
        previous_log_context = self._set_log_context("job", task_id) if task_id else None
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            task_result = self._run_runtime_behavior_tree(
                runtime_ctx=ctx,
                asset_tree_path=asset_tree_path,
                stop_event=stop_event,
                action=lambda: self._execute_runtime_task(ctx, task_type, payload, stop_event),
                label=self._runtime_task_label(task_type, payload),
                max_runtime_seconds=self._task_timeout_seconds(payload),
            )
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success" if task_result == "success" else str(task_result or "success"),
                    "phase": "done",
                    "message": f"{self._runtime_task_label(task_type, payload)}完成" if task_result == "success" else f"{self._runtime_task_label(task_type, payload)}已跳过",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": 1,
                    "current_code": "",
                })
                self._log_locked("success" if task_result == "success" else "skip", self._status["message"])
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "任务已停止")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail))
        finally:
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._persist_status()

    def _run_manual_runtime_task(
        self,
        *,
        entry: Any,
        entry_id: str,
        task: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
    ) -> None:
        task_id = str(task.get("id") or "")
        previous_log_context = self._set_log_context("manual_job", "manual_job")
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            result = self._run_direct_runtime_action(
                lambda: self._execute_runtime_task(ctx, str(task.get("task_type") or ""), payload, stop_event),
                stop_event=stop_event,
                max_runtime_seconds=self._task_timeout_seconds(payload),
            )
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
            if scheduler_task_id:
                tasks = _read_data_annotation_scheduler_tasks()
                self._mark_scheduler_task(tasks, scheduler_task_id, str(result or "success"))
            elif (result or "success") == "success":
                self._mark_matching_scheduler_tasks_for_manual_success(str(task.get("task_type") or ""), payload)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success" if (result or "success") == "success" else str(result or "success"),
                    "phase": "done",
                    "message": f"手动作业完成：{task.get('label') or task.get('task_type') or task_id}",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": 1,
                })
                self._log_locked("success", self._manual_job_log_message(task_id, self._status["message"]), scope="manual_job", item_id="manual_job")
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "手动作业已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", self._manual_job_log_message(task_id, "手动作业已停止"), scope="manual_job", item_id="manual_job")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "")
            if scheduler_task_id:
                tasks = _read_data_annotation_scheduler_tasks()
                self._mark_scheduler_task(tasks, scheduler_task_id, "error")
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", self._manual_job_log_message(task_id, str(detail)), scope="manual_job", item_id="manual_job")
        finally:
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            isolation_token = str(payload.get("__job_group_isolation_token") or "")
            if isolation_token:
                self._release_job_group_isolation(isolation_token)
            _remove_data_annotation_manual_job(task_id)
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._persist_status()

    def _run_scheduler_tasks(
        self,
        *,
        entry: Any,
        entry_id: str,
        tasks: list[dict[str, Any]],
        all_tasks: list[dict[str, Any]],
        asset_tree_path: Path,
        stop_event: threading.Event,
        run_label: str = "执行全部到期任务",
    ) -> None:
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            for index, task in enumerate(tasks):
                self._raise_if_stopped(stop_event)
                task_id = str(task.get("id") or "")
                label = str(task.get("label") or task_id or task.get("task_type") or "未命名任务")
                previous_log_context = self._set_log_context("job", task_id) if task_id else None
                try:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"Scheduler 执行 {index + 1}/{len(tasks)}：{label}",
                            current_index=index,
                            current_task=label,
                            task_type=str(task.get("task_type") or ""),
                            phase="scheduler_task",
                            current_task_id=task_id,
                            interruptible=bool(task.get("interruptible", True)),
                        )
                        self._log_locked("action", f"开始到期任务：{label}")
                    self._mark_scheduler_task(all_tasks, task_id, "running")
                    result = self._run_runtime_behavior_tree(
                        runtime_ctx=ctx,
                        asset_tree_path=asset_tree_path,
                        stop_event=stop_event,
                        action=lambda task=task: self._execute_runtime_task(
                            ctx,
                            str(task.get("task_type") or ""),
                            _data_annotation_task_payload_with_meta(task),
                            stop_event,
                        ),
                        label=label,
                        max_runtime_seconds=self._task_timeout_seconds(_data_annotation_task_payload_with_meta(task)),
                    )
                    self._mark_scheduler_task(all_tasks, task_id, result or "success")
                    with self._lock:
                        self._log_locked("success" if (result or "success") == "success" else "skip", f"到期任务{('完成' if (result or 'success') == 'success' else '跳过')}：{label}")
                finally:
                    if previous_log_context is not None:
                        self._restore_log_context(previous_log_context)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success",
                    "phase": "done",
                    "message": f"{run_label}完成",
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": len(tasks),
                    "current_code": "",
                })
                self._log_locked("success", f"Scheduler {run_label}完成")
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "Scheduler 任务已停止")
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            current_task_id = ""
            with self._lock:
                current_task_id = str(self._status.get("current_task_id") or "")
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail), scope="job" if current_task_id else None, item_id=current_task_id or None)
            if current_task_id:
                self._mark_scheduler_task(all_tasks, current_task_id, "error")
        finally:
            self._persist_status()

    def _mark_scheduler_task(self, tasks: list[dict[str, Any]], task_id: str, result: str) -> None:
        if not task_id:
            return
        now_text = _now().strftime("%Y-%m-%d %H:%M:%S")
        changed = False
        for item in tasks:
            if str(item.get("id") or "") != task_id:
                continue
            if result == "running":
                item["last_run_at"] = now_text
                item["retry_after"] = None
            elif result in {"success", "skipped", "unsupported"}:
                item["retry_after"] = None
                item["next_time"] = self._scheduler_task_fact_next_time(str(item.get("id") or "")) or _next_data_annotation_scheduler_time(item)
            elif result == "error":
                cooldown_seconds = int(item.get("cooldown_seconds") or 600)
                item["retry_after"] = (_now() + timedelta(seconds=cooldown_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            item["last_result"] = result
            changed = True
            break
        if changed:
            _write_data_annotation_scheduler_tasks(tasks)
            _record_data_annotation_scheduler_task_fact(item, result)

    def _scheduler_task_fact_next_time(self, task_id: str) -> str | None:
        if not task_id:
            return None
        facts = _read_data_annotation_world_facts()
        discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
        task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
        fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
        for key in ("discovered_next_time", "next_time"):
            value = str(fact.get(key) or "").strip()
            if value:
                return value
        return None

    def _record_scheduler_task_discovered_next_time(
        self,
        task_id: str,
        next_time_text: str,
        *,
        task_type: str,
        label: str,
    ) -> None:
        task_id = str(task_id or "").strip()
        next_time_text = str(next_time_text or "").strip()
        if not task_id or not next_time_text:
            return
        facts = _read_data_annotation_world_facts()
        discoveries = facts.get("discoveries")
        if not isinstance(discoveries, dict):
            discoveries = {}
            facts["discoveries"] = discoveries
        task_facts = discoveries.get("task")
        if not isinstance(task_facts, dict):
            task_facts = {}
            discoveries["task"] = task_facts
        existing = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
        task_facts[task_id] = {
            **existing,
            "id": task_id,
            "task_type": str(task_type or ""),
            "label": str(label or task_id),
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "discovered_next_time": next_time_text,
            "updated_at": time.time(),
        }
        _write_data_annotation_world_facts(facts)

    def _mark_matching_scheduler_tasks_for_manual_success(self, task_type: str, payload: dict[str, Any]) -> None:
        if str(payload.get("__scheduler_task_id") or ""):
            return
        normalized_task_type = self._canonical_runtime_task_type(task_type)
        if not normalized_task_type:
            return
        if normalized_task_type == "daily_boss":
            self._log("detail", "日常_首领手动作业不使用通用成功同步；只按奖励次数或刷新 CD 写入下次复查")
            return
        tasks = _read_data_annotation_scheduler_tasks()
        matched = False
        for item in tasks:
            if self._canonical_runtime_task_type(str(item.get("task_type") or "")) != normalized_task_type:
                continue
            if not _data_annotation_task_supported(item):
                continue
            last_result = str(item.get("last_result") or "")
            if not item.get("retry_after") and last_result not in {"queued", "running", "error", "stopped"}:
                continue
            self._mark_scheduler_task(tasks, str(item.get("id") or ""), "success")
            matched = True
        if matched:
            self._log("detail", f"手动作业成功后已同步清理同类 Scheduler 重试：{normalized_task_type}")

    def _execute_runtime_task(self, ctx: dict[str, Any], task_type: str, payload: dict[str, Any], stop_event: threading.Event) -> str:
        task_type = self._canonical_runtime_task_type(task_type)
        if task_type in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_name = str(payload.get("legacy_name") or task_type)
            self._log("skip", f"旧版任务「{legacy_name}」尚未迁移，已跳过")
            return "unsupported"
        definition = _data_annotation_manual_job_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        normalized_payload = dict(payload or {})
        if definition.normalize_payload is not None:
            normalized_payload = definition.normalize_payload(normalized_payload)
        result = definition.handler(self, ctx, normalized_payload, stop_event)
        if isinstance(result, GeneratorType):
            return result
        return str(result or "success")

    def _execute_mail_legacy_scan_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        entry_mode = str(payload.get("entry_mode") or payload.get("mail_entry_mode") or "dynamic").strip().lower()
        observe_only = bool(payload.get("observe_only") or payload.get("scan_only"))
        scan_mode = str(payload.get("scan_mode") or ("full" if payload.get("full_scan") else "incremental")).strip().lower()
        use_current_page = bool(payload.get("use_current_page"))
        target_title = str(payload.get("target_title") or payload.get("mail_title") or "").strip()
        target_time_text = self._normalize_mail_time_text(str(payload.get("target_time_text") or payload.get("mail_time_text") or "").strip())
        capture_enabled = not bool(payload.get("skip_capture") or payload.get("no_capture"))
        game_first = bool(payload.get("game_first") or payload.get("ui_first"))
        fail_on_packet_gap = bool(payload.get("fail_on_packet_gap"))
        try:
            max_actions = int(payload.get("max_actions") or 0)
        except (TypeError, ValueError):
            max_actions = 0
        raw_action_policies = payload.get("action_policies")
        if isinstance(raw_action_policies, list):
            action_policies = {str(item or "").strip().lower() for item in raw_action_policies}
            action_policies &= {"claim", "delete"}
        else:
            action_policies = {"claim", "delete"}
        if not action_policies:
            action_policies = {"claim", "delete"}
        if observe_only and not payload.get("scan_mode") and not payload.get("full_scan"):
            scan_mode = "full"
        capture_reason = f"mail-full-scan:{'observe' if observe_only else 'action'}"
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_历史扫描资产树路径，无法执行邮件作业")
        try:
            if capture_enabled:
                fanxiu_capture_runtime_service.ensure_running(capture_reason)
                with self._lock:
                    self._log_locked("info", f"邮件_抓包：已请求抓包服务 {capture_reason}")
                self._refresh_recent_mail_packets_for_runtime_log("启动抓包后", flush_capture=False)
            else:
                with self._lock:
                    self._log_locked("info", "邮件_历史扫描：本轮跳过抓包协作，仅使用当前页与既有邮件事实")
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包：启动抓包服务失败：{exc}")
            raise
        try:
            if observe_only:
                with self._lock:
                    self._log_locked("info", "邮件_全量遍历：只观察并滚动加载邮件，不领取、不删除")
            elif game_first:
                with self._lock:
                    self._log_locked("info", "邮件_历史扫描：游戏画面优先模式，缺 packet 的可见邮件按详情页按钮处理")
            scene_id, score, frame = self._current_scene_number(ctx)
            force_reopen_mail = observe_only or scan_mode in {"full", "full_scan", "observe", "observe_only", "refresh", "sync"}
            if scene_id == 121 and not use_current_page and (force_reopen_mail or (not observe_only and self._pending_packet_mail_action_count() > 0)):
                image121 = ctx.get("images", {}).get(121)
                back_shape = self._find_shape(image121, "空白-返回") if isinstance(image121, dict) else None
                if isinstance(image121, dict) and back_shape:
                    reason = "刷新邮件 packet 列表" if force_reopen_mail else "重置列表顶部"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"邮件_历史扫描：退出邮件页以{reason}",
                            phase="mail_claim_reset_mail_list",
                            current_scene=121,
                        )
                        self._log_locked("action", f"邮件_历史扫描：点击 #121「空白-返回」，重新从顶部进入邮件，{reason}")
                    self._click_shape(ctx, image121, back_shape, frame)
                    yield from self._wait_scene_id(ctx, stop_event, 34, timeout=12.0, label="邮件_历史扫描：返回世界 #34")
                    scene_id = 34
                else:
                    with self._lock:
                        self._log_locked("error", "邮件_历史扫描：缺少 #121「空白-返回」标注，保留当前位置扫描")
            if scene_id != 121:
                open_result = self._open_mail_scene(ctx, stop_event, asset_tree_path, entry_mode=entry_mode)
                result = (yield from open_result) if isinstance(open_result, GeneratorType) else open_result
                if result == "no_mail":
                    return "success"
                if result != "success":
                    return result
            else:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"邮件_历史扫描：当前已在邮件 #121 {score:.0f}%",
                        phase="mail_claim_resume_mail_scene",
                        current_scene=121,
                    )
                    self._log_locked("info", f"邮件_历史扫描：当前已在邮件 #121 {score:.0f}%，直接扫描")
            scan_result = self._scan_mail_scene(
                ctx,
                stop_event,
                action_enabled=not observe_only,
                scan_mode=scan_mode,
                action_policies=action_policies,
                max_actions=max_actions if max_actions > 0 else None,
                target_title=target_title,
                target_time_text=target_time_text,
                game_first=game_first,
                fail_on_packet_gap=fail_on_packet_gap,
            )
            return (yield from scan_result) if isinstance(scan_result, GeneratorType) else scan_result
        finally:
            if capture_enabled:
                self._refresh_recent_mail_packets_for_runtime_log("释放抓包前", flush_capture=True)
                try:
                    fanxiu_capture_runtime_service.release(capture_reason)
                    with self._lock:
                        self._log_locked("info", f"邮件_抓包：已释放抓包服务 {capture_reason}")
                except Exception as exc:
                    with self._lock:
                        self._log_locked("error", f"邮件_抓包：释放抓包服务失败：{exc}")

    def _execute_mail_cleanup_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        ensure_fanxiu_mail_table()
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少邮件_清理资产树路径，无法执行邮件作业")
        max_actions = max(1, int(payload.get("max_actions") or 20))
        max_scrolls = max(1, int(payload.get("max_scrolls") or 24))
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)

        with self._lock:
            self._set_status_locked("running", "邮件_清理：进入邮件 #121", phase="mail_cleanup_go_mail")
        frame = runtime.cur_frame(update=True)
        scene_id, score = self._identify_scene_number(ctx, frame, [121, 122, 123, 34, 35, 69])
        text = self._ocr_text(self._ocr_lines(frame))
        if scene_id not in {121, 122, 123} and (
            yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="邮件_清理")
        ):
            frame = runtime.cur_frame(update=True)
            scene_id, score = self._identify_scene_number(ctx, frame, [121, 122, 123, 34, 35, 69])
        if scene_id == 121:
            with self._lock:
                self._status.update({"current_scene": 121, "updated_at": time.time()})
                self._log_locked("info", f"邮件_清理：当前已在邮件 #121 {score:.0f}%，直接扫描")
        else:
            with self._lock:
                self._log_locked("action", "邮件_清理：按 #34/#68/#35 入口进入 #121")
            yield from self._open_mail_cleanup_entry(runtime)
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法清理邮件")
        view121 = View(image121)
        list_shape = view121.get_shape("邮件清单2")
        if list_shape is None:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        processed_count = 0
        seen_count = 0
        scroll_count = 0
        scanned_to_end = False
        while processed_count < max_actions and scroll_count < max_scrolls:
            self._raise_if_stopped(stop_event)
            frame = runtime.cur_frame(update=True)
            rows = self._runtime_mail_rows_from_frame(runtime, view121, frame)
            action_row: _RuntimeMailRow | None = None
            for mail in rows:
                seen_count += 1
                self._prepare_mail_row_policy(mail.raw, action_enabled=True, action_policies={"claim"})
                if mail.status in {"已阅", "锁定"}:
                    continue
                if mail.raw.get("policy") == "claim":
                    action_row = mail
                    break
            if action_row is not None:
                action_started_at = time.monotonic()
                actual_policy = yield from self._claim_runtime_mail_row(runtime, action_row)
                action_elapsed = time.monotonic() - action_started_at
                self._log("detail", f"邮件_清理：处理「{action_row.title}」耗时 {action_elapsed:.1f}s，动作 {actual_policy}")
                self._update_packet_mail_action_for_row(
                    action_row.raw,
                    status=f"{actual_policy}_requested",
                    evidence={
                        "runtime_requested_action": actual_policy,
                        "runtime_action_requested_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                        "runtime_action_source": "mail_cleanup",
                    },
                )
                processed_count += 1
                continue

            scroll_started_at = time.monotonic()
            yield from list_shape.load(runtime)
            scroll_elapsed = time.monotonic() - scroll_started_at
            self._log("detail", f"邮件_清理：翻页 {scroll_count + 1} 耗时 {scroll_elapsed:.1f}s，load_new={bool(runtime.attrs.get('load_new'))}")
            if not runtime.attrs.get("load_new"):
                scanned_to_end = True
                break
            scroll_count += 1

        reached_scroll_limit = not scanned_to_end and processed_count < max_actions
        if reached_scroll_limit:
            self._log("info", f"邮件_清理：达到 max_scrolls={max_scrolls} 仍未确认到底，继续一键删除已阅")

        if scanned_to_end or reached_scroll_limit:
            delete_read_shape = view121.get_shape("一键删除")
            if delete_read_shape is not None:
                with self._lock:
                    self._set_status_locked("running", "邮件_清理：一键删除已阅", phase="mail_cleanup_delete_read", current_scene=121)
                    self._log_locked("action", "邮件_清理：点击 #121「一键删除」清理已阅")
                delete_read_shape.click(runtime)
                yield from runtime.wait_view(121, timeout=8.0, label="邮件_清理：一键删除后返回 #121")
            else:
                self._log("error", "邮件_清理：缺少 #121「一键删除」标注，跳过清理已阅")
        elif processed_count >= max_actions:
            self._log("info", f"邮件_清理：达到 max_actions={max_actions}，跳过一键删除已阅")

        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_清理：完成，见到 {seen_count} 封，领取 {processed_count} 封，滚动 {scroll_count} 次",
                phase="mail_cleanup_done",
                current_scene=121,
            )
            self._log_locked("success", self._status["message"])
        return "success"

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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        required_images = {69: "日常", 178: "首领列表", 179: "首领详情", 180: "挑战中", 181: "挑战完", 182: "首领列表刷新时间"}
        for scene_id, label in required_images.items():
            if not isinstance(images.get(scene_id), dict):
                raise RuntimeError(f"缺少 #{scene_id}「{label}」标注，无法执行日常_首领")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, score, _frame = self._current_scene_number(ctx)
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if scene_id == 180:
                return (yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload))
            if scene_id == 181:
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
        else:
            current_text = self._daily_boss_status_text_from_frame(ctx, _frame)
            if self._daily_boss_done_text(current_text):
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
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
            if self._daily_boss_combat_in_progress_text(current_text):
                return (yield from self._wait_daily_boss_after_challenge(ctx, stop_event, payload))

        if scene_id != 179:
            if scene_id != 178:
                if scene_id != 69:
                    world_text = self._ocr_text(self._ocr_lines(_frame))
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

        return (yield from self._handle_daily_boss_detail(ctx, stop_event, payload))

    def _open_daily_boss_list_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找击败首领")
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找击败首领")
        last_signature = ""
        max_scrolls = 30
        for scroll_index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：查找日常任务「击败首领」 {scroll_index}/{max_scrolls}",
                    phase="daily_boss_find_daily_entry",
                    current_scene=69,
                )
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            matches = self._daily_entry_matches(lines, image69, title_pattern=r"击\s*败\s*首\s*领")
            if matches:
                x, y, text = matches[0]
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_首领：点击日常任务 {text}",
                        phase="daily_boss_click_daily_entry",
                        current_scene=69,
                    )
                    self._log_locked("action", f"日常_首领：点击 #69「{text}」")
                self._click_frame_point(ctx, image69, x, y)
                yield from self._wait_scene_id(ctx, stop_event, 178, timeout=18.0, label="日常_首领：等待首领列表 #178")
                return "success"

            signature = self._vertical_text_signature_in_shape(lines, image69, "滚动窗口", exclude_boxes=self._occlusion_marker_boxes(ctx, image69))
            if signature and signature == last_signature:
                break
            last_signature = signature
            with self._lock:
                self._log_locked("action", f"日常_首领：未找到「击败首领」，滚动日常列表 {scroll_index + 1}")
            self._scroll_shape_content(ctx, image69, list_shape)
            yield from self._wait_scroll_settle(ctx, stop_event)
        raise RuntimeError("日常_首领：#69 日常列表未找到「击败首领」")

    def _open_watched_daily_boss_detail(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image178 = ctx.get("images", {}).get(178)
        if not isinstance(image178, dict):
            raise RuntimeError("缺少 #178「首领列表」标注，无法查找注视中首领")
        list_shape = self._find_shape(image178, "首领列表")
        if list_shape is None:
            raise RuntimeError("缺少 #178「首领列表」滚动区域标注，无法查找注视中首领")
        xianjie_shape = self._find_shape(image178, "仙界")
        if xianjie_shape is not None:
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", "日常_首领：确认仙界页签", phase="daily_boss_open_xianjie", current_scene=178)
                self._log_locked("action", "日常_首领：点击 #178「仙界」页签")
            self._click_shape(ctx, image178, xianjie_shape, frame)
            yield from self._wait_scene_id(ctx, stop_event, 178, timeout=8.0, label="日常_首领：等待仙界首领列表 #178")

        remaining = self._daily_boss_reward_remaining_from_scene(ctx, image178)
        if remaining == 0:
            next_time = self._next_daily_boss_reset_time_text()
            scheduler_task_id = "daily-boss"
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="daily_boss",
                label="日常_首领",
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
                    self._log_locked("success", self._status["message"])
                return "done"

        last_signature = ""
        max_scrolls = 8
        for scroll_index in range(max_scrolls + 1):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：查找仙界注视中首领 {scroll_index}/{max_scrolls}",
                    phase="daily_boss_find_watched",
                    current_scene=178,
                )
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            matches = self._ocr_centers_in_shape(lines, image178, "首领列表", include=("注视",))
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
                self._click_frame_point(ctx, image178, click_x, y)
                yield from self._wait_scene_id(ctx, stop_event, 179, timeout=18.0, label="日常_首领：等待首领详情 #179")
                return "opened"

            signature = self._vertical_text_signature_in_shape(lines, image178, "首领列表")
            if signature and signature == last_signature:
                break
            last_signature = signature
            with self._lock:
                self._log_locked("action", f"日常_首领：未找到「注视中」，滚动首领列表 {scroll_index + 1}")
            self._scroll_shape_content(ctx, image178, list_shape)
            yield from self._wait_scroll_settle(ctx, stop_event)
        raise RuntimeError("日常_首领：仙界首领列表未找到「注视中」目标")

    def _handle_daily_boss_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        image179 = ctx.get("images", {}).get(179)
        if not isinstance(image179, dict):
            raise RuntimeError("缺少 #179「首领详情」标注，无法处理首领挑战")
        frame = self._screencap(ctx)
        lines = self._ocr_lines_in_shapes(frame, image179, ("神识注视", "剩余奖励次数", "挑战状态"), padding=20)
        detail_text = self._ocr_text(lines)
        remaining = _parse_daily_boss_reward_remaining(detail_text)
        if remaining == 0:
            next_time = self._next_daily_boss_reset_time_text()
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
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
            next_time = (_now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_首领：首领尚未刷新，{detail_text}，下次 {next_time}",
                    phase="daily_boss_wait_cd",
                    current_scene=179,
                )
                self._log_locked("success", self._status["message"])
            yield from self._return_daily_boss_to_world(ctx, stop_event)
            return "success"

        if "前往挑战" not in detail_text:
            fallback_seconds = int(payload.get("fallback_seconds") or 300)
            next_time = (_now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
            )
            self._log("skip", f"日常_首领：未识别到「前往挑战」或 CD，当前文本：{detail_text or '空'}；{next_time} 兜底重试")
            return "skipped"

        runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
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
        image179 = ctx.get("images", {}).get(179)
        stuck_twenty_count = 0
        stuck_boss_map_count = 0
        stuck_twenty_threshold = max(2, int(payload.get("boss_twenty_percent_stuck_count") or 5))
        stuck_boss_map_threshold = max(2, int(payload.get("boss_map_stuck_count") or 5))
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            if stop_event.wait(3.0):
                self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            scene_id, score, frame = self._current_scene_number(ctx)
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
                lines = self._ocr_lines_in_shapes(frame, image179, ("剩余奖励次数", "挑战状态"), padding=20)
                text = self._ocr_text(lines)
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
        fallback_seconds = int(payload.get("fallback_seconds") or 300)
        next_time = (_now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
            task_type="daily_boss",
            label="日常_首领",
        )
        self._log("skip", f"日常_首领：等待 #181「封印」超时，未确认挑战完成，{next_time} 重试确认")
        return "skipped"

    def _complete_daily_boss_from_done_frame(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]) -> str:
        next_time, source = yield from self._record_daily_boss_next_time_after_done(ctx, stop_event, payload)
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：已识别 #181 封印完成，{source}，下次 {next_time}",
                phase="daily_boss_done",
                current_scene=181,
            )
            self._log_locked("success", self._status["message"])
        yield from self._return_daily_boss_to_world(ctx, stop_event)
        return "success"

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
            scene_id, _score, _frame = self._current_scene_number(ctx)
            if scene_id == 181:
                return (yield from self._complete_daily_boss_from_done_frame(ctx, stop_event, payload))
            next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
            self._log("skip", f"日常_首领：{reason}，离开后未能回到 #178 复核奖励次数，{next_time} 复查")
            return "skipped"

        next_time, source = self._record_daily_boss_next_time_from_current_list(ctx, payload)
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_首领：{reason}，已回列表复核，{source}，下次 {next_time}",
                phase="daily_boss_done_after_stuck_20",
                current_scene=178,
            )
            self._log_locked("success", self._status["message"])
        yield from self._return_daily_boss_to_world(ctx, stop_event)
        return "success"

    def _return_daily_boss_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            with self._lock:
                self._log_locked("warning", "日常_首领：缺少资产树路径，无法收尾回世界 #34")
            return "skipped"
        self._clear_tick_frame(ctx)
        scene_id, _score, _frame = self._current_scene_number(ctx)
        if scene_id == 34:
            yield from self._ensure_daily_lingzu_outer_world(ctx, stop_event)
            return "success"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        with self._lock:
            self._set_status_locked("running", "日常_首领：收尾回到世界 #34", phase="daily_boss_return_world", current_scene=scene_id)
            self._log_locked("action", "日常_首领：完成后按场景图回到 #34 世界")
        try:
            self._clear_tick_frame(ctx)
            runtime.clear_frame()
            yield from runtime.goto_view(34)
            self._clear_tick_frame(ctx)
            scene_id, _score, _frame = self._current_scene_number(ctx)
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
            yield from self._wait_scene_id(ctx, stop_event, 178, timeout=8.0, label="日常_首领：等待首领列表 #178")
            return True
        except RuntimeError:
            pass
        scene_id, _score, _frame = self._current_scene_number(ctx)
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
        scene_id, _score, _frame = self._current_scene_number(ctx)
        if scene_id != 178:
            runtime = self._fanxiu_runtime(ctx, ctx["asset_tree_path"], stop_event=stop_event)
            view181 = runtime.get_view(181)
            leave_shape = view181.get_shape("离开") if isinstance(view181, View) else None
            if leave_shape is not None:
                with self._lock:
                    self._set_status_locked("running", "日常_首领：挑战完成，点击离开回列表读取刷新时间", phase="daily_boss_leave_done", current_scene=181)
                    self._log_locked("action", "日常_首领：点击 #181「离开」")
                leave_shape.click(runtime)
                try:
                    yield from self._wait_scene_id(ctx, stop_event, 178, timeout=20.0, label="日常_首领：等待首领列表 #178")
                except RuntimeError as exc:
                    with self._lock:
                        self._log_locked("warning", f"日常_首领：离开 #181 后未能回到 #178 读取刷新时间：{exc}")
            else:
                with self._lock:
                    self._log_locked("warning", "日常_首领：缺少 #181「离开」标注，无法回列表读取 #182 刷新时间")

        scene_id, _score, _frame = self._current_scene_number(ctx)
        if scene_id == 178:
            return self._record_daily_boss_next_time_from_current_list(ctx, payload)

        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "未能可靠读取 #182 刷新时间，半小时后复查"

    def _record_daily_boss_next_time_from_current_list(self, ctx: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str]:
        remaining = self._daily_boss_reward_remaining_from_scene(ctx, ctx.get("images", {}).get(178) or {})
        if remaining == 0:
            next_time = self._next_daily_boss_reset_time_text()
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "daily-boss"),
                next_time,
                task_type="daily_boss",
                label="日常_首领",
            )
            return next_time, "奖励次数已用尽"
        cd_seconds, cd_text = self._daily_boss_refresh_cd_from_list(ctx)
        if cd_seconds and cd_seconds > 0:
            next_time = self._record_daily_boss_recheck_time(payload, seconds=cd_seconds + 10)
            return next_time, f"按 #182 刷新时间读取 {cd_text or str(cd_seconds) + ' 秒'}"
        next_time = self._record_daily_boss_recheck_time(payload, seconds=1800)
        return next_time, "奖励次数未用尽但未读到 #182 刷新时间，半小时后复查"

    def _daily_boss_status_text_from_frame(self, ctx: dict[str, Any], frame: str | None = None) -> str:
        frame_data_url = frame or self._screencap(ctx)
        return self._ocr_text(self._ocr_lines(frame_data_url))

    def _daily_boss_combat_in_progress_text(self, text: str) -> bool:
        return "首领" in text and any(fragment in text for fragment in ("自动战斗中", "后刷新", "数据统计", "伤害"))

    def _daily_boss_done_text(self, text: str) -> bool:
        return "封印" in _sanitize_ocr_text(text)

    def _daily_boss_stuck_map_text(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "离开" in normalized and "数据统计" in normalized and "自动战斗中" not in normalized

    def _record_daily_boss_recheck_time(self, payload: dict[str, Any], *, seconds: int) -> str:
        next_time = (_now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "daily-boss"),
            next_time,
            task_type="daily_boss",
            label="日常_首领",
        )
        return next_time

    def _daily_boss_refresh_cd_from_list(self, ctx: dict[str, Any]) -> tuple[int | None, str]:
        images = ctx.get("images", {}) if isinstance(ctx.get("images"), dict) else {}
        image182 = images.get(182)
        image178 = images.get(178)
        frame = self._screencap(ctx)
        texts: list[str] = []
        if isinstance(image182, dict):
            lines = self._ocr_lines_in_shapes(frame, image182, ("刷新时间",), padding=20)
            text = self._ocr_text(lines)
            if text:
                texts.append(text)
                cd_seconds = _parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0:
                    return cd_seconds, text
        if isinstance(image178, dict):
            lines = self._ocr_lines_in_shapes(frame, image178, ("首领列表",), padding=8)
            text = self._ocr_text(lines)
            if text:
                texts.append(text)
                cd_seconds = _parse_daily_boss_cd_seconds(text)
                if cd_seconds and cd_seconds > 0 and "刷新" in text:
                    return cd_seconds, text
        return None, " ".join(texts)

    def _daily_boss_reward_remaining_from_scene(self, ctx: dict[str, Any], image: dict[str, Any]) -> int | None:
        frame = self._screencap(ctx)
        lines = self._ocr_lines_in_shapes(frame, image, ("剩余奖励次数",), padding=12)
        return _parse_daily_boss_reward_remaining(self._ocr_text(lines))

    def _next_daily_boss_reset_time_text(self) -> str:
        now = _now()
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

    def _record_daily_lingzu_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_lingzu_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-lingzu")
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_lingzu",
            label="日常_灵祖",
        )
        self._log("success", f"日常_灵祖：{message}，下次 {next_time}")
        return next_time

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
        if discovered_next_time:
            with self._lock:
                self._set_status_locked(
                    "done",
                    f"日常_灵祖：已记录今日完成，下次 {discovered_next_time}",
                    phase="daily_lingzu_already_done",
                )
                self._log_locked("success", self._status["message"])
            return "success"
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        required_images = {
            34: "世界",
            69: "日常",
            183: "灵祖活动列表",
            184: "灵祖挑战详情",
            185: "灵祖挑战过场",
            186: "灵祖奖励浮层",
            187: "战灵长老",
            188: "圣雷龙妖祖",
            189: "灵祖挑战结算",
        }
        for scene_id, label in required_images.items():
            if not isinstance(images.get(scene_id), dict):
                raise RuntimeError(f"缺少 #{scene_id}「{label}」标注，无法执行日常_灵祖")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        scene_id, _score, frame = self._current_scene_number(ctx)
        if scene_id == 186:
            self._record_daily_lingzu_done(payload, message="当前已在灵祖奖励完成态")
            yield from self._return_daily_lingzu_to_world(ctx, stop_event)
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
            world_text = self._ocr_text(self._ocr_lines(frame))
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
            yield from self._return_daily_lingzu_to_world(ctx, stop_event)
            return "success"

        detail_status = yield from self._open_daily_lingzu_detail(ctx, runtime, stop_event, payload)
        if detail_status == "done":
            yield from self._return_daily_lingzu_to_world(ctx, stop_event)
            return "success"

        return (yield from self._run_daily_lingzu_challenge(ctx, runtime, stop_event, payload))

    def _return_daily_lingzu_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            with self._lock:
                self._log_locked("warning", "日常_灵祖：缺少资产树路径，无法收尾回世界 #34")
            return "skipped"
        self._clear_tick_frame(ctx)
        scene_id, _score, _frame = self._current_scene_number(ctx)
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
        image187 = images.get(187)
        image188 = images.get(188)
        if not all(isinstance(item, dict) for item in (image69, image183, image184, image187, image188)):
            raise RuntimeError("日常_灵祖：缺少 #69/#183/#184/#187/#188 返回世界标注")

        if scene_id == 69:
            exit_shape = self._find_shape(image69, "退出")
            if exit_shape is None:
                raise RuntimeError("日常_灵祖：缺少 #69「退出」标注，无法回世界")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：从日常列表返回世界", phase="daily_lingzu_return_daily", current_scene=69)
                self._log_locked("action", "日常_灵祖：点击 #69「退出」")
            x, y = ActionPlanner().shape_center(image69, exit_shape)
            self._click_frame_point(ctx, image69, x, y)
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_灵祖")):
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
            yield from self._wait_scene_id(ctx, stop_event, 34, timeout=18.0, label="日常_灵祖：等待世界 #34")

        if scene_id == 188:
            back_shape = self._find_shape(image188, "返回")
            if back_shape is None:
                raise RuntimeError("日常_灵祖：缺少 #188「返回」标注，无法回世界")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：从圣雷龙妖祖返回战灵长老", phase="daily_lingzu_return_elder", current_scene=188)
                self._log_locked("action", "日常_灵祖：点击 #188「返回」")
            self._click_shape(ctx, image188, back_shape, frame_data_url=frame)
            scene_id, _score = yield from self._wait_scene_id(ctx, stop_event, 187, timeout=18.0, label="日常_灵祖：等待战灵长老 #187")

        if scene_id == 187:
            blank_shape = self._find_shape(image187, "空白")
            if blank_shape is None:
                raise RuntimeError("日常_灵祖：缺少 #187「空白」标注，无法返回活动列表")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：关闭战灵长老对话", phase="daily_lingzu_close_elder", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「空白」")
            self._click_shape(ctx, image187, blank_shape, frame_data_url=frame)
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [183, 34],
                timeout=18.0,
                label="日常_灵祖：等待灵祖活动列表 #183 或世界 #34",
            )

        if scene_id == 184:
            blank_shape = self._find_shape(image184, "空白")
            if blank_shape is None:
                raise RuntimeError("日常_灵祖：缺少 #184「空白」标注，无法返回活动列表")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：关闭灵祖详情", phase="daily_lingzu_close_detail", current_scene=184)
                self._log_locked("action", "日常_灵祖：点击 #184「空白」")
            self._click_shape(ctx, image184, blank_shape, frame_data_url=frame)
            scene_id, _score = yield from self._wait_scene_id(ctx, stop_event, 183, timeout=18.0, label="日常_灵祖：等待灵祖活动列表 #183")

        if scene_id == 183:
            back_shape = self._find_shape(image183, "返回")
            if back_shape is None:
                raise RuntimeError("日常_灵祖：缺少 #183「返回」标注，无法回世界")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：返回世界", phase="daily_lingzu_return_world_click", current_scene=183)
                self._log_locked("action", "日常_灵祖：点击 #183「返回」")
            self._click_shape(ctx, image183, back_shape, frame_data_url=frame)
            yield from self._wait_scene_id(ctx, stop_event, 34, timeout=18.0, label="日常_灵祖：等待世界 #34")

        self._clear_tick_frame(ctx)
        scene_id, _score, _frame = self._current_scene_number(ctx)
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
        frame = self._screencap(ctx)
        text = self._ocr_text(self._ocr_lines(frame))
        if not self._daily_lingzu_world_text_is_internal_area(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        leave_shape = self._find_shape(image85, "离开")
        if leave_shape is None:
            raise RuntimeError("日常_灵祖：缺少 #85「离开」标注，无法离开宗门内部")
        with self._lock:
            self._set_status_locked("running", "日常_灵祖：当前仍在宗门内部，点击离开", phase="daily_lingzu_leave_internal_area", current_scene=85)
            self._log_locked("action", "日常_灵祖：点击 #85「离开」")
        x, y = ActionPlanner().shape_center(image85, leave_shape)
        self._click_frame_point(ctx, image85, x, y)

        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            scene_id, score = self._identify_scene_number(ctx, frame, [34])
            last_scene_id, last_score, last_text = scene_id, score, text
            if scene_id == 34 and not self._daily_lingzu_world_text_is_internal_area(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_灵祖：已离开宗门内部并回到外层世界 #34 {score:.0f}%")
                return "success"
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
        )
        self._log("success", f"日常_剑灵：{message}，下次 {next_time}")
        return next_time

    def _daily_jianling_progress_done(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        return bool(re.search(r"(?:挑战或扫荡淬剑试炼|淬剑试炼).*(?:1/1|已完成)", normalized))

    def _daily_jianling_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(re.search(r"剩余次数[:：]?(?:0|O)(?:\\+)?", normalized, re.IGNORECASE))

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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        required_images = {34: "世界", 69: "日常", 85: "某区域内部", 190: "剑灵淬剑试炼", 191: "剑灵扫荡确认", 192: "剑灵扫荡结果"}
        for scene_id, label in required_images.items():
            if not isinstance(images.get(scene_id), dict):
                raise RuntimeError(f"缺少 #{scene_id}「{label}」标注，无法执行日常_剑灵")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [190, 191, 192, 69, 34])
        if scene_id == 192:
            yield from self._finish_daily_jianling_result(ctx, stop_event)
            scene_id = 190
        if scene_id == 191:
            yield from self._confirm_daily_jianling_sweep(ctx, stop_event)
            yield from self._finish_daily_jianling_result(ctx, stop_event)
            scene_id = 190
        if scene_id == 190:
            text = self._ocr_text(self._ocr_lines(self._screencap(ctx)))
            if self._daily_jianling_remaining_zero(text):
                self._record_daily_jianling_done(payload, message="淬剑试炼剩余次数为 0")
                yield from self._return_daily_jianling_to_world(ctx, stop_event)
                return "success"
            yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
            return "success"

        if scene_id != 69:
            world_text = self._ocr_text(self._ocr_lines(frame))
            scene_id = yield from self._enter_daily_from_world_like(
                ctx,
                runtime,
                stop_event,
                frame,
                scene_id,
                world_text,
                label="日常_剑灵",
            )

        daily_status = yield from self._open_daily_jianling_from_daily(ctx, stop_event, payload)
        if daily_status == "done":
            self._record_daily_jianling_done(payload, message="日常列表显示已完成")
            yield from self._return_daily_jianling_to_world(ctx, stop_event)
            return "success"
        yield from self._run_daily_jianling_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_jianling_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找淬剑试炼")
        max_scrolls = int(payload.get("max_scrolls") or payload.get("jianling_max_scrolls") or 10)
        reverse_scrolls = int(payload.get("reverse_scrolls") or payload.get("jianling_reverse_scrolls") or max_scrolls)
        for direction, scroll_count in [("down", max_scrolls), ("up", reverse_scrolls)]:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_剑灵：查找日常任务「淬剑试炼」 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_jianling_find_daily_entry",
                        current_scene=69,
                    )
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
                text = self._ocr_text(lines)
                if self._daily_jianling_progress_done(text):
                    return "done"
                matches = self._ocr_centers_in_shape(lines, image69, "滚动窗口", include=("淬剑",))
                if not matches:
                    matches = self._ocr_centers_in_shape(lines, image69, "滚动窗口", include=("剑试",))
                if matches:
                    x, y, matched_text = matches[0]
                    with self._lock:
                        self._set_status_locked("running", f"日常_剑灵：点击日常任务 {matched_text}", phase="daily_jianling_click_daily_entry", current_scene=69)
                        self._log_locked("action", f"日常_剑灵：点击 #69「{matched_text}」")
                    self._click_frame_point(ctx, image69, x, y)
                    yield from self._wait_scene_id(ctx, stop_event, 190, timeout=18.0, label="日常_剑灵：等待淬剑试炼 #190")
                    return "open"
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_剑灵：未找到「淬剑试炼」，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
        raise RuntimeError("日常_剑灵：日常列表未找到「淬剑试炼」任务")

    def _run_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image190 = ctx.get("images", {}).get(190)
        if not isinstance(image190, dict):
            raise RuntimeError("缺少 #190「剑灵淬剑试炼」标注，无法扫荡")
        sweep_shape = self._find_shape(image190, "扫荡")
        if sweep_shape is None:
            raise RuntimeError("缺少 #190「扫荡」标注，无法扫荡")
        frame = self._screencap(ctx)
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：点击扫荡", phase="daily_jianling_sweep", current_scene=190)
            self._log_locked("action", "日常_剑灵：点击 #190「扫荡」")
        x, y = ActionPlanner().shape_center(image190, sweep_shape)
        self._click_frame_point(ctx, image190, x, y)
        yield from self._wait_scene_id(ctx, stop_event, 191, timeout=18.0, label="日常_剑灵：等待扫荡确认 #191")
        yield from self._confirm_daily_jianling_sweep(ctx, stop_event)
        yield from self._finish_daily_jianling_result(ctx, stop_event)
        self._record_daily_jianling_done(payload, message="淬剑试炼扫荡完成")
        yield from self._return_daily_jianling_to_world(ctx, stop_event)

    def _confirm_daily_jianling_sweep(self, ctx: dict[str, Any], stop_event: threading.Event):
        image191 = ctx.get("images", {}).get(191)
        if not isinstance(image191, dict):
            raise RuntimeError("缺少 #191「剑灵扫荡确认」标注，无法确认扫荡")
        confirm_shape = self._find_shape(image191, "进行扫荡")
        if confirm_shape is None:
            raise RuntimeError("缺少 #191「进行扫荡」标注，无法确认扫荡")
        with self._lock:
            self._set_status_locked("running", "日常_剑灵：确认进行扫荡", phase="daily_jianling_confirm_sweep", current_scene=191)
            self._log_locked("action", "日常_剑灵：点击 #191「进行扫荡」")
        x, y = ActionPlanner().shape_center(image191, confirm_shape)
        self._click_frame_point(ctx, image191, x, y)
        yield from self._wait_scene_id(ctx, stop_event, 192, timeout=18.0, label="日常_剑灵：等待扫荡结果 #192")

    def _finish_daily_jianling_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        image192 = ctx.get("images", {}).get(192)
        if not isinstance(image192, dict):
            raise RuntimeError("缺少 #192「剑灵扫荡结果」标注，无法继续")
        continue_shape = self._find_shape(image192, "点击继续")
        if continue_shape is None:
            raise RuntimeError("缺少 #192「点击继续」标注，无法继续")
        for index in range(8):
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            scene_id, _score = self._identify_scene_number(ctx, frame, [190, 192])
            if scene_id == 190 or ("淬剑试炼" in text and "通关进度" in text):
                return "success"
            if scene_id == 192 or "点击" in text or "扫荡奖励" in text:
                with self._lock:
                    self._set_status_locked("running", f"日常_剑灵：关闭扫荡结果 {index + 1}", phase="daily_jianling_continue_result", current_scene=192)
                    self._log_locked("action", "日常_剑灵：点击 #192「点击继续」")
                x, y = ActionPlanner().shape_center(image192, continue_shape)
                self._click_frame_point(ctx, image192, x, y)
                self._clear_tick_frame(ctx)
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_剑灵：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_剑灵：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_jianling_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image69 = images.get(69)
        image190 = images.get(190)
        if not isinstance(image69, dict) or not isinstance(image190, dict):
            raise RuntimeError("日常_剑灵：缺少 #69/#190 返回世界标注")
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [190, 69, 34])
        if scene_id == 190:
            back_shape = self._find_shape(image190, "返回")
            if back_shape is None:
                raise RuntimeError("日常_剑灵：缺少 #190「返回」标注，无法回世界")
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：退出淬剑试炼", phase="daily_jianling_exit_main", current_scene=190)
                self._log_locked("action", "日常_剑灵：点击 #190「返回」")
            x, y = ActionPlanner().shape_center(image190, back_shape)
            self._click_frame_point(ctx, image190, x, y)
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [69, 34],
                timeout=18.0,
                label="日常_剑灵：等待日常 #69 或世界 #34",
            )
        if scene_id == 69:
            exit_shape = self._find_shape(image69, "退出")
            if exit_shape is None:
                raise RuntimeError("日常_剑灵：缺少 #69「退出」标注，无法回世界")
            with self._lock:
                self._set_status_locked("running", "日常_剑灵：从日常列表返回世界", phase="daily_jianling_return_daily", current_scene=69)
                self._log_locked("action", "日常_剑灵：点击 #69「退出」")
            x, y = ActionPlanner().shape_center(image69, exit_shape)
            self._click_frame_point(ctx, image69, x, y)
            yield from self._wait_scene_id(ctx, stop_event, 34, timeout=18.0, label="日常_剑灵：等待世界 #34")
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
        for line in lines:
            text = re.sub(r"\s+", "", _sanitize_ocr_text(line.get("text")))
            if text != "离开":
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if cx >= width * 0.72 and height * 0.30 <= cy <= height * 0.70:
                click_x = max(0.0, min(width, cx - min(16.0, w * 0.25)))
                click_y = max(0.0, min(height, cy - max(56.0, h * 1.75)))
                matches.append((click_x, click_y, text))
        return sorted(matches, key=lambda item: (item[0], item[1]), reverse=True)

    def _leave_world_side_scene_if_present(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        frame: str,
        text: str,
        *,
        label: str,
    ):
        if not self._daily_assistant_text_is_world_like(text):
            return False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        ref_image = images.get(34) if isinstance(images.get(34), dict) else {"filename": "world_runtime.png", "width": 900, "height": 1600}
        width, height = self._frame_size(ref_image)
        lines = self._ocr_lines(frame)
        matches = self._world_scene_leave_matches(lines, width=width, height=height)
        if not matches:
            return False
        x, y, matched_text = matches[0]
        with self._lock:
            self._set_status_locked("running", f"{label}：当前在场景内，点击右侧「离开」", phase="world_side_scene_leave", current_scene=None)
            self._log_locked("action", f"{label}：OCR 命中右侧「{matched_text}」，先离开场景")
        self._click_frame_point(ctx, ref_image, x, y)
        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
        confirm_frame = self._screencap(ctx)
        confirm_scene_id, _confirm_score = self._identify_scene_number(ctx, confirm_frame, [86])
        confirm_text = self._ocr_text(self._ocr_lines(confirm_frame))
        if confirm_scene_id == 86 or "是否离开" in _sanitize_ocr_text(confirm_text):
            image86 = images.get(86) if isinstance(images.get(86), dict) else None
            confirm_shape = self._find_shape(image86, "确认") if isinstance(image86, dict) else None
            if isinstance(image86, dict) and confirm_shape is not None:
                x2, y2 = ActionPlanner().shape_center(image86, confirm_shape)
                with self._lock:
                    self._set_status_locked("running", f"{label}：确认离开当前场景", phase="world_side_scene_leave_confirm", current_scene=86)
                    self._log_locked("action", f"{label}：点击 #86「确认」离开场景")
                self._click_frame_point(ctx, image86, x2, y2)
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
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
        if scene_id == 69:
            return 69
        if scene_id != 34 and not self._daily_assistant_text_is_world_like(text):
            raise RuntimeError(f"{label}：当前不在可识别的世界或日常页，无法开始")
        if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=label)):
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, [69, 34])
            text = self._ocr_text(self._ocr_lines(frame))
            if scene_id == 69:
                return 69
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        if not isinstance(image34, dict):
            raise RuntimeError(f"{label}：缺少 #34「世界」标注，无法进入日常")
        with self._lock:
            self._set_status_locked("running", f"{label}：进入日常 #69", phase="daily_go_daily", current_scene=scene_id)
            self._log_locked("action", f"{label}：按场景图跳转到 #69")
        try:
            yield from runtime.goto_view(69)
            return 69
        except Exception as exc:
            raise RuntimeError(f"{label}：无法通过场景图跳转到 #69；需要补当前场景到日常页的路由/返回/离开标注：{exc}") from exc

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
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：退出绿瓶", phase="daily_lingta_exit_green_bottle", current_scene=20)
            self._log_locked("action", "日常_灵塔：点击 #20「回到世界」")
        x, y = ActionPlanner().shape_center(image20, back_shape)
        self._click_frame_point(ctx, image20, x, y)
        self._clear_tick_frame(ctx)
        yield BehaviorTreeStatus.RUNNING

        start = time.monotonic()
        clicked_outer_world = False
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            text = self._ocr_text(lines)
            scene_id, score = self._identify_scene_number(ctx, frame, [34, 20])
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
                self._click_frame_point(ctx, image20, x, y)
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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        required_images = {
            20: "绿瓶",
            34: "世界",
            69: "日常",
            193: "灵塔区域入口",
            194: "混沌灵塔界面",
            195: "灵塔扫荡确认",
            196: "灵塔扫荡结果",
        }
        for scene_id, label in required_images.items():
            if not isinstance(images.get(scene_id), dict):
                raise RuntimeError(f"缺少 #{scene_id}「{label}」标注，无法执行日常_灵塔")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [196, 195, 194, 193, 69, 34, 20])
        if scene_id == 20:
            yield from self._leave_daily_lingta_green_bottle(ctx, stop_event)
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, [196, 195, 194, 193, 69, 34, 20])
        if scene_id == 196:
            yield from self._finish_daily_lingta_result(ctx, stop_event)
            scene_id = 194
        if scene_id == 195:
            yield from self._confirm_daily_lingta_sweep(ctx, stop_event)
            yield from self._finish_daily_lingta_result(ctx, stop_event)
            scene_id = 194
        if scene_id == 193:
            yield from self._open_daily_lingta_main_from_entry(ctx, stop_event)
            scene_id = 194
        if scene_id == 194:
            text = self._ocr_text(self._ocr_lines(self._screencap(ctx)))
            if self._daily_lingta_remaining_zero(text):
                self._record_daily_lingta_done(payload, message="混沌灵塔剩余次数为 0")
                yield from self._return_daily_lingta_to_world(ctx, stop_event)
                return "success"
            yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
            return "success"

        if scene_id != 69:
            world_text = self._ocr_text(self._ocr_lines(frame))
            if scene_id == 34 or self._daily_lingta_text_is_world_like(world_text):
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
                    stop_event,
                    frame,
                    scene_id,
                    world_text,
                    label="日常_灵塔",
                )
            else:
                raise RuntimeError("日常_灵塔：当前不在可识别的世界或日常页，无法开始")

        daily_status = yield from self._open_daily_lingta_from_daily(ctx, stop_event, payload)
        if daily_status == "done":
            self._record_daily_lingta_done(payload, message="日常列表显示已完成")
            yield from self._return_daily_lingta_to_world(ctx, stop_event)
            return "success"
        yield from self._run_daily_lingta_sweep(ctx, stop_event, payload)
        return "success"

    def _open_daily_lingta_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找混沌灵塔")
        max_scrolls = int(payload.get("max_scrolls") or payload.get("lingta_max_scrolls") or 10)
        reverse_scrolls = int(payload.get("reverse_scrolls") or payload.get("lingta_reverse_scrolls") or max_scrolls)
        for direction, scroll_count in [("down", max_scrolls), ("up", reverse_scrolls)]:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_灵塔：查找日常任务「混沌灵塔」 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_lingta_find_daily_entry",
                        current_scene=69,
                    )
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
                text = self._ocr_text(lines)
                if self._daily_lingta_progress_done(text):
                    return "done"
                matches = self._ocr_centers_in_shape(lines, image69, "滚动窗口", include=("混沌灵塔",))
                if not matches:
                    matches = self._ocr_centers_in_shape(lines, image69, "滚动窗口", include=("灵塔",))
                if matches:
                    x, y, matched_text = matches[0]
                    with self._lock:
                        self._set_status_locked("running", f"日常_灵塔：点击日常任务 {matched_text}", phase="daily_lingta_click_daily_entry", current_scene=69)
                        self._log_locked("action", f"日常_灵塔：点击 #69「{matched_text}」")
                    self._click_frame_point(ctx, image69, x, y)
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
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_灵塔：未找到「混沌灵塔」，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
        raise RuntimeError("日常_灵塔：日常列表未找到「混沌灵塔」任务")

    def _open_daily_lingta_main_from_entry(self, ctx: dict[str, Any], stop_event: threading.Event):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image193 = images.get(193)
        if not isinstance(image193, dict):
            raise RuntimeError("缺少 #193「灵塔区域入口」标注，无法进入混沌灵塔")
        enter_shape = self._find_shape(image193, "进入")
        if enter_shape is None:
            raise RuntimeError("缺少 #193「进入」标注，无法进入混沌灵塔")
        for index in range(3):
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, [194, 193])
            if scene_id == 194:
                return "success"
            if scene_id == 193:
                with self._lock:
                    self._set_status_locked("running", "日常_灵塔：点击区域入口「进入」", phase="daily_lingta_enter_area", current_scene=193)
                    self._log_locked("action", "日常_灵塔：点击 #193「进入」")
                x, y = ActionPlanner().shape_center(image193, enter_shape)
                self._click_frame_point(ctx, image193, x, y)
        yield from self._wait_scene_id(ctx, stop_event, 194, timeout=18.0, label="日常_灵塔：等待混沌灵塔 #194")
        return "success"

    def _run_daily_lingta_sweep(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image194 = ctx.get("images", {}).get(194)
        if not isinstance(image194, dict):
            raise RuntimeError("缺少 #194「混沌灵塔界面」标注，无法扫荡")
        sweep_shape = self._find_shape(image194, "扫荡")
        if sweep_shape is None:
            raise RuntimeError("缺少 #194「扫荡」标注，无法扫荡")
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：点击扫荡", phase="daily_lingta_sweep", current_scene=194)
            self._log_locked("action", "日常_灵塔：点击 #194「扫荡」")
        x, y = ActionPlanner().shape_center(image194, sweep_shape)
        self._click_frame_point(ctx, image194, x, y)
        yield from self._wait_scene_id(ctx, stop_event, 195, timeout=18.0, label="日常_灵塔：等待扫荡确认 #195")
        yield from self._confirm_daily_lingta_sweep(ctx, stop_event)
        yield from self._finish_daily_lingta_result(ctx, stop_event)
        self._record_daily_lingta_done(payload, message="混沌灵塔扫荡完成")
        yield from self._return_daily_lingta_to_world(ctx, stop_event)

    def _confirm_daily_lingta_sweep(self, ctx: dict[str, Any], stop_event: threading.Event):
        image195 = ctx.get("images", {}).get(195)
        if not isinstance(image195, dict):
            raise RuntimeError("缺少 #195「灵塔扫荡确认」标注，无法确认扫荡")
        confirm_shape = self._find_shape(image195, "进行扫荡")
        if confirm_shape is None:
            raise RuntimeError("缺少 #195「进行扫荡」标注，无法确认扫荡")
        with self._lock:
            self._set_status_locked("running", "日常_灵塔：确认进行扫荡", phase="daily_lingta_confirm_sweep", current_scene=195)
            self._log_locked("action", "日常_灵塔：点击 #195「进行扫荡」")
        x, y = ActionPlanner().shape_center(image195, confirm_shape)
        self._click_frame_point(ctx, image195, x, y)
        yield from self._wait_scene_id(ctx, stop_event, 196, timeout=18.0, label="日常_灵塔：等待扫荡结果 #196")

    def _finish_daily_lingta_result(self, ctx: dict[str, Any], stop_event: threading.Event):
        image196 = ctx.get("images", {}).get(196)
        if not isinstance(image196, dict):
            raise RuntimeError("缺少 #196「灵塔扫荡结果」标注，无法继续")
        continue_shape = self._find_shape(image196, "点击继续")
        if continue_shape is None:
            raise RuntimeError("缺少 #196「点击继续」标注，无法继续")
        for index in range(8):
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            scene_id, _score = self._identify_scene_number(ctx, frame, [194, 196])
            if scene_id == 194 or ("混沌灵塔" in text and "剩余次数" in text):
                return "success"
            if scene_id == 196 or "点击" in text or "扫荡奖励" in text:
                with self._lock:
                    self._set_status_locked("running", f"日常_灵塔：关闭扫荡结果 {index + 1}", phase="daily_lingta_continue_result", current_scene=196)
                    self._log_locked("action", "日常_灵塔：点击 #196「点击继续」")
                x, y = ActionPlanner().shape_center(image196, continue_shape)
                self._click_frame_point(ctx, image196, x, y)
                self._clear_tick_frame(ctx)
                yield BehaviorTreeStatus.RUNNING
                continue
            raise RuntimeError(f"日常_灵塔：扫荡结果页状态异常，文本：{text[:120]}")
        raise RuntimeError("日常_灵塔：扫荡结果点击继续后仍未回到主界面")

    def _return_daily_lingta_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image69 = images.get(69)
        image194 = images.get(194)
        if not isinstance(image69, dict) or not isinstance(image194, dict):
            raise RuntimeError("日常_灵塔：缺少 #69/#194 返回世界标注")
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [194, 69, 20, 34])
        if scene_id == 194:
            back_shape = self._find_shape(image194, "返回")
            if back_shape is None:
                raise RuntimeError("日常_灵塔：缺少 #194「返回」标注，无法回世界")
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：退出混沌灵塔", phase="daily_lingta_exit_main", current_scene=194)
                self._log_locked("action", "日常_灵塔：点击 #194「返回」")
            x, y = ActionPlanner().shape_center(image194, back_shape)
            self._click_frame_point(ctx, image194, x, y)
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [69, 20, 34],
                timeout=18.0,
                label="日常_灵塔：等待日常 #69、绿瓶 #20 或世界 #34",
            )
        if scene_id == 69:
            exit_shape = self._find_shape(image69, "退出")
            if exit_shape is None:
                raise RuntimeError("日常_灵塔：缺少 #69「退出」标注，无法回世界")
            with self._lock:
                self._set_status_locked("running", "日常_灵塔：从日常列表返回世界", phase="daily_lingta_return_daily", current_scene=69)
                self._log_locked("action", "日常_灵塔：点击 #69「退出」")
            x, y = ActionPlanner().shape_center(image69, exit_shape)
            self._click_frame_point(ctx, image69, x, y)
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
        if not isinstance(image34, dict) or not isinstance(image69, dict):
            raise RuntimeError("缺少 #34「世界」或 #69「日常」标注，无法执行日常_挑战仙缘")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [203, 202, 201, 200, 199, 198, 197, 69, 34])
        if scene_id in {200, 201, 202, 203}:
            return (yield from self._run_daily_xianyuan_from_challenge_state(ctx, stop_event, payload, int(scene_id)))
        if scene_id == 199:
            return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
        if scene_id == 198:
            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
        if scene_id == 197:
            return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
        if scene_id != 69:
            text = self._ocr_text(self._ocr_lines(frame))
            if self._daily_xianyuan_text_is_dialogue(text):
                return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
            if self._daily_xianyuan_text_is_detail(text):
                return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
            if scene_id != 34:
                if self._daily_xianyuan_text_is_people_list(text):
                    return (yield from self._run_daily_xianyuan_from_list(ctx, stop_event, payload))
                if not self._daily_lingta_text_is_world_like(text):
                    raise RuntimeError("日常_挑战仙缘：当前不在可识别的世界或日常页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_挑战仙缘")):
                frame = self._screencap(ctx)
                scene_id, _score = self._identify_scene_number(ctx, frame, [69, 34])
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
            yield from self._return_daily_xianyuan_to_world(ctx, stop_event)
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
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError(f"缺少 #69「日常」标注，无法查找{task_label}")
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError(f"缺少 #69「滚动窗口」标注，无法滚动查找{task_label}")
        max_scrolls = int(payload.get("max_scrolls") or 10)
        reverse_scrolls = int(payload.get("reverse_scrolls") or 10)
        for direction, scroll_count in [("down", max_scrolls), ("up", reverse_scrolls)]:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：查找日常任务入口 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_entry_find",
                        current_scene=69,
                    )
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
                matches = self._daily_entry_matches(
                    lines,
                    image69,
                    title_pattern=title_pattern,
                    exclude_pattern=exclude_pattern,
                )
                if matches:
                    x, y, matched_text = matches[0]
                    progress = self._daily_task_row_progress(lines, y)
                    if progress_can_mark_done and progress is not None and progress[0] >= progress[1]:
                        return "done"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"{task_label}：点击日常任务 {matched_text}",
                            phase="daily_entry_click",
                            current_scene=69,
                        )
                        self._log_locked("action", f"{task_label}：点击 #69「{matched_text}」")
                    self._click_frame_point(ctx, image69, x, y)
                    yield from self._wait_runtime_action_settle(
                        ctx,
                        stop_event,
                        seconds=float(payload.get("entry_click_settle_seconds") or 2.0),
                    )
                    return "open"
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"{task_label}：未找到入口，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
        return "not_found"

    def _record_daily_entry_done(self, payload: dict[str, Any], *, task_id: str, task_type: str, label: str, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
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
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [69, 34])
            text = self._ocr_text(self._ocr_lines(frame))
            last_scene_id, last_score, last_text = scene_id, score, text or last_text
            if scene_id in {69, 34}:
                return scene_id, float(score), last_text
            if time.monotonic() - start >= timeout:
                if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                    frame = self._screencap(ctx)
                    scene_id, score = self._identify_scene_number(ctx, frame, [69, 34])
                    text = self._ocr_text(self._ocr_lines(frame))
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
        if not isinstance(image34, dict) or not isinstance(image69, dict):
            raise RuntimeError(f"缺少 #34「世界」或 #69「日常」标注，无法执行{task_label}")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [69, 34])
        text = self._ocr_text(self._ocr_lines(frame))
        if scene_id != 69:
            if scene_id != 34 and not self._daily_lingta_text_is_world_like(text):
                raise RuntimeError(f"{task_label}：当前不在可识别的世界或日常页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                frame = self._screencap(ctx)
                scene_id, _score = self._identify_scene_number(ctx, frame, [69, 34])
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
            yield from self._return_daily_xianyuan_to_world(ctx, stop_event)
            return "success"
        if daily_status == "not_found":
            raise RuntimeError(f"{task_label}：#69 日常列表未找到入口，不能按完成处理")

        scene_id, score, after_text = yield from self._wait_unsupported_daily_entry_after_click(ctx, stop_event, payload, task_label=task_label)
        raise RuntimeError(
            f"{task_label}：已点击 #69 入口，但后续业务状态机尚未迁移；"
            f"当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%，OCR={after_text[:120]}。"
            f"{missing_assets_message}"
        )

    def _daily_free_challenge_remaining_zero(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        return bool(re.search(r"剩余奖励次数[:：]?(?:0|O)(?:/\d{1,3})?", normalized, re.IGNORECASE))

    def _daily_free_challenge_remaining_count(self, text: str) -> int | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = re.sub(r"\s+", "", normalized)
        match = re.search(r"剩余奖励次数[:：]?(\d{1,3}|O)(?:/\d{1,3})?", normalized, re.IGNORECASE)
        if not match:
            return None
        value = match.group(1).replace("O", "0")
        return int(value)

    def _daily_free_challenge_text_is_selection(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "剿灭" in normalized and "剩余奖励次数" not in normalized and ("妖王来袭" in normalized or "妖族袭城" in normalized)

    def _daily_free_challenge_text_is_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "剩余奖励次数" in normalized and "前往剿灭" in normalized

    def _daily_free_challenge_text_is_purchase_modal(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        compact = re.sub(r"\s+", "", normalized)
        return "购买并使用" in compact and ("价格" in compact or "拥有" in compact or "限购次数" in compact)

    def _ocr_line_center_matching(self, lines: list[dict[str, Any]], *patterns: str) -> tuple[float, float, str] | None:
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if not any(re.search(pattern, text) for pattern in patterns):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            return x + w / 2, y + h / 2, text
        return None

    def _record_daily_free_challenge_done(
        self,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        task_label: str,
        message: str,
    ) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or task_id),
            next_time,
            task_type=task_type,
            label=task_label,
        )
        self._log("success", f"{task_label}：{message}，下次 {next_time}")
        return next_time

    def _return_daily_free_challenge_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        task_label: str,
    ):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image69 = images.get(69)
        image183 = images.get(183)
        image187 = images.get(187)
        image188 = images.get(188)
        if not isinstance(image69, dict):
            raise RuntimeError(f"{task_label}：缺少 #69「日常」标注，无法收尾回世界")
        self._clear_tick_frame(ctx)
        for _index in range(4):
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            scene_id, _score = self._identify_scene_number(ctx, frame, [34, 69, 188, 187, 183])
            if scene_id in {34, 69, 188, 187, 183}:
                break
            if not (self._daily_free_challenge_text_is_selection(text) or self._daily_free_challenge_text_is_detail(text)):
                break
            if not isinstance(image188, dict):
                raise RuntimeError(f"{task_label}：免费剿灭页缺少 #188「返回」标注，无法收尾回世界")
            back_shape = self._find_shape(image188, "返回")
            if back_shape is None:
                raise RuntimeError(f"{task_label}：免费剿灭页缺少 #188「返回」标注，无法收尾回世界")
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从免费剿灭页返回", phase="daily_free_challenge_return_ocr_page", current_scene=scene_id)
                self._log_locked("action", f"{task_label}：点击 #188「返回」")
            self._click_shape(ctx, image188, back_shape, frame_data_url=frame)
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
            self._clear_tick_frame(ctx)
        scene_id, _score, _frame = self._current_scene_number(ctx)
        if scene_id == 34:
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        if scene_id == 188:
            if not isinstance(image188, dict):
                raise RuntimeError(f"{task_label}：缺少 #188「返回」标注，无法收尾回世界")
            back_shape = self._find_shape(image188, "返回")
            if back_shape is None:
                raise RuntimeError(f"{task_label}：缺少 #188「返回」标注，无法收尾回世界")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从挑战页返回", phase="daily_free_challenge_return_main", current_scene=188)
                self._log_locked("action", f"{task_label}：点击 #188「返回」")
            self._click_shape(ctx, image188, back_shape, frame_data_url=frame)
            scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                ctx,
                stop_event,
                [69, 34, 187, 183],
                timeout=18.0,
                label=f"{task_label}：等待返回日常或世界",
            )
        if scene_id == 187 and isinstance(image187, dict):
            blank_shape = self._find_shape(image187, "空白")
            if blank_shape is not None:
                frame = self._screencap(ctx)
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭中间对话", phase="daily_free_challenge_close_dialogue", current_scene=187)
                    self._log_locked("action", f"{task_label}：点击 #187「空白」")
                self._click_shape(ctx, image187, blank_shape, frame_data_url=frame)
                scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                    ctx,
                    stop_event,
                    [69, 34, 183],
                    timeout=18.0,
                    label=f"{task_label}：等待返回日常或世界",
                )
        if scene_id == 183 and isinstance(image183, dict):
            back_shape = self._find_shape(image183, "返回")
            if back_shape is not None:
                frame = self._screencap(ctx)
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：返回世界", phase="daily_free_challenge_return_world_click", current_scene=183)
                    self._log_locked("action", f"{task_label}：点击 #183「返回」")
                self._click_shape(ctx, image183, back_shape, frame_data_url=frame)
                scene_id, _score = yield from self._wait_daily_lingzu_return_scene(
                    ctx,
                    stop_event,
                    [69, 34],
                    timeout=18.0,
                    label=f"{task_label}：等待返回日常或世界",
                )
        if scene_id == 69:
            exit_shape = self._find_shape(image69, "退出")
            if exit_shape is None:
                raise RuntimeError(f"{task_label}：缺少 #69「退出」标注，无法回世界")
            frame = self._screencap(ctx)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：从日常列表返回世界", phase="daily_free_challenge_return_daily", current_scene=69)
                self._log_locked("action", f"{task_label}：点击 #69「退出」")
            x, y = ActionPlanner().shape_center(image69, exit_shape)
            self._click_frame_point(ctx, image69, x, y)
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
            yield from self._wait_scene_id(ctx, stop_event, 34, timeout=18.0, label=f"{task_label}：等待世界 #34")
        self._clear_tick_frame(ctx)
        scene_id, _score, _frame = self._current_scene_number(ctx)
        if scene_id != 34:
            raise RuntimeError(f"{task_label}：收尾回世界后仍识别为 #{scene_id or 'unknown'}")
        with self._lock:
            self._status.update({"current_scene": 34, "updated_at": time.time()})
        return "success"

    def _run_daily_free_challenge_from_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        task_id: str,
        task_type: str,
        task_label: str,
    ):
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image188 = images.get(188)
        image189 = images.get(189)
        image69 = images.get(69)
        if not isinstance(image69, dict):
            raise RuntimeError(f"{task_label}：缺少 #69「日常」标注，无法按 OCR 点击妖王/妖族页")
        max_runs = int(payload.get("max_free_challenges") or 5)
        run_count = 0
        scene_id: int | None
        score: float
        start = time.monotonic()
        while True:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            text = self._ocr_text(lines)
            scene_id, score = self._identify_scene_number(ctx, frame, [188, 189, 69, 34])
            if self._daily_free_challenge_text_is_purchase_modal(text):
                raise RuntimeError(f"{task_label}：出现「购买并使用」弹窗，默认不购买次数或道具，已停止等待人工关闭")
            if self._daily_free_challenge_text_is_selection(text):
                match = self._ocr_line_center_matching(lines, r"推荐?剿灭|荐剿灭")
                if match is None:
                    raise RuntimeError(f"{task_label}：妖王/妖族选择页未找到「推荐剿灭」按钮，不能继续")
                x, y, matched_text = match
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：选择推荐剿灭目标",
                        phase="daily_free_challenge_select_recommended",
                        current_scene=scene_id,
                    )
                    self._log_locked("action", f"{task_label}：点击 OCR「{matched_text}」")
                self._click_frame_point(ctx, image69, x, y)
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
                continue
            if self._daily_free_challenge_text_is_detail(text):
                if self._daily_free_challenge_remaining_zero(text):
                    self._record_daily_free_challenge_done(
                        payload,
                        task_id=task_id,
                        task_type=task_type,
                        task_label=task_label,
                        message="详情页显示剩余奖励次数已为 0",
                    )
                    yield from self._return_daily_free_challenge_to_world(ctx, stop_event, task_label=task_label)
                    return "success"
                if run_count >= max_runs:
                    raise RuntimeError(f"{task_label}：剿灭次数超过上限 {max_runs}，停止以避免误点")
                match = self._ocr_line_center_matching(lines, r"前往剿灭")
                if match is None:
                    raise RuntimeError(f"{task_label}：详情页未找到「前往剿灭」按钮，不能继续")
                run_count += 1
                x, y, matched_text = match
                remaining = self._daily_free_challenge_remaining_count(text)
                with self._lock:
                    remaining_text = f"剩余 {remaining}" if remaining is not None else "剩余次数未读清"
                    self._set_status_locked(
                        "running",
                        f"{task_label}：执行免费剿灭 {run_count}/{max_runs}（{remaining_text}）",
                        phase="daily_free_challenge_exterminate",
                        current_scene=scene_id,
                    )
                    self._log_locked("action", f"{task_label}：点击 OCR「{matched_text}」")
                self._click_frame_point(ctx, image69, x, y)
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=4.0)
                continue
            if "妖兽波数" in _sanitize_ocr_text(text) or ("副本" in text and "用时" in text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"{task_label}：等待自动剿灭完成",
                        phase="daily_free_challenge_wait_combat",
                        current_scene=scene_id,
                    )
                self._clear_tick_frame(ctx)
                yield BehaviorTreeStatus.RUNNING
                continue
            if scene_id == 189 or "点击退出" in text:
                if not isinstance(image189, dict):
                    raise RuntimeError(f"{task_label}：缺少 #189「挑战结算」标注，无法关闭结算")
                exit_shape = self._find_shape(image189, "点击退出")
                if exit_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #189「点击退出」标注，无法关闭结算")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭剿灭结算", phase="daily_free_challenge_exit_result", current_scene=189)
                    self._log_locked("action", f"{task_label}：点击 #189「点击退出」")
                self._click_shape(ctx, image189, exit_shape, frame_data_url=frame)
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
                continue
            if scene_id == 188:
                raise RuntimeError(f"{task_label}：检测到旧 #188 快速挑战页，但妖王/妖族只允许免费「前往剿灭」流程，已停止避免误点")
            if scene_id == 34 and run_count > 0:
                return "reenter"
            if scene_id == 69 and run_count > 0:
                return "reenter"
            if time.monotonic() - start >= float(payload.get("free_challenge_timeout") or 120.0):
                raise RuntimeError(f"{task_label}：等待免费剿灭流程超时，最后 #{scene_id or 'unknown'} {score:.0f}% OCR={text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{task_label}：等待免费剿灭状态，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_free_challenge_wait",
                    current_scene=scene_id,
                )
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING

    def _execute_daily_free_challenge_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
        *,
        task_id: str,
        task_type: str,
        task_label: str,
        title_pattern: str,
        exclude_pattern: str | None = None,
    ) -> str:
        payload = {"max_scrolls": 30, "reverse_scrolls": 30, **dict(payload or {})}
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError(f"缺少{task_label}资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        required_images = {34: "世界", 69: "日常"}
        for scene_id, label in required_images.items():
            if not isinstance(images.get(scene_id), dict):
                raise RuntimeError(f"缺少 #{scene_id}「{label}」标注，无法执行{task_label}")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [188, 189, 69, 34])
        text = self._ocr_text(self._ocr_lines(frame))
        if self._daily_free_challenge_text_is_purchase_modal(text):
            raise RuntimeError(f"{task_label}：出现「购买并使用」弹窗，默认不购买次数或道具，已停止等待人工关闭")
        if (
            self._daily_free_challenge_text_is_selection(text)
            or self._daily_free_challenge_text_is_detail(text)
            or "妖兽波数" in _sanitize_ocr_text(text)
            or ("副本" in text and "用时" in text)
        ):
            result = yield from self._run_daily_free_challenge_from_scene(
                ctx,
                stop_event,
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
            )
            if result == "reenter":
                attempt = int(payload.get("_free_challenge_attempt") or 0)
                if attempt >= int(payload.get("max_free_challenges") or 5):
                    raise RuntimeError(f"{task_label}：免费剿灭重入次数超过上限，停止")
                payload["_free_challenge_attempt"] = attempt + 1
                return (yield from self._execute_daily_free_challenge_task(
                    ctx,
                    stop_event,
                    payload,
                    task_id=task_id,
                    task_type=task_type,
                    task_label=task_label,
                    title_pattern=title_pattern,
                    exclude_pattern=exclude_pattern,
                ))
            return result
        if scene_id in {188, 189}:
            result = yield from self._run_daily_free_challenge_from_scene(
                ctx,
                stop_event,
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
            )
            if result == "reenter":
                attempt = int(payload.get("_free_challenge_attempt") or 0)
                if attempt >= int(payload.get("max_free_challenges") or 5):
                    raise RuntimeError(f"{task_label}：免费剿灭重入次数超过上限，停止")
                payload["_free_challenge_attempt"] = attempt + 1
                return (yield from self._execute_daily_free_challenge_task(
                    ctx,
                    stop_event,
                    payload,
                    task_id=task_id,
                    task_type=task_type,
                    task_label=task_label,
                    title_pattern=title_pattern,
                    exclude_pattern=exclude_pattern,
                ))
            return result
        if scene_id != 69:
            if scene_id != 34 and not self._daily_lingta_text_is_world_like(text):
                raise RuntimeError(f"{task_label}：当前不在可识别的世界、日常页或免费剿灭页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label=task_label)):
                frame = self._screencap(ctx)
                scene_id, _score = self._identify_scene_number(ctx, frame, [188, 189, 69, 34])
            if scene_id not in {69, 188, 189}:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label=task_label,
                )
        if scene_id in {188, 189}:
            result = yield from self._run_daily_free_challenge_from_scene(
                ctx,
                stop_event,
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
            )
            if result == "reenter":
                attempt = int(payload.get("_free_challenge_attempt") or 0)
                if attempt >= int(payload.get("max_free_challenges") or 5):
                    raise RuntimeError(f"{task_label}：免费剿灭重入次数超过上限，停止")
                payload["_free_challenge_attempt"] = attempt + 1
                return (yield from self._execute_daily_free_challenge_task(
                    ctx,
                    stop_event,
                    payload,
                    task_id=task_id,
                    task_type=task_type,
                    task_label=task_label,
                    title_pattern=title_pattern,
                    exclude_pattern=exclude_pattern,
                ))
            return result

        daily_status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            payload,
            task_label=task_label,
            title_pattern=title_pattern,
            exclude_pattern=exclude_pattern,
            progress_can_mark_done=True,
        )
        if daily_status == "done":
            self._record_daily_free_challenge_done(
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
                message="日常列表显示已完成",
            )
            yield from self._return_daily_free_challenge_to_world(ctx, stop_event, task_label=task_label)
            return "success"
        if daily_status == "not_found":
            raise RuntimeError(f"{task_label}：#69 日常列表未找到入口，不能按完成处理")

        result = yield from self._run_daily_free_challenge_from_scene(
            ctx,
            stop_event,
            payload,
            task_id=task_id,
            task_type=task_type,
            task_label=task_label,
        )
        if result == "reenter":
            attempt = int(payload.get("_free_challenge_attempt") or 0)
            if attempt >= int(payload.get("max_free_challenges") or 5):
                raise RuntimeError(f"{task_label}：免费剿灭重入次数超过上限，停止")
            payload["_free_challenge_attempt"] = attempt + 1
            return (yield from self._execute_daily_free_challenge_task(
                ctx,
                stop_event,
                payload,
                task_id=task_id,
                task_type=task_type,
                task_label=task_label,
                title_pattern=title_pattern,
                exclude_pattern=exclude_pattern,
            ))
        return result

    def _execute_daily_yaowang_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return (yield from self._execute_daily_free_challenge_task(
            ctx,
            stop_event,
            payload,
            task_id="legacy-daily-yaowang",
            task_type="daily_yaowang",
            task_label="日常_妖王来袭",
            title_pattern=r"妖王\s*来袭|妖王",
        ))

    def _execute_daily_yaozu_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return (yield from self._execute_daily_free_challenge_task(
            ctx,
            stop_event,
            payload,
            task_id="legacy-daily-yaozu",
            task_type="daily_yaozu",
            task_label="日常_妖族袭城",
            title_pattern=r"妖族\s*袭城|妖族",
        ))

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
        matches: list[tuple[float, float, str]] = []
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
                if compact.startswith("活动报名") and "奖励找回" in compact:
                    click_x = line_x + line_w * 0.34
                else:
                    text_len = max(1, len(compact))
                    click_x = line_x + line_w * ((tab_match.start() + tab_match.end()) / 2) / text_len
                click_y = cy
                if 0 <= click_x <= image_width and 0 <= click_y <= image_height:
                    matches.append((click_x, click_y, text))
                    continue
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _open_daily_assistant_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找小助手")
        max_scrolls = int(payload.get("assistant_max_scrolls") or payload.get("max_scrolls") or 8)
        reverse_scrolls = int(payload.get("assistant_reverse_scrolls") or payload.get("reverse_scrolls") or 8)
        for direction, scroll_count in [("down", max_scrolls), ("up", reverse_scrolls)]:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：查找小助手入口 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_assistant_find_entry",
                        current_scene=69,
                    )
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
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
                    self._click_frame_point(ctx, image69, x, y)
                    yield from self._wait_runtime_action_settle(
                        ctx,
                        stop_event,
                        seconds=float(payload.get("assistant_entry_click_settle_seconds") or 2.0),
                    )
                    return "open"
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_助手：未找到小助手入口，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
        return "not_found"

    def _wait_daily_assistant_after_entry(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("post_click_timeout") or payload.get("assistant_post_click_timeout") or 20.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [204, 69, 34])
            last_scene_id, last_score = scene_id, score
            if scene_id == 204:
                return 204, float(score)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if self._daily_assistant_text_is_list(text):
                return 204, 100.0
            if scene_id in {69, 34}:
                return int(scene_id), float(score)
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
                x, y = ActionPlanner().shape_center(image69, exit_shape)
                with self._lock:
                    self._set_status_locked("running", "日常_助手：缺少小助手清单新帧标注，先退出小助手页", phase="daily_assistant_missing_assets_return", current_scene=69)
                    self._log_locked("action", "日常_助手：缺少小助手清单新帧标注，点击 #69「退出」恢复到日常页")
                self._click_frame_point(ctx, image69, x, y)
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
            raise RuntimeError(
                "日常_助手：已进入小助手清单，但资产树尚未新增小助手清单帧标注；"
                "当前资产树最后编号是 #203，建议把小助手清单作为下一帧 #204；"
                "需要补小助手清单身份、清单区域、任务块标题、执行按钮、退出、完成页「点击屏幕继续」、"
                "同游结果「确定/查看下一个/空白关闭」等标注后才能继续复刻完整流程"
            )

        assistant_items = payload.get("assistant_items") or payload.get("assistant_execute_shapes") or [
            "执行-道义秘库助手",
            "执行-神物园助手",
            "执行-宗门助手",
        ]
        if isinstance(assistant_items, str):
            assistant_items = [item.strip() for item in re.split(r"[,，\s]+", assistant_items) if item.strip()]
        if not isinstance(assistant_items, list):
            raise RuntimeError("日常_助手：assistant_items 参数格式错误")

        results: list[tuple[str, str]] = []
        for shape_title in assistant_items:
            result = yield from self._run_daily_assistant_item_from_list(
                ctx,
                stop_event,
                payload,
                image204,
                str(shape_title),
            )
            results.append((str(shape_title), str(result)))

        if bool(payload.get("assistant_return_after_items")):
            back_shape = self._find_shape(image204, "返回")
            if back_shape is not None:
                x, y = ActionPlanner().shape_center(image204, back_shape)
                with self._lock:
                    self._set_status_locked("running", "日常_助手：助手闭环后返回日常页", phase="daily_assistant_return_daily", current_scene=204)
                    self._log_locked("action", "日常_助手：点击 #204「返回」")
                self._click_frame_point(ctx, image204, x, y)
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=2.0)
        summary = "，".join(f"{title}={result}" for title, result in results)
        self._log("success", f"日常_助手：小助手可见执行项闭环完成，{summary}")
        return "success"

    def _daily_assistant_item_parts(self, item_title: str) -> tuple[str, str] | None:
        text = str(item_title or "").strip()
        if not text:
            return None
        if "/" in text:
            parent, action = [part.strip() for part in text.split("/", 1)]
            if parent and action:
                return parent, action
        for prefix in ("执行-", "领取-"):
            if text.startswith(prefix) and len(text) > len(prefix):
                return text[len(prefix):].strip(), prefix[:-1]
        return None

    def _daily_assistant_child_shape(
        self,
        parent_shape: dict[str, Any] | None,
        *titles: str,
    ) -> dict[str, Any] | None:
        if not isinstance(parent_shape, dict):
            return None
        for child in self._flatten_shapes(parent_shape.get("children")):
            title = str(child.get("title") or "").strip()
            if title in titles:
                return child
        return None

    def _daily_assistant_scroll_box(self, image204: dict[str, Any]) -> dict[str, float]:
        scroll_shape = self._find_shape(image204, "滚动窗口")
        if scroll_shape is None:
            raise RuntimeError("日常_助手：#204 缺少「滚动窗口」标注，无法定位浮动助手条目")
        box = self._box(scroll_shape, image204)
        return {
            "left": float(box.get("x") or 0),
            "top": float(box.get("y") or 0),
            "right": float(box.get("x") or 0) + float(box.get("w") or 0),
            "bottom": float(box.get("y") or 0) + float(box.get("h") or 0),
        }

    def _daily_assistant_title_center_in_scroll(
        self,
        lines: list[dict[str, Any]],
        image204: dict[str, Any],
        title: str,
    ) -> tuple[float, float, str] | None:
        title = _sanitize_ocr_text(title)
        if not title:
            return None
        box = self._daily_assistant_scroll_box(image204)
        best: tuple[float, float, str] | None = None
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            compact_text = re.sub(r"\s+", "", text)
            compact_title = re.sub(r"\s+", "", title)
            if compact_title not in compact_text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            index = compact_text.find(compact_title)
            cx = x + w * ((index + len(compact_title) / 2) / max(1, len(compact_text)))
            cy = y + h / 2
            if box["left"] <= cx <= box["right"] and box["top"] <= cy <= box["bottom"]:
                best = (cx, cy, text)
                break
        return best

    def _daily_assistant_floating_action_point(
        self,
        image204: dict[str, Any],
        lines: list[dict[str, Any]],
        parent_title: str,
        action_title: str,
    ) -> tuple[float, float, str] | None:
        parent_shape = self._find_shape(image204, parent_title)
        if parent_shape is None:
            return None
        action_shape = self._daily_assistant_child_shape(parent_shape, action_title)
        title_shape = self._daily_assistant_child_shape(parent_shape, "标题")
        if action_shape is None:
            return None
        title_hit = self._daily_assistant_title_center_in_scroll(lines, image204, parent_title)
        if title_hit is None:
            return None
        actual_title_x, actual_title_y, matched_text = title_hit
        action_x, action_y = ActionPlanner().shape_center(image204, action_shape)
        if title_shape is not None:
            title_x, title_y = ActionPlanner().shape_center(image204, title_shape)
        else:
            parent_box = self._box(parent_shape, image204)
            title_x = float(parent_box.get("x") or 0) + float(parent_box.get("w") or 0) * 0.62
            title_y = float(parent_box.get("y") or 0) + float(parent_box.get("h") or 0) * 0.28
        click_x = actual_title_x + (action_x - title_x)
        click_y = actual_title_y + (action_y - title_y)
        width, height = self._frame_size(image204)
        click_x = max(0.0, min(float(width), click_x))
        click_y = max(0.0, min(float(height), click_y))
        return click_x, click_y, matched_text

    def _run_daily_assistant_daoyi_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
    ):
        return (yield from self._run_daily_assistant_item_from_list(
            ctx,
            stop_event,
            payload,
            image204,
            "执行-道义秘库助手",
        ))

    def _run_daily_assistant_item_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image204: dict[str, Any],
        shape_title: str,
    ):
        frame = self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame, [204, 205, 208, 69, 34])
        lines = self._ocr_lines(frame)
        text = self._ocr_text(lines)
        if scene_id != 204 and not self._daily_assistant_text_is_list(text):
            raise RuntimeError(
                f"日常_助手：准备点击「{shape_title}」前已不在小助手清单，"
                f"当前 #{scene_id or 'unknown'} {score:.0f}%，OCR={text[:120]}"
            )

        parts = self._daily_assistant_item_parts(shape_title)
        assistant_label = parts[0] if parts else str(shape_title).removeprefix("执行-")
        action_label = parts[1] if parts else ""
        floating_point = None
        has_floating_template = False
        if parts is not None:
            parent_shape = self._find_shape(image204, parts[0])
            has_floating_template = (
                parent_shape is not None
                and self._daily_assistant_child_shape(parent_shape, parts[1]) is not None
            )
        if parts is not None and has_floating_template:
            floating_point = self._daily_assistant_floating_action_point(image204, lines, parts[0], parts[1])
            max_scrolls = int(payload.get("assistant_item_max_scrolls") or payload.get("assistant_max_scrolls") or 6)
            scroll_index = 0
            while floating_point is None and scroll_index < max_scrolls:
                with self._lock:
                    self._log_locked("action", f"日常_助手：当前屏未找到「{parts[0]}」，向下滚动查找 {scroll_index + 1}/{max_scrolls}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image204, direction="down")
                if not changed:
                    break
                self._clear_tick_frame(ctx)
                yield BehaviorTreeStatus.RUNNING
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
                text = self._ocr_text(lines)
                if not self._daily_assistant_text_is_list(text):
                    raise RuntimeError(f"日常_助手：滚动查找「{parts[0]}」后已不在小助手清单，OCR={text[:120]}")
                floating_point = self._daily_assistant_floating_action_point(image204, lines, parts[0], parts[1])
                scroll_index += 1
            if floating_point is None:
                with self._lock:
                    self._log_locked("action", f"日常_助手：未找到可见的「{parts[0]}」条目，跳过「{shape_title}」")
                return "not_visible"

        shape = None if floating_point is not None else self._find_shape(image204, shape_title)
        if shape is None and floating_point is None:
            raise RuntimeError(f"日常_助手：#204 缺少「{shape_title}」标注，无法执行助手闭环")
        if floating_point is None and assistant_label and assistant_label != "道义秘库助手":
            compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
            if assistant_label not in compact:
                with self._lock:
                    self._log_locked("action", f"日常_助手：当前 OCR 未确认「{assistant_label}」，跳过「{shape_title}」")
                return "not_visible"

        if floating_point is not None:
            x, y, matched_text = floating_point
        else:
            x, y = ActionPlanner().shape_center(image204, shape)
            matched_text = assistant_label
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_助手：点击「{shape_title}」",
                phase="daily_assistant_item_click",
                current_scene=204,
            )
            if floating_point is not None:
                self._log_locked("action", f"日常_助手：OCR 命中「{matched_text}」，点击 #204「{assistant_label}/{action_label}」")
            else:
                self._log_locked("action", f"日常_助手：点击 #204「{shape_title}」")
        self._click_frame_point(ctx, image204, x, y)
        return (yield from self._wait_daily_assistant_item_result(ctx, stop_event, payload, assistant_label or shape_title, action_label or "执行"))

    def _wait_daily_assistant_daoyi_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        return (yield from self._wait_daily_assistant_item_result(ctx, stop_event, payload, "道义秘库助手", "执行"))

    def _daily_assistant_should_capture_transient_feedback(self, assistant_label: str, action_label: str, payload: dict[str, Any]) -> bool:
        raw = payload.get("assistant_capture_transient_feedback")
        if raw is not None:
            return str(raw).strip().lower() not in {"0", "false", "no", "off"}
        label = str(assistant_label or "")
        action = str(action_label or "")
        return ("仙府资源" in label and action == "领取") or ("弟子授业" in label and action == "执行")

    def _save_daily_assistant_transient_feedback_frame(
        self,
        frame_data_url: str,
        *,
        assistant_label: str,
        action_label: str,
    ) -> Path | None:
        if not isinstance(frame_data_url, str) or not frame_data_url.startswith("data:image"):
            return None
        try:
            png_data = self._decode_frame_data_url(frame_data_url)
            output_dir = codeyun_temp_root("fanxiu_daily_assistant_feedback")
            safe_label = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", f"{assistant_label}_{action_label}").strip("_") or "assistant_feedback"
            path = output_dir / f"{_now().strftime('%Y%m%d_%H%M%S_%f')}_{safe_label}.png"
            path.write_bytes(png_data)
            return path
        except Exception as exc:
            self._log("detail", f"日常_助手：短暂反馈截图保存失败：{exc}")
            return None

    def _wait_daily_assistant_item_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        assistant_label: str,
        action_label: str = "执行",
    ):
        timeout = float(payload.get("assistant_item_wait_seconds") or payload.get("assistant_daoyi_wait_seconds") or payload.get("assistant_execute_wait_seconds") or 10.0)
        detail_timeout = float(payload.get("assistant_item_detail_return_timeout") or payload.get("assistant_daoyi_detail_return_timeout") or 10.0)
        poll_seconds = float(payload.get("assistant_item_poll_seconds") or payload.get("assistant_daoyi_poll_seconds") or 0.35)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        saw_list = False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image205 = images.get(205)
        image209 = images.get(209)
        capture_feedback = self._daily_assistant_should_capture_transient_feedback(assistant_label, action_label, payload)
        feedback_capture_seconds = float(payload.get("assistant_transient_feedback_capture_seconds") or 3.5)
        feedback_saved = False

        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            if capture_feedback and not feedback_saved and elapsed <= feedback_capture_seconds:
                feedback_saved = True
                feedback_path = self._save_daily_assistant_transient_feedback_frame(
                    frame,
                    assistant_label=assistant_label,
                    action_label=action_label or "执行",
                )
                if feedback_path is not None:
                    with self._lock:
                        self._log_locked("detail", f"日常_助手：已保存 {assistant_label} {action_label or '执行'} 后短暂反馈候选帧：{feedback_path}")
            scene_id, score = self._identify_scene_number(ctx, frame, [208, 209, 205, 204, 69, 34])
            last_scene_id, last_score = scene_id, score
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))

            no_action_text = ""
            if scene_id == 208 or "当前没有可执行的事项" in compact:
                no_action_text = "当前没有可执行的事项"
            elif "当前没有可授业的弟子" in compact:
                no_action_text = "当前没有可授业的弟子"
            if no_action_text:
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：{assistant_label} {no_action_text}",
                        phase="daily_assistant_item_no_action",
                        current_scene=204,
                    )
                    self._log_locked("action", f"日常_助手：识别到「{no_action_text}」")
                yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=float(payload.get("assistant_no_action_settle_seconds") or 1.0))
                return "no_action"

            if scene_id in {205, 209}:
                result_image = image209 if scene_id == 209 else image205
                if not isinstance(result_image, dict):
                    raise RuntimeError(f"日常_助手：已进入 #{scene_id} 小助手执行结果，但缺少 #{scene_id} 资产标注")
                continue_shape = self._find_shape(result_image, "点击屏幕继续")
                if continue_shape is None:
                    raise RuntimeError(f"日常_助手：#{scene_id} 缺少「点击屏幕继续」标注，无法回到 #204")
                x, y = ActionPlanner().shape_center(result_image, continue_shape)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_助手：关闭 {assistant_label} 执行结果",
                        phase="daily_assistant_item_close_detail",
                        current_scene=scene_id,
                    )
                    self._log_locked("action", f"日常_助手：点击 #{scene_id}「点击屏幕继续」")
                self._click_frame_point(ctx, result_image, x, y)
                yield from self._wait_scene_id(ctx, stop_event, 204, timeout=detail_timeout, label="日常_助手：等待回到小助手清单")
                return "detail_closed"

            if scene_id == 204 or self._daily_assistant_text_is_list(text):
                saw_list = True
            elif scene_id in {69, 34}:
                raise RuntimeError(
                    f"日常_助手：点击 {assistant_label} 执行后离开小助手清单，"
                    f"当前 #{scene_id} {score:.0f}%，OCR={text[:120]}"
                )

            if elapsed >= timeout:
                if saw_list:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_助手：{assistant_label} {action_label or '执行'} 后 10 秒内未出现结果弹窗，按未触发收口",
                            phase="daily_assistant_item_no_popup",
                            current_scene=204,
                        )
                        self._log_locked("action", f"日常_助手：{assistant_label} {action_label or '执行'} 等待超时，仍在 #204，按本轮未触发处理")
                    return "no_popup"
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_助手：{assistant_label} 执行等待超时，未确认 #205/#208/#204，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:160]}"
                )

            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_助手：等待 {assistant_label} 执行结果 {elapsed:.1f}/{timeout:.1f}s，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_assistant_item_wait_result",
                    current_scene=scene_id,
                )
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=poll_seconds)

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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image34 = images.get(34)
        image69 = images.get(69)
        if not isinstance(image34, dict) or not isinstance(image69, dict):
            raise RuntimeError("缺少 #34「世界」或 #69「日常」标注，无法执行日常_助手")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [204, 69, 34])
        text = self._ocr_text(self._ocr_lines(frame))
        if scene_id == 204 or self._daily_assistant_text_is_list(text):
            return (yield from self._run_daily_assistant_from_list(ctx, stop_event, payload))
        if scene_id != 69:
            if scene_id != 34 and not self._daily_assistant_text_is_world_like(text):
                raise RuntimeError("日常_助手：当前不在可识别的世界、日常页或小助手页，无法开始")
            if (yield from self._leave_world_side_scene_if_present(ctx, stop_event, frame, text, label="日常_助手")):
                frame = self._screencap(ctx)
                scene_id, _score = self._identify_scene_number(ctx, frame, [69, 34])
            if scene_id != 69:
                scene_id = yield from self._enter_daily_from_world_like(
                    ctx,
                    runtime,
                    stop_event,
                    frame,
                    scene_id,
                    text,
                    label="日常_助手",
                )

        daily_status = yield from self._open_daily_assistant_from_daily(ctx, stop_event, payload)
        if daily_status == "not_found":
            self._record_scheduler_task_discovered_next_time(
                str(payload.get("__scheduler_task_id") or "legacy-daily-assistant"),
                (_now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
                task_type="daily_assistant",
                label="日常_助手",
            )
            raise RuntimeError("日常_助手：未找到小助手入口，已记录 30 分钟后重试")
        scene_id, _score = yield from self._wait_daily_assistant_after_entry(ctx, stop_event, payload)
        if scene_id == 204:
            return (yield from self._run_daily_assistant_from_list(ctx, stop_event, payload))
        raise RuntimeError(f"日常_助手：入口点击后回到 #{scene_id or 'unknown'}，尚未进入小助手清单，不能按完成处理")

    def _open_daily_xianyuan_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找挑战仙缘")
        if self._find_shape(image69, "滚动窗口") is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找挑战仙缘")
        max_scrolls = int(payload.get("max_scrolls") or payload.get("xianyuan_max_scrolls") or 14)
        reverse_scrolls = int(payload.get("reverse_scrolls") or payload.get("xianyuan_reverse_scrolls") or 18)
        passes: list[tuple[str, int]] = [("down", max_scrolls), ("up", reverse_scrolls)]
        fallback_seen = 0
        for direction, scroll_count in passes:
            for scroll_index in range(scroll_count + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_挑战仙缘：查找日常任务「挑战仙缘」 {direction} {scroll_index}/{scroll_count}",
                        phase="daily_xianyuan_find_daily_entry",
                        current_scene=69,
                    )
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
                text = self._ocr_text(lines)
                matches = self._daily_xianyuan_entry_matches(lines, image69)
                if matches:
                    x, y, matched_text = matches[0]
                    progress = self._daily_xianyuan_row_progress(lines, y)
                    if progress is not None and progress[0] >= progress[1]:
                        return "done"
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_挑战仙缘：点击日常任务 {matched_text}",
                            phase="daily_xianyuan_click_daily_entry",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_挑战仙缘：点击 #69「{matched_text}」")
                    self._click_frame_point(ctx, image69, x, y)
                    yield from self._wait_runtime_action_settle(
                        ctx,
                        stop_event,
                        seconds=float(payload.get("xianyuan_entry_click_settle_seconds") or 2.0),
                    )
                    return "open"
                if self._daily_xianyuan_progress_done(text):
                    return "done"
                if re.search(r"(?:挑战\s*仙缘|仙缘人物)", text) and not re.search(r"仙缘斗法|斗法", text):
                    fallback_seen += 1
                if scroll_index >= scroll_count:
                    break
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：未找到「挑战仙缘」，{direction} 滚动日常列表 {scroll_index + 1}")
                changed = yield from self._scroll_daily_xianyuan_list(ctx, stop_event, image69, direction=direction)
                if not changed:
                    break
        if fallback_seen >= int(payload.get("completed_fallback_min_total") or 3):
            raise RuntimeError("日常_挑战仙缘：看到标题但未解析到未完成进度，不能按完成处理")
        return "not_found"

    def _daily_xianyuan_text_is_people_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool("仙缘" in compact and ("可送礼" in compact or "隐藏已无物品的仙缘" in compact))

    def _wait_daily_xianyuan_after_entry(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("post_click_timeout") or 30.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [199, 198, 197, 69, 34])
            last_scene_id, last_score = scene_id, score
            if scene_id in {199, 198, 197, 69, 34}:
                return int(scene_id), float(score)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if self._daily_xianyuan_text_is_people_list(text):
                return 197, 100.0
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：等待入口点击结果，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_xianyuan_wait_after_entry",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(
                    f"日常_挑战仙缘：等待入口点击结果超时，未检测到 #69/#34 或仙缘列表，"
                    f"最后 {scene_text} {last_score:.0f}%，OCR={last_text[:120]}"
                )

    def _scroll_daily_xianyuan_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image69: dict[str, Any],
        *,
        direction: str,
    ):
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找挑战仙缘")
        before_frame = self._screencap(ctx)
        before_signature = self._daily_scroll_window_signature(ctx, image69, before_frame)
        box = self._box(list_shape, image69)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        x = left + width * 0.5
        if direction == "up":
            start_y = top + height * 0.25
            end_y = top + height * 0.75
        else:
            start_y = top + height * 0.75
            end_y = top + height * 0.25
        self._drag_frame_point(ctx, image69, x, start_y, x, end_y, duration_ms=320)
        yield from self._wait_scroll_settle(ctx, stop_event)
        after_frame = self._screencap(ctx)
        after_signature = self._daily_scroll_window_signature(ctx, image69, after_frame)
        if before_signature and before_signature == after_signature:
            boundary = "顶部" if direction == "up" else "底部"
            with self._lock:
                self._log_locked("action", f"#69「滚动窗口」{direction} 拖拽后签名未变化，判定已到{boundary}")
            return False
        return True

    def _daily_scroll_window_signature(
        self,
        ctx: dict[str, Any] | None,
        image69: dict[str, Any],
        frame_data_url: str,
    ) -> str:
        lines = self._ocr_lines(frame_data_url)
        return self._vertical_text_signature_in_shape(
            lines,
            image69,
            "滚动窗口",
            exclude_boxes=self._occlusion_marker_boxes(ctx, image69),
        )

    def _daily_xianyuan_people_list_box(self, image197: dict[str, Any]) -> dict[str, Any]:
        list_shape = self._find_shape(image197, "人物列表")
        if list_shape is not None:
            return self._box(list_shape, image197)
        width, height = self._frame_size(image197)
        return {"name": "人物列表", "x": width * 0.07, "y": height * 0.19, "w": width * 0.88, "h": height * 0.66}

    def _daily_xianyuan_target_pattern(self, payload: dict[str, Any]) -> str:
        raw = (
            payload.get("target_pattern")
            or payload.get("xianyuan_target_pattern")
            or payload.get("target")
            or payload.get("指定目标")
            or payload.get("xianyuan_target")
            or ""
        )
        target = _sanitize_ocr_text(str(raw or "")).strip()
        if target:
            return target
        return r"两立"

    def _daily_xianyuan_list_target_candidates(
        self,
        lines: list[dict[str, Any]],
        image197: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[tuple[float, float, str]]:
        box = self._daily_xianyuan_people_list_box(image197)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        pattern = self._daily_xianyuan_target_pattern(payload)
        candidates: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            line_x = float(line.get("x") or 0)
            line_y = float(line.get("y") or 0)
            line_w = float(line.get("w") or 0)
            line_h = float(line.get("h") or 0)
            cx = line_x + line_w / 2
            cy = line_y + line_h / 2
            if cx < left or cx > right or cy < top or cy > bottom:
                continue
            try:
                matches = list(re.finditer(pattern, text))
            except re.error:
                matches = []
                index = text.find(pattern)
                if index >= 0:
                    matches = [re.match(re.escape(pattern), text[index:]) or re.match(r".*", text[index:index + len(pattern)])]
            for match in matches:
                if match is None:
                    continue
                span_start, span_end = match.span()
                if span_end <= span_start:
                    continue
                text_len = max(1, len(text))
                click_x = line_x + line_w * ((span_start + span_end) / 2) / text_len
                click_y = max(top, line_y + line_h / 2 - 120)
                candidates.append((click_x, click_y, text))
        return sorted(candidates, key=lambda item: (item[1], item[0]))

    def _scroll_daily_xianyuan_people_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image197: dict[str, Any],
        *,
        direction: str = "down",
    ):
        box = self._daily_xianyuan_people_list_box(image197)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        x = left + width * 0.5
        if direction == "up":
            start_y = top + height * 0.25
            end_y = top + height * 0.75
        else:
            start_y = top + height * 0.75
            end_y = top + height * 0.25
        self._drag_frame_point(ctx, image197, x, start_y, x, end_y, duration_ms=380)
        yield from self._wait_scroll_settle(ctx, stop_event)

    def _run_daily_xianyuan_from_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image197 = ctx.get("images", {}).get(197)
        if not isinstance(image197, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #197「仙缘列表」标注，无法选择仙缘人物")
        max_scrolls = int(payload.get("people_max_scrolls") or payload.get("xianyuan_people_max_scrolls") or 8)
        passes: list[tuple[str, int]] = [("down", max_scrolls), ("up", max_scrolls)]
        hide_empty_toggled = False
        for search_round in range(2):
            if search_round == 1:
                hide_shape = self._find_shape(image197, "隐藏已无物品的仙缘")
                if hide_shape is None:
                    break
                x, y = ActionPlanner().shape_center(image197, hide_shape)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_挑战仙缘：关闭隐藏已无物品后继续查找",
                        phase="daily_xianyuan_toggle_hide_empty",
                        current_scene=197,
                    )
                    self._log_locked("action", "日常_挑战仙缘：点击 #197「隐藏已无物品的仙缘」后重试目标搜索")
                self._click_frame_point(ctx, image197, x, y)
                hide_empty_toggled = True
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_toggle_settle_seconds") or 2.0),
                )

            for direction, scroll_count in passes:
                for scroll_index in range(scroll_count + 1):
                    self._raise_if_stopped(stop_event)
                    frame = self._screencap(ctx)
                    lines = self._ocr_lines(frame)
                    candidates = self._daily_xianyuan_list_target_candidates(lines, image197, payload)
                    if candidates:
                        x, y, matched_text = candidates[0]
                        with self._lock:
                            self._set_status_locked(
                                "running",
                                f"日常_挑战仙缘：选择仙缘人物 {matched_text[:24]}",
                                phase="daily_xianyuan_click_person",
                                current_scene=197,
                            )
                            self._log_locked("action", f"日常_挑战仙缘：点击 #197 仙缘人物候选「{matched_text[:40]}」")
                        self._click_frame_point(ctx, image197, x, y)
                        yield from self._wait_runtime_action_settle(
                            ctx,
                            stop_event,
                            seconds=float(payload.get("xianyuan_person_click_settle_seconds") or 2.0),
                        )
                        scene_id, score = yield from self._wait_daily_xianyuan_after_person_click(ctx, stop_event, payload)
                        if scene_id == 198:
                            return (yield from self._run_daily_xianyuan_from_detail(ctx, stop_event, payload))
                        raise RuntimeError(f"日常_挑战仙缘：已进入后续页面 #{scene_id or 'unknown'} {score:.0f}%，需要继续补详情/对话/挑战标注")
                    if scroll_index >= scroll_count:
                        break
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_挑战仙缘：查找仙缘人物 {direction} {scroll_index + 1}/{scroll_count}",
                            phase="daily_xianyuan_find_person",
                            current_scene=197,
                        )
                        suffix = "（已关闭隐藏无物品）" if hide_empty_toggled else ""
                        self._log_locked("action", f"日常_挑战仙缘：未找到目标{suffix}，{direction} 滚动仙缘人物列表 {scroll_index + 1}")
                    yield from self._scroll_daily_xianyuan_people_list(ctx, stop_event, image197, direction=direction)
        raise RuntimeError(f"日常_挑战仙缘：仙缘列表未找到目标「{self._daily_xianyuan_target_pattern(payload)}」")

    def _wait_daily_xianyuan_after_person_click(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("person_click_timeout") or 18.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [199, 198, 197, 69, 34])
            last_scene_id, last_score = scene_id, score
            if scene_id not in {197}:
                if scene_id is None:
                    text = self._ocr_text(self._ocr_lines(frame))
                    if self._daily_xianyuan_text_is_detail(text):
                        return 198, 100.0
                return scene_id, score
            if time.monotonic() - start >= timeout:
                return last_scene_id, last_score

    def _daily_xianyuan_text_is_detail(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            "前往" in compact
            and ("身份" in compact or "功法主修" in compact or "出没地点" in compact)
            and not re.search(r"可送礼|隐藏已无物品的仙缘|教他做人|看招吧", compact)
        )

    def _run_daily_xianyuan_from_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image198 = ctx.get("images", {}).get(198)
        if not isinstance(image198, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #198「仙缘人物详情」标注，无法前往人物")
        go_shape = self._find_shape(image198, "前往")
        if go_shape is None:
            raise RuntimeError("日常_挑战仙缘：缺少 #198「前往」标注，无法前往人物")
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：点击人物详情「前往」", phase="daily_xianyuan_go_person", current_scene=198)
            self._log_locked("action", "日常_挑战仙缘：点击 #198「前往」")
        x, y = ActionPlanner().shape_center(image198, go_shape)
        scene_id: int | None = None
        score = 0.0
        max_attempts = max(1, int(payload.get("detail_go_max_attempts") or 2))
        for attempt_index in range(max_attempts):
            self._click_frame_point(ctx, image198, x, y)
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=float(payload.get("xianyuan_detail_go_settle_seconds") or 2.0),
            )
            scene_id, score = yield from self._wait_daily_xianyuan_after_detail_go(ctx, stop_event, payload)
            if scene_id == 199:
                return (yield from self._run_daily_xianyuan_from_dialogue(ctx, stop_event, payload))
            if scene_id != 198:
                break
            if attempt_index + 1 < max_attempts:
                with self._lock:
                    self._log_locked("action", "日常_挑战仙缘：人物详情仍停留 #198，按旧版逻辑再次点击「前往」")
        raise RuntimeError(f"日常_挑战仙缘：已前往后续页面 #{scene_id or 'unknown'} {score:.0f}%，需要继续补人物对话/挑战标注")

    def _wait_daily_xianyuan_after_detail_go(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        timeout = float(payload.get("detail_go_timeout") or 35.0)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [199, 198, 197, 69, 34])
            last_scene_id, last_score = scene_id, score
            if scene_id not in {198}:
                if scene_id is None:
                    text = self._ocr_text(self._ocr_lines(frame))
                    if self._daily_xianyuan_text_is_dialogue(text):
                        return 199, 100.0
                return scene_id, score
            if time.monotonic() - start >= timeout:
                return last_scene_id, last_score

    def _daily_xianyuan_text_is_dialogue(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(
            ("教他做人" in compact or ("查探" in compact and "送礼" in compact))
            and not re.search(r"可送礼|隐藏已无物品的仙缘|出没地点", compact)
        )

    def _run_daily_xianyuan_from_dialogue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image199 = ctx.get("images", {}).get(199)
        if not isinstance(image199, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #199「仙缘人物对话」标注，无法发起挑战")
        frame = self._screencap(ctx)
        lines = self._ocr_lines(frame)
        text = self._ocr_text(lines)
        teach_shape = self._find_shape(image199, "教他做人")
        teach_matches = self._daily_xianyuan_dialogue_button_matches(lines, image199, r"教他做人")
        if not teach_matches:
            raise RuntimeError(f"日常_挑战仙缘：当前仙缘人物没有「教他做人」按钮，不能挑战；OCR={text[:120]}")
        match_x, match_y, matched_text = teach_matches[0]
        x, y = (ActionPlanner().shape_center(image199, teach_shape) if teach_shape is not None else (match_x, match_y))
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：点击「教他做人」", phase="daily_xianyuan_teach", current_scene=199)
            self._log_locked("action", f"日常_挑战仙缘：点击 #199「{matched_text}」")
        self._click_frame_point(ctx, image199, x, y)
        yield from self._wait_runtime_action_settle(
            ctx,
            stop_event,
            seconds=float(payload.get("xianyuan_click_settle_seconds") or 2.0),
        )
        return (yield from self._run_daily_xianyuan_after_teach(ctx, stop_event, payload))

    def _run_daily_xianyuan_from_challenge_state(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        scene_id: int,
    ):
        ref_image = self._daily_xianyuan_reference_image(ctx)
        if scene_id in {200, 201}:
            return (yield from self._run_daily_xianyuan_after_teach(ctx, stop_event, payload))
        if scene_id == 202:
            yield from self._wait_daily_xianyuan_challenge_result(ctx, stop_event, payload, ref_image)
            yield from self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image)
            self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
            return "success"
        if scene_id == 203:
            yield from self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image)
            self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
            return "success"
        raise RuntimeError(f"日常_挑战仙缘：无法从 #{scene_id} 恢复挑战流程")

    def _daily_xianyuan_reference_image(self, ctx: dict[str, Any]) -> dict[str, Any]:
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for scene_id in (203, 202, 201, 200, 199, 198, 197, 34, 69):
            image = images.get(scene_id)
            if isinstance(image, dict):
                return image
        return {"filename": "daily_xianyuan_runtime.png", "width": 900, "height": 1600}

    def _daily_xianyuan_challenge_count_empty(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        normalized = normalized.replace("O", "0").replace("o", "0")
        return bool(re.search(r"(?:今日)?可挑战次数[:：]?[0零]/[1-9]\d*", normalized))

    def _daily_xianyuan_text_button_matches(
        self,
        lines: list[dict[str, Any]],
        pattern: str,
        *,
        left_ratio: float = 0.0,
        right_ratio: float = 1.0,
        top_ratio: float = 0.0,
        bottom_ratio: float = 1.0,
        width: float = 900.0,
        height: float = 1600.0,
    ) -> list[tuple[float, float, str]]:
        left = width * left_ratio
        right = width * right_ratio
        top = height * top_ratio
        bottom = height * bottom_ratio
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not re.search(pattern, text):
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

    def _run_daily_xianyuan_after_teach(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        ref_image = self._daily_xianyuan_reference_image(ctx)
        width, height = self._frame_size(ref_image)
        challenge_scene_ids = [203, 202, 201, 200, 199, 198, 197, 69, 34]

        teach_deadline = time.monotonic() + float(payload.get("teach_disappear_timeout") or 15.0)
        while time.monotonic() < teach_deadline:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, challenge_scene_ids)
            if scene_id in {200, 201, 202, 203}:
                break
            lines = self._ocr_lines(frame)
            teach_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"教他做人",
                left_ratio=0.45,
                right_ratio=0.98,
                top_ratio=0.45,
                bottom_ratio=0.82,
                width=width,
                height=height,
            )
            if not teach_matches:
                break
            x, y, _text = teach_matches[0]
            image199 = (ctx.get("images") or {}).get(199)
            teach_shape = self._find_shape(image199, "教他做人") if isinstance(image199, dict) else None
            if scene_id == 199 and isinstance(image199, dict) and teach_shape is not None:
                x, y = ActionPlanner().shape_center(image199, teach_shape)
                ref_image = image199
            self._click_frame_point(ctx, ref_image, x, y)
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=float(payload.get("xianyuan_click_settle_seconds") or 2.0),
            )

        attack_deadline = time.monotonic() + float(payload.get("attack_dialogue_timeout") or 45.0)
        last_advance = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, challenge_scene_ids)
            lines = self._ocr_lines(frame)
            text = self._ocr_text(lines)
            if scene_id == 201:
                break
            if self._daily_xianyuan_challenge_count_empty(text):
                yield from self._return_daily_xianyuan_current_to_world(ctx, stop_event)
                self._record_daily_xianyuan_done(payload, message="仙缘对话显示今日可挑战次数已空")
                return "success"
            attack_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"看招吧",
                left_ratio=0.35,
                right_ratio=0.98,
                top_ratio=0.35,
                bottom_ratio=0.86,
                width=width,
                height=height,
            )
            if attack_matches:
                x, y, matched_text = attack_matches[0]
                with self._lock:
                    self._set_status_locked("running", "日常_挑战仙缘：点击「看招吧」", phase="daily_xianyuan_attack", current_scene=199)
                    self._log_locked("action", f"日常_挑战仙缘：点击「{matched_text}」")
                self._click_frame_point(ctx, ref_image, x, y)
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_click_settle_seconds") or 2.0),
                )
                break
            now = time.monotonic()
            if now >= attack_deadline:
                raise TimeoutError(f"日常_挑战仙缘：等待「看招吧」超时，OCR={text[:120]}")
            if now - last_advance >= 3.0:
                self._click_frame_point(ctx, ref_image, width * 0.48, height * 0.76)
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_dialogue_advance_settle_seconds") or 2.0),
                )
                last_advance = now

        continue_deadline = time.monotonic() + float(payload.get("challenge_continue_timeout") or 5.0)
        while time.monotonic() <= continue_deadline:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, _score = self._identify_scene_number(ctx, frame, challenge_scene_ids)
            if scene_id == 202:
                break
            lines = self._ocr_lines(frame)
            matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"继续",
                left_ratio=0.25,
                right_ratio=0.85,
                top_ratio=0.45,
                bottom_ratio=0.88,
                width=width,
                height=height,
            )
            if matches:
                x, y, matched_text = matches[0]
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：点击挑战提示「{matched_text}」")
                self._click_frame_point(ctx, ref_image, x, y)
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_click_settle_seconds") or 2.0),
                )
                break

        yield from self._wait_daily_xianyuan_challenge_result(ctx, stop_event, payload, ref_image)
        yield from self._leave_daily_xianyuan_battle(ctx, stop_event, payload, ref_image)
        self._record_daily_xianyuan_done(payload, message="挑战流程已完成")
        return "success"

    def _wait_daily_xianyuan_challenge_result(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        ref_image: dict[str, Any],
    ):
        width, height = self._frame_size(ref_image)
        deadline = time.monotonic() + float(payload.get("challenge_result_timeout") or 300.0)
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            text = self._ocr_text(lines)
            last_text = text or last_text
            if re.search(r"友好度|减少", _sanitize_ocr_text(text)):
                with self._lock:
                    self._log_locked("success", "日常_挑战仙缘：识别到友好度减少结果")
                self._click_frame_point(ctx, ref_image, width * 0.50, height * 0.62)
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_click_settle_seconds") or 2.0),
                )
                return "success"
            if re.search(r"离\s*开|离开", _sanitize_ocr_text(text)):
                return "success"
            if time.monotonic() >= deadline:
                raise TimeoutError(f"日常_挑战仙缘：等待挑战结果超时，OCR={last_text[:120]}")

    def _leave_daily_xianyuan_battle(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        ref_image: dict[str, Any],
    ):
        width, height = self._frame_size(ref_image)
        deadline = time.monotonic() + float(payload.get("battle_leave_timeout") or 60.0)
        last_leave_click = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [34, 69, 197, 198, 199, 200, 201, 202, 203])
            if scene_id == 34:
                with self._lock:
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {score:.0f}%")
                return "success"
            lines = self._ocr_lines(frame)
            text = self._ocr_text(lines)
            last_text = text or last_text
            if self._daily_lingta_text_is_world_like(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", "日常_挑战仙缘：已回到世界")
                return "success"
            confirm_matches = self._daily_xianyuan_text_button_matches(
                lines,
                r"确认|确定",
                left_ratio=0.25,
                right_ratio=0.85,
                top_ratio=0.45,
                bottom_ratio=0.88,
                width=width,
                height=height,
            )
            if confirm_matches:
                x, y, matched_text = confirm_matches[-1]
                with self._lock:
                    self._log_locked("action", f"日常_挑战仙缘：点击离开确认「{matched_text}」")
                self._click_frame_point(ctx, ref_image, x, y)
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_click_settle_seconds") or 2.0),
                )
                continue
            now = time.monotonic()
            if now - last_leave_click >= 3.0:
                leave_matches = self._daily_xianyuan_text_button_matches(
                    lines,
                    r"离\s*开|离开",
                    left_ratio=0.72,
                    right_ratio=1.0,
                    top_ratio=0.35,
                    bottom_ratio=0.72,
                    width=width,
                    height=height,
                )
                if leave_matches:
                    x, y, matched_text = leave_matches[0]
                    with self._lock:
                        self._log_locked("action", f"日常_挑战仙缘：点击「{matched_text}」")
                    self._click_frame_point(ctx, ref_image, x, y)
                else:
                    self._click_frame_point(ctx, ref_image, width * 0.92, height * 0.08)
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=float(payload.get("xianyuan_leave_settle_seconds") or 2.0),
                )
                last_leave_click = now
            if now >= deadline:
                raise TimeoutError(f"日常_挑战仙缘：点击离开后等待确认框超时，OCR={last_text[:120]}")

    def _daily_xianyuan_dialogue_button_matches(
        self,
        lines: list[dict[str, Any]],
        image199: dict[str, Any],
        pattern: str,
    ) -> list[tuple[float, float, str]]:
        width, height = self._frame_size(image199)
        left = width * 0.45
        right = width * 0.97
        top = height * 0.45
        bottom = height * 0.78
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not re.search(pattern, text):
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

    def _return_daily_xianyuan_current_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        frame = self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame, [34, 69, 197, 198, 199, 200, 201, 202, 203])
        if scene_id == 34:
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        text = self._ocr_text(self._ocr_lines(frame))
        if self._daily_lingta_text_is_world_like(text):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
                self._log_locked("success", "日常_挑战仙缘：已回到世界")
            return "success"
        if scene_id == 69:
            return (yield from self._return_daily_xianyuan_to_world(ctx, stop_event))
        if scene_id in {197, 198}:
            image = ctx.get("images", {}).get(scene_id)
            if not isinstance(image, dict):
                raise RuntimeError(f"日常_挑战仙缘：缺少 #{scene_id} 标注，无法安全返回世界")
            back_shape = self._find_shape(image, "返回")
            if back_shape is None:
                raise RuntimeError(f"日常_挑战仙缘：缺少 #{scene_id}「返回」标注，无法安全返回世界")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：从 #{scene_id} 返回日常/世界",
                    phase="daily_xianyuan_return_current",
                    current_scene=scene_id,
                )
                self._log_locked("action", f"日常_挑战仙缘：点击 #{scene_id}「返回」")
            x, y = ActionPlanner().shape_center(image, back_shape)
            self._click_frame_point(ctx, image, x, y)
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=2.0,
            )
            frame = self._screencap(ctx)
            next_scene_id, next_score = self._identify_scene_number(ctx, frame, [34, 69])
            if next_scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {next_score:.0f}%")
                return "success"
            if next_scene_id == 69:
                return (yield from self._return_daily_xianyuan_to_world(ctx, stop_event))
            text = self._ocr_text(self._ocr_lines(frame))
            if self._daily_lingta_text_is_world_like(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", "日常_挑战仙缘：已回到世界")
                return "success"
            raise RuntimeError(f"日常_挑战仙缘：点击 #{scene_id}「返回」后未回到 #69/#34，当前 #{next_scene_id or 'unknown'} {next_score:.0f}%")
        raise RuntimeError(
            f"日常_挑战仙缘：当前 #{scene_id or 'unknown'} 显示次数已空，但缺少该页返回世界标注，不能按完成处理"
        )

    def _return_daily_xianyuan_to_world(self, ctx: dict[str, Any], stop_event: threading.Event):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("日常_挑战仙缘：缺少 #69「日常」标注，无法回世界")
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [69, 34])
        if scene_id == 34:
            return "success"
        if scene_id != 69 and self._daily_lingta_text_is_world_like(self._ocr_text(self._ocr_lines(frame))):
            with self._lock:
                self._status.update({"current_scene": 34, "updated_at": time.time()})
            return "success"
        if scene_id != 69:
            raise RuntimeError("日常_挑战仙缘：当前不在 #69 或 #34，缺少后续页面标注，无法安全返回")
        exit_shape = self._find_shape(image69, "退出")
        if exit_shape is None:
            raise RuntimeError("日常_挑战仙缘：缺少 #69「退出」标注，无法回世界")
        with self._lock:
            self._set_status_locked("running", "日常_挑战仙缘：从日常列表返回世界", phase="daily_xianyuan_return_daily", current_scene=69)
            self._log_locked("action", "日常_挑战仙缘：点击 #69「退出」")
        x, y = ActionPlanner().shape_center(image69, exit_shape)
        self._click_frame_point(ctx, image69, x, y)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [34])
            last_scene_id, last_score = scene_id, score
            if scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", f"日常_挑战仙缘：已回到世界 #34 {score:.0f}%")
                return "success"
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if self._daily_lingta_text_is_world_like(text):
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked("success", "日常_挑战仙缘：已回到世界")
                return "success"
            if time.monotonic() - start >= 18.0:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise TimeoutError(f"日常_挑战仙缘：等待世界超时，最后 {scene_text} {last_score:.0f}% OCR={last_text[:120]}")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_挑战仙缘：等待世界，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_xianyuan_wait_world",
                    current_scene=scene_id,
                )
        return "success"

    def _wait_daily_lingzu_return_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        scene_ids: list[int],
        *,
        timeout: float,
        label: str,
    ) -> tuple[int, float]:
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, scene_ids)
            last_scene_id, last_score = scene_id, score
            if scene_id in scene_ids:
                with self._lock:
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
                    self._log_locked("success", f"{label}：已到达 #{scene_id} {score:.0f}%")
                return int(scene_id), float(score)
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                expected = "/".join(f"#{scene_id}" for scene_id in scene_ids)
                raise TimeoutError(f"{label} 超时，未检测到 {expected}，最后 {scene_text} {last_score:.0f}%")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    phase="daily_lingzu_wait_return_scene",
                    current_scene=scene_id,
                )

    def _open_daily_lingzu_activity_from_daily(self, ctx: dict[str, Any], stop_event: threading.Event, payload: dict[str, Any]):
        image69 = ctx.get("images", {}).get(69)
        if not isinstance(image69, dict):
            raise RuntimeError("缺少 #69「日常」标注，无法查找灵祖挑战")
        list_shape = self._find_shape(image69, "滚动窗口")
        if list_shape is None:
            raise RuntimeError("缺少 #69「滚动窗口」标注，无法滚动查找灵祖挑战")
        runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        max_scrolls = int(payload.get("max_scrolls") or payload.get("lingzu_max_scrolls") or 10)
        passes = ((False, "当前方向"), (True, "反向"))
        for reverse, direction_label in passes:
            for scroll_index in range(max_scrolls + 1):
                self._raise_if_stopped(stop_event)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_灵祖：{direction_label}查找日常任务「灵祖」 {scroll_index}/{max_scrolls}",
                        phase="daily_lingzu_find_daily_entry",
                        current_scene=69,
                    )
                frame = self._screencap(ctx)
                lines = self._ocr_lines(frame)
                text = self._ocr_text(lines)
                if "灵祖" in text and self._daily_lingzu_progress_done(text):
                    return "done"
                matches = self._ocr_centers_in_shape(lines, image69, "滚动窗口", include=("灵祖",))
                if matches:
                    x, y, matched_text = matches[0]
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"日常_灵祖：点击日常任务 {matched_text}",
                            phase="daily_lingzu_click_daily_entry",
                            current_scene=69,
                        )
                        self._log_locked("action", f"日常_灵祖：点击 #69「{matched_text}」")
                    self._click_frame_point(ctx, image69, x, y)
                    yield from self._wait_scene_id(ctx, stop_event, 183, timeout=18.0, label="日常_灵祖：等待灵祖活动列表 #183")
                    return "open"
                if scroll_index >= max_scrolls:
                    break
                with self._lock:
                    if reverse:
                        self._log_locked("action", f"日常_灵祖：反向未找到「灵祖」，滚动日常列表 {scroll_index + 1}")
                    else:
                        self._log_locked("action", f"日常_灵祖：未找到「灵祖」，滚动日常列表 {scroll_index + 1}")
                if reverse:
                    self._scroll_shape_content(ctx, image69, list_shape, reverse=True)
                    yield from self._wait_scroll_settle(ctx, stop_event)
                else:
                    yield from View(image69).get_shape("滚动窗口").load(runtime)
        raise RuntimeError("日常_灵祖：日常列表未找到「灵祖」任务")

    def _open_daily_lingzu_detail(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
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
        yield from self._wait_scene_id(ctx, stop_event, 184, timeout=18.0, label="日常_灵祖：等待灵祖挑战详情 #184")
        frame = self._screencap(ctx)
        detail_text = self._ocr_text(self._ocr_lines(frame))
        if self._daily_lingzu_remaining_zero(detail_text):
            self._record_daily_lingzu_done(payload, message="详情页显示今日剩余次数 0/1")
            return "done"
        return "open"

    def _run_daily_lingzu_challenge(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
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

        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [184, 185, 186, 187, 188, 189, 34])
        if scene_id == 184:
            go_shape = self._find_shape(image184, "前往")
            if go_shape is None:
                raise RuntimeError("缺少 #184「前往」标注，无法前往战灵长老")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：前往战灵长老", phase="daily_lingzu_go_elder", current_scene=184)
                self._log_locked("action", "日常_灵祖：点击 #184「前往」")
            self._click_shape(ctx, image184, go_shape, frame_data_url=frame)
            scene_id, _score = yield from self._wait_scene_id(ctx, stop_event, 187, timeout=18.0, label="日常_灵祖：等待战灵长老 #187")
            frame = self._screencap(ctx)

        if scene_id == 187:
            challenge_shape = self._find_shape(image187, "灵祖挑战")
            if challenge_shape is None:
                raise RuntimeError("缺少 #187「灵祖挑战」标注，无法进入圣雷龙妖祖")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：进入圣雷龙妖祖", phase="daily_lingzu_open_boss", current_scene=187)
                self._log_locked("action", "日常_灵祖：点击 #187「灵祖挑战」")
            self._click_shape(ctx, image187, challenge_shape, frame_data_url=frame)
            scene_id, _score = yield from self._wait_scene_id(ctx, stop_event, 188, timeout=18.0, label="日常_灵祖：等待圣雷龙妖祖 #188")
            frame = self._screencap(ctx)

        if scene_id == 188:
            text = self._ocr_text(self._ocr_lines(frame))
            if self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示剩余奖励次数 0/1")
                yield from self._return_daily_lingzu_to_world(ctx, stop_event)
                return "success"
            go_shape = self._find_shape(image188, "前往")
            if go_shape is None:
                raise RuntimeError("缺少 #188「前往」标注，无法开始灵祖挑战")
            with self._lock:
                self._set_status_locked("running", "日常_灵祖：开始圣雷龙妖祖挑战", phase="daily_lingzu_start_boss", current_scene=188)
                self._log_locked("action", "日常_灵祖：点击 #188「前往」")
            self._click_shape(ctx, image188, go_shape, frame_data_url=frame)
        elif scene_id == 186:
            self._record_daily_lingzu_done(payload, message="当前已在灵祖奖励完成态")
            yield from self._return_daily_lingzu_to_world(ctx, stop_event)
            return "success"

        start = time.monotonic()
        skipped = False
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            scene_id, score = self._identify_scene_number(ctx, frame, [34, 185, 186, 188, 189])
            if scene_id == 185 or "跳过" in text:
                skip_shape = self._find_shape(image185, "跳过")
                if skip_shape is not None:
                    with self._lock:
                        self._set_status_locked("running", "日常_灵祖：跳过挑战过场", phase="daily_lingzu_skip_cutscene", current_scene=185)
                        self._log_locked("action", "日常_灵祖：点击 #185「跳过」")
                    self._click_shape(ctx, image185, skip_shape, frame_data_url=frame)
                    skipped = True
                    continue
            if scene_id == 189 or "点击退出" in text:
                exit_shape = self._find_shape(image189, "点击退出")
                if exit_shape is None:
                    raise RuntimeError("缺少 #189「点击退出」标注，无法离开灵祖挑战结算")
                with self._lock:
                    self._set_status_locked("running", "日常_灵祖：退出挑战结算", phase="daily_lingzu_exit_result", current_scene=189)
                    self._log_locked("action", "日常_灵祖：点击 #189「点击退出」")
                self._click_shape(ctx, image189, exit_shape, frame_data_url=frame)
                continue
            if scene_id == 186 or "点击查看" in text or "灵环" in text or "宝魄" in text:
                self._record_daily_lingzu_done(payload, message="已回到世界并出现灵祖奖励")
                yield from self._return_daily_lingzu_to_world(ctx, stop_event)
                return "success"
            if scene_id == 188 and self._daily_lingzu_remaining_zero(text):
                self._record_daily_lingzu_done(payload, message="圣雷龙妖祖页显示挑战次数已消耗")
                yield from self._return_daily_lingzu_to_world(ctx, stop_event)
                return "success"
            if scene_id == 34 or ("日常" in text and ("储物袋" in text or "战斗" in text)):
                self._record_daily_lingzu_done(payload, message="挑战后已回到世界")
                yield from self._return_daily_lingzu_to_world(ctx, stop_event)
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

    def _execute_xianfu_visit_partner_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙府_寻访仙侣资产树路径，无法执行作业")
        raw_max_continue = payload.get("max_continue", 20)
        max_continue = int(20 if raw_max_continue in {None, ""} else raw_max_continue)
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        frame = self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame, [175, 174, 173, 172, 171, 34])
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})

        if scene_id == 175:
            yield from self._handle_xianfu_continue_visit_popup(runtime, max_continue=max_continue)
            scene_id = 174

        if scene_id != 174:
            if scene_id not in {171, 172, 173}:
                with self._lock:
                    self._set_status_locked("running", "仙府_寻访仙侣：进入仙府主页 #171", phase="xianfu_visit_go_home")
                    self._log_locked("action", "仙府_寻访仙侣：按场景图跳转到 #171")
                yield from runtime.goto_view(171)
                scene_id = 171
            if scene_id == 171:
                view171 = runtime.get_view(171)
                shape = view171.get_shape("寻仙台") if isinstance(view171, View) else None
                if shape is None:
                    raise RuntimeError("缺少 #171「寻仙台」标注，无法进入寻仙台")
                with self._lock:
                    self._set_status_locked("running", "仙府_寻访仙侣：点击寻仙台", phase="xianfu_visit_open_platform", current_scene=171)
                    self._log_locked("action", "仙府_寻访仙侣：点击 #171「寻仙台」")
                shape.click(runtime)
                yield from runtime.wait_view(172, timeout=18.0, label="仙府_寻访仙侣：等待寻仙台 #172")
                scene_id = 172
            if scene_id == 172:
                view172 = runtime.get_view(172)
                shape = view172.get_shape("寻访") if isinstance(view172, View) else None
                if shape is None:
                    raise RuntimeError("缺少 #172「寻访」标注，无法进入仙侣寻访")
                with self._lock:
                    self._set_status_locked("running", "仙府_寻访仙侣：进入寻访", phase="xianfu_visit_open_visit", current_scene=172)
                    self._log_locked("action", "仙府_寻访仙侣：点击 #172「寻访」")
                shape.click(runtime)
                view = yield from runtime.wait_view(173, 174, timeout=18.0, label="仙府_寻访仙侣：等待寻访页")
                scene_id = view.id if isinstance(view, View) else None
            if scene_id == 173:
                view173 = runtime.get_view(173)
                shape = view173.get_shape("绝品仙侣") if isinstance(view173, View) else None
                if shape is None:
                    raise RuntimeError("缺少 #173「绝品仙侣」标注，无法切换绝品页")
                with self._lock:
                    self._set_status_locked("running", "仙府_寻访仙侣：切换绝品仙侣", phase="xianfu_visit_open_juepin", current_scene=173)
                    self._log_locked("action", "仙府_寻访仙侣：点击 #173「绝品仙侣」")
                shape.click(runtime)
                yield from runtime.wait_view(174, timeout=18.0, label="仙府_寻访仙侣：等待绝品仙侣 #174")

        image174 = ctx.get("images", {}).get(174)
        if not isinstance(image174, dict):
            raise RuntimeError("缺少 #174 绝品仙侣标注，无法读取寻访状态")
        frame = runtime.cur_frame(update=True)
        lines = self._ocr_lines_in_shapes(frame, image174, ("状态", "免费提示"), padding=16)
        status_text = self._ocr_text(lines)
        cd_seconds = _parse_xianfu_visit_cd_seconds(status_text)
        if cd_seconds is None:
            raise RuntimeError(f"仙府_寻访仙侣：无法识别免费寻访倒计时：{status_text or '空'}")
        if cd_seconds > 0:
            next_time = (_now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-visit-partner")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_visit_partner",
                label="仙府_寻访仙侣",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_寻访仙侣：未到免费时间，{status_text}，下次 {next_time}",
                    phase="xianfu_visit_wait_cd",
                    current_scene=174,
                )
                self._log_locked("success", self._status["message"])
            return "success"

        image175 = ctx.get("images", {}).get(175)
        if not isinstance(image175, dict):
            self._log("skip", "仙府_寻访仙侣：当前可免费寻访，但缺少 #175「继续寻访」弹窗标注，暂不自动点击")
            return "skipped"
        view174 = runtime.get_view(174)
        visit_shape = view174.get_shape("寻访") if isinstance(view174, View) else None
        if visit_shape is None:
            raise RuntimeError("缺少 #174「寻访」标注，无法执行免费寻访")
        with self._lock:
            self._set_status_locked("running", "仙府_寻访仙侣：免费寻访一次", phase="xianfu_visit_free_draw", current_scene=174)
            self._log_locked("action", "仙府_寻访仙侣：点击 #174「寻访」")
        visit_shape.click(runtime)
        yield from self._handle_xianfu_continue_visit_popup(runtime, max_continue=max_continue)
        frame = runtime.cur_frame(update=True)
        lines = self._ocr_lines_in_shapes(frame, image174, ("状态", "免费提示"), padding=16)
        status_text = self._ocr_text(lines)
        cd_seconds = _parse_xianfu_visit_cd_seconds(status_text)
        if cd_seconds and cd_seconds > 0:
            next_time = (_now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-visit-partner")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_visit_partner",
                label="仙府_寻访仙侣",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_寻访仙侣：寻访后读取 CD {status_text}，下次 {next_time}",
                    phase="xianfu_visit_done",
                    current_scene=174,
                )
                self._log_locked("success", self._status["message"])
            return "success"
        self._log("skip", f"仙府_寻访仙侣：寻访后未读到有效 CD：{status_text or '空'}")
        return "skipped"

    def _handle_xianfu_continue_visit_popup(self, runtime: FanxiuRuntime, *, max_continue: int = 20):
        view175 = runtime.get_view(175)
        if not isinstance(view175, View):
            raise RuntimeError("缺少 #175「继续寻访」标注，无法处理寻访结果弹窗")
        continue_count = 0
        max_continue_count = max(0, int(max_continue))
        while True:
            yield from runtime.wait_view(175, timeout=18.0, label="仙府_寻访仙侣：等待继续寻访弹窗 #175")
            frame = runtime.cur_frame(update=True)
            half_lines = self._ocr_lines_in_shapes(frame, view175.raw, ("半价",), padding=24)
            half_text = self._ocr_text(half_lines)
            half_value = _parse_first_int(half_text)
            if half_value is not None and half_value < 100 and continue_count < max_continue_count:
                continue_shape = view175.get_shape("继续")
                if continue_shape is None:
                    raise RuntimeError("缺少 #175「继续」标注，无法执行半价继续寻访")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"仙府_寻访仙侣：半价 {half_value}，继续寻访",
                        phase="xianfu_visit_continue",
                        current_scene=175,
                    )
                    self._log_locked("action", f"仙府_寻访仙侣：点击 #175「继续」，半价={half_value}")
                continue_shape.click(runtime)
                continue_count += 1
                continue
            break
        close_shape = view175.get_shape("关闭")
        if close_shape is None:
            raise RuntimeError("缺少 #175「关闭」标注，无法关闭寻访结果弹窗")
        with self._lock:
            self._set_status_locked("running", "仙府_寻访仙侣：关闭继续寻访弹窗", phase="xianfu_visit_close_continue", current_scene=175)
            self._log_locked("action", "仙府_寻访仙侣：点击 #175「关闭」")
        close_shape.click(runtime)
        yield from runtime.wait_view(174, timeout=18.0, label="仙府_寻访仙侣：关闭弹窗后回到 #174")
        return "success"

    def _execute_xianfu_learn_skill_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少仙府_领悟绝技资产树路径，无法执行作业")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if not isinstance(images.get(176), dict):
            self._log("skip", "仙府_领悟绝技：缺少 #176「绝技」页面标注，暂不自动点击")
            return "skipped"
        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        preferred = [177, 176, 172, 171, 34]
        frame = self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame, preferred)
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})

        if scene_id == 177:
            yield from self._handle_xianfu_learn_skill_result_popup(runtime)
            scene_id = 176

        if scene_id != 176:
            if scene_id not in {171, 172}:
                with self._lock:
                    self._set_status_locked("running", "仙府_领悟绝技：进入仙府主页 #171", phase="xianfu_skill_go_home")
                    self._log_locked("action", "仙府_领悟绝技：按场景图跳转到 #171")
                yield from runtime.goto_view(171)
                scene_id = 171
            if scene_id == 171:
                view171 = runtime.get_view(171)
                platform_shape = view171.get_shape("寻仙台") if isinstance(view171, View) else None
                if platform_shape is None:
                    raise RuntimeError("缺少 #171「寻仙台」标注，无法进入寻仙台")
                with self._lock:
                    self._set_status_locked("running", "仙府_领悟绝技：点击寻仙台", phase="xianfu_skill_open_platform", current_scene=171)
                    self._log_locked("action", "仙府_领悟绝技：点击 #171「寻仙台」")
                platform_shape.click(runtime)
                yield from runtime.wait_view(172, timeout=18.0, label="仙府_领悟绝技：等待寻仙台 #172")
                scene_id = 172
            if scene_id == 172:
                view172 = runtime.get_view(172)
                skill_shape = view172.get_shape("领悟绝技") if isinstance(view172, View) else None
                if skill_shape is None:
                    raise RuntimeError("缺少 #172「领悟绝技」标注，无法进入绝技页")
                with self._lock:
                    self._set_status_locked("running", "仙府_领悟绝技：进入绝技页", phase="xianfu_skill_open_page", current_scene=172)
                    self._log_locked("action", "仙府_领悟绝技：点击 #172「领悟绝技」")
                skill_shape.click(runtime)
                yield from runtime.wait_view(176, timeout=18.0, label="仙府_领悟绝技：等待绝技 #176")

        image176 = images.get(176)
        if not isinstance(image176, dict):
            raise RuntimeError("缺少 #176 绝技标注，无法读取领悟状态")
        frame = yield from self._ensure_xianfu_learn_skill_xianpin_tab(runtime, image176)
        status_lines = self._ocr_lines_in_shapes(frame, image176, ("状态", "价格"), padding=16)
        status_text = self._ocr_text(status_lines)
        cd_seconds = _parse_xianfu_skill_cd_seconds(status_text)
        if cd_seconds is None:
            fallback_seconds = int(payload.get("fallback_seconds") or 1800)
            next_time = (_now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-learn-skill")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_learn_skill",
                label="仙府_领悟绝技",
            )
            self._log("skip", f"仙府_领悟绝技：未识别到免费领悟或倒计时，当前文本：{status_text or '空'}；{next_time} 兜底重试")
            return "skipped"
        if cd_seconds > 0:
            next_time = (_now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-learn-skill")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_learn_skill",
                label="仙府_领悟绝技",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_领悟绝技：未到免费时间，{status_text}，下次 {next_time}；本轮未点击领悟",
                    phase="xianfu_skill_wait_cd",
                    current_scene=176,
                )
                self._log_locked("skip", self._status["message"])
            return "skipped"

        if not isinstance(images.get(177), dict):
            self._log("skip", "仙府_领悟绝技：当前可免费领悟，但缺少 #177「领悟绝技」结果弹窗标注，暂不自动点击")
            return "skipped"
        view176 = runtime.get_view(176)
        learn_shape = view176.get_shape("领悟一次") if isinstance(view176, View) else None
        if learn_shape is None:
            raise RuntimeError("缺少 #176「领悟一次」标注，无法执行免费领悟")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：免费领悟一次", phase="xianfu_skill_free_draw", current_scene=176)
            self._log_locked("action", "仙府_领悟绝技：点击 #176「领悟一次」")
        learn_shape.click(runtime)
        yield from self._handle_xianfu_learn_skill_result_popup(runtime)

        frame = runtime.cur_frame(update=True)
        status_lines = self._ocr_lines_in_shapes(frame, image176, ("状态", "价格"), padding=16)
        status_text = self._ocr_text(status_lines)
        cd_seconds = _parse_xianfu_skill_cd_seconds(status_text)
        if cd_seconds and cd_seconds > 0:
            next_time = (_now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-learn-skill")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_learn_skill",
                label="仙府_领悟绝技",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_领悟绝技：领悟后读取 CD {status_text}，下次 {next_time}",
                    phase="xianfu_skill_done",
                    current_scene=176,
                )
                self._log_locked("success", self._status["message"])
            return "success"
        self._log("skip", f"仙府_领悟绝技：领悟后未读到有效 CD：{status_text or '空'}")
        return "skipped"

    def _ensure_xianfu_learn_skill_xianpin_tab(self, runtime: FanxiuRuntime, image176: dict[str, Any]):
        frame = runtime.cur_frame(update=True)
        status_text = self._ocr_text(self._ocr_lines_in_shapes(frame, image176, ("状态", "价格"), padding=16))
        if _parse_xianfu_skill_cd_seconds(status_text) is not None:
            self._log("detail", f"仙府_领悟绝技：当前绝技页状态区已可读，跳过重复切换仙品绝技：{status_text}")
            return frame
        yield from self._switch_xianfu_learn_skill_xianpin_tab(runtime)
        return runtime.cur_frame(update=True)

    def _switch_xianfu_learn_skill_xianpin_tab(self, runtime: FanxiuRuntime):
        view176 = runtime.get_view(176)
        tab_shape = view176.get_shape("仙品绝技") if isinstance(view176, View) else None
        if tab_shape is None:
            raise RuntimeError("缺少 #176「仙品绝技」标注，无法切换到仙品绝技读取 CD")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：切换仙品绝技", phase="xianfu_skill_open_xianpin", current_scene=176)
            self._log_locked("action", "仙府_领悟绝技：点击 #176「仙品绝技」")
        tab_shape.click(runtime)
        yield from runtime.wait_view(176, timeout=5.0, label="仙府_领悟绝技：等待仙品绝技 #176")

    def _handle_xianfu_learn_skill_result_popup(self, runtime: FanxiuRuntime):
        view177 = runtime.get_view(177)
        if not isinstance(view177, View):
            raise RuntimeError("缺少 #177「领悟绝技」结果弹窗标注，无法继续")
        yield from runtime.wait_view(177, timeout=18.0, label="仙府_领悟绝技：等待结果弹窗 #177")
        continue_shape = view177.get_shape("继续")
        if continue_shape is None:
            raise RuntimeError("缺少 #177「继续」标注，无法关闭领悟结果")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：关闭结果弹窗", phase="xianfu_skill_continue", current_scene=177)
            self._log_locked("action", "仙府_领悟绝技：点击 #177「继续」")
        continue_shape.click(runtime)
        yield from runtime.wait_view(176, timeout=18.0, label="仙府_领悟绝技：返回绝技 #176")
        return "success"

    def _open_mail_cleanup_entry(self, runtime: FanxiuRuntime):
        """按清理邮件伪代码进入邮件页：#34 -> #68 或 #34 -> #35。"""

        yield from runtime.goto_view(34)
        view68 = runtime.get_view(68)
        mail68 = view68.get_shape("邮件") if isinstance(view68, View) else None
        if mail68 is not None and mail68.is_match(runtime):
            with self._lock:
                self._set_status_locked("running", "邮件_清理：点击 #68 邮件入口", phase="mail_cleanup_open_68", current_scene=68)
                self._log_locked("action", "邮件_清理：#68「邮件」已匹配，点击进入 #121")
            mail68.click(runtime)
        else:
            view34 = runtime.get_view(34)
            open_shape = view34.get_shape("打开下方菜单") if isinstance(view34, View) else None
            if open_shape is None:
                raise RuntimeError("缺少 #34「打开下方菜单」标注，无法进入邮件")
            with self._lock:
                self._set_status_locked("running", "邮件_清理：打开 #35 下方菜单", phase="mail_cleanup_open_35", current_scene=34)
                self._log_locked("action", "邮件_清理：#68 不可用，点击 #34「打开下方菜单」")
            open_shape.click(runtime)
            yield from runtime.wait_view(35, timeout=8.0, label="邮件_清理：等待 #35 下方菜单")

            view35 = runtime.get_view(35)
            mail35 = view35.get_shape("邮件") if isinstance(view35, View) else None
            if mail35 is None:
                raise RuntimeError("缺少 #35「邮件」标注，无法进入邮件")
            with self._lock:
                self._set_status_locked("running", "邮件_清理：等待并点击 #35 邮件入口", phase="mail_cleanup_click_35_mail", current_scene=35)
                self._log_locked("action", "邮件_清理：等待 #35「邮件」命中后进入 #121")
            try:
                yield from mail35.wait_click(runtime, timeout=8.0)
            except TimeoutError:
                x, y = self._mail_world_menu_icon_click_point(view35.raw, 0, 0)
                self._log(
                    "info",
                    f"邮件_清理：#35「邮件」OCR 未命中，改按固定标注点击 ({x:.0f},{y:.0f})",
                )
                self._click_frame_point(runtime.ctx, view35.raw, x, y)
                runtime.clear_frame()

        yield from runtime.wait_view(121, timeout=18.0, label="邮件_清理：等待邮件 #121")
        return "success"

    def _runtime_mail_rows_from_frame(self, runtime: FanxiuRuntime, view121: View, frame: str) -> list[_RuntimeMailRow]:
        if not isinstance(view121.raw, dict):
            return []
        rows = self._recognize_visible_mail_rows(runtime.ctx, view121.raw, frame)
        result: list[_RuntimeMailRow] = []
        for row in rows:
            shape = self._mail_row_title_shape(view121, row)
            if shape is not None:
                result.append(_RuntimeMailRow(row, shape))
        return result

    def _mail_row_title_shape(self, view: View, row: dict[str, Any]) -> Shape | None:
        if not isinstance(view.raw, dict):
            return None
        width, height = self._frame_size(view.raw)
        try:
            x = float(row.get("x") or 0)
            y = float(row.get("y") or 0)
        except (TypeError, ValueError):
            return None
        raw = {
            "id": f"mail-row-title:{row.get('time_text') or ''}:{row.get('title') or ''}",
            "kind": "rect",
            "title": str(row.get("title") or "邮件标题"),
            "x": max(0.0, min(0.999, (x - 16.0) / max(1, width))),
            "y": max(0.0, min(0.999, (y - 12.0) / max(1, height))),
            "w": max(1.0 / max(1, width), 32.0 / max(1, width)),
            "h": max(1.0 / max(1, height), 24.0 / max(1, height)),
            "imageMatchRole": "off",
            "ocrMatchRole": "off",
        }
        return Shape(raw, parent_view=view)

    def _claim_runtime_mail_row(self, runtime: FanxiuRuntime, mail: _RuntimeMailRow):
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_清理：打开「{mail.title}」",
                phase="mail_cleanup_open_row",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_清理：点击标题「{mail.title}」")
        mail.title_shape.click(runtime)
        detail_view = yield from runtime.wait_view(122, 123, timeout=12.0, label=f"邮件_清理：等待「{mail.title}」详情")
        if not isinstance(detail_view, View) or detail_view.id not in {122, 123}:
            return "claim"
        actual_policy = "claim" if detail_view.id == 122 else "delete"
        action_title = "领取" if detail_view.id == 122 else "删除"
        action_shape = detail_view.get_shape(action_title)
        if action_shape is None and detail_view.id == 123:
            action_shape = detail_view.get_shape("领取")
        if action_shape is None:
            raise RuntimeError(f"缺少 #{detail_view.id}「{action_title}」标注，无法处理邮件")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_清理：{action_title}「{mail.title}」",
                phase="mail_cleanup_claim",
                current_scene=detail_view.id,
            )
            self._log_locked("action", f"邮件_清理：点击 #{detail_view.id}「{action_shape.title}」")
        action_shape.click(runtime)
        try:
            yield from runtime.wait_view(121, timeout=18.0, label="邮件_清理：返回邮件 #121")
        except TimeoutError as exc:
            back_shape = detail_view.get_shape("空白-返回")
            if back_shape is None:
                raise
            self._log("info", f"邮件_清理：{action_title}后未自动回列表，点击详情页返回：{exc}")
            back_shape.click(runtime)
            yield from runtime.wait_view(121, timeout=12.0, label="邮件_清理：详情页返回邮件 #121")
        return actual_policy

    def _refresh_recent_mail_packets_for_runtime_log(self, label: str, *, flush_capture: bool) -> None:
        try:
            pcap_paths: list[str] = []
            if flush_capture:
                flush = fanxiu_capture_runtime_service.flush_recent_capture(f"mail-cleanup:{label}", restart=False)
                pcap_path = str(flush.get("pcap_path") or "").strip() if isinstance(flush, dict) else ""
                if pcap_path and bool(flush.get("flushed")):
                    pcap_paths.append(pcap_path)
                with self._lock:
                    self._log_locked(
                        "info",
                        "邮件_抓包协作："
                        f"{label} flush={bool(flush.get('flushed')) if isinstance(flush, dict) else False} "
                        f"pcap_size={flush.get('pcap_size', 0) if isinstance(flush, dict) else 0}",
                    )
            if pcap_paths:
                result = sync_fanxiu_capture_paths(pcap_paths, max_streams=4)
            else:
                result = {"decoded_count": 0, "mail_packet_sync": {}}
            mail_sync = result.get("mail_packet_sync") or {}
            if not mail_sync:
                for decoded_item in result.get("decoded") or []:
                    if isinstance(decoded_item, dict) and isinstance(decoded_item.get("batch_mail_packet_sync"), dict):
                        mail_sync = decoded_item["batch_mail_packet_sync"]
                        break
            with self._lock:
                self._log_locked(
                    "info",
                    "邮件_抓包协作："
                    f"{label} decoded={result.get('decoded_count', 0)} "
                    f"updated={mail_sync.get('updated', 0)} inserted={mail_sync.get('inserted', 0)} "
                    f"source_packets={mail_sync.get('source_packets', 0)} action_packets={mail_sync.get('action_packets', 0)}",
                )
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"邮件_抓包协作：{label}失败：{exc}")

    def _open_mail_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        asset_tree_path: Path,
        *,
        entry_mode: str = "dynamic",
    ) -> str:
        if entry_mode in {"stable", "menu", "full", "full_scan", "debug"}:
            visible_menu_result = self._try_open_mail_from_visible_world_menu(ctx, stop_event, timeout=1.0)
            opened_from_menu = (yield from visible_menu_result) if isinstance(visible_menu_result, GeneratorType) else visible_menu_result
            if opened_from_menu == "success":
                return "success"
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：确认世界 #34", phase="mail_claim_go_world")
            self._log_locked("action", "邮件_历史扫描：先确认进入世界 #34")
        go_scene_result = self._go_scene_task(ctx, asset_tree_path, 34, stop_event)
        result = (yield from go_scene_result) if isinstance(go_scene_result, GeneratorType) else go_scene_result
        if result != "success":
            return result
        if entry_mode in {"stable", "menu", "full", "full_scan", "debug"}:
            stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path)
            return (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result
        dynamic_result = self._try_open_mail_dynamic_entry(ctx, stop_event)
        opened = (yield from dynamic_result) if isinstance(dynamic_result, GeneratorType) else dynamic_result
        if opened == "no_mail":
            return "no_mail"
        if opened == "success":
            return "success"
        stable_result = self._open_mail_stable_entry(ctx, stop_event, asset_tree_path)
        return (yield from stable_result) if isinstance(stable_result, GeneratorType) else stable_result

    def _try_open_mail_dynamic_entry(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        image34 = ctx.get("images", {}).get(34)
        image68 = ctx.get("images", {}).get(68)
        if not isinstance(image68, dict) and isinstance(image34, dict):
            image68 = self._find_child_image_by_number(image34, 68)
        mail_shape = self._find_shape(image68, "邮件") if isinstance(image68, dict) else None
        if not isinstance(image68, dict) or not mail_shape:
            self._log("detail", "邮件_历史扫描：#68 动态邮件入口标注缺失，尝试稳定入口")
            return "missing"
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：检测 #68 邮件入口", phase="mail_claim_check_mail", current_scene=34)
            self._log_locked("action", "邮件_历史扫描：检测 #68「邮件」")
        frame = self._screencap(ctx)
        result = self._match_shape(ctx, image68, mail_shape, frame)
        similarity = float(result.get("similarity") or 0)
        matched = bool(result.get("matched"))
        if not matched:
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：#68 未命中，改走 #35 稳定入口", phase="mail_claim_dynamic_missing", current_scene=34)
                self._log_locked("info", f"邮件_历史扫描：未发现 #68「邮件」{similarity:.0f}%，改走稳定入口")
            return "missing"
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：打开 #68 邮件入口", phase="mail_claim_open_mail", current_scene=34)
            self._log_locked("action", f"邮件_历史扫描：识别到 #68「邮件」{similarity:.0f}%，点击打开")
        self._click_shape(ctx, image68, mail_shape, frame)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
        return "success"

    def _open_mail_stable_entry(self, ctx: dict[str, Any], stop_event: threading.Event, asset_tree_path: Path) -> str:
        image34 = ctx.get("images", {}).get(34)
        open_shape = self._find_shape(image34, "打开下方菜单") if isinstance(image34, dict) else None
        if not isinstance(image34, dict) or not open_shape:
            raise RuntimeError("缺少 #34「打开下方菜单」标注，无法走稳定邮件入口")
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：打开下方菜单 #35", phase="mail_claim_open_world_menu", current_scene=34)
            self._log_locked("action", "邮件_历史扫描：#68 不可用，尝试 #34 -> #35 稳定入口")
        frame = self._screencap(ctx)
        try:
            self._click_shape(ctx, image34, open_shape, frame)
        except RuntimeError as exc:
            message = str(exc)
            if "打开下方菜单" not in message or "定位" not in message:
                raise
            box = self._box(open_shape, image34)
            x = float(box.get("x") or 0) + float(box.get("w") or 0) / 2
            y = float(box.get("y") or 0) + float(box.get("h") or 0) / 2
            with self._lock:
                self._log_locked("info", f"邮件_历史扫描：#34「打开下方菜单」图像定位失败，改按固定标注点击 ({x:.0f},{y:.0f})")
            self._click_frame_point(ctx, image34, x, y)
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：等待下方菜单展开", phase="mail_claim_wait_world_menu", current_scene=34)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            yield BehaviorTreeStatus.RUNNING
        menu_result = self._open_mail_from_world_menu_shape(ctx, stop_event)
        return (yield from menu_result) if isinstance(menu_result, GeneratorType) else menu_result

    def _open_mail_from_world_menu_shape(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        # Runtime actions must be driven by the asset-tree annotations. Do not
        # infer alternate menu coordinates from screenshots here; if the current
        # UI changed, update the #34/#35/#121 shapes or sceneJumpTarget data.
        image35 = ctx.get("images", {}).get(35)
        mail_shape = self._find_shape(image35, "邮件") if isinstance(image35, dict) else None
        menu_shape = self._find_shape(image35, "菜单") if isinstance(image35, dict) else None
        if not isinstance(image35, dict) or (not mail_shape and not menu_shape):
            raise RuntimeError("缺少 #35「邮件」或「菜单」标注，无法走稳定邮件入口")
        self._raise_if_stopped(stop_event)
        with self._lock:
            self._set_status_locked("running", "邮件_历史扫描：等待 #35 邮件命中", phase="mail_claim_wait_world_menu_mail", current_scene=35)
        if mail_shape and not menu_shape:
            wait_result = self._wait_shape_match(
                ctx,
                stop_event,
                image35,
                mail_shape,
                timeout=8.0,
                label="邮件_历史扫描：等待 #35「邮件」",
            )
            frame, match_result = (yield from wait_result) if isinstance(wait_result, GeneratorType) else wait_result
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件", phase="mail_claim_click_world_menu_mail", current_scene=35)
                self._log_locked("action", "邮件_历史扫描：按 #35「邮件」标注点击")
            self._click_shape(ctx, image35, mail_shape, frame, match_result=match_result)
            yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
            return "success"
        deadline = time.time() + 8.0
        last_score = 0.0
        last_ocr = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            if mail_shape:
                match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto")
                last_score = max(last_score, match_score)
                if match_score >= float(self.scene_threshold):
                    mail_box = self._box(mail_shape, image35)
                    x = float(mail_box.get("x") or 0) + float(mail_box.get("w") or 0) / 2
                    y = float(mail_box.get("y") or 0) + float(mail_box.get("h") or 0) / 2
                    with self._lock:
                        self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件", phase="mail_claim_click_world_menu_mail", current_scene=35)
                        self._log_locked("action", f"邮件_历史扫描：#35「邮件」标注命中 {match_score:.0f}%，点击标注中心 ({x:.0f},{y:.0f})")
                    self._click_frame_point(ctx, image35, x, y)
                    yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                    return "success"

            ocr_lines = self._ocr_lines(frame)
            last_ocr = " / ".join(str(item.get("text") or "") for item in ocr_lines[-3:]) or last_ocr
            menu_matches = self._ocr_centers_in_shape(ocr_lines, image35, "菜单", include=("邮件",))
            if menu_matches:
                x, y, text = menu_matches[0]
                x, y = self._mail_world_menu_icon_click_point(image35, x, y)
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                    self._log_locked("action", f"邮件_历史扫描：#35 菜单 OCR 命中「{text}」，点击邮件入口 ({x:.0f},{y:.0f})")
                self._click_frame_point(ctx, image35, x, y)
                yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                return "success"

            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：等待 #35 邮件入口 {last_score:.0f}%",
                    phase="mail_claim_wait_world_menu_mail",
                    current_scene=35,
                )
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError(f"邮件_历史扫描：等待 #35 邮件入口超时，最后 {last_score:.0f}% OCR={last_ocr}")

    def _mail_world_menu_icon_click_point(self, image35: dict[str, Any], x: float, y: float) -> tuple[float, float]:
        mail_shape = self._find_shape(image35, "邮件")
        if mail_shape:
            box = self._box(mail_shape, image35)
            return float(box.get("x") or 0) + float(box.get("w") or 0) / 2, float(box.get("y") or 0) + float(box.get("h") or 0) * 0.78
        return x, y

    def _try_open_mail_from_visible_world_menu(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
    ) -> str:
        image35 = ctx.get("images", {}).get(35)
        if not isinstance(image35, dict):
            return "missing"
        mail_shape = self._find_shape(image35, "邮件")
        deadline = time.time() + max(0.1, float(timeout or 0.1))
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            if mail_shape:
                scene_id, score, _frame = self._current_scene_number(ctx)
                if scene_id == 35 and score >= float(self.scene_threshold):
                    return (yield from self._open_mail_from_world_menu_shape(ctx, stop_event))
                match_score = self._shape_score(ctx, image35, mail_shape, frame, match_strategy="auto")
                if match_score >= float(self.scene_threshold):
                    return (yield from self._open_mail_from_world_menu_shape(ctx, stop_event))
            menu_matches = self._ocr_centers_in_shape(self._ocr_lines(frame), image35, "菜单", include=("邮件",))
            if menu_matches:
                x, y, text = menu_matches[0]
                x, y = self._mail_world_menu_icon_click_point(image35, x, y)
                with self._lock:
                    self._set_status_locked("running", "邮件_历史扫描：点击 #35 邮件 OCR", phase="mail_claim_click_world_menu_mail", current_scene=35)
                    self._log_locked("action", f"邮件_历史扫描：#35 无「邮件」shape，点击菜单 OCR「{text}」({x:.0f},{y:.0f})")
                self._click_frame_point(ctx, image35, x, y)
                yield from self._wait_mail_list_ready(ctx, stop_event, timeout=12.0, label="邮件_历史扫描：等待邮件 #121")
                return "success"
            with self._lock:
                self._set_status_locked("running", "邮件_历史扫描：探测可见下方菜单邮件入口", phase="mail_claim_probe_world_menu_mail")
            yield BehaviorTreeStatus.RUNNING
        return "missing"

    def _scan_mail_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        action_enabled: bool = True,
        scan_mode: str = "incremental",
        action_policies: set[str] | None = None,
        max_actions: int | None = None,
        target_title: str = "",
        target_time_text: str = "",
        game_first: bool = False,
        fail_on_packet_gap: bool = False,
    ) -> str:
        image121 = ctx.get("images", {}).get(121)
        if not isinstance(image121, dict):
            raise RuntimeError("缺少 #121 邮件帧标注，无法扫描邮件")
        first_shape = self._find_shape(image121, "第1封")
        list_shape = self._find_shape(image121, "邮件清单2") or self._find_shape(image121, "邮件清单")
        if not first_shape:
            raise RuntimeError("缺少 #121「第1封」标注，无法处理首封邮件")
        if not list_shape:
            raise RuntimeError("缺少 #121「邮件清单2」标注，无法遍历邮件清单")

        processed_count = 0
        seen_count = 0
        scroll_count = 0
        scan_started_at = time.monotonic()
        last_signature = ""
        stable_same_pages = 0
        empty_pages = 0
        max_scrolls = 80
        configured_max_actions = max_actions is not None
        max_actions = max(1, min(int(max_actions or 200), 200))
        max_stable_same_pages = 5
        max_empty_pages = 5
        mode = str(scan_mode or "incremental").strip().lower()
        full_scan = mode in {"full", "full_scan", "observe", "observe_only"}
        target_title = str(target_title or "").strip()
        target_time_text = self._normalize_mail_time_text(str(target_time_text or "").strip())
        target_requested = bool(target_title or target_time_text)
        allowed_policies = (set(action_policies or {"claim", "delete"}) & {"claim", "delete"}) or {"claim", "delete"}
        pending_actions = self._pending_packet_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and (pending_actions > 0 or full_scan):
            max_scrolls = min(max_scrolls, 24)
            max_scan_seconds = 420.0 if full_scan else 300.0
        elif not action_enabled and full_scan:
            max_scrolls = min(max_scrolls, 16)
            max_scan_seconds = 360.0
        else:
            max_scan_seconds = 0.0
        scan_state = self._read_mail_scan_state()
        watermark_time = "" if full_scan else str(scan_state.get("confirmed_time_bucket") or "")
        top_time = ""
        crossed_watermark = not bool(watermark_time)
        scan_truncated = False
        packet_missing_rows: list[dict[str, str]] = []
        packet_missing_traces: list[dict[str, Any]] = []
        if action_enabled:
            with self._lock:
                self._log_locked("info", f"邮件_历史扫描：packet 待处理邮件 {pending_actions} 封")
                if target_requested:
                    target_parts = []
                    if target_title:
                        target_parts.append(f"标题={target_title}")
                    if target_time_text:
                        target_parts.append(f"时间={target_time_text}")
                    self._log_locked("info", f"邮件_历史扫描：本轮只处理目标邮件：{'，'.join(target_parts)}")
            if pending_actions <= 0 and not full_scan and not target_requested:
                with self._lock:
                    self._log_locked("success", "邮件_历史扫描：packet 无待处理邮件，跳过动作扫描")
                return "success"
        if watermark_time:
            with self._lock:
                self._log_locked("info", f"邮件_历史扫描：增量扫描水位 {watermark_time}，需扫到更早时间才可停止")
        else:
            with self._lock:
                self._log_locked("info", "邮件_历史扫描：未建立增量水位，本轮按深扫建立水位")
        while scroll_count <= max_scrolls and processed_count < max_actions:
            self._raise_if_stopped(stop_event)
            if max_scan_seconds > 0 and time.monotonic() - scan_started_at >= max_scan_seconds:
                scan_truncated = True
                with self._lock:
                    self._log_locked(
                        "info",
                        f"邮件_历史扫描：动作扫描达到内部时间预算 {max_scan_seconds:.0f}s，提前收尾",
                    )
                break
            frame = self._screencap(ctx)
            rows = self._recognize_visible_mail_rows(ctx, image121, frame)
            action_candidate: dict[str, Any] | None = None
            game_first_candidate: dict[str, Any] | None = None
            for row in rows:
                self._prepare_mail_row_policy(row, action_enabled=action_enabled, action_policies=allowed_policies)
                if row.get("packet_match") == "missing" and len(packet_missing_rows) < 20:
                    missing_item = {
                        "title": str(row.get("title") or ""),
                        "time_text": str(row.get("time_text") or ""),
                        "reason": str(row.get("packet_missing_reason") or ""),
                    }
                    packet_missing_rows.append(
                        missing_item
                    )
                    if len(packet_missing_traces) < 8:
                        packet_missing_traces.append(self._trace_mail_packet_gap(missing_item))
                if target_requested and not self._mail_row_matches_target(row, target_title=target_title, target_time_text=target_time_text):
                    row["policy"] = ""
                seen_count += 1
                top_time = top_time or str(row.get("time_text") or "")
                if watermark_time and self._mail_time_is_older_than(str(row.get("time_text") or ""), watermark_time):
                    crossed_watermark = True
                if action_candidate is None and row.get("policy"):
                    action_candidate = row
                if (
                    game_first
                    and action_enabled
                    and game_first_candidate is None
                    and row.get("packet_match") == "missing"
                    and not row.get("policy")
                ):
                    game_first_candidate = row
            if rows:
                row_summary = "；".join(
                    f"{str(row.get('title') or '')[:18]}|{row.get('time_text') or '-'}|{row.get('policy') or '-'}|{row.get('packet_match') or '-'}|lock={row.get('list_lock_score', '-')}"
                    for row in rows[:6]
                )
                with self._lock:
                    self._log_locked("detail", f"邮件_历史扫描：当前页重新 OCR 识别 {len(rows)} 行：{row_summary}")
            if action_candidate is not None:
                action_status = self._process_mail_row(ctx, stop_event, image121, action_candidate)
                status = (yield from action_status) if isinstance(action_status, GeneratorType) else action_status
                if status == "processed":
                    processed_count += 1
                    if target_requested:
                        break
                    if not full_scan and self._pending_packet_mail_action_count(allowed_policies=allowed_policies) <= 0:
                        with self._lock:
                            self._log_locked("success", "邮件_历史扫描：packet 待处理邮件已清零，停止扫描")
                        break
                    continue
            if game_first_candidate is not None:
                action_status = self._process_mail_row_by_detail(
                    ctx,
                    stop_event,
                    image121,
                    game_first_candidate,
                    allowed_policies=allowed_policies,
                )
                status = (yield from action_status) if isinstance(action_status, GeneratorType) else action_status
                if status == "processed":
                    processed_count += 1
                    if target_requested:
                        break
                    continue
            if action_enabled and not full_scan and not target_requested and self._pending_packet_mail_action_count(allowed_policies=allowed_policies) <= 0:
                break

            if watermark_time and crossed_watermark:
                with self._lock:
                    self._log_locked("success", f"邮件_历史扫描：已扫到早于水位 {watermark_time} 的邮件，增量段完整接回")
                break

            signature = self._mail_rows_signature(rows)
            if rows:
                empty_pages = 0
                if signature and signature == last_signature:
                    stable_same_pages += 1
                    if stable_same_pages >= max_stable_same_pages:
                        break
                else:
                    stable_same_pages = 0
                    last_signature = signature
            else:
                empty_pages += 1
                if empty_pages >= max_empty_pages:
                    break
            scroll_count += 1
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：邮件清单向下滚动 {scroll_count}",
                    phase="mail_claim_scroll_list",
                    current_scene=121,
                )
                self._log_locked("action", f"邮件_历史扫描：当前页无可处理邮件，滚动邮件清单2 {scroll_count}")
            self._scroll_shape_content(ctx, image121, list_shape)
            yield from self._wait_scroll_settle(ctx, stop_event)

        if processed_count >= max_actions and not configured_max_actions:
            raise RuntimeError(f"邮件_历史扫描：达到单轮处理上限 {max_actions}，为避免异常循环已停止")
        if configured_max_actions and processed_count >= max_actions:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：达到本轮指定处理数 {max_actions}，见到 {seen_count} 封，处理 {processed_count} 封",
                    phase="mail_claim_done",
                    current_scene=121,
                )
                self._log_locked("success", f"邮件_历史扫描：达到本轮指定处理数 {max_actions}，见到 {seen_count} 封，处理 {processed_count} 封")
            return "success"
        if target_requested and processed_count > 0:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"邮件_历史扫描：目标邮件处理完成，见到 {seen_count} 封，处理 {processed_count} 封",
                    phase="mail_claim_done",
                    current_scene=121,
                )
                self._log_locked("success", f"邮件_历史扫描：目标邮件处理完成，见到 {seen_count} 封，处理 {processed_count} 封")
            return "success"
        pending_after_scan = self._pending_packet_mail_action_count(allowed_policies=allowed_policies) if action_enabled else 0
        if action_enabled and full_scan and pending_after_scan > 0 and not scan_truncated:
            marked_count = self._mark_pending_packet_mail_actions_not_visible(
                reason=f"full_scan_seen={seen_count}; processed={processed_count}; scrolls={scroll_count}",
                allowed_policies=allowed_policies,
            )
            with self._lock:
                self._log_locked(
                    "info",
                    f"邮件_历史扫描：完整扫描未见 {marked_count} 封待处理邮件，标记为 missing_from_list",
                )
        elif action_enabled and full_scan and pending_after_scan > 0 and scan_truncated:
            with self._lock:
                self._log_locked(
                    "info",
                    f"邮件_历史扫描：本轮扫描被时间预算截断，仍有 {pending_after_scan} 封待处理邮件，不标记 missing_from_list",
                )
        if action_enabled and full_scan and packet_missing_rows:
            sample_text = "；".join(
                f"{item['title']}|{item['time_text']}|{item['reason']}"
                for item in packet_missing_rows[:8]
            )
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "packet_gap",
                    "last_scan_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_top_time": top_time,
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "packet_missing_count": len(packet_missing_rows),
                    "packet_missing_rows": packet_missing_rows,
                    "packet_missing_traces": packet_missing_traces,
                    "packet_gap_history": self._mail_packet_gap_history(scan_state, packet_missing_rows, packet_missing_traces),
                    "message": "可见邮件缺可用 packet 事实，标题+时间和标题降级均未匹配",
                }
            )
            with self._lock:
                self._log_locked(
                    "error" if fail_on_packet_gap else "info",
                    (
                        f"邮件_历史扫描：发现 {len(packet_missing_rows)} 个可见邮件缺 packet 事实，"
                        f"{'本轮不能证明已清干净' if fail_on_packet_gap else '已按游戏画面优先策略记录并继续'}：{sample_text}"
                    ),
                )
            if fail_on_packet_gap:
                raise RuntimeError(f"邮件_历史扫描：发现 {len(packet_missing_rows)} 个可见邮件缺 packet 事实，请先修复抓包/解析缺口：{sample_text}")
        if watermark_time and not crossed_watermark:
            self._write_mail_scan_state(
                {
                    **scan_state,
                    "status": "gap_risk",
                    "last_scan_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_top_time": top_time,
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "message": f"未接回增量水位 {watermark_time}",
                }
            )
            raise RuntimeError(f"邮件_历史扫描：未接回增量水位 {watermark_time}，可能存在中间遗漏，请继续遍历或用 full_scan/observe_only 补洞")
        if top_time:
            self._write_mail_scan_state(
                {
                    "packet_gap_history": scan_state.get("packet_gap_history") or [],
                    "status": "confirmed",
                    "confirmed_time_bucket": top_time,
                    "confirmed_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_scan_mode": "full" if full_scan else "incremental",
                    "last_seen_count": seen_count,
                    "last_processed_count": processed_count,
                    "previous_confirmed_time_bucket": scan_state.get("confirmed_time_bucket") or "",
                }
            )

        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：完成，见到 {seen_count} 封，处理 {processed_count} 封，packet缺失 {len(packet_missing_rows)} 封",
                phase="mail_claim_done",
                current_scene=121,
            )
            self._log_locked("success", f"邮件_历史扫描：完成，见到 {seen_count} 封，处理 {processed_count} 封，packet缺失 {len(packet_missing_rows)} 封")
        return "success"

    def _trace_mail_packet_gap(self, row: dict[str, str]) -> dict[str, Any]:
        try:
            return trace_packet_mail_gap(
                _db_engine,
                title=str(row.get("title") or ""),
                time_text=str(row.get("time_text") or ""),
                window_minutes=8,
                max_sources=24,
            )
        except Exception as exc:
            return {
                "title": str(row.get("title") or ""),
                "time_text": str(row.get("time_text") or ""),
                "diagnosis": "trace_error",
                "error": str(exc),
            }

    def _recognize_visible_mail_rows(
        self,
        ctx: dict[str, Any],
        image121: dict[str, Any],
        frame: str,
    ) -> list[dict[str, Any]]:
        started_at = time.monotonic()
        lines = self._ocr_lines_in_shapes(frame, image121, ("第1封", "邮件清单2"))
        first_rows = self._mail_rows_in_shape(lines, image121, "第1封")
        list_rows = self._mail_rows_in_shape(lines, image121, "邮件清单2")
        rows = self._merge_visible_mail_rows_by_position(first_rows, list_rows)
        self._annotate_mail_rows_list_state(ctx, image121, frame, rows)
        elapsed = time.monotonic() - started_at
        self._log("detail", f"邮件_历史扫描：当前页 OCR+行解析耗时 {elapsed:.1f}s，识别 {len(rows)} 行")
        return rows

    def _read_mail_scan_state(self) -> dict[str, Any]:
        payload = _read_data_annotation_json(_data_annotation_mail_scan_state_path(), {})
        return payload if isinstance(payload, dict) else {}

    def _write_mail_scan_state(self, payload: dict[str, Any]) -> None:
        _write_data_annotation_json(_data_annotation_mail_scan_state_path(), payload)

    def _mail_packet_gap_history(
        self,
        scan_state: dict[str, Any],
        rows: list[dict[str, str]],
        traces: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        history = [item for item in scan_state.get("packet_gap_history") or [] if isinstance(item, dict)]
        history.append(
            {
                "recorded_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                "rows": rows,
                "traces": traces,
            }
        )
        return history[-20:]

    def _pending_packet_mail_action_count(self, *, allowed_policies: set[str] | None = None) -> int:
        policies = (set(allowed_policies or {"claim"}) & {"claim"}) or {"claim"}
        records = pending_packet_mail_records(_db_engine)
        groups: dict[tuple[str, str], list[Any]] = {}
        for record in records:
            key = (str(record.normalized_title or record.title or "").strip(), str(record.create_time_text or "").strip())
            if key[0] and key[1]:
                groups.setdefault(key, []).append(record)
        return sum(
            1
            for group in groups.values()
            if any(fanxiu_mail_action_policy_for_record(record) in policies for record in group)
            and fanxiu_mail_visible_group_action_policy(group) in policies
        )

    def _mark_pending_packet_mail_actions_not_visible(self, *, reason: str, allowed_policies: set[str] | None = None) -> int:
        now_text = _now().strftime("%Y-%m-%d %H:%M:%S")
        marked = 0
        policies = (set(allowed_policies or {"claim"}) & {"claim"}) or {"claim"}
        records = pending_packet_mail_action_candidates(_db_engine, policies)
        for record in records:
            if fanxiu_mail_action_policy_for_record(record) not in policies:
                continue
            group = self._find_packet_mail_records_for_visible_row(str(record.normalized_title or record.title or ""), str(record.create_time_text or ""))
            if fanxiu_mail_visible_group_action_policy(group) not in policies:
                continue
            mark_packet_mail_record_missing_from_list(_db_engine, record, reason=reason, marked_at=now_text)
            marked += 1
        return marked

    def _mail_time_is_older_than(self, current_time_text: str, watermark_time_text: str) -> bool:
        current = parse_data_annotation_task_time(normalize_fanxiu_mail_time_text(current_time_text))
        watermark = parse_data_annotation_task_time(normalize_fanxiu_mail_time_text(watermark_time_text))
        return current is not None and watermark is not None and current < watermark

    def _prepare_and_maybe_process_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
        *,
        action_enabled: bool = True,
        action_policies: set[str] | None = None,
        target_title: str = "",
        target_time_text: str = "",
    ) -> str:
        self._prepare_mail_row_policy(row, action_enabled=action_enabled, action_policies=action_policies)
        if (target_title or target_time_text) and not self._mail_row_matches_target(row, target_title=target_title, target_time_text=target_time_text):
            row["policy"] = ""
        if not row.get("policy"):
            return "seen"
        return (yield from self._process_mail_row(ctx, stop_event, image121, row))

    def _mail_row_matches_target(self, row: dict[str, Any], *, target_title: str, target_time_text: str) -> bool:
        if target_title:
            observed_title = str(row.get("title") or "").strip()
            if self._mail_title_similarity(observed_title, target_title) < 0.86:
                return False
        if target_time_text:
            observed_time = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            if observed_time != self._normalize_mail_time_text(target_time_text):
                return False
        return True

    def _prepare_mail_row_policy(
        self,
        row: dict[str, Any],
        *,
        action_enabled: bool = True,
        action_policies: set[str] | None = None,
    ) -> None:
        title = str(row.get("title") or "").strip()
        time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
        row["policy"] = ""
        ui_status = self._normalize_mail_row_status(str(row.get("status") or ""))
        if not ui_status and bool(row.get("is_read")):
            ui_status = "已阅"
        if bool(row.get("list_has_lock")):
            ui_status = "锁定"
        row["status"] = ui_status or "无"
        if ui_status in {"已阅", "锁定"}:
            row["mail_key"] = ""
            row["policy"] = ""
            row["packet_match"] = "ui_skipped"
            row["packet_missing_reason"] = ""
            return
        if not self._is_valid_mail_time_text(time_text):
            row["time_text"] = ""
            row["mail_key"] = ""
            return
        row["time_text"] = time_text
        records = self._find_packet_mail_records_for_visible_row(title, time_text)
        record = records[0] if records else None
        row["mail_key"] = str(record.mail_key or "") if record else ""
        if records:
            row["packet_match"] = "matched" if any(self._mail_record_matches_visible_time(record, time_text) for record in records) else "title_only"
            row["packet_missing_reason"] = ""
        else:
            row["packet_match"] = "missing"
            row["packet_missing_reason"] = self._mail_row_packet_missing_reason(title, time_text)
        if action_enabled:
            policy = self._mail_row_packet_action_policy(title, time_text)
            allowed_policies = (set(action_policies or {"claim"}) & {"claim"}) or {"claim"}
            row["policy"] = policy if policy in allowed_policies else ""

    def _mail_row_packet_action_policy(self, title: str, time_text: str) -> str:
        records = self._find_packet_mail_records_for_visible_row(title, time_text)
        return self._visible_packet_mail_group_action_policy(records, time_text=time_text)

    def _mail_row_packet_missing_reason(self, title: str, time_text: str) -> str:
        normalized_title = normalize_fanxiu_mail_title(title)
        normalized_time = normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return "invalid_title_or_time"
        same_title = packet_mail_records_same_title(_db_engine, normalized_title, limit=5)
        same_time = packet_mail_records_same_time(_db_engine, normalized_time, limit=5)
        if same_title:
            times = ",".join(str(record.create_time_text or "") for record in same_title[:3])
            return f"same_title_without_time:{times}"
        if same_time:
            titles = ",".join(str(record.title or record.normalized_title or "") for record in same_time[:3])
            return f"same_time_without_title:{titles}"
        return "no_packet_fact"

    def _visible_packet_mail_group_action_policy(self, records: list[Any], *, time_text: str = "") -> str:
        if not records:
            return ""
        has_visible_time_match = bool(time_text) and any(self._mail_record_matches_visible_time(record, time_text) for record in records)
        for record in records:
            if has_visible_time_match:
                claimable = fanxiu_mail_desired_status_for_record(record) == "可领"
            else:
                claimable = self._packet_mail_record_initially_claimable(record)
            if not claimable:
                return ""
        return "claim"

    def _packet_mail_record_initially_claimable(self, record: Any | None) -> bool:
        if record is None:
            return False
        if fanxiu_mail_rewards_unresolved(getattr(record, "payload", None)):
            return False
        return fanxiu_mail_desired_status_for_rewards(fanxiu_mail_rewards_from_payload(getattr(record, "payload", None))) == "可领"

    def _find_packet_mail_records_for_visible_row(self, title: str, time_text: str) -> list[Any]:
        normalized_title = normalize_fanxiu_mail_title(title)
        normalized_time = normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return []
        exact = packet_mail_records_for_visible_row_exact(_db_engine, normalized_title=normalized_title, normalized_time=normalized_time)
        if exact:
            return list(exact)
        same_time = packet_mail_records_for_visible_row_same_time(_db_engine, normalized_time)
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 3:
            return self._find_packet_mail_records_by_title_only(title)
        scored: list[tuple[float, Any]] = []
        for record in same_time:
            score = self._mail_title_similarity(title, str(record.title or record.normalized_title or ""))
            if score > 0:
                scored.append((score, record))
        if not scored:
            return self._find_packet_mail_records_by_title_only(title)
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score = scored[0][0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold:
            return self._find_packet_mail_records_by_title_only(title)
        fuzzy_matches = [record for score, record in scored if score >= best_score - 0.06]
        if fuzzy_matches:
            return fuzzy_matches
        return self._find_packet_mail_records_by_title_only(title)

    def _find_packet_mail_records_by_title_only(self, title: str) -> list[Any]:
        normalized_title = normalize_fanxiu_mail_title(title)
        if not normalized_title:
            return []
        exact = packet_mail_records_by_normalized_title(_db_engine, normalized_title, limit=20)
        if exact:
            return list(exact)
        observed_key = self._mail_title_similarity_key(title)
        if len(observed_key) < 5:
            return []
        recent = recent_packet_mail_records(_db_engine, limit=200)
        scored: list[tuple[float, Any]] = []
        for record in recent:
            score = self._mail_title_similarity(title, str(record.title or record.normalized_title or ""))
            if score >= 0.86:
                scored.append((score, record))
        if not scored:
            return []
        scored.sort(key=lambda item: (item[0], float(item[1].last_seen_at or item[1].updated_at or 0)), reverse=True)
        best_score = scored[0][0]
        return [record for score, record in scored if score >= best_score - 0.03]

    def _find_packet_mail_record(
        self,
        title: str,
        time_text: str,
        *,
        action_policies: set[str] | None = None,
    ) -> Any | None:
        normalized_title = normalize_fanxiu_mail_title(title)
        normalized_time = normalize_fanxiu_mail_time_text(time_text)
        if not normalized_title or not normalized_time:
            return None
        record = find_packet_mail_record_exact(_db_engine, normalized_title=normalized_title, normalized_time=normalized_time)
        if record:
            return record
        record = find_packet_mail_record_by_raw_title(_db_engine, title=title, normalized_time=normalized_time)
        if record:
            return record
        time_candidates = packet_mail_records_for_visible_row_same_time(_db_engine, normalized_time)
        fuzzy = self._select_packet_mail_record_by_fuzzy_title(
            title,
            time_candidates,
            action_policies=action_policies,
        )
        if fuzzy:
            return fuzzy
        return None

    def _visible_packet_mail_action_policy(self, record: Any | None) -> str:
        if record is None:
            return ""
        desired_status = fanxiu_mail_desired_status_for_record(record)
        if desired_status != "可领":
            return ""
        status = str(record.status or "").strip().lower()
        if status in {"claimed", "deleted"}:
            return ""
        if status in {"claim_requested", "delete_requested"}:
            retry_policy = status.removesuffix("_requested")
            if not self._mail_requested_action_retryable(record, retry_policy):
                return ""
            return retry_policy
        if fanxiu_mail_rewards_unresolved(record.payload):
            return ""
        return fanxiu_mail_action_policy_for_rewards(fanxiu_mail_rewards_from_payload(record.payload))

    def _mail_requested_action_retryable(self, record: Any, policy: str) -> bool:
        if policy not in {"claim", "delete"}:
            return False
        evidence = record.evidence if isinstance(record.evidence, dict) else {}
        requested_action = str(evidence.get("runtime_requested_action") or "").strip().lower()
        if requested_action and requested_action != policy:
            return False
        requested_at = str(evidence.get("runtime_action_requested_at") or "").strip()
        try:
            requested_ts = datetime.strptime(requested_at, "%Y-%m-%d %H:%M:%S").timestamp() if requested_at else 0.0
        except ValueError:
            requested_ts = 0.0
        if requested_ts and time.time() - requested_ts < 60.0:
            return False
        server_protocol = "SM_GetMailReward" if policy == "claim" else "SM_DeleteMail"
        for event in evidence.get("mail_actions") or []:
            if isinstance(event, dict) and str(event.get("protocol") or "") == server_protocol:
                return False
        return True

    def _mail_row_has_attachment_hint(self, row: dict[str, Any]) -> bool:
        return bool(row.get("list_has_lock") or row.get("has_attachment_hint"))

    def _select_packet_mail_record_by_fuzzy_title(
        self,
        observed_title: str,
        candidates: list[Any],
        *,
        action_policies: set[str] | None = None,
    ) -> Any | None:
        observed_key = self._mail_title_similarity_key(observed_title)
        if len(observed_key) < 3:
            return None
        scored: list[tuple[float, Any, str]] = []
        for candidate in candidates:
            candidate_title = str(candidate.title or candidate.normalized_title or "")
            score = self._mail_title_similarity(observed_title, candidate_title)
            if score <= 0:
                continue
            scored.append((score, candidate, self._visible_packet_mail_action_policy(candidate)))
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], float(item[1].last_seen_at or item[1].updated_at or 0)), reverse=True)
        best_score, best_record, best_policy = scored[0]
        threshold = 0.58 if len(observed_key) >= 5 else 0.72
        if best_score < threshold:
            return None
        if action_policies is not None:
            allowed_policies = (set(action_policies or set()) & {"claim", "delete"}) or {"claim", "delete"}
            if best_policy not in allowed_policies:
                return None
            close_candidates = [item for item in scored if item[0] >= best_score - 0.06]
            close_policies = {policy for _, _, policy in close_candidates}
            if close_policies != {best_policy}:
                return None
            return best_record
        close_candidates = [item for item in scored if item[0] >= best_score - 0.06]
        close_titles = {self._mail_title_similarity_key(str(record.title or record.normalized_title or "")) for _, record, _ in close_candidates}
        if len(close_titles) > 1:
            return None
        return best_record

    def _mail_title_similarity_key(self, value: str) -> str:
        text = normalize_fanxiu_mail_title(value)
        return re.sub(r"[^\u4e00-\u9fff0-9A-Za-z]", "", text)

    def _mail_title_similarity(self, left: str, right: str) -> float:
        left_key = self._mail_title_similarity_key(left)
        right_key = self._mail_title_similarity_key(right)
        if not left_key or not right_key:
            return 0.0
        if left_key == right_key:
            return 1.0
        base = difflib.SequenceMatcher(None, left_key, right_key).ratio()
        shorter, longer = sorted((left_key, right_key), key=len)
        if len(shorter) >= 3 and shorter in longer:
            base = max(base, len(shorter) / max(len(longer), 1))
        return float(base)

    def _mail_record_matches_visible_time(self, record: Any, time_text: str) -> bool:
        return normalize_fanxiu_mail_time_text(str(record.create_time_text or "")) == normalize_fanxiu_mail_time_text(time_text)

    def _find_packet_mail_key(self, title: str, time_text: str) -> str:
        record = self._find_packet_mail_record(title, time_text)
        return str(record.mail_key or "") if record else ""

    def _update_packet_mail_action_for_row(self, row: dict[str, Any], *, status: str, evidence: dict[str, Any]) -> None:
        mail_key = str(row.get("mail_key") or "").strip()
        if not mail_key:
            mail_key = self._find_packet_mail_key(
                str(row.get("title") or ""),
                str(row.get("time_text") or ""),
            )
        if not mail_key:
            return
        update_packet_mail_action(_db_engine, mail_key=mail_key, status=status, evidence=evidence)

    def _process_mail_row_by_detail(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
        *,
        allowed_policies: set[str],
    ) -> str:
        title = str(row.get("title") or "")
        if not title:
            return "seen"
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：打开「{title}」按详情页判断",
                phase="mail_claim_open_game_first",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_历史扫描：缺 packet，打开「{title}」按详情页判断")
        self._click_frame_point(ctx, image121, float(row.get("x") or 0), float(row.get("y") or 0))
        scene_result = self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_历史扫描：等待「{title}」详情")
        scene_id, _score = yield from scene_result
        if scene_id == 121:
            return "seen"
        if scene_id not in {122, 123}:
            raise RuntimeError(f"邮件_历史扫描：打开「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        actual_policy = "claim" if scene_id == 122 else "delete"
        if actual_policy not in allowed_policies:
            with self._lock:
                self._log_locked("detail", f"邮件_历史扫描：「{title}」详情为 {actual_policy}，不在本轮允许动作内，返回列表")
            yield from self._return_mail_detail_to_list(ctx, stop_event, scene_id)
            return "seen"
        detail_image = ctx.get("images", {}).get(scene_id)
        action_shape = self._find_shape(detail_image, "领取" if actual_policy == "claim" else "删除") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not action_shape:
            raise RuntimeError(f"缺少 #{scene_id}「{'领取' if actual_policy == 'claim' else '删除'}」标注，无法处理邮件")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：按详情页{('领取' if actual_policy == 'claim' else '删除')}「{title}」",
                phase=f"mail_claim_do_{actual_policy}",
                current_scene=scene_id,
            )
            self._log_locked("action", f"邮件_历史扫描：详情页确认 #{scene_id}，点击「{'领取' if actual_policy == 'claim' else '删除'}」：{title}")
        match_result = yield from self._wait_shape_match(
            ctx,
            stop_event,
            detail_image,
            action_shape,
            timeout=8.0,
            label=f"邮件_历史扫描：等待「{title}」{'领取' if actual_policy == 'claim' else '删除'}按钮",
        )
        frame, action_match = match_result
        self._click_shape(ctx, detail_image, action_shape, frame, match_result=action_match)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_历史扫描：返回邮件 #121")
        self._update_packet_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={
                "runtime_requested_action": actual_policy,
                "runtime_action_requested_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                "runtime_action_source": "game_first_detail",
            },
        )
        return "processed"

    def _process_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        title = str(row.get("title") or "")
        policy = str(row.get("policy") or "")
        if policy not in {"claim", "delete"}:
            return "seen"
        with self._lock:
            action_label = "处理"
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：打开「{title}」准备{action_label}",
                phase=f"mail_claim_open_{policy}",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_历史扫描：打开「{title}」准备{action_label}")
        self._click_frame_point(ctx, image121, float(row.get("x") or 0), float(row.get("y") or 0))
        scene_result = self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_历史扫描：等待「{title}」详情")
        scene_id, _score = yield from scene_result
        if scene_id == 121:
            return "seen"
        if scene_id not in {122, 123}:
            raise RuntimeError(f"邮件_历史扫描：打开「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        target_scene_id = scene_id
        actual_policy = "claim" if target_scene_id == 122 else "delete"
        if actual_policy != policy:
            with self._lock:
                self._log_locked(
                    "detail",
                    f"邮件_历史扫描：「{title}」列表策略={policy}，详情实际为 #{target_scene_id} {actual_policy}，按详情按钮处理",
                )
        detail_image = ctx.get("images", {}).get(target_scene_id)
        action_shape = self._find_shape(detail_image, "领取" if actual_policy == "claim" else "删除") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not action_shape:
            raise RuntimeError(f"缺少 #{target_scene_id}「{'领取' if actual_policy == 'claim' else '删除'}」标注，无法处理邮件")
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：{('领取' if actual_policy == 'claim' else '删除')}「{title}」",
                phase=f"mail_claim_do_{actual_policy}",
                current_scene=target_scene_id,
            )
            self._log_locked("action", f"邮件_历史扫描：等待并点击 #{target_scene_id}「{'领取' if actual_policy == 'claim' else '删除'}」")
        match_result = yield from self._wait_shape_match(
            ctx,
            stop_event,
            detail_image,
            action_shape,
            timeout=8.0,
            label=f"邮件_历史扫描：等待「{title}」{'领取' if actual_policy == 'claim' else '删除'}按钮",
        )
        frame, action_match = match_result
        self._click_shape(ctx, detail_image, action_shape, frame, match_result=action_match)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_历史扫描：返回邮件 #121")
        self._update_packet_mail_action_for_row(
            row,
            status=f"{actual_policy}_requested",
            evidence={"runtime_requested_action": actual_policy, "runtime_action_requested_at": _now().strftime("%Y-%m-%d %H:%M:%S")},
        )
        return "processed"

    def _probe_and_maybe_delete_mail_row(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image121: dict[str, Any],
        row: dict[str, Any],
    ) -> str:
        if self._mail_row_has_attachment_hint(row):
            return "seen"
        title = str(row.get("title") or "")
        if not title:
            return "seen"
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：探测「{title}」是否可删除",
                phase="mail_claim_probe_delete",
                current_scene=121,
            )
            self._log_locked("action", f"邮件_历史扫描：打开「{title}」探测无附件删除")
        self._click_frame_point(ctx, image121, float(row.get("x") or 0), float(row.get("y") or 0))
        scene_id, score = yield from self._wait_mail_detail_or_list_scene(ctx, stop_event, timeout=12.0, label=f"邮件_历史扫描：探测「{title}」详情")
        if scene_id == 121:
            return "seen"
        if scene_id == 122:
            with self._lock:
                self._log_locked("detail", f"邮件_历史扫描：探测「{title}」进入 #122，视为有附件/可领取，返回不处理")
            yield from self._return_mail_detail_to_list(ctx, stop_event, 122)
            return "seen"
        if scene_id != 123:
            raise RuntimeError(f"邮件_历史扫描：探测「{title}」进入未知详情 #{scene_id}，为避免误操作已停止")
        detail_image = ctx.get("images", {}).get(123)
        action_shape = self._find_shape(detail_image, "删除") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not action_shape:
            raise RuntimeError("缺少 #123「删除」标注，无法执行无附件邮件删除")
        frame = self._screencap(ctx)
        with self._lock:
            self._set_status_locked(
                "running",
                f"邮件_历史扫描：UI确认无附件，删除「{title}」",
                phase="mail_claim_do_delete",
                current_scene=123,
            )
            self._log_locked("action", f"邮件_历史扫描：UI确认 #123，点击「删除」：{title}")
        self._click_shape(ctx, detail_image, action_shape, frame)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_历史扫描：返回邮件 #121")
        self._update_packet_mail_action_for_row(
            row,
            status="delete_requested",
            evidence={
                "runtime_requested_action": "delete",
                "runtime_action_requested_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
                "runtime_action_source": "ui_delete_probe",
            },
        )
        return "processed"

    def _return_mail_detail_to_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        scene_id: int,
    ):
        detail_image = ctx.get("images", {}).get(scene_id)
        back_shape = self._find_shape(detail_image, "空白-返回") if isinstance(detail_image, dict) else None
        if not isinstance(detail_image, dict) or not back_shape:
            raise RuntimeError(f"缺少 #{scene_id}「空白-返回」标注，无法从邮件详情返回")
        frame = self._screencap(ctx)
        self._click_shape(ctx, detail_image, back_shape, frame)
        yield from self._wait_mail_list_ready(ctx, stop_event, timeout=18.0, label="邮件_历史扫描：返回邮件 #121")

    def _wait_mail_detail_or_list_scene(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str,
    ):
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        candidates = [121, 122, 123]
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, candidates)
            last_scene_id, last_score = scene_id, score
            if scene_id in candidates:
                with self._lock:
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
                self._log("success", f"{label}：识别到 #{scene_id} {score:.0f}%")
                return scene_id, score
            if self._close_mail_wait_popup_once(ctx, frame):
                yield BehaviorTreeStatus.RUNNING
                continue
            with self._lock:
                self._status.update({
                    "phase": "wait_mail_detail",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if time.monotonic() - start >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到邮件详情，最后 {scene_text} {last_score:.0f}%")

    def _mail_rows_in_shape(self, lines: list[dict[str, Any]], image: dict[str, Any], shape_title: str) -> list[dict[str, Any]]:
        shape = self._find_shape(image, shape_title)
        if not shape:
            return []
        template_shape = self._find_shape(image, "邮件模板")
        template_children = self._flatten_shapes(template_shape.get("children")) if isinstance(template_shape, dict) else []
        title_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "标题"), None)
        time_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "时间"), None)
        status_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "状态"), None)
        if template_shape and title_shape and time_shape:
            rows = self._mail_rows_in_shape_by_template(lines, image, shape, title_shape, time_shape, status_shape)
            if rows:
                return rows
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        candidates: list[dict[str, Any]] = []
        date_lines: list[dict[str, Any]] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if not (left <= cx <= right and top <= cy <= bottom):
                continue
            if self._looks_like_mail_time(text):
                date_lines.append({"text": text, "x": cx, "y": cy, "is_read": "已阅" in text or "已读" in text})
                continue
            if not self._looks_like_mail_title(text):
                continue
            title = re.sub(r"[0-9A-Za-z]+$", "", normalize_fanxiu_mail_title(text)).strip()
            if not title or not self._looks_like_mail_title(title):
                continue
            candidates.append({"title": title, "x": cx, "y": cy, "raw_text": text})
        rows: list[dict[str, Any]] = []
        for item in candidates:
            title = str(item.get("title") or "")
            if not title:
                continue
            below_dates = [line for line in date_lines if float(line["y"]) >= float(item["y"]) and float(line["y"]) - float(item["y"]) < 80]
            if below_dates:
                raw_time_text = str(below_dates[0].get("text") or "")
                item["time_text"] = self._normalize_mail_time_text(raw_time_text)
                item["raw_time_text"] = raw_time_text
                item["is_read"] = bool(below_dates[0].get("is_read"))
                item["status"] = "已阅" if item["is_read"] else "无"
            else:
                item["status"] = "无"
            rows.append(item)
        return rows

    def _mail_template_child_shape(self, image: dict[str, Any], title: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        template_shape = self._find_shape(image, "邮件模板")
        template_children = self._flatten_shapes(template_shape.get("children")) if isinstance(template_shape, dict) else []
        title_shape = next((item for item in template_children if str(item.get("title") or "").strip() == "标题"), None)
        child_shape = next((item for item in template_children if str(item.get("title") or "").strip() == title), None)
        if not (isinstance(template_shape, dict) and isinstance(title_shape, dict) and isinstance(child_shape, dict)):
            return None
        return template_shape, title_shape, child_shape

    def _mail_row_template_child_shape(self, image: dict[str, Any], row: dict[str, Any], title: str) -> dict[str, Any] | None:
        found = self._mail_template_child_shape(image, title)
        if found is None:
            return None
        _template_shape, title_shape, child_shape = found
        _width, height = self._frame_size(image)
        try:
            row_title_center_y = float(row.get("y") or 0) / max(1, height)
        except (TypeError, ValueError):
            return None
        title_center_y = float(title_shape.get("y") or 0) + float(title_shape.get("h") or 0) / 2
        child_center_y = float(child_shape.get("y") or 0) + float(child_shape.get("h") or 0) / 2
        adjusted = dict(child_shape)
        adjusted["y"] = max(0.0, min(1.0, row_title_center_y + (child_center_y - title_center_y) - float(child_shape.get("h") or 0) / 2))
        adjusted["title"] = str(child_shape.get("title") or title)
        adjusted["imageMatchRole"] = "required"
        adjusted["ocrMatchRole"] = "off"
        return adjusted

    def _annotate_mail_rows_list_state(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        rows: list[dict[str, Any]],
    ) -> None:
        del ctx, image, frame_data_url
        for row in rows:
            row.setdefault("list_has_lock", False)
            row.setdefault("has_attachment_hint", False)

    def _mail_rows_in_shape_by_template(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any],
        list_shape: dict[str, Any],
        title_shape: dict[str, Any],
        time_shape: dict[str, Any],
        status_shape: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        list_box = self._box(list_shape, image)
        title_box = self._box(title_shape, image)
        time_box = self._box(time_shape, image)
        status_box = self._box(status_shape, image) if isinstance(status_shape, dict) else None
        list_left = float(list_box.get("x") or 0)
        list_top = float(list_box.get("y") or 0)
        list_right = list_left + float(list_box.get("w") or 0)
        list_bottom = list_top + float(list_box.get("h") or 0)
        title_left = float(title_box.get("x") or 0)
        title_right = title_left + float(title_box.get("w") or 0)
        title_height = float(title_box.get("h") or 0)
        title_center_y = float(title_box.get("y") or 0) + title_height / 2
        time_center_y = float(time_box.get("y") or 0) + float(time_box.get("h") or 0) / 2
        title_offset_y = title_center_y - time_center_y
        status_offset_y = 0.0
        status_left = status_right = 0.0
        if status_box:
            status_left = float(status_box.get("x") or 0)
            status_right = status_left + float(status_box.get("w") or 0)
            status_center_y = float(status_box.get("y") or 0) + float(status_box.get("h") or 0) / 2
            status_offset_y = status_center_y - title_center_y
        title_y_tolerance = max(18.0, title_height * 1.25)
        normalized_lines: list[dict[str, Any]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if not (list_left <= cx <= list_right and list_top <= cy <= list_bottom):
                continue
            normalized_lines.append({"text": text, "x": cx, "y": cy, "w": w, "h": h})
        time_lines = [
            line for line in normalized_lines
            if self._looks_like_mail_time(str(line.get("text") or ""))
        ]
        rows: list[dict[str, Any]] = []
        for time_line in sorted(time_lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            raw_time_text = str(time_line.get("text") or "")
            time_text = self._normalize_mail_time_text(raw_time_text)
            if not self._is_valid_mail_time_text(time_text):
                continue
            expected_title_y = float(time_line.get("y") or 0) + title_offset_y
            candidates: list[dict[str, Any]] = []
            for line in normalized_lines:
                text = str(line.get("text") or "")
                if text == raw_time_text:
                    continue
                if self._looks_like_mail_time(text):
                    continue
                cx = float(line.get("x") or 0)
                cy = float(line.get("y") or 0)
                if not (title_left <= cx <= title_right):
                    continue
                if abs(cy - expected_title_y) > title_y_tolerance:
                    continue
                if not self._looks_like_mail_title(text):
                    continue
                title = normalize_fanxiu_mail_title(text).strip()
                if not title or not self._looks_like_mail_title(title):
                    continue
                candidates.append({"title": title, "x": cx, "y": cy, "raw_text": text})
            if not candidates:
                continue
            best = max(candidates, key=lambda item: len(str(item.get("title") or "")))
            status = "已阅" if self._normalize_mail_row_status(raw_time_text) == "已阅" else "无"
            raw_status_text = ""
            if status_box:
                expected_status_y = float(best.get("y") or 0) + status_offset_y
                status_candidates: list[dict[str, Any]] = []
                for line in normalized_lines:
                    text = str(line.get("text") or "")
                    if text == raw_time_text or text == str(best.get("raw_text") or ""):
                        continue
                    cx = float(line.get("x") or 0)
                    cy = float(line.get("y") or 0)
                    if not (status_left <= cx <= status_right):
                        continue
                    if abs(cy - expected_status_y) > title_y_tolerance:
                        continue
                    normalized_status = self._normalize_mail_row_status(text)
                    if normalized_status:
                        status_candidates.append({"status": normalized_status, "raw_text": text})
                if status_candidates:
                    chosen = status_candidates[0]
                    status = str(chosen["status"])
                    raw_status_text = str(chosen["raw_text"])
            row = {
                **best,
                "time_text": time_text,
                "raw_time_text": raw_time_text,
                "is_read": status == "已阅",
                "status": status,
                "raw_status_text": raw_status_text,
            }
            if status == "锁定":
                row["list_has_lock"] = True
            rows.append(row)
        return rows

    def _normalize_mail_time_text(self, text: str) -> str:
        return normalize_fanxiu_mail_time_text(_sanitize_ocr_text(text))

    def _is_valid_mail_time_text(self, text: str) -> bool:
        return bool(normalize_fanxiu_mail_time_text(text))

    def _looks_like_mail_time(self, text: str) -> bool:
        return bool(re.search(r"\d{4}年|\d{1,2}月\d{1,2}(?:日)?|\d{1,2}:\d{2}", text))

    def _normalize_mail_row_status(self, text: str) -> str:
        normalized = _sanitize_ocr_text(text).replace(" ", "")
        if not normalized:
            return ""
        if "锁定" in normalized:
            return "锁定"
        if "已阅" in normalized or "已读" in normalized:
            return "已阅"
        compact = re.sub(r"[^\u4e00-\u9fff]", "", normalized)
        if not compact:
            return ""
        if len(compact) < 2:
            return ""
        if len(compact) == 2 and compact.startswith("锁"):
            return "锁定"
        if len(compact) <= 3 and compact.startswith("已"):
            return "已阅"
        for target in ("锁定", "已阅", "已读"):
            if difflib.SequenceMatcher(None, compact, target).ratio() >= 0.5:
                return "已阅" if target == "已读" else target
        return ""

    def _looks_like_mail_title(self, text: str) -> bool:
        normalized = normalize_fanxiu_mail_title(text)
        if len(normalized) < 2:
            return False
        if any(token in normalized for token in ("邮件", "已锁定", "一键删除", "一键领取", "年月日", "已阅", "未阅", "已读")):
            return False
        if self._looks_like_mail_time(normalized):
            return False
        return bool(re.search(r"[\u4e00-\u9fff]", normalized))

    def _mail_rows_signature(self, rows: list[dict[str, Any]]) -> str:
        return "|".join(f"{row.get('title') or ''}@{row.get('time_text') or ''}" for row in rows[:4])

    def _merge_visible_mail_rows_by_position(
        self,
        first_rows: list[dict[str, Any]],
        list_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for row in sorted([*first_rows, *list_rows], key=lambda item: float(item.get("y") or 0)):
            title = normalize_fanxiu_mail_title(str(row.get("title") or ""))
            time_text = self._normalize_mail_time_text(str(row.get("time_text") or ""))
            y_bucket = int(round(float(row.get("y") or 0) / 16.0))
            key = (title, time_text, y_bucket)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        return merged

    def _execute_daily_signup_task(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        frame = self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame, [23, 24, 69])
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
        if scene_id == 23:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：当前已在报名 #23 {score:.0f}%",
                    phase="daily_signup_resume_signup_scene",
                    current_scene=23,
                )
                self._log_locked("info", f"日常_报名：当前已在报名 #23 {score:.0f}%，直接处理报名列")
            list_result = self._execute_daily_signup_signup_list(ctx, stop_event)
            return (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        if scene_id == 24:
            image24 = ctx.get("images", {}).get(24)
            claim_shape = self._find_shape(image24, "领取") if isinstance(image24, dict) else None
            if not isinstance(image24, dict) or not claim_shape:
                raise RuntimeError("当前在 #24，但缺少 #24「领取」标注，无法续跑日常报名")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：当前已在领取 #24 {score:.0f}%",
                    phase="daily_signup_resume_claim_scene",
                    current_scene=24,
                )
                self._log_locked("action", "日常_报名：当前已在 #24，点击「领取」后返回报名列")
            self._click_shape(ctx, image24, claim_shape, frame)
            yield from self._wait_scene_id(ctx, stop_event, 23, timeout=12.0, label="日常_报名：返回报名 #23")
            list_result = self._execute_daily_signup_signup_list(ctx, stop_event)
            return (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        if scene_id == 69:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：当前已在日常 #69 {score:.0f}%",
                    phase="daily_signup_check_signup",
                    current_scene=69,
                )
                self._log_locked("info", f"日常_报名：当前已在日常 #69 {score:.0f}%，直接检查活动报名")
            entry_result = self._execute_daily_signup_activity_entry(ctx, stop_event)
            entry_status = (yield from entry_result) if isinstance(entry_result, GeneratorType) else entry_result
            return entry_status or "success"
        with self._lock:
            self._set_status_locked("running", "日常_报名：确认日常 #69", phase="daily_signup_go_scene")
            self._log_locked("action", "日常_报名：先确认进入日常 #69")
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_报名资产树路径，无法执行场景确认")
        go_scene_result = self._go_scene_task(ctx, asset_tree_path, 69, stop_event)
        result = (yield from go_scene_result) if isinstance(go_scene_result, GeneratorType) else go_scene_result
        if result == "success":
            with self._lock:
                self._set_status_locked("running", "日常_报名：检查活动报名", phase="daily_signup_check_signup")
                self._log_locked("success", "日常_报名：已到达日常 #69")
            entry_result = self._execute_daily_signup_activity_entry(ctx, stop_event)
            entry_status = (yield from entry_result) if isinstance(entry_result, GeneratorType) else entry_result
            if entry_status != "success":
                return entry_status or "success"
        return result

    def _execute_daily_signup_activity_entry(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        self._raise_if_stopped(stop_event)
        image = ctx.get("images", {}).get(75)
        if not isinstance(image, dict):
            raise RuntimeError("缺少 #75 活动报名帧标注，无法检查日常报名入口")
        claim_shape = self._find_shape(image, "活动报名-领取")
        click_shape = self._find_shape(image, "活动报名")
        if not claim_shape:
            raise RuntimeError("缺少 #75「活动报名-领取」标注，无法识别领取状态")
        if not click_shape:
            raise RuntimeError("缺少 #75「活动报名」标注，无法点击活动报名入口")
        frame = self._screencap(ctx)
        result = self._run_match(ctx, image, claim_shape, frame, match_strategy="auto", ocr_enabled=True)
        similarity = float(result.get("similarity") or 0)
        matched = bool(result.get("matches")) or similarity >= float(self.scene_threshold)
        if not matched:
            with self._lock:
                self._set_status_locked("running", "日常_报名：今日无需报名", phase="daily_signup_done")
                self._log_locked("success", "日常_报名：未识别到「领」，今日报名已完成")
            return "success"
        with self._lock:
            self._set_status_locked("running", "日常_报名：点击活动报名", phase="daily_signup_click_signup")
            self._log_locked("action", "日常_报名：识别到「领」，点击活动报名")
        self._click_shape(ctx, image, click_shape, frame)
        scene_id, score = yield from self._wait_scene_id(ctx, stop_event, 23, timeout=12.0, label="日常_报名：等待报名 #23")
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_报名：已进入报名 #23 {score:.0f}%",
                phase="daily_signup_signup_scene_ready",
                current_scene=scene_id,
            )
            self._log_locked("success", f"日常_报名：已确认报名 #23 {score:.0f}%")
        list_result = self._execute_daily_signup_signup_list(ctx, stop_event)
        list_status = (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        return list_status or "success"

    def _execute_daily_signup_signup_list(self, ctx: dict[str, Any], stop_event: threading.Event) -> str:
        image23 = ctx.get("images", {}).get(23)
        image24 = ctx.get("images", {}).get(24)
        if not isinstance(image23, dict):
            raise RuntimeError("缺少 #23 报名帧标注，无法处理报名列")
        if not isinstance(image24, dict):
            raise RuntimeError("缺少 #24 报名领取帧标注，无法领取报名奖励")
        column_shape = self._find_shape(image23, "报名列")
        back_shape = self._find_shape(image23, "返回")
        claim_shape = self._find_shape(image24, "领取")
        if not column_shape:
            raise RuntimeError("缺少 #23「报名列」标注，无法识别可报名项目")
        if not back_shape:
            raise RuntimeError("缺少 #23「返回」标注，无法返回日常 #69")
        if not claim_shape:
            raise RuntimeError("缺少 #24「领取」标注，无法领取报名奖励")

        clicked_count = 0
        empty_scroll_count = 0
        last_empty_scroll_signature = ""
        max_clicks = 30
        max_empty_scrolls = 8
        while clicked_count < max_clicks:
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：扫描报名列，已领取 {clicked_count}",
                    phase="daily_signup_scan_signup_list",
                    current_scene=23,
                )
            frame = self._screencap(ctx)
            lines = self._ocr_lines(frame)
            matches = self._ocr_row_clicks_in_shape(lines, image23, "报名列", include=("报名",), exclude=("已报名",))
            if matches:
                x, y, text = matches[0]
                empty_scroll_count = 0
                last_empty_scroll_signature = ""
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"日常_报名：点击报名 {text}",
                        phase="daily_signup_click_signup_item",
                        current_scene=23,
                    )
                    self._log_locked("action", f"日常_报名：点击报名列「{text}」")
                self._click_frame_point(ctx, image23, x, y)
                try:
                    yield from self._wait_scene_id(ctx, stop_event, 24, timeout=12.0, label="日常_报名：等待领取 #24")
                except RuntimeError:
                    frame_after_click = self._screencap(ctx)
                    scene_id, score = self._identify_scene_number(ctx, frame_after_click, [23])
                    if scene_id != 23:
                        raise
                    with self._lock:
                        self._log_locked(
                            "warn",
                            f"日常_报名：点击「{text}」后仍在 #23 {score:.0f}%，跳过该项并继续扫描",
                        )
                    self._scroll_shape_content(ctx, image23, column_shape)
                    yield from self._wait_scroll_settle(ctx, stop_event)
                    continue
                claim_frame = self._screencap(ctx)
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_报名：点击领取",
                        phase="daily_signup_claim_reward",
                        current_scene=24,
                    )
                    self._log_locked("action", "日常_报名：点击 #24「领取」")
                self._click_shape(ctx, image24, claim_shape, claim_frame)
                yield from self._wait_scene_id(ctx, stop_event, 23, timeout=12.0, label="日常_报名：返回报名 #23")
                clicked_count += 1
                continue

            signature = self._daily_signup_first_row_signature(lines, image23, ctx=ctx)
            if signature and signature == last_empty_scroll_signature:
                with self._lock:
                    self._log_locked("info", "日常_报名：报名列滚动后签名未变化，判定已滚到底")
                break
            if empty_scroll_count >= max_empty_scrolls:
                with self._lock:
                    self._log_locked("info", f"日常_报名：报名列空滚动达到兜底上限 {max_empty_scrolls}，停止扫描")
                break
            last_empty_scroll_signature = signature
            empty_scroll_count += 1
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_报名：报名列向下滚动 {empty_scroll_count}",
                    phase="daily_signup_scroll_signup_list",
                    current_scene=23,
                )
                self._log_locked("action", f"日常_报名：报名列未发现「报名」，向下滚动 {empty_scroll_count}")
            self._scroll_shape_content(ctx, image23, column_shape)
            yield from self._wait_scroll_settle(ctx, stop_event)

        frame = self._screencap(ctx)
        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_报名：报名处理完成，领取 {clicked_count} 个，返回日常 #69",
                phase="daily_signup_return_daily",
                current_scene=23,
            )
            self._log_locked("action", "日常_报名：点击 #23「返回」")
        self._click_shape(ctx, image23, back_shape, frame)
        yield from self._wait_scene_id(ctx, stop_event, 69, timeout=12.0, label="日常_报名：返回日常 #69")

        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_报名：报名处理完成，领取 {clicked_count} 个，已返回 #69",
                phase="daily_signup_done",
                current_scene=69,
            )
            self._log_locked("success", f"日常_报名：报名处理完成，领取 {clicked_count} 个，已返回 #69")
        return "success"

    def _scroll_shape_content(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        *,
        reverse: bool = False,
    ) -> None:
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        x = left + width / 2
        direction = str(shape.get("contentDirection") or "down").strip().lower()
        if reverse:
            direction = "down" if direction == "up" else "up"
        if direction == "up":
            start_y = top + height * 0.25
            end_y = top + height * 0.75
        else:
            start_y = top + height * 0.75
            end_y = top + height * 0.25
        self._drag_frame_point(ctx, image, x, start_y, x, end_y, duration_ms=1000)

    def _wait_scroll_settle(self, ctx: dict[str, Any], stop_event: threading.Event, seconds: float = 1.0):
        self._clear_tick_frame(ctx)
        if stop_event.wait(max(0.0, float(seconds))):
            self._raise_if_stopped(stop_event)
        self._clear_tick_frame(ctx)
        yield BehaviorTreeStatus.RUNNING

    def _wait_runtime_action_settle(self, ctx: dict[str, Any], stop_event: threading.Event, seconds: float = 2.0):
        self._clear_tick_frame(ctx)
        if stop_event.wait(max(0.0, float(seconds))):
            self._raise_if_stopped(stop_event)
        self._clear_tick_frame(ctx)
        yield BehaviorTreeStatus.RUNNING

    def _ocr_row_clicks_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
    ) -> list[tuple[float, float, str]]:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return []
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        click_x = left + width / 2
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if include and not all(fragment in text for fragment in include):
                continue
            if exclude and any(fragment in text for fragment in exclude):
                continue
            y = float(line.get("y") or 0)
            h = float(line.get("h") or 0)
            cy = y + h / 2
            if top <= cy <= bottom:
                matches.append((click_x, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _daily_signup_first_row_signature(self, lines: list[dict[str, Any]], image23: dict[str, Any], *, ctx: dict[str, Any] | None = None) -> str:
        exclude_boxes = self._occlusion_marker_boxes(ctx, image23) if ctx else []
        marker_image = self._find_child_image_by_number(image23, 25) or image23
        signature = self._text_signature_in_shape(lines, marker_image, "第1行", "shape 1", exclude_boxes=exclude_boxes)
        if signature:
            return signature
        return self._vertical_text_signature_in_shape(lines, image23, "报名列", exclude_boxes=exclude_boxes)

    def _text_signature_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        *shape_titles: str,
        exclude_boxes: list[dict[str, float]] | None = None,
    ) -> str:
        shape = self._find_shape(image, *shape_titles) if image else None
        if not shape or not image:
            return ""
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        fragments: list[str] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if self._point_in_boxes(cx, cy, exclude_boxes):
                continue
            if left <= cx <= right and top <= cy <= bottom:
                fragments.append(text)
        return "|".join(fragments)

    def _vertical_text_signature_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        exclude_boxes: list[dict[str, float]] | None = None,
    ) -> str:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return ""
        box = self._box(shape, image)
        top = float(box.get("y") or 0)
        bottom = top + float(box.get("h") or 0)
        fragments: list[str] = []
        for line in sorted(lines, key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0))):
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            y = float(line.get("y") or 0)
            h = float(line.get("h") or 0)
            cy = y + h / 2
            x = float(line.get("x") or 0)
            w = float(line.get("w") or 0)
            if self._point_in_boxes(x + w / 2, cy, exclude_boxes):
                continue
            if top <= cy <= bottom:
                fragments.append(text)
        return "|".join(fragments)

    def _occlusion_marker_boxes(self, ctx: dict[str, Any] | None, image: dict[str, Any]) -> list[dict[str, float]]:
        if not ctx:
            return []
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            return []
        boxes: list[dict[str, float]] = []

        def visit(nodes: list[dict[str, Any]], in_occlusion_folder: bool = False) -> None:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("type") or "").strip()
                title = str(node.get("title") or "").strip()
                current_in_occlusion = in_occlusion_folder or (node_type == "folder" and title == "遮挡标记")
                if current_in_occlusion and node_type == "image":
                    for shape in self._flatten_shapes(node.get("shapes")):
                        if shape.get("kind") == "group":
                            continue
                        box = self._box(shape, image)
                        boxes.append({
                            "x": float(box.get("x") or 0),
                            "y": float(box.get("y") or 0),
                            "w": float(box.get("w") or 0),
                            "h": float(box.get("h") or 0),
                        })
                children = node.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)], current_in_occlusion)

        visit(tree)
        return boxes

    def _point_in_boxes(self, x: float, y: float, boxes: list[dict[str, float]] | None) -> bool:
        if not boxes:
            return False
        for box in boxes:
            left = float(box.get("x") or 0)
            top = float(box.get("y") or 0)
            right = left + float(box.get("w") or 0)
            bottom = top + float(box.get("h") or 0)
            if left <= x <= right and top <= y <= bottom:
                return True
        return False

    def _execute_gift_code_task(self, ctx: dict[str, Any], codes: list[str], stop_event: threading.Event) -> None:
        with self._lock:
            self._set_status_locked("running", "对齐 #49 设置页", phase="align_settings")
        self._align_settings(ctx, stop_event)
        for index, code in enumerate(codes):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked("running", f"处理第 {index + 1}/{len(codes)} 个：{code}", current_index=index, current_code=code, phase="process_code")
                self._log_locked("action", f"开始兑换：{code}")
            self._process_code(ctx, code, index == len(codes) - 1, stop_event)
        with self._lock:
            self._set_status_locked("running", "从 #49 回退", phase="finish_back")
        self._finish_from_settings(ctx, stop_event)

    def _raise_if_stopped(self, stop_event: threading.Event) -> None:
        if stop_event.is_set():
            raise InterruptedError()

    def _load_asset_tree(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise RuntimeError("未找到帧树，请先保存帧树标注")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise RuntimeError("帧树格式错误")
        return [item for item in payload if isinstance(item, dict)]

    def _image_number(self, image: dict[str, Any]) -> int | None:
        return _runtime_image_number(image)

    def _find_child_image_by_number(self, image: dict[str, Any], number: int) -> dict[str, Any] | None:
        def visit(items: list[dict[str, Any]]) -> dict[str, Any] | None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image" and self._image_number(item) == number:
                    return item
                children = item.get("children")
                if isinstance(children, list):
                    found = visit([child for child in children if isinstance(child, dict)])
                    if found is not None:
                        return found
            return None

        children = image.get("children")
        if not isinstance(children, list):
            return None
        return visit([child for child in children if isinstance(child, dict)])

    def _index_images(self, nodes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        return _index_runtime_images(nodes)

    def _jump_target_text(self, shape: dict[str, Any]) -> str:
        return SceneNavigator([]).jump_target_text(shape)

    def _parse_scene_jump_entries(self, value: Any) -> list[dict[str, Any]]:
        return SceneNavigator([]).parse_scene_jump_entries(value)

    def _serialize_scene_jump_entries(self, entries: list[dict[str, Any]]) -> str:
        return SceneNavigator([]).serialize_scene_jump_entries(entries)

    def _increment_scene_jump_target(self, shape: dict[str, Any], target_scene_id: int) -> bool:
        return SceneNavigator([]).increment_scene_jump_target(shape, target_scene_id)

    def _scene_jump_label_number(self, label: Any) -> int | None:
        return SceneNavigator([]).scene_jump_label_number(label)

    def _collect_folder_image_numbers(self, node: dict[str, Any]) -> list[int]:
        return SceneNavigator([]).collect_folder_image_numbers(node)

    def _resolve_scene_jump_label(self, tree: list[dict[str, Any]], label: Any) -> list[int]:
        return SceneNavigator(tree).resolve_scene_jump_label(label)

    def _scene_jump_target_ids(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> list[int]:
        return SceneNavigator(tree).scene_jump_target_ids(shape)

    def _resolve_scene_image_title_ids(self, tree: list[dict[str, Any]], title: str) -> list[int]:
        return SceneNavigator(tree).resolve_scene_image_title_ids(title)

    def _implicit_parent_return_target_ids(
        self,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        parent_image: dict[str, Any] | None,
        parent_folder_title: str = "",
    ) -> list[int]:
        return SceneNavigator(tree).implicit_parent_return_target_ids(
            shape,
            parent_image,
            parent_folder_title,
        )

    def _scene_id_key(self, scene_id: int) -> str:
        for key, value in self.scene_ids.items():
            if int(value) == int(scene_id):
                return key
        return str(scene_id)

    def _scene_match_threshold(self, scene_id: int) -> float:
        key = self._scene_id_key(scene_id)
        return float(self.scene_thresholds.get(key, self.scene_threshold))

    def _scene_matches_id(self, scene_id: int, score: float) -> bool:
        return self._scene_recognizer().scene_matches_id(scene_id, score)

    def _scene_recognizer(self) -> SceneRecognizer:
        return SceneRecognizer(
            score_image=self._scene_score,
            threshold_for_scene_id=self._scene_match_threshold,
            image_for_key=self._image,
            threshold_for_key=lambda key: float(self.scene_thresholds.get(key, self.scene_threshold)),
            key_priorities={
                "duplicated": 12,
                "reward": 11,
                "wanling_invite": 10,
                "gift": 9,
                "youli_result": 8,
                "youli_explore": 7,
                "youli": 6,
                "daily_activity": 5,
                "signup_reward": 5,
                "signup": 5,
                "daily_xianyuan_leave_confirm": 5,
                "daily_xianyuan_challenge_result": 5,
                "daily_xianyuan_challenge_confirm": 5,
                "daily_xianyuan_challenge_dialogue": 5,
                "daily_xianyuan_dialogue": 5,
                "daily_xianyuan_detail": 5,
                "daily_xianyuan_list": 5,
                "daily": 4,
                "settings": 3,
                "world_menu": 2,
                "hide_floating": 1,
                "world": 0,
            },
        )

    def _identify_scene_number(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> tuple[int | None, float]:
        candidate_scene_ids = preferred_scene_ids or self._runtime_scene_candidate_ids(ctx)
        previous_prefer_ocr = ctx.get("_prefer_full_frame_ocr")
        if self._scene_number_scan_has_ocr_identity(ctx):
            ctx["_prefer_full_frame_ocr"] = True
            try:
                self._cached_ocr_lines(ctx, frame_data_url)
            except Exception as exc:
                self._log("detail", f"场景识别预取 OCR 失败：{exc}")
        try:
            if 86 in candidate_scene_ids:
                text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
                if self._leave_scene_confirm_text(text):
                    return 86, 100.0
            return self._scene_recognizer().identify_scene_number(
                ctx,
                frame_data_url,
                preferred_scene_ids=candidate_scene_ids or None,
            )
        finally:
            if previous_prefer_ocr is None:
                ctx.pop("_prefer_full_frame_ocr", None)
            else:
                ctx["_prefer_full_frame_ocr"] = previous_prefer_ocr

    def _leave_scene_confirm_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return bool(("是否离开" in compact or "离开当前场景" in compact) and "确认" in compact)

    def _runtime_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        return [
            int(scene_id)
            for scene_id in self.scene_ids.values()
            if int(scene_id) in images and isinstance(images.get(int(scene_id)), dict)
        ]

    def _scene_number_scan_has_ocr_identity(self, ctx: dict[str, Any]) -> bool:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return False
        for image in images.values():
            if not isinstance(image, dict):
                continue
            for shape in self._scene_identity_shapes(image):
                if self._shape_ocr_fallback_enabled(shape):
                    return True
        return False

    def _scene_jump_edges(self, tree: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        return SceneNavigator(tree).scene_jump_edges()

    def _find_scene_route(self, tree: list[dict[str, Any]], start_scene_id: int, target_scene_id: int) -> list[dict[str, Any]] | None:
        return SceneNavigator(tree).find_scene_route(start_scene_id, target_scene_id)

    def _scene_jump_confirmation_scene_ids(self, tree: list[dict[str, Any]]) -> list[int]:
        source_shape = {"title": "离开"}
        return SceneNavigator(tree).confirmation_scene_ids(
            lambda image: self._scene_jump_intermediate_confirm_shape(image, source_shape) is not None
        )

    def _scene_route_candidate_ids(self, tree: list[dict[str, Any]], target_scene_id: int) -> list[int]:
        return SceneNavigator(tree).route_candidate_ids(
            target_scene_id,
            confirmation_scene_ids=self._scene_jump_confirmation_scene_ids(tree),
        )

    def _write_asset_tree(self, asset_tree_path: Path, tree: list[dict[str, Any]]) -> None:
        _write_data_annotation_json(asset_tree_path, tree)
        self._auto_close_candidates_cache.pop(str(asset_tree_path), None)

    def _save_unknown_scene_frame(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        frame_data_url: str,
        *,
        target_scene_id: int,
        current_scene_id: int | None,
        action_shape: dict[str, Any] | None,
        elapsed_seconds: float,
        history: list[str],
    ) -> dict[str, Any]:
        action_title = action_shape.get("title") if isinstance(action_shape, dict) else "unknown"
        current_text = f"#{current_scene_id}" if current_scene_id is not None else "unknown"
        detail = "；".join([
            f"目标场景=#{target_scene_id}",
            f"当前/点击前场景={current_text}",
            f"动作 shape={action_title}",
            f"累计等待={elapsed_seconds:.1f}s",
            f"最近识别={history[-1] if history else '无'}",
        ])
        self._log("error", f"场景跳转缺少可靠标注，已中断：{detail}")
        raise RuntimeError(f"场景跳转缺少可靠标注，已中断，请人工补标/修标后重试：{detail}")

    def _is_independent_exit_shape(self, shape: dict[str, Any]) -> bool:
        return CloseActionPlanner().is_independent_exit_shape(shape)

    def _auto_close_guard_action_shape(self, image: dict[str, Any]) -> dict[str, Any] | None:
        # Fanxiu annotation convention: in the top-level popup group, "空白" means
        # the background/overlay area that closes the popup when tapped. Prefer it
        # over tiny close buttons and "确定", which may trigger extra scene changes.
        planner = CloseActionPlanner(title_priorities=("空白", "关闭"))
        action_shape = planner.choose_close_shape(image.get("shapes"), include_independent_exit=True)
        if action_shape is not None:
            return action_shape
        return CloseActionPlanner(title_priorities=("确定",)).choose_close_shape(image.get("shapes"))

    def _index_guard_candidates(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_image_ids: set[int] = set()

        def add_candidate(image: dict[str, Any], folder_path: str, action_shape: dict[str, Any] | None) -> None:
            identity = id(image)
            if identity in seen_image_ids:
                return
            seen_image_ids.add(identity)
            candidates.append({
                "image": image,
                "folder_path": folder_path,
                "action_shape": action_shape,
            })

        def add_first_level_popup_images(folder: dict[str, Any]) -> None:
            children = folder.get("children")
            if not isinstance(children, list):
                return
            folder_title = str(folder.get("title") or "").strip()
            for child in children:
                if not isinstance(child, dict) or child.get("type") != "image":
                    continue
                add_candidate(child, folder_title, self._auto_close_guard_action_shape(child))

        def visit(items: list[dict[str, Any]], path: list[str]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                node_type = str(item.get("type") or "")
                title = str(item.get("title") or "").strip()
                current_path = [*path, title] if title else path
                if node_type == "folder" and title == "弹窗":
                    add_first_level_popup_images(item)
                    continue
                if node_type == "image":
                    action_shape = self._auto_close_guard_action_shape(item)
                    if action_shape is not None and self._is_independent_exit_shape(action_shape):
                        add_candidate(item, "/".join(path), action_shape)
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)], current_path)

        visit(nodes, [])
        return candidates

    def _best_popup_47_child(self, runtime: FanxiuRuntime, popup_view: View) -> tuple[View | None, float]:
        children = popup_view.raw.get("children") if isinstance(popup_view.raw, dict) else None
        if not isinstance(children, list):
            return None, 0.0
        best_view: View | None = None
        best_score = 0.0
        for child in children:
            if not isinstance(child, dict) or child.get("type") != "image":
                continue
            child_view = View(child)
            child_score = runtime.popup_score(child_view)
            if child_score > best_score:
                best_view = child_view
                best_score = child_score
        return best_view, best_score

    def _handle_auto_close_popup_47_child(
        self,
        runtime: FanxiuRuntime,
        popup_view: View,
        event: dict[str, Any],
        *,
        allow_confirm_actions: bool = True,
    ) -> bool:
        child_view, child_score = self._best_popup_47_child(runtime, popup_view)
        if child_view is None or child_score < self.overlay_threshold:
            return False
        if child_view.id == 84:
            return self._handle_auto_close_popup_47_child_84(
                runtime,
                popup_view,
                event,
                child_view=child_view,
                child_score=child_score,
                allow_confirm_actions=allow_confirm_actions,
            )
        if child_view.id == 86:
            return self._handle_auto_close_popup_47_child_86(
                runtime,
                popup_view,
                event,
                child_view=child_view,
                child_score=child_score,
                allow_confirm_actions=allow_confirm_actions,
            )

        child_label = f"#{child_view.id}" if child_view.id is not None else child_view.title or "unknown"
        child_event = {
            **event,
            "image": child_label,
            "title": child_view.title,
            "score": round(child_score, 1),
            "parent_score": event.get("score"),
        }
        if not allow_confirm_actions:
            return self._close_popup_view_without_confirm(runtime, popup_view, event)
        try:
            child_view.close(runtime)
        except RuntimeError:
            self._record_popup_guard_missing(
                child_view.id,
                f"守护命中：#47/{child_label} {event.get('score', 0):.0f}%/{child_score:.0f}%，缺少关闭标注",
                child_event,
                "missing_action",
            )
            return True
        action_title = runtime.last_clicked_shape.title if runtime.last_clicked_shape is not None else "shape"
        self._record_popup_guard_click(
            child_view.id,
            f"守护处理：#47/{child_label} 点击「{action_title or 'shape'}」 {event.get('score', 0):.0f}%/{child_score:.0f}%",
            child_event,
            action_title or "shape",
        )
        return True

    def _handle_auto_close_popup_47_child_84(
        self,
        runtime: FanxiuRuntime,
        popup_view: View,
        event: dict[str, Any],
        *,
        child_view: View | None = None,
        child_score: float | None = None,
        allow_confirm_actions: bool = True,
    ) -> bool:
        child_view = child_view or runtime.get_view(84, root=popup_view)
        child_score = runtime.popup_score(child_view) if child_score is None else child_score
        if child_view is None or child_score < self.overlay_threshold:
            return False
        child_event = {
            **event,
            "image": "#84",
            "title": child_view.title,
            "score": round(child_score, 1),
            "parent_score": event.get("score"),
        }

        no_more_prompt = child_view.get_shape("不再提示")
        if no_more_prompt is not None and not no_more_prompt.is_match(runtime):
            runtime.click_shape(child_view, no_more_prompt)
            self._record_popup_guard_click(84, f"守护处理：#47/#84 点击「不再提示」 {event.get('score', 0):.0f}%/{child_score:.0f}%", child_event, "不再提示")
            return True

        confirm_shape = child_view.get_shape("确认")
        if confirm_shape is None:
            self._record_popup_guard_missing(84, f"守护命中：#84 {child_score:.0f}%，缺少「确认」标注", child_event, "missing_confirm")
            return True
        if not allow_confirm_actions:
            return self._close_popup_view_without_confirm(runtime, popup_view, event)
        runtime.click_shape(child_view, confirm_shape)
        self._record_popup_guard_click(84, f"守护处理：#47/#84 点击「确认」 {event.get('score', 0):.0f}%/{child_score:.0f}%", child_event, "确认")
        return True

    def _handle_auto_close_popup_47_child_86(
        self,
        runtime: FanxiuRuntime,
        popup_view: View,
        event: dict[str, Any],
        *,
        child_view: View | None = None,
        child_score: float | None = None,
        allow_confirm_actions: bool = True,
    ) -> bool:
        child_view = child_view or runtime.get_view(86, root=popup_view)
        child_score = runtime.popup_score(child_view) if child_score is None else child_score
        if child_view is None or child_score < self.overlay_threshold:
            return False
        child_event = {
            **event,
            "image": "#86",
            "title": child_view.title,
            "score": round(child_score, 1),
            "parent_score": event.get("score"),
        }
        confirm_shape = child_view.get_shape("确认")
        if not confirm_shape:
            self._record_popup_guard_missing(86, f"守护命中：#86 {child_score:.0f}%，缺少「确认」标注", child_event, "missing_confirm")
            return True
        if not allow_confirm_actions:
            return self._close_popup_view_without_confirm(runtime, popup_view, event)
        runtime.click_shape(child_view, confirm_shape)
        self._record_popup_guard_click(86, f"守护处理：#47/#86 点击「确认」 {event.get('score', 0):.0f}%/{child_score:.0f}%", child_event, "确认")
        return True

    def _record_popup_guard_missing(self, scene_id: int | None, message: str, event: dict[str, Any], action: str) -> None:
        with self._lock:
            self._status.update({
                "current_scene": scene_id,
                "message": message,
                "last_guard_event": {**event, "action": action},
                "updated_at": time.time(),
            })
            self._log_locked("error", self._status["message"])

    def _record_popup_guard_click(self, scene_id: int | None, message: str, event: dict[str, Any], action_title: str) -> None:
        with self._lock:
            self._status.update({
                "current_scene": scene_id,
                "message": message,
                "last_guard_event": {**event, "action": f"click:{action_title or 'shape'}"},
                "updated_at": time.time(),
            })
            self._log_locked("guardClick", self._status["message"])

    def _close_popup_view_without_confirm(self, runtime: FanxiuRuntime, view: View, event: dict[str, Any]) -> bool:
        action_shape = runtime.matched_view.action_shape if runtime.matched_view is not None else None
        if not isinstance(action_shape, dict) or str(action_shape.get("title") or "").strip() == "确定":
            return False
        shape = Shape(action_shape, parent_view=view)
        runtime.click_shape(view, shape)
        scene_id = view.id
        image_label = f"#{scene_id}" if scene_id is not None else view.title or view.filename or "unknown"
        action_title = shape.title or "shape"
        self._record_popup_guard_click(
            scene_id,
            f"守护处理：{image_label} 点击「{action_title}」 {event.get('score', 0):.0f}%",
            event,
            action_title,
        )
        return True

    def _auto_close_guard_images(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._index_guard_candidates(nodes)

    def _auto_close_guard_candidates_for_path(self, asset_tree_path: Path) -> list[dict[str, Any]]:
        try:
            stat = asset_tree_path.stat()
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            signature = (0, 0)
        cache_key = str(asset_tree_path)
        cached = self._auto_close_candidates_cache.get(cache_key)
        if cached and cached[0] == signature[0] and cached[1] == signature[1]:
            return cached[2]
        tree = self._load_asset_tree(asset_tree_path)
        candidates = self._auto_close_guard_images(tree)
        self._auto_close_candidates_cache[cache_key] = (signature[0], signature[1], candidates)
        return candidates

    def _auto_close_popup_candidate_score(self, ctx: dict[str, Any], candidate: dict[str, Any], frame_data_url: str) -> float:
        image = candidate.get("image")
        if not isinstance(image, dict):
            return 0.0
        return self._popup_score(ctx, image, frame_data_url)

    def _auto_close_popup_candidate_scores_serial(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> list[float]:
        return [self._auto_close_popup_candidate_score(ctx, candidate, frame_data_url) for candidate in candidates]

    def _auto_close_popup_candidate_scores_parallel(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> list[float]:
        if len(candidates) <= 1:
            return self._auto_close_popup_candidate_scores_serial(ctx, candidates, frame_data_url)
        if any(self._popup_candidate_has_ocr_match(candidate) for candidate in candidates):
            ctx["_prefer_full_frame_ocr"] = True
            try:
                self._cached_ocr_lines(ctx, frame_data_url)
            except Exception as exc:
                self._log("detail", f"弹窗守护预取 OCR 失败：{exc}")
        workers = len(candidates)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-popup-match") as executor:
            return list(executor.map(lambda candidate: self._auto_close_popup_candidate_score(ctx, candidate, frame_data_url), candidates))

    def _popup_candidate_has_ocr_match(self, candidate: dict[str, Any]) -> bool:
        image = candidate.get("image")
        if not isinstance(image, dict):
            return False
        return any(self._shape_ocr_fallback_enabled(shape) for shape in self._popup_match_shapes(image))

    def _auto_close_popup_candidate_scores(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> list[float]:
        return self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)

    def _auto_close_popup_first_match(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
    ) -> tuple[dict[str, Any] | None, float]:
        if not candidates:
            return None, 0.0
        previous_fast_match = ctx.get("_popup_fast_match_only")
        ctx["_popup_fast_match_only"] = True
        try:
            scores = self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)
        finally:
            if previous_fast_match is None:
                ctx.pop("_popup_fast_match_only", None)
            else:
                ctx["_popup_fast_match_only"] = previous_fast_match
        for candidate, score in zip(candidates, scores):
            if score >= self.overlay_threshold:
                return candidate, score
        scores = self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)
        for candidate, score in zip(candidates, scores):
            if score >= self.overlay_threshold:
                return candidate, score
        return None, 0.0

    def _auto_close_popup_guard_step(
        self,
        runtime: FanxiuRuntime,
        *,
        allow_confirm_actions: bool = True,
    ) -> bool:
        view = runtime.find_view("弹窗")
        matched = runtime.matched_view
        if view is None or matched is None:
            return False

        image_label = f"#{view.id}" if view.id is not None else view.title or view.filename or "unknown"
        event = {
            "time": time.time(),
            "kind": "popup",
            "image": image_label,
            "title": view.title,
            "folder_path": matched.folder_path,
            "score": round(matched.score, 1),
            "action": "",
        }

        if view.id == 47 and self._handle_auto_close_popup_47_child(
            runtime,
            view,
            event,
            allow_confirm_actions=allow_confirm_actions,
        ):
            return True

        try:
            if not allow_confirm_actions:
                return self._close_popup_view_without_confirm(runtime, view, event)
            view.close(runtime)
        except RuntimeError:
            self._record_popup_guard_missing(
                view.id,
                f"守护命中：{image_label} {matched.score:.0f}%，缺少关闭标注",
                event,
                "missing_action",
            )
            return False
        action_title = runtime.last_clicked_shape.title if runtime.last_clicked_shape is not None else "shape"
        self._record_popup_guard_click(
            view.id,
            f"守护处理：{image_label} 点击「{action_title or 'shape'}」 {matched.score:.0f}%",
            event,
            action_title or "shape",
        )
        return True

    def _require_assets(self, ctx: dict[str, Any]) -> None:
        images: dict[int, dict[str, Any]] = ctx["images"]
        if not images:
            raise RuntimeError("缺少帧标注，请先保存帧树")

    def _image(self, ctx: dict[str, Any], key: str) -> dict[str, Any] | None:
        return ctx["images"].get(self.scene_ids[key])

    def _flatten_shapes(self, shapes: Any) -> list[dict[str, Any]]:
        return _flatten_runtime_shapes(shapes)

    def _find_shape(self, image: dict[str, Any] | None, *titles: str, contains: bool = False) -> dict[str, Any] | None:
        if not image:
            return None
        view = View(image)
        for shape in view.get_shapes():
            if not shape.title:
                continue
            if any((contains and title in shape.title) or (not contains and shape.title == title) for title in titles):
                return shape.raw
        return None

    def _scene_identity_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        return [shape.raw for shape in View(image).get_shapes(include_groups=False) if shape.is_scene_identity]

    def _popup_match_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        shapes = [shape for shape in self._flatten_shapes(image.get("shapes")) if shape.get("kind") != "group"]
        identity = [shape for shape in shapes if bool(shape.get("isSceneIdentity"))]
        return identity or shapes[:4]

    def _frame_size(self, image: dict[str, Any]) -> tuple[int, int]:
        return _runtime_frame_size(image)

    def _box(self, shape: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
        return ActionPlanner().shape_box(image, shape)

    def _data_url(self, data: bytes) -> str:
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def _action_trace_dir(self) -> Path:
        return codeyun_temp_root("fanxiu_action_trace")

    def _action_trace_max_files(self) -> int:
        raw = os.environ.get("CODEYUN_FANXIU_ACTION_TRACE_MAX_FILES")
        try:
            value = int(raw) if raw is not None else _ACTION_TRACE_DEFAULT_MAX_FILES
        except ValueError:
            value = _ACTION_TRACE_DEFAULT_MAX_FILES
        return max(0, value)

    def _action_trace_enabled(self) -> bool:
        value = str(os.environ.get("CODEYUN_FANXIU_ACTION_TRACE", "1")).strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _decode_frame_data_url(self, frame_data_url: str) -> bytes:
        if not frame_data_url.startswith("data:image"):
            raise RuntimeError("frame_data_url 不是图片 data URL")
        return base64.b64decode(frame_data_url.split(",", 1)[1])

    def _annotate_action_trace_png(self, png_data: bytes, action: dict[str, Any]) -> bytes:
        from PIL import Image, ImageDraw, ImageFont

        with Image.open(io.BytesIO(png_data)) as source:
            image = source.convert("RGBA")
        draw = ImageDraw.Draw(image)
        kind = str(action.get("kind") or "")
        color = (255, 48, 48, 255)
        if kind == "drag":
            start = action.get("start") if isinstance(action.get("start"), (list, tuple)) else (0, 0)
            end = action.get("end") if isinstance(action.get("end"), (list, tuple)) else (0, 0)
            sx, sy = float(start[0]), float(start[1])
            ex, ey = float(end[0]), float(end[1])
            draw.line((sx, sy, ex, ey), fill=color, width=8)
            radius = 18
            draw.ellipse((sx - radius, sy - radius, sx + radius, sy + radius), outline=(255, 214, 0, 255), width=6)
            draw.ellipse((ex - radius, ey - radius, ex + radius, ey + radius), outline=color, width=6)
        else:
            point = action.get("point") if isinstance(action.get("point"), (list, tuple)) else (0, 0)
            x, y = float(point[0]), float(point[1])
            radius = 22
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=7)
            draw.line((x - radius * 1.5, y, x + radius * 1.5, y), fill=color, width=5)
            draw.line((x, y - radius * 1.5, x, y + radius * 1.5), fill=color, width=5)
        label = str(action.get("label") or kind or "action")
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        draw.rectangle((8, 8, min(image.width - 8, 8 + max(260, len(label) * 8)), 42), fill=(0, 0, 0, 160))
        draw.text((14, 16), label[:120], fill=(255, 255, 255, 255), font=font)
        out = io.BytesIO()
        image.convert("RGB").save(out, format="PNG")
        return out.getvalue()

    def _prune_action_trace_files(self, trace_dir: Path, *, max_files: int) -> None:
        if max_files <= 0:
            return
        files = sorted(
            [path for path in trace_dir.glob("*") if path.is_file() and (path.suffix.lower() == ".png" or path.name == "index.jsonl")],
            key=lambda path: path.stat().st_mtime,
        )
        overflow = len(files) - max_files
        for path in files[: max(0, overflow)]:
            try:
                path.unlink()
            except OSError:
                pass

    def _save_action_trace(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        action: dict[str, Any],
        *,
        frame_data_url: str | None = None,
    ) -> None:
        if not self._action_trace_enabled():
            return
        max_files = self._action_trace_max_files()
        if max_files <= 0:
            return
        try:
            try:
                frame = self._capture_frame(ctx)
            except Exception:
                frame = frame_data_url or self._screencap(ctx)
            png_data = self._decode_frame_data_url(frame)
            trace_dir = self._action_trace_dir()
            stamp = _now().strftime("%Y%m%d_%H%M%S_%f")
            image_number = self._image_number(image) or "unknown"
            kind = str(action.get("kind") or "action")
            stem = f"{stamp}_{kind}_scene{image_number}"
            raw_path = trace_dir / f"{stem}_before.png"
            marked_path = trace_dir / f"{stem}_marked.png"
            raw_path.write_bytes(png_data)
            marked_path.write_bytes(self._annotate_action_trace_png(png_data, action))
            record = {
                "time": _now().isoformat(timespec="seconds"),
                "kind": kind,
                "image_number": image_number,
                "image_title": image.get("title"),
                "action": action,
                "before": str(raw_path),
                "marked": str(marked_path),
                "runtime_task": self._status.get("current_task"),
                "phase": self._status.get("phase"),
            }
            with (trace_dir / "index.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._prune_action_trace_files(trace_dir, max_files=max_files)
        except Exception as exc:
            self._log("detail", f"动作回溯截图保存失败：{exc}")

    def _set_tick_frame(self, ctx: dict[str, Any], frame_data_url: str | None) -> None:
        if frame_data_url:
            ctx["_tick_frame_data_url"] = frame_data_url

    def _clear_tick_frame(self, ctx: dict[str, Any]) -> None:
        ctx.pop("_tick_frame_data_url", None)

    def _capture_frame(self, ctx: dict[str, Any]) -> str:
        entry: Any = ctx["entry"]
        response = _screencap_game_window2_service() if entry.mode == "local" else _remote_game_window2_screencap(entry)
        return self._data_url(bytes(response.body or b""))

    def _screencap(self, ctx: dict[str, Any]) -> str:
        frame_data_url = ctx.get("_tick_frame_data_url")
        if isinstance(frame_data_url, str) and frame_data_url:
            return frame_data_url
        frame_data_url = self._capture_frame(ctx)
        self._set_tick_frame(ctx, frame_data_url)
        return frame_data_url

    def _run_match(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        scan: bool = False,
        match_strategy: str = "auto",
        ocr_enabled: bool = False,
    ) -> dict[str, Any]:
        payload = self._build_shape_match_payload(
            image,
            shape,
            frame_data_url,
            scan=scan,
            match_strategy=match_strategy,
            ocr_enabled=ocr_enabled,
        )
        entry: Any = ctx["entry"]
        return _match_game_window2_service(payload) if entry.mode == "local" else _match_remote_game_window2(entry, payload)

    def _build_shape_match_payload(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        scan: bool,
        match_strategy: str,
        ocr_enabled: bool,
        save_match_frame: bool | None = None,
    ) -> dict[str, Any]:
        filename = str(image.get("filename") or "")
        if not filename:
            raise RuntimeError(f"帧「{image.get('title') or image.get('id')}」缺少图片文件")
        ocr_text = str(shape.get("ocrText") or "").strip()
        use_ocr = bool(ocr_enabled and ocr_text)
        if save_match_frame is None:
            save_match_frame = not use_ocr
        payload = {
            "filename": filename,
            "box": self._box(shape, image),
            "scan": scan,
            "pixel_tolerance": int(shape.get("pixelTolerance") if shape.get("pixelTolerance") is not None else 5),
            "alpha_mask_data_url": ((shape.get("alphaMask") or {}).get("dataUrl") if isinstance(shape.get("alphaMask"), dict) else None),
            "tolerance_min_data_url": ((shape.get("toleranceRange") or {}).get("minDataUrl") if isinstance(shape.get("toleranceRange"), dict) else None),
            "tolerance_max_data_url": ((shape.get("toleranceRange") or {}).get("maxDataUrl") if isinstance(shape.get("toleranceRange"), dict) else None),
            "current_frame_data_url": frame_data_url,
            "prefer_cached": False,
            "match_strategy": match_strategy,
            "match_search_radius": int(shape.get("jitterRadius") or 0) if bool(shape.get("jitterEnabled")) and match_strategy == "auto" and not scan else None,
            "ocr_enabled": use_ocr,
            "ocr_text": ocr_text if use_ocr else "",
            "ocr_match_mode": shape.get("ocrMatchMode") or "contains",
            "read_only_cache": use_ocr,
            "save_match_frame": bool(save_match_frame),
        }
        return payload

    def _shape_ocr_fallback_enabled(self, shape: dict[str, Any]) -> bool:
        return ShapeMatchPlanner().ocr_fallback_enabled(shape)

    def _shape_click_needs_frame(self, shape: dict[str, Any]) -> bool:
        flags = self._shape_runtime_match_payload_flags(shape)
        return str(flags.get("image_role") or "") != "off" or bool(flags.get("ocr_enabled"))

    def _shape_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        match_strategy: str = "anchor_pixel",
        ocr_fallback: bool = True,
    ) -> float:
        try:
            condition = "image" if match_strategy == "anchor_pixel" else "auto"
            score = float(self._match_shape(ctx, image, shape, frame_data_url, condition=condition).get("similarity") or 0)
            if ocr_fallback and score < self.scene_threshold and self._shape_ocr_fallback_enabled(shape):
                ocr_score = float(self._match_shape(ctx, image, shape, frame_data_url, condition="ocr").get("similarity") or 0)
                score = max(score, ocr_score)
            return score
        except Exception as exc:
            self._log("detail", f"匹配失败：{image.get('title')} / {shape.get('title')}：{exc}")
            return 0

    def _shape_match_role(self, shape: dict[str, Any], key: str, default: str = "required") -> str:
        return ShapeMatchPlanner().match_role(shape, key, default)

    def _shape_ocr_role(self, shape: dict[str, Any]) -> str:
        return ShapeMatchPlanner().ocr_role(shape)

    def _shape_image_role(self, shape: dict[str, Any]) -> str:
        return ShapeMatchPlanner().image_role(shape)

    def _shape_runtime_match_payload_flags(self, shape: dict[str, Any], *, condition: str = "auto") -> dict[str, Any]:
        return ShapeMatchPlanner().runtime_match_payload_flags(shape, condition=condition)

    def _shape_match_conditions(self, shape: dict[str, Any]) -> list[str]:
        first = "ocr" if self._shape_prefers_ocr_first(shape) else "image"
        return ShapeMatchPlanner().match_conditions(shape, first=first)

    def _shape_prefers_ocr_first(self, shape: dict[str, Any]) -> bool:
        title = str(shape.get("title") or "")
        jump_target = str(shape.get("sceneJumpTarget") or "")
        return bool(title == "邮件" and jump_target.startswith("121") and str(shape.get("ocrText") or "").strip())

    def _match_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        *,
        condition: str = "auto",
    ) -> dict[str, Any]:
        flags = self._shape_runtime_match_payload_flags(shape, condition=condition)
        image_role = str(flags["image_role"])
        ocr_role = str(flags["ocr_role"])
        ocr_enabled = bool(flags["ocr_enabled"])
        if condition == "auto" and image_role != "off" and ocr_role != "required":
            flags = self._shape_runtime_match_payload_flags(shape, condition="image")
            ocr_enabled = False
        if image_role == "off" and not ocr_enabled:
            return {"ok": False, "matched": False, "similarity": 0, "matches": [], "box": self._box(shape, image), "reason": "match_disabled", "flags": flags}
        if (
            condition == "image"
            and bool(ctx.get("_prefer_full_frame_ocr"))
            and image_role == "optional"
            and self._shape_ocr_fallback_enabled(shape)
        ):
            return {"ok": True, "matched": False, "similarity": 0, "matches": [], "box": self._box(shape, image), "reason": "prefer_full_frame_ocr", "flags": flags}
        if ocr_enabled and (bool(ctx.get("_prefer_full_frame_ocr")) or self._has_cached_ocr_lines(ctx, frame_data_url)):
            result = {"ok": True, "matched": False, "similarity": 0, "matches": [], "box": self._box(shape, image)}
            if self._shape_full_frame_ocr_matches(ctx, image, shape, frame_data_url, result):
                result["similarity"] = 100
                result["matched"] = True
                result["flags"] = flags
                result["resolved_box"] = result.get("box") if isinstance(result.get("box"), dict) else self._box(shape, image)
                return result
            if bool(ctx.get("_prefer_full_frame_ocr")):
                result["flags"] = flags
                return result
        try:
            result = self._run_match(
                ctx,
                image,
                shape,
                frame_data_url,
                scan=bool(flags["scan"]),
                match_strategy=str(flags["match_strategy"]),
                ocr_enabled=ocr_enabled,
            )
        except Exception as exc:
            raise RuntimeError(f"浮动标注「{shape.get('title') or shape.get('id')}」匹配失败：{exc}") from exc
        similarity = float(result.get("similarity") or 0)
        result_ocr_matched = bool(ocr_enabled and self._shape_match_result_ocr_matches(shape, result))
        matched = result_ocr_matched or similarity >= float(self.scene_threshold)
        if ocr_enabled and not result_ocr_matched:
            matched = matched and bool(result.get("matches"))
        if matched:
            fixed_box = result.get("fixed_box")
            if isinstance(fixed_box, dict):
                result["resolved_box"] = fixed_box
            else:
                result["resolved_box"] = result.get("box") if isinstance(result.get("box"), dict) else self._box(shape, image)
        elif ocr_enabled and self._shape_full_frame_ocr_matches(ctx, image, shape, frame_data_url, result):
            matched = True
            result["matched"] = True
            if isinstance(result.get("fixed_box"), dict):
                result["resolved_box"] = result.get("fixed_box")
            elif isinstance(result.get("box"), dict):
                result["resolved_box"] = result.get("box")
            else:
                result["resolved_box"] = self._box(shape, image)
        result["matched"] = matched
        result["flags"] = flags
        return result

    def _shape_match_result_ocr_matches(self, shape: dict[str, Any], result: dict[str, Any]) -> bool:
        target = _sanitize_ocr_text(shape.get("ocrText"))
        if not target:
            return False
        mode = str(shape.get("ocrMatchMode") or "contains")
        raw_matches = result.get("matches")
        if not isinstance(raw_matches, list):
            return False
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            text = _sanitize_ocr_text(item.get("text") or item.get("ocr_text"))
            if text and self._ocr_text_matches(text, target, mode):
                result["ocr_text"] = text
                return True
        return False

    def _shape_full_frame_ocr_matches(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        result: dict[str, Any],
    ) -> bool:
        target = _sanitize_ocr_text(shape.get("ocrText"))
        if not target:
            return False
        box = result.get("fixed_box") if isinstance(result.get("fixed_box"), dict) else self._box(shape, image)
        lines = self._cached_ocr_lines(ctx, frame_data_url)
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not self._ocr_text_matches(text, target, str(shape.get("ocrMatchMode") or "contains")):
                continue
            if self._ocr_line_overlaps_box(line, box):
                result["ocr_text"] = text
                result["matches"] = [line]
                return True
        return False

    def _cached_ocr_lines(self, ctx: dict[str, Any], frame_data_url: str) -> list[dict[str, Any]]:
        cache = ctx.setdefault("_ocr_lines_cache", {})
        if isinstance(cache, dict) and cache.get("frame") == frame_data_url and isinstance(cache.get("lines"), list):
            return cache["lines"]
        lines = self._ocr_lines(frame_data_url)
        ctx["_ocr_lines_cache"] = {"frame": frame_data_url, "lines": lines}
        return lines

    def _has_cached_ocr_lines(self, ctx: dict[str, Any], frame_data_url: str) -> bool:
        cache = ctx.get("_ocr_lines_cache")
        return isinstance(cache, dict) and cache.get("frame") == frame_data_url and isinstance(cache.get("lines"), list)

    def _ocr_text_matches(self, text: str, target: str, mode: str) -> bool:
        mode = str(mode or "contains").strip().lower()
        if mode == "exact":
            return text == target
        if mode == "regex":
            try:
                return re.search(target, text) is not None
            except re.error:
                return target in text
        if mode == "wildcard":
            pattern = "^" + re.escape(target).replace("\\*", ".*").replace("\\?", ".") + "$"
            return re.search(pattern, text) is not None
        return target in text

    def _ocr_line_overlaps_box(self, line: dict[str, Any], box: dict[str, Any]) -> bool:
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        line_left = float(line.get("x") or 0)
        line_top = float(line.get("y") or 0)
        line_right = line_left + float(line.get("w") or 0)
        line_bottom = line_top + float(line.get("h") or 0)
        overlap_x = max(0.0, min(right, line_right) - max(left, line_left))
        overlap_y = max(0.0, min(bottom, line_bottom) - max(top, line_top))
        return overlap_x > 0 and overlap_y > 0

    def _require_shape_match(
        self,
        result: dict[str, Any],
        shape: dict[str, Any],
    ) -> None:
        if bool(result.get("matched")):
            return
        flags = result.get("flags") if isinstance(result.get("flags"), dict) else {}
        ocr_text = str(shape.get("ocrText") or "").strip()
        if str(flags.get("ocr_role") or "") == "required" and bool(flags.get("ocr_enabled")):
            raise RuntimeError(f"未能按 OCR 定位浮动按钮「{shape.get('title') or shape.get('id')}」：目标 {ocr_text}")
        if str(flags.get("image_role") or "") == "required":
            raise RuntimeError(f"未能按图像定位浮动按钮「{shape.get('title') or shape.get('id')}」")
        if str(flags.get("image_role") or "") != "off" or bool(flags.get("ocr_enabled")):
            kind = "浮动按钮" if bool(shape.get("floating")) else "按钮"
            raise RuntimeError(f"未能定位{kind}「{shape.get('title') or shape.get('id')}」")

    def _scene_identity_shape_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        return SceneScorer(
            shape_score=lambda score_ctx, score_image, score_shape, score_frame: self._shape_score(
                score_ctx,
                score_image,
                score_shape,
                score_frame,
                ocr_fallback=False,
            ),
            shape_ocr_score=lambda score_ctx, score_image, score_shape, score_frame: float(
                self._match_shape(
                    score_ctx,
                    score_image,
                    score_shape,
                    score_frame,
                    condition="ocr",
                ).get("similarity") or 0
            ),
            threshold=float(self.scene_threshold),
            log_detail=lambda message: self._log("detail", message),
        ).scene_identity_shape_score(ctx, image, shape, frame_data_url)

    def _scene_score(self, ctx: dict[str, Any], image: dict[str, Any], frame_data_url: str) -> float:
        previous_prefer_ocr = ctx.get("_prefer_full_frame_ocr")
        if any(self._shape_ocr_fallback_enabled(shape) for shape in self._scene_identity_shapes(image)):
            ctx["_prefer_full_frame_ocr"] = True
            try:
                self._cached_ocr_lines(ctx, frame_data_url)
            except Exception as exc:
                self._log("detail", f"场景识别预取 OCR 失败：{exc}")
        score = SceneScorer(
            shape_score=lambda score_ctx, score_image, score_shape, score_frame: self._shape_score(
                score_ctx,
                score_image,
                score_shape,
                score_frame,
                ocr_fallback=False,
            ),
            shape_ocr_score=lambda score_ctx, score_image, score_shape, score_frame: float(
                self._match_shape(
                    score_ctx,
                    score_image,
                    score_shape,
                    score_frame,
                    condition="ocr",
                ).get("similarity") or 0
            ),
            threshold=float(self.scene_threshold),
            log_detail=lambda message: self._log("detail", message),
        ).scene_score(ctx, image, frame_data_url)
        if previous_prefer_ocr is None:
            ctx.pop("_prefer_full_frame_ocr", None)
        else:
            ctx["_prefer_full_frame_ocr"] = previous_prefer_ocr
        return self._scene_discriminator_adjusted_score(ctx, image, frame_data_url, score)

    def _scene_discriminator_groups(self, ctx: dict[str, Any]) -> list[list[dict[str, Any]]]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        cache = ctx.get("_scene_discriminator_groups")
        if isinstance(cache, list):
            return cache
        records: list[dict[str, Any]] = []
        for raw_image_id, item in images.items():
            if not isinstance(item, dict):
                continue
            image_id = self._image_number(item)
            if image_id is None:
                try:
                    image_id = int(raw_image_id)
                except Exception:
                    continue
            for shape in View(item).get_shapes(include_groups=False):
                if not bool(shape.raw.get("discriminatorEnabled")):
                    continue
                records.append({"image_id": int(image_id), "image": item, "shape": shape.raw})

        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            shape = record["shape"]
            group_id = str(shape.get("discriminatorGroupId") or "").strip()
            if group_id:
                grouped.setdefault(f"group:{group_id}", []).append(record)
            grouped.setdefault(f"box:{self._shape_box_signature(shape)}", []).append(record)

        groups: list[list[dict[str, Any]]] = []
        seen: set[tuple[tuple[int, str], ...]] = set()
        for members in grouped.values():
            if len({int(member["image_id"]) for member in members}) < 2:
                continue
            signature = tuple(sorted((int(member["image_id"]), str(member["shape"].get("id") or "")) for member in members))
            if signature in seen:
                continue
            seen.add(signature)
            groups.append(members)
        ctx["_scene_discriminator_groups"] = groups
        return groups

    def _shape_box_signature(self, shape: dict[str, Any]) -> tuple[float, float, float, float]:
        return (
            round(float(shape.get("x") or 0), 4),
            round(float(shape.get("y") or 0), 4),
            round(float(shape.get("w") or 0), 4),
            round(float(shape.get("h") or 0), 4),
        )

    def _scene_discriminator_adjusted_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        base_score: float,
    ) -> float:
        image_id = self._image_number(image)
        if image_id is None:
            return base_score
        score = float(base_score or 0)
        for group in self._scene_discriminator_groups(ctx):
            if int(image_id) not in {int(member["image_id"]) for member in group}:
                continue
            scores = [
                (
                    self._scene_discriminator_member_score(ctx, member, frame_data_url),
                    int(member["image_id"]),
                    member["shape"],
                )
                for member in group
            ]
            scores = [item for item in scores if item[0] > 0]
            if not scores:
                continue
            scores.sort(key=lambda item: item[0], reverse=True)
            best_score, best_image_id, best_shape = scores[0]
            current_score = max((item[0] for item in scores if item[1] == int(image_id)), default=0.0)
            gap = best_score - current_score
            second_score = scores[1][0] if len(scores) > 1 else 0.0
            if best_image_id != int(image_id) and best_score >= 50 and gap >= 4:
                self._log(
                    "detail",
                    (
                        f"场景区分：#{image_id} 被 #{best_image_id}「{best_shape.get('title') or best_shape.get('id')}」"
                        f"压制，{current_score:.0f}% < {best_score:.0f}%"
                    ),
                )
                return 0.0
            if best_image_id == int(image_id) and best_score - second_score >= 4:
                score = max(score, best_score)
        return score

    def _scene_discriminator_member_score(self, ctx: dict[str, Any], member: dict[str, Any], frame_data_url: str) -> float:
        cache = ctx.setdefault("_scene_discriminator_score_cache", {})
        if not isinstance(cache, dict) or cache.get("frame") != frame_data_url:
            cache = {"frame": frame_data_url, "scores": {}}
            ctx["_scene_discriminator_score_cache"] = cache
        scores = cache.setdefault("scores", {})
        shape = member["shape"]
        cache_key = f"{member['image_id']}:{shape.get('id') or shape.get('title') or self._shape_box_signature(shape)}"
        if cache_key not in scores:
            scores[cache_key] = float(self._shape_score(ctx, member["image"], shape, frame_data_url, ocr_fallback=False) or 0)
        return float(scores.get(cache_key) or 0)

    def _popup_score(self, ctx: dict[str, Any], image: dict[str, Any], frame_data_url: str) -> float:
        return self._match_image_score(
            ctx,
            image,
            frame_data_url,
            self._popup_match_shapes(image),
            log_label="弹窗标识",
            scan_fallback=not bool(ctx.get("_popup_fast_match_only")),
        )

    def _match_image_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
        shapes: list[dict[str, Any]],
        *,
        log_label: str,
        scan_fallback: bool = True,
    ) -> float:
        scores: list[float] = []
        for shape in shapes:
            score = self._shape_score(ctx, image, shape, frame_data_url)
            if scan_fallback and score < 50 and self._shape_image_role(shape) != "off":
                try:
                    scan_score = float(self._run_match(ctx, image, shape, frame_data_url, scan=True, match_strategy="auto").get("similarity") or 0)
                    score = max(score, scan_score)
                except Exception as exc:
                    self._log("detail", f"{log_label}扫描失败：{image.get('title')} / {shape.get('title')}：{exc}")
            scores.append(score)
        scores = [score for score in scores if score > 0]
        if not scores:
            return 0
        scores.sort(reverse=True)
        return sum(scores[: min(3, len(scores))]) / min(3, len(scores))

    def _identify_scene(self, ctx: dict[str, Any], frame_data_url: str, keys: list[str] | None = None) -> tuple[str, float]:
        recognizer = self._scene_recognizer()
        priorities = recognizer.key_priorities or {}
        return recognizer.identify_scene_key(
            ctx,
            frame_data_url,
            keys=keys or list(priorities),
        )

    def _scene_matches(self, key: str, score: float) -> bool:
        return self._scene_recognizer().scene_matches_key(key, score)

    def _click_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None = None,
        *,
        match_result: dict[str, Any] | None = None,
    ) -> None:
        action_match_result: dict[str, Any] | None = None
        if frame_data_url:
            action_match_result = match_result if isinstance(match_result, dict) else self._match_shape(ctx, image, shape, frame_data_url)
            if not bool(action_match_result.get("matched")) and self._shape_ocr_fallback_enabled(shape):
                ocr_match_result = self._match_shape(ctx, image, shape, frame_data_url, condition="ocr")
                if bool(ocr_match_result.get("matched")):
                    action_match_result = ocr_match_result
            self._require_shape_match(action_match_result, shape)
        click_x, click_y = ActionPlanner().shape_center(image, shape)
        if action_match_result is not None:
            actual_x, actual_y = click_x, click_y
            resolved_box = action_match_result.get("resolved_box") or action_match_result.get("fixed_box")
            if isinstance(resolved_box, dict):
                actual_x = float(resolved_box.get("x") or 0) + float(resolved_box.get("w") or 0) / 2
                actual_y = float(resolved_box.get("y") or 0) + float(resolved_box.get("h") or 0) / 2
            self._log(
                "detail",
                (
                    f"点击标注「{shape.get('title') or shape.get('id')}」："
                    f"similarity={float(action_match_result.get('similarity') or 0):.0f}，"
                    f"ocr={str(action_match_result.get('ocr_text') or '')[:40]}，"
                    f"fixed_box={action_match_result.get('fixed_box')}，"
                    f"click=({actual_x:.1f},{actual_y:.1f})，"
                    f"raw=({click_x:.1f},{click_y:.1f})"
                ),
            )
            if isinstance(resolved_box, dict):
                current_image = dict(image)
                current_image["width"] = int(action_match_result.get("width") or image.get("width") or 0)
                current_image["height"] = int(action_match_result.get("height") or image.get("height") or 0)
                self._click_frame_point(ctx, current_image, actual_x, actual_y)
                return
        payload = ActionPlanner().click_shape_payload(image, shape)
        entry: Any = ctx["entry"]
        click_x = float(payload.get("x") or 0)
        click_y = float(payload.get("y") or 0)
        self._save_action_trace(
            ctx,
            image,
            {
                "kind": "click",
                "point": [click_x, click_y],
                "label": f"click #{self._image_number(image) or '?'} {shape.get('title') or shape.get('id') or ''}".strip(),
                "shape_title": shape.get("title"),
                "shape_id": shape.get("id"),
            },
            frame_data_url=frame_data_url,
        )
        (_click_game_window2_service(payload) if entry.mode == "local" else _click_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _click_scene_route_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None = None,
    ) -> None:
        try:
            self._click_shape(ctx, image, shape, frame_data_url)
            return
        except RuntimeError as exc:
            if not self._scene_route_fixed_click_fallback_allowed(image, shape, exc):
                raise
        x, y = ActionPlanner().shape_center(image, shape)
        self._log(
            "info",
            f"场景移动：#{self._image_number(image) or '?'}「{shape.get('title') or shape.get('id')}」图像定位失败，改按固定标注点击 ({x:.0f},{y:.0f})",
        )
        self._click_frame_point(ctx, image, x, y)

    def _scene_route_fixed_click_fallback_allowed(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        exc: RuntimeError,
    ) -> bool:
        if "定位" not in str(exc):
            return False
        image_number = self._image_number(image)
        shape_title = str(shape.get("title") or "")
        return (image_number, shape_title) in {
            (34, "打开下方菜单"),
            (69, "退出"),
        }

    def _wait_shape_match(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image: dict[str, Any],
        shape: dict[str, Any],
        *,
        timeout: float,
        label: str,
        min_similarity: float | None = None,
        require_resolved_box: bool = False,
    ):
        deadline = time.monotonic() + max(0.1, float(timeout or 0.1))
        last_similarity = 0.0
        last_ocr_text = ""

        def accept_result(result: dict[str, Any]) -> bool:
            similarity = float(result.get("similarity") or 0)
            if (
                not bool(result.get("matched"))
                and min_similarity is not None
                and similarity >= float(min_similarity)
                and (not require_resolved_box or isinstance(result.get("resolved_box") or result.get("fixed_box"), dict))
            ):
                result["matched"] = True
                if not isinstance(result.get("resolved_box"), dict) and isinstance(result.get("fixed_box"), dict):
                    result["resolved_box"] = result.get("fixed_box")
            return bool(result.get("matched"))

        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            for condition in self._shape_match_conditions(shape):
                result = self._match_shape(ctx, image, shape, frame, condition=condition)
                last_similarity = max(last_similarity, float(result.get("similarity") or 0))
                last_ocr_text = str(result.get("ocr_text") or last_ocr_text)[:40]
                if accept_result(result):
                    return frame, result
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"{label}：等待命中 {last_similarity:.0f}%",
                    phase="wait_shape_match",
                )
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
        raise RuntimeError(f"{label} 超时，最后 {last_similarity:.0f}% OCR={last_ocr_text}")

    def _click_frame_point(self, ctx: dict[str, Any], image: dict[str, Any], x: float, y: float) -> None:
        payload = ActionPlanner().click_point_payload(image, x, y)
        entry: Any = ctx["entry"]
        self._save_action_trace(
            ctx,
            image,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": f"click #{self._image_number(image) or '?'} ({float(x):.0f},{float(y):.0f})",
            },
        )
        (_click_game_window2_service(payload) if entry.mode == "local" else _click_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _drag_frame_point(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        duration_ms: int = 300,
    ) -> None:
        payload = ActionPlanner().drag_point_payload(
            image,
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms=duration_ms,
        )
        entry: Any = ctx["entry"]
        self._save_action_trace(
            ctx,
            image,
            {
                "kind": "drag",
                "start": [float(start_x), float(start_y)],
                "end": [float(end_x), float(end_y)],
                "duration_ms": int(duration_ms),
                "label": (
                    f"drag #{self._image_number(image) or '?'} "
                    f"({float(start_x):.0f},{float(start_y):.0f})->({float(end_x):.0f},{float(end_y):.0f})"
                ),
            },
        )
        (_drag_game_window2_service(payload) if entry.mode == "local" else _drag_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _shape_center(self, shape: dict[str, Any], image: dict[str, Any], frame_data_url: str | None = None, ctx: dict[str, Any] | None = None) -> tuple[float, float]:
        return ActionPlanner().shape_center(image, shape)

    def _click_generic_back(self, ctx: dict[str, Any]) -> None:
        image = self._image(ctx, "settings") or self._image(ctx, "world")
        if not image:
            return
        width, height = self._frame_size(image)
        self._click_frame_point(ctx, image, width * 0.085, height * 0.947)

    def _keyevents(self, ctx: dict[str, Any], keys: list[str]) -> None:
        payload = {"keys": keys}
        entry: Any = ctx["entry"]
        (_keyevent_game_window2_service(payload) if entry.mode == "local" else _keyevent_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _text(self, ctx: dict[str, Any], text: str) -> None:
        payload = {"text": text}
        entry: Any = ctx["entry"]
        (_text_game_window2_service(payload) if entry.mode == "local" else _text_remote_game_window2(entry, payload))
        self._clear_tick_frame(ctx)

    def _wait_for_scene(self, ctx: dict[str, Any], stop_event: threading.Event, keys: list[str], timeout: float, interval: float = 0.8) -> tuple[str, float, str]:
        deadline = time.time() + timeout
        last_key, last_score = "", 0.0
        last_frame = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame, keys)
            last_key, last_score, last_frame = key, score, frame
            if key in keys and self._scene_matches(key, score):
                return key, score, frame
            self._clear_tick_frame(ctx)
            time.sleep(interval)
        return last_key, last_score, last_frame

    def _wait_scene_id(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        target_scene_id: int,
        *,
        timeout: float,
        label: str = "等待场景",
    ):
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            scene_id, score = self._identify_scene_number(ctx, frame, [target_scene_id])
            last_scene_id, last_score = scene_id, score
            if scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"{label}：已到达 #{target_scene_id} {score:.0f}%")
                return target_scene_id, score
            with self._lock:
                self._status.update({
                    "phase": "wait_scene",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            if elapsed >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，未检测到 #{target_scene_id}，最后 {scene_text} {last_score:.0f}%")

    def _wait_mail_list_ready(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        timeout: float,
        label: str = "等待邮件列表",
    ):
        image121 = (ctx.get("images") or {}).get(121)
        marker_shape = self._find_shape(image121, "邮件标识") if isinstance(image121, dict) else None
        if not isinstance(image121, dict) or not marker_shape:
            return (yield from self._wait_scene_id(ctx, stop_event, 121, timeout=timeout, label=label))
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_marker_score = 0.0
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            scene_id, score = self._identify_scene_number(ctx, frame, [121])
            last_scene_id, last_score = scene_id, score
            marker_score = 0.0
            marker_matched = False
            if scene_id == 121:
                try:
                    marker_result = self._match_shape(ctx, image121, marker_shape, frame)
                    marker_score = float(marker_result.get("similarity") or 0)
                    marker_matched = bool(marker_result.get("matched"))
                except Exception as exc:
                    self._log("detail", f"{label}：邮件标识匹配失败：{exc}")
            last_marker_score = marker_score
            if scene_id == 121 and marker_matched:
                with self._lock:
                    self._status.update({"current_scene": 121, "updated_at": time.time()})
                self._log("success", f"{label}：已到达 #121 {score:.0f}%，邮件标识 {marker_score:.0f}%")
                return 121, score
            if self._close_mail_wait_popup_once(ctx, frame):
                yield BehaviorTreeStatus.RUNNING
                continue
            with self._lock:
                self._status.update(
                    {
                        "phase": "wait_mail_list_ready",
                        "current_scene": scene_id,
                        "message": (
                            f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} "
                            f"{score:.0f}%，邮件标识 {marker_score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    }
                )
            if elapsed >= timeout:
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                raise RuntimeError(f"{label} 超时，最后 {scene_text} {last_score:.0f}%，邮件标识 {last_marker_score:.0f}%")

    def _close_mail_wait_popup_once(self, ctx: dict[str, Any], frame_data_url: str) -> bool:
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            return False
        previous_log_context = self._set_log_context("guard", "close_popups")
        try:
            runtime = self._fanxiu_runtime(ctx, asset_tree_path, frame_data_url)
            closed = self._auto_close_popup_guard_step(
                runtime,
                allow_confirm_actions=False,
            )
        finally:
            self._restore_log_context(previous_log_context)
        if closed:
            runtime.clear_frame()
        return closed

    def _ocr_lines(self, frame_data_url: str) -> list[dict[str, Any]]:
        try:
            response = _recognize_data_annotation_ocr_frame(frame_data_url)
        except Exception as exc:
            self._log("detail", f"OCR 失败：{exc}")
            return []
        return [line.model_dump() for line in response.lines]

    def _ocr_lines_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
    ) -> list[dict[str, Any]]:
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding)
        if crop is None:
            return self._ocr_lines(frame_data_url)
        crop_data_url, offset_x, offset_y = crop
        lines = self._ocr_lines(crop_data_url)
        for line in lines:
            line["x"] = float(line.get("x") or 0) + offset_x
            line["y"] = float(line.get("y") or 0) + offset_y
        return lines

    def _crop_frame_data_url_for_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
    ) -> tuple[str, float, float] | None:
        try:
            header, encoded = frame_data_url.split(",", 1) if "," in frame_data_url else ("", frame_data_url)
            raw = base64.b64decode(encoded)
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as pil_image:
                width, height = pil_image.size
                boxes: list[dict[str, Any]] = []
                for title in shape_titles:
                    shape = self._find_shape(image, str(title))
                    if shape:
                        boxes.append(self._box(shape, image))
                if not boxes:
                    return None
                left = max(0, int(min(float(box.get("x") or 0) for box in boxes) - padding))
                top = max(0, int(min(float(box.get("y") or 0) for box in boxes) - padding))
                right = min(width, int(max(float(box.get("x") or 0) + float(box.get("w") or 0) for box in boxes) + padding))
                bottom = min(height, int(max(float(box.get("y") or 0) + float(box.get("h") or 0) for box in boxes) + padding))
                if right <= left or bottom <= top:
                    return None
                cropped = pil_image.crop((left, top, right, bottom))
                buffer = io.BytesIO()
                cropped.save(buffer, format="PNG")
                data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
                return data_url, float(left), float(top)
        except Exception as exc:
            self._log("detail", f"裁剪 OCR 区域失败，回退全图 OCR：{exc}")
            return None

    def _ocr_text(self, lines: list[dict[str, Any]]) -> str:
        return "".join(_sanitize_ocr_text(line.get("text")) for line in lines)

    def _text_in_shape(self, lines: list[dict[str, Any]], image: dict[str, Any] | None, shape_title: str) -> str:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return ""
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        fragments: list[str] = []
        for line in lines:
            cx = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                fragments.append(_sanitize_ocr_text(line.get("text")))
        return "".join(fragment for fragment in fragments if fragment)

    def _ocr_centers_in_shape(
        self,
        lines: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
    ) -> list[tuple[float, float, str]]:
        shape = self._find_shape(image, shape_title) if image else None
        if not shape or not image:
            return []
        box = self._box(shape, image)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        matches: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if include and not all(fragment in text for fragment in include):
                continue
            if exclude and any(fragment in text for fragment in exclude):
                continue
            line_x = float(line.get("x") or 0)
            line_w = float(line.get("w") or 0)
            cx = line_x + line_w / 2
            if include and line_w > 0 and text:
                target_fragment = next((fragment for fragment in include if fragment in text), "")
                if target_fragment:
                    index = text.find(target_fragment)
                    if index >= 0:
                        cx = line_x + line_w * ((index + len(target_fragment) / 2) / max(1, len(text)))
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _parse_fraction(self, text: str) -> tuple[int, int] | None:
        normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
        match = re.search(r"(\d{1,5})/(\d{1,5})", normalized)
        if not match:
            return None
        current = int(match.group(1))
        total = int(match.group(2))
        return (current, total) if total > 0 else None

    def _current_scene(self, ctx: dict[str, Any], keys: list[str] | None = None) -> tuple[str, float, str]:
        frame = self._screencap(ctx)
        key, score = self._identify_scene(ctx, frame, keys)
        if key and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": self.scene_ids.get(key), "updated_at": time.time()})
        return key, score, frame

    def _current_scene_number(self, ctx: dict[str, Any], frame: str | None = None) -> tuple[int | None, float, str]:
        frame_data_url = frame or self._screencap(ctx)
        scene_id, score = self._identify_scene_number(ctx, frame_data_url)
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
        return scene_id, score, frame_data_url

    def _scene_jump_intermediate_confirm_shape(
        self,
        current_image: dict[str, Any] | None,
        source_shape: dict[str, Any],
    ) -> dict[str, Any] | None:
        if current_image is None:
            return None
        source_title = str(source_shape.get("title") or "").strip()
        if source_title not in {"离开", "返回", "关闭", "退出"}:
            return None
        scene_title = str(current_image.get("title") or "").strip()
        if "离开" not in scene_title and "退出" not in scene_title:
            return None
        for shape in self._flatten_shapes(current_image.get("shapes")):
            if str(shape.get("title") or "").strip() in {"确认", "确定"}:
                return shape
        return None

    def _xianfu_home_text_is_scene(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if "仙侣居" not in normalized and "仙侶居" not in normalized:
            return False
        markers = ("玄机阁", "本命金身", "拜仙台", "寻仙台", "仙府管家")
        return any(marker in normalized for marker in markers)

    def _wait_scene_jump_result(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        *,
        source_scene_id: int,
        target_scene_id: int,
        edge: dict[str, Any],
        stop_event: threading.Event,
    ):
        shape = edge["shape"]
        expected_ids = list(edge.get("target_ids") or [])
        allows_self = source_scene_id in expected_ids
        timeout_seconds = 30.0 if allows_self else 60.0
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_frame = ""
        history: list[str] = []
        left_source = False
        handled_intermediate_scene_ids: set[int] = set()
        handled_world_side_leave = False

        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start

            scene_id, score = self._identify_scene_number(ctx, frame)
            last_scene_id, last_score, last_frame = scene_id, score, frame
            if scene_id is not None and scene_id != source_scene_id:
                left_source = True
            matched_expected, expected_score = self._identify_scene_number(ctx, frame, expected_ids)
            scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
            history.append(f"{elapsed:.1f}s {scene_text} {score:.0f}% expected={expected_score:.0f}% left={left_source}")
            if scene_id is not None and scene_id in expected_ids:
                if self._increment_scene_jump_target(shape, scene_id):
                    self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s")
                return scene_id
            if 171 in expected_ids:
                text = self._ocr_text(self._ocr_lines(frame))
                if self._xianfu_home_text_is_scene(text):
                    if self._increment_scene_jump_target(shape, 171):
                        self._write_asset_tree(asset_tree_path, tree)
                        ctx["images"] = self._index_images(tree)
                    self._log("info", f"场景跳转：#{source_scene_id} -> #171，{elapsed:.1f}s，OCR 兜底命中仙府主页")
                    return 171
            if scene_id is not None and scene_id not in handled_intermediate_scene_ids:
                current_image = (ctx.get("images") or {}).get(scene_id)
                confirm_shape = self._scene_jump_intermediate_confirm_shape(current_image, shape)
                if confirm_shape is not None:
                    confirm_title = str(confirm_shape.get("title") or "确认")
                    handled_intermediate_scene_ids.add(scene_id)
                    with self._lock:
                        self._status.update({
                            "phase": "go_scene_confirm",
                            "current_scene": scene_id,
                            "message": f"跳转确认：#{source_scene_id} -> #{target_scene_id}，点击 {confirm_title}",
                            "updated_at": time.time(),
                        })
                    self._log("action", f"场景跳转确认：#{scene_id}，点击 {confirm_title}")
                    self._click_shape(ctx, current_image, confirm_shape, frame)
                    continue
            if scene_id is None and not handled_world_side_leave:
                text = self._ocr_text(self._ocr_lines(frame))
                if (yield from self._leave_world_side_scene_if_present(
                    ctx,
                    stop_event,
                    frame,
                    text,
                    label="场景移动",
                )):
                    handled_world_side_leave = True
                    left_source = True
                    start = time.monotonic()
                    history.append(f"{elapsed:.1f}s unknown 右侧离开已处理")
                    continue
            with self._lock:
                self._status.update({
                    "phase": "go_scene_wait",
                    "current_scene": scene_id,
                    "message": f"跳转等待：#{source_scene_id} -> #{target_scene_id}，当前 {scene_text} {score:.0f}%",
                    "updated_at": time.time(),
                })

            if elapsed < timeout_seconds:
                continue

            if allows_self and last_scene_id == source_scene_id:
                if self._increment_scene_jump_target(shape, source_scene_id):
                    self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                self._log("info", f"场景跳转：#{source_scene_id} -> #{source_scene_id}，30s 保底确认自身")
                return source_scene_id

            if last_scene_id is None:
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    last_frame or frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=source_scene_id,
                    action_shape=shape,
                    elapsed_seconds=elapsed,
                    history=history,
                )

            if not left_source and last_scene_id == source_scene_id and not allows_self:
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    last_frame or frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=source_scene_id,
                    action_shape=shape,
                    elapsed_seconds=elapsed,
                    history=history,
                )

            raise RuntimeError(
                f"场景跳转实际到达 #{last_scene_id}，但该落点不在「{shape.get('title') or '未命名'}」的 sceneJumpTarget 中；"
                "Runtime 已中断，请人工确认并修正标注后重试"
            )

    def _go_scene_task(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        target_scene_id: int,
        stop_event: threading.Event,
    ):
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            tree = self._load_asset_tree(asset_tree_path)
            ctx["asset_tree"] = tree
            ctx["images"] = self._index_images(tree)

        for _step_index in range(24):
            self._raise_if_stopped(stop_event)
            route_candidate_ids = self._scene_route_candidate_ids(tree, target_scene_id)
            frame = self._screencap(ctx)
            current_scene_id, score = self._identify_scene_number(ctx, frame, route_candidate_ids)
            if current_scene_id is None:
                current_scene_id, score = self._identify_scene_number(ctx, frame)
            if current_scene_id is None:
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=None,
                    action_shape=None,
                    elapsed_seconds=0.0,
                    history=[f"起点识别 unknown {score:.0f}%"],
                )
            if current_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                return "success"

            route = self._find_scene_route(tree, current_scene_id, target_scene_id)
            if route is None:
                current_image = (ctx.get("images") or {}).get(current_scene_id)
                confirm_shape = self._scene_jump_intermediate_confirm_shape(current_image, {"title": "离开"})
                if confirm_shape is not None:
                    confirm_title = str(confirm_shape.get("title") or "确认")
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"场景移动确认：#{current_scene_id} -> #{target_scene_id}，点击 {confirm_title}",
                            phase="go_scene_confirm",
                            current_scene=current_scene_id,
                    )
                    self._log("action", f"场景移动确认：#{current_scene_id} -> #{target_scene_id}，点击 {confirm_title}")
                    self._click_scene_route_shape(ctx, current_image, confirm_shape, frame)
                    actual_scene_id = yield from self._wait_scene_jump_result(
                        ctx,
                        asset_tree_path,
                        tree,
                        source_scene_id=current_scene_id,
                        target_scene_id=target_scene_id,
                        edge={
                            "source_id": current_scene_id,
                            "image": current_image,
                            "shape": confirm_shape,
                            "target_ids": [target_scene_id],
                        },
                        stop_event=stop_event,
                    )
                    if actual_scene_id == target_scene_id:
                        with self._lock:
                            self._status.update({
                                "current_scene": target_scene_id,
                                "updated_at": time.time(),
                            })
                        self._log("success", f"到达目标场景 #{target_scene_id}")
                        return "success"
                    self._log("detail", f"场景移动：确认后实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")
                    continue
                raise RuntimeError(f"没有从 #{current_scene_id} 到 #{target_scene_id} 的可规划场景跳转路径")
            edge = route[0]
            image = edge["image"]
            shape = edge["shape"]
            shape_title = str(shape.get("title") or "未命名")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}",
                    phase="go_scene",
                    current_scene=current_scene_id,
                )
            self._log("action", f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}")
            self._click_scene_route_shape(ctx, image, shape, frame)
            actual_scene_id = yield from self._wait_scene_jump_result(
                ctx,
                asset_tree_path,
                tree,
                source_scene_id=current_scene_id,
                target_scene_id=target_scene_id,
                edge=edge,
                stop_event=stop_event,
            )
            if actual_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"到达目标场景 #{target_scene_id}")
                return "success"
            self._log("detail", f"场景移动：实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")

        raise RuntimeError(f"场景移动超过最大重规划步数，未到达 #{target_scene_id}")

    def _execute_hide_floating_window(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "hide_floating")
        icon = self._find_shape(image, "图标")
        target = self._find_shape(image, "隐藏区")
        if not image or not icon or not target:
            raise RuntimeError("#58 缺少「图标」或「隐藏区」标注")
        frame = self._screencap(ctx)
        score = self._shape_score(ctx, image, icon, frame)
        if score < self.scene_thresholds.get("hide_floating", 55):
            self._log("info", f"浮动窗未明显出现，图标匹配 {score:.0f}%")
            return
        start_x, start_y = self._shape_center(icon, image, frame, ctx)
        end_x, end_y = self._shape_center(target, image)
        with self._lock:
            self._set_status_locked("running", f"隐藏浮动窗：图标匹配 {score:.0f}%", phase="hide_floating", current_scene=58)
        self._drag_frame_point(ctx, image, start_x, start_y, end_x, end_y, duration_ms=350)
        time.sleep(0.8)

    def _align_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        for attempt in range(12):
            frame = self._screencap(ctx)
            key, score = self._identify_scene(ctx, frame, ["settings", "gift", "duplicated", "reward", "world_menu", "world"])
            matched = key if self._scene_matches(key, score) else ""
            self._log("detail", f"对齐 #49：当前 {matched or 'unknown'} {score:.0f}%")
            if matched:
                with self._lock:
                    scene_id = self.scene_ids.get(matched)
                    self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if matched == "settings":
                return
            if matched == "reward":
                self._log("detail", "对齐 #49：检测到 #81 过渡奖励，等待回到设置页")
                self._clear_tick_frame(ctx)
                time.sleep(1.0)
                continue
            if matched in {"gift", "duplicated"}:
                close_shape = self._find_shape(self._image(ctx, "gift"), "关闭窗口")
                if close_shape is None:
                    close_shape = self._find_shape(self._image(ctx, "gift"), "关闭", contains=True)
                if close_shape:
                    self._log("detail", f"对齐 #49：检测到 #{self.scene_ids.get(matched)}，点击关闭窗口")
                    self._click_shape(ctx, self._image(ctx, "gift"), close_shape, frame)
                    time.sleep(0.9)
                    continue
            if matched == "world_menu":
                settings_shape = self._find_shape(self._image(ctx, "world_menu"), "设置")
                if not settings_shape:
                    raise RuntimeError("#35 缺少「设置」标注")
                self._log("detail", "对齐 #49：确认 #35 后匹配点击浮动「设置」")
                self._click_shape(ctx, self._image(ctx, "world_menu"), settings_shape, frame)
                time.sleep(1.0)
                continue
            if matched == "world":
                open_shape = self._find_shape(self._image(ctx, "world"), "打开下方菜单")
                if not open_shape:
                    raise RuntimeError("#34 缺少「打开下方菜单」标注")
                self._log("detail", "对齐 #49：确认 #34 后点击打开下方菜单")
                self._click_shape(ctx, self._image(ctx, "world"), open_shape, frame)
                time.sleep(0.8)
                continue
            if attempt >= 3:
                self._log("detail", "对齐 #49：未知场景，点击画面返回按钮兜底")
                self._click_generic_back(ctx)
                time.sleep(0.9)
                continue
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            time.sleep(0.8)
        raise RuntimeError("无法对齐到 #49 设置页")

    def _open_gift(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        frame = self._screencap(ctx)
        image = self._image(ctx, "settings")
        shape = self._find_shape(image, "兑换礼包")
        if not image or not shape:
            raise RuntimeError("#49 缺少「兑换礼包」标注")
        box = self._box(shape, image)
        _width, height = self._frame_size(image)
        self._click_frame_point(
            ctx,
            image,
            float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
            float(box.get("y") or 0) - height * 0.02,
        )
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["gift"], 6)
        if key != "gift" or not self._scene_matches(key, score):
            raise RuntimeError("点击兑换礼包后未进入 #78")

    def _clear_and_type(self, ctx: dict[str, Any], code: str, stop_event: threading.Event) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "输入兑换码")
        if not image or not shape:
            raise RuntimeError("#78 缺少「输入兑换码」标注")
        self._click_shape(ctx, image, shape)
        time.sleep(0.25)
        self._keyevents(ctx, ["KEYCODE_MOVE_END", *["KEYCODE_DEL" for _ in range(40)]])
        time.sleep(0.25)
        self._raise_if_stopped(stop_event)
        self._text(ctx, code)
        time.sleep(0.35)

    def _submit_code(self, ctx: dict[str, Any], code: str) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "兑换")
        if not image or not shape:
            raise RuntimeError("#78 缺少「兑换」按钮标注")
        self._click_shape(ctx, image, shape)
        self._log("action", f"已提交：{code}")

    def _settle_after_submit(self, ctx: dict[str, Any], code: str, is_last: bool, stop_event: threading.Event) -> None:
        deadline = time.time() + 16.0
        plain_gift_since = 0.0
        last_seen = ""
        while time.time() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            overlay = self._detect_overlay(ctx, frame)
            if overlay == "duplicated":
                if is_last:
                    self._log("info", f"{code}：检测到 #82 已领取，关闭窗口")
                    self._close_gift_to_settings(ctx, stop_event)
                else:
                    self._log("info", f"{code}：检测到 #82 已领取，继续下一个")
                return
            if overlay == "reward":
                last_seen = "reward"
                self._clear_tick_frame(ctx)
                time.sleep(0.8)
                continue

            key, score = self._identify_scene(ctx, frame, ["settings", "gift"])
            if key == "settings" and self._scene_matches(key, score):
                self._log("info", f"{code}：已回到 #49")
                return
            if key == "gift" and self._scene_matches(key, score):
                last_seen = "gift"
                if plain_gift_since <= 0:
                    plain_gift_since = time.time()
                if time.time() - plain_gift_since >= 4.0:
                    if is_last:
                        self._log("info", f"{code}：提交后停留 #78，关闭窗口")
                        self._close_gift_to_settings(ctx, stop_event)
                    else:
                        self._log("info", f"{code}：提交后停留 #78，继续下一个")
                    return
            else:
                plain_gift_since = 0.0
                last_seen = key or last_seen
            self._clear_tick_frame(ctx)
            time.sleep(0.8)

        if is_last:
            self._log("info", f"{code}：等待结果超时，尝试对齐 #49")
            self._align_settings(ctx, stop_event)
        else:
            self._log("info", f"{code}：等待结果超时，继续下一个（最后看到 {last_seen or 'unknown'}）")

    def _detect_overlay(self, ctx: dict[str, Any], frame: str) -> str:
        duplicated = self._image(ctx, "duplicated")
        if duplicated:
            for title in ("礼包已被领取", "已被领取"):
                shape = self._find_shape(duplicated, title, contains=True)
                if shape and self._shape_score(ctx, duplicated, shape, frame) >= self.overlay_threshold:
                    return "duplicated"
        reward = self._image(ctx, "reward")
        if reward:
            for title in ("恭喜获得", "点击继续", "奖品"):
                shape = self._find_shape(reward, title, contains=True)
                if shape and self._shape_score(ctx, reward, shape, frame) >= 65:
                    return "reward"
        return ""

    def _process_code(self, ctx: dict[str, Any], code: str, is_last: bool, stop_event: threading.Event) -> None:
        frame = self._screencap(ctx)
        key, score = self._identify_scene(ctx, frame, ["settings", "gift"])
        if key == "settings" and self._scene_matches(key, score):
            with self._lock:
                self._set_status_locked("running", f"进入 #78 填写：{code}", phase="open_gift", current_scene=49)
            self._open_gift(ctx, stop_event)
        elif key != "gift" or not self._scene_matches(key, score):
            with self._lock:
                self._set_status_locked("running", f"重新对齐后填写：{code}", phase="align_settings")
            self._align_settings(ctx, stop_event)
            self._open_gift(ctx, stop_event)
        with self._lock:
            self._set_status_locked("running", f"输入礼包码：{code}", phase="type_code", current_scene=78)
        self._clear_and_type(ctx, code, stop_event)
        with self._lock:
            self._set_status_locked("running", f"提交礼包码：{code}", phase="submit_code")
        self._submit_code(ctx, code)
        with self._lock:
            self._set_status_locked("running", f"等待兑换结果：{code}", phase="wait_result")
        self._settle_after_submit(ctx, code, is_last, stop_event)

    def _close_gift_to_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "gift")
        shape = self._find_shape(image, "关闭窗口")
        if not image or not shape:
            raise RuntimeError("#78 缺少「关闭窗口」标注")
        self._click_shape(ctx, image, shape)
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["settings"], 2.5, interval=0.25)
        if key == "settings" and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": 49, "updated_at": time.time()})

    def _finish_from_settings(self, ctx: dict[str, Any], stop_event: threading.Event) -> None:
        image = self._image(ctx, "settings")
        shape = self._find_shape(image, "回退")
        if not image or not shape:
            raise RuntimeError("#49 缺少「回退」标注")
        with self._lock:
            self._status.update({"current_scene": 49, "updated_at": time.time()})
        self._click_shape(ctx, image, shape)
        key, score, _frame = self._wait_for_scene(ctx, stop_event, ["world", "world_menu", "settings"], 2.5, interval=0.25)
        if key and self._scene_matches(key, score):
            with self._lock:
                self._status.update({"current_scene": self.scene_ids.get(key), "updated_at": time.time()})



