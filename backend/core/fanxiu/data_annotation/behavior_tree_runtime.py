from __future__ import annotations

import base64
import difflib
import hashlib
import inspect
import io
import json
import linecache
import os
import random
import re
import shutil
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from pyxllib.prog import BehaviorTreeStatus, scheduled_task_payload_with_meta

from backend.core.fanxiu.behavior_tree.runtime import (
    data_annotation_asset_tree_path as _core_data_annotation_asset_tree_path,
    ensure_behavior_tree_runtime_jobs_registered,
    fanxiu_data_annotation_mail_scan_state_path as _core_data_annotation_mail_scan_state_path,
    fanxiu_behavior_tree_runtime_state_path as _core_behavior_tree_runtime_state_path,
    fanxiu_data_annotation_scheduler_settings_path as _core_data_annotation_scheduler_settings_path,
    fanxiu_data_annotation_scheduler_state_path as _core_data_annotation_scheduler_state_path,
    fanxiu_data_annotation_world_facts_path as _core_data_annotation_world_facts_path,
)
from backend.core.fanxiu.data_annotation.jobs import (
    canonical_fanxiu_data_annotation_task_type,
    get_fanxiu_data_annotation_task_cell_definition as _data_annotation_task_cell_definition,
)
from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_data_annotation_default_runtime_jobs
from backend.core.fanxiu.data_annotation.behavior_tree_container import BehaviorTreeRuntimeContainer as _BehaviorTreeRuntimeContainer
from backend.core.fanxiu.data_annotation.recognition_candidates import (
    default_recognition_candidate_ids,
    default_recognition_candidate_layers,
    layer3_recognition_candidate_ids,
    scene_asset_directory_path,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.recognition_graph import (
    SceneGraphCandidate,
    choose_scene_from_graph,
)
from backend.core.fanxiu.data_annotation.scene_navigation import (
    explicit_scene_jump_edges,
    posterior_landing_probabilities,
)
from backend.core.fanxiu.data_annotation.task_context import runtime_task_payload
from backend.core.fanxiu.data_annotation.navigation_incidents import (
    NAVIGATION_MAX_REPLAN_STEPS,
    NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT,
    NAVIGATION_STABLE_FRAME_SIMILARITY,
    NAVIGATION_STALL_MAX_SECONDS,
    NAVIGATION_STATE_EDGE_RETRY_LIMIT,
    NavigationIncidentRecorder,
)
from backend.core.fanxiu.data_annotation.shape_inheritance import (
    ShapeInheritanceResolution,
    find_raw_shape_for_effective,
    resolve_shape_inheritance,
)
from backend.core.fanxiu.data_annotation.unknown_recovery import build_unknown_evidence, _image_similarity_percent
from backend.core.fanxiu.data_annotation.popup_guard import (
    FanxiuEmulatorRestartRequired,
    SceneInterruptionMixin,
)
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    OcrTextMatch,
    find_fuzzy_text_matches,
    find_text_matches,
    group_ocr_tokens,
    locate_text_box,
    query_ocr_lines,
    query_spatial_ocr,
    select_text_match,
    select_fuzzy_text_match,
    union_fragment_box,
)
from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values
from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.data_annotation.slider_control import (
    BalancedPointState,
    DiscreteSliderScale,
    find_labeled_percentage,
)
from backend.core.fanxiu.data_annotation.trial_difficulty import (
    TRIAL_DIFFICULTY_AXES,
    ObservedTrialDifficulty,
    build_even_trial_difficulty_plan,
    find_current_trial_difficulty,
)
from backend.core.fanxiu.data_annotation.trial_purchase import (
    XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES,
    normalize_xianqiao_trial_purchase_target,
    purchases_completed_before_price,
)
from backend.core.fanxiu.data_annotation.trial_progression import (
    ObservedTrialAttempts,
    ObservedTrialHomeState,
    parse_xianqiao_trial_attempts,
)
from backend.core.fanxiu.data_annotation.state import (
    append_behavior_tree_runtime_status_log,
    initial_behavior_tree_runtime_status,
    normalize_data_annotation_scheduler_settings,
    normalize_behavior_tree_runtime_guard_items,
    parse_data_annotation_task_time,
    persist_behavior_tree_runtime_status as _persist_behavior_tree_runtime_status_core,
    read_data_annotation_json as _read_data_annotation_json,
    read_behavior_tree_runtime_status as _read_behavior_tree_runtime_status_core,
    record_data_annotation_scheduler_task_fact,
    write_data_annotation_json as _write_data_annotation_json,
)
from backend.core.fanxiu.data_annotation.storage import (
    update_data_annotation_asset_tree,
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
from backend.core.fanxiu.runtime.mumu_control import (
    ensure_mumu_device_healthy,
    record_mumu_adb_failure,
    screencap_mumu_adb_png,
    _encode_png_frame,
)
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.behavior_tree.errors import BehaviorTreeRuntimeError
from backend.core.fanxiu.info_window import publish_fanxiu_scene_recognition
from backend.core.temp_paths import codeyun_temp_root
from pyxllib.autogui import (
    ActionPlanner,
    SceneNavigator,
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
DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS = 60.0
DEFAULT_GO_SCENE_OBSERVATION_TIMEOUT_SECONDS = 60.0
DEFAULT_SCENE_RECOGNITION_POLL_SECONDS = 1.0
OFFLINE_CULTIVATION_SETTLE_WAIT_SECONDS = 120.0
UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION = 4
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
    name_similarity: float = 0.0

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


def repeated_template_item_box_from_anchor(
    template_box: Mapping[str, Any],
    anchor_template_box: Mapping[str, Any],
    resolved_anchor_box: Mapping[str, Any],
    *,
    load_direction: str,
) -> dict[str, float]:
    """按滚动方向和锚点中心位移解析一个重复模板实例，不使用像素魔法常量。"""
    item = {key: float(template_box.get(key) or 0) for key in ("x", "y", "w", "h")}
    anchor_center_x = float(anchor_template_box.get("x") or 0) + float(anchor_template_box.get("w") or 0) / 2
    anchor_center_y = float(anchor_template_box.get("y") or 0) + float(anchor_template_box.get("h") or 0) / 2
    resolved_center_x = float(resolved_anchor_box.get("x") or 0) + float(resolved_anchor_box.get("w") or 0) / 2
    resolved_center_y = float(resolved_anchor_box.get("y") or 0) + float(resolved_anchor_box.get("h") or 0) / 2
    direction = str(load_direction or "").strip().lower()
    if direction in {"up", "down"}:
        item["y"] += resolved_center_y - anchor_center_y
    elif direction in {"left", "right"}:
        item["x"] += resolved_center_x - anchor_center_x
    else:
        raise RuntimeError(f"重复模板容器缺少有效 loadDirection：{load_direction!r}")
    return item
# A drag finishing at the ADB/input layer does not mean the game list has
# stopped moving.  Keep a real post-release delay before the consecutive
# stability samples below; business code must use ``scroll_shape_content``
# instead of taking a screenshot immediately after ``drag_shape_content``.
DEFAULT_SCROLL_SETTLE_SECONDS = 1.5
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
    seconds = _parse_xianfu_visit_cd_seconds(text)
    if seconds is None or not 0 <= seconds <= 1800:
        return None
    return seconds


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
    total_seconds = hours * 3600 + minutes * 60 + seconds
    return total_seconds if total_seconds <= 1800 else None


def _parse_daily_boss_reward_remaining(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    if not normalized:
        return None
    match = re.search(r"剩余奖励次数[:：]?(.*)", normalized)
    if match is None:
        return None
    tail = match.group(1)
    fraction = parse_ocr_values(tail, expected_count=2, allow_extra_numbers=True)
    if fraction is not None:
        return fraction[0]
    single = parse_ocr_values(tail, expected_count=1)
    return single[0] if single is not None else None


def _parse_daily_boss_hp_percent(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    matches = [int(value) for value in re.findall(r"(\d{1,3})%", normalized)]
    valid = [value for value in matches if 0 <= value <= 100]
    return min(valid) if valid else None


def _parse_first_int(text: Any) -> int | None:
    normalized = _sanitize_ocr_text(text).translate(FULLWIDTH_DIGIT_TRANSLATION)
    values = parse_ocr_values(normalized)
    return values[0] if values is not None else None


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
    check: Callable[["BehaviorTreeRuntime", str], _FanxiuWaitResult]


@dataclass(frozen=True)
class _UnknownFallbackDecision:
    status: Literal["clicked", "exhausted", "unavailable"]
    attempt: int = 0
    point: tuple[float, float] | None = None


class BehaviorTreeRuntime(Runtime):
    """Fanxiu 行为树运行时上下文。

    业务层只感知 runtime；ctx、当前帧、资产树路径和底层点击/匹配实现都收敛在这里。
    """

    default_wait_click_timeout = 18.0
    default_wait_view_timeout = 20.0
    default_wait_condition_timeout = 12.0
    _business_view_claims_attr = "business_view_claims"

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
        if isinstance(asset_tree_path, Path) and asset_tree_path.is_file() and (
            not isinstance(ctx.get("asset_tree"), list) or not isinstance(ctx.get("images"), dict)
        ):
            tree = runner._load_asset_tree(asset_tree_path)
            ctx.setdefault("asset_tree", tree)
            indexed_images = runner._index_images(tree)
            images = ctx.get("images")
            if isinstance(images, dict):
                for scene_id, image in indexed_images.items():
                    images.setdefault(scene_id, image)
            else:
                ctx["images"] = indexed_images
            ctx.setdefault("asset_tree_path", asset_tree_path)
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

    @contextmanager
    def expect_views(self, *views: View | int | str | Sequence[View | int | str]):
        """Temporarily add declared business foreground views to unified Layer 0."""

        view_ids: list[int] = []

        def append_view(view: View | int | str | Sequence[View | int | str]) -> None:
            if isinstance(view, View):
                if view.id is None:
                    raise RuntimeError(f"业务场景声明缺少场景编号：{view.title}")
                view_ids.append(int(view.id))
            elif isinstance(view, Sequence) and not isinstance(view, (str, bytes, bytearray)):
                for item in view:
                    append_view(item)
            else:
                view_ids.append(int(str(view).lstrip("#")))

        for view in views:
            append_view(view)
        claim = tuple(dict.fromkeys(view_ids))
        if not claim:
            raise ValueError("业务场景声明不能为空")
        claims = self.attrs.get(self._business_view_claims_attr)
        if not isinstance(claims, list):
            claims = []
            self.attrs[self._business_view_claims_attr] = claims
        claims.append(claim)
        try:
            yield self
        finally:
            active_claims = self.attrs.get(self._business_view_claims_attr)
            if isinstance(active_claims, list):
                for index in range(len(active_claims) - 1, -1, -1):
                    if active_claims[index] is claim:
                        active_claims.pop(index)
                        break
                if not active_claims:
                    self.attrs.pop(self._business_view_claims_attr, None)

    def active_business_view_ids(self) -> tuple[int, ...]:
        claims = self.attrs.get(self._business_view_claims_attr)
        if not isinstance(claims, list):
            return ()
        return tuple(dict.fromkeys(
            int(view_id)
            for claim in claims
            if isinstance(claim, (tuple, list))
            for view_id in claim
        ))

    @property
    def payload(self) -> dict[str, Any]:
        payload = self.attrs.get("payload")
        return payload if isinstance(payload, dict) else {}

    def set_completion_message(self, message: str) -> None:
        self.attrs["completion_message"] = str(message or "").strip()

    def set_next_time(self, next_time: str | None) -> None:
        """Persist this Job's sole future trigger fact at the business decision point."""

        task_id = str(self.payload.get("__scheduler_task_id") or "").strip()
        if not task_id:
            raise RuntimeError("业务写入 next_time 时缺少 __scheduler_task_id")
        self.runner._persist_scheduler_task_next_time(task_id, next_time)

    def set_job_next_time(self, task_id: str, next_time: str | None) -> None:
        """Apply an authorized external fact directly to another Job trigger."""

        self.runner._persist_scheduler_task_next_time(task_id, next_time)

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
        handle_interruptions: bool = True,
        include_popup_candidates: bool = True,
    ) -> tuple[int | None, float, str]:
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        business_view_ids: list[int] | None = None
        if views is not None:
            business_view_ids = [view.id if isinstance(view, View) else int(view) for view in views]
            business_view_ids = [view_id for view_id in business_view_ids if view_id is not None]

        # Observation is a strict zero-input API even when ``update=True``.
        # Popup/disconnect recovery belongs to business waits and must be
        # requested explicitly; otherwise saving an after-frame could click a
        # guard candidate as a side effect of scene recognition.
        if not handle_interruptions:
            if business_view_ids is None:
                scene_id, score = self.runner._identify_scene_number(
                    self.ctx,
                    frame,
                )
            else:
                scene_id, score = self.runner._identify_scene_number(
                    self.ctx,
                    frame,
                    business_view_ids,
                )
            return scene_id, float(score or 0.0), frame

        popup_candidates = self.popup_candidates() if include_popup_candidates else []
        popup_by_scene_id = {
            int(scene_id): candidate
            for candidate in popup_candidates
            if isinstance(candidate, dict)
            and isinstance(candidate.get("image"), dict)
            and (scene_id := self.runner._image_number(candidate["image"])) is not None
        }
        popup_scene_ids = tuple(popup_by_scene_id)
        if business_view_ids is None:
            base_view_ids = []
        else:
            base_view_ids = list(dict.fromkeys([
                *(int(scene_id) for scene_id in business_view_ids),
                *self.active_business_view_ids(),
            ]))
        layer0_ids = list(dict.fromkeys([*base_view_ids, *popup_scene_ids]))
        handled_popup_ids: list[int] = []
        synthetic_popup_ids = [
            int(popup_id)
            for popup_id, candidate in popup_by_scene_id.items()
            if isinstance(candidate.get("image"), dict)
            and (
                not self.runner._scene_identity_shapes(candidate["image"])
                or all(
                    self.runner._shape_image_role(shape) == "off"
                    and not (
                        self.runner._shape_ocr_role(shape) == "required"
                        and str(shape.get("ocrText") or "").strip()
                    )
                    for shape in self.runner._scene_identity_shapes(candidate["image"])
                )
            )
        ]

        def matching_ocr_popup_ids(current_frame: str) -> list[int]:
            """Cheaply prefilter text-identified interruptions on one OCR pass."""

            if not popup_by_scene_id:
                return []
            images = self.ctx.get("images") if isinstance(self.ctx.get("images"), dict) else {}
            # Synthetic/unit candidates without identity metadata stay in the
            # combined graph so the generic graph contract remains testable.
            synthetic_ids: list[int] = []
            candidates: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
            for popup_id, candidate in popup_by_scene_id.items():
                image = candidate.get("image") if isinstance(candidate, dict) else None
                if not isinstance(image, dict):
                    continue
                identities = self.runner._scene_identity_shapes(image)
                if not identities:
                    synthetic_ids.append(int(popup_id))
                    continue
                required_ocr = [
                    shape
                    for shape in identities
                    if self.runner._shape_ocr_role(shape) == "required"
                    and str(shape.get("ocrText") or "").strip()
                ]
                if required_ocr:
                    candidates.append((int(popup_id), image, required_ocr))
            if not candidates:
                return synthetic_ids
            self.runner._shared_spatial_ocr_result(self.ctx, current_frame)
            matched = list(synthetic_ids)
            for popup_id, image, required_ocr in candidates:
                if all(
                    bool(
                        self.runner._shape_cached_frame_ocr_match(
                            self.ctx,
                            image,
                            shape,
                            current_frame,
                        ).get("matched")
                    )
                    for shape in required_ocr
                ):
                    matched.append(popup_id)
            return matched

        for interruption_index in range(9):
            if business_view_ids is None:
                scene_id, score = self.runner._identify_scene_number(
                    self.ctx,
                    frame,
                )
            else:
                if include_popup_candidates and synthetic_popup_ids:
                    # Test/dynamic candidates without identity metadata cannot
                    # be prefiltered safely; preserve the one-pass graph.
                    scene_id, score = self.runner._identify_scene_number(
                        self.ctx,
                        frame,
                        layer0_ids,
                    )
                else:
                    base_scene_id, base_score = self.runner._identify_scene_number(
                        self.ctx,
                        frame,
                        base_view_ids,
                    )
                    if not include_popup_candidates:
                        scene_id, score = base_scene_id, base_score
                    else:
                        # Most interruption nodes are irrelevant on a normal
                        # business frame.  Scoring all image-only popup references
                        # on every poll made Layer0 consume minutes of CPU.  One
                        # shared full-frame OCR pass selects the text-specific
                        # popup candidates first (including #530); those candidates
                        # still compete with business nodes in one graph.  Only a
                        # true business miss falls back to the full popup domain.
                        ocr_popup_ids = matching_ocr_popup_ids(frame)
                        if ocr_popup_ids:
                            scene_id, score = self.runner._identify_scene_number(
                                self.ctx,
                                frame,
                                list(dict.fromkeys([*base_view_ids, *ocr_popup_ids])),
                            )
                        elif base_scene_id is not None:
                            scene_id, score = base_scene_id, base_score
                        else:
                            scene_id, score = self.runner._identify_scene_number(
                                self.ctx,
                                frame,
                                layer0_ids,
                            )
            # #546 is the real login-page maintenance prompt.  It must enter
            # the maintenance domain before generic popup handling; its
            # confirm button is intentionally not clicked.
            if scene_id == 546:
                self.runner._raise_game_maintenance(
                    scene_id=546,
                    evidence={
                        "stage": "maintenance_scene",
                        "recognized_scene_id": 546,
                    },
                )
            # 弹窗候选与当前业务候选同属 Layer 0。若一个场景被业务明确
            # 列入候选（例如论道 #302 入座确认），它就是当前作业要消费
            # 的业务节点，必须直接返回；只有候选之外的弹窗才执行前置中断。
            if (
                scene_id is None
                or scene_id not in popup_by_scene_id
                or scene_id in base_view_ids
            ):
                return scene_id, float(score or 0.0), frame
            if interruption_index >= 8:
                sequence = " -> ".join(f"#{item}" if item >= 0 else "断线重连" for item in handled_popup_ids)
                raise RuntimeError(f"场景识别连续处理弹窗超过上限：{sequence or 'unknown'}")

            candidate = popup_by_scene_id[int(scene_id)]
            if not self.runner._handle_recognized_popup_candidate(
                self,
                candidate,
                score=float(score or 0.0),
            ):
                raise RuntimeError(f"场景识别命中弹窗 #{scene_id}，但该节点没有可执行的中断处理动作")
            handled_popup_ids.append(int(scene_id))
            self.clear_frame()
            if self.stop_event is not None:
                if self.stop_event.wait(0.5):
                    self.runner._raise_if_stopped(self.stop_event)
            else:
                time.sleep(0.5)
            frame = self.cur_frame(update=True)
        raise RuntimeError("场景识别弹窗处理循环异常退出")

    def observe_scene(
        self,
        views: Iterable[View | int] | None = None,
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ) -> tuple[int | None, float, str]:
        """Recognize one frame without disconnect or popup side effects.

        ``current_scene`` retains its historical business-wait semantics and
        may consume an interruption.  Evidence capture, diagnostics and
        safety checks must use this explicit observer instead.
        """

        return self.current_scene(
            views,
            frame_data_url=frame_data_url,
            update=update,
            handle_interruptions=False,
        )

    def ocr_text(self, frame_data_url: str | None = None, *, update: bool = False) -> str:
        """Read text only from annotated shapes of the graph-recognized scene."""

        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        return self.runner._ocr_text(self._ocr_fragments_in_recognized_scene(frame))

    def ocr_fragments(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
        """Return OCR lines intersecting annotated shapes of the recognized scene."""

        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        return self._ocr_fragments_in_recognized_scene(frame)

    def ocr_lines(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
        """Return authoritative Paddle detector/recognizer lines."""

        return self.ocr_fragments(frame_data_url, update=update)

    def ocr_tokens(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
        """Return OCR tokens intersecting annotated shapes of the recognized scene."""

        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        return self.runner._recognized_scene_ocr_tokens(self.ctx, frame)

    def full_frame_ocr_tokens(
        self,
        frame_data_url: str | None = None,
        *,
        update: bool = False,
    ) -> list[dict[str, Any]]:
        """Return cached full-frame OCR tokens with their authoritative boxes.

        This is for scene-bound controls that are not asset identity shapes.
        It reuses the frame's shared Paddle result rather than launching a
        second OCR pass; callers must still apply their own uniqueness and
        fail-closed business checks before clicking.
        """

        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
        result = self.runner._shared_spatial_ocr_result(
            self.ctx,
            frame,
            options={"return_word_box": True},
        )
        tokens = result.get("tokens")
        return tokens if isinstance(tokens, list) else []

    def _ocr_fragments_in_recognized_scene(self, frame_data_url: str) -> list[dict[str, Any]]:
        return self.runner._recognized_scene_ocr_fragments(self.ctx, frame_data_url)

    def clear_frame(self) -> None:
        self.frame_data_url = None
        self.runner._clear_tick_frame(self.ctx)

    def popup_candidates(self) -> list[dict[str, Any]]:
        if self.candidates is not None:
            return self.candidates
        tree = self.ctx.get("asset_tree")
        if isinstance(tree, list):
            self.candidates = self.runner._auto_close_guard_images(tree)
            return self.candidates
        if not isinstance(self.asset_tree_path, Path) or not self.asset_tree_path.is_file():
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
        candidate, score = self.runner._auto_close_popup_graph_match(
            self.ctx,
            self.popup_candidates(),
            self.cur_frame(),
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

    def _effective_shape_search_views(self, view: View) -> list[View]:
        # Shape inheritance is resolved before a View is constructed.  Physical
        # asset-tree nesting has no inheritance meaning of its own.
        return [view]

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
                return found
        raise RuntimeError(f"shape 选择器 [{'/'.join(parts)}] 未命中")

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
        target = self.resolve_shape_selector(view, shape)
        self.last_clicked_shape = target
        target_view = target.parent_view if isinstance(target.parent_view, View) and isinstance(target.parent_view.raw, dict) else view
        label = f"wait_click #{view.id or '?'} {self._shape_path(target)}"
        # ``wait_click`` is a deterministic business action: the caller has
        # already selected the source view and action.  Re-running whole-scene
        # recognition here is both redundant and unsafe because the generic
        # popup domain (for example #47) can outscore the intended business
        # state.  Only constraints declared by the target Shape are allowed to
        # delay the click; an unconstrained Shape is a fixed business action.
        self.runner._log(
            "detail",
            f"{label}：点击前仅检查目标 Shape，不重新识别整帧场景",
        )
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
            click_options: dict[str, float] = {}
            if x_ratio != 0.5 or y_ratio != 0.5:
                click_options.update(x_ratio=x_ratio, y_ratio=y_ratio)
            self.runner._click_shape(
                self.ctx,
                target_view.raw,
                target.raw,
                frame_data_url,
                match_result=match_result,
                **click_options,
            )
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

    def shape_matches(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        *,
        frame_data_url: str | None = None,
    ) -> dict[str, Any] | None:
        """Read one fresh frame against a Shape's declared visual conditions.

        This is intentionally a no-click primitive.  It lets a Job distinguish
        a stateful visual gate (for example a bright, unclaimed reward mask)
        from a fixed-coordinate action before consuming it.  Unconstrained
        shapes cannot be used as state evidence.
        """

        view = self.resolve_view_selector(frame)
        if view is None:
            if frame is not None:
                raise RuntimeError(f"无法解析帧选择器：{frame}")
            current = self._current_view_from_frame()
            if not isinstance(current, View):
                raise RuntimeError("frame=None 时无法从当前上下文解析 view")
            view = current
        target = self.resolve_shape_selector(view, shape)
        if not self.runner._shape_has_runtime_click_condition(target.raw):
            raise RuntimeError(
                f"Shape「{self._shape_path(target)}」没有图像或 OCR 条件，不能作为视觉状态门卫"
            )
        target_view = target.parent_view if isinstance(target.parent_view, View) and isinstance(target.parent_view.raw, dict) else view
        match_shape = self._shape_match_search_shape(target)
        captured_frame = (
            frame_data_url
            if isinstance(frame_data_url, str) and frame_data_url
            else self.cur_frame(update=True)
        )
        for condition in self.runner._shape_match_conditions(match_shape):
            result = self.runner._match_shape(
                self.ctx,
                target_view.raw,
                match_shape,
                captured_frame,
                condition=condition,
            )
            if bool(result.get("matched")):
                return result
        return None

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
                scene_id, score, _frame = self.current_scene(
                    [source_view],
                    update=True,
                    handle_interruptions=True,
                )
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
        retry_if_source_remains: bool = True,
        max_clicks: int = 2,
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
        tree = self.ctx.get("asset_tree")
        declared_target_ids = self.runner._scene_jump_target_ids(
            tree if isinstance(tree, list) else [],
            target_shape.raw,
        )

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
            target_ids = list(declared_target_ids)
        if not target_ids and not wait_leave:
            raise RuntimeError(f"点击 #{source_view.id or '?'}「{self._shape_path(target_shape)}」后缺少目标场景；请显式传入目标或补 sceneJumpTarget")
        wait_label = label or (
            f"点击后等待离开 #{source_view.id or '?'}"
            if wait_leave and not target_ids
            else f"点击后等待目标场景 {','.join(f'#{target_id}' for target_id in target_ids)}"
        )
        wait_timeout = self.default_wait_condition_timeout if timeout is None else float(timeout)
        click_count = max(1, int(max_clicks or 1))
        last_error: TimeoutError | None = None
        attempts_made = 0
        claim_scope = self.expect_views(*target_ids) if target_ids else nullcontext(self)
        with claim_scope:
            try:
                for attempt in range(1, click_count + 1):
                    attempts_made = attempt
                    yield from self.wait_click(source_view, target_shape, **options)
                    yield from self.wait_action_settle(settle_seconds)
                    try:
                        if not target_ids and wait_leave:
                            return (yield from self.wait_leave_view(
                                source_view,
                                timeout=wait_timeout,
                                label=label or f"点击后等待离开 #{source_view.id or '?'}",
                            ))
                        target_view = yield from self.wait_view(
                            *target_ids,
                            timeout=wait_timeout,
                            label=wait_label,
                        )
                        self._record_wait_click_then_view_landing(source_view, target_shape, target_view)
                        self.last_clicked_shape = None
                        return target_view
                    except TimeoutError as exc:
                        last_error = exc
                        if (
                            not retry_if_source_remains
                            or not declared_target_ids
                            or attempt >= click_count
                            or source_view.id is None
                        ):
                            break
                        scene_id, score, _frame = self.current_scene(
                            update=True,
                            handle_interruptions=True,
                        )
                        if scene_id != source_view.id:
                            break
                        self.runner._log(
                            "warning",
                            (
                                f"{wait_label}：点击后仍在源场景 #{source_view.id} {score:.0f}%，"
                                f"重试点击 {attempt + 1}/{click_count}"
                            ),
                        )

                target_text = ",".join(f"#{target_id}" for target_id in target_ids) or "离开源场景"
                jump_target = str(target_shape.raw.get("sceneJumpTarget") or "").strip() or "未声明"
                raise TimeoutError(
                    f"{wait_label} 失败：源场景=#{source_view.id or '?'}，shape={self._shape_path(target_shape)}，"
                    f"期望目标={target_text}，sceneJumpTarget={jump_target}，已点击 {attempts_made} 次；{last_error or ''}"
                ) from last_error
            finally:
                self.last_clicked_shape = None

    def open_sdk_bubble_menu(
        self,
        *,
        timeout: float = 12.0,
        settle_seconds: float = 1.0,
        safe_center: tuple[float, float] | None = None,
    ) -> View:
        """Move the SDK bubble to the proven left dock, click once, and require #590.

        The 37 SDK overlay does not always consume a tap when it is docked on a
        native game button.  Repeating that tap can therefore activate the game
        underneath.  The safe transaction is: uniquely locate the live bubble;
        only when it overlaps one of the two observed native hot zones, drag it
        vertically along the same edge and confirm the new position on a fresh
        frame; then click exactly once and require the SDK menu.
        """
        attempt_timeout = max(2.0, min(8.0, float(timeout)))
        try:
            reward_overlay = self.shape_matches(421, "奖励浮层")
        except RuntimeError:
            reward_overlay = None
        if reward_overlay is not None:
            self.runner._log("detail", "气泡入口：检测到世界页奖励浮层，先关闭遮挡")
            yield from self.wait_click(421, "关闭", timeout=attempt_timeout)
            yield from self.wait_action_settle(settle_seconds)

        match = self.shape_matches(421, "气泡")
        resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
        if not isinstance(resolved, dict) or not bool((match or {}).get("unique_match", True)):
            raise RuntimeError("气泡入口：未唯一定位完整悬浮球，拒绝拖拽或点击")
        start_x = float(resolved.get("x") or 0) + float(resolved.get("w") or 0) / 2
        start_y = float(resolved.get("y") or 0) + float(resolved.get("h") or 0) / 2
        # Every observed left-edge dock can overlap the vertically stacked
        # native activity rail.  The old 300..500 range missed the real
        # (46, 878) overlap and repeatedly clicked through to the game icon.
        # Normalize any left-edge position to the proven SDK dock first.
        on_left_edge = start_x < 100.0
        on_right_edge = start_x > 800.0
        if safe_center is not None:
            target_x, target_y = (float(safe_center[0]), float(safe_center[1]))
        elif on_left_edge:
            target_x, target_y = (45.0, 680.0)
        elif on_right_edge:
            # 右侧任意停靠位都先移到已真实验证的左侧空白位。
            # 右侧直接点击可能穿透至底层折叠按钮；沿右侧下移
            # 到 y=900 也已实测不能稳定打开 #590。
            target_x, target_y = (45.0, 680.0)
        else:
            target_x, target_y = (start_x, start_y)
        if abs(start_x - target_x) > 55.0 or abs(start_y - target_y) > 55.0:
            self.runner._log(
                "action",
                f"气泡入口：从 ({start_x:.0f},{start_y:.0f}) 拖到安全空白区 ({target_x:.0f},{target_y:.0f})",
            )
            self.drag_frame_point(
                421,
                start_x,
                start_y,
                target_x,
                target_y,
                duration_ms=650,
            )
            yield from self.wait_action_settle(settle_seconds)
            match = self.shape_matches(421, "气泡")
            resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
            if not isinstance(resolved, dict) or not bool((match or {}).get("unique_match", True)):
                raise RuntimeError("气泡入口：拖拽后未唯一重定位完整悬浮球，拒绝点击")
            start_x = float(resolved.get("x") or 0) + float(resolved.get("w") or 0) / 2
            start_y = float(resolved.get("y") or 0) + float(resolved.get("h") or 0) / 2
            if abs(start_x - target_x) > 80.0 or abs(start_y - target_y) > 80.0:
                raise RuntimeError(
                    f"气泡入口：拖拽落点未到安全区，实际=({start_x:.0f},{start_y:.0f})，拒绝点击"
                )

        self.runner._log("action", f"气泡入口：安全区悬浮球只点击一次 ({start_x:.0f},{start_y:.0f})")
        self.click_frame_point(421, start_x, start_y)
        yield from self.wait_action_settle(settle_seconds)
        try:
            return (yield from self.wait_view(
                590,
                timeout=attempt_timeout,
                label="安全区点击气泡后等待37手游弹窗#590",
            ))
        except TimeoutError as exc:
            matched, _score, _frame = self.match_view(590, update=True)
            if matched:
                return self.view(590)
            raise TimeoutError("安全区悬浮球单击后未打开 #590；禁止原地重试") from exc

    def _record_wait_click_then_view_landing(self, source_view: View, shape: Shape, target_view: View) -> None:
        if target_view.id is None:
            return
        asset_tree_path = self.ctx.get("asset_tree_path")
        tree = self.ctx.get("asset_tree")
        if not isinstance(asset_tree_path, Path) or not isinstance(tree, list):
            return
        self.runner._record_scene_jump_landing(
            self.ctx,
            asset_tree_path,
            tree,
            shape.raw,
            int(target_view.id),
            reason=f"#{source_view.id or '?'} 点击后等待目标场景",
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
            scene_id, score, _frame = self.current_scene(
                update=True,
                handle_interruptions=True,
            )
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
            return self.current_scene(update=True, handle_interruptions=True)
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
        popup_guard_delay = min(preferred_wait_seconds / 2.0, 5.0)
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
        first_layer0_poll = True
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
            preferred_ids = view_ids if in_layer0_window or first_layer0_poll else None
            first_layer0_poll = False
            scene_id, score, frame = self.current_scene(
                preferred_ids,
                frame_data_url=frame,
                handle_interruptions=True,
                include_popup_candidates=elapsed >= popup_guard_delay,
            )
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
                self._record_pending_declared_click_landing(int(scene_id))
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
                        # A business wait timeout must stay bounded. Full-tree
                        # unknown exploration can compare hundreds of stored
                        # frames and previously turned a 12-second wait into a
                        # multi-minute, multi-gigabyte diagnostic. The normal
                        # scene recognizer has already run its layered graph;
                        # timeout evidence only needs the requested nodes and
                        # the last recognized node.
                        candidate_scene_ids=list(dict.fromkeys([
                            *view_ids,
                            *([last_scene_id] if last_scene_id is not None else []),
                        ])),
                    )
                    report_suffix = f"，证据={evidence.report_path}" if evidence.report_path else ""
                    frame_suffix = f"，截图={evidence.frame_path}" if evidence.frame_path else ""
                    diagnostic = f"；unknown诊断={evidence.classification}：{evidence.suggestion}{frame_suffix}{report_suffix}"
                    self.runner._log("warning", f"{label}：{diagnostic.lstrip('；')}")
                except Exception as exc:
                    self.runner._log("warning", f"{label}：unknown诊断生成失败：{exc}")
                raise TimeoutError(f"{label} 超时，未检测到 {expected}，最后 {scene_text} {last_score:.0f}%{diagnostic}")
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

    def _record_pending_declared_click_landing(self, target_scene_id: int) -> None:
        if self.active_business_view_ids():
            return
        clicked_shape = self.last_clicked_shape
        self.last_clicked_shape = None
        if not isinstance(clicked_shape, Shape):
            return
        tree = self.ctx.get("asset_tree")
        declared_ids = self.runner._scene_jump_target_ids(
            tree if isinstance(tree, list) else [],
            clicked_shape.raw,
        )
        if not declared_ids:
            return
        source_view = clicked_shape.parent_view
        if not isinstance(source_view, View):
            return
        self._record_wait_click_then_view_landing(source_view, clicked_shape, self.view(int(target_scene_id)))

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

        def check(runtime: "BehaviorTreeRuntime", frame: str) -> _FanxiuWaitResult:
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

        def check(runtime: "BehaviorTreeRuntime", frame: str) -> _FanxiuWaitResult:
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

        def check(runtime: "BehaviorTreeRuntime", frame: str) -> _FanxiuWaitResult:
            text = runtime.ocr_text(frame)
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
        def check(runtime: "BehaviorTreeRuntime", frame: str) -> _FanxiuWaitResult:
            text = runtime.ocr_text(frame)
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

        def check(runtime: "BehaviorTreeRuntime", frame: str) -> _FanxiuWaitResult:
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
            _scene_id, _score, frame = self.current_scene([], frame_data_url=frame)
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

    def click_frame_point_fast(self, view: View | int | str | dict[str, Any], x: float, y: float) -> Any:
        """Click a latency-critical fixed point without pre-click evidence capture.

        Timed answer flows already retain their current frame in memory.  Writing
        an additional before-click screenshot can consume most of the answer
        window, so this explicit primitive skips only the action trace artifact.
        """

        target_view = self.view(view)
        result = self.runner._click_frame_point(
            self.ctx,
            target_view.raw,
            x,
            y,
            save_action_trace=False,
        )
        self.clear_frame()
        return result

    def click_shape_center_fast(
        self,
        view: View | int | str,
        shape: Shape | str,
        *,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> Any:
        """Click an annotated fixed point through the latency-critical path.

        The caller must already own the current business state.  This keeps
        coordinates sourced from the formal asset tree while deliberately
        skipping fresh image/OCR matching and the before-click trace capture.
        """

        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        source_view = (
            target_shape.parent_view
            if isinstance(target_shape.parent_view, View)
            and isinstance(target_shape.parent_view.raw, dict)
            else target_view
        )
        width, height = self.runner._frame_size(source_view.raw)
        click_x = (
            float(target_shape.raw.get("x") or 0)
            + float(target_shape.raw.get("w") or 0) * float(x_ratio)
        ) * width
        click_y = (
            float(target_shape.raw.get("y") or 0)
            + float(target_shape.raw.get("h") or 0) * float(y_ratio)
        ) * height
        return self.click_frame_point_fast(source_view, click_x, click_y)

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

    def long_press_frame_point(
        self,
        view: View | int | str | dict[str, Any],
        x: float,
        y: float,
        *,
        duration: float = 1.2,
    ) -> Any:
        """Long-press an OCR-derived point in frame coordinates."""

        target_view = self.view(view)
        duration_ms = max(50, min(3000, int(float(duration) * 1000)))
        self._emit_runtime_action(
            (
                f"固定长按 #{target_view.id or '?'} "
                f"({float(x):.0f},{float(y):.0f}) {duration_ms / 1000:.1f}s"
            ),
            phase="runtime_long_press_point",
            kind="long_press",
            current_scene=target_view.id,
        )
        result = self.runner._drag_frame_point(
            self.ctx,
            target_view.raw,
            float(x),
            float(y),
            float(x),
            float(y),
            duration_ms=duration_ms,
        )
        self.clear_frame()
        return result

    def drag_frame_point(
        self,
        view: View | int | str | dict[str, Any],
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        *,
        duration_ms: int = 300,
    ) -> Any:
        """Drag between two stable frame coordinates through the normal backend."""

        target_view = self.view(view)
        result = self.runner._drag_frame_point(
            self.ctx,
            target_view.raw,
            start_x,
            start_y,
            end_x,
            end_y,
            duration_ms=max(50, int(duration_ms)),
        )
        self.clear_frame()
        return result

    def shape_center(
        self,
        view: View | int | str | dict[str, Any],
        shape: Shape | str | dict[str, Any],
        *,
        live: bool = False,
        strict_live: bool = False,
    ) -> tuple[float, float]:
        """Resolve a shape center, optionally requiring a unique live match."""

        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        frame = self.cur_frame(update=True) if live else None
        return self.runner._shape_center(
            target_shape.raw,
            target_view.raw,
            frame,
            self.ctx if live else None,
            strict_live=bool(strict_live),
        )

    def shape_box(
        self,
        view: View | int | str | dict[str, Any],
        shape: Shape | str | dict[str, Any],
    ) -> dict[str, Any]:
        """Return the fixed reference box for geometry-only calculations."""

        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        return self.runner._box(target_shape.raw, target_view.raw)

    def match_view(
        self,
        view: View | int | str | dict[str, Any],
        *,
        frame_data_url: str | None = None,
        update: bool = False,
    ) -> tuple[bool, float, str]:
        """Score one view directly, without competition from nearby scenes."""

        target_view = self.view(view)
        if target_view.id is None:
            raise RuntimeError(f"场景缺少编号：{target_view.title}")
        frame = (
            frame_data_url
            if isinstance(frame_data_url, str) and frame_data_url
            else self.cur_frame(update=update)
        )
        score = float(self.runner._scene_score(self.ctx, target_view.raw, frame) or 0.0)
        return self.runner._scene_matches_id(int(target_view.id), score), score, frame

    def advance_dialogue(
        self,
        view: View | int | str,
        shape: Shape | str,
        *,
        quiet_seconds: float = 5.0,
        poll_seconds: float = 0.5,
        initial_timeout: float = 20.0,
        max_clicks: int = 12,
        label: str = "推进连续对话",
    ):
        """连续点击同一对话入口，直到自身场景连续一段时间不再出现。

        对话点击后常有短暂过渡帧，不能把一次 ``unknown`` 当成结束。每次点击
        都重新开启静默观察窗口；窗口内再次识别到自身场景时立即推进下一句，
        只有连续 ``quiet_seconds`` 未再次识别到自身场景才返回。
        """
        source_view = self.view(view)
        source_id = source_view.id
        if source_id is None:
            raise RuntimeError(f"{label}：对话场景缺少编号")
        quiet_seconds = float(quiet_seconds)
        poll_seconds = float(poll_seconds)
        initial_timeout = float(initial_timeout)
        max_clicks = int(max_clicks)
        if quiet_seconds <= 0:
            raise ValueError("quiet_seconds 必须大于 0")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds 必须大于 0")
        if max_clicks <= 0:
            raise ValueError("max_clicks 必须大于 0")

        scene_id, _score, _frame = self.current_scene([source_id], update=True)
        if scene_id != source_id:
            yield from self.wait_scene(
                source_view,
                timeout=initial_timeout,
                label=f"{label}：等待自身场景 #{source_id}",
            )

        click_count = 0
        while click_count < max_clicks:
            self.click_shape_center(source_view, shape)
            click_count += 1
            quiet_deadline = time.monotonic() + quiet_seconds
            source_reappeared = False
            while True:
                remaining_seconds = quiet_deadline - time.monotonic()
                if remaining_seconds <= 0:
                    break
                sample_seconds = min(poll_seconds, remaining_seconds)
                yield from self.wait_action_settle(sample_seconds)
                scene_id, _score, _frame = self.current_scene([source_id], update=True)
                if scene_id == source_id:
                    source_reappeared = True
                    break
            if not source_reappeared:
                self.runner._log(
                    "success",
                    f"{label}：#{source_id} 连续 {quiet_seconds:g} 秒未再出现，共推进 {click_count} 次",
                )
                return click_count

        raise RuntimeError(f"{label}：连续推进 {max_clicks} 次后 #{source_id} 仍会再次出现")

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
        target_shape = self.resolve_shape_selector(target_view, shape_title)
        source_view = target_shape.parent_view if isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict) else target_view
        lines = self.ocr_fragments_in_shapes(
            source_view,
            (str(target_shape.raw.get("title") or target_shape.raw.get("id") or shape_title),),
            padding=0,
            frame_data_url=frame,
        )
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
        cached = self.runner._shared_spatial_ocr_result(self.ctx, frame)
        tokens = cached.get("tokens") if isinstance(cached.get("tokens"), list) else []
        fragments = cached.get("lines") if isinstance(cached.get("lines"), list) else []
        target_shape = self.resolve_shape_selector(target_view, shape_title)
        source_view = target_shape.parent_view if isinstance(target_shape.parent_view, View) and isinstance(target_shape.parent_view.raw, dict) else target_view
        return self.runner._ocr_centers_in_shape(
            fragments,
            source_view.raw,
            shape_title,
            include=include,
            exclude=exclude,
            tokens=tokens,
        )

    def ocr_fragments_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
        crop: bool = False,
    ) -> list[dict[str, Any]]:
        target_view = View(view) if isinstance(view, dict) else self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        return self.runner._ocr_fragments_in_shapes(
            frame,
            target_view.raw,
            tuple(shape_titles),
            padding=padding,
            options=options,
            ctx=None if crop else self.ctx,
        )

    def ocr_lines_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
        crop: bool = False,
    ) -> list[dict[str, Any]]:
        """Return native Paddle lines intersecting the requested ROI."""

        return self.ocr_fragments_in_shapes(
            view,
            shape_titles,
            padding=padding,
            frame_data_url=frame_data_url,
            options=options,
            crop=crop,
        )

    def ocr_tokens_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
        crop: bool = False,
    ) -> list[dict[str, Any]]:
        """Return OCR word tokens in the requested shapes.

        The default path reuses shared full-frame OCR. ``crop=True`` performs
        OCR on the cropped region itself as a targeted fallback for small or
        stylized text that full-frame OCR may omit.
        """

        target_view = View(view) if isinstance(view, dict) else self.view(view)
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        ocr_options = {"return_word_box": True, **dict(options or {})}
        return self.runner._ocr_tokens_in_shapes(
            frame,
            target_view.raw,
            tuple(shape_titles),
            padding=padding,
            options=ocr_options,
            ctx=None if crop else self.ctx,
        )

    def find_ocr_text(
        self,
        view: View | int | str | dict[str, Any],
        target: str,
        *,
        in_shapes: Iterable[str] | None = None,
        occurrence: int | None = None,
        padding: int = 0,
        frame_data_url: str | None = None,
        match_mode: Literal["exact", "fuzzy"] = "exact",
        min_similarity: float = 70.0,
        ambiguity_margin: float = 5.0,
        crop: bool = False,
        max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    ) -> OcrTextMatch | None:
        """Locate OCR text and retain the real tokens as click evidence."""

        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        tokens = (
            self.ocr_tokens_in_shapes(
                view,
                in_shapes,
                padding=padding,
                frame_data_url=frame,
                crop=crop,
            )
            if in_shapes is not None
            else self.ocr_tokens(frame)
        )
        exact_matches = find_text_matches(
            tokens,
            target,
            max_gap_height_ratio=max_gap_height_ratio,
        )
        if exact_matches or match_mode == "exact":
            return select_text_match(exact_matches, target, occurrence=occurrence)
        if match_mode != "fuzzy":
            raise ValueError(f"OCR 文本匹配模式无效：{match_mode!r}")
        return select_fuzzy_text_match(
            find_fuzzy_text_matches(
                tokens,
                target,
                min_score=min_similarity,
                max_gap_height_ratio=max_gap_height_ratio,
            ),
            target,
            occurrence=occurrence,
            ambiguity_margin=ambiguity_margin,
        )

    def wait_ocr_text(
        self,
        view: View | int | str | dict[str, Any],
        target: str,
        *,
        in_shapes: Iterable[str],
        occurrence: int | None = None,
        padding: int = 0,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 1.0,
        max_scrolls_per_direction: int = 30,
        search_direction: Literal["up", "down", "left", "right"] | None = None,
        match_mode: Literal["exact", "fuzzy"] = "exact",
        min_similarity: float = 70.0,
        ambiguity_margin: float = 5.0,
        crop_fallback: bool = False,
        frame_data_url: str | None = None,
        max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    ):
        return (yield from self.wait_ocr_any_text(
            view,
            (target,),
            in_shapes=in_shapes,
            occurrence=occurrence,
            padding=padding,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            max_scrolls_per_direction=max_scrolls_per_direction,
            search_direction=search_direction,
            match_mode=match_mode,
            min_similarity=min_similarity,
            ambiguity_margin=ambiguity_margin,
            crop_fallback=crop_fallback,
            frame_data_url=frame_data_url,
            max_gap_height_ratio=max_gap_height_ratio,
        ))

    def wait_ocr_any_text(
        self,
        view: View | int | str | dict[str, Any],
        targets: Iterable[str],
        *,
        in_shapes: Iterable[str],
        occurrence: int | None = None,
        padding: int = 0,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 1.0,
        max_scrolls_per_direction: int = 30,
        direction_cycles: int = 1,
        cycle_pause_seconds: float = 0.0,
        search_direction: Literal["up", "down", "left", "right"] | None = None,
        match_mode: Literal["exact", "fuzzy"] = "exact",
        min_similarity: float = 70.0,
        ambiguity_margin: float = 5.0,
        crop_fallback: bool = False,
        frame_data_url: str | None = None,
        max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    ):
        """等待 OCR 区域出现文本；区域可加载时自动有界遍历内容。

        每帧按顺序检查 ``targets``，任一文本命中即返回真实 OCR 框。
        业务可声明多轮主方向/反方向往返，以覆盖短暂遮挡；默认加载方向
        来自 Shape 标注，明确的反向业务（例如升级）可用 ``search_direction``
        覆盖首轮方向。
        """

        normalized_targets = tuple(dict.fromkeys(
            str(item or "").strip() for item in targets if str(item or "").strip()
        ))
        if not normalized_targets:
            raise ValueError("OCR 等待目标不能为空")
        target_view = View(view) if isinstance(view, dict) else self.view(view)
        shape_titles = tuple(in_shapes)
        shapes = [
            self.resolve_shape_selector(target_view, title)
            for title in shape_titles
        ]
        loadable_shapes = [
            shape
            for shape in shapes
            if str(shape.load_direction or "").strip().lower()
            in {"up", "down", "left", "right"}
        ]
        if len(loadable_shapes) > 1:
            titles = "、".join(shape.title for shape in loadable_shapes)
            raise RuntimeError(f"OCR 查找区域包含多个可加载 Shape：{titles}")

        deadline = time.monotonic() + max(0.0, float(timeout_seconds or 0.0))
        loadable_shape = loadable_shapes[0] if loadable_shapes else None
        declared_direction = (
            str(loadable_shape.load_direction or "").strip().lower()
            if loadable_shape is not None
            else ""
        )
        primary_direction = str(search_direction or declared_direction).strip().lower()
        if search_direction is not None and loadable_shape is None:
            raise RuntimeError("OCR 搜索指定了方向，但查找区域没有可加载 Shape")
        if primary_direction and primary_direction not in {"up", "down", "left", "right"}:
            raise ValueError(f"OCR 搜索方向无效：{primary_direction!r}")
        opposite_direction = {
            "up": "down",
            "down": "up",
            "left": "right",
            "right": "left",
        }.get(primary_direction, "")
        directions = (
            (primary_direction, opposite_direction)
            if primary_direction and opposite_direction
            else ("",)
        )
        scroll_limit = max(0, int(max_scrolls_per_direction or 0))
        cycle_limit = max(1, int(direction_cycles or 1))

        def find_in_frame(frame: str) -> OcrTextMatch | None:
            for target in normalized_targets:
                match = self.find_ocr_text(
                    target_view,
                    target,
                    in_shapes=shape_titles,
                    occurrence=occurrence,
                    padding=padding,
                    frame_data_url=frame,
                    match_mode=match_mode,
                    min_similarity=min_similarity,
                    ambiguity_margin=ambiguity_margin,
                    crop=False,
                    max_gap_height_ratio=max_gap_height_ratio,
                )
                if match is not None:
                    return match
                if crop_fallback:
                    match = self.find_ocr_text(
                        target_view,
                        target,
                        in_shapes=shape_titles,
                        occurrence=occurrence,
                        padding=padding,
                        frame_data_url=frame,
                        match_mode=match_mode,
                        min_similarity=min_similarity,
                        ambiguity_margin=ambiguity_margin,
                        crop=True,
                        max_gap_height_ratio=max_gap_height_ratio,
                    )
                    if match is not None:
                        return match
            return None

        initial_frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else None
        if initial_frame:
            match = find_in_frame(initial_frame)
            if match is not None or time.monotonic() >= deadline:
                return match

        if loadable_shape is None:
            while True:
                frame = self.cur_frame(update=True)
                match = find_in_frame(frame)
                if match is not None or time.monotonic() >= deadline:
                    return match
                yield from self.wait_action_settle(max(0.0, float(poll_seconds or 0.0)))

        for cycle_index in range(cycle_limit):
            for direction in directions:
                for scroll_index in range(scroll_limit + 1):
                    frame = self.cur_frame(update=True)
                    match = find_in_frame(frame)
                    if match is not None:
                        return match
                    if time.monotonic() >= deadline:
                        return None
                    if loadable_shape is None or scroll_index >= scroll_limit:
                        break
                    changed = yield from self.scroll_shape_content(
                        loadable_shape,
                        direction=direction,
                    )
                    if not changed:
                        break
            if cycle_index + 1 < cycle_limit and time.monotonic() < deadline:
                yield from self.wait_action_settle(
                    max(0.0, float(cycle_pause_seconds or 0.0))
                )
        return None

    def wait_click_ocr_text(
        self,
        view: View | int | str | dict[str, Any],
        target: str,
        *,
        in_shapes: Iterable[str],
        occurrence: int | None = None,
        anchor: Literal["center", "top_left", "top_center", "bottom_center"] = "center",
        offset: tuple[float, float] = (0.0, 0.0),
        offset_unit: Literal["pixel", "height"] = "pixel",
        padding: int = 0,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 1.0,
        max_scrolls_per_direction: int = 30,
        search_direction: Literal["up", "down", "left", "right"] | None = None,
        match_mode: Literal["exact", "fuzzy"] = "exact",
        min_similarity: float = 70.0,
        ambiguity_margin: float = 5.0,
        crop_fallback: bool = False,
        frame_data_url: str | None = None,
        max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    ):
        """Find an OCR target in a scrollable shape and click a related point."""

        match_options = {
            "in_shapes": in_shapes,
            "occurrence": occurrence,
            "padding": padding,
            "timeout_seconds": timeout_seconds,
            "poll_seconds": poll_seconds,
            "max_scrolls_per_direction": max_scrolls_per_direction,
            "search_direction": search_direction,
            "max_gap_height_ratio": max_gap_height_ratio,
        }
        if frame_data_url:
            match_options["frame_data_url"] = frame_data_url
        if match_mode != "exact" or crop_fallback:
            match_options.update({
                "match_mode": match_mode,
                "min_similarity": min_similarity,
                "ambiguity_margin": ambiguity_margin,
                "crop_fallback": crop_fallback,
            })
        match = yield from self.wait_ocr_text(view, target, **match_options)
        if match is None:
            raise TimeoutError(f"OCR 可加载区域内未找到文本「{target}」")
        x, y = match.point(
            anchor=anchor,
            offset=offset,
            offset_unit=offset_unit,
        )
        self.click_frame_point(view, x, y)
        return match

    def click_ocr_text(
        self,
        view: View | int | str | dict[str, Any],
        target: str,
        *,
        in_shapes: Iterable[str] | None = None,
        occurrence: int | None = None,
        anchor: Literal["center", "top_left", "top_center", "bottom_center"] = "center",
        offset: tuple[float, float] = (0.0, 0.0),
        offset_unit: Literal["pixel", "height"] = "pixel",
        padding: int = 0,
        frame_data_url: str | None = None,
        match_mode: Literal["exact", "fuzzy"] = "exact",
        min_similarity: float = 70.0,
        ambiguity_margin: float = 5.0,
        crop: bool = False,
        max_gap_height_ratio: float = DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    ) -> OcrTextMatch:
        """Locate and click an OCR target using an explicit anchor and offset."""

        shape_titles = tuple(in_shapes) if in_shapes is not None else None
        match = self.find_ocr_text(
            view,
            target,
            in_shapes=shape_titles,
            occurrence=occurrence,
            padding=padding,
            frame_data_url=frame_data_url,
            match_mode=match_mode,
            min_similarity=min_similarity,
            ambiguity_margin=ambiguity_margin,
            crop=crop,
            max_gap_height_ratio=max_gap_height_ratio,
        )
        if match is None:
            scope = f"shape {shape_titles}" if shape_titles is not None else "当前画面"
            raise RuntimeError(f"{scope} 未识别到可精确定位的 OCR 文本「{target}」")
        x, y = match.point(anchor=anchor, offset=offset, offset_unit=offset_unit)
        self.click_frame_point(view, x, y)
        return match

    def ocr_text_in_shapes(
        self,
        view: View | int | str | dict[str, Any],
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        options: dict[str, Any] | None = None,
        crop: bool = False,
    ) -> str:
        lines = self.ocr_fragments_in_shapes(
            view,
            shape_titles,
            padding=padding,
            frame_data_url=frame_data_url,
            options=options,
            crop=crop,
        )
        return self.runner._ocr_text(lines)

    def ocr_numbers_in_shapes(
        self,
        view: View | int | str,
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        crop: bool = False,
    ) -> tuple[list[int], str]:
        text = self.ocr_text_in_shapes(
            view,
            shape_titles,
            padding=padding,
            frame_data_url=frame_data_url,
            crop=crop,
        )
        normalized = str(text or "").translate(FULLWIDTH_DIGIT_TRANSLATION)
        values = parse_ocr_values(normalized) or ()
        return list(values), normalized

    def set_slider_value(
        self,
        view: View | int | str,
        label: str,
        target: int,
        *,
        track: Shape | str,
        anchor: Shape | str | None = None,
        minimum: int,
        maximum: int,
        step: int,
        max_attempts: int = 3,
        duration: float = 1.0,
        settle_seconds: float = 0.8,
    ):
        """Set a discrete slider and close the loop with its OCR percentage."""

        scale = DiscreteSliderScale(minimum=int(minimum), maximum=int(maximum), step=int(step))
        target = int(target)
        scale.index(target)
        attempts = max(1, int(max_attempts))
        target_view = self.view(view)
        track_shape = self.resolve_shape_selector(target_view, track)
        source_view = (
            track_shape.parent_view
            if isinstance(track_shape.parent_view, View) and isinstance(track_shape.parent_view.raw, dict)
            else target_view
        )
        track_box = self.runner._box(track_shape.raw, source_view.raw)
        anchor_offset_y: float | None = None
        if anchor is not None:
            anchor_shape = self.resolve_shape_selector(target_view, anchor)
            anchor_view = (
                anchor_shape.parent_view
                if isinstance(anchor_shape.parent_view, View) and isinstance(anchor_shape.parent_view.raw, dict)
                else target_view
            )
            anchor_box = self.runner._box(anchor_shape.raw, anchor_view.raw)
            anchor_offset_y = (
                float(track_box.get("y") or 0) + float(track_box.get("h") or 0) / 2
                - float(anchor_box.get("y") or 0) - float(anchor_box.get("h") or 0) / 2
            )
        before: int | None = None
        observed_text = ""

        for attempt in range(1, attempts + 1):
            observation = None
            cached_tokens: list[dict[str, Any]] = []
            for observation_attempt in range(3):
                frame = self.cur_frame(update=True)
                cached_ocr = self.runner._shared_spatial_ocr_result(self.ctx, frame)
                cached_tokens = cached_ocr.get("tokens") if isinstance(cached_ocr.get("tokens"), list) else []
                observation = find_labeled_percentage(group_ocr_tokens(cached_tokens), label)
                if observation is not None:
                    break
                # 滑块拖动后数值文字会短暂重绘为空。实机上下一帧通常即可
                # 恢复；单独的只读复核不占用有限拖动次数，也不能在数值
                # 未知时继续拖动。
                if observation_attempt < 2:
                    self.clear_frame()
                    yield from self.wait_action_settle(settle_seconds)
            if observation is None:
                raise RuntimeError(f"未从当前画面读到滑杆「{label}」的百分比")
            current, observed_text = observation.value, observation.text
            scale.index(current)
            if before is None:
                before = current
            if current == target:
                return {
                    "label": label,
                    "before": before,
                    "after": current,
                    "target": target,
                    "attempts": attempt - 1,
                    "text": observed_text,
                }

            active_track_box = dict(track_box)
            if anchor_offset_y is not None:
                label_box = locate_text_box(cached_tokens, label)
                if label_box is None:
                    raise RuntimeError(f"未从 OCR token 定位到滑杆标题「{label}」")
                anchor_geometry = label_box
                live_anchor_center_y = (
                    float(anchor_geometry.get("y") or 0)
                    + float(anchor_geometry.get("h") or 0) / 2
                )
                active_track_box["y"] = (
                    live_anchor_center_y
                    + anchor_offset_y
                    - float(track_box.get("h") or 0) / 2
                )
            start_x, start_y, end_x, end_y = scale.drag_points(active_track_box, current, target)
            self._emit_runtime_action(
                f"调整 #{target_view.id or '?'}「{label}」：{current}% -> {target}%",
                phase="runtime_set_slider",
                kind="drag",
                current_scene=target_view.id,
            )
            self.runner._drag_frame_point(
                self.ctx,
                source_view.raw,
                start_x,
                start_y,
                end_x,
                end_y,
                duration_ms=max(50, int(float(duration) * 1000)),
            )
            self.clear_frame()
            yield from self.wait_action_settle(settle_seconds)

        frame = self.cur_frame(update=True)
        cached_ocr = self.runner._shared_spatial_ocr_result(self.ctx, frame)
        cached_tokens = cached_ocr.get("tokens") if isinstance(cached_ocr.get("tokens"), list) else []
        observation = find_labeled_percentage(group_ocr_tokens(cached_tokens), label)
        after = observation.value if observation is not None else None
        raise RuntimeError(
            f"滑杆「{label}」调整失败：目标 {target}%，{attempts} 次拖拽后为 "
            f"{str(after) + '%' if after is not None else '无法识别'}"
        )

    def allocate_balanced_points(
        self,
        view: View | int | str,
        *,
        points_shape: Shape | str,
        first_value_shape: Shape | str,
        second_value_shape: Shape | str,
        first_increase_shape: Shape | str,
        second_increase_shape: Shape | str,
        first_label: str = "第一项",
        second_label: str = "第二项",
        minimum: int = 10,
        step: int = 10,
        max_points: int = 100,
        verify_attempts: int = 3,
        settle_seconds: float = 0.8,
    ):
        """均衡消耗两个增量属性的剩余点数，并逐次闭环复核。

        本函数不知道“攻击/伤害”或“仙窍试炼”，只处理两个具有共同最小值和
        步长的增量控件。它按累计已投入档位选择较少的一项，相同时优先第一
        项。每次点击后只做 OCR 复读，不会因识别延迟重复点击。

        :return dict: 初始状态、最终状态及每一次实际点击后的状态。
        """

        target_view = self.view(view)
        verify_attempts = max(1, int(verify_attempts))
        max_points = max(0, int(max_points))

        def read_one(shape: Shape | str, frame: str, label: str) -> int:
            numbers, text = self.ocr_numbers_in_shapes(
                target_view,
                (shape.title if isinstance(shape, Shape) else str(shape),),
                frame_data_url=frame,
            )
            if len(numbers) != 1:
                raise RuntimeError(f"未能唯一读取「{label}」：OCR={text!r}，数字={numbers}")
            return int(numbers[0])

        def read_state() -> BalancedPointState:
            frame = self.cur_frame(update=True)
            return BalancedPointState(
                remaining=read_one(points_shape, frame, "剩余点数"),
                first_value=read_one(first_value_shape, frame, first_label),
                second_value=read_one(second_value_shape, frame, second_label),
                minimum=int(minimum),
                step=int(step),
            )

        initial = read_state()
        if initial.remaining > max_points:
            raise RuntimeError(f"剩余点数 {initial.remaining} 超过安全上限 {max_points}")
        state = initial
        actions: list[dict[str, Any]] = []

        while state.remaining > 0:
            target = state.next_target()
            is_first = target == "first"
            target_label = first_label if is_first else second_label
            target_shape = first_increase_shape if is_first else second_increase_shape
            expected = BalancedPointState(
                remaining=state.remaining - 1,
                first_value=state.first_value + (state.step if is_first else 0),
                second_value=state.second_value + (0 if is_first else state.step),
                minimum=state.minimum,
                step=state.step,
            )
            self.click_shape_center(target_view, target_shape)
            yield from self.wait_action_settle(settle_seconds)

            observed: BalancedPointState | None = None
            for verification in range(verify_attempts):
                observed = read_state()
                if observed == expected:
                    break
                if verification + 1 < verify_attempts:
                    yield from self.wait_action_settle(settle_seconds)
            if observed != expected:
                raise RuntimeError(
                    f"点击增加{target_label}后状态未按预期更新：expected={expected}，observed={observed}"
                )
            actions.append(
                {
                    "target": target_label,
                    "remaining": observed.remaining,
                    "first_value": observed.first_value,
                    "second_value": observed.second_value,
                }
            )
            state = observed

        return {
            "before": {
                "remaining": initial.remaining,
                "first_value": initial.first_value,
                "second_value": initial.second_value,
            },
            "after": {
                "remaining": state.remaining,
                "first_value": state.first_value,
                "second_value": state.second_value,
            },
            "actions": actions,
        }

    def read_current_trial_difficulty(
        self,
        view: View | int | str,
        *,
        frame_data_url: str | None = None,
    ) -> ObservedTrialDifficulty:
        """从 #358 当前真实帧读取“当前难度为 N 级”。

        该函数只建立本轮 ``当前+1`` 的难度起点，不推断任何滑杆值。每根
        滑杆仍必须由 ``set_slider_value`` 读取自己的标题百分比并复核。
        """

        self.view(view)  # Fail early when the requested asset is unavailable.
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        cached_ocr = self.runner._shared_spatial_ocr_result(self.ctx, frame)
        tokens = cached_ocr.get("tokens") if isinstance(cached_ocr.get("tokens"), list) else []
        observation = find_current_trial_difficulty(group_ocr_tokens(tokens))
        if observation is None:
            # The saved #358 failure frame visibly contains the complete
            # label, while the shared full-frame OCR omitted it. Re-read only
            # the existing formal「当前难度」Shape; do not broaden the OCR
            # region or infer a level from slider positions.
            bounded_lines = self.ocr_fragments_in_shapes(
                view,
                ("当前难度",),
                padding=12,
                frame_data_url=frame,
                crop=True,
            )
            bounded_text = self.runner._ocr_text(bounded_lines)
            observation = find_current_trial_difficulty([{"text": bounded_text}])
        if observation is None:
            raise RuntimeError("未从当前画面读取到“当前难度为 N 级”")
        return observation

    def configure_even_trial_difficulty(
        self,
        view: View | int | str,
        target_level: int,
        *,
        scroll_shape: Shape | str = "难度窗口",
        track_shape: Shape | str = "伤害降低",
        title_anchor_shape: Shape | str = "难度条",
        max_scrolls_per_axis: int = 3,
        settle_seconds: float = 0.8,
    ):
        """按均匀模型配置五根难度滑杆并复核最终显示等级。

        函数职责仅是业务编排：计算计划、按标题滚动到可见行、逐根调用
        ``set_slider_value``，最后确认界面显示的当前难度。单根滑杆的坐标
        换算、拖拽和百分比校准不在这里重复实现。

        #358 当前资产中两个历史 shape 名称与视觉语义相反：``伤害降低``
        标的是第一根滑轨，``难度条`` 标的是第一行标题区域。本函数通过参数
        显式保留这个事实，若以后重命名标注，只需调整调用参数。

        :param int target_level: 最终目标等级，例如 26。
        :return dict: 纯业务计划、五根滑杆结果和最终显示等级。
        """

        target_view = self.view(view)
        plan = build_even_trial_difficulty_plan(target_level)
        max_scrolls_per_axis = max(0, int(max_scrolls_per_axis))

        def label_visible(label: str) -> bool:
            frame = self.cur_frame(update=True)
            cached_ocr = self.runner._shared_spatial_ocr_result(self.ctx, frame)
            tokens = cached_ocr.get("tokens") if isinstance(cached_ocr.get("tokens"), list) else []
            return any(label in re.sub(r"\s+", "", str(fragment.get("text") or "")) for fragment in group_ocr_tokens(tokens))

        def ensure_axis_visible(label: str, direction: str):
            for scroll_index in range(max_scrolls_per_axis + 1):
                if label_visible(label):
                    return
                if scroll_index >= max_scrolls_per_axis:
                    break
                # This container is filled with slider tracks.  A center drag
                # can be consumed by the slider underneath and leave the list
                # stationary, so scroll through the right-side blank band of
                # the already annotated container.
                self.drag_shape_content(
                    target_view,
                    scroll_shape,
                    direction=direction,
                    ratio=0.65,
                    duration=1.0,
                    cross_axis_ratio=0.92,
                )
                yield from self.wait_action_settle(settle_seconds)
            raise RuntimeError(f"滚动难度窗口后仍未看到“{label}”")

        results: list[dict[str, Any]] = []
        for index, (axis, target_value) in enumerate(zip(TRIAL_DIFFICULTY_AXES, plan.values)):
            # Start from the first row and then move monotonically toward the
            # lower rows.  This keeps scroll behavior deterministic regardless
            # of where the previous Cell left the list.
            direction = "up" if index < 3 else "down"
            yield from ensure_axis_visible(axis.label, direction)
            result = yield from self.set_slider_value(
                target_view,
                axis.label,
                target_value,
                track=track_shape,
                anchor=title_anchor_shape,
                minimum=axis.minimum,
                maximum=axis.maximum,
                step=axis.step,
                settle_seconds=settle_seconds,
            )
            results.append(result)

        final_observation: ObservedTrialDifficulty | None = None
        last_read_error: RuntimeError | None = None
        for verification in range(3):
            try:
                final_observation = self.read_current_trial_difficulty(target_view)
                last_read_error = None
                if final_observation.level == plan.level:
                    break
            except RuntimeError as exc:
                # The difficulty label is animated after the final slider drag.
                # A single OCR frame can therefore be empty even though the
                # settled frame immediately afterwards is authoritative.
                last_read_error = exc
            if verification < 2:
                yield from self.wait_action_settle(settle_seconds)
        if final_observation is None and last_read_error is not None:
            raise RuntimeError("难度配置后连续三次未读到当前难度") from last_read_error
        if final_observation is None or final_observation.level != plan.level:
            actual = final_observation.level if final_observation is not None else None
            raise RuntimeError(f"难度配置后显示等级不符：目标 {plan.level}，实际 {actual}")

        return {
            "plan": {
                "level": plan.level,
                "positions": list(plan.positions),
                "values": list(plan.values),
            },
            "sliders": results,
            "final_level": final_observation.level,
            "final_text": final_observation.text,
        }

    def prepare_xianqiao_trial_settings(
        self,
        view: View | int | str = 358,
        *,
        target_level: int | None = None,
        difficulty_increment: int = 1,
        settle_seconds: float = 0.8,
    ):
        """按仙窍试炼业务顺序准备 #358 的全部开战参数。

        固定顺序是：

        1. 先按当前体系已穿戴仙纹统计金/水/火，选择数量最少者作为掉落元素；
        2. 再把五行增益点数按累计投入均衡分完；
        3. 从实时画面读取当前难度；
        3. 优先使用显式 ``target_level``；否则以
           ``当前难度 + difficulty_increment`` 生成均匀难度计划；
        4. 配置五根滑杆并复核最终显示等级。

        五行增益和难度滑杆是两套独立资源模型。五行点数不参与难度等级
        公式，但必须先完成，避免后续流程在 #358 留下未消费资源。

        ``target_level`` 保留给人工调试、纠偏和指定等级测试。正式每日推进
        不保存外部等级，只根据 #357 的“开启扫荡”状态使用相对 ``+1/-1``。
        """

        drop_element = yield from self.configure_xianqiao_trial_drop_element(
            view,
            settle_seconds=settle_seconds,
        )
        five_elements = yield from self.allocate_balanced_points(
            view,
            points_shape="五行点数",
            first_value_shape="当前攻击",
            second_value_shape="当前伤害",
            first_increase_shape="增加攻击",
            second_increase_shape="增加伤害",
            first_label="攻击",
            second_label="伤害",
            minimum=10,
            step=10,
            settle_seconds=settle_seconds,
        )
        current: ObservedTrialDifficulty | None = None
        last_current_error: RuntimeError | None = None
        for attempt in range(3):
            try:
                current = self.read_current_trial_difficulty(view)
                last_current_error = None
                break
            except RuntimeError as exc:
                # #358 is already transactionally established and the five-
                # element allocation has just animated the page.  A missing
                # difficulty label on one fresh OCR frame is therefore a
                # transient field read, not a reason to discard the whole
                # navigation and wait for the Scheduler's technical retry.
                last_current_error = exc
                if attempt < 2:
                    self.runner._log(
                        "warning",
                        f"仙窍_试炼：当前难度首帧暂未读到，原地刷新重试 {attempt + 2}/3",
                    )
                    yield from self.wait_action_settle(settle_seconds)
        if current is None:
            raise RuntimeError("仙窍_试炼：连续三次未读到当前难度") from last_current_error
        resolved_target_level = (
            current.level + int(difficulty_increment)
            if target_level is None
            else int(target_level)
        )
        difficulty = yield from self.configure_even_trial_difficulty(
            view,
            resolved_target_level,
            settle_seconds=settle_seconds,
        )
        return {
            "drop_element": drop_element,
            "five_elements": five_elements,
            "current_level": current.level,
            "target_level": resolved_target_level,
            "difficulty": difficulty,
        }

    def configure_xianqiao_trial_drop_element(
        self,
        view: View | int | str = 358,
        *,
        selector_shape: Shape | str = "shape 12",
        settle_seconds: float = 0.8,
    ):
        """Set #358's trial drop element to the least represented 金/水/火.

        Business selection comes exclusively from the read-only
        ``ImmHoleData.GetElementLvDic(type)`` equivalent. OCR only locates the
        already-decided label in the dropdown; it must never choose the element.
        If the runtime model or final visual verification is unavailable, fail
        closed instead of silently keeping the game's default 金.
        """

        from backend.core.fanxiu.instrumentation.xianqiao import (
            read_xianqiao_snapshot,
            select_xianqiao_trial_drop_element,
        )

        snapshot = read_xianqiao_snapshot()
        if not snapshot.get("complete"):
            raise RuntimeError(
                "仙窍已穿戴五行数据不完整，拒绝沿用默认掉落元素："
                f"{snapshot.get('reason') or 'runtime snapshot incomplete'}"
            )
        decision = select_xianqiao_trial_drop_element(
            snapshot.get("element_counts_by_id") or {}
        )
        target = str(decision["element"])
        self.runner._log(
            "detail",
            (
                "仙窍试炼掉落元素决策：当前已穿戴仙纹 "
                f"金{decision['desired_counts']['金']}、"
                f"水{decision['desired_counts']['水']}、"
                f"火{decision['desired_counts']['火']}，选择最少的「{target}」"
            ),
        )
        target_view = self.view(view)
        shape_title = selector_shape.title if isinstance(selector_shape, Shape) else str(selector_shape)

        current = self.find_ocr_text(
            target_view,
            target,
            in_shapes=[shape_title],
            padding=8,
        )
        changed = current is None
        if changed:
            self.click_shape(target_view, selector_shape)
            yield from self.wait_action_settle(settle_seconds)
            # The popup is attached below the selector. Padding intentionally
            # covers the five short rows while excluding the rest of #358.
            option = self.find_ocr_text(
                target_view,
                target,
                in_shapes=[shape_title],
                padding=260,
            )
            if option is None:
                raise RuntimeError(f"#358 掉落元素下拉框未识别到目标「{target}」")
            click_x, click_y = option.point()
            self.click_frame_point(target_view, click_x, click_y)
            yield from self.wait_action_settle(settle_seconds)

        verified = self.find_ocr_text(
            target_view,
            target,
            in_shapes=[shape_title],
            padding=8,
        )
        if verified is None:
            raise RuntimeError(f"#358 掉落元素配置复核失败：目标「{target}」")
        self.runner._log(
            "success",
            (
                f"#358 掉落元素已复核为「{target}」"
                f"（{'已切换' if changed else '原设置已正确'}）"
            ),
        )
        return {
            **decision,
            "active_system_type": snapshot.get("active_system_type"),
            "worn_parts": snapshot.get("worn_parts"),
            "changed": changed,
        }

    def read_xianqiao_trial_attempts(
        self,
        view: View | int | str = 357,
        *,
        attempts_shape: Shape | str = "次数",
        frame_data_url: str | None = None,
    ) -> ObservedTrialAttempts:
        """从 #357 实时读取剩余奖励次数。

        逐级探测只在 ``remaining > 0`` 时允许发起下一场。次数必须从当前
        画面读取，不能用购买次数或本轮循环次数推导，因为购买、成功、失败
        是否消耗次数属于游戏实时状态。
        """

        shape_title = attempts_shape.title if isinstance(attempts_shape, Shape) else str(attempts_shape)
        text = self.ocr_text_in_shapes(
            view,
            [shape_title],
            padding=16,
            frame_data_url=frame_data_url,
        )
        return parse_xianqiao_trial_attempts(text)

    def observe_xianqiao_trial_home(
        self,
        view: View | int | str = 357,
        *,
        attempts_shape: Shape | str = "次数",
        sweep_shape: Shape | str = "开启扫荡",
        threshold: float | None = None,
    ) -> ObservedTrialHomeState:
        """从 #357 的同一帧读取次数和“开启扫荡”状态。

        “开启扫荡”是游戏对当前难度是否已经通关的权威标志。出现时，下一场
        应先把难度加1；未出现时，当前难度本身就是尚未通过的越级难度，应
        直接挑战，不能再加1。这里不读取或持久化昨天的成功等级。
        """

        frame = self.cur_frame(update=True)
        attempts = self.read_xianqiao_trial_attempts(
            view,
            attempts_shape=attempts_shape,
            frame_data_url=frame,
        )
        score = self.shape_score(view, sweep_shape, frame_data_url=frame)
        min_score = self.runner.overlay_threshold if threshold is None else float(threshold)
        return ObservedTrialHomeState(
            attempts=attempts,
            sweep_available=score >= float(min_score),
            sweep_score=score,
        )

    def configure_xianqiao_trial_level(
        self,
        target_level: int,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        settle_seconds: float = 0.8,
    ):
        """从 #357 进入设置页并配置一个绝对难度后返回 #357。

        这是人工调试和纠偏接口；正式每日流程使用
        :meth:`adjust_xianqiao_trial_level`，避免依赖外部保存的等级。
        """

        self.click_shape_center(home_view, "设置难度")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_view(
            settings_view,
            timeout=15.0,
            label="进入仙窍试炼设置页",
        )
        settings = yield from self.prepare_xianqiao_trial_settings(
            settings_view,
            target_level=int(target_level),
            settle_seconds=settle_seconds,
        )
        self.click_shape_center(settings_view, "返回")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_view(
            home_view,
            timeout=15.0,
            label=f"仙窍试炼{int(target_level)}级设置后返回主页",
        )
        return settings

    def adjust_xianqiao_trial_level(
        self,
        difficulty_increment: int,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        settle_seconds: float = 0.8,
    ):
        """从 #357 进入设置页，把实时难度相对调整后返回 #357。

        日常无状态推进只使用 ``+1``（已出现“开启扫荡”）和 ``-1``（首次
        失败后回退到已证明可通过的难度）。具体等级由 #358 的实时文字读取，
        不从外部保存的等级推导。
        """

        increment = int(difficulty_increment)
        if increment == 0:
            raise ValueError("仙窍试炼相对难度调整不能为0")
        self.click_shape_center(home_view, "设置难度")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_view(
            settings_view,
            timeout=15.0,
            label="进入仙窍试炼设置页",
        )
        settings = yield from self.prepare_xianqiao_trial_settings(
            settings_view,
            difficulty_increment=increment,
            settle_seconds=settle_seconds,
        )
        self.click_shape_center(settings_view, "返回")
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_view(
            home_view,
            timeout=15.0,
            label=f"仙窍试炼难度{increment:+d}后返回主页",
        )
        return settings

    def start_xianqiao_trial_challenge(
        self,
        *,
        challenge_view: View | int | str = 357,
        start_confirm_view: View | int | str = 359,
        continue_confirm_view: View | int | str = 360,
        sweep_confirm_view: View | int | str = 366,
        max_polls: int = 30,
        stable_departure_polls: int = 5,
        settle_seconds: float = 0.8,
        sweep_result_delay: float = 5.0,
        sweep_return_timeout: float = 15.0,
    ):
        """按当前实际场景推进仙窍试炼的开战确认链。

        这是从试炼主页进入战斗的一组标准动作。函数每轮只观察当前场景并
        响应它实际看到的按钮，不使用“是否加过难度”等历史变量推导弹窗：

        - 遇到 #357，点击“挑战”；
        - 遇到 #359，点击“开始挑战”；
        - 遇到 #360，点击“继续挑战”。
        - 遇到 #366，点击“开启扫荡”。

        #359/#360/#366 都只是点击 #357“挑战”后可能实际出现的候选，不由
        调用方根据难度历史预选分支。因此函数也可从任一确认页恢复执行。
        若游戏没有展示某个确认场景，该步骤会自然跳过；连续若干轮离开已知场景后，视为已经进入加载
        或战斗。每个已处理场景最多点击一次，避免界面切换延迟造成重复操作。

        游戏可能走 ``#366 -> #367 -> #357``，也可能把通用奖励页 #227
        插在其中，而且一次扫荡可能连续出现多页 #227。#367 会自动消失；
        #227 必须点击已有的“继续”动作。因此点击扫荡后会在限定时间内按
        当前真实场景逐页收口，直到稳定回到 #357。

        :param challenge_view: 试炼主页及“挑战”动作所在 View，默认 #357。
        :param start_confirm_view: “开始挑战”确认 View，默认 #359。
        :param continue_confirm_view: “继续挑战”确认 View，默认 #360。
        :param sweep_confirm_view: “开启扫荡”确认 View，默认 #366。
        :param int max_polls: 整个确认链允许的最大观察轮数。
        :param int stable_departure_polls: 离开已知场景后判定进入战斗所需连续轮数。
        :param float settle_seconds: 点击或观察之间的稳定等待秒数。
        :param float sweep_result_delay: 扫荡后等待瞬时奖励层消失的秒数。
        :param float sweep_return_timeout: 奖励层消失后等待 #357 的超时时间。
        :return dict: 实际执行的动作及离开确认链的原因。
        """

        views = {
            int(self.view(challenge_view).id): "挑战",
            int(self.view(start_confirm_view).id): "开始挑战",
            int(self.view(continue_confirm_view).id): "继续挑战",
            int(self.view(sweep_confirm_view).id): "开启扫荡",
        }
        challenge_id = int(self.view(challenge_view).id)
        continue_id = int(self.view(continue_confirm_view).id)
        sweep_id = int(self.view(sweep_confirm_view).id)
        terminal_confirmation_ids = {continue_id}
        max_polls = max(1, int(max_polls))
        stable_departure_polls = max(1, int(stable_departure_polls))
        handled: set[int] = set()
        actions: list[dict[str, Any]] = []
        absent_polls = 0
        last_scene_id: int | None = None

        business_foreground_ids = tuple(
            scene_id for scene_id in views if scene_id != challenge_id
        ) + (227, 367)
        with self.expect_views(business_foreground_ids):
            for poll_index in range(max_polls):
                scene_id, score, frame = self.current_scene(list(views), update=True)
                last_scene_id = scene_id
                if scene_id in views:
                    absent_polls = 0
                    if scene_id not in handled:
                        shape = views[scene_id]
                        self.click_shape(scene_id, shape, frame_data_url=frame)
                        handled.add(scene_id)
                        actions.append({
                            "scene": scene_id,
                            "shape": shape,
                            "score": float(score),
                        })
                        if scene_id == sweep_id:
                            yield from self.wait_action_settle(sweep_result_delay)
                            return_deadline = time.monotonic() + max(1.0, float(sweep_return_timeout))
                            while True:
                                remaining = return_deadline - time.monotonic()
                                if remaining <= 0:
                                    raise TimeoutError("仙窍试炼扫荡奖励收口超时，未返回 #357")
                                landed = yield from self.wait_view(
                                    challenge_view,
                                    227,
                                    367,
                                    timeout=remaining,
                                    label="仙窍试炼扫荡奖励收口并返回主页",
                                )
                                landed_id = int(landed.id)
                                if landed_id == challenge_id:
                                    break
                                if landed_id == 227:
                                    frame = self.cur_frame(update=True)
                                    self.click_shape(227, "继续", frame_data_url=frame)
                                    actions.append({
                                        "scene": 227,
                                        "shape": "继续",
                                        "score": 100.0,
                                    })
                                yield from self.wait_action_settle(settle_seconds)
                            return {
                                "actions": actions,
                                "exit_reason": "sweep_completed",
                                "last_scene": challenge_id,
                            }
                        yield from self.wait_action_settle(settle_seconds)
                        if scene_id in terminal_confirmation_ids:
                            return {
                                "actions": actions,
                                "exit_reason": "continue_confirmed",
                                "last_scene": scene_id,
                            }
                        continue
                elif handled:
                    absent_polls += 1
                    if absent_polls >= stable_departure_polls:
                        return {
                            "actions": actions,
                            "exit_reason": "left_confirmation_chain",
                            "last_scene": scene_id,
                        }
                elif poll_index + 1 >= stable_departure_polls:
                    raise RuntimeError("未遇到 #357/#359/#360/#366，无法开始仙窍试炼挑战")

                yield from self.wait_action_settle(settle_seconds)

            pending = (
                f"#{last_scene_id}"
                if last_scene_id is not None
                else "unknown"
            )
            if challenge_id in handled:
                raise TimeoutError(f"仙窍试炼开战确认链超时，最后场景 {pending}")
            raise RuntimeError(f"未能开始仙窍试炼挑战，最后场景 {pending}")

    def wait_xianqiao_trial_result(
        self,
        *,
        battle_view: View | int | str = 362,
        success_view: View | int | str = 361,
        failure_view: View | int | str = 365,
        world_view: View | int | str = 34,
        battle_entry_timeout: float = 30.0,
        battle_timeout: float = 360.0,
        result_settle_seconds: float = 0.2,
    ):
        """识别 #362 战斗中状态并等待仙窍试炼结算画面。

        #362 是持续数分钟的战斗中间态，不是异常，也不是挑战完成。函数可
        以从 #362 等待到结算，也可在 Cell 恢复时直接接管 #361/#365。
        两种结算都包含“退出”，但成功/失败必须由场景身份区分，不能由共用
        按钮反推结果。

        业务上 #361/#365 若约1分钟无人操作，大概率会自行消失并回到 #34。
        一旦回到世界页，胜负状态和可点击现场都已丢失，重新进入仙窍的恢复
        成本很高。因此等待候选显式包含 #34：命中时返回 ``result_expired``，
        绝不把它猜成成功或失败；正常命中结算后只等待极短稳定时间，交由上
        层立即记录结果并点击“退出”。工程调度不应在这个窗口执行其它工作。

        本函数只观察结果，不点击“退出”。需要结算后自动返回 #357 时使用
        :meth:`complete_xianqiao_trial_challenge`。

        :param battle_view: “战斗中”场景，默认 #362。
        :param success_view: 成功结算及“退出”动作所在 View，默认 #361。
        :param failure_view: 失败结算及“退出”动作所在 View，默认 #365。
        :param world_view: 结算自动消失后的世界页，默认 #34。
        :param float battle_entry_timeout: 开战确认后等待进入 #362 的秒数。
        :param float battle_timeout: 在 #362 中等待战斗结束的最大秒数。
        :return dict: ``success``、``failure`` 或 ``result_expired`` 及 OCR 证据。
        """

        battle = self.view(battle_view)
        success = self.view(success_view)
        failure = self.view(failure_view)
        world = self.view(world_view)
        observed_view = yield from self.wait_view(
            battle,
            success,
            failure,
            timeout=battle_entry_timeout,
            label="等待仙窍试炼战斗或直接结算",
        )
        observed_id = int(observed_view.id) if isinstance(observed_view, View) else int(observed_view)
        if observed_id == int(battle.id):
            result_view = yield from self.wait_view(
                success,
                failure,
                world,
                timeout=battle_timeout,
                label="等待仙窍试炼成功、失败或结算过期",
            )
        else:
            result_view = observed_view

        result_id = int(result_view.id) if isinstance(result_view, View) else int(result_view)
        if result_id == int(world.id):
            return {
                "outcome": "result_expired",
                "battle_scene": int(battle.id),
                "result_scene": int(world.id),
                "ocr_text": "",
                "_frame_data_url": None,
            }
        yield from self.wait_action_settle(result_settle_seconds)
        frame = self.cur_frame(update=True)
        outcome_by_scene = {
            int(success.id): "success",
            int(failure.id): "failure",
        }
        outcome = outcome_by_scene.get(result_id)
        if outcome is None:
            raise RuntimeError(f"仙窍试炼结算命中了非候选场景 #{result_id}")
        return {
            "outcome": outcome,
            "battle_scene": int(battle.id),
            "result_scene": result_id,
            "ocr_text": self.ocr_text(update=False),
            "_frame_data_url": frame,
        }

    def complete_xianqiao_trial_challenge(
        self,
        *,
        home_view: View | int | str = 357,
        success_view: View | int | str = 361,
        battle_view: View | int | str = 362,
        failure_view: View | int | str = 365,
        world_view: View | int | str = 34,
        battle_entry_timeout: float = 30.0,
        battle_timeout: float = 360.0,
        settle_seconds: float = 0.8,
    ):
        """发起仙窍试炼、等待 #362 战斗结束并安全处理已知成功页。

        这是业务调用方最常用的完整挑战接口。它组合开战确认链与战斗结果
        等待，明确区分 #361 成功和 #365 失败；两种已知结算都会立即记录并
        点击各自“退出”，避免约1分钟后自动回 #34。结算页退出或自动消失后
        既可能回 #357，也可能直接回 #34；命中 #34 时复用正式入口重新进入
        #357。若结算未捕捉，本函数只恢复主页，不猜测胜负，交由逐级探测根
        据战后“开启扫荡”状态判定本轮结果。

        Runtime 会在点击前写入明确结果日志并把结构化结果返回。无需持久化
        最高成功或首次失败等级；返回 #357 后由游戏按钮继续提供权威状态。

        :return dict: 开战动作、结算识别结果，以及是否已返回主页。
        """

        started = yield from self.start_xianqiao_trial_challenge(
            settle_seconds=settle_seconds,
        )
        if started.get("exit_reason") == "sweep_completed":
            return {
                "started": started,
                "result": {
                    "outcome": "sweep",
                    "result_scene": int(self.view(home_view).id),
                },
                "returned_home": True,
            }
        result = yield from self.wait_xianqiao_trial_result(
            battle_view=battle_view,
            success_view=success_view,
            failure_view=failure_view,
            world_view=world_view,
            battle_entry_timeout=battle_entry_timeout,
            battle_timeout=battle_timeout,
            result_settle_seconds=settle_seconds,
        )
        frame = result.pop("_frame_data_url", None)
        if result["outcome"] == "result_expired":
            self.runner._log(
                "warning",
                "仙窍试炼结算未捕捉且已回到 #34，重新进入 #357 复核当前关卡通关状态",
            )
            reentry = yield from self.enter_xianqiao_trial(
                trial_view=home_view,
                settle_seconds=settle_seconds,
            )
            return {
                "started": started,
                "result": result,
                "returned_home": True,
                "landing_scene": int(self.view(world_view).id),
                "reentered_from_world": True,
                "reentry": reentry,
            }

        self.runner._log(
            "success" if result["outcome"] == "success" else "warning",
            (
                f"仙窍试炼结算已确认：{result['outcome']} "
                f"#{result['result_scene']}，立即退出以避免弹窗自动消失"
            ),
        )
        self.click_shape(result["result_scene"], "退出", frame_data_url=frame)
        yield from self.wait_action_settle(settle_seconds)
        landing_view = yield from self.wait_view(
            home_view,
            world_view,
            timeout=15.0,
            label="仙窍试炼结算退出后的实际落点",
        )
        landing_id = int(landing_view.id) if isinstance(landing_view, View) else int(landing_view)
        reentry = None
        if landing_id == int(self.view(world_view).id):
            self.runner._log(
                "action",
                "仙窍试炼结算退出后直接回到 #34，重新进入 #357 继续本轮任务",
            )
            reentry = yield from self.enter_xianqiao_trial(
                trial_view=home_view,
                settle_seconds=settle_seconds,
            )
        return {
            "started": started,
            "result": result,
            "returned_home": True,
            "landing_scene": landing_id,
            "reentered_from_world": reentry is not None,
            "reentry": reentry,
        }

    def probe_xianqiao_trial_until_failure(
        self,
        *,
        home_view: View | int | str = 357,
        settings_view: View | int | str = 358,
        max_challenges: int = 20,
        settle_seconds: float = 0.8,
        battle_timeout: float = 360.0,
    ):
        """完全依赖 #357 实时状态逐级挑战，直到次数耗尽或首次失败。

        每轮先观察同一帧中的剩余次数和“开启扫荡”：若按钮存在，说明当前
        难度已通关，先相对加1再挑战；若按钮不存在，说明当前已经处于越级
        状态，直接挑战。成功后回到 #357 再观察，绝不在内存中推算下一等级。

        首次失败后立即把当前难度相对减1，恢复到刚刚已经证明能通过的难度。
        若仍有次数，返回 ``sweep_required=True``，留在 #357 等待后续扫荡
        逻辑消费；扫荡动作尚未纳入本函数。

        #361/#365 约1分钟无人操作会自动回 #34，点击结算页“退出”也可能直
        接落到 #34。完整挑战函数会重新进入 #357。若结算窗口未捕捉，则用
        战后主页的“开启扫荡”恢复本轮结果：出现表示刚挑战的当前关已通关，
        未出现表示刚挑战失败。这个规则只用于同一轮挑战后的复核，不能与初
        次进入时“无扫荡则允许挑战一次”的规则混用。

        当前接口故意不自动购买次数，也不在失败后扫荡。每日入口应先调用
        :meth:`purchase_xianqiao_trial_attempts` 买到目标档位，再调用本函数。
        :param int max_challenges: 单次调用最多实际挑战次数，防止无界循环。
        :return dict: 剩余次数、是否需要扫荡、逐轮证据和停止原因。
        """

        home_id = int(self.view(home_view).id)
        scene_id, score, _frame = self.current_scene([home_id], update=True)
        if scene_id != home_id:
            raise RuntimeError(
                f"逐级探测必须从仙窍试炼主页 #{home_id} 开始，"
                f"实际 #{scene_id} ({float(score):.0f}%)"
            )

        trials: list[dict[str, Any]] = []
        last_observation: ObservedTrialHomeState | None = None

        def finish(
            exit_reason: str,
            remaining_attempts: int | None,
            *,
            rollback_settings: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {
                "exit_reason": exit_reason,
                "remaining_attempts": remaining_attempts,
                "sweep_required": bool(
                    exit_reason == "failure_found"
                    and remaining_attempts is not None
                    and remaining_attempts > 0
                ),
                "rollback_settings": rollback_settings,
                "trials": trials,
            }

        for _index in range(max(1, int(max_challenges))):
            observation = self.observe_xianqiao_trial_home(home_view)
            last_observation = observation
            attempts = observation.attempts
            if attempts.remaining <= 0:
                return finish("attempts_exhausted", attempts.remaining)

            settings = None
            mode = "challenge_existing_overlevel"
            if observation.sweep_available:
                settings = yield from self.adjust_xianqiao_trial_level(
                    1,
                    home_view=home_view,
                    settings_view=settings_view,
                    settle_seconds=settle_seconds,
                )
                mode = "incremented_from_sweep"
            challenge = yield from self.complete_xianqiao_trial_challenge(
                home_view=home_view,
                battle_timeout=battle_timeout,
                settle_seconds=settle_seconds,
            )
            outcome = str(challenge["result"]["outcome"])
            outcome_source = "result_scene"
            recovery_observation = None
            if outcome == "result_expired" and challenge.get("returned_home"):
                recovery_observation = self.observe_xianqiao_trial_home(home_view)
                outcome = "success" if recovery_observation.sweep_available else "failure"
                outcome_source = "post_challenge_sweep"
                self.runner._log(
                    "success" if outcome == "success" else "warning",
                    (
                        "仙窍试炼结算未捕捉，重新进入后"
                        f"{'检测到' if recovery_observation.sweep_available else '未检测到'}"
                        f"“开启扫荡”，判定本轮{outcome}"
                    ),
                )
            attempts_after = (
                recovery_observation.attempts
                if recovery_observation is not None
                else (
                    self.read_xianqiao_trial_attempts(home_view)
                    if challenge.get("returned_home")
                    else None
                )
            )
            record = {
                "mode": mode,
                "resolved_outcome": outcome,
                "outcome_source": outcome_source,
                "sweep_score_before": observation.sweep_score,
                "sweep_score_after": (
                    recovery_observation.sweep_score
                    if recovery_observation is not None
                    else None
                ),
                "attempts_before": {
                    "remaining": attempts.remaining,
                    "capacity": attempts.capacity,
                    "text": attempts.text,
                },
                "attempts_after": (
                    {
                        "remaining": attempts_after.remaining,
                        "capacity": attempts_after.capacity,
                        "text": attempts_after.text,
                    }
                    if attempts_after is not None
                    else None
                ),
                "settings": settings,
                "challenge": challenge,
            }
            trials.append(record)

            if outcome == "success":
                if attempts_after is None:
                    return finish("result_expired", None)
                continue
            if outcome == "failure":
                remaining = attempts_after.remaining if attempts_after is not None else None
                if attempts_after is None:
                    return finish("result_expired", None)
                rollback = yield from self.adjust_xianqiao_trial_level(
                    -1,
                    home_view=home_view,
                    settings_view=settings_view,
                    settle_seconds=settle_seconds,
                )
                return finish(
                    "failure_found",
                    remaining,
                    rollback_settings=rollback,
                )
            return finish("result_expired", None)

        remaining = (
            last_observation.attempts.remaining
            if last_observation is not None
            else None
        )
        return finish("max_challenges_reached", remaining)

    def purchase_xianqiao_trial_attempts(
        self,
        target_daily_purchases: int = 3,
        *,
        home_view: View | int | str = 357,
        purchase_view: View | int | str = 363,
        exhausted_view: View | int | str = 364,
        wait_timeout: float = 15.0,
        settle_seconds: float = 0.8,
        max_transitions: int = 12,
    ):
        """把仙窍试炼当天累计购买次数补到目标档位并返回 #357。

        ``target_daily_purchases`` 表示当天累计购买目标，不是本次额外购买数。
        默认 3 即清空当天三个购买档位；传 2 时只购买 100、150 灵石两档，
        在 #363 看到 200 灵石后主动点击“返回”，不会继续消费。

        函数完全根据实时场景推进，不预设点击 #357“购买”后一定出现哪个页：

        - 遇到 #357：点击“购买”，等待 #363 或 #364；
        - 遇到 #363：OCR 当前价格并反推当天已买次数。未到目标则购买，已到
          目标则点击 #363“返回”；
        - 遇到 #364：说明当天购买次数已达上限，点击 #364“返回”。

        因此它既可从 #357 开始，也可从已打开的 #363/#364 恢复；每次购买
        后同样重新识别实际场景。未知价格会立即停止，宁可保留现场也不误购。

        :param int target_daily_purchases: 当天累计希望购买的次数，合法值 0..3。
        :param home_view: 试炼主页及“购买”入口所在 View，默认 #357。
        :param purchase_view: 价格和“购买并使用”所在 View，默认 #363。
        :param exhausted_view: 当天购买次数耗尽提示 View，默认 #364。
        :return dict: 目标、开始/结束档位、本次实际购买价格和结束原因。
        """

        target = normalize_xianqiao_trial_purchase_target(target_daily_purchases)
        home_id = int(self.view(home_view).id)
        purchase_id = int(self.view(purchase_view).id)
        exhausted_id = int(self.view(exhausted_view).id)
        candidate_ids = (home_id, purchase_id, exhausted_id)
        purchases_now: list[int] = []
        purchased_before: int | None = None
        purchased_after: int | None = None
        actions: list[dict[str, Any]] = []
        pending_price: tuple[int, str, str] | None = None

        def waited_view_id(waited: View | int) -> int:
            if isinstance(waited, View):
                if waited.id is None:
                    raise RuntimeError("等待结果缺少 View 编号")
                return int(waited.id)
            return int(waited)

        def read_purchase_price(frame: str | None = None) -> tuple[int, str, str]:
            current_frame = frame if isinstance(frame, str) and frame else self.cur_frame(update=True)
            numbers, ocr_text = self.ocr_numbers_in_shapes(
                purchase_view,
                ["价格"],
                frame_data_url=current_frame,
            )
            known_prices = [
                number
                for number in numbers
                if number in XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES
            ]
            if len(set(known_prices)) != 1:
                raise RuntimeError(f"#363 无法唯一识别购买价格：{ocr_text!r}")
            return known_prices[0], ocr_text, current_frame

        scene_id, _score, _frame = self.current_scene(candidate_ids, update=True)
        if scene_id not in candidate_ids:
            raise RuntimeError("仙窍试炼购买流程必须从 #357/#363/#364 之一开始")

        for _transition in range(max(1, int(max_transitions))):
            if scene_id == home_id:
                self.click_shape_center(home_view, "购买")
                actions.append({"scene": home_id, "shape": "购买"})
                yield from self.wait_action_settle(settle_seconds)
                waited = yield from self.wait_view(
                    purchase_view,
                    exhausted_view,
                    timeout=wait_timeout,
                    label="等待仙窍试炼购买结果",
                )
                scene_id = waited_view_id(waited)
                continue

            if scene_id == exhausted_id:
                purchased_before = (
                    len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES)
                    if purchased_before is None
                    else purchased_before
                )
                purchased_after = len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES)
                self.click_shape_center(exhausted_view, "返回")
                actions.append({"scene": exhausted_id, "shape": "返回"})
                yield from self.wait_action_settle(settle_seconds)
                yield from self.wait_view(
                    home_view,
                    timeout=wait_timeout,
                    label="等待返回仙窍试炼主页",
                )
                return {
                    "target_daily_purchases": target,
                    "purchased_before": purchased_before,
                    "purchases_now": purchases_now,
                    "purchased_after": purchased_after,
                    "actions": actions,
                    "exit_reason": "daily_limit_reached",
                    "terminal_scene": home_id,
                }

            if pending_price is not None:
                price, ocr_text, frame = pending_price
                pending_price = None
            else:
                price, ocr_text, frame = read_purchase_price()
            completed = purchases_completed_before_price(price)
            purchased_before = completed if purchased_before is None else purchased_before
            purchased_after = completed

            if completed >= target:
                self.click_shape_center(purchase_view, "返回")
                actions.append({"scene": purchase_id, "shape": "返回", "price": price})
                yield from self.wait_action_settle(settle_seconds)
                yield from self.wait_view(
                    home_view,
                    timeout=wait_timeout,
                    label="等待返回仙窍试炼主页",
                )
                return {
                    "target_daily_purchases": target,
                    "purchased_before": purchased_before,
                    "purchases_now": purchases_now,
                    "purchased_after": purchased_after,
                    "actions": actions,
                    "exit_reason": "target_reached",
                    "terminal_scene": home_id,
                }

            self.click_shape(
                purchase_view,
                "购买并使用",
                frame_data_url=frame,
            )
            purchases_now.append(price)
            purchased_after = completed + 1
            actions.append({"scene": purchase_id, "shape": "购买并使用", "price": price})
            yield from self.wait_action_settle(settle_seconds)
            waited = yield from self.wait_view(
                home_view,
                purchase_view,
                exhausted_view,
                timeout=wait_timeout,
                label="等待仙窍试炼购买后的实际场景",
            )
            scene_id = waited_view_id(waited)
            if scene_id != purchase_id:
                continue

            # #363 本身没有离开时，必须等价格从本次档位前进到下一档。
            # wait_view 可能在购买动画尚未刷新时立即命中旧 #363；若直接进入
            # 下一轮，会把旧价格当成仍可购买并造成重复消费。
            expected_index = completed + 1
            if expected_index >= len(XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES):
                expected_price = None
            else:
                expected_price = XIANQIAO_TRIAL_DAILY_PURCHASE_PRICES[expected_index]
            refresh_deadline = time.monotonic() + max(1.0, float(wait_timeout))
            while scene_id == purchase_id:
                try:
                    next_price, next_text, next_frame = read_purchase_price()
                except RuntimeError:
                    next_price = None
                    next_text = ""
                    next_frame = ""
                if expected_price is not None and next_price == expected_price:
                    pending_price = (next_price, next_text, next_frame)
                    break
                if next_price not in (None, price):
                    raise RuntimeError(
                        f"#363 购买 {price} 后价格未按档位前进："
                        f"预期 {expected_price}，实际 {next_price}"
                    )
                if time.monotonic() >= refresh_deadline:
                    raise TimeoutError(f"#363 购买 {price} 后价格/场景未在限时内刷新")
                yield from self.wait_action_settle(min(0.4, settle_seconds or 0.4))
                scene_id, _score, _frame = self.current_scene(candidate_ids, update=True)

        raise TimeoutError(
            f"仙窍试炼购买流程超过 {max_transitions} 次场景转换；"
            f"已购买价格 {purchases_now}，最后场景 #{scene_id}"
        )

    def enter_xianqiao_trial(
        self,
        *,
        daily_view: View | int | str = 69,
        category_view: View | int | str = 356,
        trial_view: View | int | str = 357,
        max_daily_scrolls: int = 30,
        settle_seconds: float = 0.8,
        trial_entry_timeout: float = 120.0,
    ):
        """从稳定世界锚点进入仙窍试炼主页 #357。

        正式任务由框架先归一到 #34。本函数用通用场景规划进入 #69，在日常
        列表实时查找“仙窍”，等待 #356 后，再在“试炼”区域内用全局空间
        OCR 找到唯一“真仙”并点击。标题文字坐标不是固定 shape，因此必须
        使用本轮 OCR 坐标；无法唯一命中时保留现场并停止，不能猜位置。
        """

        yield from self.goto_view(daily_view)
        status = yield from self.open_daily_entry(
            label="仙窍_试炼",
            title_pattern=r"仙\s*窍",
            progress_can_mark_done=False,
            max_scrolls=max(0, int(max_daily_scrolls)),
            # 首条任务会被世界公告/飘字短暂遮挡；先保持列表静止复识别，
            # 不要因单帧 OCR 漏字立即把入口滚出当前画面。
            initial_checks=3,
        )
        if status != "open":
            raise RuntimeError(f"仙窍_试炼：#69 未能打开仙窍入口，状态 {status!r}")
        yield from self.wait_view(
            category_view,
            timeout=15.0,
            label="仙窍_试炼：等待仙窍分类页 #356",
        )
        frame = self.cur_frame(update=True)
        matches = self.ocr_centers_in_shape(
            category_view,
            "试炼",
            include=("真仙",),
            frame_data_url=frame,
        )
        if len(matches) != 1:
            visible = [text for _x, _y, text in matches]
            raise RuntimeError(
                f"仙窍_试炼：#356「试炼」区域无法唯一定位“真仙”，命中 {visible}"
            )
        x, y, text = matches[0]
        self.runner._log("action", f"仙窍_试炼：#356 点击 OCR「{text}」进入真仙试炼")
        self.click_frame_point(category_view, x, y)
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_view(
            trial_view,
            timeout=max(15.0, float(trial_entry_timeout)),
            label="仙窍_试炼：等待试炼主页 #357",
        )
        return {"daily_entry": status, "category_scene": 356, "terminal_scene": 357}

    def sweep_remaining_xianqiao_trial_attempts(
        self,
        *,
        home_view: View | int | str = 357,
        max_sweeps: int = 10,
        settle_seconds: float = 0.8,
    ):
        """失败回退一级后，把 #357 实时剩余次数全部扫荡完。

        每轮重新读取次数和“开启扫荡”，再复用标准挑战入口。扫荡后必须看到
        次数严格减少；否则立即停止，避免输入未生效时重复消费或无限循环。
        """

        sweeps: list[dict[str, Any]] = []
        for _index in range(max(1, int(max_sweeps))):
            observation = self.observe_xianqiao_trial_home(home_view)
            before = observation.attempts
            if before.remaining <= 0:
                return {
                    "exit_reason": "attempts_exhausted",
                    "remaining_attempts": 0,
                    "sweeps": sweeps,
                }
            if not observation.sweep_available:
                raise RuntimeError(
                    "仙窍_试炼：失败回退后仍有次数，但 #357 未识别到“开启扫荡”"
                )
            challenge = yield from self.complete_xianqiao_trial_challenge(
                home_view=home_view,
                settle_seconds=settle_seconds,
            )
            outcome = str(challenge.get("result", {}).get("outcome") or "")
            if outcome != "sweep" or not challenge.get("returned_home"):
                raise RuntimeError(f"仙窍_试炼：剩余次数扫荡未稳定返回 #357，结果 {outcome!r}")
            after = self.read_xianqiao_trial_attempts(home_view)
            if after.remaining >= before.remaining:
                raise RuntimeError(
                    f"仙窍_试炼：扫荡后次数未减少，之前 {before.remaining}，之后 {after.remaining}"
                )
            sweeps.append({
                "attempts_before": before.remaining,
                "attempts_after": after.remaining,
                "challenge": challenge,
            })
            if after.remaining <= 0:
                return {
                    "exit_reason": "attempts_exhausted",
                    "remaining_attempts": 0,
                    "sweeps": sweeps,
                }
        raise RuntimeError(f"仙窍_试炼：超过 {max_sweeps} 次扫荡仍未耗尽次数")

    def leave_xianqiao_trial(
        self,
        *,
        home_view: View | int | str = 357,
        world_view: View | int | str = 34,
        settle_seconds: float = 0.8,
    ):
        """点击 #357“返回”并确认直接回到稳定世界 #34。"""

        home_id = int(self.view(home_view).id)
        scene_id, score, frame = self.current_scene([home_id], update=True)
        if scene_id != home_id:
            raise RuntimeError(
                f"仙窍_试炼收尾预期 #357，实际 #{scene_id} ({float(score):.0f}%)"
            )
        self.click_shape(home_view, "返回", frame_data_url=frame)
        yield from self.wait_action_settle(settle_seconds)
        yield from self.wait_view(
            world_view,
            timeout=15.0,
            label="仙窍_试炼：等待返回世界 #34",
        )
        return {"from_scene": home_id, "terminal_scene": int(self.view(world_view).id)}

    def run_xianqiao_trial_daily(
        self,
        *,
        target_daily_purchases: int = 0,
        max_challenges: int = 20,
        settle_seconds: float = 0.8,
        battle_timeout: float = 360.0,
    ):
        """从 #357 执行仙窍试炼当日完整闭环并最终回到 #34。

        每天不读取或保存昨天的难度状态。默认不购买额外次数，只消耗当天
        两次免费次数；仅当调用方显式传入非零 ``target_daily_purchases`` 时，
        才先把当天累计购买次数补到目标档位。随后完全依据 #357 的实时按钮
        逐级挑战。

        购买函数会处理已经买过部分档位或已出现 #364 的情况；挑战次数仍以
        #357 OCR 的实际值为准，不硬断言次数。若探测到首次失败，本
        函数已经回退1级；若仍有次数，继续扫荡到次数为0。无论当天次数都
        挑战成功，还是中途失败后扫荡剩余次数，最后都从 #357 返回 #34。

        真实运行已证明最终“返回”直接回世界页，不经过 #356。AI 单步调试
        不调用本完整入口，而是继续按用户指令拆分调用底层函数。
        """

        purchase_target = normalize_xianqiao_trial_purchase_target(target_daily_purchases)
        if purchase_target:
            purchase = yield from self.purchase_xianqiao_trial_attempts(
                purchase_target,
                settle_seconds=settle_seconds,
            )
        else:
            purchase = {
                "target_daily_purchases": 0,
                "purchased_before": None,
                "purchases_now": [],
                "purchased_after": None,
                "actions": [],
                "exit_reason": "purchase_disabled",
                "terminal_scene": 357,
            }
        progression = yield from self.probe_xianqiao_trial_until_failure(
            max_challenges=max_challenges,
            settle_seconds=settle_seconds,
            battle_timeout=battle_timeout,
        )
        exit_reason = str(progression.get("exit_reason") or "")
        if exit_reason not in {"attempts_exhausted", "failure_found"}:
            raise RuntimeError(f"仙窍_试炼：逐级探测未正常结束，原因 {exit_reason!r}")
        sweep = None
        if progression.get("sweep_required"):
            sweep = yield from self.sweep_remaining_xianqiao_trial_attempts(
                max_sweeps=max_challenges,
                settle_seconds=settle_seconds,
            )
        leave = yield from self.leave_xianqiao_trial(settle_seconds=settle_seconds)
        return {
            "purchase": purchase,
            "progression": progression,
            "sweep": sweep,
            "leave": leave,
            "result": "success",
            "message": "仙窍_试炼完成，已回到世界 #34",
            "current_scene": 34,
        }

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
        container_title = (
            str(container_shape.raw.get("title") or "")
            if isinstance(container_shape, Shape)
            else str(container_shape or item_template.raw.get("title") or "")
        )
        tokens = self.ocr_tokens_in_shapes(
            target_view,
            (container_title,),
            padding=0,
            frame_data_url=frame,
        )
        fragments = group_ocr_tokens(tokens)
        container_box = (
            _absolute_shape_box(self.resolve_shape_selector(target_view, container_shape))
            if container_shape is not None
            else _absolute_shape_box(item_template)
        )
        template_box = _absolute_shape_box(item_template)
        anchor_template_box = _absolute_shape_box(anchor_shape)
        target_text = _sanitize_ocr_text(anchor_shape.raw.get("ocrText") or anchor_shape.title)
        mode = str(anchor_shape.raw.get("ocrMatchMode") or "contains")
        for fragment in fragments:
            text = _sanitize_ocr_text(fragment.get("text"))
            if not text or not target_text or not self.runner._ocr_text_matches(text, target_text, mode):
                continue
            fragment_tokens = query_spatial_ocr(tokens, fragment)["tokens"]
            resolved_box = locate_text_box(fragment_tokens, target_text)
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

    def find_floating_items_by_anchor_text(
        self,
        view: View | int | str,
        template_shape: Shape | str,
        anchor_field: Shape | str,
        target_text: str,
        *,
        container_shape: Shape | str,
        frame_data_url: str | None = None,
        match_mode: str = "exact",
        crop: bool = False,
    ) -> list[FloatingItemInstance]:
        """在滚动容器内用动态 OCR 锚点解析所有同名重复模板实例。"""
        target_view = self.view(view)
        item_template = self.resolve_shape_selector(target_view, template_shape)
        anchor_shape = self._resolve_floating_item_field(target_view, item_template, anchor_field)
        container = self.resolve_shape_selector(target_view, container_shape)
        direction = str(container.raw.get("loadDirection") or "").strip().lower()
        frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=True)
        tokens = (
            self.ocr_tokens_in_shapes(
                target_view,
                (str(container.raw.get("title") or ""),),
                frame_data_url=frame,
                crop=True,
            )
            if crop
            else self.ocr_tokens_in_shapes(
                target_view,
                (str(container.raw.get("title") or ""),),
                padding=0,
                frame_data_url=frame,
            )
        )
        container_box = _absolute_shape_box(container)
        template_box = _absolute_shape_box(item_template)
        anchor_template_box = _absolute_shape_box(anchor_shape)
        normalized_target = _sanitize_ocr_text(target_text)
        matches: list[FloatingItemInstance] = []
        for fragment in group_ocr_tokens(tokens):
            text = _sanitize_ocr_text(fragment.get("text"))
            if not text or not normalized_target:
                continue
            name_similarity = 0.0
            if match_mode == "name":
                name_similarity = ocr_name_similarity(normalized_target, text)
                if name_similarity <= 0:
                    continue
                resolved_box = {
                    key: float(fragment.get(key) or 0)
                    for key in ("x", "y", "w", "h")
                }
            else:
                if not self.runner._ocr_text_matches(text, normalized_target, match_mode):
                    continue
                fragment_tokens = query_spatial_ocr(tokens, fragment)["tokens"]
                resolved_box = locate_text_box(fragment_tokens, normalized_target)
                if resolved_box is None:
                    continue
            center_x = float(resolved_box.get("x") or 0) + float(resolved_box.get("w") or 0) / 2
            center_y = float(resolved_box.get("y") or 0) + float(resolved_box.get("h") or 0) / 2
            if not self._point_in_box(center_x, center_y, container_box):
                continue
            item_box = repeated_template_item_box_from_anchor(
                template_box,
                anchor_template_box,
                resolved_box,
                load_direction=direction,
            )
            matches.append(
                FloatingItemInstance(
                    view=target_view,
                    template_shape=item_template,
                    anchor_shape=anchor_shape,
                    anchor_box={key: float(resolved_box.get(key) or 0) for key in ("x", "y", "w", "h")},
                    item_box=item_box,
                    text=text,
                    name_similarity=name_similarity,
                )
            )
        if match_mode == "name":
            matches.sort(key=lambda item: item.name_similarity, reverse=True)
        return matches

    def floating_item_is_fully_inside(self, item: FloatingItemInstance, container_shape: Shape | str) -> bool:
        container = self.resolve_shape_selector(item.view, container_shape)
        outer = _absolute_shape_box(container)
        inner = item.item_box
        return (
            float(inner.get("x") or 0) >= outer["x"]
            and float(inner.get("y") or 0) >= outer["y"]
            and float(inner.get("x") or 0) + float(inner.get("w") or 0) <= outer["x"] + outer["w"]
            and float(inner.get("y") or 0) + float(inner.get("h") or 0) <= outer["y"] + outer["h"]
        )

    def floating_item_field_is_inside(
        self,
        item: FloatingItemInstance,
        field: Shape | str,
        container_shape: Shape | str,
    ) -> bool:
        field_shape = self._resolve_floating_item_field(item.view, item.template_shape, field)
        field_box = item.field_box(field_shape)
        center_x = field_box["x"] + field_box["w"] / 2
        center_y = field_box["y"] + field_box["h"] / 2
        container = self.resolve_shape_selector(item.view, container_shape)
        return self._point_in_box(center_x, center_y, _absolute_shape_box(container))

    def floating_item_field_is_fully_inside(
        self,
        item: FloatingItemInstance,
        field: Shape | str,
        container_shape: Shape | str,
    ) -> bool:
        """Require the whole clickable field, not merely its center, in bounds."""

        field_shape = self._resolve_floating_item_field(item.view, item.template_shape, field)
        inner = item.field_box(field_shape)
        container = self.resolve_shape_selector(item.view, container_shape)
        outer = _absolute_shape_box(container)
        return (
            float(inner.get("x") or 0) >= outer["x"]
            and float(inner.get("y") or 0) >= outer["y"]
            and float(inner.get("x") or 0) + float(inner.get("w") or 0)
            <= outer["x"] + outer["w"]
            and float(inner.get("y") or 0) + float(inner.get("h") or 0)
            <= outer["y"] + outer["h"]
        )

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
        cached = self.runner._shared_spatial_ocr_result(self.ctx, frame, options={"return_word_box": True})
        tokens = cached.get("tokens") if isinstance(cached.get("tokens"), list) else []
        return str(query_spatial_ocr(tokens, field_box).get("text") or "")

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
        shape_label = f"{label}：#{target_view.id or '?'} {self._shape_path(target_shape)}"
        if not self.runner._shape_has_runtime_click_condition(target_shape.raw):
            # An unconstrained Shape has no visual fact that can be tested.
            # Treat it as the caller's already-established business action and
            # avoid scene matching, popup injection, OCR, and image matching.
            frame = self.cur_frame()
            self.runner._log("detail", f"{shape_label} 无图像/OCR约束，跳过检测")
            return frame
        try:
            frame, match_result = yield from self.runner._wait_shape_match(
                self.ctx,
                self.stop_event or threading.Event(),
                target_view.raw,
                self._shape_match_search_shape(target_shape),
                timeout=wait_timeout,
                label=shape_label,
                min_similarity=min_score,
            )
        except RuntimeError as exc:
            raise TimeoutError(str(exc)) from exc
        matched_score = float(match_result.get("similarity") or 0.0)
        self.runner._log("success", f"{shape_label} {matched_score:.0f}%")
        return frame
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
        cross_axis_ratio: float = 0.5,
    ) -> Any:
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) and shape is None else self.shape(view_or_shape, shape or "")
        view = target_shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError("shape 缺少 parent_view，无法滚动加载")
        planner = ActionPlanner()
        resolved_direction = str(direction or target_shape.load_direction or "down").strip().lower()
        start_x, start_y, end_x, end_y = planner.drag_shape_content_points(
            view.raw,
            target_shape.raw,
            direction=resolved_direction,
            ratio=ratio,
        )
        cross_axis_ratio = max(0.0, min(1.0, float(cross_axis_ratio)))
        box = planner.shape_box(view.raw, target_shape.raw)
        if resolved_direction in {"up", "down"}:
            safe_x = float(box.get("x") or 0) + float(box.get("w") or 0) * cross_axis_ratio
            start_x = end_x = safe_x
        else:
            safe_y = float(box.get("y") or 0) + float(box.get("h") or 0) * cross_axis_ratio
            start_y = end_y = safe_y
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

    def drag_shape_to_frame_edge(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        direction: str,
        duration: float = 0.6,
    ) -> None:
        """从指定 shape 内部起拖，并一直拖到画面的安全边缘。

        适用于滑块这类控件：标注框描述控件本身，但要把滑块可靠推到极限，
        拖拽终点不能受标注框边界限制。业务代码只需要提供场景和 shape 名称。
        """
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) and shape is None else self.shape(view_or_shape, shape or "")
        view = target_shape.parent_view
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError("shape 缺少 parent_view，无法拖到画面边缘")

        box = self.runner._box(target_shape.raw, view.raw)
        frame_width, frame_height = self.runner._frame_size(view.raw)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        width = max(1.0, float(box.get("w") or 0))
        height = max(1.0, float(box.get("h") or 0))
        margin_x = max(2.0, frame_width * 0.02)
        margin_y = max(2.0, frame_height * 0.02)
        inset_x = min(width * 0.5, max(2.0, width * 0.04))
        inset_y = min(height * 0.5, max(2.0, height * 0.04))
        normalized_direction = str(direction or "").strip().lower()
        live_center: tuple[float, float] | None = None
        if bool(target_shape.raw.get("floating")):
            frame = self.cur_frame(update=True)
            live_center = self.runner._shape_center(
                target_shape.raw,
                view.raw,
                frame,
                self.ctx,
            )

        if normalized_direction == "right":
            start_x, start_y = live_center or (left + inset_x, top + height / 2)
            end_x, end_y = frame_width - margin_x, start_y
        elif normalized_direction == "left":
            start_x, start_y = live_center or (left + width - inset_x, top + height / 2)
            end_x, end_y = margin_x, start_y
        elif normalized_direction == "down":
            start_x, start_y = live_center or (left + width / 2, top + inset_y)
            end_x, end_y = start_x, frame_height - margin_y
        elif normalized_direction == "up":
            start_x, start_y = live_center or (left + width / 2, top + height - inset_y)
            end_x, end_y = start_x, margin_y
        else:
            raise ValueError(f"不支持的拖拽方向：{direction}")

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

    def drag_shape_between_shapes_fraction(
        self,
        view: View | int | str,
        start_shape: Shape | str,
        left_shape: Shape | str,
        right_shape: Shape | str,
        *,
        fraction: float,
        duration: float = 0.35,
    ) -> None:
        """Drag a thumb to a horizontal fraction between two named anchors.

        This is only a coarse step for large discrete sliders.  Business code
        must still read the displayed value and close the loop with exact
        controls; geometry is never treated as the resulting business value.
        """

        target_view = self.view(view)
        start = self.resolve_shape_selector(target_view, start_shape)
        left = self.resolve_shape_selector(target_view, left_shape)
        right = self.resolve_shape_selector(target_view, right_shape)
        frame = self.cur_frame()
        start_x, start_y = self.runner._shape_center(
            start.raw,
            target_view.raw,
            frame,
            self.ctx,
        )
        left_x, _left_y = self.runner._shape_center(left.raw, target_view.raw)
        right_x, _right_y = self.runner._shape_center(right.raw, target_view.raw)
        ratio = min(1.0, max(0.0, float(fraction)))
        end_x = left_x + (right_x - left_x) * ratio
        self.runner._drag_frame_point(
            self.ctx,
            target_view.raw,
            start_x,
            start_y,
            end_x,
            start_y,
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
        unchanged_confirmations: int = 1,
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
        before_frame = self.cur_frame(update=True)
        before_signature = self.image_signature_bytes_in_shape(
            signature_shape,
            frame_data_url=before_frame,
        )
        self.drag_shape_content(target_shape, direction=direction, ratio=ratio, duration=duration)
        yield from self.wait_action_settle(settle_seconds)
        after_frame = self.cur_frame(update=True)
        after_signature = self.image_signature_bytes_in_shape(
            signature_shape,
            frame_data_url=after_frame,
        )
        # 滚动动画结束不代表画面已经稳定。连续采样，优先采用相邻稳定后的帧；
        # 横幅等持续动态内容应通过遮挡标注或 observe_scroll_content 的语义键规避。
        for _ in range(max(1, int(stable_sample_count)) - 1):
            yield from self.wait_action_settle(max(0.1, float(stable_sample_interval)))
            candidate_frame = self.cur_frame(update=True)
            candidate_signature = self.image_signature_bytes_in_shape(
                signature_shape,
                frame_data_url=candidate_frame,
            )
            if self.image_signature_similarity(after_signature, candidate_signature) >= float(unchanged_threshold):
                after_signature = candidate_signature
                break
            after_signature = candidate_signature
        similarity = self.image_signature_similarity(before_signature, after_signature)
        changed = bool(after_signature and similarity < float(unchanged_threshold))
        shape_identity = str(target_shape.raw.get("id") or target_shape.raw.get("title") or "shape")
        state_key = f"{shape_identity}:{direction or target_shape.load_direction or 'down'}"
        confirmation_state = self.attrs.setdefault("_scroll_unchanged_confirmations", {})
        if changed:
            confirmation_state[state_key] = 0
            return True
        confirmations = int(confirmation_state.get(state_key) or 0) + 1
        confirmation_state[state_key] = confirmations
        return confirmations < max(1, int(unchanged_confirmations))

    def paged_content_snapshot(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        frame_data_url: str | None = None,
    ) -> dict[str, Any]:
        """Read one fully visible page/card from a paged loading window."""

        target_shape = (
            view_or_shape
            if isinstance(view_or_shape, Shape) and shape is None
            else self.shape(view_or_shape, shape or "")
        )
        view = target_shape.parent_view
        if not isinstance(view, View):
            raise RuntimeError("整页加载 shape 缺少 parent_view")
        frame = (
            frame_data_url
            if isinstance(frame_data_url, str) and frame_data_url
            else self.cur_frame(update=True)
        )
        lines = self.ocr_fragments_in_shapes(
            view,
            [self._shape_path(target_shape).strip("[]")],
            frame_data_url=frame,
        )
        text = " ".join(
            str(item.get("text") or "").strip()
            for item in lines
            if str(item.get("text") or "").strip()
        )
        return {
            "frame": frame,
            "lines": lines,
            "text": text,
            "signature": self.image_signature_bytes_in_shape(
                target_shape, frame_data_url=frame
            ),
        }

    def step_paged_content(
        self,
        view_or_shape: View | int | str | Shape,
        shape: Shape | str | None = None,
        *,
        direction: str | None = None,
        ratio: float = 0.82,
        duration: float = 0.45,
        settle_seconds: float = 0.65,
        stable_sample_interval: float = 0.25,
        stable_sample_count: int = 4,
        max_stability_samples: int = 16,
        unchanged_threshold: float = 96.0,
    ):
        """Move exactly one snap page and wait until the whole page is stable."""

        target_shape = (
            view_or_shape
            if isinstance(view_or_shape, Shape) and shape is None
            else self.shape(view_or_shape, shape or "")
        )
        load_mode = str(target_shape.raw.get("loadMode") or "continuous").strip()
        if load_mode != "paged":
            raise RuntimeError(
                f"{self._shape_path(target_shape)} loadMode 不是 paged，"
                "拒绝按整页控件操作"
            )
        resolved_direction = str(
            direction or target_shape.load_direction or ""
        ).strip().lower()
        if resolved_direction not in {"up", "down", "left", "right"}:
            raise RuntimeError(
                f"{self._shape_path(target_shape)} 缺少有效窗口加载方向"
            )

        before = self.paged_content_snapshot(target_shape)
        self.drag_shape_content(
            target_shape,
            direction=resolved_direction,
            ratio=ratio,
            duration=duration,
        )
        yield from self.wait_action_settle(settle_seconds)
        after = self.paged_content_snapshot(target_shape)
        stable_samples = 1
        sample_attempts = 1
        while (
            stable_samples < max(1, int(stable_sample_count))
            and sample_attempts < max(2, int(max_stability_samples))
        ):
            yield from self.wait_action_settle(max(0.1, stable_sample_interval))
            candidate = self.paged_content_snapshot(target_shape)
            sample_attempts += 1
            similarity = self.image_signature_similarity(
                after["signature"], candidate["signature"]
            )
            after = candidate
            if similarity >= unchanged_threshold:
                stable_samples += 1
            else:
                stable_samples = 1
        if stable_samples < max(1, int(stable_sample_count)):
            raise RuntimeError(
                f"{self._shape_path(target_shape)} 整页切换后未稳定吸附"
            )
        similarity = self.image_signature_similarity(
            before["signature"], after["signature"]
        )
        return {
            "changed": bool(
                before["signature"]
                and after["signature"]
                and similarity < unchanged_threshold
            ),
            "similarity": similarity,
            "direction": resolved_direction,
            "before": before,
            "after": after,
        }

    def find_paged_content(
        self,
        view_or_shape: View | int | str | Shape,
        predicate: Callable[[dict[str, Any]], bool],
        shape: Shape | str | None = None,
        *,
        direction: str | None = None,
        max_pages: int = 30,
        repeat_threshold: float = 96.0,
    ):
        """Find a snap page using the annotated cursor prior and safe evidence.

        ``start`` only scans in the canonical loading direction. For a bounded
        control, ``unknown`` first rewinds to the real starting edge and then
        scans forward. For a cyclic control there is no distinguished edge, so
        it scans from the current page until repetition. Static metadata guides
        intent, while unchanged/repeated frames remain the fail-closed runtime
        evidence. Business callers cannot opt back into an unproved reverse
        pass; cursor ambiguity must be represented by metadata or established
        by the future shared Runtime-GUI alignment layer.
        """

        target_shape = (
            view_or_shape
            if isinstance(view_or_shape, Shape) and shape is None
            else self.shape(view_or_shape, shape or "")
        )

        def repeats_seen_page(candidate: dict[str, Any], seen: dict[str, Any]) -> bool:
            candidate_text = re.sub(r"\s+", "", str(candidate.get("text") or ""))
            seen_text = re.sub(r"\s+", "", str(seen.get("text") or ""))
            if candidate_text and seen_text:
                text_similarity = difflib.SequenceMatcher(
                    None, candidate_text, seen_text
                ).ratio()
                if text_similarity < 0.9:
                    return False
            return (
                self.image_signature_similarity(
                    seen["signature"], candidate["signature"]
                )
                >= repeat_threshold
            )
        primary = str(
            direction or target_shape.load_direction or ""
        ).strip().lower()
        opposites = {
            "up": "down",
            "down": "up",
            "left": "right",
            "right": "left",
        }
        if primary not in opposites:
            raise RuntimeError(
                f"{self._shape_path(target_shape)} 缺少有效窗口加载方向"
            )

        current = self.paged_content_snapshot(target_shape)
        if predicate(current):
            return current

        initial_position = str(
            target_shape.raw.get("loadInitialPosition") or "start"
        ).strip().lower()
        boundary = str(
            target_shape.raw.get("loadBoundary") or "bounded"
        ).strip().lower()

        # A bounded control with an unknown cursor must first be normalized to
        # its real starting edge. Only then does a forward pass have stable,
        # complete traversal semantics. A cyclic control has no distinguished
        # edge: starting anywhere and stopping on the first repeated signature
        # covers exactly one full cycle.
        if initial_position == "unknown" and boundary != "cyclic":
            rewind_pages = [current]
            for _ in range(max(1, int(max_pages))):
                step = yield from self.step_paged_content(
                    target_shape, direction=opposites[primary]
                )
                current = step["after"]
                if not step["changed"]:
                    break
                if any(repeats_seen_page(current, seen) for seen in rewind_pages):
                    raise RuntimeError(
                        f"{self._shape_path(target_shape)} 标注为有限窗口，"
                        "反向寻找起始端时却检测到循环"
                    )
                rewind_pages.append(current)
            else:
                raise RuntimeError(
                    f"{self._shape_path(target_shape)} 在 {int(max_pages)} 页内"
                    "未找到有限窗口起始端"
                )
            if predicate(current):
                return current

        for scan_direction in (primary,):
            directional_pages = [current]
            for _ in range(max(1, int(max_pages))):
                step = yield from self.step_paged_content(
                    target_shape, direction=scan_direction
                )
                current = step["after"]
                if predicate(current):
                    return current
                if not step["changed"]:
                    break
                if any(repeats_seen_page(current, seen) for seen in directional_pages):
                    # Covers cyclic carousels without requiring a static cycle
                    # flag. Bounded controls stop through changed=False instead.
                    break
                directional_pages.append(current)
        return None

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
        state_key = f"{shape_identity}:{direction or target_shape.load_direction or 'down'}"
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
        load_direction = str(target_shape.load_direction or "down").strip().lower()
        direction: str | None = None
        if load_direction in {"left", "right"}:
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
        fraction = parse_ocr_values(
            row_text,
            expected_count=2,
            allow_extra_numbers=True,
        )
        if fraction is None:
            return None
        current_int, total_int = fraction
        current_text = str(current_int)
        if total_int > 0 and current_int > total_int and len(current_text) >= 2:
            suffix_int = int(current_text[-1])
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

    def _daily_scroll_safe_shape(self, view69: View, list_shape: Shape) -> Shape:
        """Return the post-scroll viewport excluding only fixed UI overlays.

        A drag can move more than one task-row height.  Excluding the complete
        first visible row therefore creates a blind band: a title can jump from
        below that band to above it without ever becoming searchable.  Keep a
        small top inset for the fixed activity header, while allowing a
        partially visible row whose title centre is already inside the list.
        """

        list_box = self.runner._box(list_shape.raw, view69.raw)
        left = float(list_box.get("x") or 0)
        top = float(list_box.get("y") or 0)
        width = float(list_box.get("w") or 0)
        height = float(list_box.get("h") or 0)
        safe_top = top + height * 0.08
        # The fixed bottom navigation overlaps the lowest visible row and has
        # animated notification badges.  It is not scroll content, so exclude
        # it from both screenshot hashing and post-scroll OCR.  Together with
        # the first-row exclusion above, the stable middle rows are the
        # authoritative 2/3/4-style viewport after the first screen.
        safe_bottom = top + height * 0.78
        view_width = max(1.0, float(view69.raw.get("width") or 1))
        view_height = max(1.0, float(view69.raw.get("height") or 1))
        return Shape(
            {
                "id": f"{list_shape.raw.get('id') or 'daily-list'}-safe-viewport",
                "title": "滚动安全视口",
                "x": left / view_width,
                "y": safe_top / view_height,
                "w": width / view_width,
                "h": max(1.0, safe_bottom - safe_top) / view_height,
            },
            parent_view=view69,
        )

    def _daily_lines_in_shape(
        self,
        lines: list[dict[str, Any]],
        shape: Shape,
    ) -> list[dict[str, Any]]:
        view = shape.parent_view
        if not isinstance(view, View):
            return []
        box = self.runner._box(shape.raw, view.raw)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)
        return [
            line
            for line in lines
            if isinstance(line, dict)
            and left <= float(line.get("x") or 0) + float(line.get("w") or 0) / 2 <= right
            and top <= float(line.get("y") or 0) + float(line.get("h") or 0) / 2 <= bottom
        ]

    def _daily_visible_list_signature(
        self,
        lines: list[dict[str, Any]],
        view69: View,
        *,
        region_shape: Shape | None = None,
    ) -> tuple[tuple[str, int], ...]:
        target_shape = region_shape or self.resolve_shape_selector(view69, "滚动窗口")
        box = self.runner._box(target_shape.raw, view69.raw)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        height = float(box.get("h") or 0)
        bottom = top + height
        safe_top = top + height * 0.02
        safe_bottom = bottom - height * 0.08
        row_title_markers = (
            "参与",
            "完成",
            "接受",
            "击败",
            "挑战",
            "抵御",
            "收取",
            "进行",
            "寻找",
            "报名",
            "修炼",
            "副本",
            "拜谒",
            "仙窍",
            "首领",
            "领取",
        )
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
            if (
                left <= cx <= right
                and safe_top <= cy <= safe_bottom
                and any(marker in text for marker in row_title_markers)
            ):
                signature.append((text, int(round(cy))))
        return tuple(signature)

    def _daily_visible_list_moved(
        self,
        before: tuple[tuple[str, int], ...],
        after: tuple[tuple[str, int], ...],
        *,
        minimum_shift: int = 12,
    ) -> bool:
        """Confirm list movement from the same OCR row changing vertical position.

        OCR text alone is not an edge signal: animated counters and occasional
        character errors can change while the list is stationary.  Requiring a
        shared row with a material y shift preserves the legacy fallback for
        false-negative image-diff checks without turning OCR noise into endless
        scrolling at the top or bottom edge.
        """

        before_counts: dict[str, int] = {}
        after_counts: dict[str, int] = {}
        after_positions: dict[str, list[int]] = {}
        for text, _y in before:
            before_counts[text] = before_counts.get(text, 0) + 1
        for text, y in after:
            after_counts[text] = after_counts.get(text, 0) + 1
            after_positions.setdefault(text, []).append(int(y))
        threshold = max(1, int(minimum_shift))
        shared_unique = [
            (int(before_y), int(after_y))
            for text, before_y in before
            if before_counts.get(text) == 1 and after_counts.get(text) == 1
            for after_y in after_positions.get(text, ())
        ]
        if any(
            abs(int(before_y) - int(after_y)) >= threshold
            for before_y, after_y in shared_unique
        ):
            return True
        if shared_unique:
            return False
        # A large drag can replace the whole viewport, leaving no shared row.
        # With floating text excluded above, two non-empty disjoint title sets
        # are evidence of movement rather than OCR animation.
        return bool(before and after and {text for text, _y in before} != {text for text, _y in after})

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
            self.ocr_fragments(frame),
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
        cached = self.runner._shared_spatial_ocr_result(self.ctx, frame)
        tokens = cached.get("tokens") if isinstance(cached.get("tokens"), list) else []
        return self.runner._ocr_centers_in_shape(
            group_ocr_tokens(tokens),
            target_view.raw,
            shape_title,
            include=include,
            exclude=exclude,
            tokens=tokens,
        )

    def _ensure_daily_list_frame(self, frame: str, lines: list[dict[str, Any]], *, label: str) -> None:
        scene_id, score = self.runner._identify_scene_number(self.ctx, frame, [69, 34])
        text = self.runner._ocr_text(lines)
        if scene_id == 69:
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
        zero_progress_can_mark_done: bool = False,
        max_scrolls: int = 30,
        initial_checks: int = 1,
    ):
        """Find one #69 entry from its annotated starting edge.

        #69[滚动窗口] declares ``loadInitialPosition=start`` and
        ``loadDirection=down``.  This traversal therefore observes the current
        page first and only advances in the annotated loading direction.  A
        caller must not turn transient OCR loss or a guessed stale cursor into
        an unconditional rewind.  If a future Runtime-GUI alignment proves
        that the current cursor is elsewhere, that evidence belongs in the
        shared window navigator rather than a per-job reverse budget.
        """
        view69 = self.view(69)
        list_shape = self.shape(view69, "滚动窗口")
        safe_scroll_shape = self._daily_scroll_safe_shape(view69, list_shape)
        initial_checks = max(1, int(initial_checks or 1))
        # The post-scroll frame below is already fresh, OCRed and validated
        # for movement.  Reuse it as the next page's search evidence instead
        # of capturing and OCRing an unchanged screen a second time.  Keep
        # this cache local to one traversal and consume it exactly once.
        pending_page: tuple[str, list[dict[str, Any]]] | None = None
        for direction, scroll_count in (("down", max(0, int(max_scrolls))),):
            unchanged_scrolls = 0
            for scroll_index in range(scroll_count + 1):
                if self.stop_event is not None:
                    self.runner._raise_if_stopped(self.stop_event)
                check_count = initial_checks if direction == "down" and scroll_index == 0 else 1
                for check_index in range(check_count):
                    self._emit_runtime_action(
                        (
                            f"{label}：第一屏重复识别 {check_index + 1}/{check_count}"
                            if check_count > 1
                            else f"{label}：查找日常任务入口 {direction} {scroll_index}/{scroll_count}"
                        ),
                        phase="daily_entry_find",
                        kind="wait",
                        current_scene=69,
                    )
                    if pending_page is not None:
                        frame, lines = pending_page
                        pending_page = None
                    else:
                        frame = self.cur_frame(update=True)
                        lines = self.runner._ocr_fragments_in_scene_shapes(self.ctx, frame, view69.raw)
                    self._ensure_daily_list_frame(frame, lines, label=label)
                    # `_daily_entry_matches` 已按日常列表本身约束候选，并排除
                    # 固定头尾。较窄的 safe_scroll_shape 只用于判断列表是否
                    # 真正滚动；若也用于标题搜索，回到顶部后会永久滤掉被
                    # 世界公告遮挡的第一条任务（例如“完成仙窍试炼”）。
                    searchable_lines = lines
                    matches = self._daily_entry_matches(
                        searchable_lines,
                        view69,
                        title_pattern=title_pattern,
                        exclude_pattern=exclude_pattern,
                    )
                    if matches:
                        x, y, matched_text = matches[0]
                        progress = self._daily_entry_row_progress(lines, y)
                        if progress_can_mark_done and progress is not None and progress[0] >= progress[1]:
                            return "done"
                        if zero_progress_can_mark_done and progress is not None and progress[0] == 0:
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
                    if check_index + 1 < check_count:
                        self.runner._log(
                            "detail",
                            f"{label}：第一屏暂未识别到入口，保持列表不动并等待下一 tick {check_index + 1}/{check_count}",
                        )
                        yield BehaviorTreeStatus.RUNNING
                if scroll_index >= scroll_count:
                    break
                self.runner._log("action", f"{label}：未找到入口，{direction} 滚动日常列表 {scroll_index + 1}")
                before_visible_signature = self._daily_visible_list_signature(
                    lines,
                    view69,
                    region_shape=safe_scroll_shape,
                )
                changed = yield from self.scroll_shape_content(
                    view69,
                    list_shape,
                    recognition_shape=safe_scroll_shape,
                    direction=direction,
                )
                after_frame = self.cur_frame(update=True)
                after_lines = self.runner._ocr_fragments_in_scene_shapes(self.ctx, after_frame, view69.raw)
                pending_page = (after_frame, after_lines)
                after_visible_signature = self._daily_visible_list_signature(
                    after_lines,
                    view69,
                    region_shape=safe_scroll_shape,
                )
                if before_visible_signature and after_visible_signature:
                    # The screenshot-level diff also sees floating combat/status
                    # text crossing #69.  When OCR rows are available, their
                    # vertical displacement is the authoritative scroll signal.
                    changed = self._daily_visible_list_moved(
                        before_visible_signature,
                        after_visible_signature,
                    )
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
    return job_now()


def ensure_fanxiu_mail_table() -> None:
    from backend.core.fanxiu.mail.store import ensure_fanxiu_mail_table as _ensure_fanxiu_mail_table

    _ensure_fanxiu_mail_table()


def normalize_fanxiu_mail_title(value: Any) -> str:
    from backend.core.fanxiu.mail.store import normalize_fanxiu_mail_title as _normalize_fanxiu_mail_title

    return _normalize_fanxiu_mail_title(value)


def normalize_fanxiu_mail_time_text(value: Any) -> str:
    from backend.core.fanxiu.mail.store import normalize_fanxiu_mail_time_text as _normalize_fanxiu_mail_time_text

    return _normalize_fanxiu_mail_time_text(value)


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


def _behavior_tree_runtime_state_path() -> Path:
    return _core_behavior_tree_runtime_state_path()


def _data_annotation_world_facts_path() -> Path:
    return _core_data_annotation_world_facts_path()


def _data_annotation_scheduler_state_path() -> Path:
    return _core_data_annotation_scheduler_state_path()


def _data_annotation_scheduler_settings_path() -> Path:
    return _core_data_annotation_scheduler_settings_path()


def _data_annotation_mail_scan_state_path() -> Path:
    return _core_data_annotation_mail_scan_state_path()


def _persist_behavior_tree_runtime_status(status: dict[str, Any]) -> None:
    _persist_behavior_tree_runtime_status_core(
        _behavior_tree_runtime_state_path(),
        _data_annotation_world_facts_path(),
        status,
    )


def _read_behavior_tree_runtime_status() -> dict[str, Any]:
    return _read_behavior_tree_runtime_status_core(_behavior_tree_runtime_state_path())


def _record_data_annotation_scheduler_task_fact(task: dict[str, Any], result: str) -> None:
    record_data_annotation_scheduler_task_fact(_data_annotation_world_facts_path(), task, result)


def _read_data_annotation_world_facts() -> dict[str, Any]:
    return _read_data_annotation_json(_data_annotation_world_facts_path(), {})


def _write_data_annotation_world_facts(facts: dict[str, Any]) -> None:
    _write_data_annotation_json(_data_annotation_world_facts_path(), facts)


def _read_data_annotation_scheduler_tasks() -> list[dict[str, Any]]:
    # Scheduler persistence has one owner.  Keep this lazy import so the
    # Runtime module can still be imported while behavior_tree_control is loading.
    from backend.core.fanxiu.data_annotation.behavior_tree_control import (
        read_scheduler_tasks,
    )

    return read_scheduler_tasks(
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        world_facts_path=_data_annotation_world_facts_path(),
        now=_now(),
    )


def _write_data_annotation_scheduler_tasks(
    tasks: list[dict[str, Any]],
    *,
    runtime_update_ids: set[str] | None = None,
) -> None:
    from backend.core.fanxiu.data_annotation.behavior_tree_control import (
        write_scheduler_tasks,
    )

    write_scheduler_tasks(
        tasks,
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        runtime_update_ids=runtime_update_ids,
    )


def set_data_annotation_scheduler_task_trigger_time(
    task_name: str,
    trigger_time: datetime | str | None,
) -> str | None:
    """Set or clear the sole trigger timestamp of any Scheduler task."""

    # Scheduler persistence has one atomic field-level command.  A Job must
    # never write back a stale whole-table snapshot merely to update its own
    # ``next_time``.
    from backend.core.fanxiu.data_annotation.behavior_tree_control import (
        set_scheduler_task_next_time,
    )

    return set_scheduler_task_next_time(
        task_name,
        trigger_time,
        scheduler_state_path=_data_annotation_scheduler_state_path(),
        now=_now(),
    )


def _read_data_annotation_scheduler_settings() -> dict[str, Any]:
    return normalize_data_annotation_scheduler_settings(
        _read_data_annotation_json(_data_annotation_scheduler_settings_path(), None)
    )


def _data_annotation_task_supported(task: dict[str, Any]) -> bool:
    register_fanxiu_data_annotation_default_runtime_jobs()
    task_type = canonical_fanxiu_data_annotation_task_type(str(task.get("task_type") or ""))
    definition = _data_annotation_task_cell_definition(task_type)
    return bool(definition and definition.scheduler_supported)


def _data_annotation_task_payload_with_meta(task: dict[str, Any]) -> dict[str, Any]:
    return scheduled_task_payload_with_meta(task)


from backend.core.fanxiu.data_annotation.tasks.daily_activity_list_sync import DailyActivityListSyncTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_challenge import DailyChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_redpacket import DailyRedpacketTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_experience import DailyExperienceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_signin import DailySigninTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_xuanhuang import DailyXuanhuangTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daofa import DaofaTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daozu_challenge import DaozuChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_task_rewards import DailyTaskRewardsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_foundation import DailyFoundationTaskMixin
from backend.core.fanxiu.data_annotation.tasks.daily_resources import DailyResourceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.gift_code import GiftCodeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.jianling import JianlingTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianyan import XianyanTaskMixin
from backend.core.fanxiu.data_annotation.tasks.login_game import LoginGameTaskMixin
from backend.core.fanxiu.data_annotation.tasks.maintenance import MaintenanceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mail import MailTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mail_claim_law import MailClaimLawTaskMixin
from backend.core.fanxiu.data_annotation.tasks.misc_actions import MiscActionTaskMixin
from backend.core.fanxiu.data_annotation.tasks.mozu import MozuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.moyu_signup import MoyuSignupTaskMixin
from backend.core.fanxiu.data_annotation.tasks.moyu_challenge import MoyuChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.signup_misc import SignupMiscTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianfu import XianfuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.yihuo import 日常异火任务Mixin
from backend.core.fanxiu.data_annotation.tasks.zhenxie import ZhenxieTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianqiao_trial import XianqiaoTrialTaskMixin
from backend.core.fanxiu.data_annotation.tasks.weekly_hanli import WeeklyHanliTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_claim_pills import BubbleClaimPillsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_hide import BubbleHideTaskMixin
from backend.core.fanxiu.data_annotation.tasks.bubble_lifecycle import BubbleLifecycleTaskMixin
from backend.core.fanxiu.data_annotation.tasks.take_medicine_batch import TakeMedicineBatchTaskMixin
from backend.core.fanxiu.data_annotation.tasks.weekly_shengzu import WeeklyShengzuTaskMixin
from backend.core.fanxiu.data_annotation.tasks.lingquan import LingquanTaskMixin
from backend.core.fanxiu.data_annotation.tasks.lingta_challenge import LingtaChallengeTaskMixin
from backend.core.fanxiu.data_annotation.tasks.prayer_daily_resource import PrayerDailyResourceTaskMixin
from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import ResourceRankDailyGiftTaskMixin
from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import DandaoTaskRewardsTaskMixin
from backend.core.fanxiu.data_annotation.tasks.yuanding_sansheng import YuandingSanshengTaskMixin
from backend.core.fanxiu.data_annotation.tasks.xianshi_exchange import XianshiExchangeTaskMixin


class BehaviorTreeRuntimeRunner(
    SceneInterruptionMixin,
    MaintenanceTaskMixin,
    DaofaTaskMixin,
    DaozuChallengeTaskMixin,
    DailyTaskRewardsTaskMixin,
    MozuTaskMixin,
    ZhenxieTaskMixin,
    日常异火任务Mixin,
    JianlingTaskMixin,
    XianyanTaskMixin,
    LoginGameTaskMixin,
    DailyFoundationTaskMixin,
    DailyResourceTaskMixin,
    DailyActivityListSyncTaskMixin,
    DailyChallengeTaskMixin,
    DailyRedpacketTaskMixin,
    DailyExperienceTaskMixin,
    DailySigninTaskMixin,
    DailyXuanhuangTaskMixin,
    BubbleLifecycleTaskMixin,
    BubbleClaimPillsTaskMixin,
    BubbleHideTaskMixin,
    TakeMedicineBatchTaskMixin,
    WeeklyHanliTaskMixin,
    WeeklyShengzuTaskMixin,
    LingquanTaskMixin,
    LingtaChallengeTaskMixin,
    XianqiaoTrialTaskMixin,
    XianfuTaskMixin,
    MoyuSignupTaskMixin,
    MoyuChallengeTaskMixin,
    SignupMiscTaskMixin,
    GiftCodeTaskMixin,
    MiscActionTaskMixin,
    MailTaskMixin,
    MailClaimLawTaskMixin,
    PrayerDailyResourceTaskMixin,
    ResourceRankDailyGiftTaskMixin,
    DandaoTaskRewardsTaskMixin,
    YuandingSanshengTaskMixin,
    XianshiExchangeTaskMixin,
):
    default_guard_enabled = False
    default_guard_interval_seconds = 2.0
    engineering_idle_guard_interval_seconds = 60.0
    device_health_guard_interval_seconds = 60.0
    idle_recovery_interval_seconds = 300.0
    default_guard_items = {
        "device_health": {"enabled": True, "entry_id": "", "updated_at": 0.0},
    }
    guard_definitions = {
        "device_health": {
            "id": "device_health",
            "label": "设备健康",
            "default_enabled": True,
            "message": "低频检查 MuMu/安卓容器，异常时恢复模拟器和游戏",
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
        "signup": 23,
        "signup_reward": 24,
        "gift": 78,
        "reward": 81,
        "duplicated": 82,
    }
    scene_threshold = 80
    layer3_similarity_threshold = 90.0
    scene_thresholds = {"gift": 60, "daily": 60, "hide_floating": 55}
    overlay_threshold = 55

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._guard_group_enabled = True
        self._guard_enabled = self.default_guard_enabled
        self._guard_entry_id = ""
        self._guard_interval_seconds = self.default_guard_interval_seconds
        self._guard_items: dict[str, dict[str, Any]] = json.loads(json.dumps(self.default_guard_items, ensure_ascii=False))
        self._last_device_health_guard_at = 0.0
        self._auto_close_candidates_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
        self._shape_inheritance_cache: tuple[
            list[dict[str, Any]],
            ShapeInheritanceResolution,
        ] | None = None
        self._missing_match_source_filenames: set[str] = set()
        self._log_scope = ""
        self._log_item_id = ""
        self._last_status_persist_at = 0.0
        self._cell_execution_lock = threading.RLock()
        self._shared_ocr_lock = threading.RLock()
        self._navigation_random = random.Random()
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
        direction = runtime_shape.load_direction or "down"
        if reverse:
            direction = {
                "up": "down",
                "down": "up",
                "left": "right",
                "right": "left",
            }.get(str(direction).strip().lower(), "up")
        return (yield from runtime.scroll_shape_content(
            runtime_shape,
            direction=direction,
            settle_seconds=settle_seconds,
            unchanged_threshold=unchanged_threshold,
            stable_sample_count=1,
            unchanged_confirmations=1,
        ))

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
        return initial_behavior_tree_runtime_status()

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
            "logs": current_logs[-500:],
            "cell_logs": current_cell_logs[:100],
        })
        return base

    def status(self, *, include_cell_logs: bool = True) -> dict[str, Any]:
        with self._lock:
            self._sync_guard_status_locked()
            payload = self._status if include_cell_logs else {**self._status, "cell_logs": []}
            return json.loads(json.dumps(payload, ensure_ascii=False))

    def replace_logs(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            self._status["logs"] = list(logs)
            self._status["updated_at"] = time.time()
            self._sync_guard_status_locked()
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
        guard_group_running = False
        guard_running = False
        guard_items: dict[str, dict[str, Any]] = {}
        for guard_id, definition in self.guard_definitions.items():
            state = self._guard_items.get(guard_id)
            if not isinstance(state, dict):
                state = {}
            enabled = bool(state.get("enabled"))
            entry_id = str(state.get("entry_id") or "")
            running = False
            message = str(definition.get("message") or "")
            if guard_id == "device_health":
                running = False
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
            if self._guard_enabled:
                self._guard_entry_id = entry_id
            if not self._status.get("entry_id"):
                self._status["entry_id"] = entry_id
            self._set_status_locked("idle", "凡修框架已加载到 Jupyter Kernel", phase="idle")
        return self.status()

    def _restore_persisted_config_locked(self) -> None:
        if self._status.get("running"):
            return
        persisted = _read_behavior_tree_runtime_status()
        if not persisted:
            return
        normalize_behavior_tree_runtime_guard_items(persisted, self.guard_definitions)
        self._guard_group_enabled = bool(persisted.get("guard_group_enabled", True))
        self._guard_enabled = False
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

    def _run_idle_guard_tick(
        self,
        entry: Any,
        entry_id: str,
        asset_tree_path: Path,
        *,
        stop_event: threading.Event | None = None,
    ) -> bool:
        return self._run_device_health_guard_tick(entry_id)

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
        """Run low-frequency device health maintenance while no job is active."""
        self._run_device_health_guard_tick(entry_id)

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
                self._set_status_locked("idle", "当前没有正在运行的 Cell")
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
        guard_id: str = "device_health",
        asset_tree_path: Path,
    ) -> dict[str, Any]:
        guard_id = str(guard_id or "device_health").strip() or "device_health"
        if guard_id == "close_popups":
            raise ValueError("close_popups 独立弹窗守护已下线；弹窗由场景识别管线自动处理")
        if guard_id not in self.guard_definitions:
            raise ValueError(f"未知守护：{guard_id}")
        interval_seconds = max(0.5, min(30.0, float(interval_seconds or 2.0)))
        with self._lock:
            guard_item = self._guard_items.setdefault(guard_id, {})
            guard_item.update({
                "enabled": bool(enabled),
                "entry_id": entry_id if enabled else "",
                "updated_at": time.time(),
            })
            self._set_status_locked(str(self._status.get("status") or "idle"), f"守护{'已开启' if enabled else '已关闭'}：{guard_id}")
            self._sync_guard_status_locked()
            self._log_locked("info", self._status["message"], scope="guard", item_id=guard_id)
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
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
            self._log_locked("info", self._status["message"], scope="guard", item_id="guard_group")
        self.ensure_service(entry=entry, entry_id=entry_id, asset_tree_path=asset_tree_path)
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
            "current_cell_id": "",
            "interruptible": True,
        })

    def _canonical_runtime_task_type(self, task_type: str) -> str:
        return canonical_fanxiu_data_annotation_task_type(task_type)

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
        append_behavior_tree_runtime_status_log(
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
            running = bool(self._status.get("running"))
        if running:
            self._persist_status(min_interval_seconds=2.0)

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

    def _runtime_task_cell_source(self, task_type: str, payload: dict[str, Any]) -> str:
        clean_payload = {
            str(key): value
            for key, value in dict(payload or {}).items()
            if not str(key).startswith("__")
        }
        return f"run_task_cell({task_type!r}, {clean_payload!r})"

    def _append_runtime_cell_log_locked(
        self,
        *,
        title: str,
        source: str,
        logs: list[dict[str, Any]] | None = None,
        cell_id: str | None = None,
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
        cell_id = str(cell_id or "").strip() or (
            f"cell-{hashlib.sha1((title + source + str(time.time())).encode('utf-8')).hexdigest()[:16]}"
        )
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
        *,
        task_type: str,
        label: str,
        message: str,
        current_scene: int | None = 34,
    ) -> None:
        with self._lock:
            self._set_status_locked(
                "success",
                message,
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
        flow: Callable[[BehaviorTreeRuntime], Any],
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
        if isinstance(flow_result, dict) and "next_time" in flow_result:
            raise RuntimeError(
                f"{label}违反调度契约：正式 flow 不得返回 next_time，"
                "必须在业务完成点调用统一原子入口持久化"
            )
        runtime_attrs = getattr(runtime, "attrs", None)
        completion_message = str(runtime_attrs.get("completion_message") or "").strip() if isinstance(runtime_attrs, dict) else ""
        if isinstance(flow_result, dict):
            completion_message = str(flow_result.get("message") or completion_message).strip()
        self._finish_daily_runtime_task(
            task_type=task_type,
            label=label,
            message=completion_message or f"{label}完成，已回到世界",
            current_scene=flow_result.get("current_scene", 34) if isinstance(flow_result, dict) else 34,
        )
        return "success"

    def _task_cell_log_message(self, task_id: str, message: str) -> str:
        task_id = str(task_id or "").strip()
        return f"[{task_id}] {message}" if task_id else message

    def _normalize_runtime_task_result(self, value: Any) -> tuple[str, str]:
        """Normalize any ordinary business return to trigger success.

        Business success/failure is represented by the task's persisted
        ``next_time``.  Only a raised exception can make the Scheduler attempt
        fail and enter framework retry handling.
        """

        if isinstance(value, dict):
            message = str(value.get("message") or "").strip()
            return "success", message
        return "success", ""

    def _persist_status(self, *, min_interval_seconds: float = 0.0) -> None:
        now = time.monotonic()
        with self._lock:
            if min_interval_seconds > 0 and now - self._last_status_persist_at < min_interval_seconds:
                return
            self._last_status_persist_at = now
        try:
            _persist_behavior_tree_runtime_status(self.status())
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
        if task_type == "mail_selective_claim" and (payload or {}).get("observe_only"):
            label = "邮件_选择性领取"
        return label

    def _fanxiu_runtime(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path | None = None,
        frame_data_url: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> BehaviorTreeRuntime:
        if asset_tree_path is None:
            ctx_asset_tree_path = ctx.get("asset_tree_path")
            if isinstance(ctx_asset_tree_path, Path):
                asset_tree_path = ctx_asset_tree_path
        return BehaviorTreeRuntime(self, ctx, asset_tree_path=asset_tree_path, frame_data_url=frame_data_url, stop_event=stop_event)

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
            required_methods = ("cur_frame", "current_scene", "ocr_fragments", "ocr_text", "clear_frame")
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

            def ocr_fragments(self, frame_data_url: str | None = None, *, update: bool = False) -> list[dict[str, Any]]:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                return runner._recognized_scene_ocr_fragments(ctx, frame)

            def ocr_text(self, frame_data_url: str | None = None, *, update: bool = False) -> str:
                frame = frame_data_url if isinstance(frame_data_url, str) and frame_data_url else self.cur_frame(update=update)
                return runner._ocr_text(self.ocr_fragments(frame))

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
            text = runtime.ocr_text(frame) if hasattr(runtime, "ocr_text") else self._recognized_scene_ocr_text(ctx, frame, scene_ids)
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
        return self._ocr_text(self._ocr_fragments_in_shapes(frame_data_url, image, tuple(shape_titles), padding=padding))

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
            return False
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
        return BehaviorTreeStatus.SKIP

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
            return _BehaviorTreeRuntimeContainer(
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

    def _task_timeout_seconds(self, payload: dict[str, Any] | None = None) -> float | None:
        payload = payload if isinstance(payload, dict) else {}
        if bool(payload.get("unbounded_runtime")):
            return None
        raw_value = payload.get("max_runtime_seconds", payload.get("timeout_seconds", 7200))
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = 7200.0
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
        current_cell_id = f"cell-{uuid.uuid4().hex[:16]}"
        with self._lock:
            self._status["current_cell_id"] = current_cell_id
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
                    source=self._runtime_task_cell_source(task_type, payload),
                    cell_id=current_cell_id,
                )
        except InterruptedError:
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"status": "stopped", "phase": "stopped", "message": "已停止", "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("stop", "任务已停止")
                self._append_runtime_cell_log_locked(
                    title=f"执行任务：{self._runtime_task_label(task_type, payload)}",
                    source=self._runtime_task_cell_source(task_type, payload),
                    cell_id=current_cell_id,
                )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            with self._lock:
                self._clear_current_task_locked()
                self._status.update({"ok": False, "status": "error", "phase": "error", "message": str(detail), "error": str(detail), "finished_at": time.time(), "updated_at": time.time()})
                self._log_locked("error", str(detail))
                self._append_runtime_cell_log_locked(
                    title=f"执行任务：{self._runtime_task_label(task_type, payload)}",
                    source=self._runtime_task_cell_source(task_type, payload),
                    cell_id=current_cell_id,
                )
        finally:
            if previous_log_context is not None:
                self._restore_log_context(previous_log_context)
            self._persist_status()

    def _cleanup_failed_scheduler_task_to_scene(
        self,
        *,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        task_label: str,
        target_scene_id: int,
    ) -> bool:
        """Best-effort atomic cleanup after a Scheduler task fails.

        A fresh stop event is deliberate: a normal task exception should still
        return the game to the task's explicitly declared failure anchor.
        """
        target_scene_id = int(target_scene_id)
        cleanup_stop_event = threading.Event()
        with self._lock:
            self._set_status_locked(
                "running",
                f"{task_label}：任务失败，收尾返回锚点 #{target_scene_id}",
                phase="scheduler_failure_cleanup",
            )
            self._log_locked("action", f"{task_label}：任务失败，通用场景规划收尾到 #{target_scene_id}")
        self._persist_status()

        def target_confirmed() -> tuple[bool, float]:
            """Require the failure anchor on two independent fresh frames.

            Failure cleanup must not report success from one transient or stale
            scene match: that leaves the next Scheduler task inside the failed
            task's business page.  The focused Layer 0 probe is intentionally
            repeated after clearing the Runtime frame cache.
            """

            runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=cleanup_stop_event)
            last_score = 0.0
            for probe_index in range(2):
                scene_id, score, _frame = runtime.current_scene([target_scene_id], update=True)
                last_score = float(score or 0.0)
                if scene_id != target_scene_id or not self._scene_matches_id(target_scene_id, last_score):
                    return False, last_score
                if probe_index == 0 and cleanup_stop_event.wait(0.35):
                    self._raise_if_stopped(cleanup_stop_event)
            return True, last_score

        def run_cleanup_route() -> None:
            self._run_direct_runtime_action(
                lambda: self._go_scene_task(
                    ctx,
                    asset_tree_path,
                    target_scene_id,
                    cleanup_stop_event,
                ),
                stop_event=cleanup_stop_event,
                max_runtime_seconds=120.0,
            )

        try:
            run_cleanup_route()
            confirmed, score = target_confirmed()
            if not confirmed:
                with self._lock:
                    self._log_locked(
                        "warning",
                        f"{task_label}：首次失败收尾未连续确认 #{target_scene_id}，重新识别并规划",
                    )
                run_cleanup_route()
                confirmed, score = target_confirmed()
            if not confirmed:
                raise RuntimeError(f"失败收尾未连续确认 #{target_scene_id}")
        except Exception as cleanup_exc:
            try:
                confirmed, score = target_confirmed()
            except Exception:
                confirmed, score = False, 0.0
            if confirmed:
                with self._lock:
                    self._status.update({"current_scene": target_scene_id, "updated_at": time.time()})
                    self._log_locked(
                        "success",
                        f"{task_label}：失败收尾已到 #{target_scene_id} {score:.0f}%（来源 shape 落点未声明，但目标锚点已可靠确认）",
                    )
                return True
            with self._lock:
                self._log_locked("warning", f"{task_label}：失败收尾未到 #{target_scene_id}：{cleanup_exc}")
            return False
        with self._lock:
            self._log_locked("success", f"{task_label}：失败收尾已连续确认 #{target_scene_id} {score:.0f}%")
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
        announcement_scene_id, _announcement_score = self._identify_scene_number(
            ctx,
            frame,
            [14],
        )
        announcement_is_formal_scene = bool(
            announcement_scene_id == 14
            and str(ctx.get("_last_scene_recognition_status") or "") != "startup_ocr"
        )
        if str(ctx.get("_last_scene_recognition_status") or "") == "startup_ocr":
            return None
        if announcement_is_formal_scene:
            image = self._find_asset_image_by_title(ctx, "游戏公告")
            close_shape = self._known_game_announcement_action_shape(image)
            if close_shape is None:
                return {
                    "scene_id": 14,
                    "title": "游戏公告",
                    "blocking": True,
                    "all_shapes": shape_titles(image),
                    "message": "检测到游戏公告遮挡；资产树「游戏公告」缺少「关闭公告」动作标注，无法安全进入游戏",
                }
            return {
                "scene_id": 14,
                "title": "游戏公告",
                "blocking": False,
                "all_shapes": shape_titles(image),
                "action_shapes": [str(close_shape.get("title") or "")],
                "message": "检测到游戏公告遮挡，已有安全关闭动作标注",
            }
        purchase_scene_id, _purchase_score = self._identify_scene_number(ctx, frame, [224, 225])
        if purchase_scene_id in {224, 225}:
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

    def _set_scheduler_task_next_time(
        self,
        task_id: str,
        next_time_text: str | None,
    ) -> None:
        """Persist the absolute next run selected by the current job.

        Standard scheduled-job pattern:

        1. Inspect all business facts available to this run.
        2. Choose the next absolute time for every normal branch, including
           cooldown, already-complete and temporarily-unreachable branches.
        3. Call this method at the business decision point, then
           return ``success``.  A business action not being completed is not a
           scheduler failure when the job handled it and planned a revisit.
        4. Raise only when execution itself is broken or interrupted.  The
           external Scheduler then keeps the job retryable and applies its
           configured error delay; the next attempt never continues this run mid-flow.

        Passing ``None`` deliberately makes a job dormant until explicitly
        scheduled again.  Do not infer recurrence from ``trigger_description``.
        """
        task_id = str(task_id or "").strip()
        if not task_id:
            return
        try:
            set_data_annotation_scheduler_task_trigger_time(task_id, next_time_text)
        except LookupError:
            # A plain debug Cell may not correspond to a persisted task
            # instance. Debugging must not create hidden scheduling state.
            return

    def _persist_scheduler_task_next_time(
        self,
        task_id: str,
        next_time_text: str | None,
    ) -> None:
        """Strict Job completion-point write; failure prevents a success return."""

        task_id = str(task_id or "").strip()
        if not task_id:
            raise RuntimeError("业务写入 next_time 时缺少 Scheduler Job id")
        try:
            set_data_annotation_scheduler_task_trigger_time(task_id, next_time_text)
        except LookupError as exc:
            raise RuntimeError(f"业务写入 next_time 失败：Scheduler 中不存在 Job {task_id!r}") from exc

    def _persist_admission_decision(
        self,
        payload: dict[str, Any] | None,
        decision: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Consume a pure admission decision inside the Job business domain.

        The returned admission payload intentionally cannot transport
        ``next_time`` to the Cell or Scheduler.
        """

        if decision is None:
            return None
        normalized = dict(decision)
        if "next_time" not in normalized:
            raise RuntimeError("正常结束的作业准入决策缺少 next_time")
        next_time = normalized.pop("next_time")
        task_id = str((payload or {}).get("__scheduler_task_id") or "").strip()
        if not task_id:
            raise RuntimeError("作业准入写入 next_time 时缺少 __scheduler_task_id")
        self._persist_scheduler_task_next_time(
            task_id,
            str(next_time) if next_time is not None else None,
        )
        return normalized

    def _clear_scheduler_task_payload_flag(self, task_id: str, flag: str) -> None:
        task_id = str(task_id or "").strip()
        flag = str(flag or "").strip()
        if not task_id or not flag:
            return
        from backend.core.fanxiu.data_annotation.behavior_tree_control import (
            read_scheduler_tasks,
            update_scheduler_tasks,
        )

        tasks = read_scheduler_tasks(
            scheduler_state_path=_data_annotation_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            now=_now(),
        )
        task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
        if not isinstance(task, dict):
            return
        payload = dict(task.get("payload") or {})
        if flag not in payload:
            return
        payload.pop(flag, None)
        update_scheduler_tasks(
            [{"id": task_id, "payload": payload}],
            scheduler_state_path=_data_annotation_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            now=_now(),
        )

    def _get_scheduler_task_payload_flag(self, task_id: str, flag: str) -> Any:
        """Read one Job-owned idempotency fact from the authoritative task store."""

        task_id = str(task_id or "").strip()
        flag = str(flag or "").strip()
        if not task_id or not flag:
            return None
        from backend.core.fanxiu.data_annotation.behavior_tree_control import (
            read_scheduler_tasks,
        )

        tasks = read_scheduler_tasks(
            scheduler_state_path=_data_annotation_scheduler_state_path(),
            world_facts_path=_data_annotation_world_facts_path(),
            now=_now(),
        )
        task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
        if not isinstance(task, dict):
            return None
        return dict(task.get("payload") or {}).get(flag)

    def _set_scheduler_task_payload_flag(self, task_id: str, flag: str, value: Any) -> bool:
        """Persist a narrow idempotency flag via the atomic task store."""
        task_id = str(task_id or "").strip()
        flag = str(flag or "").strip()
        if not task_id or not flag:
            return False
        from backend.core.fanxiu.data_annotation.behavior_tree_control import (
            read_scheduler_tasks,
            update_scheduler_tasks,
        )

        try:
            scheduler_state_path = _data_annotation_scheduler_state_path()
            world_facts_path = _data_annotation_world_facts_path()
            now = _now()
            tasks = read_scheduler_tasks(
                scheduler_state_path=scheduler_state_path,
                world_facts_path=world_facts_path,
                now=now,
            )
            task = next((item for item in tasks if str(item.get("id") or "") == task_id), None)
            if not isinstance(task, dict):
                return False
            payload = dict(task.get("payload") or {})
            payload[flag] = value
            persisted = update_scheduler_tasks(
                [{"id": task_id, "payload": payload}],
                scheduler_state_path=scheduler_state_path,
                world_facts_path=world_facts_path,
                now=now,
            )
        except Exception:
            return False
        persisted_task = next(
            (item for item in persisted if str(item.get("id") or "") == task_id),
            None,
        )
        return bool(
            isinstance(persisted_task, dict)
            and dict(persisted_task.get("payload") or {}).get(flag) == value
        )

    def _schedule_login_job_first(self) -> str | None:
        """Atomically place login before every currently materialized Job time."""

        # Keep Scheduler persistence owned by behavior_tree_control and import lazily
        # so the long-lived Runtime can still be imported during bootstrap.
        from backend.core.fanxiu.data_annotation.behavior_tree_control import (
            schedule_login_job_first,
        )

        return schedule_login_job_first(
            scheduler_state_path=_data_annotation_scheduler_state_path(),
            now=_now(),
        )

    def _execute_runtime_task(self, ctx: dict[str, Any], task_type: str, payload: dict[str, Any], stop_event: threading.Event) -> Any:
        task_type = self._canonical_runtime_task_type(task_type)
        ensure_behavior_tree_runtime_jobs_registered()
        definition = _data_annotation_task_cell_definition(task_type)
        if definition is None:
            raise RuntimeError(f"暂不支持的任务类型：{task_type}")
        normalized_payload = dict(payload or {})
        if definition.normalize_payload is not None:
            normalized_payload = definition.normalize_payload(normalized_payload)
        with runtime_task_payload(ctx, normalized_payload):
            result = definition.handler(self, ctx, normalized_payload, stop_event)
            if not isinstance(result, GeneratorType):
                return str(result or "success")

        def run_generator():
            # A generator function does not execute its body when constructed.
            # Re-enter the same task scope while it is actually consumed, and
            # restore the context on normal return, error, or interruption.
            with runtime_task_payload(ctx, normalized_payload):
                return (yield from result)

        return run_generator()

    def _try_enter_daily_youli_from_world_mainline(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
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
        text = self._recognized_scene_ocr_text(ctx, frame, [228, 71])
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
        tokens = self._ocr_tokens_in_scene_shapes(ctx, frame, image71)
        fragments = group_ocr_tokens(tokens)
        width, height = self._frame_size(image71)
        min_y = height * float(payload.get("youli_menu_min_y_ratio") or 0.62)
        candidates: list[tuple[float, float, str]] = []
        for fragment in fragments:
            text = _sanitize_ocr_text(fragment.get("text"))
            if "游历" not in text:
                continue
            target_box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], "游历")
            if target_box is None:
                continue
            cx = float(target_box["x"]) + float(target_box["w"]) / 2
            cy = float(target_box["y"]) + float(target_box["h"]) / 2
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
        return (
            "秘藏阁" in normalized
            and "仙币" in normalized
            and any(fragment in normalized for fragment in ("天衍灵石", "兑换所需", "限购"))
        )

    def _daily_xianshi_text_is_box_detail(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "宝匣" in normalized and any(fragment in normalized for fragment in ("领取", "打开可获得", "兑换"))

    def _record_daily_xianshi_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_boss_reset_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-xianshi"),
            next_time,
        )
        self._log("success", f"仙市_秘藏阁：{message}，下次 {next_time}")
        return next_time

    def _schedule_daily_xianshi_next_check(self, payload: dict[str, Any], *, message: str, seconds: int) -> str:
        task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-xianshi").strip() or "legacy-daily-xianshi"
        next_time = (_now() + timedelta(seconds=max(60, int(seconds)))).strftime("%Y-%m-%d %H:%M:%S")
        self._persist_scheduler_task_next_time(
            task_id,
            next_time,
        )
        self._log("skip", f"仙市_秘藏阁：{message}，下次 {next_time}")
        return next_time

    def _record_daily_vip_done(self, payload: dict[str, Any], *, message: str) -> str:
        next_time = self._next_daily_vip_reset_time_text()
        self._persist_scheduler_task_next_time(
            str(payload.get("__scheduler_task_id") or "legacy-daily-vip"),
            next_time,
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
        tokens = runtime.ocr_tokens(frame)
        for fragment in group_ocr_tokens(tokens):
            fragment_text = _sanitize_ocr_text(fragment.get("text"))
            if "世界" not in fragment_text:
                continue
            target_box = locate_text_box(query_spatial_ocr(tokens, fragment)["tokens"], "世界")
            if target_box is None:
                continue
            cx = float(target_box["x"]) + float(target_box["w"]) / 2
            cy = float(target_box["y"]) + float(target_box["h"]) / 2
            if cx <= width * 0.22 and cy >= height * 0.72:
                candidates.append((float(cx), float(cy), fragment_text))
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
            text = runtime.ocr_text(update=True)
            if (
                not self._daily_xianshi_text_is_box_detail(text)
                or not self._daily_xianshi_text_indicates_no_free_coin_box(text)
            ):
                raise RuntimeError(
                    f"{task_label}：#250 未匹配「领取」，且新鲜 OCR 未同时证明商品详情与非免费状态，"
                    f"拒绝按已领取收尾；OCR={text[:120]}"
                ) from exc
            self._log(
                "success",
                f"{task_label}：#250 未匹配「领取」，详情 OCR 已证明当前宝匣需要付费，视为今日已无可领免费项",
            )
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
        if "免费" in normalized or "领取" in normalized:
            return False
        return "宝匣" in normalized and any(fragment in normalized for fragment in ("兑换", "价格", "所需"))

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
        # Paid box details use scene #316 while the free detail was originally
        # annotated as #250.  Both share the global bottom-left return control;
        # using #424 avoids ever touching the paid "兑换" action.
        runtime.click_shape_center(424, "返回")
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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        return (yield from runtime.wait_scene(
            217,
            timeout=float(payload.get("invite_timeout") or 12.0),
            label="日常_双修：等待邀请页 #217",
        ))

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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        yield from runtime.wait_scene(
            218,
            timeout=float(payload.get("xianyuan_list_timeout") or 12.0),
            label="日常_双修：等待仙缘邀请列表 #218",
        )
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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        return (yield from runtime.wait_scene(
            219,
            timeout=float(payload.get("training_ready_timeout") or 12.0),
            label="日常_双修：等待修炼准备页 #219",
        ))

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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        return (yield from runtime.wait_scene(
            221,
            timeout=float(payload.get("training_complete_timeout") or 18.0),
            label="日常_双修：等待修炼完成页 #221",
        ))

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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        scene_id = yield from runtime.wait_scene(
            34,
            289,
            86,
            219,
            timeout=float(payload.get("after_complete_timeout") or 8.0),
            label="日常_双修：等待修炼完成后的正式落点",
        )
        if isinstance(scene_id, View):
            scene_id = scene_id.id
        if scene_id == 34:
            return self._complete_daily_shuangxiu_after_continue(current_scene=34)
        if scene_id in {289, 86}:
            yield from self._confirm_daily_shuangxiu_leave(ctx, stop_event, payload, scene_id=int(scene_id))
        elif scene_id == 219:
            yield from self._leave_daily_shuangxiu_training_ready(ctx, stop_event, payload)
        else:
            raise RuntimeError(f"日常_双修：修炼完成后的落点 #{scene_id} 尚未实现")
        yield from runtime.wait_scene(
            34,
            timeout=float(payload.get("after_leave_world_timeout") or 12.0),
            label="日常_双修：等待离开后回到世界 #34",
        )
        return self._complete_daily_shuangxiu_after_continue(current_scene=34)

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
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        scene_id = yield from runtime.wait_scene(
            289,
            86,
            34,
            timeout=float(payload.get("leave_result_timeout") or 8.0),
            label="日常_双修：等待离开确认或世界",
        )
        if isinstance(scene_id, View):
            scene_id = scene_id.id
        if scene_id in {289, 86}:
            yield from self._confirm_daily_shuangxiu_leave(ctx, stop_event, payload, scene_id=int(scene_id))

    def _confirm_daily_shuangxiu_leave(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any],
        *,
        scene_id: int | None = None,
    ):
        if scene_id not in {289, 86}:
            raise RuntimeError("日常_双修：未识别到正式离开确认场景 #289/#86，禁止借用确认坐标")
        confirm_id = int(scene_id)
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
        runtime: BehaviorTreeRuntime,
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
            text = self._recognized_scene_ocr_text(ctx, frame, [184])
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
        detail_text = self._recognized_scene_ocr_text(ctx, frame, [184])
        if self._daily_lingzu_remaining_zero(detail_text):
            self._record_daily_lingzu_done(payload, message="详情页显示今日剩余次数 0/1")
            return "done"
        return "open"







    def _return_xianfu_learn_skill_to_world(self, runtime: BehaviorTreeRuntime):
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：返回世界 #34", phase="xianfu_skill_return_world")
            self._log_locked("action", "仙府_领悟绝技：按仙府收尾链路返回 #34")
        yield from self._return_xianfu_pages_to_world(
            runtime,
            task_label="仙府_领悟绝技",
            current_candidates=(177, 176, 172, 171, 86, 34),
        )
        return "success"

    def _ensure_xianfu_learn_skill_xianpin_tab(self, runtime: BehaviorTreeRuntime, image176: dict[str, Any]):
        frame = runtime.cur_frame(update=True)
        status_text = self._ocr_text(self._ocr_fragments_in_shapes(frame, image176, ("状态", "价格"), padding=16))
        if _parse_xianfu_skill_cd_seconds(status_text) is not None:
            self._log("detail", f"仙府_领悟绝技：当前绝技页状态区已可读，跳过重复切换仙品绝技：{status_text}")
            return frame
        yield from self._switch_xianfu_learn_skill_xianpin_tab(runtime)
        return runtime.cur_frame(update=True)

    def _switch_xianfu_learn_skill_xianpin_tab(self, runtime: BehaviorTreeRuntime):
        view176 = runtime.get_view(176)
        tab_shape = view176.get_shape("仙品绝技") if isinstance(view176, View) else None
        if tab_shape is None:
            raise RuntimeError("缺少 #176「仙品绝技」标注，无法切换到仙品绝技读取 CD")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：切换仙品绝技", phase="xianfu_skill_open_xianpin", current_scene=176)
            self._log_locked("action", "仙府_领悟绝技：点击 #176「仙品绝技」")
        tab_shape.click(runtime)
        yield from runtime.wait_view(176, timeout=5.0, label="仙府_领悟绝技：等待仙品绝技 #176")

    def _handle_xianfu_learn_skill_result_popup(
        self,
        runtime: BehaviorTreeRuntime,
        *,
        refresh_reference_frame_once: bool = False,
        scheduler_task_id: str = "",
    ):
        view177 = runtime.get_view(177)
        if not isinstance(view177, View):
            raise RuntimeError("缺少 #177「领悟绝技」结果弹窗标注，无法继续")
        yield from runtime.wait_view(177, timeout=18.0, label="仙府_领悟绝技：等待结果弹窗 #177")
        if refresh_reference_frame_once:
            frame_data_url = runtime.cur_frame()
            evidence_dir = self._refresh_scene_reference_frame(runtime, 177, frame_data_url)
            self._clear_scheduler_task_payload_flag(
                scheduler_task_id,
                "refresh_scene_177_reference_once",
            )
            self._log(
                "success",
                f"仙府_领悟绝技：已用本次真实 #177 画面重置参考帧；原图备份={evidence_dir}",
            )
        continue_shape = view177.get_shape("继续")
        if continue_shape is None:
            raise RuntimeError("缺少 #177「继续」标注，无法关闭领悟结果")
        with self._lock:
            self._set_status_locked("running", "仙府_领悟绝技：关闭结果弹窗", phase="xianfu_skill_continue", current_scene=177)
            self._log_locked("action", "仙府_领悟绝技：点击 #177「继续」")
        continue_shape.click(runtime)
        yield from runtime.wait_view(176, timeout=18.0, label="仙府_领悟绝技：返回绝技 #176")
        return "success"

    def _refresh_scene_reference_frame(
        self,
        runtime: BehaviorTreeRuntime,
        scene_id: int,
        frame_data_url: str,
    ) -> Path:
        view = runtime.get_view(scene_id)
        image = view.raw if isinstance(view, View) and isinstance(view.raw, dict) else None
        filename = str((image or {}).get("filename") or "").strip()
        asset_tree_path = runtime.asset_tree_path or runtime.ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path) or not filename:
            raise RuntimeError(f"缺少 #{scene_id} 参考帧路径，无法重置")
        image_path = asset_tree_path.parent / "images" / Path(filename).name
        if not image_path.is_file():
            raise RuntimeError(f"#{scene_id} 参考帧不存在：{image_path}")

        raw = self._decode_frame_data_url(frame_data_url)
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as source:
            converted = source.convert("RGB") if image_path.suffix.lower() in {".jpg", ".jpeg"} else source.convert("RGBA")
            width, height = converted.size
            expected_size = (int((image or {}).get("width") or 0), int((image or {}).get("height") or 0))
            if all(expected_size) and (width, height) != expected_size:
                raise RuntimeError(
                    f"#{scene_id} 直播帧尺寸 {width}x{height} 与标注尺寸 {expected_size[0]}x{expected_size[1]} 不一致，拒绝重置"
                )
            buffer = io.BytesIO()
            converted.save(buffer, format="JPEG" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "PNG", quality=95)

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        evidence_dir = asset_tree_path.parent / "recognition-ops" / "reference-refresh" / f"scene-{scene_id}-{stamp}"
        evidence_dir.mkdir(parents=True, exist_ok=False)
        backup_path = evidence_dir / f"before-{image_path.name}"
        shutil.copy2(image_path, backup_path)
        summary = {
            "scene_id": int(scene_id),
            "title": str((image or {}).get("title") or ""),
            "filename": image_path.name,
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "preserved_fields": ["id", "title", "filename", "shapes", "children"],
            "shape_count": len((image or {}).get("shapes") or []),
            "backup_path": os.fspath(backup_path),
            "captured_size": [int(width), int(height)],
        }
        _write_data_annotation_json(evidence_dir / "summary.json", summary)

        replacement = buffer.getvalue()
        temporary = image_path.with_name(f".{image_path.name}.{hashlib.sha256(replacement).hexdigest()[:12]}.tmp")
        temporary.write_bytes(replacement)
        temporary.replace(image_path)
        return evidence_dir

    def _record_daily_signup_done(self, payload: dict[str, Any] | None, *, message: str) -> str:
        payload = dict(payload or {})
        next_time = self._next_daily_boss_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-signup")
        self._persist_scheduler_task_next_time(
            scheduler_task_id,
            next_time,
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
        return dict(self._shape_inheritance_resolution(nodes).images)

    def _shape_inheritance_resolution(
        self,
        nodes: list[dict[str, Any]],
    ) -> ShapeInheritanceResolution:
        cached = self._shape_inheritance_cache
        if cached is not None:
            raw_tree, resolution = cached
            if nodes is raw_tree or nodes is resolution.tree:
                return resolution
        resolution = resolve_shape_inheritance(nodes)
        self._shape_inheritance_cache = (nodes, resolution)
        return resolution

    def _resolved_asset_tree(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._shape_inheritance_resolution(nodes).tree

    def _invalidate_shape_inheritance_cache(self) -> None:
        self._shape_inheritance_cache = None

    def _invalidate_asset_derived_caches(self, asset_tree_path: Path) -> None:
        """Invalidate runner-global values derived from one asset snapshot."""

        self._invalidate_shape_inheritance_cache()
        self._auto_close_candidates_cache.pop(str(asset_tree_path), None)
        self._missing_match_source_filenames.clear()

    @staticmethod
    def _publish_asset_ctx_revision(ctx: dict[str, Any], revision: str) -> None:
        """Advance one ctx snapshot after an in-place persisted asset mutation."""

        ctx["asset_tree_revision"] = str(revision or "")
        ctx["asset_tree_generation"] = int(ctx.get("asset_tree_generation") or 0) + 1
        for key in (
            "_scene_graph_relation_cache",
            "_scene_discriminator_groups",
            "_scene_discriminator_score_cache",
        ):
            ctx.pop(key, None)

    def _jump_target_text(self, shape: dict[str, Any]) -> str:
        return SceneNavigator([]).jump_target_text(shape)

    def _parse_scene_jump_entries(self, value: Any) -> list[dict[str, Any]]:
        return SceneNavigator([]).parse_scene_jump_entries(value)

    def _serialize_scene_jump_entries(self, entries: list[dict[str, Any]]) -> str:
        return SceneNavigator([]).serialize_scene_jump_entries(entries)

    def _increment_scene_jump_target(self, shape: dict[str, Any], target_scene_id: int) -> bool:
        """Record one real landing in the shape's shared jump history.

        ``sceneJumpTarget`` is an observed-destination frequency table used as
        routing prior, *not* an allow-list.  Therefore a newly recognized,
        already-annotated scene must be appended here.  Do not simplify this
        back to ``SceneNavigator.increment_scene_jump_target``: that helper only
        increments labels already present and would silently discard new D
        landings.  Do not split new values into ``observedLanding`` either.
        """
        navigator = SceneNavigator([])
        current_text = navigator.jump_target_text(shape)
        if current_text in {"-1", "0"}:
            return False
        entries = navigator.parse_scene_jump_entries(current_text)
        target_label = str(int(target_scene_id))
        for entry in entries:
            if navigator.scene_jump_label_number(entry.get("label")) == int(target_scene_id):
                entry["count"] = int(entry.get("count") or 0) + 1
                break
        else:
            entries.append({"label": target_label, "count": 1})
        serialized = navigator.serialize_scene_jump_entries(entries)
        if serialized == current_text:
            return False
        shape["sceneJumpTarget"] = serialized
        return True

    def _record_scene_jump_landing(
        self,
        ctx: dict[str, Any],
        asset_tree_path: Path,
        tree: list[dict[str, Any]],
        shape: dict[str, Any],
        target_scene_id: int,
        *,
        reason: str,
    ) -> None:
        resolution = self._shape_inheritance_resolution(tree)
        raw_shape = find_raw_shape_for_effective(resolution.raw_images, shape)
        target_shape = raw_shape if raw_shape is not None else shape
        if self._increment_scene_jump_target(target_shape, target_scene_id):
            shape_id = str(target_shape.get("id") or "").strip()
            scene_jump_target = str(target_shape.get("sceneJumpTarget") or "")

            def update_latest(items: list[dict[str, Any]]) -> bool:
                def visit(nodes: Any) -> bool:
                    if not isinstance(nodes, list):
                        return False
                    for node in nodes:
                        if not isinstance(node, dict):
                            continue
                        shapes = node.get("shapes")
                        if isinstance(shapes, list) and visit_shapes(shapes):
                            return True
                        if visit(node.get("children")):
                            return True
                    return False

                def visit_shapes(shapes: list[Any]) -> bool:
                    for candidate in shapes:
                        if not isinstance(candidate, dict):
                            continue
                        if str(candidate.get("id") or "").strip() == shape_id:
                            candidate["sceneJumpTarget"] = scene_jump_target
                            return True
                        children = candidate.get("children")
                        if isinstance(children, list) and visit_shapes(children):
                            return True
                    return False

                return bool(shape_id and visit(items))

            snapshot = update_data_annotation_asset_tree(asset_tree_path, update_latest)
            tree[:] = snapshot.tree
            ctx["asset_tree"] = tree
            self._invalidate_asset_derived_caches(asset_tree_path)
            ctx["images"] = self._index_images(tree)
            self._publish_asset_ctx_revision(ctx, snapshot.revision)
            self._log(
                "detail",
                f"场景跳转历史：记录「{target_shape.get('title') or '未命名'}」落点 #{target_scene_id}（{reason}）",
            )

    def _scene_jump_label_number(self, label: Any) -> int | None:
        return SceneNavigator([]).scene_jump_label_number(label)

    def _resolve_scene_jump_label(self, tree: list[dict[str, Any]], label: Any) -> list[int]:
        return SceneNavigator(self._resolved_asset_tree(tree)).resolve_scene_jump_label(label)

    def _scene_jump_target_ids(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> list[int]:
        return SceneNavigator(self._resolved_asset_tree(tree)).scene_jump_target_ids(shape)

    def _resolve_scene_image_title_ids(self, tree: list[dict[str, Any]], title: str) -> list[int]:
        tree = self._resolved_asset_tree(tree)
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

    def _scene_id_key(self, scene_id: int) -> str:
        for key, value in self.scene_ids.items():
            if int(value) == int(scene_id):
                return key
        return str(scene_id)

    def _scene_match_threshold(self, scene_id: int) -> float:
        key = self._scene_id_key(scene_id)
        return float(self.scene_thresholds.get(key, self.scene_threshold))

    def _scene_matches_id(self, scene_id: int, score: float) -> bool:
        return float(score) >= self._scene_match_threshold(scene_id)

    def _layer3_match_threshold(self, image: dict[str, Any]) -> float:
        configured = image.get("layer3SimilarityThreshold")
        if configured not in (None, ""):
            try:
                return max(0.0, min(100.0, float(configured)))
            except (TypeError, ValueError):
                pass
        return float(self.layer3_similarity_threshold)

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
            "asset_tree_revision": str(ctx.get("asset_tree_revision") or ""),
            "asset_tree_generation": int(ctx.get("asset_tree_generation") or 0),
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
        # Keep one fact frame hot while evaluating every reference rule.  Scene
        # scoring shares OCR by current frame through ctx["_ocr_tokens_cache"];
        # iterating references first would cycle through every fact image and
        # evict/expire that OCR result before the next OCR rule can reuse it.
        # With facts outermost, a full matrix performs at most one OCR pass per
        # fact image instead of one OCR pass per OCR-reference/fact pair.
        for fact_id in scene_ids:
            for reference_id in scene_ids:
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

    def _runtime_graph_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            return default_recognition_candidate_ids(tree, images)
        runtime_ids = [
            int(scene_id)
            for scene_id in self._runtime_scene_candidate_ids(ctx)
            if isinstance(images.get(int(scene_id)), dict)
        ]
        if runtime_ids:
            return runtime_ids
        return [
            int(scene_id)
            for scene_id, image in images.items()
            if isinstance(image, dict)
            and int(View(image).layer) <= 2
        ]

    def _runtime_graph_scene_candidate_layers(self, ctx: dict[str, Any]) -> list[tuple[int, list[int]]]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            return [
                *default_recognition_candidate_layers(tree, images),
                (3, layer3_recognition_candidate_ids(tree, images)),
            ]
        candidate_ids = self._runtime_graph_scene_candidate_ids(ctx)
        return [
            (
                layer,
                [
                    int(scene_id)
                    for scene_id in candidate_ids
                    if isinstance(images.get(int(scene_id)), dict)
                    and int(View(images[int(scene_id)]).layer) == layer
                ],
            )
            for layer in (1, 2, 3)
        ]

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
        relation_cache = ctx.setdefault("_scene_graph_relation_cache", {})
        if not isinstance(relation_cache, dict):
            relation_cache = {}
            ctx["_scene_graph_relation_cache"] = relation_cache
        edges: list[dict[str, Any]] = []
        for reference_id in ids:
            for fact_id in ids:
                if int(reference_id) == int(fact_id):
                    continue
                relation_key = self._scene_match_cache_key(
                    ctx,
                    [int(reference_id), int(fact_id)],
                    threshold=None,
                )
                cached_relation = relation_cache.get(relation_key)
                if isinstance(cached_relation, dict):
                    result = cached_relation
                    if bool(result.get("matched")):
                        edges.append(result)
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
                relation_cache[relation_key] = result
                if bool(result.get("matched")):
                    edges.append(result)
        return edges

    def _scene_reference_similarity(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
    ) -> float | None:
        """Compare the stored scene frame with the current fact frame."""

        try:
            reference_frame = self._scene_frame_data_url_from_reference(ctx, image)
        except Exception:
            return None
        return _image_similarity_percent(self, reference_frame, frame_data_url)

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
        recognition_layers: list[tuple[str, list[int]]] = []
        if preferred_scene_ids is not None:
            candidate_ids = [
                int(scene_id)
                for scene_id in preferred_scene_ids
                if int(scene_id) in images
                and isinstance(images.get(int(scene_id)), dict)
                and self._image_layer(images[int(scene_id)]) <= 2
                and bool(self._scene_identity_shapes(images[int(scene_id)]))
            ]
            recognition_layers.append(("layer0", candidate_ids))
        if preferred_scene_ids is None:
            popup_scene_ids = self._runtime_popup_scene_candidate_ids(ctx)
            recognition_layers.extend(
                (
                    f"layer{layer}",
                    list(dict.fromkeys([
                        *candidate_ids,
                        *(popup_scene_ids if layer in {1, 2} else []),
                    ])),
                )
                for layer, candidate_ids in self._runtime_graph_scene_candidate_layers(ctx)
            )

        best_miss_score = 0.0
        evaluated_scene_ids: set[int] = set()
        for layer_label, candidate_ids in recognition_layers:
            layer_scene_ids = [
                int(scene_id)
                for scene_id in candidate_ids
                if int(scene_id) not in evaluated_scene_ids
            ]
            evaluated_scene_ids.update(layer_scene_ids)
            if layer_label == "layer3":
                scene_id, score, status = self._identify_scene_number_in_layer3_candidates(
                    ctx,
                    frame_data_url,
                    layer_scene_ids,
                    trace=trace,
                )
            else:
                scene_id, score, status = self._identify_scene_number_in_graph_candidates(
                    ctx,
                    frame_data_url,
                    layer_scene_ids,
                    layer_label=layer_label,
                    trace=trace,
                )
            best_miss_score = max(best_miss_score, float(score or 0.0))
            if status not in {"no_candidates", "no_match"}:
                return scene_id, score, status
        return None, best_miss_score, "no_match"

    def _identify_scene_number_in_layer3_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        *,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float, str]:
        """Rank identity-free Layer 3 references without producing a scene id.

        Layer 3 is diagnostic evidence for an unresolved frame.  Full-frame
        similarity can explain what an unknown frame resembles, but without a
        scene identity it cannot establish where navigation currently is.
        """

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        scene_ids = [
            scene_id
            for scene_id in list(dict.fromkeys(int(item) for item in candidate_scene_ids))
            if isinstance(images.get(scene_id), dict)
            and not self._scene_identity_shapes(images[scene_id])
        ]
        if not scene_ids:
            return None, 0.0, "no_candidates"

        def similarity(scene_id: int) -> float:
            value = self._scene_reference_similarity(ctx, images[scene_id], frame_data_url)
            return float(value or 0.0)

        workers = min(len(scene_ids), 32)
        if len(scene_ids) == 1:
            scores = [similarity(scene_ids[0])]
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-layer3-match") as executor:
                scores = list(executor.map(similarity, scene_ids))

        ranked = sorted(zip(scene_ids, scores), key=lambda item: item[1], reverse=True)
        best_reference_id, best_score = ranked[0]
        auxiliary = {
            "reference_id": int(best_reference_id),
            "score": round(float(best_score), 3),
            "threshold": round(float(self._layer3_match_threshold(images[best_reference_id])), 3),
            "above_threshold": bool(
                best_score >= self._layer3_match_threshold(images[best_reference_id])
            ),
        }
        ctx["_last_layer3_auxiliary"] = auxiliary
        if trace is not None:
            trace.append({
                "event": "layer3_auxiliary",
                **auxiliary,
            })
        return None, best_score, "no_match"

    def _scene_candidate_scores_parallel(
        self,
        ctx: dict[str, Any],
        images: dict[int, dict[str, Any]],
        scene_ids: list[int],
        frame_data_url: str,
    ) -> list[float]:
        def score(scene_id: int) -> float:
            image = images.get(int(scene_id))
            if not isinstance(image, dict):
                return 0.0
            return float(self._scene_score(ctx, image, frame_data_url) or 0.0)

        if len(scene_ids) <= 1:
            return [score(scene_id) for scene_id in scene_ids]
        workers = min(len(scene_ids), 32)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-scene-match") as executor:
            return list(executor.map(score, scene_ids))

    def _identify_scene_number_in_graph_candidates(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        candidate_scene_ids: list[int],
        *,
        layer_label: str,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float, str]:
        if not candidate_scene_ids:
            return None, 0.0, "no_candidates"

        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        scene_ids = [
            scene_id
            for scene_id in list(dict.fromkeys(int(item) for item in candidate_scene_ids))
            if isinstance(images.get(scene_id), dict)
        ]
        scores = self._scene_candidate_scores_parallel(ctx, images, scene_ids, frame_data_url)
        candidates: list[SceneGraphCandidate] = []
        for scene_id, score in zip(scene_ids, scores):
            image = images.get(scene_id)
            if not isinstance(image, dict):
                continue
            matched = score >= self._scene_match_threshold(scene_id)
            candidates.append(SceneGraphCandidate(scene_id=scene_id, score=score, matched=matched))

        matched_ids = [item.scene_id for item in candidates if item.matched]
        edges: list[dict[str, Any]] = []
        if len(matched_ids) > 1:
            edges.extend(self._scene_match_edges_for_candidates(ctx, matched_ids, trace=trace))
            candidates = [
                SceneGraphCandidate(
                    scene_id=item.scene_id,
                    score=item.score,
                    matched=item.matched,
                    frame_similarity=(
                        self._scene_reference_similarity(ctx, images[item.scene_id], frame_data_url)
                        if item.matched and isinstance(images.get(item.scene_id), dict)
                        else None
                    ),
                )
                for item in candidates
            ]
        result = choose_scene_from_graph(candidates, edges)
        if result.status == "unknown":
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

    def _identify_scene_number(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> tuple[int | None, float]:
        ctx.pop("_last_layer3_auxiliary", None)
        graph_scene_id, graph_score, graph_status = self._identify_scene_number_by_graph(
            ctx,
            frame_data_url,
            preferred_scene_ids,
            trace=trace,
        )
        scene_id = graph_scene_id
        score = graph_score
        ctx["_last_scene_recognition_status"] = str(graph_status or "no_match")
        identity_boxes: list[dict[str, Any]] = []
        all_shape_boxes: list[dict[str, Any]] = []
        frame_width = 0
        frame_height = 0
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        matched_image = images.get(int(scene_id)) if scene_id is not None else None
        asset_tree = ctx.get("asset_tree")
        asset_directory = scene_asset_directory_path(asset_tree, scene_id) if isinstance(asset_tree, list) else ""
        if isinstance(matched_image, dict):
            frame_width, frame_height = self._frame_size(matched_image)
            identity_boxes = [
                self._box(shape, matched_image)
                for shape in self._scene_identity_shapes(matched_image)
            ]
            all_shape_boxes = [
                self._box(shape, matched_image)
                for shape in self._all_scene_shapes(matched_image)
            ]
        publish_fanxiu_scene_recognition(
            scene_id,
            score,
            source=str(ctx.get("_fanxiu_scene_observation_source") or "runtime"),
            asset_directory=asset_directory,
            boxes=identity_boxes,
            all_shape_boxes=all_shape_boxes,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        return scene_id, score

    def _runtime_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        return self._runtime_scene_candidate_ids_by_kind(ctx, include_popups=None)

    def _runtime_popup_scene_candidate_ids(self, ctx: dict[str, Any]) -> list[int]:
        tree = ctx.get("asset_tree")
        if not isinstance(tree, list):
            return []
        return [
            int(scene_id)
            for candidate in self._auto_close_guard_images(tree)
            if isinstance(candidate.get("image"), dict)
            and (scene_id := self._image_number(candidate["image"])) is not None
        ]

    def _runtime_scene_candidate_ids_by_kind(self, ctx: dict[str, Any], *, include_popups: bool | None) -> list[int]:
        images = ctx.get("images") or {}
        if not isinstance(images, dict):
            return []
        tree = ctx.get("asset_tree")
        if isinstance(tree, list):
            return default_recognition_candidate_ids(
                tree,
                images,
                include_popups=include_popups,
            )
        if include_popups is True:
            return []
        return [
            int(scene_id)
            for scene_id in self.scene_ids.values()
            if int(scene_id) in images
            and isinstance(images.get(int(scene_id)), dict)
            and int(View(images[int(scene_id)]).layer) <= 2
        ]

    def _scene_jump_edges(self, tree: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        tree = self._resolved_asset_tree(tree)
        edges = explicit_scene_jump_edges(tree)
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

    def _scene_jump_edge_semantic_key(self, edge: dict[str, Any]) -> tuple[Any, ...]:
        """Identify one action independently of mutable landing counters."""

        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        return (
            int(edge.get("source_id") or 0),
            str(shape.get("id") or ""),
            str(shape.get("title") or ""),
            bool(edge.get("_runtime_confirm_edge")),
        )

    def _scene_jump_target_counts(self, tree: list[dict[str, Any]], shape: dict[str, Any]) -> dict[int, int]:
        navigator = SceneNavigator(self._resolved_asset_tree(tree))
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

    def _scene_navigation_edge_risk(
        self,
        edge: dict[str, Any],
        target_scene_id: int,
    ) -> int | None:
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        shape_title = str(shape.get("title") or "")
        if (
            int(target_scene_id) == 34
            and "前往" in _sanitize_ocr_text(shape_title)
            and shape.get("allowReturnViaForward") is not True
        ):
            return None
        risk = self._scene_navigation_shape_risk(shape)
        if risk >= 100 and _sanitize_ocr_text(shape_title) == "领取奖励":
            return 0
        return None if risk >= 100 else risk

    def _scene_navigation_distances_to_target(
        self,
        navigation_edges: dict[int, list[dict[str, Any]]],
        target_scene_id: int,
    ) -> dict[int, int]:
        reverse_edges: dict[int, set[int]] = {}
        for source_id, edges in navigation_edges.items():
            for edge in edges:
                if self._scene_navigation_edge_risk(edge, int(target_scene_id)) is None:
                    continue
                for landing_id in edge.get("target_ids") or []:
                    reverse_edges.setdefault(int(landing_id), set()).add(int(source_id))
        distances = {int(target_scene_id): 0}
        queue = [int(target_scene_id)]
        while queue:
            landing_id = queue.pop(0)
            next_distance = distances[landing_id] + 1
            for source_id in reverse_edges.get(landing_id, set()):
                if source_id in distances:
                    continue
                distances[source_id] = next_distance
                queue.append(source_id)
        return distances

    def _scene_navigation_edge_progress_probability(
        self,
        tree: list[dict[str, Any]],
        edge: dict[str, Any],
        target_scene_id: int,
        *,
        distances_to_target: Mapping[int, int],
        landing_probability_cache: dict[tuple[Any, ...], dict[int, float]],
    ) -> tuple[float, list[int]]:
        source_id = int(edge.get("source_id") or 0)
        source_distance = distances_to_target.get(source_id)
        if source_distance is None or self._scene_navigation_edge_risk(edge, target_scene_id) is None:
            return 0.0, []
        shape = edge.get("shape") if isinstance(edge.get("shape"), dict) else {}
        edge_key = self._scene_jump_edge_key(edge)
        landing_probabilities = landing_probability_cache.get(edge_key)
        if landing_probabilities is None:
            landing_probabilities = posterior_landing_probabilities(
                self._scene_jump_target_counts(tree, shape),
                [int(scene_id) for scene_id in edge.get("target_ids") or []],
            )
            landing_probability_cache[edge_key] = landing_probabilities
        progress_landing_ids = [
            int(landing_id)
            for landing_id in landing_probabilities
            if int(landing_id) == int(target_scene_id)
            or distances_to_target.get(int(landing_id), source_distance) < source_distance
        ]
        probability = sum(landing_probabilities[landing_id] for landing_id in progress_landing_ids)
        return min(1.0, max(0.0, probability)), progress_landing_ids

    def _rank_scene_next_edge(
        self,
        tree: list[dict[str, Any]],
        edge: dict[str, Any],
        target_scene_id: int,
        *,
        order: int,
        navigation_edges: dict[int, list[dict[str, Any]]] | None = None,
        distances_to_target: Mapping[int, int] | None = None,
        landing_probability_cache: dict[tuple[Any, ...], dict[int, float]] | None = None,
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
        shape_title = str(shape.get("title") or "")
        current_edge_risk = self._scene_navigation_edge_risk(edge, int(target_scene_id))
        if (
            current_edge_risk is None
            and int(target_scene_id) == 34
            and "前往" in _sanitize_ocr_text(shape_title)
            and shape.get("allowReturnViaForward") is not True
        ):
            self._log(
                "detail",
                f"场景移动：回 #34 时拒绝把 #{source_id}「{shape_title}」作为返回动作",
            )
            return None
        if (
            current_edge_risk == 0
            and self._scene_navigation_shape_risk(shape) >= 100
            and _sanitize_ocr_text(shape_title) == "领取奖励"
        ):
            self._log(
                "detail",
                f"场景移动：精确奖励收尾 #{source_id}，允许点击「{shape_title}」回到已声明落点",
            )
        if current_edge_risk is None:
            return None
        target_counts = self._scene_jump_target_counts(tree, shape)
        navigation_edges = navigation_edges if navigation_edges is not None else self._scene_jump_edges(tree)
        distances_to_target = (
            distances_to_target
            if distances_to_target is not None
            else self._scene_navigation_distances_to_target(navigation_edges, int(target_scene_id))
        )
        landing_probability_cache = landing_probability_cache if landing_probability_cache is not None else {}
        progress_probability, progress_landing_ids = self._scene_navigation_edge_progress_probability(
            tree,
            edge,
            int(target_scene_id),
            distances_to_target=distances_to_target,
            landing_probability_cache=landing_probability_cache,
        )
        if progress_probability <= 0:
            return None
        landing_probabilities = landing_probability_cache[self._scene_jump_edge_key(edge)]
        best_landing_id = max(
            progress_landing_ids,
            key=lambda landing_id: landing_probabilities.get(int(landing_id), 0.0),
        )
        source_distance = int(distances_to_target.get(source_id, 1))
        downstream_len = int(distances_to_target.get(best_landing_id, max(0, source_distance - 1)))
        direct = best_landing_id == int(target_scene_id)
        best_count = target_counts.get(best_landing_id, 0)
        posterior_score = int(round(progress_probability * 1_000_000))
        self_count = target_counts.get(source_id, 0)
        wrong_target_count = max(
            (
                count
                for landing_id, count in target_counts.items()
                if landing_id != int(target_scene_id)
            ),
            default=0,
        )
        ambiguity = len(target_ids)
        dynamic_penalty = 1 if edge.get("_runtime_confirm_edge") else 0
        total_path_len = 1 + int(downstream_len)
        direct_target_count = target_counts.get(int(target_scene_id), 0)
        navigation_risk = current_edge_risk
        exit_score = self._scene_navigation_shape_exit_score(shape) if not direct else 0
        score = (
            int(posterior_score),
            int(direct_target_count),
            -int(total_path_len),
            int(exit_score),
            int(best_count),
            -int(self_count),
            -int(wrong_target_count),
            -int(total_path_len),
            -int(navigation_risk),
            -int(ambiguity),
            -int(dynamic_penalty),
            -int(order),
        )
        reason = "下一步主要直达目标" if direct else f"下一步主要落到 #{best_landing_id}"
        if best_count:
            reason += f"，历史命中 {best_count} 次"
        reason += f"，单步进展权重 {progress_probability:.3%}"
        if self_count:
            reason += f"，自身落点 {self_count} 次"
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
            "weight": progress_probability,
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
        navigation_edges = self._scene_jump_edges(tree)
        distances_to_target = self._scene_navigation_distances_to_target(
            navigation_edges,
            int(target_scene_id),
        )
        landing_probability_cache: dict[tuple[Any, ...], dict[int, float]] = {}
        for order, edge in enumerate(navigation_edges.get(int(current_scene_id), [])):
            # ``sceneJumpTarget`` is a live landing-frequency table.  Every
            # self-loop increments it before the next replan, so the full edge
            # key changes even though this is still the exact same action.
            # Accept both key forms for compatibility, but make semantic keys
            # the stable failure identity used by goto.
            if (
                self._scene_jump_edge_key(edge) in failed_edge_keys
                or self._scene_jump_edge_semantic_key(edge) in failed_edge_keys
            ):
                continue
            ranked = self._rank_scene_next_edge(
                tree,
                edge,
                int(target_scene_id),
                order=order,
                navigation_edges=navigation_edges,
                distances_to_target=distances_to_target,
                landing_probability_cache=landing_probability_cache,
            )
            if ranked is not None:
                candidates.append(ranked)
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return self._navigation_random.choices(
            candidates,
            weights=[max(1e-12, float(item.get("weight") or 0.0)) for item in candidates],
            k=1,
        )[0]

    def _scene_navigation_exploration_priority(self, shape: dict[str, Any]) -> int:
        """Rank bounded navigation actions when the static graph has no route.

        This is deliberately narrower than "click any shape".  go_scene may
        explore only annotated controls whose UI semantics normally move or
        dismiss the current view.  The real landing is then recognized,
        recorded in sceneJumpTarget and fed back into the next planning step.
        """
        if self._scene_navigation_shape_risk(shape) >= 100:
            return 0
        title = _sanitize_ocr_text(shape.get("title"))
        priorities = {
            "回到世界": 500,
            "返回": 480,
            "离开": 460,
            "退出": 440,
            "关闭下方菜单": 430,
            "关闭": 420,
            "空白": 380,
            "取消": 340,
            # Confirmation is intentionally last: it is useful for known
            # prompt/result scenes such as #330, but less universally safe
            # than an explicit return/close control.
            "确定": 200,
            "确认": 200,
            # Some result pages use the same annotated control both as their
            # scene identity and as the only safe dismissal action.
            "继续": 180,
            "点击屏幕继续": 180,
        }
        return priorities.get(title, 0)

    def _select_scene_exploration_edge(
        self,
        tree: list[dict[str, Any]],
        image: dict[str, Any],
        current_scene_id: int,
        target_scene_id: int,
        *,
        failed_edge_keys: set[tuple[Any, ...]] | None = None,
        explored_shape_keys: set[tuple[str, str, str]] | None = None,
        navigation_state_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Choose one safe, evidence-ranked action without requiring a route."""
        failed_edge_keys = failed_edge_keys or set()
        explored_shape_keys = explored_shape_keys or set()
        candidates: list[dict[str, Any]] = []
        for order, shape in enumerate(self._flatten_shapes(image.get("shapes"))):
            priority = self._scene_navigation_exploration_priority(shape)
            if priority <= 0:
                continue
            exploration_key = (
                str(navigation_state_key or f"scene:{int(current_scene_id)}"),
                str(shape.get("id") or ""),
                str(shape.get("title") or ""),
            )
            if exploration_key in explored_shape_keys:
                continue
            target_ids = self._scene_jump_target_ids(tree, shape)
            edge = {
                "source_id": int(current_scene_id),
                "image": image,
                "shape": shape,
                "target_ids": target_ids,
                "_dynamic_exploration": True,
            }
            if (
                self._scene_jump_edge_key(edge) in failed_edge_keys
                or self._scene_jump_edge_semantic_key(edge) in failed_edge_keys
            ):
                continue
            counts = self._scene_jump_target_counts(tree, shape)
            total_count = sum(counts.values())
            self_count = counts.get(int(current_scene_id), 0)
            progressing_count = total_count - self_count
            progress_rate = int(1000 * progressing_count / total_count) if total_count else 500
            candidates.append({
                "edge": edge,
                "score": (
                    int(priority),
                    int(progress_rate),
                    int(progressing_count),
                    -int(self_count),
                    int(total_count),
                    -len(target_ids),
                    -int(order),
                ),
                "reason": (
                    f"静态无路，动态尝试已标注导航动作"
                    + (
                        f"，历史前进 {progressing_count}/{total_count} 次、自身落点 {self_count} 次"
                        if total_count
                        else "，暂无落点历史"
                    )
                ),
                "landing_id": None,
                "downstream_len": None,
            })
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[0]

    def _navigation_frame_signature(self, frame_data_url: str) -> bytes:
        """Return a small visual signature used to distinguish planner states.

        Scene recognition and planner state are intentionally different: two
        visually different screens may both be unknown, or may share one scene
        id.  The signature lets goto continue unknown -> unknown exploration
        without repeatedly clicking the same fallback on an unchanged screen.
        """
        try:
            from PIL import Image

            png_data = self._decode_frame_data_url(frame_data_url)
            with Image.open(io.BytesIO(png_data)) as source:
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                return source.convert("L").resize((16, 16), resampling).tobytes()
        except Exception:
            return str(frame_data_url or "").encode("utf-8", errors="replace")

    def _navigation_state_key(
        self,
        frame_data_url: str,
        current_scene_id: int | None,
        states: list[tuple[int | None, bytes, str]],
    ) -> str:
        """Resolve the current observed screen to a stable state within one goto."""
        signature = self._navigation_frame_signature(frame_data_url)
        for known_scene_id, known_signature, state_key in states:
            if known_scene_id != current_scene_id:
                continue
            if signature == known_signature:
                return state_key
            if len(signature) == 256 and len(known_signature) == 256:
                total_delta = sum(abs(a - b) for a, b in zip(signature, known_signature))
                similarity = 100.0 * (1.0 - total_delta / (255.0 * len(signature)))
                if similarity >= 95.0:
                    return state_key
        prefix = f"scene:{current_scene_id}" if current_scene_id is not None else "unknown"
        state_key = f"{prefix}:state:{len(states) + 1}"
        states.append((current_scene_id, signature, state_key))
        return state_key

    def _try_navigation_fallback_return(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        *,
        navigation_state_key: str,
        target_scene_id: int,
        attempted_actions: dict[tuple[str, str], dict[str, float | int]],
        current_scene_id: int | None = None,
        current_score: float = 0.0,
        incident_recorder: NavigationIncidentRecorder | None = None,
    ) -> _UnknownFallbackDecision:
        """Consume one recovery attempt after continuous unknown qualification.

        #424 has no scene identity and therefore never becomes a Layer 1/2
        recognition or graph node.  Unknown frames may use it while returning
        to the stable world.  #611 is a verified full-screen promotion overlay:
        its own only labelled action is the business-changing ``前往``, while
        the formal lower-left #424 return closed the overlay to #34 in real
        Runtime.  Other recognized scenes must still use their own graph edge.
        """
        recognized_overlay_return = (
            int(current_scene_id or 0) == 611 and int(target_scene_id) == 34
        )
        if current_scene_id is not None and not recognized_overlay_return:
            return _UnknownFallbackDecision("unavailable")
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        fallback_image = images.get(424) if isinstance(images, dict) else None
        if not isinstance(fallback_image, dict):
            return _UnknownFallbackDecision("unavailable")
        attempt_key = ("__continuous_unknown__", "fallback_return")
        shape = self._find_shape(fallback_image, "返回")
        if not isinstance(shape, dict):
            return _UnknownFallbackDecision("unavailable")
        state = attempted_actions.setdefault(attempt_key, {"count": 0})
        attempt_count = int(state.get("count") or 0)
        if attempt_count >= UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION:
            return _UnknownFallbackDecision("exhausted", attempt=attempt_count)

        # #424 is an action template, not proof that every unknown page has a
        # return button.  Locate the masked arrow in the current frame first;
        # zero or multiple candidates must fail closed instead of projecting
        # the reference coordinate onto an unrelated page.
        match_shape = {
            **shape,
            "floating": True,
            "imageMatchRole": "required",
            "ocrMatchRole": "off",
            "_match_scan_box": {
                "x": 0.0,
                "y": 0.75,
                "w": 0.30,
                "h": 0.25,
            },
        }
        try:
            match_result = self._match_shape(
                ctx,
                fallback_image,
                match_shape,
                frame_data_url,
                condition="image",
            )
        except Exception as exc:
            self._log("warning", f"场景移动：#424「返回」图形匹配失败，保留现场：{exc}")
            return _UnknownFallbackDecision("unavailable", attempt=attempt_count)
        click_point = self._shape_match_resolved_click_point(
            fallback_image,
            match_shape,
            match_result,
        )
        if not bool(match_result.get("matched")) or click_point is None:
            if not bool(state.get("not_visible_logged")):
                reason = str(match_result.get("reason") or "not_matched")
                candidate_count = len(match_result.get("matches") or [])
                self._log(
                    "warning",
                    "场景移动：当前帧未唯一确认左下返回按钮，禁止使用 #424 固定坐标"
                    f"（reason={reason}, candidates={candidate_count}）",
                )
                state["not_visible_logged"] = 1
            return _UnknownFallbackDecision("unavailable", attempt=attempt_count)

        attempt_count += 1
        state["count"] = attempt_count
        state.pop("not_visible_logged", None)
        fallback_reason = (
            "#611 推广弹窗使用正式左下返回"
            if recognized_overlay_return
            else "unknown 回世界使用正式左下返回"
        )
        if incident_recorder is not None:
            incident_recorder.trigger(
                trigger_type="normal_actions_exhausted",
                trigger_label=f"{fallback_reason}，开始使用 #424[返回]",
                threshold={
                    "fallback_scene_id": 424,
                    "fallback_attempt": attempt_count,
                    "continuous_unknown_seconds": DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
                    "max_attempts_per_navigation": UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION,
                },
                frame_data_url=frame_data_url,
                current_scene_id=current_scene_id,
                current_score=current_score,
                candidate_scene_ids=[
                    scene_id
                    for scene_id in (current_scene_id, target_scene_id, 424)
                    if scene_id is not None
                ],
            )
            incident_recorder.mark_fallback_used()
        x, y = click_point
        with self._lock:
            self._status.update({
                "phase": "go_scene_navigation_fallback",
                "current_scene": None,
                "message": (
                    f"场景移动：{fallback_reason}（第 {attempt_count} 次），"
                    f"随后重新识别并规划到 #{target_scene_id}"
                ),
                "updated_at": time.time(),
            })
        self._log(
            "action",
            f"场景移动：{fallback_reason}，点击一次 #424「返回」（第 {attempt_count}/"
            f"{UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION} 次），随后重新计时并规划到 #{target_scene_id}",
        )
        self._save_action_trace(
            ctx,
            fallback_image,
            {
                "kind": "click",
                "point": [float(x), float(y)],
                "label": "click #424 返回 navigation_fallback",
                "shape_title": shape.get("title"),
                "shape_id": shape.get("id"),
                "source_scene_id": current_scene_id,
                "target_scene_id": int(target_scene_id),
                "navigation_fallback_scene_id": 424,
                "navigation_state_key": navigation_state_key,
                "navigation_fallback_attempt": attempt_count,
                "navigation_fallback_match": {
                    "similarity": match_result.get("similarity"),
                    "box": match_result.get("box"),
                    "resolved_box": match_result.get("resolved_box"),
                    "reason": match_result.get("reason"),
                },
            },
            frame_data_url=frame_data_url,
        )
        self._click_frame_point(ctx, fallback_image, x, y, save_action_trace=False)
        self._clear_tick_frame(ctx)
        return _UnknownFallbackDecision("clicked", attempt=attempt_count, point=(float(x), float(y)))

    def _wait_or_click_navigation_fallback_return(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        stop_event: threading.Event,
        *,
        navigation_state_key: str,
        target_scene_id: int,
        attempted_actions: dict[tuple[str, str], dict[str, float | int]],
        current_scene_id: int | None = None,
        current_score: float = 0.0,
        incident_recorder: NavigationIncidentRecorder | None = None,
    ):
        decision = self._try_navigation_fallback_return(
            ctx,
            frame_data_url,
            navigation_state_key=navigation_state_key,
            target_scene_id=target_scene_id,
            attempted_actions=attempted_actions,
            current_scene_id=current_scene_id,
            current_score=current_score,
            incident_recorder=incident_recorder,
        )
        if decision.status == "clicked":
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.5)
            if incident_recorder is not None and incident_recorder.active:
                after_frame = self._screencap(ctx)
                after_scene_id, after_score = self._identify_scene_number(ctx, after_frame)
                fallback_image = (ctx.get("images") or {}).get(424)
                fallback_shape = self._find_shape(fallback_image, "返回")
                incident_recorder.record_action(
                    kind="fallback",
                    source_scene_id=current_scene_id,
                    source_score=current_score,
                    shape=fallback_shape,
                    reason="连续一分钟 unknown 后的通用返回投影",
                    before_frame=frame_data_url,
                    landing_scene_id=after_scene_id,
                    landing_score=after_score,
                    after_frame=after_frame,
                    frame_similarity=_image_similarity_percent(self, frame_data_url, after_frame),
                    navigation_state_key=navigation_state_key,
                    attempt=decision.attempt,
                    point=decision.point,
                )
            return True
        if decision.status == "exhausted":
            self._log(
                "warning",
                f"场景移动：本次导航已尝试 #424「返回」{decision.attempt} 次，停止重复点击并保留现场",
            )
        return False

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
            if self._image_layer(image) >= 3 or not self._scene_identity_shapes(image):
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
        tree = self._resolved_asset_tree(tree)
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
        tree = self._resolved_asset_tree(tree)
        images = self._index_images(tree)

        def is_route_candidate(scene_id: int) -> bool:
            image = images.get(int(scene_id))
            if not isinstance(image, dict):
                return False
            try:
                declared_layer = int(image.get("layer") or self._image_layer(image))
            except (TypeError, ValueError):
                declared_layer = self._image_layer(image)
            return not (declared_layer >= 3 and not self._scene_identity_shapes(image))

        candidates = SceneNavigator(tree).route_candidate_ids(
            target_scene_id,
            confirmation_scene_ids=self._scene_jump_confirmation_scene_ids(tree),
        )
        candidates = [int(scene_id) for scene_id in candidates if is_route_candidate(int(scene_id))]
        candidate_set = {int(scene_id) for scene_id in candidates}
        image_ids: list[int] = []

        def collect_image_ids(items: list[dict[str, Any]]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                image_id = self._image_number(item)
                if (
                    image_id is not None
                    and is_route_candidate(int(image_id))
                    and int(image_id) not in image_ids
                ):
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

        return candidates

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
        incident_recorder = ctx.get("_navigation_incident_recorder")
        if isinstance(incident_recorder, NavigationIncidentRecorder):
            incident_recorder.trigger(
                trigger_type="recovery_exhausted",
                trigger_label="统一识别或有界恢复耗尽，仍缺少可靠导航落点",
                threshold={
                    "fallback_max_attempts_per_navigation": UNKNOWN_FALLBACK_MAX_ATTEMPTS_PER_NAVIGATION,
                    "elapsed_seconds": round(float(elapsed_seconds or 0.0), 1),
                },
                frame_data_url=frame_data_url,
                current_scene_id=current_scene_id,
                current_score=0.0,
                candidate_scene_ids=[
                    scene_id
                    for scene_id in (current_scene_id, target_scene_id)
                    if scene_id is not None
                ],
            )
            incident_recorder.finalize(
                status="unrecovered",
                final_scene_id=current_scene_id,
                final_frame=frame_data_url,
                message=detail,
            )
            ctx.pop("_navigation_incident_recorder", None)
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

    def _effective_shape_source_image(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
    ) -> dict[str, Any]:
        # Inherited Shape configuration always runs in the child/host scene's
        # image context.  Its source scene is only the annotation owner.
        return image

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
        images = ctx.get("images") if isinstance(ctx.get("images"), dict) else {}
        image = images.get(int(scene_id))
        if isinstance(image, dict):
            try:
                declared_layer = int(image.get("layer") or self._image_layer(image))
            except (TypeError, ValueError):
                declared_layer = self._image_layer(image)
            if declared_layer >= 3 and not self._scene_identity_shapes(image):
                return None
        return int(scene_id)

    def _scene_identity_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            shape.raw
            for shape in View(image).get_shapes(include_groups=False)
            if shape.is_scene_identity
        ]

    def _all_scene_shapes(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            shape.raw
            for shape in View(image).get_shapes(include_groups=False)
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
            frame = frame_data_url
            if not frame:
                try:
                    frame = self._capture_frame(ctx)
                except Exception:
                    frame = self._screencap(ctx)
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
        while True:
            try:
                response = _screencap_game_window2_service() if entry.mode == "local" else _remote_game_window2_screencap(entry)
                break
            except Exception as exc:
                if entry.mode != "local":
                    raise
                state = record_mumu_adb_failure(exc, recover=True)
                recovered = bool(state.get("recovered"))
                with self._lock:
                    self._status["device_health"] = state
                    if recovered:
                        self._log_locked(
                            "warning",
                            "ADB 持续取帧失败后已恢复 MuMu 安卓容器；当前 GUI 事务作废",
                            scope="guard",
                            item_id="device_health",
                        )
                    self._sync_guard_status_locked()
                if recovered:
                    raise FanxiuEmulatorRestartRequired(
                        "ADB 持续取帧失败；已完整重启 MuMu，当前业务尝试作废",
                        evidence={
                            "reason": "adb_frame_recovery",
                            "device_health": state,
                            "error": str(exc),
                        },
                        recovery_succeeded=True,
                    ) from exc
                if state.get("recovery_deferred") != "frame_unusable_observation_window":
                    raise
                elapsed = max(0.0, float(state.get("frame_unusable_elapsed_seconds") or 0.0))
                threshold = max(elapsed, float(state.get("frame_unusable_recovery_seconds") or 30.0))
                wait_seconds = max(0.1, min(2.0, threshold - elapsed))
                self._log(
                    "detail",
                    f"ADB 黑帧仍在有界观察窗口 {elapsed:.1f}/{threshold:.1f}s，{wait_seconds:.1f}s 后重试同一截图事务",
                )
                time.sleep(wait_seconds)
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
        if ocr_enabled:
            self._shared_spatial_ocr_result(ctx, frame_data_url)
            result = self._shape_cached_frame_ocr_match(ctx, image, shape, frame_data_url)
            result["flags"] = flags
            if condition == "ocr" or result.get("matched") or ocr_role == "required" or image_role == "off":
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
            if bool(flags["scan"]) and image_role != "off":
                result = self._resolve_unique_floating_image_match(
                    ctx,
                    image,
                    shape,
                    frame_data_url,
                    result,
                    match_strategy=str(flags["match_strategy"]),
                )
        except Exception as exc:
            raise RuntimeError(f"浮动标注「{shape.get('title') or shape.get('id')}」匹配失败：{exc}") from exc
        similarity = float(result.get("similarity") or 0)
        result_ocr_matched = bool(ocr_enabled and self._shape_match_result_ocr_matches(shape, result))
        if ocr_enabled and not result_ocr_matched and self._has_cached_ocr_tokens(ctx, frame_data_url):
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

    def _resolve_unique_floating_image_match(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str,
        initial_result: dict[str, Any],
        *,
        match_strategy: str,
    ) -> dict[str, Any]:
        """Adapt pixel tolerance until a full-frame image hit is unique."""

        configured = max(0, min(255, int(shape.get("pixelTolerance") if shape.get("pixelTolerance") is not None else 20)))
        threshold = float(self.scene_threshold)
        reference_box = initial_result.get("box") if isinstance(initial_result.get("box"), dict) else None
        initial_matches = initial_result.get("matches") if isinstance(initial_result.get("matches"), list) else []
        initial_count = sum(
            1 for item in initial_matches
            if isinstance(item, dict) and float(item.get("crop_similarity") or 0) >= threshold
        )
        # A larger pixel tolerance is less strict; a smaller one is stricter.
        if initial_count == 0:
            tolerances = [configured, *(value for value in (configured + 5, configured + 10, configured + 20, configured + 35) if value <= 255)]
        elif initial_count > 1:
            tolerances = [configured, *(value for value in (configured - 5, configured - 10, configured - 15, 0) if 0 <= value < configured)]
        else:
            tolerances = [configured]
        tolerances = list(dict.fromkeys(tolerances))
        attempts: list[dict[str, Any]] = []
        result = initial_result

        for index, tolerance in enumerate(tolerances):
            if index:
                probe_shape = {**shape, "pixelTolerance": tolerance}
                result = self._run_match(
                    ctx,
                    image,
                    probe_shape,
                    frame_data_url,
                    scan=True,
                    match_strategy=match_strategy,
                    ocr_enabled=False,
                )
            raw_matches = result.get("matches") if isinstance(result.get("matches"), list) else []
            candidates = [item for item in raw_matches if isinstance(item, dict) and float(item.get("crop_similarity") or 0) >= threshold]
            selection_threshold = threshold
            if len(candidates) > 1:
                for stricter_threshold in range(int(threshold) + 1, 101):
                    stricter = [
                        item for item in raw_matches
                        if isinstance(item, dict) and float(item.get("crop_similarity") or 0) >= stricter_threshold
                    ]
                    if len(stricter) == 1:
                        candidates = stricter
                        selection_threshold = float(stricter_threshold)
                        break
            attempts.append({
                "pixel_tolerance": tolerance,
                "selection_threshold": selection_threshold,
                "candidate_count": len(candidates),
            })
            if len(candidates) != 1:
                continue
            selected = candidates[0]
            resolved = dict(result)
            if reference_box is not None:
                resolved["box"] = reference_box
            resolved.update({
                "fixed_box": selected.get("box") or result.get("fixed_box"),
                "resolved_box": selected.get("box") or result.get("resolved_box"),
                "similarity": selected.get("crop_similarity") or selected.get("similarity") or 0,
                "score": selected.get("crop_score") or selected.get("score") or 0,
                "pixel_tolerance": tolerance,
                "selection_threshold": selection_threshold,
                "candidate_count": 1,
                "adaptive_match_attempts": attempts,
                "unique_match": True,
            })
            return resolved

        unresolved = dict(result)
        unresolved.update({
            "similarity": 0,
            "score": 0.0,
            "candidate_count": attempts[-1]["candidate_count"] if attempts else 0,
            "adaptive_match_attempts": attempts,
            "unique_match": False,
            "reason": "floating_image_not_unique",
        })
        return unresolved

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
                fixed_box = self._ocr_fragment_box(item)
                if fixed_box is not None:
                    if existing_fixed_box is not None:
                        result["ocr_box"] = fixed_box
                        result.setdefault("resolved_box", existing_fixed_box)
                    else:
                        result["fixed_box"] = fixed_box
                        result["resolved_box"] = fixed_box
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
        cache = ctx.get("_ocr_tokens_cache")
        if not isinstance(cache, dict) or cache.get("frame") != frame_data_url:
            return result
        tokens = cache.get("tokens") if isinstance(cache.get("tokens"), list) else []
        box = self._box(shape, image)
        floating_ocr = bool(shape.get("floating"))
        search_box = box
        if floating_ocr:
            explicit_scan_box = shape.get("_match_scan_box")
            if isinstance(explicit_scan_box, dict):
                search_box = self._box(explicit_scan_box, image)
            else:
                width, height = self._frame_size(image)
                search_box = {"x": 0.0, "y": 0.0, "w": float(width), "h": float(height)}
        spatial = query_spatial_ocr(tokens, search_box)
        text = _sanitize_ocr_text(spatial.get("text"))
        fragments = spatial.get("fragments") if isinstance(spatial.get("fragments"), list) else []
        result["ocr_text"] = text
        result["matches"] = fragments
        token_box = None
        if floating_ocr and mode in {"contains", "exact"}:
            exact_matches = find_text_matches(spatial.get("tokens") or [], target)
            if len(exact_matches) > 1:
                result["reason"] = "floating_ocr_ambiguous"
                result["candidate_boxes"] = [match.box for match in exact_matches]
                return result
            if exact_matches:
                token_box = exact_matches[0].box
                result["ocr_text"] = exact_matches[0].text
        text_matched = (
            bool(token_box)
            if floating_ocr and mode == "exact"
            else bool(text and self._ocr_text_matches(text, target, mode))
        )
        if text_matched:
            if floating_ocr and mode in {"contains", "exact"} and token_box is None:
                return result
            result["matched"] = True
            result["similarity"] = 100
            token_box = token_box or locate_text_box(spatial.get("tokens") or [], target)
            ocr_box = token_box or union_fragment_box(fragments)
            result["fixed_box"] = box
            result["resolved_box"] = ocr_box if floating_ocr and ocr_box is not None else box
            result["floating_ocr"] = floating_ocr
            if ocr_box is not None:
                # ``token_box`` already is the exact union of the matched
                # character boxes.  Re-slicing it by the full aggregated text
                # would incorrectly apply the old uniform-line estimate twice.
                result["ocr_box"] = token_box or ocr_box
        return result

    def _ocr_options_cache_key(self, options: dict[str, Any] | None = None) -> str:
        if not options:
            return "{}"
        try:
            return json.dumps(options, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            return str(sorted((str(key), str(value)) for key, value in options.items()))

    def _cached_ocr_result(self, ctx: dict[str, Any], frame_data_url: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        canonical_options = {**dict(options or {}), "return_word_box": True}
        cache = ctx.setdefault("_ocr_tokens_cache", {})
        options_key = self._ocr_options_cache_key(canonical_options)
        if (
            isinstance(cache, dict)
            and cache.get("version") == 4
            and cache.get("frame") == frame_data_url
            and cache.get("options_key") == options_key
            and isinstance(cache.get("tokens"), list)
            and isinstance(cache.get("lines"), list)
        ):
            return cache
        response = self._ocr_frame(frame_data_url, options=canonical_options)
        lines = response.get("lines") if isinstance(response.get("lines"), list) else []
        tokens = response.get("tokens") if isinstance(response.get("tokens"), list) else []
        cache = {
            "version": 4,
            "frame": frame_data_url,
            "options_key": options_key,
            "lines": lines,
            "tokens": tokens,
        }
        ctx["_ocr_tokens_cache"] = cache
        return cache

    def _shared_spatial_ocr_result(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical_options = {**dict(options or {}), "return_word_box": True}
        options_key = self._ocr_options_cache_key(canonical_options)
        with self._shared_ocr_lock:
            cache = ctx.get("_ocr_tokens_cache")
            if (
                isinstance(cache, dict)
                and cache.get("version") == 4
                and cache.get("frame") == frame_data_url
                and cache.get("options_key") == options_key
                and isinstance(cache.get("tokens"), list)
                and isinstance(cache.get("lines"), list)
            ):
                return cache
            return self._cached_ocr_result(ctx, frame_data_url, options=canonical_options)

    def _cached_ocr_tokens(self, ctx: dict[str, Any], frame_data_url: str) -> list[dict[str, Any]]:
        result = self._cached_ocr_result(ctx, frame_data_url)
        tokens = result.get("tokens")
        return tokens if isinstance(tokens, list) else []

    def _cached_ocr_fragments(self, ctx: dict[str, Any], frame_data_url: str) -> list[dict[str, Any]]:
        result = self._cached_ocr_result(ctx, frame_data_url)
        lines = result.get("lines")
        return lines if isinstance(lines, list) else []

    def _has_cached_ocr_tokens(self, ctx: dict[str, Any], frame_data_url: str) -> bool:
        cache = ctx.get("_ocr_tokens_cache")
        tokens = cache.get("tokens") if isinstance(cache, dict) and cache.get("frame") == frame_data_url else None
        return isinstance(tokens, list) and any(isinstance(token, dict) for token in tokens)

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
        if score >= float(self.scene_threshold) and bool(image.get("floatingAlignmentRequired")):
            score = min(score, self._floating_scene_alignment_score(ctx, image, frame_data_url))
        return self._scene_discriminator_adjusted_score(ctx, image, frame_data_url, score)

    def _floating_scene_alignment_score(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str,
    ) -> float:
        """Require floating OCR identities to describe one translated panel.

        Independent full-frame OCR hits prove that labels exist; they do not
        prove that the labels belong to the same popup.  This alignment gate
        compares every live OCR box with its annotated box and accepts only a
        common translation, mirroring Runtime-GUI alignment semantics.
        """

        anchors = [
            shape
            for shape in self._scene_identity_shapes(image)
            if bool(shape.get("floating"))
            and self._shape_ocr_role(shape) == "required"
            and str(shape.get("ocrText") or "").strip()
        ]
        if len(anchors) < 2:
            self._log("detail", f"#{self._image_number(image) or '?'} 浮动弹窗缺少至少两个 OCR 身份锚点")
            return 0.0
        offsets: list[tuple[float, float, str]] = []
        for shape in anchors:
            result = self._match_shape(ctx, image, shape, frame_data_url, condition="ocr")
            resolved = result.get("resolved_box") if isinstance(result.get("resolved_box"), dict) else None
            if not bool(result.get("matched")) or resolved is None:
                return 0.0
            expected = self._box(shape, image)
            offsets.append((
                float(resolved.get("x") or 0) + float(resolved.get("w") or 0) / 2
                - (float(expected.get("x") or 0) + float(expected.get("w") or 0) / 2),
                float(resolved.get("y") or 0) + float(resolved.get("h") or 0) / 2
                - (float(expected.get("y") or 0) + float(expected.get("h") or 0) / 2),
                str(shape.get("title") or shape.get("id") or "anchor"),
            ))
        dx_values = sorted(item[0] for item in offsets)
        dy_values = sorted(item[1] for item in offsets)
        middle = len(offsets) // 2
        dx = dx_values[middle] if len(offsets) % 2 else (dx_values[middle - 1] + dx_values[middle]) / 2
        dy = dy_values[middle] if len(offsets) % 2 else (dy_values[middle - 1] + dy_values[middle]) / 2
        tolerance = max(1.0, float(image.get("floatingAlignmentTolerance") or 36.0))
        maximum_error = max(max(abs(item_dx - dx), abs(item_dy - dy)) for item_dx, item_dy, _title in offsets)
        if maximum_error > tolerance:
            self._log(
                "detail",
                f"#{self._image_number(image) or '?'} 浮动弹窗 OCR 锚点不共线：最大偏差 {maximum_error:.1f}px > {tolerance:.1f}px",
            )
            return 0.0
        ctx["_floating_scene_alignment"] = {
            "frame": frame_data_url,
            "image_id": self._image_number(image),
            "dx": dx,
            "dy": dy,
            "maximum_error": maximum_error,
            "anchors": [title for _item_dx, _item_dy, title in offsets],
        }
        return 100.0

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
        candidate_ids = [
            int(self.scene_ids[key])
            for key in (keys or self._scene_key_order())
            if key in self.scene_ids and self._image(ctx, key) is not None
        ]
        scene_id, score = self._identify_scene_number(
            ctx,
            frame_data_url,
            preferred_scene_ids=candidate_ids,
        )
        return (self._scene_id_key(scene_id), score) if scene_id is not None else ("", score)

    def _scene_matches(self, key: str, score: float) -> bool:
        scene_id = self.scene_ids.get(key)
        return scene_id is not None and self._scene_matches_id(int(scene_id), score)

    def _click_shape(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None = None,
        *,
        match_result: dict[str, Any] | None = None,
        jitter_radius: int = 0,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
    ) -> None:
        xuanhuang_forward = (
            self._image_number(image) == 418
            and str(shape.get("title") or "").strip() == "前往"
        )
        frame_data_url = self._guard_xuanhuang_forward_click(ctx, image, shape, frame_data_url)
        if xuanhuang_forward:
            match_result = None
        action_match_result: dict[str, Any] | None = None
        if frame_data_url:
            action_match_result = match_result if isinstance(match_result, dict) else self._match_shape(ctx, image, shape, frame_data_url)
            if not bool(action_match_result.get("matched")) and self._shape_ocr_fallback_enabled(shape):
                ocr_match_result = self._match_shape(ctx, image, shape, frame_data_url, condition="ocr")
                if bool(ocr_match_result.get("matched")):
                    action_match_result = ocr_match_result
            self._require_shape_match(action_match_result, shape)
        if x_ratio == 0.5 and y_ratio == 0.5:
            raw_click_x, raw_click_y = ActionPlanner().shape_center(image, shape)
        else:
            width, height = self._frame_size(image)
            raw_click_x = (float(shape.get("x") or 0) + float(shape.get("w") or 0) * x_ratio) * width
            raw_click_y = (float(shape.get("y") or 0) + float(shape.get("h") or 0) * y_ratio) * height
        click_x, click_y = raw_click_x, raw_click_y
        resolved_click = None
        if shape.get("clickResolvedBox") is not False:
            if x_ratio == 0.5 and y_ratio == 0.5:
                resolved_click = self._shape_match_resolved_click_point(image, shape, action_match_result)
            else:
                resolved_click = self._shape_match_resolved_click_point(
                    image,
                    shape,
                    action_match_result,
                    x_ratio=x_ratio,
                    y_ratio=y_ratio,
                )
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
        if jitter_radius > 0:
            click_x, click_y = self._randomly_perturb_click_point(
                image,
                click_x,
                click_y,
                radius=jitter_radius,
            )
            payload["x"] = click_x
            payload["y"] = click_y
            self._log(
                "detail",
                f"点击无响应重试：随机扰动半径 r={jitter_radius}px，实际落点=({click_x:.1f},{click_y:.1f})",
            )
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

    @staticmethod
    def _navigation_retry_jitter_radius(no_response_count: int) -> int:
        """Grow retry jitter exponentially, with at most 50px growth per retry."""

        count = max(0, int(no_response_count or 0))
        if count <= 0:
            return 0
        radius = 1
        for _ in range(1, count):
            radius = min(radius * 2, radius + 50)
        return radius

    def _randomly_perturb_click_point(
        self,
        image: dict[str, Any],
        x: float,
        y: float,
        *,
        radius: int,
    ) -> tuple[float, float]:
        """Apply simple bounded random jitter inside the game frame."""

        width, height = self._frame_size(image)
        radius = max(0, int(radius or 0))
        if radius <= 0:
            return float(x), float(y)
        jittered_x = float(x) + random.randint(-radius, radius)
        jittered_y = float(y) + random.randint(-radius, radius)
        return (
            min(max(jittered_x, 0.0), max(0.0, float(width) - 1.0)),
            min(max(jittered_y, 0.0), max(0.0, float(height) - 1.0)),
        )

    def _guard_xuanhuang_forward_click(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        shape: dict[str, Any],
        frame_data_url: str | None,
    ) -> str | None:
        """Require a fresh, positive #418 attempt fraction before clicking 前往."""

        if self._image_number(image) != 418 or str(shape.get("title") or "").strip() != "前往":
            return frame_data_url

        # This is a destructive business action. Never trust a cached frame or
        # a caller's earlier OCR result when deciding whether another attempt
        # may be consumed.
        self._clear_tick_frame(ctx)
        fresh_frame = self._screencap(ctx)
        scene_id, scene_score = self._identify_scene_number(ctx, fresh_frame, preferred_scene_ids=[418])
        if scene_id != 418 or not self._scene_matches_id(418, float(scene_score or 0.0)):
            raise RuntimeError(
                "安全拦截：点击 #418[前往] 前无法在新帧确认 #418，禁止点击"
            )

        lines = self._ocr_fragments_in_shapes(
            fresh_frame,
            image,
            ("次数",),
            padding=12,
            ctx=ctx,
        )
        counter_text = self._ocr_text(lines)
        fraction = parse_ocr_values(counter_text, expected_count=2)
        if fraction is None:
            raise RuntimeError(
                f"安全拦截：无法从 #418[次数] 识别完整分子/分母，禁止点击[前往]，OCR={counter_text!r}"
            )
        numerator, denominator = fraction
        if not 0 <= numerator <= denominator:
            raise RuntimeError(
                f"安全拦截：#418[次数] 数值异常 {numerator}/{denominator}，禁止点击[前往]"
            )
        if numerator == 0:
            raise RuntimeError("安全拦截：#418[次数] 分子为0，禁止点击[前往]")
        self._log("detail", f"安全确认：#418[次数]={numerator}/{denominator}，允许点击[前往]")
        return fresh_frame

    def _shape_match_resolved_click_point(
        self,
        image: dict[str, Any],
        shape: dict[str, Any],
        match_result: dict[str, Any] | None,
        *,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
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
        if bool(match_result.get("floating_ocr")):
            return (
                float(resolved_box.get("x") or 0) + dst_w * float(x_ratio),
                float(resolved_box.get("y") or 0) + dst_h * float(y_ratio),
            )
        if x_ratio == 0.5 and y_ratio == 0.5:
            raw_x, raw_y = ActionPlanner().shape_center(image, shape)
        else:
            width, height = self._frame_size(image)
            raw_x = (float(shape.get("x") or 0) + float(shape.get("w") or 0) * x_ratio) * width
            raw_y = (float(shape.get("y") or 0) + float(shape.get("h") or 0) * y_ratio) * height
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
        *,
        jitter_radius: int = 0,
    ) -> None:
        try:
            if jitter_radius > 0:
                self._click_shape(
                    ctx,
                    image,
                    shape,
                    frame_data_url,
                    jitter_radius=jitter_radius,
                )
            else:
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
        if jitter_radius > 0:
            x, y = self._randomly_perturb_click_point(
                image,
                x,
                y,
                radius=jitter_radius,
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
        return bool(self._shape_match_conditions(shape))

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

    def _click_frame_point(
        self,
        ctx: dict[str, Any],
        image: dict[str, Any],
        x: float,
        y: float,
        *,
        save_action_trace: bool = True,
    ) -> None:
        payload = ActionPlanner().click_point_payload(image, x, y)
        entry: Any = ctx["entry"]
        if save_action_trace:
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

    def _shape_center(
        self,
        shape: dict[str, Any],
        image: dict[str, Any],
        frame_data_url: str | None = None,
        ctx: dict[str, Any] | None = None,
        *,
        strict_live: bool = False,
    ) -> tuple[float, float]:
        if strict_live and not bool(shape.get("floating")):
            raise RuntimeError(
                f"shape 不是 floating，不能严格定位实时中心：{shape.get('title') or shape.get('id')}"
            )
        if strict_live and (not frame_data_url or not isinstance(ctx, dict)):
            raise RuntimeError(
                f"严格定位实时中心缺少直播帧或运行上下文：{shape.get('title') or shape.get('id')}"
            )
        if bool(shape.get("floating")) and frame_data_url and isinstance(ctx, dict):
            try:
                match_result = self._match_shape(ctx, image, shape, frame_data_url)
                resolved = self._shape_match_resolved_click_point(image, shape, match_result)
                if resolved is not None and bool(match_result.get("matched")):
                    return resolved
            except Exception as exc:
                if strict_live:
                    raise RuntimeError(
                        f"浮动 shape 无法严格定位实时中心：{shape.get('title') or shape.get('id')}"
                    ) from exc
                self._log(
                    "detail",
                    f"浮动 shape 中心定位失败，退回参考坐标：{shape.get('title') or shape.get('id')}：{exc}",
                )
            if strict_live:
                raise RuntimeError(
                    f"浮动 shape 未唯一匹配实时中心：{shape.get('title') or shape.get('id')}"
                )
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
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            scene_id, score, frame = runtime.current_scene(
                [target_scene_id],
                frame_data_url=frame,
            )
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
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        while True:
            self._raise_if_stopped(stop_event)
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start
            scene_id, score, frame = runtime.current_scene(
                [121],
                frame_data_url=frame,
            )
            last_scene_id, last_score = scene_id, score
            try:
                text = runtime.ocr_text(frame)
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

    def _ocr_frame(self, frame_data_url: str, *, options: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = _recognize_data_annotation_ocr_frame(frame_data_url, options=options)
        except Exception as exc:
            self._log("detail", f"OCR 失败：{exc}")
            return {"lines": [], "tokens": []}
        return {
            "lines": [line.model_dump() for line in response.lines],
            "tokens": [token.model_dump() for token in response.tokens],
        }

    def _ocr_tokens(self, frame_data_url: str) -> list[dict[str, Any]]:
        response = self._ocr_frame(frame_data_url)
        tokens = response.get("tokens")
        return tokens if isinstance(tokens, list) else []

    def _ocr_fragments(self, frame_data_url: str) -> list[dict[str, Any]]:
        response = self._ocr_frame(frame_data_url)
        lines = response.get("lines")
        return lines if isinstance(lines, list) else []

    def _ocr_fragments_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        options: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(ctx, dict):
            query_box = self._query_box_for_shapes(image, shape_titles, padding=padding, ctx=ctx)
            if query_box is None:
                return []
            cached = self._shared_spatial_ocr_result(ctx, frame_data_url, options=options)
            return query_ocr_lines(cached.get("lines") or [], query_box)
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding, ctx=ctx)
        if crop is None:
            return []
        crop_data_url, offset_x, offset_y = crop
        response = self._ocr_frame(crop_data_url, options=options)
        lines = response.get("lines") if isinstance(response.get("lines"), list) else []
        for line in lines:
            line["x"] = float(line.get("x") or 0) + offset_x
            line["y"] = float(line.get("y") or 0) + offset_y
        return lines

    def _ocr_tokens_in_shapes(
        self,
        frame_data_url: str,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        options: dict[str, Any] | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(ctx, dict):
            query_box = self._query_box_for_shapes(image, shape_titles, padding=padding, ctx=ctx)
            if query_box is None:
                return []
            cached = self._shared_spatial_ocr_result(ctx, frame_data_url, options=options)
            spatial = query_spatial_ocr(cached.get("tokens") or [], query_box)
            return spatial.get("tokens") if isinstance(spatial.get("tokens"), list) else []
        crop = self._crop_frame_data_url_for_shapes(frame_data_url, image, shape_titles, padding=padding, ctx=ctx)
        if crop is None:
            return []
        crop_data_url, offset_x, offset_y = crop
        response = self._ocr_frame(crop_data_url, options=options)
        tokens = response.get("tokens") if isinstance(response.get("tokens"), list) else []
        for token in tokens:
            token["x"] = float(token.get("x") or 0) + offset_x
            token["y"] = float(token.get("y") or 0) + offset_y
        return tokens

    def _query_box_for_shapes(
        self,
        image: dict[str, Any],
        shape_titles: tuple[str, ...] | list[str],
        *,
        padding: int = 16,
        ctx: dict[str, Any] | None = None,
    ) -> dict[str, float] | None:
        boxes: list[dict[str, Any]] = []
        for title in shape_titles:
            shape = self._find_shape(image, str(title))
            if shape:
                source_image = self._effective_shape_source_image(ctx, image, shape) if isinstance(ctx, dict) else image
                boxes.append(self._box(shape, source_image))
                continue
            # Shape inheritance is resolved explicitly through
            # ``parentSceneIds`` before this helper is called.  Physical
            # asset-tree ancestors are editorial structure only.
        if not boxes:
            return None
        width = max(1.0, float(image.get("width") or 900))
        height = max(1.0, float(image.get("height") or 1600))
        left = max(0.0, min(float(box.get("x") or 0) for box in boxes) - padding)
        top = max(0.0, min(float(box.get("y") or 0) for box in boxes) - padding)
        right = min(width, max(float(box.get("x") or 0) + float(box.get("w") or 0) for box in boxes) + padding)
        bottom = min(height, max(float(box.get("y") or 0) + float(box.get("h") or 0) for box in boxes) + padding)
        if right <= left or bottom <= top:
            return None
        return {"x": left, "y": top, "w": right - left, "h": bottom - top}

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
                    # See ``_query_box_for_shapes``: no implicit physical
                    # parent inheritance is allowed here.
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
            self._log("detail", f"裁剪 OCR 区域失败，已按 shape 边界安全失败：{exc}")
            return None

    def _scene_shape_boxes(self, ctx: dict[str, Any], image: dict[str, Any]) -> list[dict[str, float]]:
        boxes: list[dict[str, float]] = []
        for shape in View(image).get_shapes(include_groups=False):
            source_image = self._effective_shape_source_image(ctx, image, shape.raw)
            box = self._box(shape.raw, source_image)
            if float(box.get("w") or 0) > 0 and float(box.get("h") or 0) > 0:
                boxes.append(box)
        return boxes

    @staticmethod
    def _deduplicate_spatial_ocr(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[tuple[str, int, int, int, int], dict[str, Any]] = {}
        for item in items:
            key = (
                _sanitize_ocr_text(item.get("text")),
                round(float(item.get("x") or 0)),
                round(float(item.get("y") or 0)),
                round(float(item.get("w") or 0)),
                round(float(item.get("h") or 0)),
            )
            unique.setdefault(key, item)
        return sorted(unique.values(), key=lambda item: (float(item.get("y") or 0), float(item.get("x") or 0)))

    def _ocr_fragments_in_scene_shapes(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        image: dict[str, Any],
    ) -> list[dict[str, Any]]:
        boxes = self._scene_shape_boxes(ctx, image)
        if not boxes:
            return []
        cached = self._shared_spatial_ocr_result(ctx, frame_data_url)
        lines: list[dict[str, Any]] = []
        for box in boxes:
            lines.extend(query_ocr_lines(cached.get("lines") or [], box))
        return self._deduplicate_spatial_ocr(lines)

    def _ocr_tokens_in_scene_shapes(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        image: dict[str, Any],
    ) -> list[dict[str, Any]]:
        boxes = self._scene_shape_boxes(ctx, image)
        if not boxes:
            return []
        cached = self._shared_spatial_ocr_result(ctx, frame_data_url, options={"return_word_box": True})
        tokens: list[dict[str, Any]] = []
        for box in boxes:
            spatial = query_spatial_ocr(cached.get("tokens") or [], box)
            tokens.extend(spatial.get("tokens") or [])
        return self._deduplicate_spatial_ocr(tokens)

    def _recognized_scene_image(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> dict[str, Any] | None:
        scene_id, _score = self._identify_scene_number(ctx, frame_data_url, preferred_scene_ids)
        if scene_id is None:
            return None
        image = (ctx.get("images") or {}).get(int(scene_id))
        return image if isinstance(image, dict) else None

    def _recognized_scene_ocr_fragments(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        image = self._recognized_scene_image(ctx, frame_data_url, preferred_scene_ids)
        if image is None:
            return []
        return self._ocr_fragments_in_scene_shapes(ctx, frame_data_url, image)

    def _recognized_scene_ocr_tokens(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        image = self._recognized_scene_image(ctx, frame_data_url, preferred_scene_ids)
        if image is None:
            return []
        return self._ocr_tokens_in_scene_shapes(ctx, frame_data_url, image)

    def _recognized_scene_ocr_text(
        self,
        ctx: dict[str, Any],
        frame_data_url: str,
        preferred_scene_ids: list[int] | None = None,
    ) -> str:
        return self._ocr_text(
            self._recognized_scene_ocr_fragments(ctx, frame_data_url, preferred_scene_ids)
        )

    def _ocr_text(self, fragments: list[dict[str, Any]]) -> str:
        return "".join(_sanitize_ocr_text(fragment.get("text")) for fragment in fragments)

    def _ocr_fragment_box(self, fragment: dict[str, Any]) -> dict[str, float] | None:
        x = float(fragment.get("x") or 0)
        y = float(fragment.get("y") or 0)
        w = float(fragment.get("w") or 0)
        h = float(fragment.get("h") or 0)
        if w <= 0 or h <= 0:
            return None
        return {"x": x, "y": y, "w": w, "h": h}

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
        fragments: list[dict[str, Any]],
        image: dict[str, Any] | None,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        tokens: list[dict[str, Any]] | None = None,
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
        for fragment in fragments:
            text = _sanitize_ocr_text(fragment.get("text"))
            if not text:
                continue
            if include and not all(fragment in text for fragment in include):
                continue
            if exclude and any(fragment in text for fragment in exclude):
                continue
            fragment_x = float(fragment.get("x") or 0)
            fragment_w = float(fragment.get("w") or 0)
            cx = fragment_x + fragment_w / 2
            if include and fragment_w > 0 and text:
                target_fragment = next((fragment for fragment in include if fragment in text), "")
                if target_fragment:
                    token_box = locate_text_box(tokens or [], target_fragment)
                    if token_box is not None:
                        cx = float(token_box["x"]) + float(token_box["w"]) / 2
                        cy = float(token_box["y"]) + float(token_box["h"]) / 2
                    else:
                        cy = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
                else:
                    cy = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
            else:
                cy = float(fragment.get("y") or 0) + float(fragment.get("h") or 0) / 2
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
        values = parse_ocr_values(_sanitize_ocr_text(text), expected_count=2)
        return (values[0], values[1]) if values is not None else None

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

    def _xianfu_home_text_is_scene(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        world_markers = (
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

    def _scene_jump_source_stall_timeout(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
        shape: dict[str, Any],
    ) -> float:
        # Business-specific transition duration belongs in
        # ``layer0_wait_seconds``.  The navigation core keeps only a generic
        # minimum and extends it with that caller-supplied wait window.
        return 8.0

    def _scene_jump_preferred_wait_seconds(
        self,
        *,
        source_scene_id: int,
        target_scene_id: int,
        expected_ids: list[int],
        shape: dict[str, Any],
        requested_wait_seconds: float | None,
    ) -> float:
        preferred = max(
            0.0,
            float(
                requested_wait_seconds
                if requested_wait_seconds is not None
                else (DEFAULT_LAYER0_WAIT_SECONDS if expected_ids else 0.0)
            ),
        )
        # #609 is the offline-cultivation acknowledgement shown after login.
        # Its confirmed landing can be either #34 or #20.  A stream of reward
        # banners may temporarily cover #20's required identity even though no
        # further input is needed.  Keep the exact declared Layer-0 candidates
        # alive until the banners settle; a reliable #20/#34 match still
        # returns immediately, so the longer ceiling adds no normal-path cost.
        if (
            int(source_scene_id) == 609
            and int(target_scene_id) == 34
            and str(shape.get("title") or "").strip() == "确定"
            and {20, 34}.issubset({int(item) for item in expected_ids})
        ):
            preferred = max(preferred, OFFLINE_CULTIVATION_SETTLE_WAIT_SECONDS)
        return preferred

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
        ctx.pop("_last_scene_jump_evidence", None)
        shape = edge["shape"]
        expected_ids = list(edge.get("target_ids") or [])
        allows_self = source_scene_id in expected_ids
        preferred_wait_seconds = self._scene_jump_preferred_wait_seconds(
            source_scene_id=source_scene_id,
            target_scene_id=target_scene_id,
            expected_ids=expected_ids,
            shape=shape,
            requested_wait_seconds=layer0_wait_seconds,
        )
        popup_guard_delay = min(preferred_wait_seconds / 2.0, 5.0)
        timeout_seconds = max(30.0 if allows_self else 60.0, preferred_wait_seconds)
        start = time.monotonic()
        last_scene_id: int | None = None
        last_score = 0.0
        last_frame = ""
        history: list[str] = []
        left_source = False
        handled_intermediate_scene_ids: set[int] = set()
        shape_jump_target = str(shape.get("sceneJumpTarget") or "").strip()
        dynamic_landing = bool(edge.get("_runtime_confirm_edge")) or shape_jump_target == "-1" or shape_jump_target.startswith("-1(")
        source_stall_timeout = self._scene_jump_source_stall_timeout(
            source_scene_id=source_scene_id,
            target_scene_id=target_scene_id,
            expected_ids=expected_ids,
            shape=shape,
        )
        source_stall_timeout = max(source_stall_timeout, preferred_wait_seconds)
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path,
            stop_event=stop_event,
        )
        source_shape_title = str(shape.get("title") or "").strip()
        confirmation_scene_ids = (
            self._scene_jump_confirmation_scene_ids(tree)
            if source_shape_title in {"离开", "返回", "关闭", "退出"}
            else []
        )
        first_poll = True

        def remember_landing(
            scene_id: int | None,
            score: float,
            frame_data_url: str,
            elapsed_seconds: float,
            *,
            outcome: str,
        ) -> None:
            ctx["_last_scene_jump_evidence"] = {
                "scene_id": scene_id,
                "score": float(score or 0.0),
                "frame_data_url": frame_data_url,
                "elapsed_seconds": float(elapsed_seconds),
                "history": list(history[-40:]),
                "outcome": outcome,
            }

        while True:
            self._raise_if_stopped(stop_event)
            if not first_poll:
                # Layer-0 waiting is a real fresh-frame polling window, not a
                # tight loop whose pace accidentally depends on the outer
                # behavior-tree driver.  The explicit interruptible wait also
                # gives long UI transitions time to publish their stable HUD.
                yield from self._wait_runtime_action_settle(
                    ctx,
                    stop_event,
                    seconds=DEFAULT_SCENE_RECOGNITION_POLL_SECONDS,
                )
            first_poll = False
            self._clear_tick_frame(ctx)
            yield BehaviorTreeStatus.RUNNING
            frame = self._screencap(ctx)
            elapsed = time.monotonic() - start

            # A click such as 「离开」may synchronously open a confirmation
            # popup.  Keep that popup in the transition's explicit Layer-0
            # candidate domain before the general popup domain is evaluated.
            # Otherwise a broad parent such as #47 can legitimately match the
            # same frame first and execute its generic close action, cancelling
            # the leave operation before #86 gets a chance to confirm it.
            if confirmation_scene_ids:
                confirmation_scene_id, confirmation_score = self._identify_scene_number(
                    ctx,
                    frame,
                    confirmation_scene_ids,
                )
                if (
                    confirmation_scene_id is not None
                    and confirmation_scene_id not in handled_intermediate_scene_ids
                ):
                    confirmation_image = (ctx.get("images") or {}).get(int(confirmation_scene_id))
                    confirmation_shape = self._scene_jump_intermediate_confirm_shape(
                        confirmation_image,
                        shape,
                    )
                    if confirmation_shape is not None:
                        confirm_title = str(confirmation_shape.get("title") or "确认")
                        handled_intermediate_scene_ids.add(int(confirmation_scene_id))
                        with self._lock:
                            self._status.update({
                                "phase": "go_scene_confirm",
                                "current_scene": int(confirmation_scene_id),
                                "message": (
                                    f"跳转确认：#{source_scene_id} -> #{target_scene_id}，"
                                    f"点击 {confirm_title}"
                                ),
                                "updated_at": time.time(),
                            })
                        self._log(
                            "action",
                            (
                                f"场景跳转确认：#{int(confirmation_scene_id)} "
                                f"{float(confirmation_score or 0.0):.0f}%，点击 {confirm_title}"
                            ),
                        )
                        self._click_shape(
                            ctx,
                            confirmation_image,
                            confirmation_shape,
                            frame,
                        )
                        left_source = True
                        start = time.monotonic()
                        continue

            expected_id_set = {int(item) for item in expected_ids}
            fallback_scene_id: int | None = None
            fallback_score = 0.0
            matched_expected, expected_score, frame = runtime.current_scene(
                expected_ids,
                frame_data_url=frame,
                include_popup_candidates=elapsed >= popup_guard_delay,
            )
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
                    default_scene_id, default_score = self._identify_scene_number(ctx, frame)
                    if default_scene_id == source_scene_id and float(default_score or 0) >= float(expected_score or 0):
                        last_scene_id, last_score, last_frame = default_scene_id, default_score, frame
                        history.append(
                            f"{elapsed:.1f}s #{default_scene_id} {default_score:.0f}% "
                            f"expected=#{matched_expected} {expected_score:.0f}% left={left_source}"
                        )
                        continue
                last_scene_id, last_score, last_frame = matched_expected, expected_score, frame
                if matched_expected != source_scene_id:
                    left_source = True
                history.append(f"{elapsed:.1f}s #{matched_expected} {expected_score:.0f}% expected={expected_score:.0f}% left={left_source}")
                if not edge.get("_runtime_confirm_edge"):
                    self._record_scene_jump_landing(
                        ctx,
                        asset_tree_path,
                        tree,
                        shape,
                        int(matched_expected),
                        reason="命中声明落点",
                    )
                self._log("info", f"场景跳转：#{source_scene_id} -> #{matched_expected}，{elapsed:.1f}s")
                remember_landing(int(matched_expected), float(expected_score or 0.0), frame, elapsed, outcome="declared_landing")
                return matched_expected

            if expected_ids and preferred_wait_seconds > 0 and elapsed < preferred_wait_seconds:
                route_candidate_ids = [
                    candidate_id
                    for candidate_id in self._scene_route_candidate_ids(tree, target_scene_id)
                    if int(candidate_id) not in expected_id_set
                    and int(candidate_id) != int(source_scene_id)
                ]
                route_scene_id, route_score = self._identify_scene_number_for_route(
                    ctx,
                    frame,
                    tree,
                    target_scene_id,
                    route_candidate_ids,
                )
                if (
                    route_scene_id is not None
                    and self._find_scene_route(tree, int(route_scene_id), target_scene_id) is not None
                ):
                    fallback_scene_id = int(route_scene_id)
                    fallback_score = float(route_score or 0.0)
                    history.append(
                        f"{elapsed:.1f}s #{fallback_scene_id} {fallback_score:.0f}% "
                        f"route-capable-layer0-interruption target={expected_ids}"
                    )
                else:
                    last_scene_id, last_score, last_frame = None, expected_score, frame
                    history.append(f"{elapsed:.1f}s unknown layer0-wait target={expected_ids}")
                    with self._lock:
                        self._status.update({
                            "phase": "go_scene_wait_layer0",
                            "current_scene": None,
                            "message": (
                                f"跳转等待：#{source_scene_id} -> #{target_scene_id}，"
                                f"继续等待预期落点 {expected_ids}，当前 layer0 未命中 {expected_score:.0f}%"
                            ),
                            "updated_at": time.time(),
                        })
                    continue

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
                # Do not keep the original auxiliary reference id when it is
                # not a reliable navigation scene.
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
            if scene_id is not None and scene_id != source_scene_id and int(scene_id) in expected_ids:
                left_source = True
                history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% declared-landing left={left_source}")
                if not edge.get("_runtime_confirm_edge"):
                    self._record_scene_jump_landing(
                        ctx,
                        asset_tree_path,
                        tree,
                        shape,
                        int(scene_id),
                        reason="命中声明落点",
                    )
                self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，命中声明落点")
                remember_landing(int(scene_id), float(score or 0.0), frame, elapsed, outcome="declared_landing")
                return int(scene_id)
            if (
                scene_id is not None
                and scene_id != source_scene_id
                and int(scene_id) not in expected_id_set
                and scene_id != target_scene_id
                and expected_ids
                and preferred_wait_seconds > 0
                and elapsed < preferred_wait_seconds
                and self._find_scene_route(tree, int(scene_id), target_scene_id) is None
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
            # A/B/C in sceneJumpTarget are historical observations, not a
            # whitelist.  A reliably recognized new D is equally valid: record
            # it in the same frequency table and let the outer goto planner
            # continue from D.  Only true unknown/recovery exhaustion is fatal.
            if scene_id is not None and scene_id != source_scene_id:
                left_source = True
                history.append(f"{elapsed:.1f}s #{scene_id} {score:.0f}% observed-landing left={left_source}")
                if not dynamic_landing:
                    self._record_scene_jump_landing(
                        ctx,
                        asset_tree_path,
                        tree,
                        shape,
                        int(scene_id),
                        reason="实际识别落点",
                    )
                route_exists = self._find_scene_route(tree, int(scene_id), target_scene_id) is not None
                if scene_id == target_scene_id:
                    self._log("info", f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，到达目标")
                elif route_exists:
                    self._log(
                        "info",
                        f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，记录落点并重新规划到 #{target_scene_id}",
                    )
                else:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} -> #{scene_id}，{elapsed:.1f}s，已记录新落点；上层将尝试通用恢复",
                    )
                remember_landing(int(scene_id), float(score or 0.0), frame, elapsed, outcome="observed_landing")
                return int(scene_id)
            if scene_id is not None and scene_id != source_scene_id:
                left_source = True
            scene_text = f"#{scene_id}" if scene_id is not None else "unknown"
            history.append(f"{elapsed:.1f}s {scene_text} {score:.0f}% expected={expected_score:.0f}% left={left_source}")
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
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    source_scene_id,
                    reason="点击后稳定识别仍在源场景",
                )
                if return_source_on_stall:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"{elapsed:.1f}s 后仍停在源场景，回到动态选择尝试其它候选",
                    )
                    remember_landing(source_scene_id, float(last_score or score or 0.0), last_frame or frame, elapsed, outcome="source_stall")
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
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    source_scene_id,
                    reason="声明自环并稳定识别源场景",
                )
                self._log("info", f"场景跳转：#{source_scene_id} -> #{source_scene_id}，30s 保底确认自身")
                remember_landing(source_scene_id, float(last_score or 0.0), last_frame or frame, elapsed, outcome="declared_self_loop")
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
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    source_scene_id,
                    reason="点击后超时仍稳定识别源场景",
                )
                if return_source_on_stall:
                    self._log(
                        "warning",
                        f"场景跳转：#{source_scene_id} 点击「{shape.get('title') or '未命名'}」"
                        f"{elapsed:.1f}s 后仍停在源场景，回到动态选择尝试其它候选",
                    )
                    remember_landing(source_scene_id, float(last_score or 0.0), last_frame or frame, elapsed, outcome="source_stall")
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

            if not dynamic_landing:
                self._record_scene_jump_landing(
                    ctx,
                    asset_tree_path,
                    tree,
                    shape,
                    int(last_scene_id),
                    reason="超时前稳定识别落点",
                )
            self._log(
                "warning",
                f"场景跳转：#{source_scene_id} -> #{last_scene_id}，超时前稳定识别；记录落点并交由上层重新规划",
            )
            remember_landing(int(last_scene_id), float(last_score or 0.0), last_frame or frame, elapsed, outcome="timeout_landing")
            return int(last_scene_id)

    def _wait_for_go_scene_recognition(
        self,
        ctx: dict[str, Any],
        runtime: BehaviorTreeRuntime,
        tree: list[dict[str, Any]],
        target_scene_id: int,
        stop_event: threading.Event,
        initial_frame: str,
        *,
        wait_seconds: float = DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
        max_wait_seconds: float = DEFAULT_GO_SCENE_OBSERVATION_TIMEOUT_SECONDS,
        immediate_unknown_fallback: bool = False,
    ):
        """Keep observing until a scene is known or unknown is continuous.

        Unknown is not a navigation node.  Every pass consumes a fresh full
        recognition result and yields without action until ``wait_seconds``
        has elapsed without a reliable Layer 0/1/2 scene id.  Layer 3 scores
        and unresolved ambiguity remain auxiliary evidence for the unknown.
        """

        started_at = time.monotonic()
        wait_seconds = max(0.0, float(wait_seconds))
        max_wait_seconds = max(wait_seconds, float(max_wait_seconds))
        frame = initial_frame
        best_score = 0.0
        best_layer3_auxiliary: dict[str, Any] | None = None
        attempts = 0
        continuous_unknown_started_at: float | None = None
        route_candidate_ids = self._scene_route_candidate_ids(tree, target_scene_id)
        ctx.pop("_last_go_scene_recognition_wait_elapsed", None)
        ctx.pop("_last_go_scene_recognition_evidence", None)

        while True:
            self._raise_if_stopped(stop_event)
            attempts += 1
            ctx.pop("_last_scene_recognition_status", None)
            current_scene_id, score, frame = runtime.current_scene(frame_data_url=frame)
            recognition_status = str(
                ctx.pop(
                    "_last_scene_recognition_status",
                    "matched" if current_scene_id is not None else "no_match",
                )
                or "no_match"
            )
            best_score = max(best_score, float(score or 0.0))
            layer3_auxiliary = ctx.get("_last_layer3_auxiliary")
            if isinstance(layer3_auxiliary, dict) and (
                best_layer3_auxiliary is None
                or float(layer3_auxiliary.get("score") or 0.0)
                > float(best_layer3_auxiliary.get("score") or 0.0)
            ):
                best_layer3_auxiliary = dict(layer3_auxiliary)

            if current_scene_id is None:
                current_scene_id, route_score = self._identify_scene_number_for_route(
                    ctx,
                    frame,
                    tree,
                    target_scene_id,
                    route_candidate_ids,
                )
                score = max(float(score or 0.0), float(route_score or 0.0))
                best_score = max(best_score, float(score or 0.0))
                if current_scene_id is not None:
                    recognition_status = "route_candidate"

            if current_scene_id is not None and self._scene_matches_id(
                int(current_scene_id),
                float(score or 0.0),
            ):
                # A first-pass match did not wait.  Avoid another clock read
                # here: callers only need the actual duration for an eventual
                # unknown result, and navigation tests deliberately advance a
                # fake clock when recording historical landings.
                ctx["_last_go_scene_recognition_wait_elapsed"] = 0.0
                if attempts > 1:
                    self._log(
                        "info",
                        f"场景移动：整体识别等待后命中 #{current_scene_id} {float(score or 0.0):.0f}%",
                    )
                return current_scene_id, float(score or 0.0), frame, recognition_status

            now = time.monotonic()
            elapsed = now - started_at
            # Any result without a reliable scene id is still unknown for
            # navigation.  The initial frame already exists when this wait
            # starts, so its first unresolved result is timed from started_at;
            # starting after recognition finishes makes equal 60s limits
            # mathematically impossible to satisfy in real runtime.
            if continuous_unknown_started_at is None:
                continuous_unknown_started_at = started_at if attempts == 1 else now
            continuous_unknown_seconds = (
                max(0.0, now - continuous_unknown_started_at)
                if continuous_unknown_started_at is not None
                else 0.0
            )
            immediate_unknown_qualified = (
                bool(immediate_unknown_fallback)
                and recognition_status not in {"ambiguous"}
            )
            if immediate_unknown_qualified or continuous_unknown_seconds >= wait_seconds:
                ctx["_last_go_scene_recognition_wait_elapsed"] = elapsed
                ctx["_last_go_scene_recognition_evidence"] = {
                    "attempts": attempts,
                    "continuous_unknown_seconds": continuous_unknown_seconds,
                    "best_score": best_score,
                    "best_layer3_auxiliary": best_layer3_auxiliary,
                    "last_unresolved_status": recognition_status,
                }
                layer3_text = ""
                if best_layer3_auxiliary is not None:
                    layer3_text = (
                        f"，Layer 3 辅助参考 #{best_layer3_auxiliary.get('reference_id')} "
                        f"{float(best_layer3_auxiliary.get('score') or 0.0):.0f}%（非场景 ID）"
                    )
                self._log(
                    "warning",
                    f"场景移动：完整识别为 unknown（连续 {continuous_unknown_seconds:.1f}s），"
                    f"获得一次 #424 恢复资格，最佳分数 {best_score:.0f}%{layer3_text}",
                )
                return None, best_score, frame, "continuous_unknown"
            if elapsed >= max_wait_seconds:
                ctx["_last_go_scene_recognition_wait_elapsed"] = elapsed
                final_status = "ambiguous" if recognition_status == "ambiguous" else "observation_timeout"
                self._log(
                    "warning",
                    f"场景移动：观察达到 {max_wait_seconds:.1f}s，但未形成连续 "
                    f"{wait_seconds:.1f}s unknown；结果={final_status}，禁止执行 #424",
                )
                return None, best_score, frame, final_status

            unknown_remaining = wait_seconds - continuous_unknown_seconds
            remaining = min(unknown_remaining, max_wait_seconds - elapsed)
            with self._lock:
                self._status.update({
                    "phase": "go_scene_wait_recognition",
                    "current_scene": None,
                    "message": (
                        f"场景移动：未识别到可靠场景，保持当前业务等待；"
                        f"连续 unknown {continuous_unknown_seconds:.1f}/{wait_seconds:.1f}s，"
                        f"总等待 {elapsed:.1f}/{max_wait_seconds:.1f}s"
                    ),
                    "updated_at": time.time(),
                })
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=min(DEFAULT_SCENE_RECOGNITION_POLL_SECONDS, remaining),
            )
            frame = self._screencap(ctx)

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

        failed_edge_keys_by_state: dict[str, set[tuple[Any, ...]]] = {}
        last_failed_edges_by_state: dict[str, dict[str, Any]] = {}
        explored_shape_keys: set[tuple[str, str, str]] = set()
        navigation_fallback_attempts: dict[tuple[str, str], dict[str, float | int]] = {}
        navigation_states: list[tuple[int | None, bytes, str]] = []
        stalled_edge_attempts: dict[tuple[Any, ...], int] = {}
        semantic_stalled_edge_attempts: dict[tuple[Any, ...], int] = {}
        globally_failed_edge_keys: set[tuple[Any, ...]] = set()
        navigation_started_at = time.monotonic()
        incident_recorder = NavigationIncidentRecorder(
            self,
            ctx,
            asset_tree_path,
            target_scene_id=target_scene_id,
            started_monotonic=navigation_started_at,
        )
        ctx["_navigation_incident_recorder"] = incident_recorder
        last_navigation_frame = ""
        last_navigation_scene_id: int | None = None
        last_navigation_score = 0.0
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path,
            stop_event=stop_event,
        )

        def confirm_target_on_fresh_frame():
            """Reject a cached/transient target hit before reporting success."""
            self._clear_tick_frame(ctx)
            yield from self._wait_runtime_action_settle(
                ctx,
                stop_event,
                seconds=DEFAULT_SCENE_RECOGNITION_POLL_SECONDS,
            )
            self._clear_tick_frame(ctx)
            fresh_frame = self._screencap(ctx)
            full_scene_id, full_score = self._identify_scene_number(ctx, fresh_frame)
            confirmed = (
                full_scene_id == int(target_scene_id)
                and self._scene_matches_id(int(target_scene_id), float(full_score or 0.0))
            )
            observed_scene_id = (
                int(full_scene_id)
                if full_scene_id is not None
                and self._scene_matches_id(int(full_scene_id), float(full_score or 0.0))
                else None
            )
            return confirmed, observed_scene_id, float(full_score or 0.0), fresh_frame

        for _step_index in range(NAVIGATION_MAX_REPLAN_STEPS):
            self._raise_if_stopped(stop_event)
            if time.monotonic() - navigation_started_at >= NAVIGATION_STALL_MAX_SECONDS:
                incident_recorder.trigger(
                    trigger_type="duration_limit",
                    trigger_label="活跃导航累计 10 分钟仍未到达目标",
                    threshold={"max_duration_seconds": NAVIGATION_STALL_MAX_SECONDS},
                    frame_data_url=last_navigation_frame,
                    current_scene_id=last_navigation_scene_id,
                    current_score=last_navigation_score,
                    candidate_scene_ids=[
                        scene_id
                        for scene_id in (last_navigation_scene_id, target_scene_id)
                        if scene_id is not None
                    ],
                )
                incident_recorder.finalize(
                    status="unrecovered",
                    final_scene_id=last_navigation_scene_id,
                    final_score=last_navigation_score,
                    final_frame=last_navigation_frame,
                    message=f"导航达到 {NAVIGATION_STALL_MAX_SECONDS:.0f} 秒硬上限",
                )
                raise RuntimeError(f"场景移动停滞超过 {NAVIGATION_STALL_MAX_SECONDS:.0f} 秒，未到达 #{target_scene_id}")
            frame = self._screencap(ctx)
            last_navigation_frame = frame
            known_scene_id = ctx.pop("_go_scene_known_scene_id", None)
            if known_scene_id is not None:
                current_scene_id, score = int(known_scene_id), float(self.scene_threshold)
                recognition_status = "known_landing"
            else:
                transition_guard = ctx.get("_go_scene_unknown_transition_guard")
                guarded_transition = False
                guarded_wait_seconds = DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS
                if int(target_scene_id) == 34 and isinstance(transition_guard, dict):
                    try:
                        reference_scene_id = int(transition_guard.get("reference_scene_id") or 0)
                        threshold = float(transition_guard.get("similarity_threshold") or 94.0)
                        reference_image = (ctx.get("images") or {}).get(reference_scene_id)
                        similarity = (
                            self._scene_reference_similarity(ctx, reference_image, frame)
                            if isinstance(reference_image, dict) and reference_scene_id > 0
                            else None
                        )
                    except (TypeError, ValueError):
                        similarity = None
                    if similarity is not None and similarity >= threshold:
                        guarded_transition = True
                        guarded_wait_seconds = max(
                            DEFAULT_GO_SCENE_CONTINUOUS_UNKNOWN_SECONDS,
                            float(transition_guard.get("wait_seconds") or 120.0),
                        )
                        phase = str(transition_guard.get("phase") or "go_scene_wait_guarded_transition")
                        label = str(transition_guard.get("label") or "动态回城过场")
                        if not transition_guard.get("announced"):
                            transition_guard["announced"] = True
                            self._log(
                                "wait",
                                f"场景移动：检测到{label}（与 #{reference_scene_id} 全帧相似 "
                                f"{similarity:.0f}%），只等待可靠场景自然落地",
                            )
                        with self._lock:
                            self._status.update({
                                "phase": phase,
                                "current_scene": None,
                                "message": f"场景移动：{label}中，等待自然落到 #34",
                                "updated_at": time.time(),
                            })
                current_scene_id, score, frame, recognition_status = yield from self._wait_for_go_scene_recognition(
                    ctx,
                    runtime,
                    tree,
                    target_scene_id,
                    stop_event,
                    frame,
                    wait_seconds=guarded_wait_seconds,
                    max_wait_seconds=guarded_wait_seconds,
                    immediate_unknown_fallback=int(target_scene_id) == 34 and not guarded_transition,
                )
            recognition_wait_elapsed = float(
                ctx.pop("_last_go_scene_recognition_wait_elapsed", 0.0) or 0.0
            )
            last_navigation_frame = frame
            last_navigation_scene_id = current_scene_id
            last_navigation_score = float(score or 0.0)
            navigation_state_key = self._navigation_state_key(frame, current_scene_id, navigation_states)
            failed_edge_keys = failed_edge_keys_by_state.setdefault(navigation_state_key, set())
            failed_edge_keys.update(globally_failed_edge_keys)
            last_failed_edge = last_failed_edges_by_state.get(navigation_state_key)
            if current_scene_id is None:
                if recognition_status != "continuous_unknown":
                    return self._save_unknown_scene_frame(
                        ctx,
                        asset_tree_path,
                        tree,
                        frame,
                        target_scene_id=target_scene_id,
                        current_scene_id=None,
                        action_shape=None,
                        elapsed_seconds=recognition_wait_elapsed,
                        history=[
                            f"整体识别等待 {recognition_wait_elapsed:.1f}s 后为 {recognition_status} "
                            f"{score:.0f}%；未形成连续一分钟 unknown，禁止执行 #424"
                        ],
                    )
                if (yield from self._wait_or_click_navigation_fallback_return(
                    ctx,
                    frame,
                    stop_event,
                    navigation_state_key=navigation_state_key,
                    target_scene_id=target_scene_id,
                    attempted_actions=navigation_fallback_attempts,
                    current_score=score,
                    incident_recorder=incident_recorder,
                )):
                    continue
                return self._save_unknown_scene_frame(
                    ctx,
                    asset_tree_path,
                    tree,
                    frame,
                    target_scene_id=target_scene_id,
                    current_scene_id=None,
                    action_shape=None,
                    elapsed_seconds=recognition_wait_elapsed,
                    history=[
                        f"完整识别管线连续 {recognition_wait_elapsed:.1f}s "
                        f"unknown，最佳分数 {score:.0f}%"
                    ],
                )
            if current_scene_id == target_scene_id and self._scene_matches_id(int(target_scene_id), float(score or 0.0)):
                confirmed, observed_scene_id, observed_score, fresh_frame = yield from confirm_target_on_fresh_frame()
                if not confirmed:
                    observed_text = f"#{observed_scene_id}" if observed_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        f"场景移动：#{target_scene_id} 首次命中未通过新帧确认，当前 {observed_text} "
                        f"{observed_score:.0f}%，继续重新规划",
                    )
                    if observed_scene_id is not None:
                        ctx["_go_scene_known_scene_id"] = observed_scene_id
                    last_navigation_frame = fresh_frame
                    continue
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=score,
                    final_frame=frame,
                    message="重新规划后到达目标场景",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"
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
                navigation_state_key = self._navigation_state_key(frame, current_scene_id, navigation_states)
                failed_edge_keys = failed_edge_keys_by_state.setdefault(navigation_state_key, set())
                failed_edge_keys.update(globally_failed_edge_keys)
                last_failed_edge = last_failed_edges_by_state.get(navigation_state_key)
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
                    history=[f"弱兜底匹配不可作为导航起点 {score:.0f}%；禁止直接执行 #424"],
                )
            if current_scene_id == target_scene_id and self._scene_matches_id(int(target_scene_id), float(score or 0.0)):
                confirmed, observed_scene_id, observed_score, fresh_frame = yield from confirm_target_on_fresh_frame()
                if not confirmed:
                    observed_text = f"#{observed_scene_id}" if observed_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        f"场景移动：#{target_scene_id} 首次命中未通过新帧确认，当前 {observed_text} "
                        f"{observed_score:.0f}%，继续重新规划",
                    )
                    if observed_scene_id is not None:
                        ctx["_go_scene_known_scene_id"] = observed_scene_id
                    last_navigation_frame = fresh_frame
                    continue
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"已在目标场景 #{target_scene_id}")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=score,
                    final_frame=frame,
                    message="重新规划后到达目标场景",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"

            decision = self._select_scene_next_edge(
                tree,
                current_scene_id,
                target_scene_id,
                failed_edge_keys=failed_edge_keys,
            )
            current_image = (
                (ctx.get("images") or {}).get(int(current_scene_id))
                if isinstance(ctx.get("images"), dict)
                else None
            )
            current_is_layer1_hub = (
                isinstance(current_image, dict)
                and int(View(current_image).layer) == 1
            )
            if decision is None:
                if (
                    int(current_scene_id or 0) == 611
                    and int(target_scene_id) == 34
                    and (yield from self._wait_or_click_navigation_fallback_return(
                        ctx,
                        frame,
                        stop_event,
                        navigation_state_key=navigation_state_key,
                        target_scene_id=target_scene_id,
                        attempted_actions=navigation_fallback_attempts,
                        current_scene_id=current_scene_id,
                        current_score=score,
                        incident_recorder=incident_recorder,
                    ))
                ):
                    continue
                # Layer-1 scenes are stable navigation hubs.  Missing a route
                # from a hub is an asset/business-path defect, not permission
                # to click generic blank/return actions and manufacture an
                # unknown transition.
                if isinstance(current_image, dict) and not current_is_layer1_hub:
                    decision = self._select_scene_exploration_edge(
                        tree,
                        current_image,
                        current_scene_id,
                        target_scene_id,
                        failed_edge_keys=failed_edge_keys,
                        explored_shape_keys=explored_shape_keys,
                        navigation_state_key=navigation_state_key,
                    )
            if decision is None:
                if current_is_layer1_hub:
                    incident_recorder.trigger(
                        trigger_type="layer1_route_missing",
                        trigger_label="Layer 1 枢纽缺少到目标的可靠路径，拒绝猜测点击",
                        threshold={"failed_edge_count": len(failed_edge_keys)},
                        frame_data_url=frame,
                        current_scene_id=current_scene_id,
                        current_score=score,
                        candidate_scene_ids=[current_scene_id, target_scene_id],
                    )
                    incident_recorder.finalize(
                        status="unrecovered",
                        final_scene_id=current_scene_id,
                        final_score=score,
                        final_frame=frame,
                        message="Layer 1 枢纽缺少可靠路径，已保留现场",
                    )
                    ctx.pop("_navigation_incident_recorder", None)
                    raise RuntimeError(
                        f"go_scene({target_scene_id}) 失败：当前 #{current_scene_id} 是 Layer 1 枢纽，"
                        "但场景图没有可靠路径；已拒绝空白/#424 猜测点击。"
                    )
                if last_failed_edge is not None:
                    failed_shape = last_failed_edge.get("shape") if isinstance(last_failed_edge, dict) else None
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
                incident_recorder.trigger(
                    trigger_type="normal_actions_exhausted",
                    trigger_label="当前已知场景没有可到达目标的低风险动作，拒绝冒充 unknown 使用 #424",
                    threshold={"failed_edge_count": len(failed_edge_keys)},
                    frame_data_url=frame,
                    current_scene_id=current_scene_id,
                    current_score=score,
                    candidate_scene_ids=[current_scene_id, target_scene_id],
                )
                incident_recorder.finalize(
                    status="unrecovered",
                    final_scene_id=current_scene_id,
                    final_score=score,
                    final_frame=frame,
                    message="无可用低风险导航动作",
                )
                ctx.pop("_navigation_incident_recorder", None)
                raise RuntimeError(
                    f"go_scene({target_scene_id}) 失败：无法从当前#{current_scene_id}找到可达#{target_scene_id}的路径，请检查标注shape。"
                )
            edge = decision["edge"]
            image = edge["image"]
            shape = edge["shape"]
            shape_title = str(shape.get("title") or "未命名")
            if int(target_scene_id) == 34:
                # Returning to the stable world anchor is common and a false
                # positive here is unusually destructive: one stale/animated
                # frame once identified the real world as #69 and immediately
                # clicked #69「退出」at the lower-left world entry.  Require a
                # fresh-frame confirmation before every return-to-world click.
                self._clear_tick_frame(ctx)
                yield BehaviorTreeStatus.RUNNING
                confirm_frame = self._screencap(ctx)
                confirm_scene_id, confirm_score = self._identify_scene_number(ctx, confirm_frame)
                if (
                    confirm_scene_id != current_scene_id
                    or not self._scene_matches_id(int(confirm_scene_id), float(confirm_score or 0.0))
                ):
                    confirm_text = f"#{confirm_scene_id}" if confirm_scene_id is not None else "unknown"
                    self._log(
                        "warning",
                        (
                            f"场景移动：回 #34 前新帧由 #{current_scene_id} 变为 {confirm_text} "
                            f"{float(confirm_score or 0.0):.0f}%，取消本次「{shape_title}」点击并重新规划"
                        ),
                    )
                    if confirm_scene_id is not None and self._scene_matches_id(int(confirm_scene_id), float(confirm_score or 0.0)):
                        ctx["_go_scene_known_scene_id"] = int(confirm_scene_id)
                    continue
                frame = confirm_frame
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
            if edge.get("_dynamic_exploration"):
                explored_shape_keys.add((
                    navigation_state_key,
                    str(shape.get("id") or ""),
                    str(shape.get("title") or ""),
                ))
            semantic_retry_key = (
                int(target_scene_id),
                *self._scene_jump_edge_semantic_key(edge),
            )
            no_response_count = semantic_stalled_edge_attempts.get(semantic_retry_key, 0)
            jitter_radius = self._navigation_retry_jitter_radius(no_response_count)
            self._click_scene_route_shape(
                ctx,
                image,
                shape,
                frame,
                jitter_radius=jitter_radius,
            )
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
            landing_evidence = ctx.pop("_last_scene_jump_evidence", None)
            after_frame = (
                str(landing_evidence.get("frame_data_url") or "")
                if isinstance(landing_evidence, dict)
                else ""
            )
            landing_score = (
                float(landing_evidence.get("score") or 0.0)
                if isinstance(landing_evidence, dict)
                else 0.0
            )
            frame_similarity = _image_similarity_percent(self, frame, after_frame)
            incident_recorder.record_action(
                kind="navigation",
                source_scene_id=current_scene_id,
                source_score=score,
                shape=shape,
                reason=str(decision.get("reason") or ""),
                before_frame=frame,
                landing_scene_id=actual_scene_id,
                landing_score=landing_score,
                after_frame=after_frame,
                frame_similarity=frame_similarity,
                navigation_state_key=navigation_state_key,
                point=ActionPlanner().shape_center(image, shape),
            )
            last_navigation_frame = after_frame or frame
            last_navigation_scene_id = actual_scene_id
            last_navigation_score = landing_score
            if actual_scene_id == target_scene_id:
                with self._lock:
                    self._status.update({
                        "current_scene": target_scene_id,
                        "updated_at": time.time(),
                    })
                self._log("success", f"到达目标场景 #{target_scene_id}")
                incident_recorder.finalize(
                    status="recovered_with_fallback" if incident_recorder.fallback_used else "recovered_after_stall",
                    final_scene_id=target_scene_id,
                    final_score=landing_score,
                    final_frame=after_frame,
                    message="导航停滞后重新规划成功",
                )
                ctx.pop("_navigation_incident_recorder", None)
                return "success"
            if actual_scene_id == current_scene_id:
                scene_edge_key = self._scene_jump_edge_key(edge)
                semantic_scene_edge_key = self._scene_jump_edge_semantic_key(edge)
                edge_key = (navigation_state_key, *scene_edge_key)
                attempts = stalled_edge_attempts.get(edge_key, 0) + 1
                stalled_edge_attempts[edge_key] = attempts
                semantic_edge_key = (
                    int(target_scene_id),
                    *self._scene_jump_edge_semantic_key(edge),
                )
                semantic_attempts = semantic_stalled_edge_attempts.get(semantic_edge_key, 0) + 1
                semantic_stalled_edge_attempts[semantic_edge_key] = semantic_attempts
                last_failed_edge = edge
                last_failed_edges_by_state[navigation_state_key] = edge
                if (
                    attempts >= NAVIGATION_STATE_EDGE_RETRY_LIMIT
                    and frame_similarity is not None
                    and frame_similarity >= NAVIGATION_STABLE_FRAME_SIMILARITY
                ):
                    incident_recorder.trigger(
                        trigger_type="stable_self_loop",
                        trigger_label="同一场景、同一动作重复执行且真实画面稳定，未向目标推进",
                        threshold={
                            "state_attempts": attempts,
                            "semantic_attempts": semantic_attempts,
                            "state_retry_limit": NAVIGATION_STATE_EDGE_RETRY_LIMIT,
                            "semantic_retry_limit": NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT,
                            "frame_similarity": frame_similarity,
                            "stable_frame_similarity": NAVIGATION_STABLE_FRAME_SIMILARITY,
                        },
                        frame_data_url=after_frame or frame,
                        current_scene_id=current_scene_id,
                        current_score=landing_score or score,
                        candidate_scene_ids=[current_scene_id, target_scene_id],
                    )
                if semantic_attempts < NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT:
                    self._log(
                        "warning",
                        f"场景移动：点击 {shape_title} 后仍在 #{current_scene_id}，"
                        "扩大随机扰动半径后再尝试一次",
                    )
                    yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.0)
                    continue
                # Failure exclusion must survive the landing counter mutation
                # performed by ``_record_scene_jump_landing``.  Otherwise an
                # action that stays on the same scene is immediately treated
                # as a new edge and can be clicked until the 24-step ceiling.
                failed_edge_keys.add(semantic_scene_edge_key)
                if semantic_attempts >= NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT:
                    globally_failed_edge_keys.add(semantic_scene_edge_key)
                self._log(
                    "warning",
                    f"场景移动：点击 {shape_title} 后仍在 #{current_scene_id}，"
                    + (
                        f"跨动态画面累计无效 {semantic_attempts} 次，全局排除该候选并重新规划"
                        if semantic_attempts >= NAVIGATION_SEMANTIC_EDGE_RETRY_LIMIT
                        else f"本轮排除该候选并重新选择通往 #{target_scene_id} 的下一步"
                    ),
                )
                yield BehaviorTreeStatus.RUNNING
                continue
            self._log("detail", f"场景移动：实际到达 #{actual_scene_id}，重新规划到 #{target_scene_id}")
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.5)

        incident_recorder.trigger(
            trigger_type="replan_step_limit",
            trigger_label="达到最大重规划步数仍未到达目标",
            threshold={"max_replan_steps": NAVIGATION_MAX_REPLAN_STEPS},
            frame_data_url=last_navigation_frame,
            current_scene_id=last_navigation_scene_id,
            current_score=last_navigation_score,
            candidate_scene_ids=[
                scene_id
                for scene_id in (last_navigation_scene_id, target_scene_id)
                if scene_id is not None
            ],
        )
        incident_recorder.finalize(
            status="unrecovered",
            final_scene_id=last_navigation_scene_id,
            final_score=last_navigation_score,
            final_frame=last_navigation_frame,
            message="达到最大重规划步数",
        )
        ctx.pop("_navigation_incident_recorder", None)
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
                self._log("detail", "对齐 #49：未知场景，保留现场等待可靠识别")
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
        deadline = time.monotonic() + 10.0
        poll_count = 0
        while time.monotonic() < deadline:
            self._raise_if_stopped(stop_event)
            frame = self._screencap(ctx)
            if self._is_gift_page_ready(ctx, frame):
                self._log("success", "兑换礼包窗口已就绪")
                return
            poll_count += 1
            if poll_count in {4, 8}:
                key, score = self._identify_scene(ctx, frame, ["settings"])
                if key == "settings" and self._scene_matches(key, score):
                    self._log("detail", f"兑换礼包入口点击未生效，仍在 #49，安全重试 {poll_count // 4}/2")
                    self._click_frame_point(
                        ctx,
                        image,
                        float(box.get("x") or 0) + float(box.get("w") or 0) / 2,
                        float(box.get("y") or 0) - height * 0.02,
                    )
            self._clear_tick_frame(ctx)
            time.sleep(0.5)
        raise RuntimeError("点击兑换礼包后未检测到兑换窗口文案")

    def _is_gift_page_ready(self, ctx: dict[str, Any], frame: str) -> bool:
        text = self._recognized_scene_ocr_text(ctx, frame, [self.scene_ids["gift"]])
        return self._gift_page_text_ready(text)

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
        # 中文文本虽已显示在游戏输入框中，输入覆盖层仍处于编辑态；按本帧
        # OCR 的唯一“确定”实框完成输入，否则随后点击“兑换”只会收起覆盖层。
        frame = self._screencap(ctx)
        width, height = self._frame_size(image)
        confirm_shape = self._find_shape(image, "输入确定")
        if confirm_shape is None:
            raise RuntimeError("#78 缺少「输入确定」标注")
        confirm_deadline = time.monotonic() + 3.0
        while True:
            try:
                confirm_x, confirm_y = self._gift_input_confirm_point(
                    self._ocr_fragments_in_shapes(
                        frame,
                        image,
                        ["输入确定"],
                        padding=8,
                        ctx=ctx,
                    ),
                    frame_width=width,
                    frame_height=height,
                )
                break
            except RuntimeError as exc:
                if "匹配到 0 项" not in str(exc) or time.monotonic() >= confirm_deadline:
                    raise
            self._clear_tick_frame(ctx)
            time.sleep(0.25)
            frame = self._screencap(ctx)
        self._click_frame_point(ctx, image, confirm_x, confirm_y)
        time.sleep(0.5)

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
            if (
                key == "gift" and self._scene_matches(key, score)
            ) or self._is_gift_page_ready(ctx, frame):
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
        elif not (
            key == "gift" and self._scene_matches(key, score)
        ) and not self._is_gift_page_ready(ctx, frame):
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








