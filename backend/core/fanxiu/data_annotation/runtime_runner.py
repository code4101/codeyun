from __future__ import annotations

import base64
import difflib
import hashlib
import inspect
import io
import json
import linecache
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable, Iterable, Mapping, Sequence

from pyxllib.prog import BehaviorTreeStatus, scheduled_task_payload_with_meta, select_due_scheduled_tasks

from backend.core.fanxiu.runtime.behavior_tree import (
    data_annotation_asset_tree_path as _core_data_annotation_asset_tree_path,
    ensure_fanxiu_runtime_jobs_registered,
    fanxiu_data_annotation_mail_scan_state_path as _core_data_annotation_mail_scan_state_path,
    fanxiu_data_annotation_runtime_state_path as _core_data_annotation_runtime_state_path,
    fanxiu_data_annotation_scheduler_settings_path as _core_data_annotation_scheduler_settings_path,
    fanxiu_data_annotation_scheduler_state_path as _core_data_annotation_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path as _core_data_annotation_world_facts_path,
    fanxiu_runtime_runner_status,
    fanxiu_runtime_task_label,
    _fanxiu_process_matches_service_owner,
)
from backend.core.fanxiu.runtime.capture_runtime import fanxiu_capture_runtime_service
from backend.core.fanxiu.data_annotation.jobs import (
    get_fanxiu_data_annotation_task_cell_definition as _data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.runtime import DataAnnotationRuntimeContainer as _DataAnnotationRuntimeContainer
from backend.core.fanxiu.data_annotation.recognition_tree import (
    build_recognition_tree_nodes,
    is_explicit_local_identity_only_scene,
    layer0_recognition_candidate_ids,
    runtime_root_scene_candidate_ids,
)
from backend.core.fanxiu.data_annotation.recognition_graph import (
    SceneGraphCandidate,
    choose_scene_from_graph,
)
from backend.core.fanxiu.data_annotation.scheduler import (
    data_annotation_world_facts_summary,
    preserve_data_annotation_scheduler_runtime_state,
    repair_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import default_data_annotation_scheduler_tasks as _default_data_annotation_scheduler_tasks
from backend.core.fanxiu.data_annotation.unknown_recovery import build_unknown_evidence, _reference_frame_similarity
from backend.core.fanxiu.data_annotation.popup_guard import PopupGuardMixin
from backend.core.fanxiu.data_annotation.state import (
    append_data_annotation_runtime_status_log,
    data_annotation_runtime_owner_message,
    CLOSE_POPUPS_GUARD_CONFIG_VERSION,
    close_popups_guard_enabled_from_status,
    data_annotation_scheduler_task_state as _data_annotation_scheduler_task_state,
    data_annotation_task_due as _data_annotation_task_due,
    initial_data_annotation_runtime_status,
    next_data_annotation_scheduler_time as _core_next_data_annotation_scheduler_time,
    normalize_data_annotation_scheduler_settings,
    normalize_data_annotation_runtime_guard_items,
    parse_data_annotation_task_time,
    persist_data_annotation_runtime_status as _persist_data_annotation_runtime_status_core,
    read_data_annotation_json as _read_data_annotation_json,
    read_data_annotation_runtime_status as _read_data_annotation_runtime_status_core,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json as _write_data_annotation_json,
)
from backend.core.fanxiu.mail.policy import (
    fanxiu_mail_action_policy_for_record,
    fanxiu_mail_action_policy_for_rewards,
    fanxiu_mail_desired_status_for_rewards,
    fanxiu_mail_desired_status_for_record,
    fanxiu_mail_rewards_from_payload,
    fanxiu_mail_rewards_unresolved,
    fanxiu_mail_visible_group_action_policy,
)
from backend.core.fanxiu.mail.runtime_store import (
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
from backend.core.fanxiu.runtime.mumu_control import (
    capture_mumu_window_frame,
    ensure_mumu_device_healthy,
    mark_mumu_device_startup_ready,
    mumu_device_startup_grace_state,
    record_mumu_adb_failure,
    screencap_mumu_adb_png,
    _encode_png_frame,
)
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.runtime.errors import FanxiuRuntimeError
from backend.core.temp_paths import codeyun_temp_root
from pyxllib.autogui import (
    ActionPlanner,
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
DEFAULT_SCROLL_RATIO = 0.5
DEFAULT_SCROLL_DURATION_SECONDS = 1.5
DEFAULT_LAYER0_WAIT_SECONDS = 30.0
OCCLUSION_ASSET_GROUP_TITLE = "遮挡"
LEGACY_OCCLUSION_ASSET_GROUP_TITLES = {"遮挡标记"}


@dataclass
class FloatingItemInstance:
    view: View
    template_shape: Shape
    anchor_shape: Shape
    anchor_box: dict[str, float]
    item_box: dict[str, float]
    text: str = ""

    def field_box(self, field_shape: Shape) -> dict[str, float]:
        template_box = _absolute_shape_box(self.template_shape)
        field_box = _absolute_shape_box(field_shape)
        offset_x = field_box["x"] - template_box["x"]
        offset_y = field_box["y"] - template_box["y"]
        return {
            "x": self.item_box["x"] + offset_x,
            "y": self.item_box["y"] + offset_y,
            "w": field_box["w"],
            "h": field_box["h"],
        }


def _absolute_shape_box(shape: Shape) -> dict[str, float]:
    box = shape.box()
    return {
        "x": float(box.get("x") or 0),
        "y": float(box.get("y") or 0),
        "w": float(box.get("w") or 0),
        "h": float(box.get("h") or 0),
    }
DEFAULT_SCROLL_SETTLE_SECONDS = 1.0
DEFAULT_SCROLL_UNCHANGED_THRESHOLD = 95.0
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
    seconds = _parse_xianfu_visit_cd_seconds(text)
    if seconds is not None:
        return seconds
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    normalized = normalized.replace("：", ":").replace("O", "0").replace("o", "0")
    if "免费抽取" in normalized or "免费领悟" in normalized:
        return 0
    return None


def _parse_daily_boss_cd_seconds(text: Any) -> int | None:
    return _parse_xianfu_visit_cd_seconds(text)


def _parse_daily_boss_cd_seconds_from_six_digits(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    digits = re.findall(r"\d", normalized)
    if len(digits) < 6:
        return None
    compact = "".join(digits[:6])
    hours = int(compact[:2])
    minutes = int(compact[2:4])
    seconds = int(compact[4:6])
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


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


@dataclass(frozen=True)
class _FanxiuWaitResult:
    matched: bool
    detail: str = ""
    score: float | None = None
    current_scene: int | None = None


@dataclass(frozen=True)
class _FanxiuWaitCondition:
    label: str
    check: Callable[["FanxiuRuntime", str], _FanxiuWaitResult]


class FanxiuRuntime(Runtime):
    """Fanxiu 行为树运行时上下文。

    业务层只感知 runtime；ctx、当前帧、资产树路径和底层点击/匹配实现都收敛在这里。
    """

    default_wait_click_timeout = 18.0
    default_wait_view_timeout = 20.0
    default_wait_condition_timeout = 12.0

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

    @property
    def payload(self) -> dict[str, Any]:
        payload = self.attrs.get("payload")
        return payload if isinstance(payload, dict) else {}

    def set_completion_message(self, message: str) -> None:
        self.attrs["completion_message"] = str(message or "").strip()

    def cur_frame(self, update: bool = False) -> str:
        if update:
            self.clear_frame()
        if isinstance(self.frame_data_url, str) and self.frame_data_url:
            self.runner._set_tick_frame(self.ctx, self.frame_data_url)
            return self.frame_data_url
        self.frame_data_url = self.runner._screencap(self.ctx)
        return self.frame_data_url

    def current_scene(
        self,
        views: Iterable[View | int] | None = None,
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ) -> tuple[int | None, float, str]:
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        view_ids: list[int] | None = None
        if views is not None:
            view_ids = [view.id if isinstance(view, View) else int(view) for view in views]
            view_ids = [view_id for view_id in view_ids if view_id is not None]
        scene_id, score = self.runner._identify_scene_number(self.ctx, frame, view_ids)
        return scene_id, float(score or 0.0), frame

    def ocr_text(self, frame_data_url: str | None = None, *, update: bool = False) -> str:
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        return self.runner._ocr_text(self.runner._cached_ocr_lines(self.ctx, frame))

    def ocr_lines(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        return self.runner._cached_ocr_lines(self.ctx, frame)

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
        min_score = self.attrs.get("popup_guard_min_score")
        candidate, score = self.runner._auto_close_popup_first_match(
            self.ctx,
            self.popup_candidates(),
            self.cur_frame(),
            min_score=float(min_score) if min_score is not None else None,
        )
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

    def resolve_view_selector(self, selector: View | int | str | dict[str, Any] | None) -> View | None:
        if selector is None:
            return None
        if isinstance(selector, View):
            return selector
        if isinstance(selector, dict):
            return View(selector)
        text = str(selector or "").strip()
        if text.startswith("#"):
            text = text[1:].strip()
        if isinstance(selector, int) or text.isdigit():
            return self.get_view(int(selector if isinstance(selector, int) else text))
        images = self.ctx.get("images")
        if isinstance(images, dict):
            if text in self.runner.scene_ids:
                image = images.get(self.runner.scene_ids[text])
                return View(image) if isinstance(image, dict) else None
            for image in images.values():
                if isinstance(image, dict) and str(image.get("title") or "").strip() == text:
                    return View(image)
        return None

    def view(self, selector: View | int | str | dict[str, Any]) -> View:
        view = self.resolve_view_selector(selector)
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError(f"无法解析帧选择器：{selector}")
        return view

    def shape(self, view: View | int | str, shape: Shape | str) -> Shape:
        return self.resolve_shape_selector(self.view(view), shape)

    def _selector_text(self, selector: Any) -> str:
        text = str(selector or "").strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1].strip()
        return text

    def _shape_selector_parts(self, selector: Any) -> list[str]:
        text = self._selector_text(selector)
        return [part.strip() for part in text.split("/") if part.strip()]

    def _shape_path(self, shape: Shape) -> str:
        parts: list[str] = []
        current: Shape | None = shape
        while isinstance(current, Shape):
            parts.append(str(current.title or current.raw.get("id") or "<shape>"))
            current = current.parent_shape
        return "[" + "/".join(reversed(parts)) + "]"

    def _view_ancestor_chain(self, view: View) -> list[View]:
        if not isinstance(view.raw, dict):
            return []
        view_id = self.runner._image_number(view.raw)
        if view_id is None:
            return []
        tree = self.ctx.get("asset_tree")
        if not isinstance(tree, list):
            return []
        path: list[dict[str, Any]] = []

        def visit(items: list[dict[str, Any]], ancestors: list[dict[str, Any]]) -> bool:
            for item in items:
                if not isinstance(item, dict):
                    continue
                next_ancestors = ancestors
                if item.get("type") == "image":
                    item_id = self.runner._image_number(item)
                    if item_id == view_id:
                        path.extend(ancestors)
                        return True
                    next_ancestors = [*ancestors, item]
                children = item.get("children")
                if isinstance(children, list) and visit([child for child in children if isinstance(child, dict)], next_ancestors):
                    return True
            return False

        visit([node for node in tree if isinstance(node, dict)], [])
        return [View(item) for item in reversed(path)]

    def _effective_shape_search_views(self, view: View) -> list[View]:
        return [view, *self._view_ancestor_chain(view)]

    def _resolve_own_shape_selector(self, view: View, parts: list[str]) -> Shape | None:
        if len(parts) > 1:
            candidates = [shape for shape in view.get_shapes(include_descendants=False) if shape.title == parts[0]]
            for title in parts[1:]:
                next_candidates: list[Shape] = []
                for candidate in candidates:
                    next_candidates.extend([child for child in candidate.children() if child.title == title])
                candidates = next_candidates
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                choices = "\n".join(f"- #{view.id or '?'} {self._shape_path(candidate)}" for candidate in candidates)
                raise RuntimeError(f"shape 选择器 [{'/'.join(parts)}] 命中多个目标：\n{choices}\n请使用更精确路径。")
            return None
        title = parts[0]
        candidates = [shape for shape in view.get_shapes() if shape.title == title]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            choices = "\n".join(f"- #{view.id or '?'} {self._shape_path(candidate)}" for candidate in candidates)
            raise RuntimeError(f"shape 选择器 [{title}] 命中多个目标：\n{choices}\n请使用精确路径。")
        return None

    def resolve_shape_selector(self, view: View, selector: Shape | str) -> Shape:
        if isinstance(selector, Shape):
            return selector
        parts = self._shape_selector_parts(selector)
        if not parts:
            raise RuntimeError("shape 选择器为空")
        for candidate_view in self._effective_shape_search_views(view):
            found = self._resolve_own_shape_selector(candidate_view, parts)
            if found is not None:
                if candidate_view.id != view.id:
                    self.runner._log(
                        "detail",
                        f"shape 继承：#{view.id or '?'} 使用父帧 #{candidate_view.id or '?'} {self._shape_path(found)}",
                    )
                return found
        raise RuntimeError(f"shape 选择器 [{'/'.join(parts)}] 未命中")

    def _view_has_scene_identity(self, view: View) -> bool:
        if not isinstance(view.raw, dict):
            return False
        return bool(self.runner._scene_identity_shapes(view.raw))

    def _shape_match_search_shape(self, shape: Shape) -> dict[str, Any]:
        raw = dict(shape.raw)
        parent = shape.parent_shape
        if not isinstance(parent, Shape):
            return raw
        parent_raw = parent.raw
        if self.runner._shape_image_role(raw) != "off":
            scan_box = {key: parent_raw.get(key) for key in ("x", "y", "w", "h") if key in parent_raw}
            if {"x", "y", "w", "h"}.issubset(scan_box):
                raw["_match_scan_box"] = scan_box
            raw["_wait_click_action_title"] = shape.title or shape.raw.get("id")
            return raw
        for key in ("x", "y", "w", "h"):
            if key in parent_raw:
                raw[key] = parent_raw.get(key)
        raw["_wait_click_action_title"] = shape.title or shape.raw.get("id")
        return raw

    def _current_view_from_frame(self) -> View | None:
        frame_data_url = self.cur_frame(update=False)
        scene_id, _score = self.runner._identify_scene_number(self.ctx, frame_data_url, None)
        if scene_id is not None:
            view = self.get_view(int(scene_id))
            if isinstance(view, View):
                return view
        return self.get_cur_view(update=False)

    def _runtime_source_info(self, action: str, source_expr: str = "") -> dict[str, Any]:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back
        own_file = Path(__file__).resolve()
        while frame is not None:
            filename = Path(frame.f_code.co_filename).resolve()
            if filename != own_file:
                line = linecache.getline(str(filename), frame.f_lineno).strip()
                try:
                    source_path = str(filename.relative_to(Path.cwd()))
                except ValueError:
                    source_path = str(filename)
                return {
                    "action": action,
                    "source_file": filename.name,
                    "source_path": source_path.replace("\\", "/"),
                    "source_line": int(frame.f_lineno),
                    "source_expr": source_expr or self._compact_runtime_source_expr(line),
                }
            frame = frame.f_back
        return {"action": action, "source_expr": source_expr}

    def _compact_runtime_source_expr(self, line: str) -> str:
        text = str(line or "").strip()
        text = re.sub(r"^.+?=\s*yield\s+from\s+", "", text)
        text = re.sub(r"^yield\s+from\s+", "", text)
        return text.replace("runtime.", "").strip()

    def _format_runtime_call(self, name: str, *args: Any) -> str:
        return f"{name}({', '.join(self._format_runtime_arg(arg) for arg in args)})"

    def _format_runtime_arg(self, value: Any) -> str:
        if isinstance(value, View):
            return str(value.id) if value.id is not None else repr(value.title)
        if isinstance(value, Shape):
            return repr(value.title or value.raw.get("id") or "shape")
        return repr(value)

    def _emit_runtime_action(
        self,
        message: str,
        *,
        phase: str,
        kind: str = "action",
        source_info: dict[str, Any] | None = None,
        current_scene: int | None = None,
    ) -> None:
        with self.runner._lock:
            self.runner._set_status_locked(
                "running",
                message,
                phase=phase,
                current_scene=current_scene,
            )
            self.runner._log_locked(kind, message, extra=source_info)

    def wait_click(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        **options: Any,
    ):
        source_info = options.pop("_source_info", None)
        if not isinstance(source_info, dict):
            source_info = self._runtime_source_info("wait_click", self._format_runtime_call("wait_click", frame, shape))
        timeout = float(self.default_wait_click_timeout if options.get("timeout") is None else options["timeout"])
        scene_timeout = float(timeout if options.get("scene_timeout") is None else options["scene_timeout"])
        x_ratio = float(0.5 if options.get("x_ratio") is None else options["x_ratio"])
        y_ratio = float(0.5 if options.get("y_ratio") is None else options["y_ratio"])
        view = self.resolve_view_selector(frame)
        if view is None:
            if frame is not None:
                raise RuntimeError(f"无法解析帧选择器：{frame}")
            current = self._current_view_from_frame()
            if not isinstance(current, View):
                raise RuntimeError("frame=None 时无法从当前上下文解析 view")
            view = current
        if self._view_has_scene_identity(view):
            self.runner._log("detail", f"wait_click：等待场景 #{view.id}「{view.title}」")
            waited = yield from self.wait_view(view, timeout=scene_timeout, label="wait_click")
            if isinstance(waited, View):
                view = waited
        else:
            self.runner._log("detail", f"wait_click：#{view.id or '?'} 无场景标识，跳过场景等待")
        target = self.resolve_shape_selector(view, shape)
        target_view = target.parent_view if isinstance(target.parent_view, View) and isinstance(target.parent_view.raw, dict) else view
        label = f"wait_click #{view.id or '?'} {self._shape_path(target)}"
        self._emit_runtime_action(
            f"点击 #{view.id or '?'}「{self._shape_path(target)}」",
            phase="runtime_wait_click",
            kind="waitClick",
            source_info=source_info,
            current_scene=view.id,
        )
        if self.runner._shape_has_runtime_click_condition(target.raw):
            match_shape = self._shape_match_search_shape(target)
            if isinstance(target.parent_shape, Shape):
                self.runner._log("detail", f"{label}：使用父区域 {self._shape_path(target.parent_shape)} 约束匹配")
            frame_data_url, match_result = yield from self.runner._wait_shape_match(
                self.ctx,
                self.stop_event or threading.Event(),
                target_view.raw,
                match_shape,
                timeout=timeout,
                label=label,
            )
            self.runner._click_shape(self.ctx, target_view.raw, target.raw, frame_data_url, match_result=match_result)
            self.clear_frame()
            return
        if bool(target.raw.get("floating")):
            self.runner._log(
                "warning",
                f"{label}：标注开启了浮动但没有图像/OCR条件，退化为固定坐标点击",
            )
        width, height = self.runner._frame_size(target_view.raw)
        click_x = (float(target.raw.get("x") or 0) + float(target.raw.get("w") or 0) * x_ratio) * width
        click_y = (float(target.raw.get("y") or 0) + float(target.raw.get("h") or 0) * y_ratio) * height
        self.runner._log("detail", f"{label}：固定点击 ({click_x:.1f},{click_y:.1f})")
        self.runner._click_frame_point(self.ctx, target_view.raw, click_x, click_y)
        self.clear_frame()

    def wait_clicks(self, steps: Iterable[tuple[View | int | str | None, Shape | str]]):
        for frame, shape in steps:
            source_info = self._runtime_source_info("wait_click", self._format_runtime_call("wait_click", frame, shape))
            yield from self.wait_click(frame, shape, _source_info=source_info)

    def wait_click_and_ocr(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        *,
        settle_seconds: float = 1.0,
        **options: Any,
    ) -> str:
        yield from self.wait_click(frame, shape, **options)
        yield from self.wait_action_settle(settle_seconds)
        return self.ocr_text(update=True)

    def wait_click_then_shape(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        target_frame: View | int | str,
        target_shape: Shape | str,
        *,
        settle_seconds: float = 1.0,
        timeout: float | None = None,
        label: str = "点击后等待目标",
        retry_if_source_remains: bool = False,
        max_clicks: int = 1,
        **options: Any,
    ) -> str:
        source_view = self.resolve_view_selector(frame)
        target_view = self.resolve_view_selector(target_frame)
        click_count = max(1, int(max_clicks or 1))
        wait_timeout = self.default_wait_condition_timeout if timeout is None else float(timeout)
        last_error: TimeoutError | None = None
        for attempt in range(1, click_count + 1):
            yield from self.wait_click(frame, shape, **options)
            yield from self.wait_action_settle(settle_seconds)
            try:
                return (yield from self.wait_shape(
                    target_frame,
                    target_shape,
                    timeout=wait_timeout,
                    label=label,
                ))
            except TimeoutError as exc:
                last_error = exc
                if not retry_if_source_remains or attempt >= click_count:
                    raise
                if not isinstance(source_view, View) or source_view.id is None:
                    raise
                if isinstance(target_view, View) and target_view.id == source_view.id:
                    raise
                scene_id, score, _frame = self.current_scene([source_view], update=True)
                if scene_id != source_view.id:
                    raise
                self.runner._log(
                    "warning",
                    (
                        f"{label}：点击后仍在源场景 #{source_view.id} {score:.0f}%，"
                        f"重试点击 {attempt + 1}/{click_count}"
                    ),
                )
        if last_error is not None:
            raise last_error
        raise TimeoutError(f"{label} 失败")

    def wait_click_then_view(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        *target_views: View | int | str | Sequence[View | int | str],
        settle_seconds: float = 1.0,
        timeout: float | None = None,
        label: str | None = None,
        wait_leave: bool = False,
        **options: Any,
    ) -> View:
        source_view = self.resolve_view_selector(frame)
        if source_view is None:
            if frame is not None:
                raise RuntimeError(f"无法解析帧选择器：{frame}")
            current = self._current_view_from_frame()
            if not isinstance(current, View):
                raise RuntimeError("frame=None 时无法从当前上下文解析 view")
            source_view = current
        target_shape = self.resolve_shape_selector(source_view, shape)
        target_ids: list[int] = []

        def append_target(target_view: View | int | str | Sequence[View | int | str]) -> None:
            if isinstance(target_view, View):
                if target_view.id is None:
                    raise RuntimeError(f"目标 view 缺少场景编号：{target_view.title}")
                target_ids.append(int(target_view.id))
            elif isinstance(target_view, Sequence) and not isinstance(target_view, (str, bytes, bytearray)):
                for item in target_view:
                    append_target(item)
            else:
                target_ids.append(int(str(target_view).lstrip("#")))

        for target_view in target_views:
            append_target(target_view)
        if not target_ids:
            tree = self.ctx.get("asset_tree")
            target_ids = self.runner._scene_jump_target_ids(tree if isinstance(tree, list) else [], target_shape.raw)
        if not target_ids and not wait_leave:
            raise RuntimeError(f"点击 #{source_view.id or '?'}「{self._shape_path(target_shape)}」后缺少目标场景；请显式传入目标或补 sceneJumpTarget")
        yield from self.wait_click(source_view, target_shape, **options)
        yield from self.wait_action_settle(settle_seconds)
        if not target_ids and wait_leave:
            return (yield from self.wait_leave_view(
                source_view,
                timeout=self.default_wait_condition_timeout if timeout is None else float(timeout),
                label=label or f"点击后等待离开 #{source_view.id or '?'}",
            ))
        wait_label = label or f"点击后等待目标场景 {','.join(f'#{target_id}' for target_id in target_ids)}"
        try:
            target_view = yield from self.wait_view(
                *target_ids,
                timeout=self.default_wait_condition_timeout if timeout is None else float(timeout),
                label=wait_label,
            )
            self._record_wait_click_then_view_landing(source_view, target_shape, target_view)
            return target_view
        except TimeoutError as exc:
            target_text = ",".join(f"#{target_id}" for target_id in target_ids)
            jump_target = str(target_shape.raw.get("sceneJumpTarget") or "").strip() or "未声明"
            raise TimeoutError(
                f"{wait_label} 失败：源场景=#{source_view.id or '?'}，shape={self._shape_path(target_shape)}，"
                f"期望目标={target_text}，sceneJumpTarget={jump_target}；{exc}"
            ) from exc

    def _record_wait_click_then_view_landing(self, source_view: View, shape: Shape, target_view: View) -> None:
        if target_view.id is None:
            return
        asset_tree_path = self.ctx.get("asset_tree_path")
        tree = self.ctx.get("asset_tree")
        if not isinstance(asset_tree_path, Path) or not isinstance(tree, list):
            return
        if self.runner._increment_scene_jump_target(shape.raw, int(target_view.id)):
            self.runner._write_asset_tree(asset_tree_path, tree)
            self.ctx["images"] = self.runner._index_images(tree)
            self.runner._log(
                "detail",
                (
                    f"场景跳转频数：#{source_view.id or '?'}「{self._shape_path(shape)}」"
                    f" -> #{int(target_view.id)} 已记录"
                ),
            )

    def wait_leave_view(
        self,
        view: View | int,
        *,
        timeout: float | None = None,
        label: str = "等待离开场景",
    ):
        source_id = view.id if isinstance(view, View) else int(view)
        if source_id is None:
            raise RuntimeError("缺少源场景编号")
        start = time.monotonic()
        wait_timeout = self.default_wait_condition_timeout if timeout is None else float(timeout)
        source_info = self._runtime_source_info("wait_leave_view", self._format_runtime_call("wait_leave_view", source_id))
        self._emit_runtime_action(
            f"{label}：等待离开 #{source_id}",
            phase="runtime_wait_leave_view",
            kind="wait",
            source_info=source_info,
            current_scene=source_id,
        )
        last_scene_id: int | None = source_id
        last_score = 0.0
        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.runner._clear_tick_frame(self.ctx)
            yield BehaviorTreeStatus.RUNNING
            scene_id, score, _frame = self.current_scene(update=True)
            last_scene_id, last_score = scene_id, score
            if scene_id != source_id:
                self.runner._log("success", f"{label}：已离开 #{source_id}，当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%")
                return self.get_view(scene_id) if scene_id is not None else view
            if time.monotonic() - start >= wait_timeout:
                raise TimeoutError(f"{label} 超时，仍在 #{source_id} {last_score:.0f}%")
            with self.runner._lock:
                self.runner._status.update({
                    "phase": "wait_leave_scene",
                    "current_scene": last_scene_id,
                    "message": f"{label}：仍在 #{source_id} {last_score:.0f}%",
                    "updated_at": time.time(),
                })

    def wait_click_then_any(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        conditions: Mapping[str, _FanxiuWaitCondition],
        *,
        settle_seconds: float = 1.0,
        timeout: float | None = None,
        label: str = "点击后等待结果",
        **options: Any,
    ) -> str:
        yield from self.wait_click(frame, shape, **options)
        yield from self.wait_action_settle(settle_seconds)
        return (yield from self.wait_any(
            conditions,
            timeout=timeout,
            label=label,
        ))

    def _go_scene_movement(
        self,
        scene: View | int,
        *,
        layer0_wait_seconds: float | None = None,
        wait_seconds: float | None = None,
        wait_time: float | None = None,
        api_name: str,
    ) -> Any:
        target_scene_id = scene.id if isinstance(scene, View) else int(scene)
        if layer0_wait_seconds is None and wait_seconds is not None:
            layer0_wait_seconds = wait_seconds
        if layer0_wait_seconds is None and wait_time is not None:
            layer0_wait_seconds = wait_time
        if not isinstance(self.asset_tree_path, Path):
            raise RuntimeError("缺少场景移动资产树路径")
        self._emit_runtime_action(
            f"前往 #{target_scene_id}",
            phase="runtime_go_scene" if api_name == "go_scene" else "runtime_goto_view",
            kind="goto",
            source_info=self._runtime_source_info(api_name, self._format_runtime_call(api_name, target_scene_id)),
            current_scene=target_scene_id,
        )
        stop_event = self.stop_event or threading.Event()
        go_scene_task = self.runner._go_scene_task
        go_scene_kwargs: dict[str, Any] = {}
        go_scene_parameters = inspect.signature(go_scene_task).parameters
        if (
            "layer0_wait_seconds" in go_scene_parameters
            or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in go_scene_parameters.values())
        ):
            go_scene_kwargs["layer0_wait_seconds"] = layer0_wait_seconds
        result = go_scene_task(
            self.ctx,
            self.asset_tree_path,
            target_scene_id,
            stop_event,
            **go_scene_kwargs,
        )
        status = (yield from result) if isinstance(result, GeneratorType) else result
        if str(status or "").lower() in {"error", "failure", "failed"}:
            raise RuntimeError(f"前往 #{target_scene_id} 失败")
        return status

    def goto_view(
        self,
        view: View | int,
        *,
        layer0_wait_seconds: float | None = None,
        wait_seconds: float | None = None,
        wait_time: float | None = None,
    ) -> Any:
        return (yield from self._go_scene_movement(
            view,
            layer0_wait_seconds=layer0_wait_seconds,
            wait_seconds=wait_seconds,
            wait_time=wait_time,
            api_name="goto_view",
        ))

    def go_scene(
        self,
        scene: View | int,
        *,
        layer0_wait_seconds: float | None = None,
        wait_seconds: float | None = None,
        wait_time: float | None = None,
    ) -> Any:
        return (yield from self._go_scene_movement(
            scene,
            layer0_wait_seconds=layer0_wait_seconds,
            wait_seconds=wait_seconds,
            wait_time=wait_time,
            api_name="go_scene",
        ))

    def _wait_scene_core(
        self,
        *scenes: View | int,
        timeout: float | None = None,
        layer0_wait_seconds: float | None = None,
        wait_seconds: float | None = None,
        wait_time: float | None = None,
        label: str = "等待场景",
        api_name: str,
    ):
        view_ids = [scene.id if isinstance(scene, View) else int(scene) for scene in scenes]
        view_ids = [view_id for view_id in view_ids if view_id is not None]
        if not view_ids:
            return self.current_scene(update=True)
        if layer0_wait_seconds is None and wait_seconds is not None:
            layer0_wait_seconds = wait_seconds
        if layer0_wait_seconds is None and wait_time is not None:
            layer0_wait_seconds = wait_time
        images = self.ctx.get("images")
        target_views_by_id: dict[int, View] = {int(scene.id): scene for scene in scenes if isinstance(scene, View) and scene.id is not None}
        if isinstance(images, dict):
            for view_id in view_ids:
                image = images.get(view_id)
                if isinstance(image, dict):
                    target_views_by_id.setdefault(int(view_id), View(image))
        start = time.monotonic()
        wait_timeout = self.default_wait_view_timeout if timeout is None else float(timeout)
        preferred_wait_seconds = max(
            0.0,
            float(
                layer0_wait_seconds
                if layer0_wait_seconds is not None
                else (DEFAULT_LAYER0_WAIT_SECONDS if api_name == "wait_scene" else wait_timeout)
            ),
        )
        source_info = self._runtime_source_info(api_name, self._format_runtime_call(api_name, *view_ids))
        self._emit_runtime_action(
            f"{label}：等待 {'/'.join(f'#{view_id}' for view_id in view_ids)}",
            phase="runtime_wait_scene" if api_name == "wait_scene" else "runtime_wait_view",
            kind="wait",
            source_info=source_info,
            current_scene=view_ids[0] if len(view_ids) == 1 else None,
        )
        last_scene_id: int | None = None
        last_score = 0.0
        previous_frame: str | None = None
        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.runner._clear_tick_frame(self.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame_started_at = time.monotonic()
            frame = self.cur_frame(update=True)
            frame_elapsed = time.monotonic() - frame_started_at
            elapsed = time.monotonic() - start
            identify_started_at = time.monotonic()
            in_layer0_window = elapsed < preferred_wait_seconds
            for view_id in view_ids:
                target_view = target_views_by_id.get(int(view_id))
                if isinstance(target_view, View) and target_view.is_match(self):
                    matched_scene_id = int(view_id)
                    matched_score = float(self.runner.scene_threshold)
                    if self.runner._scene_number_ocr_confirmed(self.ctx, frame, matched_scene_id, matched_score):
                        scene_id, score = matched_scene_id, matched_score
                        break
            else:
                preferred_ids = view_ids if in_layer0_window else None
                scene_id, score = self.runner._identify_scene_number(self.ctx, frame, preferred_ids)
            identify_elapsed = time.monotonic() - identify_started_at
            if elapsed >= 10.0 or frame_elapsed + identify_elapsed >= 1.0:
                self.runner._log(
                    "detail",
                    (
                        f"{label}：{api_name}轮询 elapsed={elapsed:.1f}s "
                        f"frame={frame_elapsed:.2f}s identify={identify_elapsed:.2f}s "
                        f"scene={'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%"
                    ),
                )
            last_scene_id, last_score = scene_id, score
            if scene_id in view_ids:
                with self.runner._lock:
                    self.runner._status.update({
                        "current_scene": scene_id,
                        "updated_at": time.time(),
                    })
                self.runner._log("success", f"{label}：已到达 #{scene_id} {score:.0f}%")
                return target_views_by_id.get(int(scene_id)) or self.get_view(scene_id) or scene_id
            if elapsed >= float(wait_timeout):
                scene_text = f"#{last_scene_id}" if last_scene_id is not None else "unknown"
                expected = "/".join(f"#{view_id}" for view_id in view_ids)
                diagnostic = ""
                try:
                    evidence = build_unknown_evidence(
                        self.runner,
                        self.ctx,
                        frame,
                        label=label,
                        expected_scene_ids=view_ids,
                        last_scene_id=last_scene_id,
                        last_score=last_score,
                        previous_frame_data_url=previous_frame,
                    )
                    report_suffix = f"，证据={evidence.report_path}" if evidence.report_path else ""
                    frame_suffix = f"，截图={evidence.frame_path}" if evidence.frame_path else ""
                    diagnostic = f"；unknown诊断={evidence.classification}：{evidence.suggestion}{frame_suffix}{report_suffix}"
                    self.runner._log("warning", f"{label}：{diagnostic.lstrip('；')}")
                except Exception as exc:
                    self.runner._log("warning", f"{label}：unknown诊断生成失败：{exc}")
                raise TimeoutError(f"{label} 超时，未检测到 {expected}，最后 {scene_text} {last_score:.0f}%{diagnostic}")
            if self.runner._auto_close_popup_guard_step(self):
                self.clear_frame()
                continue
            if scene_id is None and any(int(view_id) in {34, 69} for view_id in view_ids):
                target_scene_id = 34 if 34 in {int(view_id) for view_id in view_ids} else 69
                if self.runner._recover_unknown_start_to_world(self.ctx, frame, target_scene_id=target_scene_id):
                    self.clear_frame()
                    previous_frame = None
                    continue
            with self.runner._lock:
                self.runner._status.update({
                    "phase": "wait_scene",
                    "current_scene": scene_id,
                    "message": f"{label}：当前 {'#' + str(scene_id) if scene_id is not None else 'unknown'} {score:.0f}%",
                    "updated_at": time.time(),
                })
            previous_frame = frame

    def wait_view(
        self,
        *views: View | int,
        timeout: float | None = None,
        label: str = "等待场景",
    ):
        return (yield from self._wait_scene_core(*views, timeout=timeout, label=label, api_name="wait_view"))

    def wait_scene(
        self,
        *scenes: View | int,
        timeout: float | None = None,
        layer0_wait_seconds: float | None = None,
        wait_seconds: float | None = None,
        wait_time: float | None = None,
        label: str = "等待场景",
    ):
        return (yield from self._wait_scene_core(
            *scenes,
            timeout=timeout,
            layer0_wait_seconds=layer0_wait_seconds,
            wait_seconds=wait_seconds,
            wait_time=wait_time,
            label=label,
            api_name="wait_scene",
        ))

    def wait_view_id(
        self,
        *views: View | int,
        timeout: float | None = None,
        label: str = "等待场景",
    ):
        waited = yield from self.wait_view(*views, timeout=timeout, label=label)
        if isinstance(waited, View) and waited.id is not None:
            return int(waited.id), 100.0
        try:
            return int(waited), 100.0
        except (TypeError, ValueError):
            view_ids = [view.id if isinstance(view, View) else int(view) for view in views]
            scene_id, score, _frame = self.current_scene(view_ids)
            return scene_id, score

    def view_visible(self, view: View | int, *, threshold: float = 80.0) -> _FanxiuWaitCondition:
        target = view if isinstance(view, View) else self.get_view(int(view))
        if not isinstance(target, View):
            raise RuntimeError(f"无法解析等待场景：{view}")
        view_id = target.id
        label = f"#{view_id}「{target.title}」" if view_id is not None else f"View「{target.title}」"

        def check(runtime: "FanxiuRuntime", frame: str) -> _FanxiuWaitResult:
            if target.is_match(runtime):
                return _FanxiuWaitResult(True, f"{label} View 已匹配", 100.0, view_id)
            scene_id, score = runtime.runner._identify_scene_number(runtime.ctx, frame, [int(view_id)] if view_id is not None else None)
            score = float(score or 0.0)
            matched = scene_id == view_id and score >= float(threshold)
            return _FanxiuWaitResult(matched, f"{label} {score:.0f}%", score, scene_id)

        return _FanxiuWaitCondition(label=label, check=check)

    def shape_visible(self, view: View | int, shape: Shape | str, *, threshold: float = 80.0) -> _FanxiuWaitCondition:
        target_view = view if isinstance(view, View) else self.get_view(int(view))
        if not isinstance(target_view, View) or not isinstance(target_view.raw, dict):
            raise RuntimeError(f"无法解析等待 shape 所在场景：{view}")
        target_shape = self.resolve_shape_selector(target_view, shape)
        view_id = target_view.id
        label = f"#{view_id or '?'} {self._shape_path(target_shape)}"

        def check(runtime: "FanxiuRuntime", frame: str) -> _FanxiuWaitResult:
            score = float(runtime.runner._shape_score(runtime.ctx, target_view.raw, target_shape.raw, frame) or 0.0)
            return _FanxiuWaitResult(score >= float(threshold), f"{label} {score:.0f}%", score, view_id)

        return _FanxiuWaitCondition(label=label, check=check)

    def ocr_contains(
        self,
        *,
        all_of: Iterable[str] = (),
        any_of: Iterable[str] = (),
        normalize: bool = True,
        label: str = "OCR 文本",
    ) -> _FanxiuWaitCondition:
        required = [str(item) for item in all_of if str(item)]
        optional = [str(item) for item in any_of if str(item)]

        def clean(text: str) -> str:
            compact = re.sub(r"\s+", "", _sanitize_ocr_text(text)) if normalize else text
            return compact

        required_clean = [clean(item) for item in required]
        optional_clean = [clean(item) for item in optional]

        def check(runtime: "FanxiuRuntime", frame: str) -> _FanxiuWaitResult:
            text = runtime.runner._ocr_text(runtime.runner._cached_ocr_lines(runtime.ctx, frame))
            haystack = clean(text)
            required_ok = all(item in haystack for item in required_clean)
            optional_ok = True if not optional_clean else any(item in haystack for item in optional_clean)
            matched = required_ok and optional_ok
            display = " ".join(required + optional) or label
            return _FanxiuWaitResult(matched, f"{label} {'命中' if matched else '未命中'}：{display}")

        return _FanxiuWaitCondition(label=label, check=check)

    def ocr_matches(
        self,
        predicate: Callable[[str], bool],
        *,
        label: str = "OCR 文本",
        preview_chars: int = 60,
    ) -> _FanxiuWaitCondition:
        def check(runtime: "FanxiuRuntime", frame: str) -> _FanxiuWaitResult:
            text = runtime.runner._ocr_text(runtime.runner._cached_ocr_lines(runtime.ctx, frame))
            matched = bool(predicate(text))
            preview = _sanitize_ocr_text(text)[: max(0, int(preview_chars))]
            return _FanxiuWaitResult(matched, f"{label} {'命中' if matched else '未命中'}：{preview}")

        return _FanxiuWaitCondition(label=label, check=check)

    def wait_view_or_ocr(
        self,
        view: View | int,
        predicate: Callable[[str], bool],
        *,
        view_threshold: float = 80.0,
        timeout: float | None = None,
        label: str = "等待场景或 OCR",
    ):
        result = yield from self.wait_any(
            {
                "scene": self.view_visible(view, threshold=view_threshold),
                "text": self.ocr_matches(predicate, label=f"{label} OCR"),
            },
            timeout=timeout,
            label=label,
        )
        target_view = view.id if isinstance(view, View) else int(view)
        scene_id, score, _frame = self.current_scene([target_view])
        if scene_id != target_view:
            scene_id, score = target_view, 0.0
        return result, scene_id, score

    def all_of(self, *conditions: _FanxiuWaitCondition, label: str | None = None) -> _FanxiuWaitCondition:
        condition_list = [condition for condition in conditions if isinstance(condition, _FanxiuWaitCondition)]
        if not condition_list:
            raise RuntimeError("all_of 至少需要一个等待条件")
        title = label or " + ".join(condition.label for condition in condition_list)

        def check(runtime: "FanxiuRuntime", frame: str) -> _FanxiuWaitResult:
            details: list[str] = []
            score: float | None = None
            current_scene: int | None = None
            for condition in condition_list:
                result = condition.check(runtime, frame)
                details.append(result.detail or condition.label)
                if result.score is not None:
                    score = result.score
                if result.current_scene is not None:
                    current_scene = result.current_scene
                if not result.matched:
                    return _FanxiuWaitResult(False, "；".join(details), score, current_scene)
            return _FanxiuWaitResult(True, "；".join(details), score, current_scene)

        return _FanxiuWaitCondition(label=title, check=check)

    def wait_any(
        self,
        conditions: Mapping[str, _FanxiuWaitCondition],
        *,
        timeout: float | None = None,
        label: str = "等待任一条件",
        interval: float = 0.5,
    ):
        if not conditions:
            raise RuntimeError("wait_any 至少需要一个等待条件")
        wait_timeout = self.default_wait_condition_timeout if timeout is None else float(timeout)
        source_info = self._runtime_source_info("wait_any", "wait_any(...)")
        self._emit_runtime_action(
            f"{label}：等待 {' / '.join(str(key) for key in conditions.keys())}",
            phase="runtime_wait_any",
            kind="wait",
            source_info=source_info,
        )
        start = time.monotonic()
        last_details: dict[str, str] = {}
        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.runner._clear_tick_frame(self.ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self.cur_frame(update=True)
            for key, condition in conditions.items():
                result = condition.check(self, frame)
                last_details[str(key)] = result.detail or condition.label
                if result.matched:
                    with self.runner._lock:
                        self.runner._status.update({
                            "phase": "wait_any",
                            "current_scene": result.current_scene,
                            "message": f"{label}：命中 {key}，{last_details[str(key)]}",
                            "updated_at": time.time(),
                        })
                    self.runner._log("success", self.runner._status["message"])
                    return key
            if time.monotonic() - start >= max(1.0, float(wait_timeout)):
                detail = "；".join(f"{key}: {value}" for key, value in last_details.items())
                raise TimeoutError(f"{label} 超时：{detail or '无匹配结果'}")
            if self.runner._auto_close_popup_guard_step(self):
                self.clear_frame()
                continue
            if interval > 0:
                stop_event = self.stop_event or threading.Event()
                stop_event.wait(float(interval))

    def match_shape(self, shape: Shape) -> bool:
        view = shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            return False
        frame = self.cur_frame()
        entry = self.ctx.get("entry")
        source_view = shape.parent_view if isinstance(shape.parent_view, View) and isinstance(shape.parent_view.raw, dict) else view
        if hasattr(entry, "mode"):
            result: dict[str, Any] = {"matched": False}
            for condition in self.runner._shape_match_conditions(shape.raw):
                result = self.runner._match_shape(self.ctx, source_view.raw, shape.raw, frame, condition=condition)
                if bool(result.get("matched")):
                    break
            self._shape_match_results[id(shape.raw)] = result
            return bool(result.get("matched"))

        score = float(self.runner._shape_score(self.ctx, source_view.raw, shape.raw, frame) or 0)
        matched = score >= float(self.runner.overlay_threshold)
        if not matched:
            self._shape_match_results.pop(id(shape.raw), None)
        return matched

    def click_shape(
        self,
        view: View | int | str | dict[str, Any],
        shape: Shape | str | dict[str, Any],
        *,
        frame_data_url: str | None = None,
        match_result: dict[str, Any] | None = None,
    ) -> Any:
        target_view = self.view(view)
        if not isinstance(target_view.raw, dict):
            raise RuntimeError("缺少可点击 view")
        target_shape = shape if isinstance(shape, Shape) else None
        raw_shape = target_shape.raw if isinstance(target_shape, Shape) else shape
        if not isinstance(raw_shape, dict):
            target_shape = self.resolve_shape_selector(target_view, raw_shape)
            raw_shape = target_shape.raw
        source_view = (
            target_shape.parent_view
            if isinstance(target_shape, Shape) and isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict)
            else target_view
        )
        if isinstance(target_shape, Shape):
            self.last_clicked_shape = target_shape
        action_match_result = match_result
        if action_match_result is None and isinstance(target_shape, Shape):
            action_match_result = self._shape_match_results.get(id(target_shape.raw))
        frame = frame_data_url
        if not isinstance(frame, str) or not frame:
            frame = self.cur_frame() if action_match_result is not None or self.runner._shape_click_needs_frame(raw_shape) else None
        try:
            result = self.runner._click_shape(
                self.ctx,
                source_view.raw,
                raw_shape,
                frame,
                match_result=action_match_result,
            )
        except RuntimeError as exc:
            if not self.runner._scene_route_fixed_click_fallback_allowed(source_view.raw, raw_shape, exc):
                raise
            x, y = ActionPlanner().shape_center(source_view.raw, raw_shape)
            self.runner._log(
                "info",
                f"Runtime View：#{self.runner._image_number(source_view.raw) or '?'}「{raw_shape.get('title') or raw_shape.get('id')}」图像定位失败，改按固定标注点击 ({x:.0f},{y:.0f})",
            )
            result = self.runner._click_frame_point(self.ctx, source_view.raw, x, y)
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

    def click_frame_point(self, view: View | int | str | dict[str, Any], x: float, y: float) -> Any:
        target_view = self.view(view)
        self._emit_runtime_action(
            f"固定点击 #{target_view.id or '?'} ({float(x):.0f},{float(y):.0f})",
            phase="runtime_click_point",
            kind="click",
            current_scene=target_view.id,
        )
        result = self.runner._click_frame_point(self.ctx, target_view.raw, x, y)
        self.clear_frame()
        return result

    def click_shape_center(
        self,
        view: View | int | str,
        shape: Shape | str,
        *,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> Any:
        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        source_view = target_shape.parent_view if isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict) else target_view
        width, height = self.runner._frame_size(source_view.raw)
        click_x = (float(target_shape.raw.get("x") or 0) + float(target_shape.raw.get("w") or 0) * float(x_ratio)) * width
        click_y = (float(target_shape.raw.get("y") or 0) + float(target_shape.raw.get("h") or 0) * float(y_ratio)) * height
        self._emit_runtime_action(
            f"固定点击 #{target_view.id or '?'}「{self._shape_path(target_shape)}」",
            phase="runtime_click_shape",
            kind="click",
            current_scene=target_view.id,
        )
        result = self.runner._click_frame_point(self.ctx, source_view.raw, click_x, click_y)
        self.clear_frame()
        return result

    def long_press_shape(
        self,
        view: View | int | str,
        shape: Shape | str,
        *,
        duration: float = 3.0,
    ) -> Any:
        """Long-press a named shape; business code never handles coordinates."""
        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        source_view = (
            target_shape.parent_view
            if isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict)
            else target_view
        )
        if not self.match_shape(target_shape):
            raise RuntimeError(
                f"长按前未匹配 #{target_view.id or '?'}「{self._shape_path(target_shape)}」"
            )
        x, y = ActionPlanner().shape_center(source_view.raw, target_shape.raw)
        duration_ms = max(50, min(3000, int(float(duration) * 1000)))
        self._emit_runtime_action(
            f"长按 #{target_view.id or '?'}「{self._shape_path(target_shape)}」 {duration_ms / 1000:.1f}s",
            phase="runtime_long_press_shape",
            kind="long_press",
            current_scene=target_view.id,
        )
        result = self.runner._drag_frame_point(
            self.ctx,
            source_view.raw,
            x,
            y,
            x,
            y,
            duration_ms=duration_ms,
        )
        self.clear_frame()
        return result

    def click_shape_center_then_view(
        self,
        view: View | int | str,
        shape: Shape | str,
        *target_views: View | int | str | Sequence[View | int | str],
        settle_seconds: float = 1.0,
        timeout: float | None = None,
        label: str | None = None,
    ):
        source_view = self.view(view)
        target_ids: list[int] = []

        def append_target(target_view: View | int | str | Sequence[View | int | str]) -> None:
            if isinstance(target_view, View):
                if target_view.id is None:
                    raise RuntimeError(f"目标 view 缺少场景编号：{target_view.title}")
                target_ids.append(int(target_view.id))
            elif isinstance(target_view, Sequence) and not isinstance(target_view, (str, bytes, bytearray)):
                for item in target_view:
                    append_target(item)
            else:
                target_ids.append(int(str(target_view).lstrip("#")))

        for target_view in target_views:
            append_target(target_view)
        if not target_ids:
            raise RuntimeError(f"固定点击 #{source_view.id or '?'}「{shape}」后缺少目标场景；请显式传入目标")
        self.click_shape_center(source_view, shape)
        yield from self.wait_action_settle(settle_seconds)
        wait_label = label or f"固定点击后等待目标场景 {','.join(f'#{target_id}' for target_id in target_ids)}"
        return (yield from self.wait_view(
            *target_ids,
            timeout=self.default_wait_condition_timeout if timeout is None else float(timeout),
            label=wait_label,
        ))

    def ocr_row_clicks_in_shape(
        self,
        view: View | int | str,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        frame_data_url: str | None = None,
    ) -> list[tuple[float, float, str]]:
        target_view = self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        lines = self.runner._cached_ocr_lines(self.ctx, frame)
        target_shape = self.resolve_shape_selector(target_view, shape_title)
        source_view = target_shape.parent_view if isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict) else target_view
        return self.runner._ocr_row_clicks_in_shape(
            lines,
            source_view.raw,
            shape_title,
            include=include,
            exclude=exclude,
        )

    def ocr_centers_in_shape(
        self,
        view: View | int | str,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        frame_data_url: str | None = None,
    ) -> list[tuple[float, float, str]]:
        target_view = self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        lines = self.runner._cached_ocr_lines(self.ctx, frame)
        target_shape = self.resolve_shape_selector(target_view, shape_title)
        source_view = target_shape.parent_view if isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict) else target_view
        return self.runner._ocr_centers_in_shape(
            lines,
            source_view.raw,
            shape_title,
            include=include,
            exclude=exclude,
        )

    def ocr_lines_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        target_view = View(view) if isinstance(view, dict) else self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        return self.runner._ocr_lines_in_shapes(frame, target_view.raw, tuple(shape_titles), padding=padding, options=options, ctx=self.ctx)

    def ocr_words_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        target_view = View(view) if isinstance(view, dict) else self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        ocr_options = {"return_word_box": True, **dict(options or {})}
        return self.runner._ocr_words_in_shapes(frame, target_view.raw, tuple(shape_titles), padding=padding, options=ocr_options, ctx=self.ctx)

    def ocr_text_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        lines = self.ocr_lines_in_shapes(view, shape_titles, padding=padding, frame_data_url=frame_data_url, options=options)
        return self.runner._ocr_text(lines)

    def ocr_numbers_in_shapes(
        self,
        view: View | int | str,
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
    ) -> tuple[list[int], str]:
        text = self.ocr_text_in_shapes(view, shape_titles, padding=padding, frame_data_url=frame_data_url)
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        return [int(match) for match in re.findall(r"\d+", normalized)], normalized

    def find_floating_item_by_anchor(
        self,
        view: View | int | str,
        template_shape: Shape | str,
        anchor_field: Shape | str,
        *,
        container_shape: Shape | str | None = None,
        frame_data_url: str | None = None,
    ) -> FloatingItemInstance | None:
        target_view = self.view(view)
        item_template = self.resolve_shape_selector(target_view, template_shape)
        anchor_shape = self._resolve_floating_item_field(target_view, item_template, anchor_field)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        lines = self.runner._cached_ocr_lines(self.ctx, frame)
        container_box = (
            _absolute_shape_box(self.resolve_shape_selector(target_view, container_shape))
            if container_shape is not None
            else _absolute_shape_box(item_template)
        )
        template_box = _absolute_shape_box(item_template)
        anchor_template_box = _absolute_shape_box(anchor_shape)
        target_text = _sanitize_ocr_text(anchor_shape.raw.get("ocrText") or anchor_shape.title)
        mode = str(anchor_shape.raw.get("ocrMatchMode") or "contains")
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not target_text or not self.runner._ocr_text_matches(text, target_text, mode):
                continue
            resolved_box = self.runner._ocr_match_resolved_box(line, target_text, mode) or self.runner._ocr_line_box(line)
            if resolved_box is None:
                continue
            center_x = float(resolved_box.get("x") or 0) + float(resolved_box.get("w") or 0) / 2
            center_y = float(resolved_box.get("y") or 0) + float(resolved_box.get("h") or 0) / 2
            if not self._point_in_box(center_x, center_y, container_box):
                continue
            anchor_offset_x = anchor_template_box["x"] - template_box["x"]
            anchor_offset_y = anchor_template_box["y"] - template_box["y"]
            anchor_box = {
                "x": float(resolved_box.get("x") or 0),
                "y": float(resolved_box.get("y") or 0),
                "w": float(resolved_box.get("w") or 0),
                "h": float(resolved_box.get("h") or 0),
            }
            item_box = {
                "x": anchor_box["x"] - anchor_offset_x,
                "y": anchor_box["y"] - anchor_offset_y,
                "w": template_box["w"],
                "h": template_box["h"],
            }
            return FloatingItemInstance(
                view=target_view,
                template_shape=item_template,
                anchor_shape=anchor_shape,
                anchor_box=anchor_box,
                item_box=item_box,
                text=text,
            )
        return None

    def read_floating_item_field(
        self,
        item: FloatingItemInstance,
        field: Shape | str,
        *,
        padding: int = 8,
        frame_data_url: str | None = None,
    ) -> str:
        field_shape = self._resolve_floating_item_field(item.view, item.template_shape, field)
        field_box = self._padded_box(item.field_box(field_shape), padding)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        lines = self.runner._cached_ocr_lines(self.ctx, frame)
        selected: list[dict[str, Any]] = []
        for line in lines:
            line_box = self.runner._ocr_line_box(line)
            if line_box is not None and self.runner._ocr_line_overlaps_box(line, field_box):
                selected.append(line)
        return self.runner._ocr_text(selected)

    def click_floating_item_field(
        self,
        item: FloatingItemInstance,
        field: Shape | str,
        *,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> None:
        field_shape = self._resolve_floating_item_field(item.view, item.template_shape, field)
        field_box = item.field_box(field_shape)
        click_x = field_box["x"] + field_box["w"] * float(x_ratio)
        click_y = field_box["y"] + field_box["h"] * float(y_ratio)
        self.click_frame_point(item.view, click_x, click_y)

    def _resolve_floating_item_field(self, view: View, template_shape: Shape, field: Shape | str) -> Shape:
        if isinstance(field, Shape):
            return field
        field_text = self._selector_text(field)
        for child in template_shape.children():
            if child.title == field_text or str(child.raw.get("id") or "").strip() == field_text:
                return child
        try:
            return self.resolve_shape_selector(view, f"{template_shape.title}/{field_text}")
        except Exception:
            raise RuntimeError(f"浮动条目「{template_shape.title}」缺少字段「{field_text}」")

    def _point_in_box(self, x: float, y: float, box: Mapping[str, Any]) -> bool:
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        return left <= x <= right and top <= y <= bottom

    def _padded_box(self, box: Mapping[str, Any], padding: int) -> dict[str, float]:
        pad = float(padding)
        return {
            "x": float(box.get("x") or 0) - pad,
            "y": float(box.get("y") or 0) - pad,
            "w": float(box.get("w") or 0) + pad * 2,
            "h": float(box.get("h") or 0) + pad * 2,
        }

    def shape_score(
        self,
        view: View | int | str,
        shape: Shape | str,
        *,
        frame_data_url: str | None = None,
    ) -> float:
        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame()
        return float(self.runner._shape_score(self.ctx, target_view.raw, target_shape.raw, frame) or 0.0)

    def wait_shape(
        self,
        view: View | int | str,
        shape: Shape | str,
        *,
        timeout: float | None = None,
        threshold: float | None = None,
        label: str = "等待 shape",
    ) -> str:
        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        wait_timeout = self.default_wait_condition_timeout if timeout is None else float(timeout)
        min_score = self.runner.overlay_threshold if threshold is None else float(threshold)
        start = time.monotonic()
        last_score = 0.0
        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            frame = self.cur_frame()
            last_score = self.shape_score(target_view, target_shape, frame_data_url=frame)
            if last_score >= min_score:
                self.runner._log(
                    "success",
                    f"{label}：#{target_view.id or '?'} {self._shape_path(target_shape)} {last_score:.0f}%",
                )
                return frame
            if time.monotonic() - start >= max(1.0, wait_timeout):
                raise TimeoutError(
                    f"{label} 超时：#{target_view.id or '?'} {self._shape_path(target_shape)} {last_score:.0f}%"
                )
            if self.runner._auto_close_popup_guard_step(self):
                self.clear_frame()

    def image_signature_in_shape(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        frame_data_url: str | None = None,
    ) -> str:
        data = self.image_signature_bytes_in_shape(view_or_shape, shape, frame_data_url=frame_data_url)
        return hashlib.sha256(data).hexdigest() if data else ""

    def image_signature_bytes_in_shape(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        frame_data_url: str | None = None,
    ) -> bytes:
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) and shape is None else self.shape(view_or_shape, shape or "")
        view = target_shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError("shape 缺少 parent_view，无法计算内容签名")
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame()
        png_data = self.runner._decode_frame_data_url(frame)
        from PIL import Image, ImageDraw

        with Image.open(io.BytesIO(png_data)) as source:
            image = source.convert("RGB")
            target_box = self.runner._box(target_shape.raw, view.raw)
            raw_left = float(target_box.get("x") or 0)
            raw_top = float(target_box.get("y") or 0)
            left = max(0, min(image.width, int(round(raw_left))))
            top = max(0, min(image.height, int(round(raw_top))))
            right = max(left, min(image.width, int(round(raw_left + float(target_box.get("w") or 0)))))
            bottom = max(top, min(image.height, int(round(raw_top + float(target_box.get("h") or 0)))))
            if right <= left or bottom <= top:
                return b""
            crop = image.crop((left, top, right, bottom))
            draw = ImageDraw.Draw(crop)
            for box in self.runner._occlusion_marker_boxes(self.ctx, view.raw):
                box_left = float(box.get("x") or 0)
                box_top = float(box.get("y") or 0)
                box_right = box_left + float(box.get("w") or 0)
                box_bottom = box_top + float(box.get("h") or 0)
                inter_left = max(left, int(round(box_left)))
                inter_top = max(top, int(round(box_top)))
                inter_right = min(right, int(round(box_right)))
                inter_bottom = min(bottom, int(round(box_bottom)))
                if inter_left < inter_right and inter_top < inter_bottom:
                    draw.rectangle(
                        (inter_left - left, inter_top - top, inter_right - left, inter_bottom - top),
                        fill=(0, 0, 0),
                    )
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            normalized = crop.convert("L").resize((32, 32), resampling)
            return normalized.tobytes()

    def image_signature_similarity(self, left: bytes, right: bytes) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        total_delta = sum(abs(a - b) for a, b in zip(left, right))
        return max(0.0, 100.0 * (1.0 - total_delta / (255.0 * len(left))))

    def drag_shape_content(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        direction: str | None = None,
        ratio: float = 0.5,
        duration: float = 1.5,
    ) -> Any:
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) and shape is None else self.shape(view_or_shape, shape or "")
        view = target_shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError("shape 缺少 parent_view，无法滚动加载")
        start_x, start_y, end_x, end_y = ActionPlanner().drag_shape_content_points(
            view.raw,
            target_shape.raw,
            direction=str(direction or target_shape.content_direction or "down"),
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

    def drag_shape_to_shape(
        self,
        view: View | int | str,
        start_shape: Shape | str,
        end_shape: Shape | str,
        *,
        duration: float = 0.35,
        frame_data_url: str | None = None,
    ) -> None:
        target_view = self.view(view)
        start = self.resolve_shape_selector(target_view, start_shape)
        end = self.resolve_shape_selector(target_view, end_shape)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame()
        start_x, start_y = self.runner._shape_center(start.raw, target_view.raw, frame, self.ctx)
        end_x, end_y = self.runner._shape_center(end.raw, target_view.raw)
        self.runner._drag_frame_point(
            self.ctx,
            target_view.raw,
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms=max(0, int(float(duration) * 1000)),
        )
        self.clear_frame()

    def wait_action_settle(self, seconds: float = 1.0):
        yield from self.runner._wait_runtime_action_settle(
            self.ctx,
            self.stop_event or threading.Event(),
            seconds=max(0.0, float(seconds)),
        )

    def scroll_shape_content(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        recognition_shape: Shape | str | None = None,
        direction: str | None = None,
        ratio: float = DEFAULT_SCROLL_RATIO,
        duration: float = DEFAULT_SCROLL_DURATION_SECONDS,
        settle_seconds: float = DEFAULT_SCROLL_SETTLE_SECONDS,
        unchanged_threshold: float = DEFAULT_SCROLL_UNCHANGED_THRESHOLD,
        stable_sample_interval: float = 0.35,
        stable_sample_count: int = 3,
        unchanged_confirmations: int = 2,
    ) -> bool:
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) and shape is None else self.shape(view_or_shape, shape or "")
        signature_shape = target_shape
        if recognition_shape is not None:
            if isinstance(recognition_shape, Shape):
                signature_shape = recognition_shape
            else:
                view = target_shape.parent_view
                if not isinstance(view, View):
                    raise RuntimeError("shape 缺少 parent_view，无法解析识别区")
                signature_shape = self.resolve_shape_selector(view, recognition_shape)
        before_signature = self.image_signature_bytes_in_shape(signature_shape)
        self.drag_shape_content(target_shape, direction=direction, ratio=ratio, duration=duration)
        yield from self.wait_action_settle(settle_seconds)
        after_signature = self.image_signature_bytes_in_shape(signature_shape)
        # 滚动动画结束不代表画面已经稳定。连续采样，优先采用相邻稳定后的帧；
        # 横幅等持续动态内容应通过遮挡标注或 observe_scroll_content 的语义键规避。
        for _ in range(max(1, int(stable_sample_count)) - 1):
            yield from self.wait_action_settle(max(0.1, float(stable_sample_interval)))
            candidate_signature = self.image_signature_bytes_in_shape(signature_shape)
            if self.image_signature_similarity(after_signature, candidate_signature) >= float(unchanged_threshold):
                after_signature = candidate_signature
                break
            after_signature = candidate_signature
        similarity = self.image_signature_similarity(before_signature, after_signature)
        changed = bool(after_signature and similarity < float(unchanged_threshold))
        shape_identity = str(target_shape.raw.get("id") or target_shape.raw.get("title") or "shape")
        state_key = f"{shape_identity}:{direction or target_shape.content_direction or 'down'}"
        confirmation_state = self.attrs.setdefault("_scroll_unchanged_confirmations", {})
        if changed:
            confirmation_state[state_key] = 0
            return True
        confirmations = int(confirmation_state.get(state_key) or 0) + 1
        confirmation_state[state_key] = confirmations
        return confirmations < max(1, int(unchanged_confirmations))

    def observe_scroll_content(
        self,
        view_or_shape: View | int | str | Shape,
        visible_keys: Any,
        *,
        direction: str | None = None,
        unchanged_confirmations: int = 2,
    ) -> bool:
        """用业务可见项键统一判断滚动是否仍出现新内容。

        返回 ``False`` 表示连续多次没有任何新键，已可确认到底。动态横幅、列表
        高度动画不会进入业务键，因此比整块截图哈希可靠。
        """
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) else self.shape(view_or_shape, "")
        normalized_keys = {str(item).strip() for item in (visible_keys or ()) if str(item).strip()}
        shape_identity = str(target_shape.raw.get("id") or target_shape.raw.get("title") or "shape")
        state_key = f"{shape_identity}:{direction or target_shape.content_direction or 'down'}"
        states = self.attrs.setdefault("_scroll_semantic_progress", {})
        state = states.setdefault(state_key, {"seen": set(), "unchanged": 0})
        seen = state.setdefault("seen", set())
        has_new = bool(normalized_keys - seen)
        if has_new:
            seen.update(normalized_keys)
            state["unchanged"] = 0
            return True
        state["unchanged"] = int(state.get("unchanged") or 0) + 1
        return int(state["unchanged"]) < max(1, int(unchanged_confirmations))

    def nudge_shape_content_for_box(
        self,
        view_or_shape: View | int | str | Shape,
        shape_or_box: Shape | str | Mapping[str, Any],
        box: Mapping[str, Any] | None = None,
        *,
        edge_margin_ratio: float = 0.12,
        nudge_ratio: float = 0.15,
        duration: float = DEFAULT_SCROLL_DURATION_SECONDS,
        settle_seconds: float = DEFAULT_SCROLL_SETTLE_SECONDS,
    ) -> str | None:
        shape: Shape | str | None
        candidate_box: Mapping[str, Any]
        if box is None:
            shape = None
            if not isinstance(shape_or_box, Mapping):
                raise RuntimeError("缺少候选框，无法小幅复位内容")
            candidate_box = shape_or_box
        else:
            shape = shape_or_box if not isinstance(shape_or_box, Mapping) else None
            candidate_box = box
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) and shape is None else self.shape(view_or_shape, shape or "")
        view = target_shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError("shape 缺少 parent_view，无法小幅复位内容")
        target_box = self.runner._box(target_shape.raw, view.raw)
        left = float(target_box.get("x") or 0)
        top = float(target_box.get("y") or 0)
        width = float(target_box.get("w") or 0)
        height = float(target_box.get("h") or 0)
        if width <= 0 or height <= 0:
            return None
        cx = float(candidate_box.get("x") or 0) + float(candidate_box.get("w") or 0) / 2
        cy = float(candidate_box.get("y") or 0) + float(candidate_box.get("h") or 0) / 2
        margin = max(0.0, min(0.45, float(edge_margin_ratio)))
        content_direction = str(target_shape.content_direction or "down").strip().lower()
        direction: str | None = None
        if content_direction in {"left", "right"}:
            if cx <= left + width * margin:
                direction = "left"
            elif cx >= left + width * (1.0 - margin):
                direction = "right"
        else:
            if cy <= top + height * margin:
                direction = "up"
            elif cy >= top + height * (1.0 - margin):
                direction = "down"
        if direction is None:
            return None
        self.drag_shape_content(target_shape, direction=direction, ratio=nudge_ratio, duration=duration)
        yield from self.wait_action_settle(settle_seconds)
        return direction

    def _daily_entry_row_progress(
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
        view69: View,
        *,
        title_pattern: str,
        exclude_pattern: str | None = None,
    ) -> list[tuple[float, float, str]]:
        list_shape = self.resolve_shape_selector(view69, "滚动窗口")
        box = self.runner._box(list_shape.raw, view69.raw)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        bottom = top + height
        safe_top = top + height * 0.02
        safe_bottom = bottom - height * 0.08
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
            if left <= cx <= right and safe_top <= cy <= safe_bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

    def _daily_visible_list_signature(self, lines: list[dict[str, Any]], view69: View) -> tuple[tuple[str, int], ...]:
        list_shape = self.resolve_shape_selector(view69, "滚动窗口")
        box = self.runner._box(list_shape.raw, view69.raw)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        bottom = top + height
        safe_top = top + height * 0.02
        safe_bottom = bottom - height * 0.08
        signature: list[tuple[str, int]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            if re.search(r"活动报名.*小助手.*奖励找回|日常.*周常", text):
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if left <= cx <= right and safe_top <= cy <= safe_bottom:
                signature.append((text, int(round(cy / 12.0))))
        return tuple(signature)

    def daily_entry_matches(
        self,
        view: View | int | str = 69,
        *,
        title_pattern: str,
        exclude_pattern: str | None = None,
        frame_data_url: str | None = None,
    ) -> list[tuple[float, float, str]]:
        target_view = self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        return self._daily_entry_matches(
            self.ocr_lines(frame),
            target_view,
            title_pattern=title_pattern,
            exclude_pattern=exclude_pattern,
        )

    def ocr_centers_in_shape(
        self,
        view: View | int | str,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        frame_data_url: str | None = None,
    ) -> list[tuple[float, float, str]]:
        target_view = self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        return self.runner._ocr_centers_in_shape(
            self.ocr_lines(frame),
            target_view.raw,
            shape_title,
            include=include,
            exclude=exclude,
        )

    def _daily_text_is_daily_list(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        # These are #69 bottom tab OCR anchors, not the business meaning of the
        # currently visible scroll window. Daily tasks still search the list rows.
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

    def _ensure_daily_list_frame(self, frame: str, lines: list[dict[str, Any]], *, label: str) -> None:
        scene_id, score = self.runner._identify_scene_number(self.ctx, frame, [69, 34])
        text = self.runner._ocr_text(lines)
        if scene_id == 69 and self._daily_text_is_daily_list(text):
            return
        scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
        raise RuntimeError(f"{label}：未确认当前在 #69 日常列表，禁止滚动查找；当前 {scene_text} {score:.0f}% OCR={text[:120]}")

    def open_daily_entry(
        self,
        *,
        label: str,
        title_pattern: str,
        exclude_pattern: str | None = None,
        progress_can_mark_done: bool = True,
        max_scrolls: int = 30,
        reverse_scrolls: int = 0,
    ):
        view69 = self.view(69)
        list_shape = self.shape(view69, "滚动窗口")
        passes: list[tuple[str, int]] = [("down", max(0, int(max_scrolls)))]
        if int(reverse_scrolls) > 0:
            passes.append(("up", int(reverse_scrolls)))
        for direction, scroll_count in passes:
            unchanged_scrolls = 0
            for scroll_index in range(scroll_count + 1):
                if self.stop_event is not None:
                    self.runner._raise_if_stopped(self.stop_event)
                self._emit_runtime_action(
                    f"{label}：查找日常任务入口 {direction} {scroll_index}/{scroll_count}",
                    phase="daily_entry_find",
                    kind="wait",
                    current_scene=69,
                )
                frame = self.cur_frame(update=True)
                lines = self.runner._cached_ocr_lines(self.ctx, frame)
                self._ensure_daily_list_frame(frame, lines, label=label)
                matches = self._daily_entry_matches(
                    lines,
                    view69,
                    title_pattern=title_pattern,
                    exclude_pattern=exclude_pattern,
                )
                if matches:
                    x, y, matched_text = matches[0]
                    progress = self._daily_entry_row_progress(lines, y)
                    if progress_can_mark_done and progress is not None and progress[0] >= progress[1]:
                        return "done"
                    self._emit_runtime_action(
                        f"{label}：点击日常任务 {matched_text}",
                        phase="daily_entry_click",
                        kind="click",
                        current_scene=69,
                    )
                    self.click_frame_point(view69, x, y)
                    yield from self.wait_action_settle()
                    return "open"
                if scroll_index >= scroll_count:
                    break
                self.runner._log("action", f"{label}：未找到入口，{direction} 滚动日常列表 {scroll_index + 1}")
                before_visible_signature = self._daily_visible_list_signature(lines, view69)
                changed = yield from self.scroll_shape_content(view69, list_shape, direction=direction)
                after_frame = self.cur_frame(update=True)
                after_lines = self.runner._cached_ocr_lines(self.ctx, after_frame)
                after_visible_signature = self._daily_visible_list_signature(after_lines, view69)
                if after_visible_signature and after_visible_signature != before_visible_signature:
                    changed = True
                if not changed:
                    unchanged_scrolls += 1
                    self.runner._log(
                        "detail",
                        f"{label}：日常列表 {direction} 滚动后签名未变化 {unchanged_scrolls}/2",
                    )
                    if unchanged_scrolls >= 2:
                        break
                else:
                    unchanged_scrolls = 0
        return "not_found"

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
    from backend.core.fanxiu.mail.store import ensure_fanxiu_mail_table as _ensure_fanxiu_mail_table

    _ensure_fanxiu_mail_table()


def normalize_fanxiu_mail_title(value: Any) -> str:
    from backend.core.fanxiu.mail.store import normalize_fanxiu_mail_title as _normalize_fanxiu_mail_title

    return _normalize_fanxiu_mail_title(value)


def normalize_fanxiu_mail_time_text(value: Any) -> str:
    from backend.core.fanxiu.mail.store import normalize_fanxiu_mail_time_text as _normalize_fanxiu_mail_time_text

    return _normalize_fanxiu_mail_time_text(value)


def sync_fanxiu_capture_paths(pcap_paths: list[str], *, max_streams: int = 4) -> dict[str, Any]:
    from backend.core.fanxiu.packet.insight_worker import sync_fanxiu_capture_paths as _sync_fanxiu_capture_paths

    return _sync_fanxiu_capture_paths(pcap_paths, max_streams=max_streams)


def _recognize_data_annotation_ocr_frame(frame_data_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    from backend.core.fanxiu.game.macro_annotation import _recognize_data_annotation_ocr_frame as _recognize_frame

    return _recognize_frame(frame_data_url, options=options)


def _screencap_game_window2_service() -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import screencap_game_window2_service

    return screencap_game_window2_service()


def _remote_game_window2_screencap(entry: Any) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import remote_game_window2_screencap

    return remote_game_window2_screencap(entry)


def _match_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import match_game_window2_service

    return match_game_window2_service(payload)


def _match_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import match_remote_game_window2

    return match_remote_game_window2(entry, payload)


def _click_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import click_game_window2_service

    return click_game_window2_service(payload)


def _click_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import click_remote_game_window2

    return click_remote_game_window2(entry, payload)


def _drag_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import drag_game_window2_service

    return drag_game_window2_service(payload)


def _drag_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import drag_remote_game_window2

    return drag_remote_game_window2(entry, payload)


def _keyevent_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import keyevent_game_window2_service

    return keyevent_game_window2_service(payload)


def _keyevent_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import keyevent_remote_game_window2

    return keyevent_remote_game_window2(entry, payload)


def _text_game_window2_service(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import text_game_window2_service

    return text_game_window2_service(payload)


def _text_remote_game_window2(entry: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from backend.core.fanxiu.game.window_actions import text_remote_game_window2

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


def _data_annotation_mail_scan_state_path() -> Path:
    return _core_data_annotation_mail_scan_state_path()


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
    raw = _read_data_annotation_json(_data_annotation_scheduler_state_path(), None)
    tasks, changed = repair_data_annotation_scheduler_tasks(
        raw,
        _default_data_annotation_scheduler_tasks(),
        _read_data_annotation_world_facts(),
        task_supported=_data_annotation_task_supported,
        now=_now(),
    )
    if changed:
        _write_data_annotation_scheduler_tasks(tasks)
    return list(tasks)


def _write_data_annotation_scheduler_tasks(tasks: list[dict[str, Any]]) -> None:
    path = _data_annotation_scheduler_state_path()
    payload = [_data_annotation_scheduler_task_state(task) for task in tasks]
    existing = _read_data_annotation_json(path, [])
    if isinstance(existing, list):
        payload = preserve_data_annotation_scheduler_runtime_state(payload, existing)
    _write_data_annotation_json(
        path,
        payload,
    )


def _read_data_annotation_scheduler_settings() -> dict[str, Any]:
    return normalize_data_annotation_scheduler_settings(
        _read_data_annotation_json(_data_annotation_scheduler_settings_path(), None)
    )


def _next_data_annotation_scheduler_time(task: dict[str, Any], now: datetime | None = None) -> str | None:
    return _core_next_data_annotation_scheduler_time(task, now if now is not None else _now())


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    register_fanxiu_data_annotation_default_runtime_jobs()
    task_type = str(task.get("task_type") or "")
    if task_type == "mail_claim_check":
        task_type = "mail_cleanup"
    definition = _data_annotation_task_cell_definition(task_type)
    return bool(definition and definition.scheduler_supported)


def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_payload_with_meta(task)


from backend.core.fanxiu.data_annotation.tasks.daily_challenge import DailyChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import DailyFoundationTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.gift_code import GiftCodeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.jianling import JianlingTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mail import MailTaskMixin
from backend.core.fanxiu.data_annotation.tasks.misc_actions import MiscActionTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mozu import MozuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.signup_misc import SignupMiscTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianfu import XianfuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.yihuo import 日常异火任务Mixin
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin


class DataAnnotationRuntimeRunner(
    PopupGuardMixin,
    MozuTaskMixin,
    ZhenxieTaskMixin,
    日常异火任务Mixin,
    JianlingTaskMixin,
    DailyFoundationTaskMixin,
    DailyResourceTaskMixin,
    DailyChallengeTaskMixin,
    XianfuTaskMixin,
    SignupMiscTaskMixin,
    GiftCodeTaskMixin,
    MiscActionTaskMixin,
    MailTaskMixin,
):
    default_guard_enabled = True
    default_guard_interval_seconds = 2.0
    engineering_idle_guard_interval_seconds = 60.0
    device_health_guard_interval_seconds = 60.0
    idle_recovery_interval_seconds = 300.0
    idle_recovery_max_popup_ticks = 6
    idle_recovery_settle_seconds = 1.0
    default_guard_items = {
        "device_health": {"enabled": True, "entry_id": "", "updated_at": 0.0},
        "close_popups": {"enabled": True, "entry_id": "", "updated_at": 0.0},
        "wanling_invite": {"enabled": False, "entry_id": "", "updated_at": 0.0},
    }
    guard_definitions = {
        "device_health": {
            "id": "device_health",
            "label": "设备健康",
            "default_enabled": True,
            "message": "低频检查 MuMu/安卓容器，异常时恢复模拟器和游戏",
        },
        "close_popups": {
            "id": "close_popups",
            "label": "关闭弹窗",
            "default_enabled": True,
            "message": "常驻处理已标注弹窗和遮挡",
        },
        "wanling_invite": {
            "id": "wanling_invite",
            "label": "万灵切磋邀请",
            "default_enabled": False,
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
        "daily_assistant_overview": 204,
        "daily_assistant_tongyou_confirm": 210,
        "daily_assistant_one_key_result": 275,
        "daily_assistant_one_key_confirm": 276,
        "daily_assistant_one_key_progress": 277,
        "daily_shuangxiu_secret": 215,
        "daily_shuangxiu_detail": 216,
        "daily_shuangxiu_invite": 217,
        "daily_shuangxiu_xianyuan_invite": 218,
        "daily_shuangxiu_training_ready": 219,
        "daily_shuangxiu_complete": 221,
        "wanling_invite": 70,
        "youli": 71,
        "youli_home": 228,
        "youli_purchase": 229,
        "youli_purchase_empty": 233,
        "youli_region_detail": 236,
        "youli_quick_result": 237,
        "xianshi": 247,
        "xianshi_coin_tab": 248,
        "xianshi_coin_list": 249,
        "xianshi_coin_box_detail": 250,
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
    task_popup_guard_threshold = 88

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
        self._last_device_health_guard_at = 0.0
        self._auto_close_candidates_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
        self._missing_match_source_filenames: set[str] = set()
        self._log_scope = ""
        self._log_item_id = ""
        self._cell_execution_lock = threading.RLock()
        self._status: dict[str, Any] = self._initial_status()

    def _wait_runtime_action_settle(self, ctx: dict[str, Any], stop_event: threading.Event, seconds: float = 2.0):
        self._clear_tick_frame(ctx)
        if stop_event.wait(max(0.0, float(seconds))):
            self._raise_if_stopped(stop_event)
        self._clear_tick_frame(ctx)
        yield BehaviorTreeStatus.RUNNING

    def _runtime_view_for_image(self, image: dict[str, Any]) -> View:
        return View(image)

    def _runtime_shape_for_legacy_shape(self, image: dict[str, Any], shape: dict[str, Any]) -> Shape:
        return Shape(shape, parent_view=self._runtime_view_for_image(image))

    def _scroll_shape_content_changed(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        stop_event: threading.Event,
        *,
        reverse: bool = False,
        settle_seconds: float = DEFAULT_SCROLL_SETTLE_SECONDS,
        unchanged_threshold: float = DEFAULT_SCROLL_UNCHANGED_THRESHOLD,
    ):
        runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path") if isinstance(ctx.get("asset_tree_path"), Path) else None, stop_event=stop_event)
        runtime_shape = self._runtime_shape_for_legacy_shape(image, shape)
        original_direction = runtime_shape.raw.get("contentDirection")
        if reverse:
            direction = str(original_direction or runtime_shape.content_direction or "down").strip().lower()
            runtime_shape.raw["contentDirection"] = "down" if direction == "up" else "up"
        try:
            return (yield from runtime.scroll_shape_content(
                runtime_shape,
                settle_seconds=settle_seconds,
                unchanged_threshold=unchanged_threshold,
            ))
        finally:
            if reverse:
                if original_direction is None:
                    runtime_shape.raw.pop("contentDirection", None)
                else:
                    runtime_shape.raw["contentDirection"] = original_direction

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
                is_occlusion_folder = (
                    node_type == "folder"
                    and (title == OCCLUSION_ASSET_GROUP_TITLE or title in LEGACY_OCCLUSION_ASSET_GROUP_TITLES)
                )
                current_in_occlusion = in_occlusion_folder or is_occlusion_folder
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

    def _initial_status(self) -> dict[str, Any]:
        return initial_data_annotation_runtime_status()

    def _status_base_preserving_guard_locked(self) -> dict[str, Any]:
        base = self._initial_status()
        current_logs = [item for item in self._status.get("logs") or [] if isinstance(item, dict)]
        current_cell_logs = [item for item in self._status.get("cell_logs") or [] if isinstance(item, dict)]
        base.update({
            "guard_group_enabled": bool(self._guard_group_enabled),
            "guard_enabled": bool(self._guard_enabled),
            "guard_running": bool(self._status.get("guard_running")),
            "guard_entry_id": self._guard_entry_id,
            "guard_interval_seconds": self._guard_interval_seconds,
            "guard_items": json.loads(json.dumps(self._guard_items, ensure_ascii=False)),
            "last_guard_event": self._status.get("last_guard_event") if isinstance(self._status.get("last_guard_event"), dict) else {},
            "service_running": bool(self._service_thread is not None and self._service_thread.is_alive()),
            "logs": current_logs[-500:],
            "cell_logs": current_cell_logs[:100],
        })
        return base

    def status(self, *, include_cell_logs: bool = True) -> dict[str, Any]:
        with self._lock:
            self._sync_guard_status_locked()
            self._sync_service_status_locked()
            payload = self._status if include_cell_logs else {**self._status, "cell_logs": []}
            return json.loads(json.dumps(payload, ensure_ascii=False))

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
            elif guard_id == "device_health":
                running = bool(service_running and self._guard_group_enabled and enabled)
                device_health = self._status.get("device_health")
                if isinstance(device_health, dict):
                    state_text = str(device_health.get("status") or "")
                    if state_text:
                        message = f"设备状态：{state_text}"
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

    def _service_owner_payload(self, token: str, entry_id: str, generation: int, step: str) -> dict[str, Any]:
        pid = os.getpid()
        now = time.time()
        return {
            "resource": "fanxiu-behavior-tree-kernel",
            "owner_kind": "process",
            "owner_id": f"pid:{pid}",
            "pid": pid,
            "token": token,
            "lease_token": token,
            "entry_id": entry_id,
            "generation": generation,
            "step": step,
            "heartbeat_at": now,
            "updated_at": now,
        }

    def _write_service_owner(self, owner_path: Path, token: str, entry_id: str, generation: int, step: str) -> None:
        try:
            owner_path.parent.mkdir(parents=True, exist_ok=True)
            owner_path.write_text(
                json.dumps(self._service_owner_payload(token, entry_id, generation, step), ensure_ascii=False, indent=2),
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
                owner_pid = 0
                if isinstance(owner, dict):
                    try:
                        owner_pid = int(owner.get("pid") or 0)
                    except (TypeError, ValueError):
                        owner_pid = 0
                owner_alive = owner_pid == os.getpid() or _fanxiu_process_matches_service_owner(owner_pid)
                if isinstance(owner, dict) and owner_alive and not self._service_owner_stale(owner):
                    pid = owner.get("pid")
                    step = owner.get("step") or "unknown"
                    return False, data_annotation_runtime_owner_message(pid, step)
                if not self._unlink_service_owner_path(owner_path):
                    return False, "行为树执行器单例锁释放失败：owner 文件暂被占用，请稍后重试"
            fd = os.open(str(owner_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(self._service_owner_payload(token, entry_id, generation, "starting"), file, ensure_ascii=False, indent=2)
            self._service_owner_token = token
            self._service_owner_path = owner_path
            return True, ""
        except FileExistsError:
            return False, "行为树执行器已由另一个后端实例抢先启动"
        except Exception as exc:
            return False, f"行为树执行器单例锁获取失败：{exc}"

    def _unlink_service_owner_path(self, owner_path: Path) -> bool:
        for attempt in range(5):
            try:
                owner_path.unlink()
                return True
            except FileNotFoundError:
                return True
            except PermissionError:
                if attempt >= 4:
                    return False
                time.sleep(0.05)
        return False

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
            self._unlink_service_owner_path(owner_path)

    def _ensure_runtime_cell_idle_locked(self) -> None:
        if self._status.get("running"):
            raise FanxiuRuntimeError("数据标注 Runtime 正在运行任务", status_code=409)

    def ensure_service(
        self,
        *,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        tick_seconds: float = 1.0,
    ) -> dict[str, Any]:
        del tick_seconds
        entry_id = str(getattr(entry, "entry_id", None) or entry_id)
        with self._lock:
            self._restore_persisted_config_locked()
            self._service_entry = entry
            self._service_entry_id = entry_id
            self._service_asset_tree_path = asset_tree_path
            self._service_runtime_state_path = _data_annotation_runtime_state_path()
            self._service_scheduler_state_path = _data_annotation_scheduler_state_path()
            self._service_world_facts_path = _data_annotation_world_facts_path()
            if self._guard_enabled:
                self._guard_entry_id = entry_id
            if not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            self._status["service_running"] = False
            self._set_status_locked("idle", "凡修框架已加载到 Jupyter Kernel", phase="idle")
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
        normalize_data_annotation_runtime_guard_items(persisted, self.guard_definitions)
        self._guard_group_enabled = bool(persisted.get("guard_group_enabled", True))
        self._guard_enabled = close_popups_guard_enabled_from_status(persisted)
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
        current_cell_logs = [item for item in self._status.get("cell_logs") or [] if isinstance(item, dict)]
        persisted_cell_logs = [item for item in persisted.get("cell_logs") or [] if isinstance(item, dict)]
        kept_cell_logs = (current_cell_logs or persisted_cell_logs)[:100]
        self._status.update({
            **self._status,
            "entry_id": persisted.get("entry_id") or persisted.get("guard_entry_id") or self._status.get("entry_id") or "",
            "current_scene": persisted.get("current_scene"),
            "message": "行为树常驻服务恢复配置",
            "logs": kept_logs,
            "cell_logs": kept_cell_logs,
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
            scheduler_state_path = self._service_scheduler_state_path
            world_facts_path = self._service_world_facts_path
        return (
            runtime_state_path == _data_annotation_runtime_state_path()
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
        except OSError:
            return
        except Exception:
            request = {}
        if not isinstance(request, dict):
            return
        request_id = str(request.get("id") or "")
        if not request_id or request_id == self._service_last_control_id:
            return
        command = str(request.get("command") or "").strip()
        if command == "wake_service":
            self._service_last_control_id = request_id
            self._service_wake_event.set()
            with self._lock:
                self._log_locked(
                    "info",
                    f"已处理本地控制请求：wake_service reason={request.get('reason') or ''}",
                )
            self._remove_service_control_request(control_path)
            return
        if command == "shutdown_service":
            self._service_last_control_id = request_id
            stop_event = self._service_stop_event
            if stop_event is not None:
                stop_event.set()
            self._service_wake_event.set()
            with self._lock:
                self._log_locked(
                    "stop",
                    f"已处理本地控制请求：shutdown_service reason={request.get('reason') or ''}",
                )
            self._remove_service_control_request(control_path)
            return
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
        self._remove_service_control_request(control_path)

    def _remove_service_control_request(self, control_path: Path) -> None:
        for _attempt in range(3):
            try:
                control_path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError:
                time.sleep(0.05)

    def _run_service_loop(self, *, stop_event: threading.Event, tick_seconds: float, generation: int) -> None:
        last_engineering_idle_guard_at = 0.0
        engineering_idle_guard_fast_mode = False
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
                    if self._run_mumu_startup_gate_tick(entry_id, asset_tree_path):
                        self._mark_service_heartbeat("mumu_startup_gate")
                        self._service_wake_event.wait(max(0.5, min(2.0, float(tick_seconds or 1.0))))
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
                    now = time.time()
                    engineering_idle_guard_due = now - last_engineering_idle_guard_at >= max(1.0, float(self.engineering_idle_guard_interval_seconds))
                    if self._engineering_scheduler_enabled() and self._guard_group_enabled and (engineering_idle_guard_fast_mode or engineering_idle_guard_due):
                        if not engineering_idle_guard_fast_mode:
                            last_engineering_idle_guard_at = now
                        self._mark_service_heartbeat("idle_guard")
                        guard_handled = self._run_engineering_idle_guard_tick(
                            entry,
                            entry_id,
                            asset_tree_path,
                            stop_event=stop_event,
                            include_device_health=not engineering_idle_guard_fast_mode,
                        )
                        self._mark_service_heartbeat("idle_guard_done")
                        engineering_idle_guard_fast_mode = guard_handled
                        if guard_handled:
                            self._service_wake_event.wait(max(0.2, min(1.0, float(tick_seconds or 1.0))))
                            self._service_wake_event.clear()
                            continue
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

    def run_service_tick_once(
        self,
        *,
        guard: bool = True,
        task_cell: bool = True,
        scheduled_job: bool = True,
    ) -> dict[str, Any]:
        context = self._service_context()
        if context is None or not self._service_paths_still_current():
            self._mark_service_heartbeat("waiting_context")
            status = self.status()
            status["cell_tick"] = {
                "ran": False,
                "reason": "waiting_context",
                "guard": bool(guard),
                "task_cell": bool(task_cell),
                "scheduled_job": bool(scheduled_job),
            }
            self._persist_status()
            return status
        entry, entry_id, asset_tree_path = context
        action = "idle"
        try:
            if self._run_mumu_startup_gate_tick(entry_id, asset_tree_path):
                action = "mumu_startup_gate"
                status = self.status()
                status["cell_tick"] = {
                    "ran": True,
                    "action": action,
                    "guard": bool(guard),
                    "task_cell": bool(task_cell),
                    "scheduled_job": bool(scheduled_job),
                }
                self._persist_status()
                return status
            if guard:
                self._run_device_health_guard_tick(entry_id)
            if not self.status().get("running"):
                guard_handled = False
                guard_checked = False
                if guard and self._guard_group_enabled and self._guard_enabled:
                    self._mark_service_heartbeat("idle_guard")
                    guard_handled = self._run_idle_guard_tick(entry, entry_id, asset_tree_path)
                    self._mark_service_heartbeat("idle_guard_done")
                    guard_checked = True
                    if guard_handled:
                        action = "guard_checked"
                if not guard_handled and action == "idle" and scheduled_job:
                    self._mark_service_heartbeat("scheduler_external")
                    action = "scheduler_external"
                if action == "idle" and guard_checked:
                    action = "guard_checked"
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"行为树 tick 失败：{exc}")
                self._status.update({"ok": False, "status": "error", "message": str(exc), "error": str(exc), "updated_at": time.time()})
            self._persist_status()
            raise
        status = self.status()
        status["cell_tick"] = {
            "ran": True,
            "action": action,
            "guard": bool(guard),
            "task_cell": bool(task_cell),
            "scheduled_job": bool(scheduled_job),
        }
        self._persist_status()
        return status

    def run_service_ticks(
        self,
        *,
        guard: bool = True,
        task_cell: bool = True,
        scheduled_job: bool = True,
        run_mode: str = "tick_once",
        max_ticks: int = 10,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        mode = str(run_mode or "tick_once").strip() or "tick_once"
        if mode not in {"tick_once", "until_idle", "current_job"}:
            mode = "tick_once"
        max_count = max(1, min(int(max_ticks or 1), 100))
        deadline = time.time() + max(0.1, min(float(timeout_seconds or 30.0), 300.0))
        statuses: list[dict[str, Any]] = []

        if mode == "current_job" and self.status().get("running"):
            completed = self.wait_until_idle(timeout_seconds=max(0.1, deadline - time.time()))
            status = self.status()
            status["cell_tick"] = {
                "ran": False,
                "reason": "current_job_done" if completed else "timeout",
                "run_mode": mode,
                "ticks": 0,
                "guard": bool(guard),
                "task_cell": bool(task_cell),
                "scheduled_job": bool(scheduled_job),
            }
            self._persist_status()
            return status

        no_progress_actions = {"idle", "guard_checked", "scheduler_isolated"}
        for index in range(max_count):
            if time.time() >= deadline:
                break
            status = self.run_service_tick_once(
                guard=guard,
                task_cell=task_cell,
                scheduled_job=scheduled_job,
            )
            statuses.append(status)
            tick = status.get("cell_tick") if isinstance(status.get("cell_tick"), dict) else {}
            action = str(tick.get("action") or "")
            if mode == "tick_once":
                break
            if mode == "current_job":
                if status.get("running"):
                    completed = self.wait_until_idle(timeout_seconds=max(0.1, deadline - time.time()))
                    status = self.status()
                    tick = status.get("cell_tick") if isinstance(status.get("cell_tick"), dict) else {}
                    status["cell_tick"] = {
                        **tick,
                        "ran": True,
                        "action": action,
                        "reason": "current_job_done" if completed else "timeout",
                        "guard": bool(guard),
                        "task_cell": bool(task_cell),
                        "scheduled_job": bool(scheduled_job),
                    }
                    statuses[-1] = status
                break
            if action in no_progress_actions:
                break

        status = statuses[-1] if statuses else self.status()
        tick = status.get("cell_tick") if isinstance(status.get("cell_tick"), dict) else {}
        status["cell_tick"] = {
            **tick,
            "run_mode": mode,
            "ticks": len(statuses),
            "timeout": time.time() >= deadline and bool(statuses),
            "guard": bool(guard),
            "task_cell": bool(task_cell),
            "scheduled_job": bool(scheduled_job),
        }
        self._persist_status()
        return status

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
        due_task = due_tasks[0]
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "entry_id": entry_id,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            blocking_overlay = self._known_blocking_overlay_info(ctx)
        except Exception:
            blocking_overlay = None
        if blocking_overlay and bool(blocking_overlay.get("blocking")):
            blocking_message = str(blocking_overlay.get("message") or "")
            self._mark_scheduler_tasks_blocked(tasks, due_tasks, blocking_message)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "entry_id": entry_id,
                    "status": "idle",
                    "phase": "scheduler_blocked",
                    "message": blocking_message,
                    "blocking_overlays": [blocking_overlay],
                    "updated_at": time.time(),
                })
                if self._status.get("last_scheduler_block_message") != blocking_message:
                    self._status["last_scheduler_block_message"] = blocking_message
                    self._log_locked("warning", blocking_message, scope="job", item_id="scheduler")
            self._persist_status()
            return False
        self.start_scheduler_tasks(
            entry=entry,
            entry_id=entry_id,
            tasks=[due_task],
            all_tasks=tasks,
            asset_tree_path=asset_tree_path,
            run_label="执行到期任务",
        )
        return True

    def _run_mumu_startup_gate_tick(self, entry_id: str, asset_tree_path: Path) -> bool:
        grace = mumu_device_startup_grace_state()
        if not bool(grace.get("active")):
            return False
        remaining = float(grace.get("remaining_seconds") or 0.0)
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry_id": entry_id,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            frame = self._data_url(_encode_png_frame(capture_mumu_window_frame()))
            scene_id, score = self._identify_scene_number(ctx, frame, preferred_scene_ids=[14])
            if scene_id == 14 and self._scene_matches_id(14, float(score or 0.0)):
                mark_mumu_device_startup_ready(reason="scene_14_ready")
                with self._lock:
                    self._status.update({
                        "status": "idle",
                        "phase": "mumu_startup_ready",
                        "current_scene": 14,
                        "message": "MuMu 启动保护期结束：已识别 #14「游戏公告」",
                        "error": "",
                        "updated_at": time.time(),
                    })
                    self._log_locked("info", "MuMu 启动保护期结束：已识别 #14「游戏公告」", scope="guard", item_id="device_health")
                self._persist_status()
                return True
            with self._lock:
                self._status.update({
                    "status": "idle",
                    "phase": "mumu_startup_wait_scene_14",
                    "current_scene": scene_id,
                    "message": f"MuMu 启动保护期：等待 #14「游戏公告」，剩余 {remaining:.0f}s",
                    "error": "",
                    "updated_at": time.time(),
                    "device_health": {
                        "status": "starting",
                        "startup_gate": "wait_scene_14",
                        "startup_grace_remaining_seconds": round(remaining, 3),
                        "current_scene": scene_id,
                        "score": round(float(score or 0.0), 3),
                    },
                })
        except Exception as exc:
            with self._lock:
                self._status.update({
                    "status": "idle",
                    "phase": "mumu_startup_wait_scene_14",
                    "message": f"MuMu 启动保护期：等待 #14「游戏公告」，剩余 {remaining:.0f}s",
                    "error": "",
                    "updated_at": time.time(),
                    "device_health": {
                        "status": "starting",
                        "startup_gate": "wait_scene_14",
                        "startup_grace_remaining_seconds": round(remaining, 3),
                        "last_error": str(exc),
                    },
                })
        self._persist_status()
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

    def _engineering_scheduler_enabled(self) -> bool:
        try:
            settings = _read_data_annotation_scheduler_settings()
        except Exception:
            settings = {}
        return bool(settings.get("job_group_enabled", True)) and not self._job_group_isolated()

    def _run_engineering_idle_guard_tick(
        self,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        *,
        stop_event: threading.Event,
        include_device_health: bool = True,
    ) -> bool:
        handled = False
        if include_device_health:
            handled = self._run_device_health_guard_tick(entry_id) or handled
        if self._runtime_guard_enabled("close_popups"):
            handled = self._run_idle_guard_tick(entry, entry_id, asset_tree_path, stop_event=stop_event) or handled
        return handled

    def _run_idle_guard_tick(
        self,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        *,
        stop_event: threading.Event | None = None,
    ) -> bool:
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "entry_id": entry_id,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            result = self._runtime_guard_service_tick("close_popups", ctx, asset_tree_path, stop_event or threading.Event())
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
            return result == BehaviorTreeStatus.RUNNING
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"守护空转失败：{exc}", scope="guard", item_id="close_popups")
            return False

    def _run_idle_recovery(
        self,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        *,
        stop_event: threading.Event,
        max_popup_ticks: int | None = None,
        settle_seconds: float | None = None,
    ) -> None:
        """Run bounded idle maintenance without starting business jobs."""
        self._run_device_health_guard_tick(entry_id)
        if not (self._guard_group_enabled and self._guard_enabled):
            return
        max_ticks = max(1, int(max_popup_ticks or self.idle_recovery_max_popup_ticks))
        settle = max(0.0, float(settle_seconds if settle_seconds is not None else self.idle_recovery_settle_seconds))
        try:
            tree = self._load_asset_tree(asset_tree_path)
            ctx = {
                "entry": entry,
                "entry_id": entry_id,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            handled_count = 0
            for _index in range(max_ticks):
                self._raise_if_stopped(stop_event)
                if self.status().get("running"):
                    break
                result = self._runtime_guard_service_tick("close_popups", ctx, asset_tree_path, stop_event)
                if result != BehaviorTreeStatus.RUNNING:
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
                                "message": f"空闲复原识别：#{scene_id} {key} {score:.0f}%",
                                "updated_at": time.time(),
                            })
                    break
                handled_count += 1
                self._clear_tick_frame(ctx)
                if settle > 0 and stop_event.wait(settle):
                    self._raise_if_stopped(stop_event)
            if handled_count >= max_ticks:
                with self._lock:
                    self._log_locked(
                        "warning",
                        f"空闲复原达到弹窗处理上限：{handled_count}",
                        scope="guard",
                        item_id="close_popups",
                    )
            self._clear_tick_frame(ctx)
            self._persist_status()
        except Exception as exc:
            with self._lock:
                self._log_locked("error", f"空闲复原失败：{exc}", scope="guard", item_id="close_popups")

    def _run_device_health_guard_tick(self, entry_id: str, *, force: bool = False) -> bool:
        if not self._runtime_guard_enabled("device_health"):
            return False
        now = time.time()
        if (
            not force
            and self._last_device_health_guard_at > 0
            and now - self._last_device_health_guard_at < max(1.0, float(self.device_health_guard_interval_seconds))
        ):
            return False
        self._last_device_health_guard_at = now
        try:
            state = ensure_mumu_device_healthy(recover=True, reason="resident_heartbeat")
        except Exception as exc:
            state = {"status": "suspect", "last_error": str(exc)}
        status_text = str(state.get("status") or "unknown")
        recovered = bool(state.get("recovered"))
        with self._lock:
            previous = self._status.get("device_health")
            previous_status = str(previous.get("status") or "") if isinstance(previous, dict) else ""
            self._status["device_health"] = state
            if recovered:
                self._log_locked("warning", "设备健康守护已恢复 MuMu 安卓容器并拉起凡修游戏", scope="guard", item_id="device_health")
            elif status_text and status_text != previous_status and status_text != "healthy":
                self._log_locked("warning", f"设备健康异常：{status_text}", scope="guard", item_id="device_health")
            if entry_id and not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            self._sync_guard_status_locked()
        if recovered:
            self._persist_status()
        return recovered

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
                self._status["close_popups_guard_config_version"] = CLOSE_POPUPS_GUARD_CONFIG_VERSION
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

    def _run_registered_task_inline(
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
        definition = _data_annotation_task_cell_definition(task_type)
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
        tick_seconds: float = 0.2,
    ) -> dict[str, Any]:
        task_type = self._canonical_runtime_task_type(task_type)
        payload = dict(payload or {})
        ensure_fanxiu_runtime_jobs_registered()
        definition = _data_annotation_task_cell_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        if definition.normalize_payload is not None:
            payload = definition.normalize_payload(payload)
        return self._run_inline_runtime_task(
            entry=entry,
            entry_id=entry_id,
            task_type=task_type,
            payload={**payload, "__local_run": True, "__tick_seconds": max(0.1, float(tick_seconds or 0.2))},
            asset_tree_path=asset_tree_path,
        )

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
            self._ensure_runtime_cell_idle_locked()
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
        is_run_due = run_label in {"执行全部到期任务", "执行到期任务"}
        with self._lock:
                self._ensure_runtime_cell_idle_locked()
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
        self._persist_status()
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

    def _log_locked(
        self,
        kind: str,
        message: str,
        *,
        scope: str | None = None,
        item_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
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
            extra=extra,
        )

    def _log(self, kind: str, message: str) -> None:
        with self._lock:
            self._log_locked(kind, message)

    def _runtime_cell_log_entry_base_id(self, item: dict[str, Any]) -> str:
        return hashlib.sha1(
            json.dumps(
                {
                    "time": item.get("time") or "",
                    "kind": item.get("kind") or "",
                    "scope": item.get("scope") or "",
                    "item_id": item.get("item_id") or "",
                    "message": item.get("message") or "",
                    "action": item.get("action") or "",
                    "source_file": item.get("source_file") or "",
                    "source_line": item.get("source_line") or "",
                    "source_expr": item.get("source_expr") or "",
                    "ts": item.get("ts") or "",
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:16]

    def _runtime_cell_log_entry(self, item: dict[str, Any], occurrence: int) -> dict[str, Any]:
        return {
            "id": f"runtime-{self._runtime_cell_log_entry_base_id(item)}-{occurrence}",
            "time": str(item.get("time") or ""),
            "kind": str(item.get("kind") or ""),
            "scope": str(item.get("scope") or ""),
            "item_id": str(item.get("item_id") or ""),
            "message": str(item.get("message") or ""),
            "action": str(item.get("action") or ""),
            "source_file": str(item.get("source_file") or ""),
            "source_path": str(item.get("source_path") or ""),
            "source_line": item.get("source_line") if isinstance(item.get("source_line"), int) else None,
            "source_expr": str(item.get("source_expr") or ""),
            "ts": str(item.get("ts") or ""),
        }

    def _runtime_task_cell_source(self, task_type: str, payload: dict[str, Any], *, local_run: bool = False) -> str:
        clean_payload = {
            str(key): value
            for key, value in dict(payload or {}).items()
            if not str(key).startswith("__")
        }
        prefix = "# 本地直接运行\n" if local_run else ""
        return (
            f"{prefix}task = 行为树.create_task({task_type!r}, {clean_payload!r})\n"
            "行为树.step(task, 守护=True)"
        )

    def _append_runtime_cell_log_locked(
        self,
        *,
        title: str,
        source: str,
        logs: list[dict[str, Any]] | None = None,
    ) -> None:
        raw_entries = [item for item in (logs if logs is not None else self._status.get("logs") or []) if isinstance(item, dict)]
        raw_entries = raw_entries[-300:]
        seen_ids: dict[str, int] = {}
        entries: list[dict[str, Any]] = []
        for item in raw_entries:
            base_id = self._runtime_cell_log_entry_base_id(item)
            occurrence = seen_ids.get(base_id, 0)
            seen_ids[base_id] = occurrence + 1
            entries.append(self._runtime_cell_log_entry(item, occurrence))
        if not entries:
            now = _now().strftime("%H:%M:%S")
            entries = [{
                "id": f"runtime-cell-empty-{uuid.uuid4().hex[:8]}",
                "time": now,
                "kind": "info",
                "scope": "cell",
                "item_id": "framework",
                "message": f"提交 cell：{title}",
                "action": "",
                "source_file": "",
                "source_path": "",
                "source_line": None,
                "source_expr": "",
                "ts": str(time.time()),
            }]
        cell_id = f"cell-{hashlib.sha1((title + source + str(time.time())).encode('utf-8')).hexdigest()[:16]}"
        cell = {
            "id": cell_id,
            "title": title,
            "source_kind": "command",
            "source": source,
            "started_at": str(entries[0].get("time") or ""),
            "ended_at": str(entries[-1].get("time") or ""),
            "entries": entries,
        }
        existing = self._status.get("cell_logs") if isinstance(self._status.get("cell_logs"), list) else []
        self._status["cell_logs"] = [cell, *[item for item in existing if isinstance(item, dict) and item.get("id") != cell_id]][:100]

    def _finish_daily_runtime_task(
        self,
        payload: dict[str, Any],
        *,
        task_type: str,
        label: str,
        message: str,
        current_scene: int | None = 34,
        next_time: str | None = None,
    ) -> None:
        next_time = str(next_time or self._next_daily_boss_reset_time_text())
        scheduler_task_id = str(payload.get("__scheduler_task_id") or f"legacy-{task_type}")
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type=task_type,
            label=label,
            last_result="success",
        )
        with self._lock:
            self._set_status_locked(
                "success",
                f"{message}，下次 {next_time}",
                phase=f"{task_type}_done",
                current_scene=current_scene,
            )
            self._log_locked("success", self._status["message"])

    def _execute_daily_runtime_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None,
        *,
        task_type: str,
        label: str,
        flow: Callable[[FanxiuRuntime], Any],
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError(f"缺少{label}资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        runtime_attrs = getattr(runtime, "attrs", None)
        if isinstance(runtime_attrs, dict):
            runtime_attrs["payload"] = payload
        result = flow(runtime)
        flow_result: Any = None
        if isinstance(result, GeneratorType):
            flow_result = yield from result
        else:
            flow_result = result
        yield from self._wait_runtime_action_settle(ctx, stop_event)
        if isinstance(flow_result, dict) and str(flow_result.get("result") or "success") != "success":
            task_result = str(flow_result.get("result") or "skipped")
            retry_after = (
                _runtime_runner._now()
                + timedelta(seconds=max(60, int(payload.get("fallback_seconds") or payload.get("cooldown_seconds") or 600)))
            ).strftime("%Y-%m-%d %H:%M:%S")
            self._record_scheduler_task_discovered_retry_after(
                str(payload.get("__scheduler_task_id") or f"legacy-{task_type}"),
                retry_after,
                task_type=task_type,
                label=label,
                last_result=task_result,
            )
            with self._lock:
                self._set_status_locked(
                    task_result,
                    str(flow_result.get("message") or f"{label}未确认完成，{retry_after} 重试"),
                    phase=f"{task_type}_{task_result}",
                    current_scene=34,
                )
                self._log_locked("skip" if task_result == "skipped" else task_result, self._status["message"])
            return task_result
        runtime_attrs = getattr(runtime, "attrs", None)
        completion_message = str(runtime_attrs.get("completion_message") or "").strip() if isinstance(runtime_attrs, dict) else ""
        if isinstance(flow_result, dict):
            completion_message = str(flow_result.get("message") or completion_message).strip()
        self._finish_daily_runtime_task(
            payload,
            task_type=task_type,
            label=label,
            message=completion_message or f"{label}完成，已回到世界",
            current_scene=flow_result.get("current_scene", 34) if isinstance(flow_result, dict) else 34,
            next_time=str(flow_result.get("next_time") or "") if isinstance(flow_result, dict) else None,
        )
        return "success"

    def _task_cell_log_message(self, task_id: str, message: str) -> str:
        task_id = str(task_id or "").strip()
        return f"[{task_id}] {message}" if task_id else message

    def _normalize_runtime_task_result(self, value: Any) -> tuple[str, str]:
        if isinstance(value, dict):
            result = str(value.get("result") or "success").strip() or "success"
            message = str(value.get("message") or "").strip()
            return result, message
        return str(value or "success"), ""

    def _persist_status(self) -> None:
        try:
            _persist_data_annotation_runtime_status(self.status())
        except Exception:
            pass

    def _runtime_task_label(self, task_type: str, payload: dict[str, Any] | None = None) -> str:
        task_type = self._canonical_runtime_task_type(task_type)
        definition = _data_annotation_task_cell_definition(task_type)
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

    def _fanxiu_observer(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        frame_data_url: str | None = None,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        if isinstance(asset_tree_path, Path):
            runtime = self._fanxiu_runtime(ctx, asset_tree_path, frame_data_url=frame_data_url, stop_event=stop_event)
            required_methods = ("cur_frame", "current_scene", "ocr_lines", "ocr_text", "clear_frame")
            if all(hasattr(runtime, name) for name in required_methods):
                return runtime

        runner = self

        class _FallbackObserver:
            def __init__(self, initial_frame: str | None = None) -> None:
                self.frame_data_url = initial_frame

            def cur_frame(self, update: bool = False) -> str:
                if update:
                    self.clear_frame()
                if isinstance(self.frame_data_url, str) and self.frame_data_url:
                    return self.frame_data_url
                self.frame_data_url = runner._screencap(ctx)
                return self.frame_data_url

            def current_scene(
                self,
                views: list[int] | None = None,
                *,
                frame_data_url: str | None = None,
                update: bool = False,
            ) -> tuple[int | None, float, str]:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                scene_id, score = runner._identify_scene_number(ctx, frame, views)
                return scene_id, float(score or 0.0), frame

            def ocr_lines(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                return runner._cached_ocr_lines(ctx, frame)

            def ocr_text(self, frame_data_url: str | None = None, *, update: bool = False) -> str:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                return runner._ocr_text(self.ocr_lines(frame))

            def click_frame_point(self, view: View | dict[str, Any], x: float, y: float) -> None:
                image = view.raw if isinstance(view, View) else view
                runner._click_frame_point(ctx, image, x, y)
                self.clear_frame()

            def click_shape_center(self, view: View | dict[str, Any], shape: Shape | str) -> None:
                image = view.raw if isinstance(view, View) else view
                if not isinstance(image, dict):
                    raise RuntimeError("缺少可点击 view")
                raw_shape = shape.raw if isinstance(shape, Shape) else runner._find_shape(image, str(shape))
                if not isinstance(raw_shape, dict):
                    raise RuntimeError(f"缺少 #{runner._image_number(image) or '?'}「{shape}」标注")
                x, y = ActionPlanner().shape_center(image, raw_shape)
                self.click_frame_point(image, x, y)

            def wait_click(self, view: View | int | str | dict[str, Any], shape: Shape | str, **_options: Any):
                if False:
                    yield BehaviorTreeStatus.RUNNING
                if isinstance(view, View):
                    image = view.raw
                elif isinstance(view, dict):
                    image = view
                else:
                    images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
                    view_id = int(str(view).lstrip("#"))
                    image = images.get(view_id)
                if not isinstance(image, dict):
                    raise RuntimeError(f"无法解析帧选择器：{view}")
                raw_shape = shape.raw if isinstance(shape, Shape) else runner._find_shape(image, str(shape))
                if not isinstance(raw_shape, dict):
                    raise RuntimeError(f"缺少 #{runner._image_number(image) or '?'}「{shape}」标注")
                x, y = ActionPlanner().shape_center(image, raw_shape)
                self.click_frame_point(image, x, y)

            def wait_action_settle(self, seconds: float = 1.0):
                yield from runner._wait_runtime_action_settle(ctx, stop_event, seconds=float(seconds or 0))

            def clear_frame(self) -> None:
                self.frame_data_url = None
                runner._clear_tick_frame(ctx)

        return _FallbackObserver(frame_data_url)

    def _fanxiu_runtime_scene_text(
        self,
        ctx: dict[str, Any],
        runtime: Any,
        scene_ids: list[int] | None = None,
        *,
        frame: str | None = None,
        update: bool = False,
    ) -> tuple[int | None, float, str, str]:
        if "entry" not in ctx and (not isinstance(frame, str) or not frame):
            if scene_ids is None:
                scene_id, score, frame = self._current_scene_number(ctx)
            else:
                frame = self._screencap(ctx)
                scene_id, score = self._identify_scene_number(ctx, frame, scene_ids)
        elif hasattr(runtime, "current_scene"):
            scene_id, score, frame = runtime.current_scene(scene_ids, frame_data_url=frame, update=update)
        else:
            if not isinstance(frame, str) or not frame:
                frame = runtime.cur_frame(update=update) if hasattr(runtime, "cur_frame") else self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, scene_ids)
        try:
            text = runtime.ocr_text(frame) if hasattr(runtime, "ocr_text") else self._ocr_text(self._ocr_lines(frame))
        except Exception:
            text = ""
        return scene_id, float(score or 0.0), frame, text

    def _fanxiu_runtime_ocr_text_in_shapes(
        self,
        runtime: Any,
        view: View | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        frame_data_url: str,
        padding: int = 16,
    ) -> str:
        if hasattr(runtime, "ocr_text_in_shapes"):
            return runtime.ocr_text_in_shapes(view, tuple(shape_titles), frame_data_url=frame_data_url, padding=padding)
        image = view.raw if isinstance(view, View) else view
        return self._ocr_text(self._ocr_lines_in_shapes(frame_data_url, image, tuple(shape_titles), padding=padding))

    def _runtime_guard_enabled(self, guard_id: str) -> bool:
        guard_id = str(guard_id or "").strip()
        with self._lock:
            if not self._guard_group_enabled:
                return False
            return self._runtime_guard_item_enabled_locked(guard_id)

    def _runtime_guard_item_enabled(self, guard_id: str) -> bool:
        guard_id = str(guard_id or "").strip()
        with self._lock:
            return self._runtime_guard_item_enabled_locked(guard_id)

    def _runtime_guard_item_enabled_locked(self, guard_id: str) -> bool:
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
        *,
        allow_during_task: bool = False,
        guard_override: bool | None = None,
    ) -> BehaviorTreeStatus:
        self._raise_if_stopped(stop_event)
        guard_id = str(guard_id or "").strip()
        if guard_override is False:
            return BehaviorTreeStatus.SKIP
        if guard_override is True:
            enabled = self._runtime_guard_item_enabled(guard_id)
        else:
            enabled = self._runtime_guard_enabled(guard_id)
        if not enabled:
            return BehaviorTreeStatus.SKIP
        if guard_id == "device_health":
            entry_id = str(runtime_ctx.get("entry_id") or self._status.get("entry_id") or "")
            return BehaviorTreeStatus.RUNNING if self._run_device_health_guard_tick(entry_id) else BehaviorTreeStatus.SKIP
        if guard_id != "close_popups":
            return BehaviorTreeStatus.SKIP
        if not allow_during_task:
            with self._lock:
                if bool(self._status.get("running")) or str(self._status.get("phase") or "") in {"task_cell", "local_run"}:
                    return BehaviorTreeStatus.SKIP
        tick_started_at = time.monotonic()
        previous_log_context = self._set_log_context("guard", "close_popups")
        try:
            runtime = self._fanxiu_runtime(runtime_ctx, asset_tree_path)
            if not self._auto_close_popup_guard_step(runtime):
                elapsed = time.monotonic() - tick_started_at
                if elapsed >= 5.0:
                    self._log("detail", f"弹窗守护tick耗时 {elapsed:.2f}s result=skip")
                return BehaviorTreeStatus.SKIP
            self._persist_status()
            runtime.clear_frame()
            elapsed = time.monotonic() - tick_started_at
            if elapsed >= 5.0:
                self._log("detail", f"弹窗守护tick耗时 {elapsed:.2f}s result=handled")
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
        guard_override: bool | None = None,
    ) -> Any:
        with self._cell_execution_lock:
            return _DataAnnotationRuntimeContainer(
                self,
                runtime_ctx=runtime_ctx,
                asset_tree_path=asset_tree_path,
                stop_event=stop_event,
                guard_override=guard_override,
            ).run_job_until_complete(
                action=action,
                label=label,
                tick_seconds=tick_seconds,
                max_runtime_seconds=max_runtime_seconds,
            )

    def _execute_registered_task_cell(
        self,
        *,
        runtime_ctx: dict[str, Any],
        asset_tree_path: Path,
        stop_event: threading.Event,
        task_type: str,
        payload: dict[str, Any],
        label: str,
    ) -> tuple[str, str]:
        """Route jobs through Jupyter when hosted by the resident kernel.

        The direct branch is retained for isolated unit tests and explicit
        in-process tooling that does not start the kernel service.
        """
        from backend.core.fanxiu.runtime.jupyter_kernel import (
            execute_fanxiu_jupyter_task_cell,
            fanxiu_jupyter_connection_path,
        )

        connection_path = fanxiu_jupyter_connection_path()
        embedded = str(os.environ.get("CODEYUN_FANXIU_EMBEDDED_BEHAVIOR_TREE_SERVICE") or "").strip().lower()
        if embedded in {"1", "true"} and connection_path.is_file():
            response = execute_fanxiu_jupyter_task_cell(
                task_type,
                payload,
                timeout_seconds=self._task_timeout_seconds(payload) + 30.0,
            )
            return str(response.get("result") or "success"), str(response.get("message") or "")

        raw_result = self._run_runtime_behavior_tree(
            runtime_ctx=runtime_ctx,
            asset_tree_path=asset_tree_path,
            stop_event=stop_event,
            action=lambda: self._execute_runtime_task(runtime_ctx, task_type, payload, stop_event),
            label=label,
            tick_seconds=max(0.1, float(payload.get("__tick_seconds") or 1.0)),
            max_runtime_seconds=self._task_timeout_seconds(payload),
            guard_override=self._runtime_guard_override_from_payload(payload),
        )
        return self._normalize_runtime_task_result(raw_result)

    def _task_timeout_seconds(self, payload: dict[str, Any] | None = None) -> float:
        payload = payload if isinstance(payload, dict) else {}
        raw_value = payload.get("max_runtime_seconds", payload.get("timeout_seconds", 600))
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 600.0
        return max(30.0, min(21600.0, value))

    def _runtime_guard_override_from_payload(self, payload: dict[str, Any] | None) -> bool | None:
        if not isinstance(payload, dict) or "guard" not in payload:
            return None
        value = payload.get("guard")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "enable", "enabled", "开启", "开", "启用"}:
            return True
        if text in {"0", "false", "no", "off", "disable", "disabled", "关闭", "关", "禁用"}:
            return False
        return None

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
            self._mark_service_heartbeat("task_running")
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
                "entry_id": entry_id,
                "asset_tree": tree,
                "asset_tree_path": asset_tree_path,
                "images": self._index_images(tree),
            }
            self._require_assets(ctx)
            raw_task_result = self._run_runtime_behavior_tree(
                runtime_ctx=ctx,
                asset_tree_path=asset_tree_path,
                stop_event=stop_event,
                action=lambda: self._execute_runtime_task(ctx, task_type, payload, stop_event),
                label=self._runtime_task_label(task_type, payload),
                tick_seconds=max(0.1, float(payload.get("__tick_seconds") or 1.0)),
                max_runtime_seconds=self._task_timeout_seconds(payload),
                guard_override=self._runtime_guard_override_from_payload(payload),
            )
            task_result, task_message = self._normalize_runtime_task_result(raw_task_result)
            if task_id:
                tasks = _read_data_annotation_scheduler_tasks()
                self._mark_scheduler_task(tasks, task_id, task_result)
            elif task_result == "success":
                self._mark_matching_scheduler_tasks_for_task_cell_success(task_type, payload)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({
                    "status": "success" if task_result == "success" else str(task_result or "success"),
                    "phase": "done",
                    "message": task_message or (f"{self._runtime_task_label(task_type, payload)}完成" if task_result == "success" else f"{self._runtime_task_label(task_type, payload)}已跳过"),
                    "finished_at": time.time(),
                    "updated_at": time.time(),
                    "current_index": 1,
                    "current_code": "",
                })
                self._log_locked("success" if task_result == "success" else "skip", self._status["message"])
                self._append_runtime_cell_log_locked(
                    title=f"执行任务：{self._runtime_task_label(task_type, payload)}",
                    source=self._runtime_task_cell_source(task_type, payload, local_run=bool(payload.get("__local_run"))),
                )
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "任务已停止")
                self._append_runtime_cell_log_locked(
                    title=f"执行任务：{self._runtime_task_label(task_type, payload)}",
                    source=self._runtime_task_cell_source(task_type, payload, local_run=bool(payload.get("__local_run"))),
                )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail))
                self._append_runtime_cell_log_locked(
                    title=f"执行任务：{self._runtime_task_label(task_type, payload)}",
                    source=self._runtime_task_cell_source(task_type, payload, local_run=bool(payload.get("__local_run"))),
                )
        finally:
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._mark_service_heartbeat("scheduler_poll")
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
                try:
                    while self._run_direct_runtime_action(
                        lambda: self._clear_known_blocking_overlay_if_possible(ctx, stop_event, label="Scheduler"),
                        stop_event=stop_event,
                        max_runtime_seconds=60.0,
                    ):
                        pass
                except RuntimeError as exc:
                    blocking_message = str(exc)
                    self._mark_scheduler_tasks_blocked(all_tasks, tasks[index:], blocking_message)
                    raise RuntimeError(blocking_message) from exc
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
                    self._persist_status()
                    self._mark_scheduler_task(all_tasks, task_id, "running")
                    task_payload = _data_annotation_task_payload_with_meta(task)
                    result, _result_message = self._execute_registered_task_cell(
                        runtime_ctx=ctx,
                        asset_tree_path=asset_tree_path,
                        stop_event=stop_event,
                        task_type=str(task.get("task_type") or ""),
                        payload=task_payload,
                        label=label,
                    )
                    self._mark_scheduler_task(all_tasks, task_id, result or "success")
                    with self._lock:
                        self._log_locked("success" if (result or "success") == "success" else "skip", f"到期任务{('完成' if (result or 'success') == 'success' else '跳过')}：{label}")
                except InterruptedError:
                    raise
                except Exception:
                    self._cleanup_failed_scheduler_task_to_world(
                        ctx=ctx,
                        asset_tree_path=asset_tree_path,
                        task_label=label,
                    )
                    raise
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
            current_task_id = ""
            with self._lock:
                current_task_id = str(self._status.get("current_task_id") or "")
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "Scheduler 任务已停止")
            if current_task_id:
                self._mark_scheduler_task(all_tasks, current_task_id, "stopped")
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
            self._mark_service_heartbeat("scheduler_poll")
            self._persist_status()

    def _cleanup_failed_scheduler_task_to_world(
        self,
        *,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        task_label: str,
    ) -> bool:
        """Best-effort atomic cleanup after a Scheduler task fails.

        A fresh stop event is deliberate: a normal task exception should still
        return the game to the stable world anchor, while an explicit user stop
        bypasses this helper in ``_run_scheduler_tasks``.
        """
        cleanup_stop_event = threading.Event()
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：任务失败，收尾返回世界 #34",
                phase="scheduler_failure_cleanup",
            )
            self._log_locked("action", f"{task_label}：任务失败，通用场景规划收尾到 #34")
        self._persist_status()
        try:
            self._run_direct_runtime_action(
                lambda: self._go_scene_task(
                    ctx,
                    asset_tree_path,
                    34,
                    cleanup_stop_event,
                ),
                stop_event=cleanup_stop_event,
                max_runtime_seconds=120.0,
            )
        except Exception as cleanup_exc:
            try:
                runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=cleanup_stop_event)
                scene_id, score, _frame = runtime.current_scene([34], update=True)
            except Exception:
                scene_id, score = None, 0.0
            if scene_id == 34:
                with self._lock:
                    self._status.update({"current_scene": 34, "updated_at": time.time()})
                    self._log_locked(
                        "success",
                        f"{task_label}：失败收尾已到 #34 {score:.0f}%（来源 shape 落点未声明，但目标锚点已可靠确认）",
                    )
                return True
            with self._lock:
                self._log_locked("warning", f"{task_label}：失败收尾未回到 #34：{cleanup_exc}")
            return False
        with self._lock:
            self._log_locked("success", f"{task_label}：失败收尾已回到 #34")
        return True

    def _find_asset_image_by_title(self, ctx: dict[str, Any], title: str) -> dict[str, Any] | None:
        def visit(nodes: Any) -> dict[str, Any] | None:
            if not isinstance(nodes, list):
                return None
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if node.get("type") == "image" and str(node.get("title") or "") == title:
                    return node
                found = visit(node.get("children"))
                if found is not None:
                    return found
            return None

        return visit(ctx.get("asset_tree"))

    def _known_blocking_overlay_info(self, ctx: dict[str, Any]) -> dict[str, Any] | None:
        def shape_titles(image: dict[str, Any] | None) -> list[str]:
            if not isinstance(image, dict):
                return []
            titles: list[str] = []
            for shape in image.get("shapes") or []:
                if isinstance(shape, dict):
                    title = str(shape.get("title") or "").strip()
                    if title:
                        titles.append(title)
            return titles

        frame = self._screencap(ctx)
        ocr_lines = self._ocr_lines(frame)
        text = self._ocr_text(ocr_lines)
        if "游戏公告" in text or ("更新公告" in text and "风险提醒" in text):
            image = self._find_asset_image_by_title(ctx, "游戏公告")
            close_shape = self._known_game_announcement_action_shape(image)
            if close_shape is None:
                return {
                    "scene_id": None,
                    "title": "游戏公告",
                    "blocking": True,
                    "all_shapes": shape_titles(image),
                    "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，无法安全进入游戏",
                }
            return {
                "scene_id": None,
                "title": "游戏公告",
                "blocking": False,
                "all_shapes": shape_titles(image),
                "action_shapes": [str(close_shape.get("title") or "")],
                "message": "检测到游戏公告遮挡，已有安全关闭动作标注",
            }
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if "破界符" in compact and "购买并使用" in compact and ("剩余限购次数" in compact or "价格" in compact):
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            candidate = images.get(224)
            image224 = candidate if isinstance(candidate, dict) else None
            candidate225 = images.get(225)
            image225 = candidate225 if isinstance(candidate225, dict) else None
            use_shape = self._find_shape(image224, "购买并使用")
            blank_shape = self._find_shape(image225, "空白")
            if use_shape is None or blank_shape is None:
                missing: list[str] = []
                if use_shape is None:
                    missing.append("#224「购买并使用」")
                if blank_shape is None:
                    missing.append("#225「空白」")
                return {
                    "scene_id": 224,
                    "title": "购买破界符",
                    "blocking": True,
                    "all_shapes": shape_titles(image224),
                    "message": f"检测到 #224「购买破界符」弹窗；资产树缺少 {'、'.join(missing)}，无法按 #224 连续购买到 #225 后回退",
                }
            return {
                "scene_id": 224,
                "title": "购买破界符",
                "blocking": False,
                "all_shapes": shape_titles(image224),
                "action_shapes": ["购买并使用", "#225 空白"],
                "message": "检测到 #224「购买破界符」弹窗，已有连续购买与 #225 回退标注",
            }
        return None

    def _known_blocking_overlay_message(self, ctx: dict[str, Any]) -> str | None:
        info = self._known_blocking_overlay_info(ctx)
        if not isinstance(info, dict) or not bool(info.get("blocking")):
            return None
        return str(info.get("message") or "")

    def _known_blocking_overlay_action(self, ctx: dict[str, Any], info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        title = str(info.get("title") or "")
        image: dict[str, Any] | None = None
        if title == "灵祖奖励浮层":
            images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
            candidate = images.get(186)
            image = candidate if isinstance(candidate, dict) else None
        elif title == "游戏公告":
            image = self._find_asset_image_by_title(ctx, "游戏公告")
        if not isinstance(image, dict):
            return None
        if title == "游戏公告":
            shape = self._known_game_announcement_action_shape(image)
        else:
            shape = self._find_shape(image, "关闭") or self._find_shape(image, "空白") or self._find_shape(image, "返回") or self._find_shape(image, "退出")
        if not isinstance(shape, dict):
            return None
        return image, shape

    def _known_game_announcement_action_shape(self, image: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(image, dict):
            return None
        shape = self._find_shape(image, "关闭公告")
        return shape if isinstance(shape, dict) else None

    def _clear_known_blocking_overlay_if_possible(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        *,
        label: str = "Scheduler",
        timeout: float = 12.0,
    ):
        info = self._known_blocking_overlay_info(ctx)
        if not isinstance(info, dict):
            return False
        message = str(info.get("message") or "检测到阻断浮层")
        if bool(info.get("blocking")):
            raise RuntimeError(message)
        action = self._known_blocking_overlay_action(ctx, info)
        if action is None:
            return False
        image, shape = action
        title = str(info.get("title") or image.get("title") or "阻断浮层")
        shape_title = str(shape.get("title") or "关闭")
        frame = self._screencap(ctx)
        with self._lock:
            self._set_status_locked("running", f"{label}：关闭{title}", phase="clear_blocking_overlay")
            self._log_locked("action", f"{label}：点击「{title}/{shape_title}」清理阻断浮层", scope="job", item_id="scheduler")
        x, y = ActionPlanner().shape_center(image, shape)
        click_ref = dict(image)
        click_ref["title"] = (title, shape_title)
        self._click_frame_point(ctx, click_ref, x, y)
        deadline = time.monotonic() + max(0.5, float(timeout or 12.0))
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.0)
            self._clear_tick_frame(ctx)
            next_info = self._known_blocking_overlay_info(ctx)
            if not isinstance(next_info, dict) or str(next_info.get("title") or "") != title:
                with self._lock:
                    self._log_locked("success", f"{label}：{title}已清理", scope="job", item_id="scheduler")
                return True
            if bool(next_info.get("blocking")):
                raise RuntimeError(str(next_info.get("message") or message))
        raise RuntimeError(f"{label}：点击「{title}/{shape_title}」后阻断浮层仍未消失")

    def _mark_scheduler_task(self, tasks: list[dict[str, Any]], task_id: str, result: str) -> None:
        if not task_id:
            return
        task_ids = {str(item.get("id") or "") for item in tasks if isinstance(item, dict)}
        write_tasks = tasks
        scheduler_state_path = _data_annotation_scheduler_state_path()
        if scheduler_state_path.exists():
            persisted_tasks = _read_data_annotation_scheduler_tasks()
            persisted_ids = {str(item.get("id") or "") for item in persisted_tasks if isinstance(item, dict)}
            if task_id in persisted_ids and task_ids != persisted_ids:
                write_tasks = persisted_tasks
        now_text = _now().strftime("%Y-%m-%d %H:%M:%S")
        changed = False
        for item in write_tasks:
            if str(item.get("id") or "") != task_id:
                continue
            if result == "running":
                item["last_run_at"] = now_text
                item["retry_after"] = None
            elif result == "success":
                fact_retry_after = self._scheduler_task_runtime_discovered_retry_after(item)
                fact_next_time = self._scheduler_task_runtime_discovered_next_time(item)
                if fact_retry_after:
                    item["last_run_at"] = now_text
                    item["next_time"] = None
                    item["retry_after"] = fact_retry_after
                    item["last_result"] = "skipped"
                    changed = True
                    break
                item["last_run_at"] = now_text
                item["retry_after"] = None
                if str(item.get("schedule_kind") or "") in {"daily", "weekly"}:
                    if fact_next_time:
                        fact_next_ts = parse_data_annotation_task_time(fact_next_time)
                        if fact_next_ts is None or fact_next_ts <= time.time():
                            fact_next_time = ""
                    item["next_time"] = fact_next_time if fact_next_time else _next_data_annotation_scheduler_time(item)
                else:
                    fact_next_time = self._scheduler_task_fact_next_time(str(item.get("id") or ""))
                    item["next_time"] = fact_next_time if fact_next_time else None
            elif result in {"skipped", "unsupported"}:
                item["last_run_at"] = now_text
                fact_next_time = self._scheduler_task_fact_next_time(str(item.get("id") or ""))
                if fact_next_time:
                    item["next_time"] = fact_next_time
                    item["retry_after"] = None
                    item["last_result"] = result
                    changed = True
                    break
                fact_retry_after = self._scheduler_task_fact_retry_after(str(item.get("id") or ""))
                if fact_retry_after:
                    item["next_time"] = None
                    item["retry_after"] = fact_retry_after
                    item["last_result"] = result
                    changed = True
                    break
                cooldown_seconds = int(item.get("cooldown_seconds") or 600)
                item["next_time"] = None
                item["retry_after"] = (_now() + timedelta(seconds=cooldown_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            elif result in {"error", "stopped"}:
                item["last_run_at"] = now_text
                cooldown_seconds = int(item.get("cooldown_seconds") or 600)
                item["next_time"] = None
                item["retry_after"] = (_now() + timedelta(seconds=cooldown_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            elif result == "blocked":
                item["retry_after"] = None
            item["last_result"] = result
            changed = True
            break
        if changed:
            _write_data_annotation_scheduler_tasks(write_tasks)
            if write_tasks is not tasks:
                for original in tasks:
                    if str(original.get("id") or "") == task_id:
                        original.clear()
                        original.update(item)
                        break
            _record_data_annotation_scheduler_task_fact(item, result)

    def _mark_scheduler_tasks_blocked(self, tasks: list[dict[str, Any]], due_tasks: list[dict[str, Any]], message: str) -> None:
        due_ids = {str(task.get("id") or "") for task in due_tasks if str(task.get("id") or "")}
        if not due_ids:
            return
        changed = False
        now_ts = time.time()
        for item in tasks:
            if str(item.get("id") or "") not in due_ids:
                continue
            item["last_result"] = "blocked"
            checkpoint = item.get("checkpoint") if isinstance(item.get("checkpoint"), dict) else {}
            checkpoint = dict(checkpoint)
            manual_note = checkpoint.pop("manual_inspection_note", None)
            if manual_note:
                checkpoint["previous_manual_inspection_note"] = manual_note
            item["checkpoint"] = {**checkpoint, "blocked_message": message, "blocked_at": now_ts}
            item["retry_after"] = None
            _record_data_annotation_scheduler_task_fact(item, "blocked")
            changed = True
        if changed:
            _write_data_annotation_scheduler_tasks(tasks)

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

    def _scheduler_task_runtime_discovered_next_time(self, item: dict[str, Any]) -> str | None:
        task_id = str(item.get("id") or "").strip()
        if not task_id:
            return None
        facts = _read_data_annotation_world_facts()
        discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
        task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
        fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
        value = str(fact.get("discovered_next_time") or "").strip()
        if not value or "updated_at" not in fact:
            return None
        last_run_at = str(item.get("last_run_at") or "").strip()
        if last_run_at:
            try:
                started_at = datetime.strptime(last_run_at, "%Y-%m-%d %H:%M:%S").timestamp()
                if float(fact.get("updated_at") or 0) + 1e-6 >= started_at:
                    return value
            except (TypeError, ValueError):
                return None
        if str(item.get("last_result") or "") == "running":
            return value
        return None

    def _scheduler_task_runtime_discovered_retry_after(self, item: dict[str, Any]) -> str | None:
        task_id = str(item.get("id") or "").strip()
        if not task_id:
            return None
        facts = _read_data_annotation_world_facts()
        discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
        task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
        fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
        value = ""
        for key in ("discovered_retry_after", "retry_after"):
            value = str(fact.get(key) or "").strip()
            if value:
                break
        if not value or "updated_at" not in fact:
            return None
        last_result = str(fact.get("last_result") or "")
        if last_result and last_result not in {"skipped", "unsupported", "error", "stopped"}:
            return None
        last_run_at = str(item.get("last_run_at") or "").strip()
        if last_run_at:
            try:
                started_at = datetime.strptime(last_run_at, "%Y-%m-%d %H:%M:%S").timestamp()
                if float(fact.get("updated_at") or 0) + 1e-6 >= started_at:
                    return value
            except (TypeError, ValueError):
                return None
        if str(item.get("last_result") or "") == "running":
            return value
        return None

    def _scheduler_task_fact_retry_after(self, task_id: str) -> str | None:
        if not task_id:
            return None
        facts = _read_data_annotation_world_facts()
        discoveries = facts.get("discoveries") if isinstance(facts.get("discoveries"), dict) else {}
        task_facts = discoveries.get("task") if isinstance(discoveries.get("task"), dict) else {}
        fact = task_facts.get(task_id) if isinstance(task_facts.get(task_id), dict) else {}
        for key in ("discovered_retry_after", "retry_after"):
            value = str(fact.get(key) or "").strip()
            if value:
                return value
        return None

    def _scheduler_task_next_time_from_schedule(self, task_id: str, task_type: str = "") -> str | None:
        task_id = str(task_id or "").strip()
        task_type = str(task_type or "").strip()
        if not task_id and not task_type:
            return None
        for item in _read_data_annotation_scheduler_tasks():
            if task_id and str(item.get("id") or "") == task_id:
                return _next_data_annotation_scheduler_time(item)
            if task_type and str(item.get("task_type") or "") == task_type:
                return _next_data_annotation_scheduler_time(item)
        return None

    def _record_scheduler_task_discovered_next_time(
        self,
        task_id: str,
        next_time_text: str,
        *,
        task_type: str,
        label: str,
        last_result: str | None = None,
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
            "next_time": next_time_text,
            "discovered_retry_after": None,
            "retry_after": None,
            "updated_at": time.time(),
        }
        if last_result:
            task_facts[task_id]["last_result"] = str(last_result)
            task_facts[task_id]["last_run_at"] = _now().strftime("%Y-%m-%d %H:%M:%S")
        _write_data_annotation_world_facts(facts)

    def _record_scheduler_task_discovered_retry_after(
        self,
        task_id: str,
        retry_after_text: str,
        *,
        task_type: str,
        label: str,
        last_result: str | None = "skipped",
    ) -> None:
        task_id = str(task_id or "").strip()
        retry_after_text = str(retry_after_text or "").strip()
        if not task_id or not retry_after_text:
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
            "discovered_next_time": None,
            "next_time": None,
            "discovered_retry_after": retry_after_text,
            "retry_after": retry_after_text,
            "updated_at": time.time(),
        }
        if last_result:
            task_facts[task_id]["last_result"] = str(last_result)
            task_facts[task_id]["last_run_at"] = _now().strftime("%Y-%m-%d %H:%M:%S")
        _write_data_annotation_world_facts(facts)

    def _mark_matching_scheduler_tasks_for_task_cell_success(self, task_type: str, payload: dict[str, Any]) -> None:
        if str(payload.get("__scheduler_task_id") or ""):
            return
        normalized_task_type = self._canonical_runtime_task_type(task_type)
        if not normalized_task_type:
            return
        if normalized_task_type == "daily_boss":
            self._log("detail", "日常_首领作业不使用通用成功同步；只按奖励次数或刷新 CD 写入下次复查")
            return
        tasks = _read_data_annotation_scheduler_tasks()
        matched = False
        for item in tasks:
            if self._canonical_runtime_task_type(str(item.get("task_type") or "")) != normalized_task_type:
                continue
            self._record_scheduler_task_discovered_next_time(
                str(item.get("id") or ""),
                _next_data_annotation_scheduler_time(item) or self._next_daily_boss_reset_time_text(),
                task_type=str(item.get("task_type") or normalized_task_type),
                label=str(item.get("label") or normalized_task_type),
                last_result="success",
            )
            self._mark_scheduler_task(tasks, str(item.get("id") or ""), "success")
            matched = True
        if matched:
            self._log("detail", f"作业成功后已同步清理同类 Scheduler 重试：{normalized_task_type}")

    def _execute_runtime_task(self, ctx: dict[str, Any], task_type: str, payload: dict[str, Any], stop_event: threading.Event) -> str:
        task_type = self._canonical_runtime_task_type(task_type)
        if task_type in {"legacy_daily_task", "legacy_dynamic_task"}:
            legacy_name = str(payload.get("legacy_name") or task_type)
            self._log("skip", f"旧版任务「{legacy_name}」尚未迁移，已跳过")
            return "unsupported"
        ensure_fanxiu_runtime_jobs_registered()
        definition = _data_annotation_task_cell_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        normalized_payload = dict(payload or {})
        if definition.normalize_payload is not None:
            normalized_payload = definition.normalize_payload(normalized_payload)
        result = definition.handler(self, ctx, normalized_payload, stop_event)
        if isinstance(result, GeneratorType):
            return result
        return str(result or "success")

    def _try_enter_daily_youli_from_world_mainline(
        self,
        ctx: dict[str, Any],
        runtime: FanxiuRuntime,
        stop_event: threading.Event,
        payload: dict[str, Any],
        image34: dict[str, Any],
        image228: dict[str, Any],
        *,
        task_label: str,
    ):
        if not bool(payload.get("youli_mainline_shortcut", True)):
            return False
        mainline_shape = self._find_shape(image34, "主线")
        if mainline_shape is None:
            self._log("detail", f"{task_label}：#34 缺少「主线」标注，跳过快路径")
            return False
        with self._lock:
            self._set_status_locked("running", f"{task_label}：尝试 #34「主线」快路径", phase="daily_youli_mainline_shortcut", current_scene=34)
            self._log_locked("action", f"{task_label}：点击 #34「主线」尝试直达修仙传游历")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image34,
            mainline_shape,
            payload,
            label=f"{task_label}：点击 #34「主线」",
            timeout_key="youli_mainline_click_timeout",
        )
        yield from self._wait_runtime_action_settle(
            ctx,
            stop_event,
            seconds=float(payload.get("youli_mainline_settle_seconds") or 2.0),
        )
        try:
            view = yield from runtime.wait_view(
                228,
                71,
                timeout=float(payload.get("youli_mainline_wait_timeout") or 12.0),
                label=f"{task_label}：等待主线快路径进入修仙传",
            )
        except TimeoutError as exc:
            self._log("warning", f"{task_label}：主线快路径未进入修仙传，回退日常入口：{exc}")
            return False
        scene_id = view.id if isinstance(view, View) else int(view) if isinstance(view, int) else None
        if scene_id == 228:
            yield from self._select_daily_youli_tab_from_menu_if_visible(ctx, stop_event, payload, image228, task_label=task_label)
        frame = self._screencap(ctx)
        text = self._ocr_text(self._ocr_lines(frame))
        scene_id, _score = self._identify_scene_number(ctx, frame, [228, 71])
        if scene_id == 71:
            self._log("success", f"{task_label}：主线快路径进入修仙传菜单")
            return True
        if self._daily_youli_text_is_home(text):
            self._log("success", f"{task_label}：主线快路径进入修仙传游历")
            return True
        self._log("warning", f"{task_label}：主线快路径落点不是游历页，回退日常入口，OCR={text[:120]}")
        return False

    def _select_daily_youli_tab_from_menu_if_visible(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image228: dict[str, Any],
        *,
        task_label: str,
    ):
        menu_shape = self._find_shape(image228, "菜单")
        if menu_shape is None:
            self._log("detail", f"{task_label}：#228 缺少「菜单」区域，跳过游历菜单选择")
            return False
        try:
            runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
            with self._lock:
                self._set_status_locked("running", f"{task_label}：选择 #228[菜单/游历]", phase="daily_youli_select_menu", current_scene=228)
                self._log_locked("action", f"{task_label}：wait_click #228[菜单/游历]")
            yield from runtime.wait_click(
                228,
                "[菜单/游历]",
                timeout=float(payload.get("youli_menu_click_timeout") or payload.get("shape_click_timeout") or 8.0),
            )
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=float(payload.get("youli_menu_settle_seconds") or 1.0),
            )
            return True
        except RuntimeError as exc:
            self._log("detail", f"{task_label}：#228[菜单/游历] 通用点击未命中，保留当前页：{exc}")
            return False

    def _select_daily_youli_from_xiuxianzhuan_menu(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image71: dict[str, Any] | None,
        *,
        task_label: str,
    ):
        if not isinstance(image71, dict):
            raise RuntimeError(f"{task_label}：缺少 #71「修仙传」标注，无法从菜单选择游历")
        frame = self._screencap(ctx)
        lines = self._ocr_lines(frame)
        width, height = self._frame_size(image71)
        min_y = height * float(payload.get("youli_menu_min_y_ratio") or 0.62)
        candidates: list[tuple[float, float, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if "游历" not in text:
                continue
            center = self._ocr_substring_center(line, "游历")
            if center is None:
                continue
            cx, cy = center
            if cy >= min_y:
                candidates.append((cx, cy, text))
        if not candidates:
            raise RuntimeError(f"{task_label}：#71 菜单未识别到「游历」入口")
        x, y, text = sorted(candidates, key=lambda item: (item[1], item[0]))[0]
        with self._lock:
            self._set_status_locked("running", f"{task_label}：从 #71 菜单选择「{text}」", phase="daily_youli_select_xiuxianzhuan_menu", current_scene=71)
            self._log_locked("action", f"{task_label}：点击 #71 菜单 OCR「{text}」")
        self._click_frame_point(ctx, image71, x, y)
        yield from self._wait_runtime_action_settle(
            ctx,
            stop_event,
            seconds=float(payload.get("youli_menu_settle_seconds") or 1.5),
        )
        return True






































    def _daily_xianshi_text_is_coin_list(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "秘藏阁" in normalized and "天衍灵石" in normalized and "仙币" in normalized

    def _daily_xianshi_text_is_box_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "灵石仙币宝匣" in normalized and ("领取" in normalized or "打开可获得" in normalized)

    def _record_daily_xianshi_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-xianshi"),
            next_time,
            task_type="daily_xianshi",
            label="仙市_秘藏阁",
        )
        self._log("success", f"仙市_秘藏阁：{message}，下次 {next_time}")
        return next_time

    def _record_daily_xianshi_retry(self, payload: dict[str, Any], *, message: str, seconds: int) -> str:
        task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-xianshi").strip() or "legacy-daily-xianshi"
        retry_after = (_now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
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
            "task_type": "daily_xianshi",
            "label": "仙市_秘藏阁",
            "source": "data_annotation_runtime",
            "schedule_kind": "dynamic",
            "discovered_next_time": None,
            "next_time": None,
            "discovered_retry_after": retry_after,
            "retry_after": retry_after,
            "last_result": "skipped",
            "updated_at": time.time(),
        }
        _write_data_annotation_world_facts(facts)
        self._log("skip", f"仙市_秘藏阁：{message}，{retry_after} 重试")
        return retry_after

    def _record_daily_vip_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_vip_reset_time_text()
        self._record_scheduler_task_discovered_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-vip"),
            next_time,
            task_type="daily_vip",
            label="日常_vip",
        )
        self._log("success", f"日常_vip：{message}，下次 {next_time}")
        return next_time

    def _next_daily_vip_reset_time_text(self) -> str:
        now = _now()
        reset_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if reset_at <= now:
            reset_at += timedelta(days=1)
        return reset_at.strftime("%Y-%m-%d %H:%M:%S")

    def _open_daily_xianshi_coin_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image34: dict[str, Any],
        image247: dict[str, Any],
        image248: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from self._ensure_world_main_for_right_menu(ctx, runtime, stop_event, image34, task_label=task_label)
        yield from runtime.wait_click_then_shape(
            34,
            "仙市",
            247,
            "秘藏阁",
            settle_seconds=2.0,
            timeout=float(payload.get("xianshi_entry_wait_seconds") or 6.0),
            retry_if_source_remains=True,
            max_clicks=int(payload.get("xianshi_entry_max_clicks") or 3),
            label=f"{task_label}：等待仙市入口页",
        )
        yield from runtime.wait_click_then_shape(
            247,
            "秘藏阁",
            248,
            "仙币",
            settle_seconds=1.5,
            label=f"{task_label}：等待秘藏阁仙币页",
        )
        yield from runtime.wait_click(248, "仙币")
        yield from runtime.wait_action_settle(float(payload.get("coin_tab_settle_seconds") or 2.5))

    def _ensure_world_main_for_right_menu(
        self,
        ctx: dict[str, Any],
        runtime: Any,
        stop_event: threading.Event,
        image34: dict[str, Any],
        *,
        task_label: str,
    ):
        frame = runtime.cur_frame(update=True)
        text = runtime.ocr_text(frame)
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if "仙市" in compact and ("仙府" in compact or "储物袋" in compact):
            return
        width, height = self._frame_size(image34)
        candidates: list[tuple[float, float, str]] = []
        for line in runtime.ocr_lines(frame):
            line_text = _sanitize_ocr_text(line.get("text"))
            if "世界" not in line_text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            cx = x + w / 2
            cy = y + h / 2
            if cx <= width * 0.22 and cy >= height * 0.72:
                center = self._ocr_substring_center(line, "世界")
                if center is not None:
                    cx, cy = center
                candidates.append((float(cx), float(cy), line_text))
        if not candidates:
            return
        x, y, source_text = sorted(candidates, key=lambda item: (item[1], item[0]))[-1]
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：当前 #34 不是世界主态，点击底部「世界」恢复右侧菜单",
                phase="world_main_restore_for_right_menu",
                current_scene=34,
            )
            self._log_locked("action", f"{task_label}：OCR 命中底部「世界」({source_text})，点击恢复世界主态 ({x:.0f},{y:.0f})")
        runtime.click_frame_point(View(image34), x, y)
        yield from runtime.wait_action_settle(2.0)

    def _click_daily_xianshi_free_coin_box(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image249: dict[str, Any],
        image250: dict[str, Any],
        *,
        task_label: str,
    ):
        del image249
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        self._raise_if_stopped(stop_event)
        with self._lock:
            self._status.update({
                "phase": "daily_xianshi_open_coin_box",
                "message": f"{task_label}：点击灵石仙币宝匣进入详情",
                "updated_at": time.time(),
            })
            self._log_locked("action", f"{task_label}：点击 #249「灵石仙币宝匣」")
        runtime.click_shape_center(249, "灵石仙币宝匣")
        yield from runtime.wait_action_settle(float(payload.get("coin_box_settle_seconds") or 1.5))
        try:
            return (yield from self._claim_daily_xianshi_coin_box(ctx, stop_event, payload, image250, task_label=task_label))
        except Exception as exc:
            if not self._daily_xianshi_claim_shape_missing_error(exc):
                raise
            self._log("success", f"{task_label}：#250 未匹配「领取」，视为今日已无可领免费项：{exc}")
            yield from self._return_daily_xianshi_box_detail_to_coin_list(ctx, stop_event, payload, image250, task_label=task_label)
            return "not_free"

    def _daily_xianshi_claim_shape_missing_error(self, exc: Exception) -> bool:
        message = str(exc)
        compact = re.sub(r"\s+", "", message)
        if "250" not in compact or "领取" not in compact:
            return False
        return any(token in compact for token in ("未匹配", "超时", "timeout", "Timeout", "0%"))

    def _daily_xianshi_text_indicates_no_free_coin_box(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        if not normalized:
            return False
        if "免费" in normalized:
            return False
        return "宝匣" in normalized and any(fragment in normalized for fragment in ("兑换所需", "价格", "所需"))

    def _return_daily_xianshi_box_detail_to_coin_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image250: dict[str, Any],
        *,
        task_label: str,
    ):
        del image250
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        runtime.click_shape_center(250, "返回")
        yield from runtime.wait_action_settle(float(payload.get("coin_box_return_settle_seconds") or 1.0))
        return "success"

    def _claim_daily_xianshi_coin_box(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image250: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        yield from runtime.wait_click(250, "领取")
        yield from runtime.wait_action_settle(float(payload.get("claim_settle_seconds") or 1.5))
        text = runtime.ocr_text(update=True)
        self._log("success", f"{task_label}：领取后 OCR={text[:120]}")
        return True

    def _return_daily_xianshi_to_world(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        image249: dict[str, Any],
        *,
        task_label: str,
    ):
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(ctx, asset_tree_path if isinstance(asset_tree_path, Path) else None, stop_event=stop_event)
        with self._lock:
            self._log_locked("action", f"{task_label}：收尾前往 #34")
        yield from runtime.goto_view(
            34,
            layer0_wait_seconds=float(payload.get("return_world_layer0_wait_seconds") or 2.0),
        )
        yield from runtime.wait_view(34, label=f"{task_label}：等待世界 #34")


























    def _wait_daily_shuangxiu_invite(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        timeout = float(payload.get("invite_timeout") or 12.0)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if self._daily_shuangxiu_text_is_invite(text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_双修：已进入邀请页",
                        phase="daily_shuangxiu_invite_ready",
                        current_scene=217,
                    )
                return frame
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_双修：等待邀请页",
                    phase="daily_shuangxiu_wait_invite",
                    current_scene=None,
                )
            if time.monotonic() - start >= timeout:
                raise RuntimeError(f"日常_双修：等待邀请页超时，OCR={last_text[:120]}")

    def _click_daily_shuangxiu_xianyuan_tab(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image217 = ctx.get("images", {}).get(217)
        if not isinstance(image217, dict):
            raise RuntimeError("日常_双修：缺少 #217「双修邀请」标注，无法点击仙缘页签")
        tab_shape = self._find_shape(image217, "仙缘页签", "shape 1")
        if tab_shape is None:
            raise RuntimeError("日常_双修：#217 缺少「仙缘页签」标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #217「仙缘」",
                phase="daily_shuangxiu_click_xianyuan_tab",
                current_scene=217,
            )
            self._log_locked("action", "日常_双修：点击 #217「仙缘」页签")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image217,
            tab_shape,
            payload,
            label="日常_双修：等待 #217「仙缘」页签",
            timeout_key="xianyuan_tab_click_timeout",
        )
        settle_seconds = float(payload.get("xianyuan_tab_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=settle_seconds)
        return (yield from self._click_daily_shuangxiu_first_partner(ctx, stop_event, payload))

    def _click_daily_shuangxiu_first_partner(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image218 = ctx.get("images", {}).get(218)
        if not isinstance(image218, dict):
            raise RuntimeError("日常_双修：缺少 #218「双修仙缘邀请列表」标注，无法点击邀请按钮")
        invite_shape = self._find_shape(image218, "邀请按钮", "shape 1")
        if invite_shape is None:
            raise RuntimeError("日常_双修：#218 缺少「邀请按钮」浮动标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #218 第一个可用邀请",
                phase="daily_shuangxiu_click_first_partner",
                current_scene=218,
            )
            self._log_locked("action", "日常_双修：点击 #218「邀请按钮」第一个匹配项")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image218,
            invite_shape,
            payload,
            label="日常_双修：等待 #218「邀请按钮」",
            timeout_key="partner_invite_click_timeout",
        )
        settle_seconds = float(payload.get("partner_invite_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=settle_seconds)
        yield from self._wait_daily_shuangxiu_training_ready(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_start_training(ctx, stop_event, payload))

    def _wait_daily_shuangxiu_training_ready(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        timeout = float(payload.get("training_ready_timeout") or 12.0)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if self._daily_shuangxiu_text_is_training_ready(text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_双修：已进入修炼准备页",
                        phase="daily_shuangxiu_training_ready",
                        current_scene=219,
                    )
                return frame
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_双修：等待修炼准备页",
                    phase="daily_shuangxiu_wait_training_ready",
                    current_scene=None,
                )
            if time.monotonic() - start >= timeout:
                raise RuntimeError(f"日常_双修：等待修炼准备页超时，OCR={last_text[:120]}")

    def _click_daily_shuangxiu_start_training(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image219 = ctx.get("images", {}).get(219)
        if not isinstance(image219, dict):
            raise RuntimeError("日常_双修：缺少 #219「双修修炼准备」标注，无法点击前往修炼")
        start_shape = self._find_shape(image219, "前往修炼", "shape 1")
        if start_shape is None:
            raise RuntimeError("日常_双修：#219 缺少「前往修炼」按钮标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #219「前往修炼」",
                phase="daily_shuangxiu_click_start_training",
                current_scene=219,
            )
            self._log_locked("action", "日常_双修：点击 #219「前往修炼」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image219,
            start_shape,
            payload,
            label="日常_双修：等待 #219「前往修炼」",
            timeout_key="start_training_click_timeout",
        )
        settle_seconds = float(payload.get("start_training_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=settle_seconds)
        yield from self._wait_daily_shuangxiu_complete(ctx, stop_event, payload)
        return (yield from self._click_daily_shuangxiu_continue(ctx, stop_event, payload))

    def _wait_daily_shuangxiu_complete(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        timeout = float(payload.get("training_complete_timeout") or 18.0)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if self._daily_shuangxiu_text_is_complete(text):
                with self._lock:
                    self._set_status_locked(
                        "running",
                        "日常_双修：已进入修炼完成页",
                        phase="daily_shuangxiu_complete_ready",
                        current_scene=221,
                    )
                return frame
            with self._lock:
                self._set_status_locked(
                    "running",
                    "日常_双修：等待修炼完成页",
                    phase="daily_shuangxiu_wait_complete",
                    current_scene=None,
                )
            if time.monotonic() - start >= timeout:
                raise RuntimeError(f"日常_双修：等待修炼完成页超时，OCR={last_text[:120]}")

    def _click_daily_shuangxiu_continue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        self._raise_if_stopped(stop_event)
        image221 = ctx.get("images", {}).get(221)
        if not isinstance(image221, dict):
            raise RuntimeError("日常_双修：缺少 #221「双修修炼完成」标注，无法点击继续")
        continue_shape = self._find_shape(image221, "点击屏幕继续", "继续", "shape 1")
        if continue_shape is None:
            raise RuntimeError("日常_双修：#221 缺少「点击屏幕继续」按钮标注")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #221「点击屏幕继续」",
                phase="daily_shuangxiu_click_continue",
                current_scene=221,
            )
            self._log_locked("action", "日常_双修：点击 #221「点击屏幕继续」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image221,
            continue_shape,
            payload,
            label="日常_双修：等待 #221「点击屏幕继续」",
            timeout_key="complete_continue_click_timeout",
        )
        settle_seconds = float(payload.get("complete_continue_settle_seconds") or 2.0)
        if settle_seconds > 0:
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=settle_seconds)
        return (yield from self._finish_daily_shuangxiu_after_continue(ctx, stop_event, payload))

    def _finish_daily_shuangxiu_after_continue(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ) -> str:
        timeout = float(payload.get("after_complete_timeout") or 8.0)
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame, [34, 289, 86])
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            if scene_id == 34 or self._daily_lingta_text_is_world_like(text):
                return self._complete_daily_shuangxiu_after_continue(current_scene=34)
            if scene_id in {289, 86} or self._leave_scene_confirm_text(text):
                yield from self._confirm_daily_shuangxiu_leave(ctx, stop_event, payload, scene_id=scene_id)
                yield from self._wait_scene_id(
                    ctx,
                    stop_event,
                    34,
                    timeout=float(payload.get("after_leave_world_timeout") or 12.0),
                    label="日常_双修：等待离开后回到世界 #34",
                )
                return self._complete_daily_shuangxiu_after_continue(current_scene=34)
            if self._daily_shuangxiu_text_is_training_ready(text):
                yield from self._leave_daily_shuangxiu_training_ready(ctx, stop_event, payload)
                yield from self._wait_scene_id(
                    ctx,
                    stop_event,
                    34,
                    timeout=float(payload.get("after_leave_world_timeout") or 12.0),
                    label="日常_双修：等待离开后回到世界 #34",
                )
                return self._complete_daily_shuangxiu_after_continue(current_scene=34)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_双修：等待修炼完成后落点，当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%",
                    phase="daily_shuangxiu_wait_after_complete",
                    current_scene=scene_id,
                )
            if time.monotonic() - start >= timeout:
                raise RuntimeError(f"日常_双修：点击完成继续后未回到 #34，不能按成功处理，OCR={last_text[:120]}")

    def _leave_daily_shuangxiu_training_ready(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
    ):
        image219 = ctx.get("images", {}).get(219)
        if not isinstance(image219, dict):
            raise RuntimeError("日常_双修：缺少 #219「双修修炼准备」标注，无法离开")
        leave_shape = self._find_shape(image219, "请离", "离开", "退出", "返回")
        if leave_shape is None:
            raise RuntimeError("日常_双修：#219 缺少「离开」按钮标注，无法完成收尾")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：点击 #219「离开」",
                phase="daily_shuangxiu_click_leave",
                current_scene=219,
            )
            self._log_locked("action", "日常_双修：点击 #219「离开」")
        yield from self._click_shape_respecting_conditions(
            ctx,
            stop_event,
            image219,
            leave_shape,
            payload,
            label="日常_双修：等待 #219「离开」",
            timeout_key="leave_click_timeout",
        )
        settle_seconds = float(payload.get("leave_click_settle_seconds") or 1.0)
        if settle_seconds > 0:
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=settle_seconds)
        frame = self._screencap(ctx)
        scene_id, _score = self._identify_scene_number(ctx, frame, [289, 86])
        text = self._ocr_text(self._ocr_lines(frame))
        if scene_id in {289, 86} or self._leave_scene_confirm_text(text):
            yield from self._confirm_daily_shuangxiu_leave(ctx, stop_event, payload, scene_id=scene_id)

    def _confirm_daily_shuangxiu_leave(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        scene_id: int | None = None,
    ):
        confirm_id = int(scene_id) if scene_id in {289, 86} else (289 if isinstance(ctx.get("images", {}).get(289), dict) else 86)
        confirm_image = ctx.get("images", {}).get(confirm_id)
        if not isinstance(confirm_image, dict):
            raise RuntimeError(f"日常_双修：缺少 #{confirm_id}「离开确认」标注，无法确认离开")
        confirm_shape = self._find_shape(confirm_image, "确认", "确定", "离开")
        if confirm_shape is None:
            raise RuntimeError(f"日常_双修：#{confirm_id} 缺少「离开/确认」按钮标注，无法确认离开")
        with self._lock:
            self._set_status_locked(
                "running",
                "日常_双修：确认离开场景",
                phase="daily_shuangxiu_confirm_leave",
                current_scene=confirm_id,
            )
            self._log_locked("action", f"日常_双修：点击 #{confirm_id}「{confirm_shape.get('title') or '确认'}」离开场景")
        box = self._box(confirm_shape, confirm_image)
        self._click_frame_point(
            ctx,
            confirm_image,
            float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
            float(box.get("y") or 0) + float(box.get("h") or 0) / 2,
        )
        settle_seconds = float(payload.get("leave_confirm_settle_seconds") or 1.0)
        if settle_seconds > 0:
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=settle_seconds)

    def _complete_daily_shuangxiu_after_continue(self, *, current_scene: int | None) -> str:
        with self._lock:
            self._set_status_locked(
                "success",
                "日常_双修：已点击修炼完成继续",
                phase="daily_shuangxiu_complete_continued",
                current_scene=current_scene,
            )
            self._log_locked("success", "日常_双修：已点击 #221「点击屏幕继续」")
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
            if 34 in scene_ids:
                text = self._ocr_text(self._ocr_lines(frame))
                if self._daily_lingta_text_is_world_like(text):
                    with self._lock:
                        self._status.update({"current_scene": 34, "updated_at": time.time()})
                        self._log_locked("success", f"{label}：已回到世界 #34（OCR 特征）")
                    return 34, float(score)
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
        status = yield from self._open_daily_entry_from_daily(
            ctx,
            stop_event,
            {
                **payload,
                "max_scrolls": payload.get("lingzu_max_scrolls") or payload.get("max_scrolls") or 10,
                "reverse_scrolls": payload.get("lingzu_reverse_scrolls") or payload.get("reverse_scrolls") or 10,
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
        start = time.monotonic()
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            text = self._ocr_text(self._ocr_lines(frame))
            last_text = text or last_text
            scene_id, score = self._identify_scene_number(ctx, frame, [184])
            if scene_id == 184 or self._daily_lingzu_text_is_detail(text):
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
        detail_text = self._ocr_text(self._ocr_lines(frame))
        if self._daily_lingzu_remaining_zero(detail_text):
            self._record_daily_lingzu_done(payload, message="详情页显示今日剩余次数 0/1")
            return "done"
        return "open"







    def _return_xianfu_learn_skill_to_world(self, runtime: FanxiuRuntime):
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：返回世界 #34", phase="xianfu_skill_return_world")
            self._log_locked("action", "仙府_领悟绝技：按仙府收尾链路返回 #34")
        yield from self._return_xianfu_pages_to_world(
            runtime,
            task_label="仙府_领悟绝技",
            current_candidates=(177, 176, 172, 171, 86, 34),
        )
        return "success"

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

    def _record_daily_signup_done(self, payload: dict[str, Any] | None, *, message: str) -> str:
        payload = dict(payload or {})
        next_time = self._next_daily_boss_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-signup")
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_signup",
            label="日常_报名",
        )
        self._log("success", f"日常_报名：{message}，下次 {next_time}")
        return next_time














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

    def _increment_observed_landing(self, shape: dict[str, Any], target_scene_id: int) -> bool:
        entries = self._parse_scene_jump_entries(shape.get("observedLanding"))
        target_label = str(int(target_scene_id))
        for entry in entries:
            if str(entry.get("label") or "") == target_label:
                entry["count"] = int(entry.get("count") or 0) + 1
                break
        else:
            entries.append({"label": target_label, "count": 1})
        serialized = self._serialize_scene_jump_entries(entries)
        if str(shape.get("observedLanding") or "") == serialized:
            return False
        shape["observedLanding"] = serialized
        return True

    def _record_observed_landing(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        target_scene_id: int,
        *,
        reason: str,
    ) -> None:
        if self._increment_observed_landing(shape, target_scene_id):
            self._write_asset_tree(asset_tree_path, tree)
            ctx["images"] = self._index_images(tree)
            self._log(
                "detail",
                f"场景跳转观察：记录「{shape.get('title') or '未命名'}」意外落点 #{target_scene_id}（{reason}）",
            )

    def _record_unexpected_landing(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        landing_scene_id: int | None,
        *,
        source_scene_id: int | None = None,
        reason: str,
    ) -> None:
        if landing_scene_id is None:
            return
        landing_scene_id = int(landing_scene_id)
        if source_scene_id is not None and landing_scene_id == int(source_scene_id):
            return
        if landing_scene_id in self._scene_jump_target_ids(tree, shape):
            return
        self._record_observed_landing(ctx, asset_tree_path, tree, shape, landing_scene_id, reason=reason)

    def _scene_jump_label_number(self, label: Any) -> int | None:
        return SceneNavigator([]).scene_jump_label_number(label)

    def _collect_folder_image_numbers(self, node: dict[str, Any]) -> list[int]:
        return SceneNavigator([]).collect_folder_image_numbers(node)

    def _resolve_scene_jump_label(self, tree: list[dict[str, Any]], label: Any) -> list[int]:
        return SceneNavigator(tree).resolve_scene_jump_label(label)

    def _scene_jump_target_ids(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> list[int]:
        return SceneNavigator(tree).scene_jump_target_ids(shape)

    def _resolve_scene_image_title_ids(self, tree: list[dict[str, Any]], title: str) -> list[int]:
        result = [int(scene_id) for scene_id in SceneNavigator(tree).resolve_scene_image_title_ids(title)]
        seen = set(result)
        expected = str(title or "").strip()
        if not expected:
            return result

        def visit(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("type") or "image") == "image" and str(item.get("title") or "").strip() == expected:
                    image_id = self._image_number(item)
                    if image_id is not None and int(image_id) not in seen:
                        result.append(int(image_id))
                        seen.add(int(image_id))
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)])

        visit([item for item in tree if isinstance(item, dict)])
        return result

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

    def _scene_match_cache_dir(self) -> Path:
        return codeyun_temp_root("fanxiu-scene-match")

    def _scene_match_cache_key(
        self,
        ctx: dict[str, Any],
        scene_ids: list[int],
        *,
        threshold: float | None,
    ) -> str:
        payload: dict[str, Any] = {
            "version": 2,
            "scene_ids": [int(scene_id) for scene_id in scene_ids],
            "threshold": round(float(threshold), 4) if threshold is not None else None,
        }
        if threshold is None:
            payload["scene_thresholds"] = {
                str(int(scene_id)): round(float(self._scene_match_threshold(int(scene_id))), 4)
                for scene_id in scene_ids
            }
        asset_tree_path = ctx.get("asset_tree_path")
        if isinstance(asset_tree_path, Path) and asset_tree_path.is_file():
            stat = asset_tree_path.stat()
            payload["asset_tree"] = {
                "path": str(asset_tree_path),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
            }
            image_dir = asset_tree_path.parent / "images"
        else:
            image_dir = None
        image_signatures: list[dict[str, Any]] = []
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for scene_id in scene_ids:
            image = images.get(int(scene_id))
            if not isinstance(image, dict):
                continue
            filename = str(image.get("filename") or "")
            record: dict[str, Any] = {"scene_id": int(scene_id), "filename": filename}
            if image_dir is not None and filename:
                path = image_dir / filename
                if path.is_file():
                    stat = path.stat()
                    record.update({"mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
            image_signatures.append(record)
        payload["images"] = image_signatures
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _scene_frame_data_url_from_reference(self, ctx: dict[str, Any], image: dict[str, Any]) -> str:
        filename = str(image.get("filename") or "")
        if not filename:
            raise RuntimeError(f"帧「{image.get('title') or self._image_number(image) or '?'}」缺少图片文件")
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("ctx 缺少 asset_tree_path，无法把参考 scene 作为 match(s,x) 的 x")
        path = asset_tree_path.parent / "images" / filename
        if not path.is_file():
            raise RuntimeError(f"参考帧图片不存在：{path}")
        return self._data_url(path.read_bytes())

    def match_scene_frame(
        self,
        ctx: dict[str, Any],
        s: int | str,
        x: int | str,
        *,
        threshold: float | None = None,
        frame_data_url: str | None = None,
    ) -> dict[str, Any]:
        """Return the directed relation ``match(s, x)``.

        ``s`` is the reference scene whose identity rules are evaluated.
        ``x`` is either a live/reference frame data URL or a scene id whose
        reference image should be used as the fact frame.
        """

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        try:
            reference_scene_id = int(str(s).lstrip("#"))
        except (TypeError, ValueError):
            raise RuntimeError(f"无法解析 match(s,x) 的 s：{s}") from None
        reference_image = images.get(reference_scene_id)
        if not isinstance(reference_image, dict):
            raise RuntimeError(f"找不到 match(s,x) 的 s 场景：#{reference_scene_id}")

        fact_scene_id: int | None = None
        if isinstance(frame_data_url, str) and frame_data_url:
            frame = frame_data_url
        elif isinstance(x, str) and x.startswith("data:image"):
            frame = x
        else:
            try:
                fact_scene_id = int(str(x).lstrip("#"))
            except (TypeError, ValueError):
                raise RuntimeError(f"无法解析 match(s,x) 的 x：{x}") from None
            fact_image = images.get(fact_scene_id)
            if not isinstance(fact_image, dict):
                raise RuntimeError(f"找不到 match(s,x) 的 x 场景：#{fact_scene_id}")
            frame = self._scene_frame_data_url_from_reference(ctx, fact_image)

        scene_threshold = float(threshold if threshold is not None else self._scene_match_threshold(reference_scene_id))
        score = float(self._scene_score(ctx, reference_image, frame) or 0.0)
        return {
            "s": reference_scene_id,
            "x": fact_scene_id if fact_scene_id is not None else "frame",
            "score": score,
            "threshold": scene_threshold,
            "matched": score >= scene_threshold,
        }

    def match_scene_matrix(
        self,
        ctx: dict[str, Any],
        scene_ids: list[int] | None = None,
        *,
        layer: int | None = 2,
        threshold: float | None = None,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Build a cached directed ``match(s, x)`` matrix for reference scenes."""

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        if scene_ids is None:
            scene_ids = [
                int(scene_id)
                for scene_id, image in images.items()
                if isinstance(image, dict) and (layer is None or int(View(image).layer) == int(layer))
            ]
        scene_ids = [int(scene_id) for scene_id in scene_ids if isinstance(images.get(int(scene_id)), dict)]
        scene_ids = list(dict.fromkeys(scene_ids))
        scene_threshold = float(threshold) if threshold is not None else None
        cache_key = self._scene_match_cache_key(ctx, scene_ids, threshold=scene_threshold)
        cache_path = self._scene_match_cache_dir() / f"{cache_key}.json"
        if use_cache and cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("cache_key") == cache_key:
                    cached["cache_hit"] = True
                    return cached
            except Exception:
                pass

        matches: list[dict[str, Any]] = []
        for reference_id in scene_ids:
            for fact_id in scene_ids:
                if int(reference_id) == int(fact_id):
                    continue
                result = self.match_scene_frame(ctx, reference_id, fact_id, threshold=scene_threshold)
                if bool(result.get("matched")):
                    matches.append(result)

        payload = {
            "cache_key": cache_key,
            "cache_path": str(cache_path),
            "cache_hit": False,
            "score_mode": "strict_scene_identity",
            "layer": layer,
            "threshold": scene_threshold if scene_threshold is not None else "per_scene",
            "scene_ids": scene_ids,
            "match_count": len(matches),
            "matches": matches,
            "updated_at": time.time(),
        }
        if use_cache:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _runtime_graph_scene_candidate_ids(self, ctx: dict[str, Any], *, max_layer: int) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            result: list[int] = []
            for node in build_recognition_tree_nodes(tree, images):
                if not node.is_root:
                    continue
                if int(node.layer) > int(max_layer):
                    continue
                if int(max_layer) <= 2 and is_explicit_local_identity_only_scene(node.image):
                    continue
                if node.scene_id not in result:
                    result.append(node.scene_id)
            return result
        runtime_ids = [
            int(scene_id)
            for scene_id in self._runtime_scene_candidate_ids(ctx)
            if isinstance(images.get(int(scene_id)), dict)
        ]
        if runtime_ids:
            return runtime_ids
        if max_layer >= 3:
            return [int(scene_id) for scene_id, image in images.items() if isinstance(image, dict)]
        return [
            int(scene_id)
            for scene_id, image in images.items()
            if isinstance(image, dict)
            and int(View(image).layer) <= int(max_layer)
            and not is_explicit_local_identity_only_scene(image)
        ]

    def _recognition_parent_match_edges_for_candidates(self, ctx: dict[str, Any], scene_ids: list[int]) -> list[dict[str, Any]]:
        tree = ctx.get("asset_tree")
        images = ctx.get("images") or {}
        if not isinstance(tree, list) or not isinstance(images, dict):
            return []
        candidate_set = {int(scene_id) for scene_id in scene_ids}
        edges: list[dict[str, Any]] = []
        for node in build_recognition_tree_nodes(tree, images):
            if node.scene_id not in candidate_set:
                continue
            for parent_id in node.parent_scene_ids:
                if int(parent_id) in candidate_set:
                    edges.append({"s": int(parent_id), "x": int(node.scene_id), "matched": True, "source": "recognition_parent"})
        return edges

    def _scene_match_edges_for_candidates(
        self,
        ctx: dict[str, Any],
        scene_ids: list[int],
        *,
        trace: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(int(scene_id) for scene_id in scene_ids))
        if len(ids) <= 1:
            return []
        edges: list[dict[str, Any]] = []
        for reference_id in ids:
            for fact_id in ids:
                if int(reference_id) == int(fact_id):
                    continue
                try:
                    result = self.match_scene_frame(ctx, reference_id, fact_id)
                except Exception as exc:
                    if trace is not None:
                        trace.append({
                            "event": "graph_edge_error",
                            "s": int(reference_id),
                            "x": int(fact_id),
                            "error": str(exc)[:200],
                        })
                    continue
                if bool(result.get("matched")):
                    edges.append(result)
        return edges

    def _identify_scene_number_by_graph(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float, str]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict) or not images:
            return None, 0.0, "unavailable"
        if preferred_scene_ids is None and not isinstance(ctx.get("asset_tree"), list):
            return None, 0.0, "unavailable"

        if preferred_scene_ids is not None:
            candidate_ids = [int(scene_id) for scene_id in preferred_scene_ids if int(scene_id) in images]
            tree = ctx.get("asset_tree")
            if isinstance(tree, list):
                candidate_ids = layer0_recognition_candidate_ids(tree, images, candidate_ids) or candidate_ids
            return self._identify_scene_number_in_graph_candidates(
                ctx,
                frame_data_url,
                candidate_ids,
                layer_label="layer0",
                trace=trace,
            )

        for layer in (1, 2, 3):
            candidate_ids = self._runtime_graph_scene_candidate_ids(ctx, max_layer=layer)
            scene_id, score, status = self._identify_scene_number_in_graph_candidates(
                ctx,
                frame_data_url,
                candidate_ids,
                layer_label=f"layer{layer}",
                trace=trace,
                allow_similarity_fallback=layer == 3,
            )
            if status in {"matched", "graph_specific", "similarity_tiebreak", "similarity_fallback", "unknown"}:
                return scene_id, score, status
            if status == "ambiguous" and layer == 3:
                return scene_id, score, status
        return None, 0.0, "unavailable"

    def _identify_scene_number_in_graph_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        *,
        layer_label: str,
        trace: list[dict[str, Any]] | None = None,
        allow_similarity_fallback: bool = True,
    ) -> tuple[int | None, float, str]:
        if not candidate_scene_ids:
            return None, 0.0, "no_candidates"
        if 86 in candidate_scene_ids:
            text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
            if self._leave_scene_confirm_text(text):
                if trace is not None:
                    trace.append({"event": "special_case", "scene_id": 86, "reason": "leave_scene_confirm_ocr", "model": "graph"})
                return 86, 100.0, "matched"

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        candidates: list[SceneGraphCandidate] = []
        for scene_id in list(dict.fromkeys(int(item) for item in candidate_scene_ids)):
            image = images.get(scene_id)
            if not isinstance(image, dict):
                continue
            score = float(self._scene_score(ctx, image, frame_data_url) or 0.0)
            candidates.append(SceneGraphCandidate(scene_id=scene_id, score=score, matched=score >= self._scene_match_threshold(scene_id)))

        matched_ids = [item.scene_id for item in candidates if item.matched]
        edges = self._recognition_parent_match_edges_for_candidates(ctx, matched_ids)
        if len(matched_ids) > 1:
            edges.extend(self._scene_match_edges_for_candidates(ctx, matched_ids, trace=trace))
        result = choose_scene_from_graph(candidates, edges)
        if not allow_similarity_fallback and result.status in {"similarity_fallback", "unknown"}:
            if trace is not None:
                trace.append({
                    "event": "graph_layer_miss",
                    "layer": layer_label,
                    "candidate_count": len(candidates),
                    "best_scene_id": result.best_similarity_scene_id,
                    "best_score": round(float(result.best_similarity_score), 3),
                })
            return None, float(result.score), "no_match"
        if result.status == "ambiguous":
            if trace is not None:
                trace.append({
                    "event": "graph_ambiguous",
                    "layer": layer_label,
                    "candidates": list(result.unresolved_candidates),
                    "best_scene_id": result.best_similarity_scene_id,
                    "best_score": round(float(result.best_similarity_score), 3),
            })
            return None, float(result.score), "ambiguous"
        if (
            result.scene_id is not None
            and isinstance(ctx.get("asset_tree"), list)
            and not self._scene_number_ocr_confirmed(ctx, frame_data_url, int(result.scene_id), float(result.score))
        ):
            if trace is not None:
                trace.append({
                    "event": "final_rejected",
                    "model": "graph",
                    "layer": layer_label,
                    "scene_id": int(result.scene_id),
                    "score": round(float(result.score), 3),
                    "reason": "ocr_confirm_failed",
                })
            return None, float(result.score), "ocr_confirm_failed"
        if trace is not None:
            trace.append({
                "event": "graph_result",
                "layer": layer_label,
                "status": result.status,
                "scene_id": result.scene_id,
                "score": round(float(result.score), 3),
                "matched_candidates": [
                    {"scene_id": item.scene_id, "score": round(float(item.score), 3)}
                    for item in result.matched_candidates[:12]
                ],
                "best_similarity_scene_id": result.best_similarity_scene_id,
                "best_similarity_score": round(float(result.best_similarity_score), 3),
            })
        return result.scene_id, float(result.score), result.status

    def _scene_key_order(self) -> list[str]:
        return [
            "duplicated",
            "reward",
            "wanling_invite",
            "gift",
            "youli_result",
            "youli_explore",
            "youli",
            "daily_activity",
            "signup_reward",
            "signup",
            "daily_xianyuan_leave_confirm",
            "daily_xianyuan_challenge_result",
            "daily_xianyuan_challenge_confirm",
            "daily_xianyuan_challenge_dialogue",
            "daily_xianyuan_dialogue",
            "daily_xianyuan_detail",
            "daily_xianyuan_list",
            "youli_quick_result",
            "youli_region_detail",
            "youli_purchase_empty",
            "youli_purchase",
            "daily_assistant_one_key_progress",
            "daily_assistant_one_key_confirm",
            "daily_assistant_one_key_result",
            "daily_assistant_tongyou_confirm",
            "daily_assistant_overview",
            "daily_shuangxiu_secret",
            "daily_shuangxiu_detail",
            "daily_shuangxiu_invite",
            "daily_shuangxiu_xianyuan_invite",
            "daily_shuangxiu_training_ready",
            "daily_shuangxiu_complete",
            "daily",
            "settings",
            "world_menu",
            "hide_floating",
            "world",
        ]

    def _scene_recognizer(self) -> SceneRecognizer:
        return SceneRecognizer(
            score_image=self._scene_score,
            threshold_for_scene_id=self._scene_match_threshold,
            image_for_key=self._image,
            threshold_for_key=lambda key: float(self.scene_thresholds.get(key, self.scene_threshold)),
            max_parallel_workers=1,
            max_candidate_batch_size=8,
        )

    def _identify_scene_number(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float]:
        graph_scene_id, graph_score, graph_status = self._identify_scene_number_by_graph(
            ctx,
            frame_data_url,
            preferred_scene_ids,
            trace=trace,
        )
        if graph_status in {"matched", "graph_specific", "similarity_tiebreak"}:
            return graph_scene_id, graph_score
        if trace is not None:
            trace.append({"event": "graph_fallback_legacy_tree", "status": graph_status})

        candidate_scene_ids = preferred_scene_ids
        if candidate_scene_ids is None:
            if 86 in self._runtime_scene_candidate_ids(ctx):
                text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
                if self._leave_scene_confirm_text(text):
                    if trace is not None:
                        trace.append({"event": "special_case", "scene_id": 86, "reason": "leave_scene_confirm_ocr"})
                    return 86, 100.0
            recognizer = self._scene_recognizer()
            root_candidate_ids = self._runtime_scene_candidate_ids(ctx)
            allowed_scene_ids = set(root_candidate_ids)
            tree = ctx.get("asset_tree")
            images = ctx.get("images")
            if isinstance(tree, list) and isinstance(images, dict) and root_candidate_ids:
                allowed_scene_ids.update(layer0_recognition_candidate_ids(tree, images, root_candidate_ids))
            if trace is None:
                tree_preferred_scene_ids = root_candidate_ids if len(root_candidate_ids) == 1 else None
                scene_id, score = recognizer.identify_scene_tree_number(ctx, frame_data_url, preferred_scene_ids=tree_preferred_scene_ids)
            else:
                tree_preferred_scene_ids = root_candidate_ids if len(root_candidate_ids) == 1 else None
                scene_id, score = recognizer.identify_scene_tree_number(ctx, frame_data_url, preferred_scene_ids=tree_preferred_scene_ids, trace=trace)
            if scene_id is not None and self._is_explicit_local_scene_result(ctx, int(scene_id), root_candidate_ids):
                if trace is not None:
                    trace.append({"event": "candidate_rejected", "scene_id": int(scene_id), "reason": "local_identity_without_layer0_context"})
                retry_scene_id, retry_score = self._identify_root_scene_number_from_candidates(ctx, frame_data_url, root_candidate_ids, trace=trace)
                if retry_scene_id is not None:
                    return retry_scene_id, retry_score
                return None, score
            if scene_id is not None and allowed_scene_ids and int(scene_id) not in allowed_scene_ids:
                if trace is not None:
                    trace.append({"event": "candidate_rejected", "scene_id": int(scene_id), "reason": "outside_runtime_root_candidates"})
                retry_scene_id, retry_score = self._identify_scene_number_from_candidates(ctx, frame_data_url, root_candidate_ids, trace=trace)
                if retry_scene_id is not None:
                    return retry_scene_id, retry_score
                return None, score
            if scene_id is not None and not self._scene_number_ocr_confirmed(ctx, frame_data_url, scene_id, score):
                if trace is not None:
                    trace.append({"event": "final_rejected", "scene_id": int(scene_id), "score": round(float(score), 3), "reason": "ocr_confirm_failed"})
                retry_ids = [int(item) for item in self._runtime_scene_candidate_ids(ctx) if int(item) != int(scene_id)]
                if retry_ids and len(retry_ids) < len(self._runtime_scene_candidate_ids(ctx)):
                    retry_scene_id, retry_score = self._identify_scene_number_from_candidates(ctx, frame_data_url, retry_ids, trace=trace)
                    if retry_scene_id is not None:
                        return retry_scene_id, retry_score
                return None, score
            return scene_id, score
        if preferred_scene_ids is not None:
            if 86 in preferred_scene_ids:
                text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
                if self._leave_scene_confirm_text(text):
                    if trace is not None:
                        trace.append({"event": "special_case", "scene_id": 86, "reason": "leave_scene_confirm_ocr"})
                    return 86, 100.0
            recognizer = self._scene_recognizer()
            if trace is None:
                scene_id, score = recognizer.identify_scene_tree_number(ctx, frame_data_url, preferred_scene_ids=preferred_scene_ids)
            else:
                scene_id, score = recognizer.identify_scene_tree_number(ctx, frame_data_url, preferred_scene_ids=preferred_scene_ids, trace=trace)
            if scene_id is None:
                if trace is None:
                    scene_id, score = recognizer.identify_scene_number(ctx, frame_data_url, preferred_scene_ids=preferred_scene_ids)
                else:
                    scene_id, score = recognizer.identify_scene_number(ctx, frame_data_url, preferred_scene_ids=preferred_scene_ids, trace=trace)
            if scene_id is not None and self._is_explicit_local_scene_result(ctx, int(scene_id), preferred_scene_ids):
                if trace is not None:
                    trace.append({"event": "candidate_rejected", "scene_id": int(scene_id), "reason": "local_identity_without_explicit_candidate"})
                retry_scene_id, retry_score = self._identify_root_scene_number_from_candidates(ctx, frame_data_url, preferred_scene_ids, trace=trace)
                if retry_scene_id is not None:
                    return retry_scene_id, retry_score
                return None, score
            if scene_id is not None and not self._scene_number_ocr_confirmed(ctx, frame_data_url, scene_id, score):
                if trace is not None:
                    trace.append({"event": "final_rejected", "scene_id": int(scene_id), "score": round(float(score), 3), "reason": "ocr_confirm_failed"})
                retry_ids = [int(item) for item in preferred_scene_ids if int(item) != int(scene_id)]
                if retry_ids and len(retry_ids) < len(preferred_scene_ids):
                    retry_scene_id, retry_score = self._identify_scene_number_from_candidates(ctx, frame_data_url, retry_ids, trace=trace)
                    if retry_scene_id is not None:
                        return retry_scene_id, retry_score
                return None, score
            return scene_id, score

        return self._identify_scene_number_from_candidates(
            ctx,
            frame_data_url,
            candidate_scene_ids,
            trace=trace,
        )

    def _is_explicit_local_scene_result(self, ctx: dict[str, Any], scene_id: int, root_candidate_ids: list[int]) -> bool:
        if int(scene_id) in {int(item) for item in root_candidate_ids}:
            return False
        images = ctx.get("images") or {}
        image = images.get(int(scene_id)) if isinstance(images, dict) else None
        return isinstance(image, dict) and is_explicit_local_identity_only_scene(image)

    def _identify_root_scene_number_from_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float]:
        recognizer = self._scene_recognizer()
        if trace is None:
            scene_id, score = recognizer.identify_scene_number(ctx, frame_data_url, preferred_scene_ids=candidate_scene_ids or None)
        else:
            scene_id, score = recognizer.identify_scene_number(ctx, frame_data_url, preferred_scene_ids=candidate_scene_ids or None, trace=trace)
        if scene_id is not None and not self._scene_number_ocr_confirmed(ctx, frame_data_url, scene_id, score):
            if trace is not None:
                trace.append({"event": "final_rejected", "scene_id": int(scene_id), "score": round(float(score), 3), "reason": "ocr_confirm_failed"})
            return None, score
        return scene_id, score

    def _identify_scene_number_from_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float]:
        if 86 in candidate_scene_ids:
            text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
            if self._leave_scene_confirm_text(text):
                if trace is not None:
                    trace.append({"event": "special_case", "scene_id": 86, "reason": "leave_scene_confirm_ocr"})
                return 86, 100.0
        recognizer = self._scene_recognizer()
        if trace is None:
            scene_id, score = recognizer.identify_scene_tree_number(ctx, frame_data_url, preferred_scene_ids=candidate_scene_ids or None)
        else:
            scene_id, score = recognizer.identify_scene_tree_number(ctx, frame_data_url, preferred_scene_ids=candidate_scene_ids or None, trace=trace)
        if scene_id is not None and not self._scene_number_ocr_confirmed(ctx, frame_data_url, scene_id, score):
            if trace is not None:
                trace.append({"event": "final_rejected", "scene_id": int(scene_id), "score": round(float(score), 3), "reason": "ocr_confirm_failed"})
            retry_ids = [int(item) for item in candidate_scene_ids if int(item) != int(scene_id)]
            if retry_ids and len(retry_ids) < len(candidate_scene_ids):
                retry_scene_id, retry_score = self._identify_scene_number_from_candidates(ctx, frame_data_url, retry_ids, trace=trace)
                if retry_scene_id is not None:
                    return retry_scene_id, retry_score
            return None, score
        return scene_id, score

    def _scene_number_ocr_confirmed(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        scene_id: int,
        score: float,
    ) -> bool:
        if int(scene_id) == 71:
            text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
            if self._game_cover_scene_ocr_text(text):
                self._log("detail", f"场景识别降级：#71 图像/OCR 命中 {float(score or 0):.0f}% 但 OCR 是 #18 登录封面，OCR={text[:120]}")
                return False
            if not self._youli_scene_ocr_confirmed_text(text):
                self._log("detail", f"场景识别降级：#71 图像/OCR 命中 {float(score or 0):.0f}% 但 OCR 不像修仙传游历，OCR={text[:120]}")
                return False
            return True
        if int(scene_id) == 180:
            text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
            if self._daily_boss_fighting_scene_ocr_confirmed_text(text):
                return True
            self._log("detail", f"场景识别降级：#180 图像/OCR 命中 {float(score or 0):.0f}% 但 OCR 不像首领战斗，OCR={text[:120]}")
            return False
        if int(scene_id) != 34:
            return True
        if not isinstance(ctx.get("asset_tree"), list):
            return True
        text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
        if self._world_scene_ocr_confirmed_text(text):
            return True
        self._log("detail", f"场景识别降级：#34 图像命中 {float(score or 0):.0f}% 但 OCR 不像世界，OCR={text[:120]}")
        return False

    def _daily_boss_fighting_scene_ocr_confirmed_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if not compact or "首领" not in compact:
            return False
        reward_noise = ("活动时间", "大奖预览", "奖励预览", "成功击败", "获得了", "使用后可以获得")
        if any(marker in compact for marker in reward_noise):
            return False
        return any(marker in compact for marker in ("自动战斗中", "数据统计", "伤害", "离开"))

    def _world_scene_ocr_confirmed_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        internal_markers = (
            "灵脉",
            "聚灵位",
            "剩余空位",
            "神脉",
            "探索",
            "论道",
            "道场",
            "闻道",
            "剩余座位",
            "请他让座",
            "道心",
        )
        if any(marker in compact for marker in internal_markers):
            return False
        world_markers = ("大地图", "储物袋", "仙市", "仙府", "天机阁", "角色", "装备", "星海", "功法书")
        return sum(1 for marker in world_markers if marker in compact) >= 2

    def _game_cover_scene_ocr_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return (
            "进入游戏" in compact
            and ("AppVer" in compact or "ResVer" in compact or "适龄提示" in compact)
            and ("账号" in compact or "公告" in compact or "协议" in compact)
        )

    def _youli_scene_ocr_confirmed_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if not compact:
            return False
        blockers = ("进入游戏", "适龄提示", "游戏公告", "限时上线", "锁定官方", "照妖镜")
        if any(marker in compact for marker in blockers):
            return False
        return any(marker in compact for marker in ("游历", "游历值", "探索完成", "当前区域", "区域列表"))

    def _strong_ocr_scene_number(self, ctx: dict[str, Any], frame_data_url: str) -> int | None:
        text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if self._game_cover_scene_ocr_text(text):
            self._log("detail", "场景强 OCR 命中 #18 游戏封面，跳过局部标题候选")
            return 18
        if "日程" in compact and "凡人历" in compact and re.search(r"\d{2}月\d{2}日", compact):
            self._log("detail", "场景强 OCR 命中 #66 日程，跳过活动素材候选")
            return 66
        internal_markers = ("灵脉", "聚灵位", "剩余空位", "神脉", "探索")
        world_markers = ("储物袋", "仙市", "仙府", "天机阁", "角色", "装备", "星海", "功法书")
        if (
            "大地图" in compact
            and not any(marker in compact for marker in internal_markers)
            and sum(1 for marker in world_markers if marker in compact) >= 1
        ):
            self._log("detail", "场景强 OCR 命中 #34 世界，跳过局部路径候选")
            return 34
        if "可旋转" in compact and "法则之主" in compact and "拜谒" in compact:
            self._log("detail", "场景强 OCR 命中 #265 法则之主旋转选择页")
            return 265
        if "封印" in compact and "离开" in compact:
            self._log("detail", "场景强 OCR 命中 #181 首领封印完成")
            return 181
        if "首领规则" in compact and ("神识注视" in compact or "前往挑战" in compact or "剩余奖励次数" in compact):
            self._log("detail", "场景强 OCR 命中 #179 首领详情")
            return 179
        if "首领" in compact and ("自动战斗中" in compact or "数据统计" in compact or "伤害" in compact):
            self._log("detail", "场景强 OCR 命中 #180 首领挑战中")
            return 180
        if "首领境界" in compact and ("剩余奖励次数" in compact or "掉落记录" in compact) and "首领规则" not in compact:
            self._log("detail", "场景强 OCR 命中 #178 首领列表")
            return 178
        return None

    def _leave_scene_confirm_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        if ("是否离开" in compact or "离开当前场景" in compact) and ("确认" in compact or "确定" in compact):
            return True
        return bool("是否中止修炼" in compact and "留下" in compact and "离开" in compact)

    def _runtime_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        return self._runtime_scene_candidate_ids_by_kind(ctx, include_popups=None)

    def _runtime_popup_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        return self._runtime_scene_candidate_ids_by_kind(ctx, include_popups=True)

    def _runtime_scene_candidate_ids_by_kind(self, ctx: dict[str, Any], *, include_popups: bool | None) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree_candidates = self._runtime_tree_scene_candidate_ids(ctx, include_popups=include_popups)
        if isinstance(ctx.get("asset_tree"), list):
            return tree_candidates
        if include_popups is True:
            return []
        return [
            int(scene_id)
            for scene_id in self.scene_ids.values()
            if int(scene_id) in images
            and isinstance(images.get(int(scene_id)), dict)
            and not is_explicit_local_identity_only_scene(images[int(scene_id)])
        ]

    def _runtime_tree_scene_candidate_ids(self, ctx: dict[str, Any], *, include_popups: bool | None) -> list[int]:
        tree = ctx.get("asset_tree")
        images = ctx.get("images") or {}
        if not isinstance(tree, list) or not isinstance(images, dict):
            return []
        candidates = runtime_root_scene_candidate_ids(tree, images, include_popups=include_popups)
        if include_popups is None:
            for node in build_recognition_tree_nodes(tree, images):
                if node.scene_id not in candidates and node.is_root and node.layer in {2, 3}:
                    candidates.append(node.scene_id)
        return candidates

    def _scene_jump_edges(self, tree: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        edges = SceneNavigator(tree).scene_jump_edges()
        seen_keys = {self._scene_jump_edge_key(edge) for edge_list in edges.values() for edge in edge_list}

        def visit(items: list[dict[str, Any]], folder_titles: tuple[str, ...] = ()) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                next_folder_titles = folder_titles
                if str(item.get("type") or "") == "folder":
                    title = str(item.get("title") or "").strip()
                    if title:
                        next_folder_titles = (*folder_titles, title)
                if str(item.get("type") or "image") == "image":
                    source_id = self._image_number(item)
                    if source_id is not None:
                        edge_list = edges.setdefault(int(source_id), [])
                        for shape in self._flatten_shapes(item.get("shapes")):
                            if not isinstance(shape, dict):
                                continue
                            target_ids: list[int] = []
                            if str(shape.get("sceneJumpTarget") or "").strip():
                                target_ids = self._scene_jump_target_ids(tree, shape)
                                if not target_ids:
                                    target_id = self._scene_jump_label_number(shape.get("sceneJumpTarget"))
                                    target_ids = [target_id] if target_id is not None else []
                            if not target_ids:
                                title = str(shape.get("title") or shape.get("id") or "").strip()
                                if title in {"离开", "返回", "关闭", "退出", "关闭下方菜单", "回到世界"}:
                                    for folder_title in reversed(folder_titles):
                                        resolved = [
                                            int(scene_id)
                                            for scene_id in self._resolve_scene_image_title_ids(tree, folder_title)
                                            if int(scene_id) != int(source_id)
                                        ]
                                        if resolved:
                                            target_ids = resolved
                                            break
                            if not target_ids:
                                continue
                            edge = {
                                "source_id": int(source_id),
                                "image": item,
                                "shape": shape,
                                "target_ids": target_ids,
                            }
                            key = self._scene_jump_edge_key(edge)
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)
                            edge_list.append(edge)
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)], next_folder_titles)

        visit([item for item in tree if isinstance(item, dict)])
        self._add_runtime_confirm_scene_edges(edges)
        return edges

    def _find_scene_route(self, tree: list[dict[str, Any]], start_scene_id: int, target_scene_id: int) -> list[dict[str, Any]] | None:
        if start_scene_id == target_scene_id:
            return []
        edges = self._scene_jump_edges(tree)
        queue: list[tuple[int, list[dict[str, Any]]]] = [(start_scene_id, [])]
        visited = {start_scene_id}
        while queue:
            scene_id, route = queue.pop(0)
            for edge in edges.get(scene_id, []):
                for next_scene_id in edge.get("target_ids") or []:
                    if next_scene_id in visited:
                        continue
                    next_route = [*route, edge]
                    if next_scene_id == target_scene_id:
                        return next_route
                    visited.add(next_scene_id)
                    queue.append((next_scene_id, next_route))
        return None

    def _scene_jump_edge_key(self, edge: dict[str, Any]) -> tuple[Any, ...]:
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        return (
            int(edge.get("source_id") or 0),
            str(shape.get("id") or ""),
            str(shape.get("title") or ""),
            str(shape.get("sceneJumpTarget") or ""),
            tuple(int(scene_id) for scene_id in edge.get("target_ids") or []),
            bool(edge.get("_runtime_confirm_edge")),
        )

    def _scene_jump_target_counts(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> dict[int, int]:
        navigator = SceneNavigator(tree)
        counts: dict[int, int] = {}
        for entry in navigator.parse_scene_jump_entries(shape.get("sceneJumpTarget")):
            count = int(entry.get("count") or 0)
            for scene_id in navigator.resolve_scene_jump_label(entry.get("label")):
                counts[int(scene_id)] = max(counts.get(int(scene_id), 0), count)
        return counts

    def _scene_navigation_shape_risk(self, shape: dict[str, Any]) -> int:
        title = _sanitize_ocr_text(shape.get("title"))
        if not title:
            return 0
        high_risk_keywords = (
            "一键领取",
            "领取",
            "购买",
            "挑战",
            "拜谒",
            "兑换",
            "升级",
            "升阶",
            "执行",
            "删除",
            "使用",
        )
        return 100 if any(keyword in title for keyword in high_risk_keywords) else 0

    def _scene_navigation_shape_exit_score(self, shape: dict[str, Any]) -> int:
        title = _sanitize_ocr_text(shape.get("title"))
        if title in {"离开", "返回", "关闭", "退出", "关闭下方菜单", "回到世界"}:
            return 1
        return 0

    def _scene_route_navigation_risk(self, route: list[dict[str, Any]]) -> int:
        risk = 0
        for edge in route:
            shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
            risk += self._scene_navigation_shape_risk(shape)
        return risk

    def _rank_scene_next_edge(
        self,
        tree: list[dict[str, Any]],
        edge: dict[str, Any],
        target_scene_id: int,
        *,
        order: int,
    ) -> dict[str, Any] | None:
        source_id = int(edge.get("source_id") or 0)
        target_ids = []
        for scene_id in edge.get("target_ids") or []:
            scene_id = int(scene_id)
            if scene_id not in target_ids:
                target_ids.append(scene_id)
        if not target_ids:
            return None

        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        current_edge_risk = self._scene_navigation_shape_risk(shape)
        image = edge.get("image") if isinstance(edge.get("image"), dict) else {}
        image_title = str(image.get("title") or "") if isinstance(image, dict) else ""
        shape_title = str(shape.get("title") or "")
        if current_edge_risk >= 100 and "奖励" in image_title and shape_title in {"领取", "确定", "确认"}:
            self._log("detail", f"场景移动：奖励/提示页打断 #{source_id}，允许点击「{shape_title}」回到已声明落点")
            current_edge_risk = 0
        if current_edge_risk >= 100:
            return None
        target_counts = self._scene_jump_target_counts(tree, shape)
        reachable_landings: list[tuple[int, int]] = []
        for landing_id in target_ids:
            if landing_id == int(target_scene_id):
                reachable_landings.append((landing_id, 0))
                continue
            if landing_id == source_id:
                continue
            route = self._find_scene_route(tree, landing_id, int(target_scene_id))
            if route is not None:
                route_risk = self._scene_route_navigation_risk(route)
                if route_risk >= 100:
                    continue
                first_route_edge = route[0] if route else None
                if isinstance(first_route_edge, dict) and int(first_route_edge.get("source_id") or 0) == landing_id:
                    next_ids = {int(item) for item in first_route_edge.get("target_ids") or []}
                    if source_id in next_ids:
                        continue
                reachable_landings.append((landing_id, len(route)))
        if not reachable_landings:
            return None

        direct = any(landing_id == int(target_scene_id) for landing_id, _ in reachable_landings)
        best_landing_id, downstream_len = min(reachable_landings, key=lambda item: item[1])
        best_count = max((target_counts.get(landing_id, 0) for landing_id, _ in reachable_landings), default=0)
        reachable_landing_ids = {int(landing_id) for landing_id, _ in reachable_landings}
        wrong_target_count = max(
            (
                count
                for landing_id, count in target_counts.items()
                if landing_id != int(target_scene_id) and int(landing_id) not in reachable_landing_ids
            ),
            default=0,
        )
        ambiguity = len(target_ids)
        dynamic_penalty = 1 if edge.get("_runtime_confirm_edge") else 0
        total_path_len = 1 + int(downstream_len)
        direct_target_count = target_counts.get(int(target_scene_id), 0)
        route = [] if direct else (self._find_scene_route(tree, best_landing_id, int(target_scene_id)) or [])
        navigation_risk = current_edge_risk + self._scene_route_navigation_risk(route)
        exit_score = self._scene_navigation_shape_exit_score(shape) if not direct else 0
        score = (
            1 if direct else 0,
            int(direct_target_count),
            int(exit_score),
            int(best_count),
            -int(wrong_target_count),
            -int(total_path_len),
            -int(navigation_risk),
            -int(ambiguity),
            -int(dynamic_penalty),
            -int(order),
        )
        reason = "直达目标" if direct else f"落到 #{best_landing_id} 后还需 {downstream_len} 步"
        if best_count:
            reason += f"，历史命中 {best_count} 次"
        if exit_score:
            reason += "，低风险退出"
        if ambiguity > 1:
            reason += f"，声明落点 {ambiguity} 个"
        if dynamic_penalty:
            reason += "，动态确认边"
        return {
            "edge": edge,
            "score": score,
            "reason": reason,
            "landing_id": best_landing_id,
            "downstream_len": downstream_len,
        }

    def _select_scene_next_edge(
        self,
        tree: list[dict[str, Any]],
        current_scene_id: int,
        target_scene_id: int,
        *,
        failed_edge_keys: set[tuple[Any, ...]] | None = None,
    ) -> dict[str, Any] | None:
        failed_edge_keys = failed_edge_keys or set()
        candidates: list[dict[str, Any]] = []
        for order, edge in enumerate(self._scene_jump_edges(tree).get(int(current_scene_id), [])):
            if self._scene_jump_edge_key(edge) in failed_edge_keys:
                continue
            ranked = self._rank_scene_next_edge(tree, edge, int(target_scene_id), order=order)
            if ranked is not None:
                candidates.append(ranked)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _add_runtime_confirm_scene_edges(self, edges: dict[int, list[dict[str, Any]]]) -> None:
        for source_id, source_edges in list(edges.items()):
            image = next(
                (edge.get("image") for edge in source_edges if isinstance(edge.get("image"), dict)),
                None,
            )
            if not isinstance(image, dict):
                continue
            confirm_shape = next(
                (
                    shape
                    for shape in self._flatten_shapes(image.get("shapes"))
                    if str(shape.get("title") or "").strip() in {"确定", "确认"}
                    and not str(shape.get("sceneJumpTarget") or "").strip()
                ),
                None,
            )
            if not isinstance(confirm_shape, dict):
                continue
            target_ids: list[int] = []
            for edge in source_edges:
                for target_id in edge.get("target_ids") or []:
                    target_id = int(target_id)
                    if target_id == int(source_id) or target_id in target_ids:
                        continue
                    target_ids.append(target_id)
            if not target_ids:
                continue
            edges[source_id] = [
                {
                    "source_id": source_id,
                    "image": image,
                    "shape": confirm_shape,
                    "target_ids": target_ids,
                    "_runtime_confirm_edge": True,
                },
                *source_edges,
            ]

    def _scene_route_ranking(
        self,
        tree: list[dict[str, Any]],
        scene_id: int,
        target_scene_id: int,
    ) -> tuple[int, int]:
        if int(scene_id) == int(target_scene_id):
            return 10, 0
        route = self._find_scene_route(tree, scene_id, target_scene_id)
        if route is not None:
            ambiguous_edges = sum(1 for edge in route if len(edge.get("target_ids") or []) != 1)
            return -ambiguous_edges, len(route)
        simple_edges: dict[int, list[list[int]]] = {}
        for item in tree:
            if not isinstance(item, dict):
                continue
            item_id = self._image_number(item)
            if item_id is None:
                continue
            for shape in self._flatten_shapes(item.get("shapes")):
                target_ids = self._scene_jump_target_ids(tree, shape)
                if target_ids:
                    simple_edges.setdefault(int(item_id), []).append([int(target_id) for target_id in target_ids])
        queue: list[tuple[int, int, int]] = [(int(scene_id), 0, 0)]
        seen = {int(scene_id)}
        while queue:
            node_id, distance, ambiguity = queue.pop(0)
            for target_ids in simple_edges.get(node_id, []):
                next_ambiguity = ambiguity + (0 if len(target_ids) == 1 else 1)
                for next_id in target_ids:
                    if next_id == int(target_scene_id):
                        return -next_ambiguity, distance + 1
                    if next_id in seen:
                        continue
                    seen.add(next_id)
                    queue.append((next_id, distance + 1, next_ambiguity))
        image = next((item for item in tree if isinstance(item, dict) and self._image_number(item) == int(scene_id)), None)
        if isinstance(image, dict):
            for shape in self._flatten_shapes(image.get("shapes")):
                target_ids = self._scene_jump_target_ids(tree, shape)
                if int(target_scene_id) in target_ids:
                    return (0 if len(target_ids) == 1 else -1), 1
        if int(scene_id) in self._scene_jump_confirmation_scene_ids(tree):
            return 1, 1
        return -100, 9999

    def _identify_scene_number_for_route(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        tree: list[dict[str, Any]],
        target_scene_id: int,
        candidate_scene_ids: list[int],
    ) -> tuple[int | None, float]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict) or not candidate_scene_ids:
            return None, 0.0

        ranked: list[tuple[int, float, int, int]] = []
        for candidate_scene_id in candidate_scene_ids:
            try:
                scene_id = int(candidate_scene_id)
            except (TypeError, ValueError):
                continue
            image = images.get(scene_id)
            if not isinstance(image, dict):
                continue
            score = float(self._scene_score(ctx, image, frame_data_url) or 0.0)
            if score < 60.0:
                continue
            clarity, route_len = self._scene_route_ranking(tree, scene_id, int(target_scene_id))
            if clarity <= -100:
                continue
            ranked.append((clarity, score, -route_len, scene_id))
        if not ranked:
            scene_id, score = self._identify_scene_number(ctx, frame_data_url, candidate_scene_ids)
            return scene_id, score
        ranked.sort(reverse=True)
        clarity, score, neg_route_len, scene_id = ranked[0]
        route_len = -neg_route_len
        self._log(
            "detail",
            f"goto_view：路径候选命中 #{scene_id} {score:.0f}%，明确性 {clarity}，路径长度 {route_len}",
        )
        return scene_id, score

    def _scene_jump_confirmation_scene_ids(self, tree: list[dict[str, Any]]) -> list[int]:
        source_shape = {"title": "离开"}
        candidates = SceneNavigator(tree).confirmation_scene_ids(
            lambda image: self._scene_jump_intermediate_confirm_shape(image, source_shape) is not None
        )
        seen = {int(scene_id) for scene_id in candidates}
        for image_id in self._resolve_scene_image_title_ids(tree, "离开场景"):
            if int(image_id) not in seen:
                candidates.append(int(image_id))
                seen.add(int(image_id))
        return candidates

    def _scene_route_candidate_ids(self, tree: list[dict[str, Any]], target_scene_id: int) -> list[int]:
        candidates = SceneNavigator(tree).route_candidate_ids(
            target_scene_id,
            confirmation_scene_ids=self._scene_jump_confirmation_scene_ids(tree),
        )
        candidate_set = {int(scene_id) for scene_id in candidates}
        image_ids: list[int] = []

        def collect_image_ids(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                image_id = self._image_number(item)
                if image_id is not None and int(image_id) not in image_ids:
                    image_ids.append(int(image_id))
                children = item.get("children")
                if isinstance(children, list):
                    collect_image_ids([child for child in children if isinstance(child, dict)])

        collect_image_ids([item for item in tree if isinstance(item, dict)])

        reachable_sources: list[tuple[int, int]] = []
        for image_id in image_ids:
            if int(image_id) == int(target_scene_id) or int(image_id) in candidate_set:
                continue
            route = self._find_scene_route(tree, int(image_id), int(target_scene_id))
            if route is None:
                continue
            route_risk = self._scene_route_navigation_risk(route)
            if route_risk >= 100:
                continue
            reachable_sources.append((len(route), int(image_id)))
        for _route_len, image_id in sorted(reachable_sources, key=lambda item: (item[0], image_ids.index(item[1]))):
            if image_id not in candidate_set:
                candidates.append(image_id)
                candidate_set.add(image_id)

        def visit(items: list[dict[str, Any]], parent_scene_id: int | None = None) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                current_parent_scene_id = parent_scene_id
                if item.get("type") == "image":
                    image_id = self._image_number(item)
                    layer = self._image_layer(item)
                    if image_id is not None and layer in {1, 2}:
                        current_parent_scene_id = int(image_id)
                    elif (
                        image_id is not None
                        and layer == 3
                        and parent_scene_id is not None
                        and int(parent_scene_id) in candidate_set
                        and int(image_id) not in candidate_set
                    ):
                        candidates.append(int(image_id))
                        candidate_set.add(int(image_id))
                children = item.get("children")
                if isinstance(children, list):
                    visit([child for child in children if isinstance(child, dict)], current_parent_scene_id)

        visit([item for item in tree if isinstance(item, dict)])
        return candidates

    def _write_asset_tree(self, asset_tree_path: Path, tree: list[dict[str, Any]]) -> None:
        tree = self._merge_asset_tree_existing_shapes(asset_tree_path, tree)
        _write_data_annotation_json(asset_tree_path, tree)
        self._auto_close_candidates_cache.pop(str(asset_tree_path), None)

    def _merge_asset_tree_existing_shapes(self, asset_tree_path: Path, tree: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            current = json.loads(asset_tree_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return tree
        if not isinstance(current, list):
            return tree

        def image_key(node: dict[str, Any]) -> str:
            filename = str(node.get("filename") or "").strip().lower()
            match = re.search(r"0*(\d+)(?=\.[^.]+$)", filename)
            if match:
                return f"image:{int(match.group(1))}"
            return f"image:{filename or str(node.get('id') or '')}"

        def shape_key(shape: dict[str, Any]) -> str:
            shape_id = str(shape.get("id") or "").strip()
            if shape_id:
                return f"id:{shape_id}"
            return "sig:" + ":".join(
                [
                    str(shape.get("title") or "").strip(),
                    str(round(float(shape.get("x") or 0) * 10000)),
                    str(round(float(shape.get("y") or 0) * 10000)),
                    str(round(float(shape.get("w") or 0) * 10000)),
                    str(round(float(shape.get("h") or 0) * 10000)),
                ]
            )

        def image_map(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
            result: dict[str, dict[str, Any]] = {}

            def visit(items: Any) -> None:
                if not isinstance(items, list):
                    return
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "image":
                        result.setdefault(image_key(item), item)
                    visit(item.get("children"))

            visit(nodes)
            return result

        def merge_shapes(target_shapes: list[dict[str, Any]], current_shapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            merged = [dict(shape) for shape in target_shapes if isinstance(shape, dict)]
            by_key = {shape_key(shape): shape for shape in merged}
            for shape in current_shapes:
                if not isinstance(shape, dict):
                    continue
                key = shape_key(shape)
                target = by_key.get(key)
                if target is None:
                    merged.append(json.loads(json.dumps(shape, ensure_ascii=False)))
                    continue
                target_children = target.get("children") if isinstance(target.get("children"), list) else []
                current_children = shape.get("children") if isinstance(shape.get("children"), list) else []
                if current_children:
                    target["children"] = merge_shapes(target_children, current_children)
            return merged

        current_images = image_map(current)

        def visit_target(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image":
                    current_image = current_images.get(image_key(item))
                    if isinstance(current_image, dict):
                        item["shapes"] = merge_shapes(
                            item.get("shapes") if isinstance(item.get("shapes"), list) else [],
                            current_image.get("shapes") if isinstance(current_image.get("shapes"), list) else [],
                        )
                visit_target(item.get("children"))

        visit_target(tree)
        return tree

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
        diagnostic = ""
        try:
            evidence = build_unknown_evidence(
                self,
                ctx,
                frame_data_url,
                label=f"go_scene_{target_scene_id}",
                expected_scene_ids=[target_scene_id],
                last_scene_id=current_scene_id,
                last_score=0.0,
            )
            report_suffix = f"，证据={evidence.report_path}" if evidence.report_path else ""
            frame_suffix = f"，截图={evidence.frame_path}" if evidence.frame_path else ""
            diagnostic = f"；unknown诊断={evidence.classification}：{evidence.suggestion}{frame_suffix}{report_suffix}"
        except Exception as exc:
            diagnostic = f"；unknown诊断生成失败：{exc}"
        detail = "；".join([
            f"目标场景=#{target_scene_id}",
            f"当前/点击前场景={current_text}",
            f"动作 shape={action_title}",
            f"累计等待={elapsed_seconds:.1f}s",
            f"最近识别={history[-1] if history else '无'}",
        ])
        self._log("error", f"场景跳转缺少可靠标注，已中断：{detail}{diagnostic}")
        raise RuntimeError(f"场景跳转缺少可靠标注，已中断，请人工补标/修标后重试：{detail}{diagnostic}")

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

    def _image_contains_shape(self, image: dict[str, Any], target_shape: dict[str, Any]) -> bool:
        target_id = str(target_shape.get("id") or "").strip()
        for shape in View(image).get_shapes():
            if shape.raw is target_shape:
                return True
            if target_id and str(shape.raw.get("id") or "").strip() == target_id:
                return True
        return False

    def _image_ancestor_chain(self, ctx: dict[str, Any], image: dict[str, Any]) -> list[dict[str, Any]]:
        image_id = self._image_number(image)
        tree = ctx.get("asset_tree")
        if image_id is None or not isinstance(tree, list):
            return []
        path: list[dict[str, Any]] = []

        def visit(items: list[dict[str, Any]], ancestors: list[dict[str, Any]]) -> bool:
            for item in items:
                if not isinstance(item, dict):
                    continue
                next_ancestors = ancestors
                if item.get("type") == "image":
                    if self._image_number(item) == image_id:
                        path.extend(ancestors)
                        return True
                    next_ancestors = [*ancestors, item]
                children = item.get("children")
                if isinstance(children, list) and visit([child for child in children if isinstance(child, dict)], next_ancestors):
                    return True
            return False

        visit([node for node in tree if isinstance(node, dict)], [])
        return list(reversed(path))

    def _effective_shape_source_image(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
    ) -> dict[str, Any]:
        for candidate in [image, *self._image_ancestor_chain(ctx, image)]:
            if isinstance(candidate, dict) and self._image_contains_shape(candidate, shape):
                return candidate
        return image

    def _scene_identity_scope(self, shape: dict[str, Any]) -> str:
        value = str(shape.get("sceneIdentityScope") or shape.get("scene_identity_scope") or "").strip().lower()
        aliases = {
            "none": "none",
            "off": "none",
            "false": "none",
            "无": "none",
            "local": "local",
            "局": "local",
            "global": "global",
            "全": "global",
        }
        normalized = aliases.get(value)
        if normalized:
            return normalized
        return "local" if bool(shape.get("isSceneIdentity")) or str(shape.get("sceneIdentityRole") or "").strip() not in {"", "off", "无"} else "none"

    def _image_layer(self, image: dict[str, Any]) -> int:
        return int(View(image).layer)

    def _navigation_scene_id(
        self,
        ctx: dict[str, Any],
        scene_id: int | None,
        frame_data_url: str | None = None,
    ) -> int | None:
        if scene_id is None:
            return None
        images = ctx.get("images") or {}
        image = images.get(int(scene_id)) if isinstance(images, dict) else None
        if not isinstance(image, dict):
            return int(scene_id)
        if self._image_layer(image) != 3 or self._scene_identity_shapes(image):
            return int(scene_id)
        for ancestor in self._image_ancestor_chain(ctx, image):
            ancestor_id = self._image_number(ancestor)
            if ancestor_id is not None and self._image_layer(ancestor) in {1, 2}:
                if frame_data_url:
                    ancestor_score = float(self._scene_score(ctx, ancestor, frame_data_url) or 0.0)
                    if not self._scene_matches_id(int(ancestor_id), ancestor_score):
                        self._log(
                            "detail",
                            f"场景移动：#{scene_id} 是无场景标识 layer3，但父场景 #{ancestor_id} 身份未成立 "
                            f"{ancestor_score:.0f}%，不作为导航起点",
                        )
                        return None
                self._log("detail", f"场景移动：#{scene_id} 是无场景标识 layer3，导航起点归到父场景 #{ancestor_id}")
                return int(ancestor_id)
        self._log("detail", f"场景移动：#{scene_id} 是无父场景标识 layer3，只作为弱兜底证据，不作为导航起点")
        return None

    def _scene_identity_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            shape.raw
            for shape in View(image).get_shapes(include_groups=False)
            if shape.is_scene_identity
        ]

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
            trace_dir.mkdir(parents=True, exist_ok=True)
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
        try:
            response = _screencap_game_window2_service() if entry.mode == "local" else _remote_game_window2_screencap(entry)
        except Exception as exc:
            if entry.mode == "local":
                state = record_mumu_adb_failure(exc, recover=True)
                with self._lock:
                    self._status["device_health"] = state
                    if state.get("recovered"):
                        self._log_locked(
                            "warning",
                            "ADB 取帧失败后已恢复 MuMu 安卓容器，重试截图",
                            scope="guard",
                            item_id="device_health",
                        )
                        self._sync_guard_status_locked()
                if state.get("recovered"):
                    response = _screencap_game_window2_service()
                else:
                    raise
            else:
                raise
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
            entry_id=str(getattr(ctx.get("entry"), "entry_id", "") or getattr(ctx.get("entry"), "id", "") or ""),
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
        entry_id: str = "",
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
        scan_box = shape.get("_match_scan_box")
        if not isinstance(scan_box, dict):
            scan_box = None
        scan_box_payload = self._box(scan_box, image) if scan_box is not None else None
        payload = {
            "entry_id": str(entry_id or ""),
            "filename": filename,
            "box": self._box(shape, image),
            "scan_box": scan_box_payload if scan else None,
            "scan": scan,
            "pixel_tolerance": int(shape.get("pixelTolerance") if shape.get("pixelTolerance") is not None else 20),
            "alpha_mask_data_url": ((shape.get("alphaMask") or {}).get("dataUrl") if isinstance(shape.get("alphaMask"), dict) else None),
            "ocr_mask_mode": shape.get("ocrMaskMode") or "inherit-envelope",
            "ocr_mask_data_url": ((shape.get("ocrMask") or {}).get("dataUrl") if isinstance(shape.get("ocrMask"), dict) else None),
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

    def _match_source_filename(self, image: dict[str, Any]) -> str:
        return str(image.get("filename") or "").strip()

    def _match_source_missing_cached(self, image: dict[str, Any]) -> bool:
        filename = self._match_source_filename(image)
        return bool(filename and filename in self._missing_match_source_filenames)

    def _record_missing_match_source(self, image: dict[str, Any], exc: Exception) -> bool:
        message = str(exc)
        if "截图不存在" not in message:
            return False
        filename = self._match_source_filename(image)
        if not filename:
            return False
        self._missing_match_source_filenames.add(filename)
        return True

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
        if self._match_source_missing_cached(image):
            return 0
        try:
            condition = "image" if match_strategy == "anchor_pixel" else "auto"
            score = float(self._match_shape(ctx, image, shape, frame_data_url, condition=condition).get("similarity") or 0)
            if ocr_fallback and score < self.scene_threshold and self._shape_ocr_fallback_enabled(shape):
                ocr_score = float(self._match_shape(ctx, image, shape, frame_data_url, condition="ocr").get("similarity") or 0)
                score = max(score, ocr_score)
            return score
        except Exception as exc:
            if not self._record_missing_match_source(image, exc):
                self._log("detail", f"匹配失败：{image.get('title')} / {shape.get('title')}：{exc}")
            return 0

    def _shape_match_role(self, shape: dict[str, Any], key: str, default: str = "required") -> str:
        return ShapeMatchPlanner().match_role(shape, key, default)

    def _shape_ocr_role(self, shape: dict[str, Any]) -> str:
        return ShapeMatchPlanner().ocr_role(shape)

    def _shape_image_role(self, shape: dict[str, Any]) -> str:
        return ShapeMatchPlanner().match_role(shape, "imageMatchRole", "off")

    def _shape_runtime_match_payload_flags(self, shape: dict[str, Any], *, condition: str = "auto") -> dict[str, Any]:
        ocr_text = str(shape.get("ocrText") or "").strip()
        image_role = self._shape_image_role(shape)
        ocr_role = self._shape_ocr_role(shape)
        force_image = condition == "image"
        force_ocr = condition == "ocr"
        ocr_enabled = bool(not force_image and ocr_role != "off" and ocr_text)
        scan_enabled = bool(shape.get("floating") and not ocr_enabled)
        jitter_enabled = bool(shape.get("jitterEnabled") and not scan_enabled and not ocr_enabled)
        return {
            "image_role": image_role,
            "ocr_role": ocr_role,
            "ocr_enabled": ocr_enabled,
            "scan": scan_enabled,
            "match_strategy": "auto" if (force_ocr or scan_enabled or jitter_enabled) else "anchor_pixel",
        }

    def _shape_match_conditions(self, shape: dict[str, Any]) -> list[str]:
        first = "ocr" if self._shape_prefers_ocr_first(shape) else "image"
        conditions: list[str] = []
        if self._shape_image_role(shape) != "off":
            conditions.append("image")
        if self._shape_ocr_role(shape) != "off" and str(shape.get("ocrText") or "").strip():
            conditions.append("ocr")
        if first == "ocr":
            conditions.sort(key=lambda item: 0 if item == "ocr" else 1)
        return conditions

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
        if ocr_enabled and (
            self._has_cached_ocr_lines(ctx, frame_data_url)
            or (image_role != "off" and not str(image.get("filename") or "").strip())
        ):
            self._cached_ocr_lines(ctx, frame_data_url)
            result = self._shape_cached_frame_ocr_match(ctx, image, shape, frame_data_url)
            result["flags"] = flags
            if result.get("matched") or image_role == "off":
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
        if ocr_enabled and not result_ocr_matched and self._has_cached_ocr_lines(ctx, frame_data_url):
            existing_fixed_box = result.get("fixed_box") if isinstance(result.get("fixed_box"), dict) else None
            existing_resolved_box = result.get("resolved_box") if isinstance(result.get("resolved_box"), dict) else None
            frame_ocr_result = self._shape_cached_frame_ocr_match(ctx, image, shape, frame_data_url)
            if bool(frame_ocr_result.get("matched")):
                if existing_fixed_box is not None:
                    if isinstance(frame_ocr_result.get("fixed_box"), dict):
                        frame_ocr_result["ocr_box"] = frame_ocr_result.get("fixed_box")
                    frame_ocr_result["fixed_box"] = existing_fixed_box
                    frame_ocr_result["resolved_box"] = existing_resolved_box or existing_fixed_box
                result = {**result, **frame_ocr_result}
                result_ocr_matched = True
                similarity = max(similarity, float(result.get("similarity") or 0))
        matched = result_ocr_matched or similarity >= float(self.scene_threshold)
        if ocr_enabled and not result_ocr_matched:
            matched = matched and bool(result.get("matches"))
        if matched:
            fixed_box = result.get("fixed_box")
            if isinstance(result.get("resolved_box"), dict):
                pass
            elif isinstance(fixed_box, dict):
                result["resolved_box"] = fixed_box
            else:
                result["resolved_box"] = result.get("box") if isinstance(result.get("box"), dict) else self._box(shape, image)
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
        existing_fixed_box = result.get("fixed_box") if isinstance(result.get("fixed_box"), dict) else None
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            text = _sanitize_ocr_text(item.get("text") or item.get("ocr_text"))
            if text and self._ocr_text_matches(text, target, mode):
                result["ocr_text"] = text
                fixed_box = self._ocr_line_box(item)
                if fixed_box is not None:
                    if existing_fixed_box is not None:
                        result["ocr_box"] = fixed_box
                        result.setdefault("resolved_box", existing_fixed_box)
                    else:
                        result["fixed_box"] = fixed_box
                        resolved_box = self._ocr_match_resolved_box(item, target, mode)
                        result["resolved_box"] = resolved_box or fixed_box
                return True
        return False

    def _shape_cached_frame_ocr_match(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
    ) -> dict[str, Any]:
        result = {
            "ok": True,
            "matched": False,
            "similarity": 0,
            "matches": [],
            "box": self._box(shape, image),
            "reason": "cached_frame_ocr",
        }
        target = _sanitize_ocr_text(shape.get("ocrText"))
        if not target:
            return result
        mode = str(shape.get("ocrMatchMode") or "contains")
        cache = ctx.get("_ocr_lines_cache")
        lines = cache.get("lines") if isinstance(cache, dict) and cache.get("frame") == frame_data_url else []
        if not isinstance(lines, list):
            return result
        box = self._box(shape, image)
        for line in lines:
            if not isinstance(line, dict):
                continue
            text = _sanitize_ocr_text(line.get("text"))
            if not text or not self._ocr_text_matches(text, target, mode):
                continue
            if not self._ocr_line_overlaps_box(line, box):
                continue
            result["matched"] = True
            result["similarity"] = 100
            result["ocr_text"] = text
            result["matches"] = [line]
            fixed_box = self._ocr_line_box(line)
            if fixed_box is not None:
                result["fixed_box"] = fixed_box
                result["resolved_box"] = self._ocr_match_resolved_box(line, target, mode) or fixed_box
            return result
        return result

    def _ocr_options_cache_key(self, options: dict[str, Any] | None = None) -> str:
        if not options:
            return "{}"
        try:
            return json.dumps(options, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            return str(sorted((str(key), str(value)) for key, value in options.items()))

    def _cached_ocr_result(self, ctx: dict[str, Any], frame_data_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        cache = ctx.setdefault("_ocr_lines_cache", {})
        options_key = self._ocr_options_cache_key(options)
        if (
            isinstance(cache, dict)
            and cache.get("frame") == frame_data_url
            and cache.get("options_key") == options_key
            and isinstance(cache.get("lines"), list)
        ):
            return cache
        response = self._ocr_frame(frame_data_url, options=options)
        lines = response.get("lines") if isinstance(response.get("lines"), list) else []
        words = response.get("words") if isinstance(response.get("words"), list) else []
        ocr_lines_method = getattr(self, "_ocr_lines", None)
        if (
            not lines
            and callable(ocr_lines_method)
            and getattr(ocr_lines_method, "__func__", None) is not type(self)._ocr_lines
        ):
            fallback_lines = ocr_lines_method(frame_data_url)
            if isinstance(fallback_lines, list):
                lines = fallback_lines
        cache = {"frame": frame_data_url, "options_key": options_key, "lines": lines, "words": words}
        ctx["_ocr_lines_cache"] = cache
        return cache

    def _cached_ocr_lines(self, ctx: dict[str, Any], frame_data_url: str) -> list[dict[str, Any]]:
        result = self._cached_ocr_result(ctx, frame_data_url)
        lines = result.get("lines")
        if not isinstance(lines, list):
            return []
        return lines

    def _has_cached_ocr_lines(self, ctx: dict[str, Any], frame_data_url: str) -> bool:
        cache = ctx.get("_ocr_lines_cache")
        lines = cache.get("lines") if isinstance(cache, dict) and cache.get("frame") == frame_data_url else None
        return isinstance(lines, list) and any(isinstance(line, dict) for line in lines)

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
            shape_score=lambda score_ctx, score_image, score_shape, score_frame: self._scene_identity_image_shape_score(
                score_ctx,
                score_image,
                score_shape,
                score_frame,
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

    def _scene_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        scene_identity_shapes = self._scene_identity_shapes(image)
        if not scene_identity_shapes and self._image_layer(image) == 3:
            return 0.0
        scorer = SceneScorer(
            shape_score=lambda score_ctx, score_image, score_shape, score_frame: self._scene_identity_image_shape_score(
                score_ctx,
                score_image,
                score_shape,
                score_frame,
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
        )
        scores = [
            scorer.scene_identity_shape_score(ctx, image, shape, frame_data_url)
            for shape in scene_identity_shapes
        ]
        score = min(scores) if scores else 0.0
        return self._scene_discriminator_adjusted_score(ctx, image, frame_data_url, score)

    def _scene_identity_image_shape_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        if self._shape_ocr_role(shape) == "required" and str(shape.get("ocrText") or "").strip():
            return 0.0
        score = self._shape_score(ctx, image, shape, frame_data_url, ocr_fallback=False)
        return score

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
            scan_fallback=False,
            ocr_fallback=True,
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
        ocr_fallback: bool = True,
    ) -> float:
        scores: list[float] = []
        for shape in shapes:
            score = self._shape_score(ctx, image, shape, frame_data_url, ocr_fallback=ocr_fallback)
            if scan_fallback and score < 50 and self._shape_image_role(shape) != "off" and not self._match_source_missing_cached(image):
                try:
                    scan_score = float(self._run_match(ctx, image, shape, frame_data_url, scan=True, match_strategy="auto").get("similarity") or 0)
                    score = max(score, scan_score)
                except Exception as exc:
                    if not self._record_missing_match_source(image, exc):
                        self._log("detail", f"{log_label}扫描失败：{image.get('title')} / {shape.get('title')}：{exc}")
            scores.append(score)
        scores = [score for score in scores if score > 0]
        if not scores:
            return 0
        scores.sort(reverse=True)
        return sum(scores[: min(3, len(scores))]) / min(3, len(scores))

    def _identify_scene(self, ctx: dict[str, Any], frame_data_url: str, keys: list[str] | None = None) -> tuple[str, float]:
        recognizer = self._scene_recognizer()
        return recognizer.identify_scene_key(
            ctx,
            frame_data_url,
            keys=keys or self._scene_key_order(),
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
        raw_click_x, raw_click_y = ActionPlanner().shape_center(image, shape)
        click_x, click_y = raw_click_x, raw_click_y
        resolved_click = None
        if shape.get("clickResolvedBox") is not False:
            resolved_click = self._shape_match_resolved_click_point(image, shape, action_match_result)
        if resolved_click is not None and not self._shape_should_keep_raw_click_for_ocr_navigation(shape, action_match_result):
            click_x, click_y = resolved_click
        if action_match_result is not None:
            self._log(
                "detail",
                (
                    f"点击标注「{shape.get('title') or shape.get('id')}」："
                    f"similarity={float(action_match_result.get('similarity') or 0):.0f}，"
                    f"ocr={str(action_match_result.get('ocr_text') or '')[:40]}，"
                    f"fixed_box={action_match_result.get('fixed_box')}，"
                    f"click=({click_x:.1f},{click_y:.1f})，"
                    f"raw=({raw_click_x:.1f},{raw_click_y:.1f})"
                ),
            )
        payload = ActionPlanner().click_shape_payload(image, shape)
        entry: Any = ctx["entry"]
        payload["x"] = float(click_x)
        payload["y"] = float(click_y)
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
        if entry.mode == "local":
            payload["input_backend"] = "adb"
            _click_game_window2_service(payload)
        else:
            _click_remote_game_window2(entry, payload)
        self._clear_tick_frame(ctx)

    def _shape_match_resolved_click_point(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        match_result: dict[str, Any] | None,
    ) -> tuple[float, float] | None:
        if not isinstance(match_result, dict):
            return None
        reference_box = match_result.get("box")
        resolved_box = match_result.get("resolved_box") or match_result.get("fixed_box")
        if not isinstance(reference_box, dict) or not isinstance(resolved_box, dict):
            return None
        ref_w = float(reference_box.get("w") or 0)
        ref_h = float(reference_box.get("h") or 0)
        dst_w = float(resolved_box.get("w") or 0)
        dst_h = float(resolved_box.get("h") or 0)
        if ref_w <= 0 or ref_h <= 0 or dst_w <= 0 or dst_h <= 0:
            return None
        raw_x, raw_y = ActionPlanner().shape_center(image, shape)
        ref_x = float(reference_box.get("x") or 0)
        ref_y = float(reference_box.get("y") or 0)
        dst_x = float(resolved_box.get("x") or 0)
        dst_y = float(resolved_box.get("y") or 0)
        return (
            dst_x + (raw_x - ref_x) * dst_w / ref_w,
            dst_y + (raw_y - ref_y) * dst_h / ref_h,
        )

    def _shape_should_keep_raw_click_for_ocr_navigation(
        self,
        shape: dict[str, Any],
        match_result: dict[str, Any] | None,
    ) -> bool:
        if not isinstance(match_result, dict):
            return False
        if not str(shape.get("sceneJumpTarget") or "").strip():
            return False
        title = str(shape.get("title") or "").strip()
        if title not in {"返回", "关闭", "离开", "退出", "回到世界"}:
            return False
        if self._shape_image_role(shape) != "off" or self._shape_ocr_role(shape) != "required":
            return False
        return isinstance(match_result.get("fixed_box"), dict) or isinstance(match_result.get("resolved_box"), dict)

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
        if str(shape.get("sceneJumpTarget") or "").strip() and "一键领取" not in shape_title:
            return True
        return (image_number, shape_title) in {
            (34, "打开下方菜单"),
            (69, "退出"),
            (121, "一键删除"),
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

    def _shape_has_runtime_click_condition(self, shape: dict[str, Any]) -> bool:
        image_role = str(shape.get("imageMatchRole") or "off").strip().lower()
        if "imageMatchRole" in shape and image_role and image_role != "off":
            return True
        ocr_role = str(shape.get("ocrMatchRole") or "off").strip().lower()
        return bool(
            "ocrMatchRole" in shape
            and ocr_role
            and ocr_role != "off"
            and str(shape.get("ocrText") or "").strip()
        )

    def _click_shape_respecting_conditions(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        image: dict[str, Any],
        shape: dict[str, Any],
        payload: dict[str, Any],
        *,
        label: str,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
        timeout_key: str = "shape_click_timeout",
    ):
        if self._shape_has_runtime_click_condition(shape):
            frame, match_result = yield from self._wait_shape_match(
                ctx,
                stop_event,
                image,
                shape,
                timeout=float(payload.get(timeout_key) or payload.get("shape_click_timeout") or 8.0),
                label=label,
            )
            self._click_shape(ctx, image, shape, frame, match_result=match_result)
            return
        if bool(shape.get("floating")):
            self._log(
                "warning",
                f"{label}：标注「{shape.get('title') or shape.get('id')}」开启了浮动但没有图像/OCR条件，退化为固定坐标点击",
            )
        width, height = self._frame_size(image)
        click_x = (float(shape.get("x") or 0) + float(shape.get("w") or 0) * float(x_ratio)) * width
        click_y = (float(shape.get("y") or 0) + float(shape.get("h") or 0) * float(y_ratio)) * height
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        runtime.click_frame_point(image, click_x, click_y)

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
        if entry.mode == "local":
            payload["input_backend"] = "adb"
            _click_game_window2_service(payload)
        else:
            _click_remote_game_window2(entry, payload)
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
        if entry.mode == "local":
            payload["input_backend"] = "adb"
            _drag_game_window2_service(payload)
        else:
            _drag_remote_game_window2(entry, payload)
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
            try:
                text = self._ocr_text(self._ocr_lines(frame))
            except Exception:
                text = ""
            reward_transition_matches = getattr(self, "_mail_reward_transition_text_matches", None)
            if callable(reward_transition_matches) and reward_transition_matches(text):
                with self._lock:
                    self._status.update(
                        {
                            "phase": "wait_mail_reward_transition",
                            "current_scene": scene_id,
                            "message": f"{label}：检测到领取奖励过场，等待自动回到邮件 #121",
                            "updated_at": time.time(),
                        }
                    )
                continue
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

    def _ocr_frame(self, frame_data_url: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = _recognize_data_annotation_ocr_frame(frame_data_url, options=options)
        except Exception as exc:
            self._log("detail", f"OCR 失败：{exc}")
            return {"lines": [], "words": []}
        return {
            "lines": [line.model_dump() for line in response.lines],
            "words": [word.model_dump() for word in getattr(response, "words", [])],
        }

    def _ocr_lines(self, frame_data_url: str) -> list[dict[str, Any]]:
        response = self._ocr_frame(frame_data_url)
        lines = response.get("lines")
        return lines if isinstance(lines, list) else []

    def _ocr_lines_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        options: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding, ctx=ctx)
        if crop is None:
            response = self._ocr_frame(frame_data_url, options=options)
            return response.get("lines") if isinstance(response.get("lines"), list) else []
        crop_data_url, offset_x, offset_y = crop
        response = self._ocr_frame(crop_data_url, options=options)
        lines = response.get("lines") if isinstance(response.get("lines"), list) else []
        for line in lines:
            line["x"] = float(line.get("x") or 0) + offset_x
            line["y"] = float(line.get("y") or 0) + offset_y
        return lines

    def _ocr_words_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        options: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding, ctx=ctx)
        if crop is None:
            response = self._ocr_frame(frame_data_url, options=options)
            return response.get("words") if isinstance(response.get("words"), list) else []
        crop_data_url, offset_x, offset_y = crop
        response = self._ocr_frame(crop_data_url, options=options)
        words = response.get("words") if isinstance(response.get("words"), list) else []
        for word in words:
            word["x"] = float(word.get("x") or 0) + offset_x
            word["y"] = float(word.get("y") or 0) + offset_y
        return words

    def _crop_frame_data_url_for_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        ctx: dict[str, Any] | None = None,
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
                        source_image = self._effective_shape_source_image(ctx, image, shape) if isinstance(ctx, dict) else image
                        boxes.append(self._box(shape, source_image))
                        continue
                    if isinstance(ctx, dict):
                        for ancestor in self._image_ancestor_chain(ctx, image):
                            shape = self._find_shape(ancestor, str(title))
                            if shape:
                                boxes.append(self._box(shape, ancestor))
                                break
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

    def _ocr_line_box(self, line: dict[str, Any]) -> dict[str, float] | None:
        x = float(line.get("x") or 0)
        y = float(line.get("y") or 0)
        w = float(line.get("w") or 0)
        h = float(line.get("h") or 0)
        if w <= 0 or h <= 0:
            return None
        return {"x": x, "y": y, "w": w, "h": h}

    def _ocr_match_resolved_box(self, line: dict[str, Any], target: str, mode: str = "contains") -> dict[str, float] | None:
        text = _sanitize_ocr_text(line.get("text"))
        target_text = _sanitize_ocr_text(target)
        line_box = self._ocr_line_box(line)
        if not text or not target_text or line_box is None:
            return None
        mode = str(mode or "contains").strip().lower()
        start = -1
        end = -1
        if mode == "regex":
            try:
                match = re.search(target_text, text)
            except re.error:
                match = None
            if match is not None:
                start, end = match.span()
        elif mode == "exact":
            if text == target_text:
                return line_box
        elif mode == "wildcard":
            if self._ocr_text_matches(text, target_text, mode):
                return line_box
        else:
            start = text.find(target_text)
            if start >= 0:
                end = start + len(target_text)
        if start < 0 or end <= start:
            return None
        text_len = max(1, len(text))
        left_ratio = min(1.0, max(0.0, start / text_len))
        right_ratio = min(1.0, max(0.0, end / text_len))
        x = line_box["x"] + line_box["w"] * left_ratio
        w = max(1.0, line_box["w"] * (right_ratio - left_ratio))
        return {"x": x, "y": line_box["y"], "w": w, "h": line_box["h"]}

    def _ocr_substring_center(self, line: dict[str, Any], target: str) -> tuple[float, float] | None:
        box = self._ocr_match_resolved_box(line, target, "contains")
        if box is None:
            return None
        return float(box.get("x") or 0) + float(box.get("w") or 0) / 2, float(box.get("y") or 0) + float(box.get("h") or 0) / 2

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
                    center = self._ocr_substring_center(line, target_fragment)
                    if center is not None:
                        cx = center[0]
            cy = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
            if left <= cx <= right and top <= cy <= bottom:
                matches.append((cx, cy, text))
        return sorted(matches, key=lambda item: (item[1], item[0]))

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
        scene_title = str(current_image.get("title") or "").strip()
        filename = str(current_image.get("filename") or "").strip()
        source_title = str(source_shape.get("title") or "").strip()
        if source_title not in {"离开", "返回", "关闭", "退出"}:
            return None
        if filename == "0086.png" or "离开场景" in scene_title:
            for shape in self._flatten_shapes(current_image.get("shapes")):
                if str(shape.get("title") or "").strip() in {"确认", "确定"}:
                    return shape
        if "离开" not in scene_title and "退出" not in scene_title:
            return None
        for shape in self._flatten_shapes(current_image.get("shapes")):
            if str(shape.get("title") or "").strip() in {"确认", "确定"}:
                return shape
        return None

    def _recover_unknown_green_bottle_return_popup(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
    ) -> bool:
        if int(source_scene_id) != 20 or int(target_scene_id) != 34 or 34 not in {int(item) for item in expected_ids}:
            return False
        world_image = (ctx.get("images") or {}).get(34)
        if not isinstance(world_image, dict):
            return False
        blank_shape = self._find_shape(world_image, "空白")
        if not isinstance(blank_shape, dict):
            return False
        x, y = ActionPlanner().shape_center(world_image, blank_shape)
        with self._lock:
            self._status.update({
                "phase": "go_scene_unknown_recover",
                "current_scene": None,
                "message": "场景移动：#20 -> #34 遇到 unknown，点击 #34「空白」关闭未归档弹窗",
                "updated_at": time.time(),
            })
        self._log("action", "场景移动：#20 -> #34 遇到 unknown，点击 #34「空白」尝试关闭未归档弹窗")
        self._save_action_trace(
            ctx,
            world_image,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": "click #34 空白 unknown recovery",
                "shape_title": blank_shape.get("title"),
                "shape_id": blank_shape.get("id"),
            },
            frame_data_url=frame_data_url,
        )
        self._click_frame_point(ctx, world_image, x, y)
        self._clear_tick_frame(ctx)
        return True

    def _recover_unknown_hidden_world_popup(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
    ) -> bool:
        image59 = (ctx.get("images") or {}).get(59)
        if not isinstance(image59, dict):
            return False
        blank_shape = self._find_shape(image59, "空白")
        if not isinstance(blank_shape, dict):
            return False
        score = float(self._shape_score(ctx, image59, blank_shape, frame_data_url) or 0.0)
        if score < float(self.overlay_threshold):
            return False
        x, y = ActionPlanner().shape_center(image59, blank_shape)
        with self._lock:
            self._status.update({
                "phase": "go_scene_unknown_recover",
                "current_scene": 59,
                "message": "场景移动：遇到 #59「封魔杀」弹层，点击「空白」关闭",
                "updated_at": time.time(),
            })
        self._log("action", f"场景移动：遇到 #59「封魔杀」弹层，点击「空白」关闭 {score:.0f}%")
        self._save_action_trace(
            ctx,
            image59,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": "click #59 空白 hidden world popup recovery",
                "shape_title": blank_shape.get("title"),
                "shape_id": blank_shape.get("id"),
                "score": round(score, 1),
            },
            frame_data_url=frame_data_url,
        )
        self._click_frame_point(ctx, image59, x, y)
        self._clear_tick_frame(ctx)
        return True

    def _recover_unknown_promo_offer_page(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
    ) -> bool:
        text = self._ocr_text(self._cached_ocr_lines(ctx, frame_data_url))
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        is_store_offer = any(token in compact for token in ("至尊甄选", "适度娱乐", "理性消费", "限购次数"))
        is_signup_activity_offer = (
            ("活动规则" in compact or "活动时间" in compact)
            and "前往" in compact
            and any(token in compact for token in ("云梦试剑", "道法争锋", "宗门灵泉", "宗门镇邪", "炼体法相", "天地弈局"))
        )
        if not (is_store_offer or is_signup_activity_offer):
            return False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        chosen_image: dict[str, Any] | None = None
        chosen_shape: dict[str, Any] | None = None
        for image_id, shape_title in ((249, "返回"), (23, "返回"), (250, "返回"), (59, "空白")):
            image = images.get(image_id) if isinstance(images, dict) else None
            shape = self._find_shape(image, shape_title) if isinstance(image, dict) else None
            if isinstance(image, dict) and isinstance(shape, dict):
                chosen_image = image
                chosen_shape = shape
                break
        if chosen_image is None or chosen_shape is None:
            return False
        x, y = ActionPlanner().shape_center(chosen_image, chosen_shape)
        image_id = self._image_number(chosen_image)
        title = str(chosen_shape.get("title") or chosen_shape.get("id") or "返回")
        with self._lock:
            self._status.update({
                "phase": "go_scene_unknown_recover",
                "current_scene": None,
                "message": f"场景移动：unknown 促销活动页，点击 #{image_id}「{title}」关闭",
                "updated_at": time.time(),
            })
        self._log("action", f"场景移动：unknown 促销活动页 OCR 命中，点击 #{image_id}「{title}」关闭")
        self._save_action_trace(
            ctx,
            chosen_image,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": f"click #{image_id} {title} promo offer recovery",
                "shape_title": chosen_shape.get("title"),
                "shape_id": chosen_shape.get("id"),
            },
            frame_data_url=frame_data_url,
        )
        self._click_frame_point(ctx, chosen_image, x, y)
        self._clear_tick_frame(ctx)
        return True

    def _recover_unknown_start_to_world(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        *,
        target_scene_id: int,
    ) -> bool:
        if int(target_scene_id) not in {34, 69}:
            return False
        if self._recover_unknown_promo_offer_page(ctx, frame_data_url):
            return True
        if self._recover_unknown_hidden_world_popup(ctx, frame_data_url):
            return True
        return False

    def _unknown_route_recovery_shape_allowed(self, shape: dict[str, Any]) -> bool:
        title = str(shape.get("title") or shape.get("id") or "").strip()
        if not title:
            return False
        safe_keywords = ("返回", "关闭", "离开", "退出", "取消", "空白", "回到世界")
        return any(keyword in title for keyword in safe_keywords) and self._scene_navigation_shape_risk(shape) < 100

    def _recover_unknown_by_matched_existing_route(
        self,
        ctx: dict[str, Any],
        tree: list[dict[str, Any]],
        frame_data_url: str,
        *,
        target_scene_id: int,
    ) -> bool:
        try:
            evidence = build_unknown_evidence(
                self,
                ctx,
                frame_data_url,
                label=f"go_scene_{target_scene_id}_recover",
                expected_scene_ids=[target_scene_id],
                last_scene_id=None,
                last_score=0.0,
            )
        except Exception as exc:
            self._log("warning", f"场景移动：unknown 低风险恢复诊断失败：{exc}")
            return False
        if evidence.classification != "matched_existing_frame":
            return False
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        for candidate in evidence.candidates:
            similarity = max(float(candidate.scene_score or 0.0), float(candidate.frame_similarity or 0.0))
            if similarity < 80.0:
                continue
            image = images.get(int(candidate.scene_id)) if isinstance(images, dict) else None
            if not isinstance(image, dict):
                continue
            route = self._find_scene_route(tree, int(candidate.scene_id), int(target_scene_id))
            if not route or self._scene_route_navigation_risk(route) >= 100:
                continue
            first_edge = route[0]
            first_shape = first_edge.get("shape") if isinstance(first_edge, dict) else None
            first_image = first_edge.get("image") if isinstance(first_edge, dict) else None
            if not isinstance(first_shape, dict) or not isinstance(first_image, dict):
                continue
            if int(first_edge.get("source_id") or 0) != int(candidate.scene_id):
                continue
            if not self._unknown_route_recovery_shape_allowed(first_shape):
                continue
            source_id = self._image_number(first_image) or candidate.scene_id
            title = str(first_shape.get("title") or first_shape.get("id") or "未命名")
            with self._lock:
                self._status.update({
                    "phase": "go_scene_unknown_recover",
                    "current_scene": None,
                    "message": f"场景移动：unknown 像 #{candidate.scene_id}，点击低风险「{title}」恢复到 #{target_scene_id}",
                    "updated_at": time.time(),
                })
            self._log(
                "warning",
                f"场景移动：unknown 与 #{candidate.scene_id}「{candidate.title}」相似 {similarity:.0f}%，"
                f"复用已标注低风险路径，点击 #{source_id}「{title}」",
            )
            self._click_scene_route_shape(ctx, first_image, first_shape, frame_data_url)
            return True
        return False

    def _xianfu_home_text_is_scene(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        world_markers = (
            "大地图",
            "日程",
            "角色",
            "装备",
            "星海",
            "功法书",
            "储物袋",
        )
        if any(marker in normalized for marker in world_markers):
            return False
        markers = (
            "玄机阁",
            "仙侣居",
            "仙侶居",
            "本命金身",
            "拜仙台",
            "寻仙台",
            "仙府管家",
        )
        hits = sum(1 for marker in markers if marker in normalized)
        if hits >= 2:
            return True
        return "仙府管家" in normalized and ("寻仙台" in normalized or "拜仙台" in normalized)

    def _is_xianfu_entry_cutscene(self, ctx: dict[str, Any], scene_id: int | None) -> bool:
        if scene_id != 185:
            return False
        image = (ctx.get("images") or {}).get(185) if isinstance(ctx.get("images"), dict) else None
        if not isinstance(image, dict):
            return False
        title = str(image.get("title") or "")
        shapes = image.get("shapes") if isinstance(image.get("shapes"), list) else []
        has_skip = any(str(shape.get("title") or "") == "跳过" for shape in shapes if isinstance(shape, dict))
        return has_skip and "过场" in title

    def _is_xianfu_entry_recoverable_landing(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
        scene_id: int | None,
        shape: dict[str, Any],
    ) -> bool:
        title = str(shape.get("title") or "")
        return (
            source_scene_id == 34
            and scene_id == 197
            and (target_scene_id == 171 or 171 in expected_ids)
            and "仙府" in title
        )

    def _observed_landing_scene_ids(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> set[int]:
        scene_ids: set[int] = set()
        for entry in self._parse_scene_jump_entries(shape.get("observedLanding")):
            for scene_id in self._resolve_scene_jump_label(tree, entry.get("label")):
                scene_ids.add(int(scene_id))
        return scene_ids

    def _is_scene_jump_recoverable_interruption(
        self,
        ctx: dict[str, Any],
        tree: list[dict[str, Any]],
        *,
        source_scene_id: int,
        target_scene_id: int,
        scene_id: int,
        shape: dict[str, Any],
    ) -> bool:
        if int(scene_id) in {int(source_scene_id), int(target_scene_id)}:
            return False
        if self._find_scene_route(tree, int(scene_id), int(target_scene_id)) is None:
            return False
        if int(scene_id) in self._observed_landing_scene_ids(tree, shape):
            return True
        clicked_title = str(shape.get("title") or shape.get("id") or "").strip()
        if clicked_title in {"返回", "退出", "离开", "关闭", "空白", "回到世界"}:
            return True
        image = (ctx.get("images") or {}).get(int(scene_id)) if isinstance(ctx.get("images"), dict) else None
        if not isinstance(image, dict):
            return False
        image_title = str(image.get("title") or "")
        if not any(marker in image_title for marker in ("奖励", "提示")):
            return False
        safe_titles = {"领取", "确定", "确认", "关闭", "返回", "退出", "空白"}
        return any(
            str(shape.get("title") or shape.get("id") or "").strip() in safe_titles
            and bool(str(shape.get("sceneJumpTarget") or "").strip())
            for shape in self._flatten_shapes(image.get("shapes"))
        )

    def _is_xianfu_cutscene_landing_target(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        scene_id: int | None,
        shape: dict[str, Any],
    ) -> bool:
        return (
            source_scene_id == 185
            and target_scene_id == 171
            and scene_id == 171
            and str(shape.get("title") or "") == "跳过"
        )

    def _scene_jump_source_stall_timeout(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
        shape: dict[str, Any],
    ) -> float:
        title = str(shape.get("title") or "")
        if source_scene_id == 34 and (target_scene_id == 171 or 171 in expected_ids) and "仙府" in title:
            return 60.0
        return 8.0

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
        return_source_on_stall: bool = False,
        layer0_wait_seconds: float | None = None,
    ):
        shape = edge["shape"]
        expected_ids = list(edge.get("target_ids") or [])
        allows_self = source_scene_id in expected_ids
        preferred_wait_seconds = max(
            0.0,
            float(layer0_wait_seconds if layer0_wait_seconds is not None else (DEFAULT_LAYER0_WAIT_SECONDS if expected_ids else 0.0)),
        )
        timeout_seconds = max(30.0 if allows_self else 60.0, preferred_wait_seconds)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_frame = ""
        history: list[str] = []
        left_source = False
        handled_intermediate_scene_ids: set[int] = set()
        handled_world_side_leave = False
        handled_green_bottle_unknown_recovery = False
        shape_jump_target = str(shape.get("sceneJumpTarget") or "").strip()
        dynamic_landing = bool(edge.get("_runtime_confirm_edge")) or shape_jump_target == "-1" or shape_jump_target.startswith("-1(")
        source_stall_timeout = self._scene_jump_source_stall_timeout(
            source_scene_id=source_scene_id,
            target_scene_id=target_scene_id,
            expected_ids=expected_ids,
            shape=shape,
        )

        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start

            expected_id_set = {int(item) for item in expected_ids}
            fallback_scene_id: int | None = None
            fallback_score = 0.0
            matched_expected, expected_score = self._identify_scene_number(ctx, frame, expected_ids)
            if matched_expected == source_scene_id and source_scene_id not in expected_ids:
                history.append(f"{elapsed:.1f}s #{matched_expected} {expected_score:.0f}% preferred-source ignored left={left_source}")
                matched_expected = None
            if matched_expected is not None and int(matched_expected) not in expected_id_set:
                fallback_scene_id, fallback_score = int(matched_expected), float(expected_score or 0.0)
                history.append(
                    f"{elapsed:.1f}s #{fallback_scene_id} {fallback_score:.0f}% "
                    f"preferred-fallback expected={expected_ids} left={left_source}"
                )
                matched_expected = None
            if matched_expected is not None:
                if not left_source and matched_expected != source_scene_id:
                    global_scene_id, global_score = self._identify_scene_number(ctx, frame)
                    if global_scene_id == source_scene_id and float(global_score or 0) >= float(expected_score or 0):
                        last_scene_id, last_score, last_frame = global_scene_id, global_score, frame
                        history.append(
                            f"{elapsed:.1f}s #{global_scene_id} {global_score:.0f}% "
                            f"expected=#{matched_expected} {expected_score:.0f}% left={left_source}"
                        )
                        continue
                last_scene_id, last_score, last_frame = matched_expected, expected_score, frame
                if matched_expected != source_scene_id:
                    left_source = True
                history.append(f"{elapsed:.1f}s #{matched_expected} {expected_score:.0f}% expected={expected_score:.0f}% left={left_source}")
                if not edge.get("_runtime_confirm_edge") and self._increment_scene_jump_target(shape, matched_expected):
                    self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                self._log("info", f"场景跳转：#{source_scene_id} -> #{matched_expected}，{elapsed:.1f}s")
                return matched_expected

            if fallback_scene_id is not None:
                scene_id, score = fallback_scene_id, fallback_score
            else:
                scene_id, score = self._identify_scene_number(ctx, frame)
            if scene_id is None:
                route_candidate_ids = self._scene_route_candidate_ids(tree, target_scene_id)
                scene_id, score = self._identify_scene_number_for_route(
                    ctx,
                    frame,
                    tree,
                    target_scene_id,
                    route_candidate_ids,
                )
            if scene_id is not None:
                navigation_scene_id = self._navigation_scene_id(ctx, scene_id, frame)
                if navigation_scene_id is not None:
                    scene_id = navigation_scene_id
            last_scene_id, last_score, last_frame = scene_id, score, frame
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
                    left_source = True
                    start = time.monotonic()
                    continue
            if (target_scene_id == 171 or 171 in expected_ids) and self._is_xianfu_entry_cutscene(ctx, scene_id):
                left_source = True
                history.append(f"{elapsed:.1f}s #{scene_id} 仙府过场 {score:.0f}% left={left_source}")
                if scene_id not in handled_intermediate_scene_ids:
                    handled_intermediate_scene_ids.add(scene_id)
                    self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id} 仙府过场，重新规划到 #171")
                    if 171 not in expected_ids and scene_id != target_scene_id and scene_id not in expected_ids:
                        return scene_id
                continue
            if scene_id is not None and scene_id != source_scene_id and int(scene_id) in expected_ids:
                left_source = True
                history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% declared-landing left={left_source}")
                if not edge.get("_runtime_confirm_edge") and self._increment_scene_jump_target(shape, int(scene_id)):
                    self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，命中声明落点")
                return int(scene_id)
            if (
                scene_id is not None
                and scene_id != source_scene_id
                and int(scene_id) not in expected_id_set
                and scene_id != target_scene_id
                and expected_ids
                and preferred_wait_seconds > 0
                and elapsed < preferred_wait_seconds
            ):
                left_source = True
                scene_text = f"#{scene_id}"
                history.append(f"{elapsed:.1f}s {scene_text} layer0-wait-before-replan target={expected_ids}")
                with self._lock:
                    self._status.update({
                        "phase": "go_scene_wait_layer0",
                        "current_scene": scene_id,
                        "message": (
                            f"跳转等待：#{source_scene_id} -> #{target_scene_id}，"
                            f"继续等待预期落点 {expected_ids}，当前 {scene_text} {score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    })
                continue
            if scene_id == target_scene_id and scene_id != source_scene_id:
                if scene_id in expected_ids:
                    left_source = True
                    history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% global-target left={left_source}")
                    if not edge.get("_runtime_confirm_edge") and self._increment_scene_jump_target(shape, scene_id):
                        self._write_asset_tree(asset_tree_path, tree)
                    ctx["images"] = self._index_images(tree)
                    self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，全局识别命中目标")
                    return scene_id
                if self._is_xianfu_cutscene_landing_target(
                    source_scene_id=source_scene_id,
                    target_scene_id=target_scene_id,
                    scene_id=scene_id,
                    shape=shape,
                ):
                    left_source = True
                    history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% xianfu-cutscene-target left={left_source}")
                    self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，仙府过场跳过后到达主页")
                    return scene_id
                if dynamic_landing:
                    self._log("detail", f"场景跳转动态落点：#{source_scene_id} -> #{scene_id}，由上层重新规划")
                    return scene_id
                self._record_unexpected_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    scene_id,
                    source_scene_id=source_scene_id,
                    reason="未声明目标落点",
                )
                raise RuntimeError(
                    f"场景跳转实际到达 #{scene_id}，但这是「{shape.get('title') or '未命名'}」的未声明落点，"
                    "不在 sceneJumpTarget 中；"
                    "Runtime 已中断，请人工确认并修正标注后重试"
                )
            if (
                scene_id is not None
                and scene_id != source_scene_id
                and self._find_scene_route(tree, scene_id, target_scene_id) is not None
            ):
                if self._is_xianfu_entry_recoverable_landing(
                    source_scene_id=source_scene_id,
                    target_scene_id=target_scene_id,
                    expected_ids=expected_ids,
                    scene_id=scene_id,
                    shape=shape,
                ):
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击仙府实际到达 #{scene_id}，"
                        f"沿用已标注路径重新规划到 #{target_scene_id}",
                    )
                    return scene_id
                if self._is_scene_jump_recoverable_interruption(
                    ctx,
                    tree,
                    source_scene_id=source_scene_id,
                    target_scene_id=target_scene_id,
                    scene_id=scene_id,
                    shape=shape,
                ):
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"实际到达可恢复中间场景 #{scene_id}，沿用已标注路径重新规划到 #{target_scene_id}",
                    )
                    return scene_id
                if dynamic_landing:
                    self._log("detail", f"场景跳转动态落点：#{source_scene_id} -> #{scene_id}，重新规划到 #{target_scene_id}")
                    return scene_id
                self._record_unexpected_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    scene_id,
                    source_scene_id=source_scene_id,
                    reason=f"未声明可恢复中间场景，目标 #{target_scene_id}",
                )
                raise RuntimeError(
                    f"场景跳转实际到达 #{scene_id}，可继续规划到 #{target_scene_id}，"
                    f"但这是「{shape.get('title') or '未命名'}」的未声明落点，不在 sceneJumpTarget 中；"
                    "Runtime 已中断，请人工确认并修正标注后重试"
                )
            if scene_id is not None and scene_id != source_scene_id:
                left_source = True
            scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
            history.append(f"{elapsed:.1f}s {scene_text} {score:.0f}% expected={expected_score:.0f}% left={left_source}")
            if 171 in expected_ids:
                text = self._ocr_text(self._ocr_lines(frame))
                if self._xianfu_home_text_is_scene(text):
                    if self._increment_scene_jump_target(shape, 171):
                        self._write_asset_tree(asset_tree_path, tree)
                        ctx["images"] = self._index_images(tree)
                    self._log("info", f"场景跳转：#{source_scene_id} -> #171，{elapsed:.1f}s，OCR 兜底命中仙府主页")
                    return 171
            if scene_id == 47:
                runtime = self._fanxiu_runtime(ctx, asset_tree_path, frame_data_url=frame, stop_event=stop_event)
                popup_view = runtime.find_view("弹窗")
                if popup_view is not None and popup_view.id == 47:
                    event = {
                        "kind": "popup",
                        "image": "#47",
                        "title": popup_view.title,
                        "score": round(score, 1),
                        "during": "scene_jump",
                    }
                    if self._handle_auto_close_popup_47_child(runtime, popup_view, event):
                        self._clear_tick_frame(ctx)
                        left_source = True
                        start = time.monotonic()
                    history.append(f"{elapsed:.1f}s #47 弹窗已处理")
                    continue
            if scene_id is None and self._recover_unknown_hidden_world_popup(ctx, frame):
                left_source = True
                start = time.monotonic()
                history.append(f"{elapsed:.1f}s unknown #59 空白已点击")
                continue
            if scene_id is None and self._recover_unknown_promo_offer_page(ctx, frame):
                left_source = True
                start = time.monotonic()
                history.append(f"{elapsed:.1f}s unknown 促销页返回已点击")
                continue
            if scene_id is None and not handled_green_bottle_unknown_recovery:
                if self._recover_unknown_green_bottle_return_popup(
                    ctx,
                    frame,
                    source_scene_id=source_scene_id,
                    target_scene_id=target_scene_id,
                    expected_ids=expected_ids,
                ):
                    handled_green_bottle_unknown_recovery = True
                    left_source = True
                    start = time.monotonic()
                    history.append(f"{elapsed:.1f}s unknown #34 空白已点击")
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
            if expected_ids and preferred_wait_seconds > 0 and elapsed < preferred_wait_seconds:
                history.append(f"{elapsed:.1f}s {scene_text} layer0-wait target={expected_ids}")
                with self._lock:
                    self._status.update({
                        "phase": "go_scene_wait_layer0",
                        "current_scene": scene_id,
                        "message": (
                            f"跳转等待：#{source_scene_id} -> #{target_scene_id}，"
                            f"继续等待预期落点 {expected_ids}，当前 {scene_text} {score:.0f}%"
                        ),
                        "updated_at": time.time(),
                    })
                continue
            with self._lock:
                self._status.update({
                    "phase": "go_scene_wait",
                    "current_scene": scene_id,
                    "message": f"跳转等待：#{source_scene_id} -> #{target_scene_id}，当前 {scene_text} {score:.0f}%",
                    "updated_at": time.time(),
                })

            if not left_source and last_scene_id == source_scene_id and not allows_self and elapsed >= source_stall_timeout:
                if return_source_on_stall:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"{elapsed:.1f}s 后仍停在源场景，回到动态选择尝试其它候选",
                    )
                    return source_scene_id
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
                if return_source_on_stall:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"{elapsed:.1f}s 后仍停在源场景，回到动态选择尝试其它候选",
                    )
                    return source_scene_id
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

            self._record_unexpected_landing(
                ctx,
                asset_tree_path,
                tree,
                shape,
                last_scene_id,
                source_scene_id=source_scene_id,
                reason="超时稳定未声明落点",
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
        *,
        layer0_wait_seconds: float | None = None,
    ):
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            tree = self._load_asset_tree(asset_tree_path)
            ctx["asset_tree"] = tree
            ctx["images"] = self._index_images(tree)

        recovered_unknown_start = False
        recovered_unknown_side_leave = False
        recovered_no_route_scene_ids: set[int] = set()
        failed_edge_keys: set[tuple[Any, ...]] = set()
        last_failed_edge: dict[str, Any] | None = None
        for _step_index in range(24):
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            known_scene_id = ctx.pop("_go_scene_known_scene_id", None)
            if known_scene_id is not None:
                current_scene_id, score = int(known_scene_id), float(self.scene_threshold)
            else:
                current_scene_id, score = self._identify_scene_number(ctx, frame)
            if current_scene_id is None:
                route_candidate_ids = self._scene_route_candidate_ids(tree, target_scene_id)
                current_scene_id, score = self._identify_scene_number_for_route(
                    ctx,
                    frame,
                    tree,
                    target_scene_id,
                    route_candidate_ids,
                )
            if current_scene_id is None:
                if not recovered_unknown_start and self._recover_unknown_start_to_world(ctx, frame, target_scene_id=target_scene_id):
                    recovered_unknown_start = True
                    yield BehaviorTreeStatus.RUNNING
                    continue
                if not recovered_unknown_side_leave:
                    text = self._ocr_text(self._ocr_lines(frame))
                    if (yield from self._leave_world_side_scene_if_present(
                        ctx,
                        stop_event,
                        frame,
                        text,
                        label="场景移动",
                        require_world_like=False,
                    )):
                        recovered_unknown_side_leave = True
                        yield BehaviorTreeStatus.RUNNING
                        continue
                if self._recover_unknown_by_matched_existing_route(
                    ctx,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                ):
                    yield BehaviorTreeStatus.RUNNING
                    continue
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
            if current_scene_id is not None and not self._scene_matches_id(int(current_scene_id), float(score or 0.0)):
                self._log(
                    "detail",
                    f"场景移动：候选 #{current_scene_id} 仅 {float(score or 0.0):.0f}%，低于阈值，不作为当前场景",
                )
                current_scene_id = None
            if current_scene_id is None:
                if not recovered_unknown_start and self._recover_unknown_start_to_world(ctx, frame, target_scene_id=target_scene_id):
                    recovered_unknown_start = True
                    yield BehaviorTreeStatus.RUNNING
                    continue
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=None,
                    action_shape=None,
                    elapsed_seconds=0.0,
                    history=[f"候选低于阈值 {score:.0f}%"],
                )
            if current_scene_id == target_scene_id and self._scene_matches_id(int(target_scene_id), float(score or 0.0)):
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                return "success"
            if int(current_scene_id) == 34 and int(target_scene_id) != 34:
                text = self._ocr_text(self._cached_ocr_lines(ctx, frame))
                runtime = self._fanxiu_runtime(ctx, asset_tree_path, frame_data_url=frame, stop_event=stop_event)
                if self._world_reward_tip_detected(ctx, frame, text):
                    yield from self._close_world_reward_tip_stack_if_present(
                        ctx,
                        runtime,
                        stop_event,
                        label="场景移动",
                        max_attempts=30,
                    )
                    yield BehaviorTreeStatus.RUNNING
                    continue
            has_navigation_edge = current_scene_id is not None and self._select_scene_next_edge(
                tree,
                int(current_scene_id),
                target_scene_id,
                failed_edge_keys=failed_edge_keys,
            ) is not None
            last_failed_source_matches = False
            if last_failed_edge is not None and current_scene_id is not None:
                try:
                    last_failed_source_matches = int(last_failed_edge.get("source_id")) == int(current_scene_id)
                except (TypeError, ValueError):
                    last_failed_source_matches = False
            if not has_navigation_edge and not last_failed_source_matches:
                current_scene_id = self._navigation_scene_id(ctx, current_scene_id, frame)
            if current_scene_id is None:
                if not recovered_unknown_start and self._recover_unknown_start_to_world(ctx, frame, target_scene_id=target_scene_id):
                    recovered_unknown_start = True
                    yield BehaviorTreeStatus.RUNNING
                    continue
                if not recovered_unknown_side_leave:
                    text = self._ocr_text(self._cached_ocr_lines(ctx, frame))
                    if (yield from self._leave_world_side_scene_if_present(
                        ctx,
                        stop_event,
                        frame,
                        text,
                        label="场景移动",
                        require_world_like=False,
                    )):
                        recovered_unknown_side_leave = True
                        yield BehaviorTreeStatus.RUNNING
                        continue
                if self._recover_unknown_by_matched_existing_route(
                    ctx,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                ):
                    yield BehaviorTreeStatus.RUNNING
                    continue
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=None,
                    action_shape=None,
                    elapsed_seconds=0.0,
                    history=[f"弱兜底匹配不可作为导航起点 {score:.0f}%"],
                )
            if current_scene_id == target_scene_id and self._scene_matches_id(int(target_scene_id), float(score or 0.0)):
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                return "success"

            decision = self._select_scene_next_edge(
                tree,
                current_scene_id,
                target_scene_id,
                failed_edge_keys=failed_edge_keys,
            )
            if decision is None:
                direct_failed_targets: set[int] = set()
                last_failed_source_id: int | None = None
                if last_failed_edge is not None:
                    try:
                        last_failed_source_id = int(last_failed_edge.get("source_id"))
                    except (TypeError, ValueError):
                        last_failed_source_id = None
                if last_failed_edge is not None and last_failed_source_id == int(current_scene_id):
                    for item in last_failed_edge.get("target_ids") or []:
                        try:
                            direct_failed_targets.add(int(item))
                        except (TypeError, ValueError):
                            continue
                if last_failed_edge is not None and last_failed_source_id == int(current_scene_id) and int(target_scene_id) in direct_failed_targets:
                    return self._save_unknown_scene_frame(
                        ctx,
                        asset_tree_path,
                        tree,
                        frame,
                        target_scene_id=target_scene_id,
                        current_scene_id=current_scene_id,
                        action_shape=last_failed_edge.get("shape") if isinstance(last_failed_edge, dict) else None,
                        elapsed_seconds=0.0,
                        history=[
                            f"#{current_scene_id} 直达入口点击后仍停在源场景，疑似被弹窗/遮挡吃掉或入口标注失效；不执行泛化恢复动作"
                        ],
                    )
                if current_scene_id not in recovered_no_route_scene_ids:
                    current_image = (ctx.get("images") or {}).get(int(current_scene_id)) if isinstance(ctx.get("images"), dict) else None
                    safe_exit_shape = None
                    if isinstance(current_image, dict):
                        for shape in self._flatten_shapes(current_image.get("shapes")):
                            title = str(shape.get("title") or shape.get("id") or "").strip()
                            if self._scene_identity_scope(shape) != "none":
                                continue
                            if title in {"返回", "退出", "离开", "关闭", "空白", "回到世界"} and self._scene_navigation_shape_risk(shape) < 100:
                                safe_exit_shape = shape
                                break
                    if isinstance(current_image, dict) and isinstance(safe_exit_shape, dict):
                        recovered_no_route_scene_ids.add(int(current_scene_id))
                        title = str(safe_exit_shape.get("title") or safe_exit_shape.get("id") or "返回")
                        self._log(
                            "warning",
                            f"场景移动：#{current_scene_id} 无到 #{target_scene_id} 的路径，先点击低风险「{title}」恢复",
                        )
                        edge = {
                            "source_id": int(current_scene_id),
                            "image": current_image,
                            "shape": safe_exit_shape,
                            "target_ids": self._scene_jump_target_ids(tree, safe_exit_shape),
                        }
                        self._click_scene_route_shape(ctx, current_image, safe_exit_shape, frame)
                        actual_scene_id = yield from self._wait_scene_jump_result(
                            ctx,
                            asset_tree_path,
                            tree,
                            source_scene_id=current_scene_id,
                            target_scene_id=target_scene_id,
                            edge=edge,
                            stop_event=stop_event,
                            return_source_on_stall=True,
                            layer0_wait_seconds=layer0_wait_seconds,
                        )
                        if actual_scene_id == target_scene_id:
                            with self._lock:
                                self._status.update({
                                    "current_scene": target_scene_id,
                                    "updated_at": time.time(),
                                })
                            self._log("success", f"到达目标场景 #{target_scene_id}")
                            return "success"
                        if actual_scene_id == current_scene_id:
                            self._log(
                                "warning",
                                f"场景移动：低风险「{title}」恢复后仍停在 #{current_scene_id}，继续尝试其它恢复动作",
                            )
                            yield BehaviorTreeStatus.RUNNING
                            continue
                        self._log("detail", f"场景移动：低风险「{title}」实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")
                        yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.5)
                        continue
                if not recovered_unknown_side_leave:
                    text = self._ocr_text(self._cached_ocr_lines(ctx, frame))
                    if (yield from self._leave_world_side_scene_if_present(
                        ctx,
                        stop_event,
                        frame,
                        text,
                        label="场景移动",
                        require_world_like=False,
                    )):
                        recovered_unknown_side_leave = True
                        yield BehaviorTreeStatus.RUNNING
                        continue
                if last_failed_edge is not None:
                    failed_shape = last_failed_edge.get("shape") if isinstance(last_failed_edge, dict) else None
                    if isinstance(failed_shape, dict):
                        self._record_unexpected_landing(
                            ctx,
                            asset_tree_path,
                            tree,
                            failed_shape,
                            current_scene_id,
                            source_scene_id=target_scene_id,
                            reason=f"点击后到达无路由场景，目标 #{target_scene_id}",
                        )
                    return self._save_unknown_scene_frame(
                        ctx,
                        asset_tree_path,
                        tree,
                        frame,
                        target_scene_id=target_scene_id,
                        current_scene_id=current_scene_id,
                        action_shape=failed_shape,
                        elapsed_seconds=0.0,
                        history=[f"#{current_scene_id} 已尝试 {len(failed_edge_keys)} 个候选仍未离开源场景"],
                    )
                raise RuntimeError(
                    f"go_scene({target_scene_id}) 失败：无法从当前#{current_scene_id}找到可达#{target_scene_id}的路径，请检查标注shape。"
                )
            edge = decision["edge"]
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
            self._log(
                "action",
                f"场景移动：#{current_scene_id} -> #{target_scene_id}，点击 {shape_title}"
                f"（{decision['reason']}）",
            )
            self._click_scene_route_shape(ctx, image, shape, frame)
            actual_scene_id = yield from self._wait_scene_jump_result(
                ctx,
                asset_tree_path,
                tree,
                source_scene_id=current_scene_id,
                target_scene_id=target_scene_id,
                edge=edge,
                stop_event=stop_event,
                return_source_on_stall=True,
                layer0_wait_seconds=layer0_wait_seconds,
            )
            if actual_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"到达目标场景 #{target_scene_id}")
                return "success"
            if actual_scene_id == current_scene_id:
                failed_edge_keys.add(self._scene_jump_edge_key(edge))
                last_failed_edge = edge
                self._log(
                    "warning",
                    f"场景移动：点击 {shape_title} 后仍在 #{current_scene_id}，"
                    f"本轮排除该候选并重新选择通往 #{target_scene_id} 的下一步",
                )
                yield BehaviorTreeStatus.RUNNING
                continue
            if int(actual_scene_id) not in self._scene_jump_target_ids(tree, shape):
                self._record_unexpected_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    actual_scene_id,
                    source_scene_id=current_scene_id,
                    reason=f"重规划前未声明落点，目标 #{target_scene_id}",
                )
                last_failed_edge = edge
            self._log("detail", f"场景移动：实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.5)

        raise RuntimeError(f"场景移动超过最大重规划步数，未到达 #{target_scene_id}")


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








